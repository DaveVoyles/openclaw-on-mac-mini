"""Vector store compaction, retention, decay, and access tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from vector_store_config import _RETENTION_PROTECT_SECONDS
from vector_store_scope import _combine_scope_where

log = logging.getLogger("openclaw.vector_store")


def _compaction_priority(doc_id: str, meta: dict[str, Any]) -> tuple[Any, ...]:
    """Lower values are less relevant/older and get pruned first."""
    access_count = int(meta.get("access_count", 0) or 0)
    last_accessed = float(meta.get("last_accessed", 0) or 0)
    added_at = float(meta.get("added_at", 0) or 0)
    return access_count, last_accessed, added_at, str(doc_id)


def _retention_window_seconds(retention_class: str | None) -> int:
    normalized = (retention_class or "standard").strip().lower()
    return _RETENTION_PROTECT_SECONDS.get(normalized, _RETENTION_PROTECT_SECONDS["standard"])


async def _compact_scope_if_needed(
    *,
    collection_name: str,
    channel_id: str | None,
    thread_id: str | None,
) -> dict[str, Any] | None:
    if not channel_id:
        return None
    try:
        from runtime_state import (
            get_memory_lifecycle_policy,
            record_memory_compaction_event,
        )
    except ImportError:
        return None

    try:
        policy = get_memory_lifecycle_policy(
            channel_id=int(channel_id),
            thread_id=int(thread_id) if thread_id is not None else None,
        )
    except Exception:  # broad: intentional — policy fetch can fail in many ways
        return None

    retention_class = str(policy.get("retention_class", "standard"))
    memory_budget_items = max(1, int(policy.get("memory_budget_items", 200)))
    protection_window = _retention_window_seconds(retention_class)

    def _compact() -> dict[str, Any] | None:
        from vector_store_client import _get_collection  # lazy — avoids circular dep
        col = _get_collection(collection_name)
        where = _combine_scope_where(None, channel_id=channel_id, thread_id=thread_id)
        if col.count() == 0:
            return None
        results = col.get(where=where, include=["metadatas"])
        ids = list(results.get("ids", []) or [])
        metadatas = list(results.get("metadatas", []) or [])
        if len(ids) <= memory_budget_items:
            return None

        now = time.time()
        overflow = len(ids) - memory_budget_items
        candidates: list[tuple[str, dict[str, Any]]] = []
        protected: list[tuple[str, dict[str, Any]]] = []
        for idx, doc_id in enumerate(ids):
            meta = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            added_at = float(meta.get("added_at", 0) or 0)
            age_seconds = max(0.0, now - added_at) if added_at else float("inf")
            row = (doc_id, meta)
            if protection_window > 0 and age_seconds < protection_window:
                protected.append(row)
            else:
                candidates.append(row)

        candidates.sort(key=lambda row: _compaction_priority(row[0], row[1]))
        protected.sort(key=lambda row: _compaction_priority(row[0], row[1]))
        ordered = [*candidates, *protected]
        prune_ids = [row[0] for row in ordered[:overflow]]
        if not prune_ids:
            return None
        col.delete(ids=prune_ids)
        return {
            "collection": collection_name,
            "scope": {"channel_id": channel_id, "thread_id": thread_id},
            "retention_class": retention_class,
            "memory_budget_items": memory_budget_items,
            "before_count": len(ids),
            "after_count": len(ids) - len(prune_ids),
            "pruned_count": len(prune_ids),
            "pruned_ids": prune_ids,
            "protected_recent_count": len(protected),
        }

    loop = asyncio.get_running_loop()
    event = await loop.run_in_executor(None, _compact)
    if not event:
        return None
    try:
        record_memory_compaction_event(
            collection=collection_name,
            channel_id=channel_id,
            thread_id=thread_id,
            retention_class=retention_class,
            memory_budget_items=memory_budget_items,
            before_count=event["before_count"],
            after_count=event["after_count"],
            pruned_count=event["pruned_count"],
            metadata={
                "protected_recent_count": event.get("protected_recent_count", 0),
                "pruned_ids": event.get("pruned_ids", [])[:20],
            },
        )
    except (OSError, RuntimeError):
        pass
    try:
        from audit import audit_log

        audit_log(
            "memory-lifecycle",
            "memory_compaction",
            detail=json.dumps(event, separators=(",", ":")),
        )
    except (ImportError, OSError, RuntimeError):
        pass
    return event


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
        from vector_store_client import _get_collection  # lazy — avoids circular dep
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
        except Exception as exc:  # broad: intentional — ChromaDB can raise various errors (RuntimeError, etc.)
            log.debug("ChromaDB where-filter fallback triggered: %s", exc)
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
        from vector_store_client import _get_collection  # lazy — avoids circular dep
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
            except Exception as exc:  # broad: intentional — ChromaDB can raise various errors
                log.debug("Mark decayed failed for %s: %s", doc_id, exc)
        return count

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _mark)


async def bump_access(collection_name: str, doc_ids: list[str]) -> None:
    """Increment access_count and update last_accessed for retrieved documents.

    Called after search results are returned so frequently-accessed memories
    rank higher over time (reinforcement) while unused ones decay.
    """
    if not doc_ids:
        return

    def _bump():
        from vector_store_client import _get_collection  # lazy — avoids circular dep
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
            except (ValueError, KeyError, AttributeError) as exc:
                log.debug("Access bump failed for %s: %s", doc_id, exc)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _bump)
    except (RuntimeError, asyncio.CancelledError) as e:
        log.debug("Access bump failed: %s", e)
