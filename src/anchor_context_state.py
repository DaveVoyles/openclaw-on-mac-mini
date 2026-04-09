"""Anchor state, context locks, and scoped recall alerts."""

from __future__ import annotations

import time
from typing import Any

from channel_profile_state import _CONVERSATION_STATE

# Module-level aliases pointing to the shared _CONVERSATION_STATE containers
# so identity checks via RUNTIME_STATE_CONTEXTS remain valid.
ANCHOR_EXPIRY_SECONDS: int = _CONVERSATION_STATE.anchor_expiry_seconds
CONTEXT_LOCK_EXPIRY_SECONDS: int = _CONVERSATION_STATE.context_lock_expiry_seconds
_ANCHOR_STATE_LOCK = _CONVERSATION_STATE.anchor_state_lock
_ANCHOR_STATE_BY_SCOPE = _CONVERSATION_STATE.anchor_state_by_scope
_CONTEXT_LOCKS = _CONVERSATION_STATE.context_locks
_CONTEXT_LOCKS_LOCK = _CONVERSATION_STATE.context_locks_lock
_SCOPED_RECALL_ALERTS = _CONVERSATION_STATE.scoped_recall_alerts
_SCOPED_RECALL_ALERTS_LOCK = _CONVERSATION_STATE.scoped_recall_alerts_lock
_MAX_SCOPED_RECALL_ALERTS: int = _CONVERSATION_STATE.max_scoped_recall_alerts

# Local mutable reference for _LAST_ANCHOR_STATE (scalar, kept in sync with
# _CONVERSATION_STATE.last_anchor_state via explicit assignments in each mutator).
_LAST_ANCHOR_STATE: dict[str, Any] | None = _CONVERSATION_STATE.last_anchor_state


def _scope_key(channel_id: int, thread_id: int | None) -> tuple[int, int | None]:
    return int(channel_id), (int(thread_id) if thread_id is not None else None)


def _anchor_matches_scope(
    anchor: dict[str, Any] | None,
    *,
    channel_id: int,
    thread_id: int | None,
) -> bool:
    if not anchor:
        return False
    try:
        anchor_channel_id = int(anchor.get("channel_id"))
    except (TypeError, ValueError):
        return False
    anchor_thread = anchor.get("thread_id")
    anchor_thread_id = int(anchor_thread) if anchor_thread is not None else None
    return anchor_channel_id == int(channel_id) and anchor_thread_id == (
        int(thread_id) if thread_id is not None else None
    )


def _get_scoped_anchor_snapshot(channel_id: int, thread_id: int | None) -> dict[str, Any] | None:
    scoped = _ANCHOR_STATE_BY_SCOPE.get(_scope_key(channel_id, thread_id))
    if scoped:
        return dict(scoped)
    latest = _LAST_ANCHOR_STATE
    if _anchor_matches_scope(latest, channel_id=channel_id, thread_id=thread_id):
        return dict(latest)
    return None


def _is_anchor_expired(anchor: dict[str, Any], now: float | None = None) -> bool:
    ts = anchor.get("timestamp")
    if not ts:
        return False
    current = now or time.time()
    return current - float(ts) > ANCHOR_EXPIRY_SECONDS


