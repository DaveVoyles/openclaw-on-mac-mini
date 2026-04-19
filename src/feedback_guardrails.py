"""Feedback guardrails: deduplication and rate-limiting for feedback interactions.

Extracted from bot.py — contains module-level state and three guard functions:
- _prune_feedback_event_buffer
- _apply_feedback_guardrails
- _reset_feedback_guardrails_for_tests
"""

from __future__ import annotations

import sys as _sys
import threading
import time

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_FEEDBACK_DEDUPE_WINDOW_SECONDS = 2.0
_FEEDBACK_USER_RATE_LIMIT_WINDOW_SECONDS = 60.0
_FEEDBACK_USER_RATE_LIMIT_MAX = 6
_FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS = 60.0
_FEEDBACK_CHANNEL_RATE_LIMIT_MAX = 40
_FEEDBACK_GUARDRAIL_LOCK = threading.Lock()
_FEEDBACK_RECENT_EVENTS: dict[tuple[int, int, int, str], float] = {}
_FEEDBACK_USER_EVENTS: dict[tuple[int, int], list[float]] = {}
_FEEDBACK_CHANNEL_EVENTS: dict[int, list[float]] = {}


def _fg_const(name: str, local_val):
    """Read constant from bot module if available (test monkeypatching), else use local."""
    m = _sys.modules.get("bot")
    if m is not None and hasattr(m, name):
        return getattr(m, name)
    return local_val


def _prune_feedback_event_buffer(events: list[float], now: float, window_seconds: float) -> list[float]:
    cutoff = now - max(0.0, float(window_seconds))
    return [ts for ts in events if ts >= cutoff]


def _apply_feedback_guardrails(
    *,
    user_id: int | None,
    channel_id: int | None,
    message_id: int | None,
    rating: str,
    now: float | None = None,
) -> tuple[bool, str]:
    """Return (accepted, decision_reason) for a feedback interaction."""
    resolved_now = float(now) if now is not None else time.monotonic()
    safe_user_id = int(user_id or 0)
    safe_channel_id = int(channel_id or 0)
    safe_message_id = int(message_id or 0)
    normalized_rating = str(rating or "").strip().lower() or "unknown"

    dedupe_key = (safe_user_id, safe_channel_id, safe_message_id, normalized_rating)
    user_key = (safe_user_id, safe_channel_id)

    with _FEEDBACK_GUARDRAIL_LOCK:
        dedupe_cutoff = resolved_now - max(
            0.0, _fg_const("_FEEDBACK_DEDUPE_WINDOW_SECONDS", _FEEDBACK_DEDUPE_WINDOW_SECONDS)
        )
        for key, ts in list(_FEEDBACK_RECENT_EVENTS.items()):
            if ts < dedupe_cutoff:
                _FEEDBACK_RECENT_EVENTS.pop(key, None)

        previous_ts = _FEEDBACK_RECENT_EVENTS.get(dedupe_key)
        if previous_ts is not None and (resolved_now - previous_ts) < _fg_const(
            "_FEEDBACK_DEDUPE_WINDOW_SECONDS", _FEEDBACK_DEDUPE_WINDOW_SECONDS
        ):
            return False, "dedupe"

        user_events = _prune_feedback_event_buffer(
            _FEEDBACK_USER_EVENTS.get(user_key, []),
            resolved_now,
            _fg_const("_FEEDBACK_USER_RATE_LIMIT_WINDOW_SECONDS", _FEEDBACK_USER_RATE_LIMIT_WINDOW_SECONDS),
        )
        _FEEDBACK_USER_EVENTS[user_key] = user_events
        if len(user_events) >= _fg_const("_FEEDBACK_USER_RATE_LIMIT_MAX", _FEEDBACK_USER_RATE_LIMIT_MAX):
            _FEEDBACK_RECENT_EVENTS[dedupe_key] = resolved_now
            return False, "rate_limited_user"

        channel_events = _prune_feedback_event_buffer(
            _FEEDBACK_CHANNEL_EVENTS.get(safe_channel_id, []),
            resolved_now,
            _fg_const("_FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS", _FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS),
        )
        _FEEDBACK_CHANNEL_EVENTS[safe_channel_id] = channel_events
        if len(channel_events) >= _fg_const("_FEEDBACK_CHANNEL_RATE_LIMIT_MAX", _FEEDBACK_CHANNEL_RATE_LIMIT_MAX):
            _FEEDBACK_RECENT_EVENTS[dedupe_key] = resolved_now
            return False, "rate_limited_channel"

        user_events.append(resolved_now)
        channel_events.append(resolved_now)
        _FEEDBACK_RECENT_EVENTS[dedupe_key] = resolved_now
        return True, "accepted"


def _reset_feedback_guardrails_for_tests() -> None:
    with _FEEDBACK_GUARDRAIL_LOCK:
        _FEEDBACK_RECENT_EVENTS.clear()
        _FEEDBACK_USER_EVENTS.clear()
        _FEEDBACK_CHANNEL_EVENTS.clear()
