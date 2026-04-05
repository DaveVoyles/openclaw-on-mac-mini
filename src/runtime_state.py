"""Small runtime registry for objects that need cross-module access."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from discord.ext import commands

_BOT: commands.Bot | None = None
_CURRENT_CHANNEL_ID: ContextVar[int | None] = ContextVar(
    "openclaw_current_channel_id",
    default=None,
)
_CURRENT_THREAD_ID: ContextVar[int | None] = ContextVar(
    "openclaw_current_thread_id",
    default=None,
)
_CURRENT_USER_ID: ContextVar[str | None] = ContextVar(
    "openclaw_current_user_id",
    default=None,
)

_CHANNEL_PROFILE_DEFAULTS: dict[str, str] = {
    "tone": "neutral",
    "table_style": "discord",
    "emoji_level": "light",
    "report_depth": "standard",
    "source_strictness": "balanced",
}
_CHANNEL_PROFILE_ALLOWED: dict[str, set[str]] = {
    "tone": {"neutral", "concise", "analytical", "friendly"},
    "table_style": {"discord", "copy-safe"},
    "emoji_level": {"none", "light", "rich"},
    "report_depth": {"brief", "standard", "detailed"},
    "source_strictness": {"balanced", "strict"},
}
_CHANNEL_PROFILE_DB: sqlite3.Connection | None = None
_CHANNEL_PROFILE_LOCK = threading.Lock()


def _channel_profile_db_path() -> Path:
    return Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))


def _get_channel_profile_db() -> sqlite3.Connection:
    global _CHANNEL_PROFILE_DB
    with _CHANNEL_PROFILE_LOCK:
        if _CHANNEL_PROFILE_DB is None:
            db_path = _channel_profile_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _CHANNEL_PROFILE_DB = sqlite3.connect(str(db_path), check_same_thread=False)
            _CHANNEL_PROFILE_DB.row_factory = sqlite3.Row
            _CHANNEL_PROFILE_DB.execute("PRAGMA journal_mode=WAL")
            _CHANNEL_PROFILE_DB.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_profiles (
                    channel_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL DEFAULT 0,
                    tone TEXT NOT NULL,
                    table_style TEXT NOT NULL,
                    emoji_level TEXT NOT NULL,
                    report_depth TEXT NOT NULL,
                    source_strictness TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (channel_id, thread_id)
                )
                """
            )
            _CHANNEL_PROFILE_DB.commit()
    return _CHANNEL_PROFILE_DB


def _normalize_profile_value(field: str, value: str | None) -> str:
    default = _CHANNEL_PROFILE_DEFAULTS[field]
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    allowed = _CHANNEL_PROFILE_ALLOWED[field]
    return normalized if normalized in allowed else default


def _scope_thread_id(thread_id: int | None) -> int:
    return int(thread_id or 0)


def get_channel_profile_defaults() -> dict[str, str]:
    return dict(_CHANNEL_PROFILE_DEFAULTS)


def get_channel_profile(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
) -> dict[str, str]:
    """Return effective profile for channel/thread, falling back to defaults."""
    profile = get_channel_profile_defaults()
    if not channel_id:
        return profile

    db = _get_channel_profile_db()
    scoped_thread_id = _scope_thread_id(thread_id)
    rows = db.execute(
        """
        SELECT tone, table_style, emoji_level, report_depth, source_strictness
        FROM channel_profiles
        WHERE channel_id = ? AND thread_id IN (0, ?)
        ORDER BY thread_id DESC
        LIMIT 1
        """,
        (int(channel_id), scoped_thread_id),
    ).fetchall()
    if not rows:
        return profile

    row = rows[0]
    for field in profile:
        profile[field] = _normalize_profile_value(field, row[field])
    return profile


def get_effective_channel_profile(
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
) -> dict[str, str]:
    resolved_channel_id = channel_id if channel_id is not None else get_current_channel_id()
    resolved_thread_id = thread_id if thread_id is not None else get_current_thread_id()
    return get_channel_profile(resolved_channel_id, thread_id=resolved_thread_id)


def set_channel_profile(
    channel_id: int,
    *,
    thread_id: int | None = None,
    tone: str | None = None,
    table_style: str | None = None,
    emoji_level: str | None = None,
    report_depth: str | None = None,
    source_strictness: str | None = None,
) -> dict[str, str]:
    """Create/update a channel (or thread override) profile and return effective values."""
    if not channel_id:
        raise ValueError("channel_id is required")

    current = get_channel_profile(channel_id, thread_id=thread_id)
    updates: dict[str, Any] = {
        "tone": tone,
        "table_style": table_style,
        "emoji_level": emoji_level,
        "report_depth": report_depth,
        "source_strictness": source_strictness,
    }
    merged = {
        field: _normalize_profile_value(field, value if value is not None else current[field])
        for field, value in updates.items()
    }

    db = _get_channel_profile_db()
    db.execute(
        """
        INSERT INTO channel_profiles (
            channel_id, thread_id, tone, table_style, emoji_level, report_depth, source_strictness, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, thread_id) DO UPDATE SET
            tone=excluded.tone,
            table_style=excluded.table_style,
            emoji_level=excluded.emoji_level,
            report_depth=excluded.report_depth,
            source_strictness=excluded.source_strictness,
            updated_at=excluded.updated_at
        """,
        (
            int(channel_id),
            _scope_thread_id(thread_id),
            merged["tone"],
            merged["table_style"],
            merged["emoji_level"],
            merged["report_depth"],
            merged["source_strictness"],
            time.time(),
        ),
    )
    db.commit()
    return merged