def set_anchor_state(
    channel_id: int,
    thread_id: int | None,
    anchor_id: str,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Set or refresh follow-up anchor state for a specific channel/thread scope."""
    anchor = {
        "channel_id": int(channel_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "anchor_id": str(anchor_id),
        "timestamp": float(timestamp or time.time()),
    }
    with _ANCHOR_STATE_LOCK:
        _ANCHOR_STATE_BY_SCOPE[_scope_key(anchor["channel_id"], anchor["thread_id"])] = dict(anchor)
        global _LAST_ANCHOR_STATE
        _LAST_ANCHOR_STATE = dict(anchor)
        _CONVERSATION_STATE.last_anchor_state = dict(anchor)
    return anchor


def get_anchor_state(channel_id: int | None = None, thread_id: int | None = None) -> dict[str, Any] | None:
    """Return active anchor state for scope, or latest anchor if no scope is supplied."""
    with _ANCHOR_STATE_LOCK:
        now = time.time()
        expired_keys = [key for key, value in _ANCHOR_STATE_BY_SCOPE.items() if _is_anchor_expired(value, now)]
        for key in expired_keys:
            _ANCHOR_STATE_BY_SCOPE.pop(key, None)

        global _LAST_ANCHOR_STATE
        if _LAST_ANCHOR_STATE and _is_anchor_expired(_LAST_ANCHOR_STATE, now):
            _LAST_ANCHOR_STATE = None
            _CONVERSATION_STATE.last_anchor_state = None

        if channel_id is not None:
            return _get_scoped_anchor_snapshot(channel_id, thread_id)
        return dict(_LAST_ANCHOR_STATE) if _LAST_ANCHOR_STATE else None


def reset_anchor_state(channel_id: int | None = None, thread_id: int | None = None) -> None:
    """Clear anchor state for a scope, or clear all anchors when scope is omitted."""
    with _ANCHOR_STATE_LOCK:
        global _LAST_ANCHOR_STATE
        if channel_id is None:
            _ANCHOR_STATE_BY_SCOPE.clear()
            _LAST_ANCHOR_STATE = None
            _CONVERSATION_STATE.last_anchor_state = None
            return
        _ANCHOR_STATE_BY_SCOPE.pop(_scope_key(channel_id, thread_id), None)
        latest = _LAST_ANCHOR_STATE
        if latest and latest.get("channel_id") == int(channel_id):
            latest_thread = latest.get("thread_id")
            if latest_thread == (int(thread_id) if thread_id is not None else None):
                _LAST_ANCHOR_STATE = None
                _CONVERSATION_STATE.last_anchor_state = None


def anchor_matches(channel_id: int, thread_id: int | None) -> bool:
    anchor = get_anchor_state(channel_id=channel_id, thread_id=thread_id)
    return bool(anchor and anchor.get("anchor_id"))


def set_context_lock(
    *,
    user_id: str | int,
    mode: str,
    channel_id: int,
    thread_id: int | None = None,
    anchor_id: str | None = None,
) -> dict[str, Any]:
    """Persist user context lock preferences across follow-up actions."""
    payload = {
        "mode": (mode or "none").strip().lower(),
        "channel_id": int(channel_id),
        "thread_id": int(thread_id) if thread_id is not None else None,
        "anchor_id": str(anchor_id) if anchor_id else None,
        "updated_at": time.time(),
    }
    with _CONTEXT_LOCKS_LOCK:
        _CONTEXT_LOCKS[str(user_id)] = payload
    return dict(payload)


def get_context_lock(user_id: str | int | None) -> dict[str, Any] | None:
    if user_id in (None, ""):
        return None
    with _CONTEXT_LOCKS_LOCK:
        value = _CONTEXT_LOCKS.get(str(user_id))
        return dict(value) if value else None


def resolve_context_lock(
    *,
    user_id: str | int | None,
    channel_id: int | None,
    thread_id: int | None,
    max_age_seconds: int = CONTEXT_LOCK_EXPIRY_SECONDS,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return scope-valid context lock and reason when ignored."""
    lock = get_context_lock(user_id)
    if not lock:
        return None, None

    try:
        updated_at = float(lock.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0

    now = time.time()
    if updated_at <= 0 or (max_age_seconds > 0 and (now - updated_at) > max_age_seconds):
        reset_context_lock(user_id)
        return None, "stale"

    mode = str(lock.get("mode") or "none").strip().lower()
    if mode not in {"channel", "thread", "prior_report"}:
        return None, "invalid"

    if channel_id is None:
        return None, "no_scope"

    try:
        lock_channel_id = int(lock.get("channel_id"))
    except (TypeError, ValueError):
        return None, "invalid"
    if lock_channel_id != int(channel_id):
        return None, "scope_mismatch"

    scoped_thread = int(thread_id) if thread_id is not None else None
    lock_thread = lock.get("thread_id")
    lock_thread_id = int(lock_thread) if lock_thread is not None else None
    if mode in {"thread", "prior_report"} and lock_thread_id != scoped_thread:
        return None, "scope_mismatch"

    return lock, None


def resolve_anchor_state(
    *,
    channel_id: int | None,
    thread_id: int | None,
    prune_stale: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return scope-valid anchor and reason when ignored."""
    if channel_id is None:
        return None, "no_scope"
    with _ANCHOR_STATE_LOCK:
        anchor = _get_scoped_anchor_snapshot(channel_id, thread_id)
        if not anchor:
            return None, None
        if _is_anchor_expired(anchor):
            if prune_stale:
                _ANCHOR_STATE_BY_SCOPE.pop(_scope_key(channel_id, thread_id), None)
                global _LAST_ANCHOR_STATE
                latest = _LAST_ANCHOR_STATE
                if _anchor_matches_scope(latest, channel_id=channel_id, thread_id=thread_id):
                    _LAST_ANCHOR_STATE = None
                    _CONVERSATION_STATE.last_anchor_state = None
            return None, "stale"
        return dict(anchor), None


def reset_context_lock(user_id: str | int | None) -> None:
    if user_id in (None, ""):
        return
    with _CONTEXT_LOCKS_LOCK:
        _CONTEXT_LOCKS.pop(str(user_id), None)


def record_scoped_recall_alert(
    *,
    category: str,
    message: str,
    channel_id: int | str | None,
    thread_id: int | str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "timestamp": time.time(),
        "category": (category or "unknown").strip().lower(),
        "message": (message or "").strip()[:240],
        "channel_id": str(channel_id) if channel_id not in (None, "") else None,
        "thread_id": str(thread_id) if thread_id not in (None, "") else None,
        "metadata": dict(metadata or {}),
    }
    with _SCOPED_RECALL_ALERTS_LOCK:
        _SCOPED_RECALL_ALERTS.append(event)
        if len(_SCOPED_RECALL_ALERTS) > _MAX_SCOPED_RECALL_ALERTS:
            del _SCOPED_RECALL_ALERTS[: len(_SCOPED_RECALL_ALERTS) - _MAX_SCOPED_RECALL_ALERTS]
    return dict(event)


def get_scoped_recall_alerts(
    *,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized_channel = str(channel_id) if channel_id not in (None, "") else None
    normalized_thread = str(thread_id) if thread_id not in (None, "") else None
    capped_limit = max(1, min(int(limit), 100))

    with _SCOPED_RECALL_ALERTS_LOCK:
        items = list(_SCOPED_RECALL_ALERTS)
    if normalized_channel is not None:
        items = [item for item in items if item.get("channel_id") == normalized_channel]
    if normalized_thread is not None:
        items = [item for item in items if item.get("thread_id") == normalized_thread]
    items.sort(key=lambda item: item.get("timestamp", 0), reverse=True)
    return items[:capped_limit]
