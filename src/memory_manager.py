"""
OpenClaw Memory Manager — Phase 16: Unified Memory Facade
Single interface for all memory operations across QMD, ChromaDB, rules, and profile.
"""

import hashlib
import logging
import time

log = logging.getLogger("openclaw.memory_manager")


async def store(
    content: str,
    *,
    source: str = "user-explicit",
    confidence: float = 1.0,
    tags: list[str] | None = None,
    dedup: bool = True,
) -> dict:
    """Store a fact/memory across all backends.

    Args:
        content: The fact or memory text to store
        source: Origin — "user-explicit", "auto-extracted", "correction", "profile"
        confidence: 0.0-1.0 reliability score
        tags: Optional tags for categorization
        dedup: If True, check for duplicates before storing

    Returns dict with {"stored": bool, "id": str, "duplicate": bool}
    """
    import vector_store
    from qmd import remember_fact

    result = {"stored": False, "id": "", "duplicate": False}
    fact_id = _content_id(content) if dedup else _unique_id(content)

    # Store in vector store (with optional dedup)
    try:
        if dedup:
            stored = await vector_store.add_memory_deduped(
                fact_id=fact_id,
                content=content,
                tags=tags,
                metadata={"source": source, "confidence": confidence},
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
            )
        result["stored"] = True
        result["id"] = f"mem_{fact_id}"
    except Exception as e:
        log.warning("Vector store failed: %s", e)

    # Also store in QMD (keyword-searchable backup)
    try:
        await remember_fact(content, tags=",".join(tags or []), source=source)
    except Exception as e:
        log.debug("QMD store failed: %s", e)

    return result


async def recall(
    query: str,
    *,
    top_k: int = 5,
    include_rules: bool = True,
    include_profile: bool = True,
) -> list[dict]:
    """Recall relevant memories from all sources.

    Returns list of dicts: [{"text", "source", "similarity", "type"}, ...]
    sorted by relevance.
    """
    results: list[dict] = []

    # Vector store (semantic search across all collections)
    try:
        import vector_store

        vs_results = await vector_store.search_all(query, top_k=top_k)
        for r in vs_results:
            results.append({
                "text": r["text"],
                "source": r.get("metadata", {}).get("source", "unknown"),
                "similarity": r.get("similarity", 0),
                "type": r.get("collection", "memory"),
                "id": r.get("id", ""),
            })
    except Exception as e:
        log.debug("Vector recall failed: %s", e)

    # Rules (if requested)
    if include_rules:
        try:
            from rules_engine import get_relevant_rules

            rules = await get_relevant_rules(query, top_k=3)
            for rule in rules:
                if isinstance(rule, str):
                    results.append({
                        "text": rule,
                        "source": "rule",
                        "similarity": 0.8,
                        "type": "rule",
                        "id": "",
                    })
        except Exception as e:
            log.debug("Rules recall failed: %s", e)

    # Sort by similarity descending
    results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
    return results[:top_k]


async def forget(memory_id: str) -> bool:
    """Remove a memory by ID from all backends."""
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
            except Exception as exc:
                log.debug("Vector delete from %s failed for %s: %s", collection, memory_id, exc)
    except Exception as e:
        log.debug("Vector forget failed: %s", e)

    return removed


async def stats() -> dict:
    """Aggregated stats across all memory backends."""
    result: dict = {
        "vector_store": {},
        "qmd": {"count": 0},
        "rules": {"count": 0},
        "profile": {"exists": False},
    }

    try:
        import vector_store

        result["vector_store"] = await vector_store.get_stats()
    except Exception as exc:
        log.debug("Vector store stats failed: %s", exc)

    try:
        from qmd import list_memories

        memories = await list_memories()
        if memories and memories != "Memory is empty.":
            result["qmd"]["count"] = memories.count("\n") + 1
    except Exception as exc:
        log.debug("QMD stats failed: %s", exc)

    try:
        from rules_engine import get_all_rules

        rules = await get_all_rules()
        result["rules"]["count"] = len(rules)
    except Exception as exc:
        log.debug("Rules stats failed: %s", exc)

    try:
        from user_profile import load_profile

        profile = load_profile()
        result["profile"]["exists"] = bool(profile)
    except Exception as exc:
        log.debug("Profile stats failed: %s", exc)

    return result


def _content_id(content: str) -> str:
    """Deterministic ID from content (stable for dedup)."""
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _unique_id(content: str) -> str:
    """Unique ID from content + timestamp (for non-dedup stores)."""
    return hashlib.md5(f"{content}_{time.time()}".encode()).hexdigest()[:12]