def clear_channel_profile(channel_id: int, *, thread_id: int | None = None) -> None:
    if not channel_id:
        return
    db = _get_channel_profile_db()
    db.execute(
        "DELETE FROM channel_profiles WHERE channel_id = ? AND thread_id = ?",
        (int(channel_id), _scope_thread_id(thread_id)),
    )
    db.commit()


def _reset_channel_profile_store_for_tests() -> None:
    """Reset channel profile DB connection (tests only)."""
    global _CHANNEL_PROFILE_DB
    with _CHANNEL_PROFILE_LOCK:
        if _CHANNEL_PROFILE_DB is not None:
            _CHANNEL_PROFILE_DB.close()
            _CHANNEL_PROFILE_DB = None


ANCHOR_EXPIRY_SECONDS = 1800  # 30 minutes
_ANCHOR_STATE_LOCK = threading.Lock()
_LAST_ANCHOR_STATE: dict[str, Any] | None = None
_ANCHOR_STATE_BY_SCOPE: dict[tuple[int, int | None], dict[str, Any]] = {}

_CONTEXT_LOCKS: dict[str, dict[str, Any]] = {}
_CONTEXT_LOCKS_LOCK = threading.Lock()

_SCOPED_RECALL_ALERTS: list[dict[str, Any]] = []
_SCOPED_RECALL_ALERTS_LOCK = threading.Lock()
_MAX_SCOPED_RECALL_ALERTS = 200


def _scope_key(channel_id: int, thread_id: int | None) -> tuple[int, int | None]:
    return int(channel_id), (int(thread_id) if thread_id is not None else None)


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

        if channel_id is not None:
            scoped = _ANCHOR_STATE_BY_SCOPE.get(_scope_key(channel_id, thread_id))
            return dict(scoped) if scoped else None
        return dict(_LAST_ANCHOR_STATE) if _LAST_ANCHOR_STATE else None


def reset_anchor_state(channel_id: int | None = None, thread_id: int | None = None) -> None:
    """Clear anchor state for a scope, or clear all anchors when scope is omitted."""
    with _ANCHOR_STATE_LOCK:
        global _LAST_ANCHOR_STATE
        if channel_id is None:
            _ANCHOR_STATE_BY_SCOPE.clear()
            _LAST_ANCHOR_STATE = None
            return
        _ANCHOR_STATE_BY_SCOPE.pop(_scope_key(channel_id, thread_id), None)
        latest = _LAST_ANCHOR_STATE
        if latest and latest.get("channel_id") == int(channel_id):
            latest_thread = latest.get("thread_id")
            if latest_thread == (int(thread_id) if thread_id is not None else None):
                _LAST_ANCHOR_STATE = None


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

def set_bot(bot: commands.Bot) -> None:
    """Register the live Discord bot instance for runtime helpers."""
    global _BOT
    _BOT = bot


def get_bot() -> commands.Bot | None:
    """Return the active Discord bot instance if one has been registered."""
    return _BOT


@contextmanager
def request_context(
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    user_id: str | None = None,
):
    """Bind the active Discord channel and user for the current request/tool call."""
    channel_token = None
    thread_token = None
    user_token = None
    if channel_id is not None:
        channel_token = _CURRENT_CHANNEL_ID.set(channel_id)
    if thread_id is not None:
        thread_token = _CURRENT_THREAD_ID.set(thread_id)
    if user_id is not None:
        user_token = _CURRENT_USER_ID.set(user_id)
    try:
        yield
    finally:
        if channel_token is not None:
            _CURRENT_CHANNEL_ID.reset(channel_token)
        if thread_token is not None:
            _CURRENT_THREAD_ID.reset(thread_token)
        if user_token is not None:
            _CURRENT_USER_ID.reset(user_token)


def get_current_channel_id() -> int | None:
    """Return the active Discord channel bound to the current request, if any."""
    return _CURRENT_CHANNEL_ID.get()


def get_current_thread_id() -> int | None:
    """Return the active Discord thread bound to the current request, if any."""
    return _CURRENT_THREAD_ID.get()


def set_current_user_id(user_id: str) -> None:
    """Set the current user ID for the request context."""
    _CURRENT_USER_ID.set(user_id)


def get_current_user_id() -> str | None:
    """Return the active Discord user ID bound to the current request, if any."""
    return _CURRENT_USER_ID.get()
