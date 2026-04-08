"""Memory compaction event tracking."""

from __future__ import annotations

import time
from typing import Any

from channel_profile_state import _CONVERSATION_STATE

# Module-level aliases pointing to the shared _CONVERSATION_STATE containers.
_MEMORY_COMPACTION_EVENTS = _CONVERSATION_STATE.memory_compaction_events
_MEMORY_COMPACTION_EVENTS_LOCK = _CONVERSATION_STATE.memory_compaction_events_lock
_MAX_MEMORY_COMPACTION_EVENTS: int = _CONVERSATION_STATE.max_memory_compaction_events


def record_memory_compaction_event(
    *,
    collection: str,
    channel_id: int | str | None,
    thread_id: int | str | None,
    retention_class: str,
    memory_budget_items: int,
    before_count: int,
    after_count: int,
    pruned_count: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "timestamp": time.time(),
        "collection": (collection or "unknown").strip(),
        "channel_id": str(channel_id) if channel_id not in (None, "") else None,
        "thread_id": str(thread_id) if thread_id not in (None, "") else None,
        "retention_class": (retention_class or "standard").strip().lower(),
        "memory_budget_items": int(memory_budget_items),
        "before_count": int(before_count),
        "after_count": int(after_count),
        "pruned_count": int(pruned_count),
        "metadata": dict(metadata or {}),
    }
    with _MEMORY_COMPACTION_EVENTS_LOCK:
        _MEMORY_COMPACTION_EVENTS.append(event)
        if len(_MEMORY_COMPACTION_EVENTS) > _MAX_MEMORY_COMPACTION_EVENTS:
            del _MEMORY_COMPACTION_EVENTS[
                : len(_MEMORY_COMPACTION_EVENTS) - _MAX_MEMORY_COMPACTION_EVENTS
            ]
    return dict(event)


def get_memory_compaction_events(
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized_channel = str(channel_id) if channel_id not in (None, "") else None
    normalized_thread = str(thread_id) if thread_id not in (None, "") else None
    capped_limit = max(1, min(int(limit), 100))

    with _MEMORY_COMPACTION_EVENTS_LOCK:
        items = list(_MEMORY_COMPACTION_EVENTS)
    if normalized_channel is not None:
        items = [item for item in items if item.get("channel_id") == normalized_channel]
    if normalized_thread is not None:
        items = [item for item in items if item.get("thread_id") == normalized_thread]
    items.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return items[:capped_limit]
