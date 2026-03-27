"""
OpenClaw Vector Store — Phase 13A + Phase 14B (Access Tracking)
Unified semantic memory layer backed by ChromaDB.

Provides three collections:
  - memories:       QMD facts, ontology entities, user preferences, learned rules
  - conversations:  Thread messages and session summaries
  - research:       Research reports and browsed sources

Embedding model is configurable via the EMBEDDING_MODEL env var:
  - Default (empty): ChromaDB's built-in all-MiniLM-L6-v2 (384 dims, CPU)
  - Custom: Any Ollama-hosted model (e.g. embeddinggemma, nomic-embed-text)
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

# Embedding model configuration
# Default: ChromaDB's built-in all-MiniLM-L6-v2 (384 dims, free, CPU)
# Optional: Ollama-hosted models like embeddinggemma, nomic-embed-text, etc.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")  # empty = ChromaDB default
OLLAMA_EMBED_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")


def _get_embedding_function():
    """Return the embedding function for ChromaDB collections.

    If EMBEDDING_MODEL is set, uses Ollama's embedding API.
    Otherwise returns None (ChromaDB uses its built-in default).

    WARNING: Changing embedding models requires re-indexing. Existing
    collections with MiniLM embeddings are incompatible with new model
    dimensions. Delete /memory/chromadb and let it rebuild.
    """
    if not EMBEDDING_MODEL:
        return None  # ChromaDB default (all-MiniLM-L6-v2)

    try:
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
        log.info("Using Ollama embedding model: %s at %s", EMBEDDING_MODEL, OLLAMA_EMBED_URL)
        log.warning(
            "Custom embedding model active (%s). If you switched models, "
            "existing collections must be re-indexed (delete %s and restart).",
            EMBEDDING_MODEL, CHROMA_DIR,
        )
        return OllamaEmbeddingFunction(
            url=f"{OLLAMA_EMBED_URL}/api/embeddings",
            model_name=EMBEDDING_MODEL,
        )
    except ImportError:
        log.warning("OllamaEmbeddingFunction not available in this ChromaDB version, using default")
        return None
    except Exception as e:
        log.warning("Failed to initialize Ollama embeddings (%s), using default: %s", EMBEDDING_MODEL, e)
        return None


_embedding_fn = _get_embedding_function()

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
        kwargs = {
            "name": name,
            "metadata": {"hnsw:space": "cosine"},
        }
        if _embedding_fn is not None:
            kwargs["embedding_function"] = _embedding_fn
        _collections[name] = client.get_or_create_collection(**kwargs)
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
    meta.setdefault("access_count", 0)
    meta.setdefault("last_accessed", 0.0)

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
    track_access: bool = True,
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

    def _query():
        col = _get_collection(collection_name)
        if col.count() == 0:
            return []
        kwargs = {
            "query_texts": [query],
            "n_results": min(fetch_k, col.count()),
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
        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
        # Deprioritize decayed documents (10% penalty)
        if meta.get("decayed"):
            similarity *= 0.9
        if similarity < threshold:
            continue
        output.append({
            "id": doc_id,
            "text": results["documents"][0][i] if results.get("documents") else "",
            "metadata": meta,
            "distance": distance,
            "similarity": round(similarity, 3),
        })

    output = output[:top_k]

    # Fire-and-forget access tracking
    if track_access and output:
        try:
            asyncio.get_running_loop().create_task(
                bump_access(collection_name, [r["id"] for r in output])
            )
        except Exception:
            pass

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


async def bump_access(collection_name: str, doc_ids: list[str]) -> None:
    """Increment access_count and update last_accessed for retrieved documents.

    Called after search results are returned so frequently-accessed memories
    rank higher over time (reinforcement) while unused ones decay.
    """
    if not doc_ids:
        return

    def _bump():
        col = _get_collection(collection_name)
        for doc_id in doc_ids:
            try:
                existing = col.get(ids=[doc_id], include=["metadatas"])
                if not existing["ids"]:
                    continue
                meta = existing["metadatas"][0] or {}
                meta["access_count"] = meta.get("access_count", 0) + 1
                meta["last_accessed"] = time.time()
                col.update(ids=[doc_id], metadatas=[meta])
            except Exception:
                pass  # best-effort; don't crash on tracking failures

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _bump)
    except Exception as e:
        log.debug("Access bump failed: %s", e)


async def get_decayed_documents(
    collection_name: str,
    max_age_days: int = 30,
    min_access_count: int = 1,
) -> list[dict]:
    """Find documents that haven't been accessed recently (candidates for decay).

    Returns documents where last_accessed is older than max_age_days
    AND access_count is below min_access_count.
    """
    cutoff = time.time() - (max_age_days * 86400)

    def _scan():
        col = _get_collection(collection_name)
        if col.count() == 0:
            return []
        # ChromaDB where filters on metadata
        try:
            results = col.get(
                where={"$and": [
                    {"last_accessed": {"$lt": cutoff}},
                    {"access_count": {"$lt": min_access_count}},
                ]},
                include=["metadatas", "documents"],
            )
        except Exception:
            # Fallback: get all and filter in Python (older ChromaDB versions)
            results = col.get(include=["metadatas", "documents"])
        docs = []
        for i, doc_id in enumerate(results.get("ids", [])):
            meta = results["metadatas"][i] if results.get("metadatas") else {}
            last_acc = meta.get("last_accessed", 0)
            acc_count = meta.get("access_count", 0)
            if last_acc < cutoff and acc_count < min_access_count:
                docs.append({
                    "id": doc_id,
                    "metadata": meta,
                    "text": results["documents"][i] if results.get("documents") else "",
                })
        return docs

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _scan)


async def mark_decayed(collection_name: str, doc_ids: list[str]) -> int:
    """Flag documents as decayed (they rank lower but aren't deleted)."""
    if not doc_ids:
        return 0

    def _mark():
        col = _get_collection(collection_name)
        count = 0
        for doc_id in doc_ids:
            try:
                existing = col.get(ids=[doc_id], include=["metadatas"])
                if not existing["ids"]:
                    continue
                meta = existing["metadatas"][0] or {}
                meta["decayed"] = True
                col.update(ids=[doc_id], metadatas=[meta])
                count += 1
            except Exception:
                pass
        return count

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _mark)


# ---------------------------------------------------------------------------
# Collection stats
# ---------------------------------------------------------------------------


async def get_stats() -> dict:
    """Return stats for all collections."""

    def _stats():
        _get_client()  # ensure collections are initialized
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


async def recall_for_context(query: str, top_k: int | None = None) -> str:
    """Recall relevant context for Auto-RAG injection.

    Searches all collections and formats results as a concise context block
    suitable for prepending to a user message before sending to the LLM.
    Returns empty string if nothing relevant is found.
    """
    from config import cfg

    top_k = top_k or cfg.auto_recall_top_k

    results = await search_all(query, top_k=top_k)
    if not results:
        return ""

    lines = ["[Your Memory]"]
    for r in results:
        source = r["collection"].replace("_", " ").title()
        sim = r.get("similarity", 0)
        text = r["text"][:200].replace("\n", " ").strip()
        lines.append(f"- [{source} · {sim:.0%}] {text}")

    return "\n".join(lines)
