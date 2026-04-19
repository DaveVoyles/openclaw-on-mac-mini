"""Vector store memory operations (store, recall, forget, stats)."""

import hashlib
import logging
import time as _time

_mem_log = logging.getLogger(__name__)

__all__ = [
    "_mem_content_id",
    "_mem_unique_id",
    "store_memory",
    "recall_memories",
    "forget_memory",
    "memory_stats",
    # backward-compatible aliases
    "recall",
    "forget",
    "stats",
    "_content_id",
    "_unique_id",
]


async def store_memory(
    content: str,
    *,
    source: str = "user-explicit",
    confidence: float = 1.0,
    tags: list[str] | None = None,
    dedup: bool = True,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> dict:
    """Store a fact/memory across all backends (vector store + QMD).

    Args:
        content: The fact or memory text to store.
        source: Origin — "user-explicit", "auto-extracted", "correction", "profile".
        confidence: 0.0-1.0 reliability score.
        tags: Optional tags for categorization.
        dedup: If True, skip storing identical content.
        channel_id / thread_id: Scope the memory to a specific channel or thread.

    Returns dict with {"stored": bool, "id": str, "duplicate": bool}.
    """
    import vector_store
    from qmd import remember_fact

    result: dict = {"stored": False, "id": "", "duplicate": False}
    fact_id = _mem_content_id(content) if dedup else _mem_unique_id(content)

    try:
        if dedup:
            stored = await vector_store.add_memory_deduped(
                fact_id=fact_id,
                content=content,
                tags=tags,
                metadata={"source": source, "confidence": confidence},
                channel_id=channel_id,
                thread_id=thread_id,
            )
            if not stored:
                result["duplicate"] = True
                return result
        else:
            await vector_store.add_memory(
                fact_id=fact_id,
                content=content,
                tags=tags,
                source=source,
                confidence=confidence,
                channel_id=channel_id,
                thread_id=thread_id,
            )
        result["stored"] = True
        result["id"] = f"mem_{fact_id}"
    except Exception as exc:  # broad: intentional — vector_store backend raises chromadb-specific exceptions
        _mem_log.warning("Vector store failed: %s", exc)

    try:
        await remember_fact(content, tags=",".join(tags or []), source=source)
    except Exception as exc:  # broad: intentional — QMD backend raises provider-specific exceptions
        _mem_log.debug("QMD store failed: %s", exc)

    return result


async def recall_memories(
    query: str,
    *,
    top_k: int = 5,
    include_rules: bool = True,
    include_profile: bool = True,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> list[dict]:
    """Recall relevant memories from all sources (vector store + rules).

    Returns list of dicts: [{"text", "source", "similarity", "type", "id"}, ...]
    sorted by relevance descending.
    """
    results: list[dict] = []

    try:
        import vector_store

        vs_results = await vector_store.search_all(
            query,
            top_k=top_k,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        for r in vs_results:
            results.append(
                {
                    "text": r["text"],
                    "source": r.get("metadata", {}).get("source", "unknown"),
                    "similarity": r.get("similarity", 0),
                    "type": r.get("collection", "memory"),
                    "id": r.get("id", ""),
                }
            )
    except (ImportError, RuntimeError, ValueError, OSError, AttributeError) as exc:
        _mem_log.debug("Vector recall failed: %s", exc)

    if include_rules:
        try:
            from rules_engine import get_relevant_rules

            for rule in await get_relevant_rules(query, top_k=3):
                if isinstance(rule, str):
                    results.append(
                        {
                            "text": rule,
                            "source": "rule",
                            "similarity": 0.8,
                            "type": "rule",
                            "id": "",
                        }
                    )
        except (ImportError, RuntimeError, ValueError, OSError, AttributeError) as exc:
            _mem_log.debug("Rules recall failed: %s", exc)

    results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
    return results[:top_k]


async def forget_memory(memory_id: str) -> bool:
    """Remove a memory by ID from all vector store collections."""
    removed = False
    try:
        import vector_store

        for collection in [
            vector_store.MEMORIES_COLLECTION,
            vector_store.CONVERSATIONS_COLLECTION,
            vector_store.RESEARCH_COLLECTION,
        ]:
            try:
                await vector_store.delete_document(collection, memory_id)
                removed = True
            except (RuntimeError, ValueError, OSError, KeyError) as exc:
                _mem_log.debug("Vector delete from %s failed for %s: %s", collection, memory_id, exc)
    except (ImportError, RuntimeError, OSError) as exc:
        _mem_log.debug("Vector forget failed: %s", exc)
    return removed


async def memory_stats() -> dict:
    """Aggregated stats across all memory backends (vector store, QMD, rules, profile)."""
    result: dict = {
        "vector_store": {},
        "qmd": {"count": 0},
        "rules": {"count": 0},
        "profile": {"exists": False},
    }
    try:
        import vector_store

        result["vector_store"] = await vector_store.get_stats()
    except (ImportError, RuntimeError, ValueError, OSError, TypeError) as exc:
        _mem_log.debug("Vector store stats failed: %s", exc)

    try:
        from qmd import list_memories

        memories = await list_memories()
        if memories and memories != "Memory is empty.":
            result["qmd"]["count"] = memories.count("\n") + 1
    except (ImportError, RuntimeError, ValueError, OSError, AttributeError, TypeError) as exc:
        _mem_log.debug("QMD stats failed: %s", exc)

    try:
        from rules_engine import get_all_rules

        result["rules"]["count"] = len(await get_all_rules())
    except (ImportError, RuntimeError, ValueError, OSError, TypeError) as exc:
        _mem_log.debug("Rules stats failed: %s", exc)

    try:
        from user_profile import load_profile

        result["profile"]["exists"] = bool(load_profile())
    except (ImportError, OSError, ValueError, AttributeError, KeyError) as exc:
        _mem_log.debug("Profile stats failed: %s", exc)

    return result


def _mem_content_id(content: str) -> str:
    """Deterministic ID from content (stable for dedup)."""
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _mem_unique_id(content: str) -> str:
    """Unique ID from content + timestamp (for non-dedup stores)."""
    return hashlib.md5(f"{content}_{_time.time()}".encode()).hexdigest()[:12]


# Backward-compatible aliases
recall = recall_memories
forget = forget_memory
stats = memory_stats
_content_id = _mem_content_id
_unique_id = _mem_unique_id
