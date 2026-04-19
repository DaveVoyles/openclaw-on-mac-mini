"""Vector store ChromaDB client, lazy singleton, and core CRUD operations."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from vector_store_config import (
    CHROMA_DIR,
    CONVERSATIONS_COLLECTION,
    DEFAULT_TOP_K,
    MEMORIES_COLLECTION,
    RESEARCH_COLLECTION,
    SIMILARITY_THRESHOLD,
    _embedding_fn,
)
from vector_store_scope import (
    _allow_fallback_result,
    _combine_scope_where,
    _inject_scope_metadata,
    _normalize_scope_id,
    _resolve_scope,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton — ChromaDB is heavy; only load when first accessed
# ---------------------------------------------------------------------------

_client = None
_collections: dict = {}
_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# TTL cache for collection objects (avoids repeated ChromaDB round-trips)
# ---------------------------------------------------------------------------

_collection_cache: dict[str, tuple[object, float]] = {}
_CACHE_TTL = 300  # seconds (5 minutes)


def _get_cached_collection(name: str) -> object | None:
    """Return a cached collection object if still within TTL, else None."""
    entry = _collection_cache.get(name)
    if entry is not None:
        obj, ts = entry
        if time.time() - ts < _CACHE_TTL:
            return obj
        del _collection_cache[name]
    return None


def _set_cached_collection(name: str, obj: object) -> None:
    """Store a collection object in the TTL cache."""
    _collection_cache[name] = (obj, time.time())


def clear_collection_cache() -> None:
    """Invalidate the collection TTL cache (useful for tests and forced refresh)."""
    _collection_cache.clear()


def _get_client() -> Any:
    """Return the ChromaDB PersistentClient, creating it on first call."""
    global _client
    if _client is None:
        import chromadb

        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        log.info("ChromaDB initialized at %s", CHROMA_DIR)
    return _client


def _get_collection(name: str) -> Any:
    """Get or create a ChromaDB collection by name.

    Results are cached with a 5-minute TTL to avoid redundant ChromaDB
    round-trips when the same collection is accessed frequently.
    """
    cached = _get_cached_collection(name)
    if cached is not None:
        return cached

    if name not in _collections:
        client = _get_client()
        kwargs = {
            "name": name,
            "metadata": {"hnsw:space": "cosine"},
        }
        if _embedding_fn is not None:
            kwargs["embedding_function"] = _embedding_fn
        _collections[name] = client.get_or_create_collection(**kwargs)
        count = _collections[name].count()
        log.info("Collection '%s' ready (%d documents)", name, count)

    _set_cached_collection(name, _collections[name])
    return _collections[name]


# ---------------------------------------------------------------------------
# Core CRUD operations
# ---------------------------------------------------------------------------


async def add_document(
    collection_name: str,
    doc_id: str,
    text: str,
    metadata: Optional[dict] = None,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> None:
    """Embed and store a document in the specified collection.

    If a document with the same ID exists, it is updated (upsert).
    Runs the embedding in a thread pool to avoid blocking the event loop.
    """
    if not text or not text.strip():
        return

    meta = _inject_scope_metadata(
        metadata,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    resolved_channel_id = _normalize_scope_id(meta.get("channel_id"))
    resolved_thread_id = _normalize_scope_id(meta.get("thread_id"))
    meta["added_at"] = time.time()
    meta.setdefault("access_count", 0)
    meta.setdefault("last_accessed", 0.0)

    def _upsert() -> None:
        col = _get_collection(collection_name)
        col.upsert(
            ids=[doc_id],
            documents=[text[:8000]],  # ChromaDB limit; truncate very long docs
            metadatas=[meta],
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _upsert)

    from vector_store_compaction import _compact_scope_if_needed  # lazy — avoids circular dep
    await _compact_scope_if_needed(
        collection_name=collection_name,
        channel_id=resolved_channel_id,
        thread_id=resolved_thread_id,
    )
    log.debug("Upserted doc '%s' into '%s'", doc_id, collection_name)


async def search(
    collection_name: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    where: Optional[dict] = None,
    threshold: Optional[float] = None,
    track_access: bool = True,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    enable_scope_fallback: bool = True,
    cross_channel: bool = False,
) -> list[dict]:
    """Semantic search across a collection.

    Returns a list of dicts: [{"id", "text", "metadata", "distance"}, ...]
    sorted by relevance (lowest distance = most similar).
    Decayed documents are deprioritized (penalty applied to similarity).
    When track_access=True, bumps access_count on returned documents.
    """
    if not query or not query.strip():
        return []

    threshold = threshold or SIMILARITY_THRESHOLD
    # Fetch extra results so we can still fill top_k after filtering decayed
    fetch_k = top_k + 5

    async def _query_once(query_where: Optional[dict]) -> list[dict]:
        def _query() -> Any:
            col = _get_collection(collection_name)
            if col.count() == 0:
                return []
            kwargs = {
                "query_texts": [query],
                "n_results": min(fetch_k, col.count()),
            }
            if query_where:
                kwargs["where"] = query_where
            return col.query(**kwargs)

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, _query)
        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        output = []
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 1.0
            # ChromaDB cosine distance: 0 = identical, 2 = opposite
            # Convert to similarity: 1 - (distance / 2)
            similarity = 1 - (distance / 2)
            meta = results["metadatas"][0][i] if results.get("metadatas") else {}
            # Deprioritize decayed documents (10% penalty)
            if meta.get("decayed"):
                similarity *= 0.9
            # Boost high-confidence facts (source tracking)
            confidence = meta.get("confidence")
            if confidence is not None:
                try:
                    # Scale: 0.5 confidence → 0.95x, 0.7 → 0.97x, 1.0 → 1.0x (no change)
                    similarity *= 0.9 + (float(confidence) * 0.1)
                except (ValueError, TypeError):
                    pass
            if similarity < threshold:
                continue
            output.append({
                "id": doc_id,
                "text": results["documents"][0][i] if results.get("documents") else "",
                "metadata": meta,
                "distance": distance,
                "similarity": round(similarity, 3),
            })

        return output[:top_k]

    if cross_channel:
        resolved_channel_id = None
        resolved_thread_id = None
        scoped_where = where
    else:
        resolved_channel_id, resolved_thread_id = _resolve_scope(
            channel_id=channel_id,
            thread_id=thread_id,
        )
        scoped_where = _combine_scope_where(
            where,
            channel_id=resolved_channel_id,
            thread_id=resolved_thread_id,
        )

    output = await _query_once(scoped_where)
    if output:
        if track_access:
            try:
                from vector_store_compaction import bump_access  # lazy — avoids circular dep
                asyncio.get_running_loop().create_task(
                    bump_access(collection_name, [r["id"] for r in output])
                )
            except (AttributeError, OSError, RuntimeError) as exc:
                log.debug("Access tracking dispatch failed: %s", exc)
        return output

    if (
        not resolved_channel_id
        or not enable_scope_fallback
        or scoped_where == where
    ):
        return output

    fallback_results = await _query_once(where)
    blocked_cross_channel = 0
    blocked_cross_thread = 0
    blocked_unscoped = 0
    filtered_output: list[dict] = []
    for item in fallback_results:
        meta = item.get("metadata", {}) or {}
        doc_channel_id = _normalize_scope_id(meta.get("channel_id"))
        doc_thread_id = _normalize_scope_id(meta.get("thread_id"))
        if _allow_fallback_result(
            meta,
            channel_id=resolved_channel_id,
            thread_id=resolved_thread_id,
        ):
            filtered_output.append(item)
            continue
        if not doc_channel_id:
            blocked_unscoped += 1
        elif doc_channel_id != resolved_channel_id:
            blocked_cross_channel += 1
        elif resolved_thread_id and doc_thread_id != resolved_thread_id:
            blocked_cross_thread += 1

    output = filtered_output[:top_k]
    if (blocked_cross_channel or blocked_cross_thread or blocked_unscoped) and resolved_channel_id:
        try:
            from runtime_state import record_scoped_recall_alert

            record_scoped_recall_alert(
                category="scope_guard_block",
                message="Scoped recall blocked out-of-scope fallback candidates.",
                channel_id=resolved_channel_id,
                thread_id=resolved_thread_id,
                metadata={
                    "collection": collection_name,
                    "blocked_cross_channel": blocked_cross_channel,
                    "blocked_cross_thread": blocked_cross_thread,
                    "blocked_unscoped": blocked_unscoped,
                    "query": query[:200],
                },
            )
        except (AttributeError, OSError, RuntimeError):
            pass

    if output:
        log.debug(
            "Scoped vector fallback used for %s (channel=%s thread=%s, hits=%d)",
            collection_name,
            resolved_channel_id,
            resolved_thread_id or "-",
            len(output),
        )

    # Fire-and-forget access tracking
    if track_access and output:
        try:
            from vector_store_compaction import bump_access  # lazy — avoids circular dep
            asyncio.get_running_loop().create_task(
                bump_access(collection_name, [r["id"] for r in output])
            )
        except (AttributeError, OSError, RuntimeError) as exc:
            log.debug("Access tracking dispatch failed: %s", exc)

    return output


async def search_safe(
    collection_name: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    **kwargs,
) -> list[dict]:
    """Search with fallback — returns empty list if vector store is down."""
    try:
        return await search(collection_name, query, top_k, **kwargs)
    except Exception as e:  # broad: intentional
        log.warning("Vector search failed (collection=%s): %s — returning empty", collection_name, e)
        return []


async def search_all(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    threshold: Optional[float] = None,
    where: Optional[dict] = None,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    cross_channel: bool = False,
) -> list[dict]:
    """Search across ALL collections and return merged, ranked results.

    Each result includes a 'collection' field indicating its source.
    """
    collections = [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]
    tasks = [
        search(
            col,
            query,
            top_k=top_k,
            threshold=threshold,
            where=where,
            channel_id=channel_id,
            thread_id=thread_id,
            cross_channel=cross_channel,
        )
        for col in collections
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = []
    for col_name, result in zip(collections, all_results):
        if isinstance(result, Exception):
            log.warning("Search failed for collection '%s': %s", col_name, result)
            continue
        for item in result:
            item["collection"] = col_name
            merged.append(item)

    # Sort by distance (ascending = most relevant first)
    merged.sort(key=lambda x: x.get("distance", 2.0))
    return merged[:top_k]


async def delete_document(collection_name: str, doc_id: str) -> None:
    """Remove a document from a collection by ID."""

    def _delete() -> None:
        col = _get_collection(collection_name)
        col.delete(ids=[doc_id])

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _delete)
    log.debug("Deleted doc '%s' from '%s'", doc_id, collection_name)


async def get_stats() -> dict:
    """Return stats for all collections."""

    def _stats() -> dict:
        _get_client()  # ensure collections are initialized
        stats = {}
        for name in [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]:
            col = _get_collection(name)
            stats[name] = {"count": col.count()}
        return stats

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _stats)
