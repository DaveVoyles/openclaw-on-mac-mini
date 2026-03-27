"""
OpenClaw Vector Store — Phase 13A
Unified semantic memory layer backed by ChromaDB.

Provides three collections:
  - memories:       QMD facts, ontology entities, user preferences
  - conversations:  Thread messages and session summaries
  - research:       Research reports and browsed sources

Uses ChromaDB's built-in all-MiniLM-L6-v2 sentence-transformer model
for local, free embeddings (384 dimensions, runs on CPU).
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw.vector_store")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "/memory/chromadb"))
SIMILARITY_THRESHOLD = float(os.getenv("CHROMA_SIMILARITY_THRESHOLD", "0.7"))
DEFAULT_TOP_K = 5

# Collection names
MEMORIES_COLLECTION = "memories"
CONVERSATIONS_COLLECTION = "conversations"
RESEARCH_COLLECTION = "research"

# ---------------------------------------------------------------------------
# Lazy singleton — ChromaDB is heavy; only load when first accessed
# ---------------------------------------------------------------------------

_client = None
_collections: dict = {}
_lock = asyncio.Lock()


def _get_client():
    """Return the ChromaDB PersistentClient, creating it on first call."""
    global _client
    if _client is None:
        import chromadb

        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        log.info("ChromaDB initialized at %s", CHROMA_DIR)
    return _client


def _get_collection(name: str):
    """Get or create a ChromaDB collection by name."""
    if name not in _collections:
        client = _get_client()
        _collections[name] = client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        count = _collections[name].count()
        log.info("Collection '%s' ready (%d documents)", name, count)
    return _collections[name]


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


async def add_document(
    collection_name: str,
    doc_id: str,
    text: str,
    metadata: Optional[dict] = None,
) -> None:
    """Embed and store a document in the specified collection.

    If a document with the same ID exists, it is updated (upsert).
    Runs the embedding in a thread pool to avoid blocking the event loop.
    """
    if not text or not text.strip():
        return

    meta = metadata or {}
    meta["added_at"] = time.time()

    def _upsert():
        col = _get_collection(collection_name)
        col.upsert(
            ids=[doc_id],
            documents=[text[:8000]],  # ChromaDB limit; truncate very long docs
            metadatas=[meta],
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _upsert)
    log.debug("Upserted doc '%s' into '%s'", doc_id, collection_name)


async def search(
    collection_name: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    where: Optional[dict] = None,
    threshold: Optional[float] = None,
) -> list[dict]:
    """Semantic search across a collection.

    Returns a list of dicts: [{"id", "text", "metadata", "distance"}, ...]
    sorted by relevance (lowest distance = most similar).
    """
    if not query or not query.strip():
        return []

    threshold = threshold or SIMILARITY_THRESHOLD

    def _query():
        col = _get_collection(collection_name)
        if col.count() == 0:
            return []
        kwargs = {
            "query_texts": [query],
            "n_results": min(top_k, col.count()),
        }
        if where:
            kwargs["where"] = where
        return col.query(**kwargs)

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _query)

    if not results or not results.get("ids") or not results["ids"][0]:
        return []

    # Flatten ChromaDB's nested response format
    output = []
    for i, doc_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results.get("distances") else 1.0
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - (distance / 2)
        similarity = 1 - (distance / 2)
        if similarity < threshold:
            continue
        output.append({
            "id": doc_id,
            "text": results["documents"][0][i] if results.get("documents") else "",
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            "distance": distance,
            "similarity": round(similarity, 3),
        })

    return output


async def search_all(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    threshold: Optional[float] = None,
) -> list[dict]:
    """Search across ALL collections and return merged, ranked results.

    Each result includes a 'collection' field indicating its source.
    """
    collections = [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]
    tasks = [
        search(col, query, top_k=top_k, threshold=threshold)
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

    def _delete():
        col = _get_collection(collection_name)
        col.delete(ids=[doc_id])

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _delete)
    log.debug("Deleted doc '%s' from '%s'", doc_id, collection_name)


# ---------------------------------------------------------------------------
# Collection stats
# ---------------------------------------------------------------------------


async def get_stats() -> dict:
    """Return stats for all collections."""

    def _stats():
        client = _get_client()
        stats = {}
        for name in [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]:
            col = _get_collection(name)
            stats[name] = {"count": col.count()}
        return stats

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _stats)


# ---------------------------------------------------------------------------
# Convenience helpers for specific domains
# ---------------------------------------------------------------------------


async def add_memory(fact_id: str, content: str, tags: Optional[list[str]] = None) -> None:
    """Store a fact/memory in the memories collection."""
    await add_document(
        MEMORIES_COLLECTION,
        doc_id=f"mem_{fact_id}",
        text=content,
        metadata={"type": "fact", "tags": ",".join(tags or [])},
    )


async def add_conversation_summary(
    user_id: int, thread_name: str, summary: str
) -> None:
    """Store a conversation summary in the conversations collection."""
    await add_document(
        CONVERSATIONS_COLLECTION,
        doc_id=f"conv_{user_id}_{thread_name}_{int(time.time())}",
        text=summary,
        metadata={
            "type": "summary",
            "user_id": str(user_id),
            "thread_name": thread_name,
        },
    )


async def add_research_report(
    query: str, report: str, sources: Optional[list[str]] = None
) -> None:
    """Store a research report in the research collection."""
    report_id = f"research_{int(time.time())}_{hash(query) % 10000}"
    await add_document(
        RESEARCH_COLLECTION,
        doc_id=report_id,
        text=report,
        metadata={
            "type": "report",
            "query": query[:500],
            "sources": ",".join(sources or [])[:2000],
        },
    )


async def recall(query: str, top_k: int = 5) -> str:
    """Semantic recall across all collections. Returns formatted text."""
    results = await search_all(query, top_k=top_k)
    if not results:
        return ""

    lines = []
    for r in results:
        source = r["collection"].replace("_", " ").title()
        sim = r.get("similarity", 0)
        text = r["text"][:300]
        lines.append(f"[{source} · {sim:.0%}] {text}")

    return "\n".join(lines)
