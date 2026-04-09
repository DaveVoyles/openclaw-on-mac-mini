"""Vector store high-level memory operations."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from vector_store_config import (
    _RECALL_GUARD_MIN_SIMILARITY,
    CONVERSATIONS_COLLECTION,
    MEMORIES_COLLECTION,
    RESEARCH_COLLECTION,
    SIMILARITY_THRESHOLD,
    _set_recall_guard_notes,
)
from vector_store_scope import (
    _combine_scope_where,
    _extract_explicit_recall_domains,
    _infer_recall_domains,
    _normalize_scope_id,
    _resolve_scope,
)

log = logging.getLogger("openclaw.vector_store")


async def get_scoped_memory_summary(
    *,
    channel_id: int | str,
    thread_id: int | str | None = None,
    latest_limit: int = 5,
    include_anchor: bool = False,
) -> dict:
    """Return per-collection scoped memory stats and latest entries."""
    resolved_channel_id, resolved_thread_id = _resolve_scope(
        channel_id=channel_id,
        thread_id=thread_id,
    )
    if not resolved_channel_id:
        raise ValueError("channel_id is required")
    latest_limit = max(1, min(int(latest_limit), 20))

    def _inspect_scope() -> dict:
        from vector_store_client import _get_collection  # lazy — allows test patching
        collections = [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]
        payload: dict[str, Any] = {
            "scope": {
                "channel_id": resolved_channel_id,
                "thread_id": resolved_thread_id,
            },
            "collections": {},
            "total_count": 0,
        }
        for name in collections:
            col = _get_collection(name)
            where = _combine_scope_where(
                None,
                channel_id=resolved_channel_id,
                thread_id=resolved_thread_id,
            )
            if col.count() == 0:
                payload["collections"][name] = {"count": 0, "latest": []}
                continue
            results = col.get(where=where, include=["metadatas", "documents"])
            rows = []
            for i, doc_id in enumerate(results.get("ids", [])):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                doc = results["documents"][i] if results.get("documents") else ""
                rows.append({
                    "id": doc_id,
                    "added_at": float(meta.get("added_at", 0) or 0),
                    "type": meta.get("type", "unknown"),
                    "excerpt": (doc or "")[:180],
                    "channel_id": _normalize_scope_id(meta.get("channel_id")),
                    "thread_id": _normalize_scope_id(meta.get("thread_id")),
                })
            rows.sort(key=lambda r: r.get("added_at", 0), reverse=True)
            payload["collections"][name] = {
                "count": len(rows),
                "latest": rows[:latest_limit],
            }
            payload["total_count"] += len(rows)
        return payload

    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _inspect_scope)
    if include_anchor:
        try:
            from runtime_state import get_anchor_state

            anchor_state = get_anchor_state()
            if anchor_state:
                anchor_channel = _normalize_scope_id(anchor_state.get("channel_id"))
                anchor_thread = _normalize_scope_id(anchor_state.get("thread_id"))
                payload["anchor"] = {
                    "present": (
                        anchor_channel == resolved_channel_id
                        and anchor_thread == resolved_thread_id
                    ),
                    "anchor_id": anchor_state.get("anchor_id"),
                    "timestamp": anchor_state.get("timestamp"),
                    "channel_id": anchor_channel,
                    "thread_id": anchor_thread,
                }
            else:
                payload["anchor"] = {"present": False}
        except Exception:
            payload["anchor"] = {"present": False}
    try:
        from runtime_state import get_scoped_recall_alerts

        alerts = get_scoped_recall_alerts(
            channel_id=resolved_channel_id,
            thread_id=resolved_thread_id,
            limit=5,
        )
        payload["alerts"] = {
            "count": len(alerts),
            "items": alerts,
        }
    except Exception:
        payload["alerts"] = {"count": 0, "items": []}
    try:
        from runtime_state import get_memory_compaction_events, get_memory_lifecycle_policy

        payload["memory_policy"] = get_memory_lifecycle_policy(
            channel_id=int(resolved_channel_id),
            thread_id=int(resolved_thread_id) if resolved_thread_id is not None else None,
        )
        compaction_events = get_memory_compaction_events(
            channel_id=resolved_channel_id,
            thread_id=resolved_thread_id,
            limit=10,
        )
        payload["compaction"] = {
            "count": len(compaction_events),
            "items": compaction_events,
        }
    except Exception:
        payload["memory_policy"] = {"retention_class": "standard", "memory_budget_items": 200}
        payload["compaction"] = {"count": 0, "items": []}
    return payload


async def clear_scoped_memory(
    *,
    channel_id: int | str,
    thread_id: int | str | None = None,
) -> dict:
    """Delete scoped documents across all vector collections."""
    resolved_channel_id, resolved_thread_id = _resolve_scope(
        channel_id=channel_id,
        thread_id=thread_id,
    )
    if not resolved_channel_id:
        raise ValueError("channel_id is required")

    def _clear_scope() -> dict:
        from vector_store_client import _get_collection  # lazy — allows test patching
        collections = [MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION]
        deleted_by_collection: dict[str, int] = {}
        total_deleted = 0
        for name in collections:
            col = _get_collection(name)
            where = _combine_scope_where(
                None,
                channel_id=resolved_channel_id,
                thread_id=resolved_thread_id,
            )
            if col.count() == 0:
                deleted_by_collection[name] = 0
                continue
            results = col.get(where=where, include=[])
            ids = results.get("ids", [])
            if ids:
                col.delete(ids=ids)
            count = len(ids)
            deleted_by_collection[name] = count
            total_deleted += count
        return {
            "scope": {
                "channel_id": resolved_channel_id,
                "thread_id": resolved_thread_id,
            },
            "deleted": deleted_by_collection,
            "total_deleted": total_deleted,
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _clear_scope)


async def add_memory(
    fact_id: str,
    content: str,
    tags: Optional[list[str]] = None,
    source: str = "user-explicit",
    confidence: float = 1.0,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> None:
    """Store a fact/memory in the memories collection."""
    from vector_store_client import add_document  # lazy — allows test patching
    await add_document(
        MEMORIES_COLLECTION,
        doc_id=f"mem_{fact_id}",
        text=content,
        metadata={
            "type": "fact",
            "tags": ",".join(tags or []),
            "source": source,
            "confidence": confidence,
        },
        channel_id=channel_id,
        thread_id=thread_id,
    )


async def add_memory_deduped(
    fact_id: str,
    content: str,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
    dedup_threshold: float = 0.9,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> bool:
    """Store a fact with deduplication — returns True if stored, False if duplicate found."""
    # Check for near-duplicates
    try:
        from vector_store_client import search  # lazy — allows test patching via vector_store_client
        existing = await search(
            MEMORIES_COLLECTION,
            content,
            top_k=1,
            threshold=dedup_threshold,
            track_access=False,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if existing:
            log.debug("Dedup: skipped near-duplicate (%.0f%% similar to '%s')",
                      existing[0]["similarity"] * 100, existing[0]["id"])
            # Reinforce the existing memory instead
            from vector_store_compaction import bump_access  # lazy — allows test patching
            await bump_access(MEMORIES_COLLECTION, [existing[0]["id"]])
            return False
    except Exception as exc:
        log.debug("Dedup check failed, storing anyway: %s", exc)

    meta = metadata or {}
    meta["type"] = meta.get("type", "fact")
    meta["tags"] = meta.get("tags", ",".join(tags or []))

    from vector_store_client import add_document as _add_document  # lazy — allows test patching
    await _add_document(
        MEMORIES_COLLECTION,
        doc_id=f"mem_{fact_id}",
        text=content,
        metadata=meta,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    return True


async def add_conversation_summary(
    user_id: int,
    thread_name: str,
    summary: str,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> None:
    """Store a conversation summary in the conversations collection."""
    from vector_store_client import add_document  # lazy — allows test patching
    await add_document(
        CONVERSATIONS_COLLECTION,
        doc_id=f"conv_{user_id}_{thread_name}_{int(time.time())}",
        text=summary,
        metadata={
            "type": "summary",
            "user_id": str(user_id),
            "thread_name": thread_name,
        },
        channel_id=channel_id,
        thread_id=thread_id,
    )


async def add_research_report(
    query: str,
    report: str,
    sources: Optional[list[str]] = None,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> str:
    """Store a research report in the research collection."""
    from vector_store_client import add_document  # lazy — allows test patching
    report_id = f"research_{int(time.time())}_{hash(query) % 10000}"
    await add_document(
        RESEARCH_COLLECTION,
        doc_id=report_id,
        text=report,
        metadata={
            "type": "report",
            "query": query[:500],
            "sources": ",".join(sources or [])[:2000],
            "anchor_id": report_id,
        },
        channel_id=channel_id,
        thread_id=thread_id,
    )
    try:
        resolved_channel_id, resolved_thread_id = _resolve_scope(channel_id=channel_id, thread_id=thread_id)
        if resolved_channel_id:
            from runtime_state import set_anchor_state

            set_anchor_state(
                int(resolved_channel_id),
                int(resolved_thread_id) if resolved_thread_id is not None else None,
                report_id,
            )
    except Exception:
        pass
    return report_id


async def recall(
    query: str,
    top_k: int = 5,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    cross_channel: bool = False,
) -> str:
    """Semantic recall across all collections. Returns formatted text."""
    from vector_store_client import search_all  # lazy — allows test patching
    results = await search_all(
        query,
        top_k=top_k,
        channel_id=channel_id,
        thread_id=thread_id,
        cross_channel=cross_channel,
    )
    if not results:
        return ""

    lines = []
    for r in results:
        source = r["collection"].replace("_", " ").title()
        sim = r.get("similarity", 0)
        text = r["text"][:300]
        lines.append(f"[{source} · {sim:.0%}] {text}")

    return "\n".join(lines)


async def recall_for_context(
    query: str,
    top_k: int | None = None,
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    cross_channel: bool = False,
    anchor_id: str | None = None,
) -> str:
    """Recall relevant context for Auto-RAG injection.

    Searches all collections and formats results as a concise context block
    suitable for prepending to a user message before sending to the LLM.
    Returns empty string if nothing relevant is found.
    """
    from config import cfg

    top_k = top_k or cfg.auto_recall_top_k
    _set_recall_guard_notes([])

    where = {"anchor_id": anchor_id} if anchor_id else None
    from vector_store_client import search_all  # lazy — allows test patching
    results = await search_all(
        query,
        top_k=top_k,
        where=where,
        channel_id=channel_id,
        thread_id=thread_id,
        cross_channel=cross_channel,
    )
    if anchor_id and not results:
        results = await search_all(
            query,
            top_k=top_k,
            channel_id=channel_id,
            thread_id=thread_id,
            cross_channel=cross_channel,
        )
    if not results:
        return ""

    query_domains = _infer_recall_domains(query)
    explicit_domains = _extract_explicit_recall_domains(query)
    guard_target_domains = {"sports", "wwe"}
    suppressed_domain = 0
    suppressed_low_similarity = 0
    guarded_results: list[dict] = []
    min_similarity = max(SIMILARITY_THRESHOLD, _RECALL_GUARD_MIN_SIMILARITY)
    for item in results:
        similarity = float(item.get("similarity") or 0.0)
        if similarity < min_similarity:
            suppressed_low_similarity += 1
            continue

        item_text = str(item.get("text") or "")
        item_domains = _infer_recall_domains(item_text)
        if (
            not cross_channel
            and not explicit_domains
            and not (query_domains & guard_target_domains)
            and (item_domains & guard_target_domains)
        ):
            suppressed_domain += 1
            continue
        guarded_results.append(item)

    if guarded_results:
        results = guarded_results
    elif suppressed_domain or suppressed_low_similarity:
        results = []

    if suppressed_domain or suppressed_low_similarity:
        notes: list[str] = []
        if suppressed_domain:
            notes.append(f"suppressed {suppressed_domain} out-of-scope sports/WWE memory candidates")
        if suppressed_low_similarity:
            notes.append(
                f"suppressed {suppressed_low_similarity} low-relevance memory candidates (<{min_similarity:.0%})"
            )
        _set_recall_guard_notes(notes)

        resolved_channel_id, resolved_thread_id = _resolve_scope(channel_id=channel_id, thread_id=thread_id)
        if resolved_channel_id:
            try:
                from runtime_state import record_scoped_recall_alert

                record_scoped_recall_alert(
                    category="contamination_guard_block",
                    message="Recall guard suppressed potentially contaminating context candidates.",
                    channel_id=resolved_channel_id,
                    thread_id=resolved_thread_id,
                    metadata={
                        "suppressed_domain": suppressed_domain,
                        "suppressed_low_similarity": suppressed_low_similarity,
                        "query": query[:200],
                        "cross_channel": cross_channel,
                        "explicit_domains": sorted(explicit_domains),
                    },
                )
            except Exception:
                pass

    if not results:
        return ""

    lines = ["[Your Memory]"]
    for r in results:
        source = r["collection"].replace("_", " ").title()
        sim = r.get("similarity", 0)
        text = r["text"][:200].replace("\n", " ").strip()
        lines.append(f"- [{source} · {sim:.0%}] {text}")

    return "\n".join(lines)
