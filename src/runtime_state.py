"""Small runtime registry for objects that need cross-module access."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from discord.ext import commands


@dataclass
class _InteractionState:
    bot: commands.Bot | None = None
    channel_id: ContextVar[int | None] = dataclass_field(
        default_factory=lambda: ContextVar("openclaw_current_channel_id", default=None)
    )
    thread_id: ContextVar[int | None] = dataclass_field(
        default_factory=lambda: ContextVar("openclaw_current_thread_id", default=None)
    )
    user_id: ContextVar[str | None] = dataclass_field(
        default_factory=lambda: ContextVar("openclaw_current_user_id", default=None)
    )


@dataclass
class _ChannelConfigState:
    defaults: dict[str, str] = dataclass_field(
        default_factory=lambda: {
            "tone": "neutral",
            "table_style": "discord",
            "emoji_level": "light",
            "report_depth": "standard",
            "source_strictness": "balanced",
            "memory_retention_class": "standard",
            "retrieval_profile": "auto",
        }
    )
    allowed: dict[str, set[str]] = dataclass_field(
        default_factory=lambda: {
            "tone": {"neutral", "concise", "analytical", "friendly"},
            "table_style": {"discord", "copy-safe"},
            "emoji_level": {"none", "light", "rich"},
            "report_depth": {"brief", "standard", "detailed"},
            "source_strictness": {"balanced", "strict"},
            "memory_retention_class": {"short", "standard", "long"},
            "retrieval_profile": {"auto", "general", "sports", "news", "engineering"},
        }
    )
    int_defaults: dict[str, int] = dataclass_field(
        default_factory=lambda: {
            "memory_budget_items": 200,
            "retrieval_min_results_override": 0,
            "retrieval_max_query_variants_override": 0,
            "retrieval_provider_attempt_cap_override": 0,
        }
    )
    usage_signals: set[str] = dataclass_field(
        default_factory=lambda: {
            "table_render_discord",
            "table_render_copy_safe",
            "recap_generated",
            "recap_copy_export",
        }
    )
    db: sqlite3.Connection | None = None
    db_lock: threading.Lock = dataclass_field(default_factory=threading.Lock)


@dataclass
class _ConversationState:
    anchor_expiry_seconds: int = 1800
    context_lock_expiry_seconds: int = 1800
    anchor_state_lock: threading.Lock = dataclass_field(default_factory=threading.Lock)
    last_anchor_state: dict[str, Any] | None = None
    anchor_state_by_scope: dict[tuple[int, int | None], dict[str, Any]] = dataclass_field(default_factory=dict)
    context_locks: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)
    context_locks_lock: threading.Lock = dataclass_field(default_factory=threading.Lock)
    scoped_recall_alerts: list[dict[str, Any]] = dataclass_field(default_factory=list)
    scoped_recall_alerts_lock: threading.Lock = dataclass_field(default_factory=threading.Lock)
    max_scoped_recall_alerts: int = 200
    memory_compaction_events: list[dict[str, Any]] = dataclass_field(default_factory=list)
    memory_compaction_events_lock: threading.Lock = dataclass_field(default_factory=threading.Lock)
    max_memory_compaction_events: int = 200


@dataclass(frozen=True)
class RuntimeStateContexts:
    """Bounded-context handles for internal runtime state decomposition."""

    channel_config: _ChannelConfigState
    conversation: _ConversationState
    interaction: _InteractionState


_INTERACTION_STATE = _InteractionState()
_CHANNEL_CONFIG_STATE = _ChannelConfigState()
_CONVERSATION_STATE = _ConversationState()
RUNTIME_STATE_CONTEXTS = RuntimeStateContexts(
    channel_config=_CHANNEL_CONFIG_STATE,
    conversation=_CONVERSATION_STATE,
    interaction=_INTERACTION_STATE,
)

# Compatibility facades during decomposition.
_BOT = _INTERACTION_STATE.bot
_CURRENT_CHANNEL_ID = _INTERACTION_STATE.channel_id
_CURRENT_THREAD_ID = _INTERACTION_STATE.thread_id
_CURRENT_USER_ID = _INTERACTION_STATE.user_id

_CHANNEL_PROFILE_DEFAULTS = _CHANNEL_CONFIG_STATE.defaults
_CHANNEL_PROFILE_ALLOWED = _CHANNEL_CONFIG_STATE.allowed
_CHANNEL_PROFILE_INT_DEFAULTS = _CHANNEL_CONFIG_STATE.int_defaults
_CHANNEL_PROFILE_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "memory_budget_items": (1, 5000),
    "retrieval_min_results_override": (0, 8),
    "retrieval_max_query_variants_override": (0, 6),
    "retrieval_provider_attempt_cap_override": (0, 6),
}
_CHANNEL_PROFILE_DB = _CHANNEL_CONFIG_STATE.db
_CHANNEL_PROFILE_LOCK = _CHANNEL_CONFIG_STATE.db_lock
_PROFILE_USAGE_SIGNALS = _CHANNEL_CONFIG_STATE.usage_signals


def _channel_profile_db_path() -> Path:
    return Path(os.getenv("THREAD_DB_PATH", "/memory/openclaw.db"))


def _get_channel_profile_db() -> sqlite3.Connection:
    global _CHANNEL_PROFILE_DB
    with _CHANNEL_PROFILE_LOCK:
        if _CHANNEL_PROFILE_DB is None:
            db_path = _channel_profile_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _CHANNEL_PROFILE_DB = sqlite3.connect(str(db_path), check_same_thread=False)
            _CHANNEL_CONFIG_STATE.db = _CHANNEL_PROFILE_DB
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
                    memory_retention_class TEXT NOT NULL DEFAULT 'standard',
                    memory_budget_items INTEGER NOT NULL DEFAULT 200,
                    retrieval_profile TEXT NOT NULL DEFAULT 'auto',
                    retrieval_min_results_override INTEGER NOT NULL DEFAULT 0,
                    retrieval_max_query_variants_override INTEGER NOT NULL DEFAULT 0,
                    retrieval_provider_attempt_cap_override INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (channel_id, thread_id)
                )
                """
            )
            _CHANNEL_PROFILE_DB.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_profile_signals (
                    channel_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL DEFAULT 0,
                    signal TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (channel_id, thread_id, signal)
                )
                """
            )
            _CHANNEL_PROFILE_DB.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_profile_recommendations (
                    recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    thread_id INTEGER NOT NULL DEFAULT 0,
                    profile_field TEXT NOT NULL,
                    recommended_value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    baseline_value TEXT,
                    created_at REAL NOT NULL,
                    decided_at REAL,
                    decision_actor TEXT,
                    applied_at REAL,
                    reverted_at REAL
                )
                """
            )
            _CHANNEL_PROFILE_DB.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_eval_scorecards (
                    scorecard_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    window_hours REAL NOT NULL,
                    sample_size INTEGER NOT NULL,
                    summary_passes INTEGER NOT NULL,
                    summary_failures INTEGER NOT NULL,
                    summary_rate REAL NOT NULL,
                    metrics_json TEXT NOT NULL
                )
                """
            )
            _ensure_channel_profile_schema(_CHANNEL_PROFILE_DB)
            _CHANNEL_PROFILE_DB.commit()
    return _CHANNEL_PROFILE_DB


def _ensure_channel_profile_schema(db: sqlite3.Connection) -> None:
    cols = {
        row["name"]
        for row in db.execute("PRAGMA table_info(channel_profiles)").fetchall()
    }
    if "memory_retention_class" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN memory_retention_class TEXT NOT NULL DEFAULT 'standard'"
        )
    if "memory_budget_items" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN memory_budget_items INTEGER NOT NULL DEFAULT 200"
        )
    if "retrieval_profile" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN retrieval_profile TEXT NOT NULL DEFAULT 'auto'"
        )
    if "retrieval_min_results_override" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN retrieval_min_results_override INTEGER NOT NULL DEFAULT 0"
        )
    if "retrieval_max_query_variants_override" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN retrieval_max_query_variants_override INTEGER NOT NULL DEFAULT 0"
        )
    if "retrieval_provider_attempt_cap_override" not in cols:
        db.execute(
            "ALTER TABLE channel_profiles "
            "ADD COLUMN retrieval_provider_attempt_cap_override INTEGER NOT NULL DEFAULT 0"
        )


def _normalize_profile_value(field: str, value: str | None) -> str:
    default = _CHANNEL_PROFILE_DEFAULTS[field]
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    allowed = _CHANNEL_PROFILE_ALLOWED[field]
    return normalized if normalized in allowed else default


def _normalize_profile_int_value(field: str, value: int | str | None) -> int:
    default = _CHANNEL_PROFILE_INT_DEFAULTS[field]
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    lower, upper = _CHANNEL_PROFILE_INT_BOUNDS.get(field, (1, 5000))
    return max(lower, min(parsed, upper))


def _scope_thread_id(thread_id: int | None) -> int:
    return int(thread_id or 0)


def get_channel_profile_defaults() -> dict[str, str]:
    return dict(_CHANNEL_PROFILE_DEFAULTS)


def get_channel_profile(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
) -> dict[str, Any]:
    """Return effective profile for channel/thread, falling back to defaults."""
    profile: dict[str, Any] = get_channel_profile_defaults()
    profile.update(_CHANNEL_PROFILE_INT_DEFAULTS)
    if not channel_id:
        return profile

    db = _get_channel_profile_db()
    scoped_thread_id = _scope_thread_id(thread_id)
    rows = db.execute(
        """
        SELECT tone, table_style, emoji_level, report_depth, source_strictness,
               memory_retention_class, memory_budget_items, retrieval_profile,
               retrieval_min_results_override, retrieval_max_query_variants_override,
               retrieval_provider_attempt_cap_override
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
    for field in _CHANNEL_PROFILE_DEFAULTS:
        profile[field] = _normalize_profile_value(field, row[field])
    for field in _CHANNEL_PROFILE_INT_DEFAULTS:
        profile[field] = _normalize_profile_int_value(field, row[field])
    return profile


def get_effective_channel_profile(
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
) -> dict[str, Any]:
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
    memory_retention_class: str | None = None,
    memory_budget_items: int | str | None = None,
    retrieval_profile: str | None = None,
    retrieval_min_results_override: int | str | None = None,
    retrieval_max_query_variants_override: int | str | None = None,
    retrieval_provider_attempt_cap_override: int | str | None = None,
) -> dict[str, Any]:
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
        "memory_retention_class": memory_retention_class,
        "retrieval_profile": retrieval_profile,
    }
    merged = {
        field: _normalize_profile_value(field, value if value is not None else current[field])
        for field, value in updates.items()
    }
    merged_int = {
        "memory_budget_items": _normalize_profile_int_value(
            "memory_budget_items",
            memory_budget_items if memory_budget_items is not None else current["memory_budget_items"],
        ),
        "retrieval_min_results_override": _normalize_profile_int_value(
            "retrieval_min_results_override",
            (
                retrieval_min_results_override
                if retrieval_min_results_override is not None
                else current["retrieval_min_results_override"]
            ),
        ),
        "retrieval_max_query_variants_override": _normalize_profile_int_value(
            "retrieval_max_query_variants_override",
            (
                retrieval_max_query_variants_override
                if retrieval_max_query_variants_override is not None
                else current["retrieval_max_query_variants_override"]
            ),
        ),
        "retrieval_provider_attempt_cap_override": _normalize_profile_int_value(
            "retrieval_provider_attempt_cap_override",
            (
                retrieval_provider_attempt_cap_override
                if retrieval_provider_attempt_cap_override is not None
                else current["retrieval_provider_attempt_cap_override"]
            ),
        ),
    }

    db = _get_channel_profile_db()
    db.execute(
        """
        INSERT INTO channel_profiles (
            channel_id, thread_id, tone, table_style, emoji_level, report_depth, source_strictness,
            memory_retention_class, memory_budget_items, retrieval_profile,
            retrieval_min_results_override, retrieval_max_query_variants_override,
            retrieval_provider_attempt_cap_override, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, thread_id) DO UPDATE SET
            tone=excluded.tone,
            table_style=excluded.table_style,
            emoji_level=excluded.emoji_level,
            report_depth=excluded.report_depth,
            source_strictness=excluded.source_strictness,
            memory_retention_class=excluded.memory_retention_class,
            memory_budget_items=excluded.memory_budget_items,
            retrieval_profile=excluded.retrieval_profile,
            retrieval_min_results_override=excluded.retrieval_min_results_override,
            retrieval_max_query_variants_override=excluded.retrieval_max_query_variants_override,
            retrieval_provider_attempt_cap_override=excluded.retrieval_provider_attempt_cap_override,
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
            merged["memory_retention_class"],
            merged_int["memory_budget_items"],
            merged["retrieval_profile"],
            merged_int["retrieval_min_results_override"],
            merged_int["retrieval_max_query_variants_override"],
            merged_int["retrieval_provider_attempt_cap_override"],
            time.time(),
        ),
    )
    db.commit()
    return {**merged, **merged_int}


def get_memory_lifecycle_policy(
    *,
    channel_id: int | None,
    thread_id: int | None = None,
) -> dict[str, Any]:
    profile = get_channel_profile(channel_id, thread_id=thread_id)
    return {
        "retention_class": profile.get("memory_retention_class", "standard"),
        "memory_budget_items": _normalize_profile_int_value(
            "memory_budget_items",
            profile.get("memory_budget_items"),
        ),
    }


def clear_channel_profile(channel_id: int, *, thread_id: int | None = None) -> None:
    if not channel_id:
        return
    db = _get_channel_profile_db()
    db.execute(
        "DELETE FROM channel_profiles WHERE channel_id = ? AND thread_id = ?",
        (int(channel_id), _scope_thread_id(thread_id)),
    )
    db.commit()


def _normalize_usage_signal(signal: str | None) -> str | None:
    normalized = (signal or "").strip().lower()
    return normalized if normalized in _PROFILE_USAGE_SIGNALS else None


def record_channel_profile_signal(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
    signal: str,
    increment: int = 1,
) -> None:
    normalized_signal = _normalize_usage_signal(signal)
    if not channel_id or not normalized_signal:
        return
    safe_increment = max(1, int(increment))
    db = _get_channel_profile_db()
    db.execute(
        """
        INSERT INTO channel_profile_signals (channel_id, thread_id, signal, count, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, thread_id, signal) DO UPDATE SET
            count = count + excluded.count,
            updated_at = excluded.updated_at
        """,
        (int(channel_id), _scope_thread_id(thread_id), normalized_signal, safe_increment, time.time()),
    )
    db.commit()


def get_channel_profile_usage_signals(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
) -> dict[str, int]:
    snapshot = {signal: 0 for signal in sorted(_PROFILE_USAGE_SIGNALS)}
    if not channel_id:
        return snapshot
    db = _get_channel_profile_db()
    rows = db.execute(
        """
        SELECT signal, count
        FROM channel_profile_signals
        WHERE channel_id = ? AND thread_id = ?
        """,
        (int(channel_id), _scope_thread_id(thread_id)),
    ).fetchall()
    for row in rows:
        signal = str(row["signal"])
        if signal in snapshot:
            snapshot[signal] = int(row["count"] or 0)
    return snapshot


def _recommendation_candidates(profile: dict[str, Any], signals: dict[str, int]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    copy_exports = int(signals.get("recap_copy_export", 0))
    recap_count = int(signals.get("recap_generated", 0))
    copy_table_usage = int(signals.get("table_render_copy_safe", 0))
    discord_table_usage = int(signals.get("table_render_discord", 0))

    if profile.get("table_style") != "copy-safe":
        prefers_copy_safe = copy_exports >= 2 or (copy_table_usage >= 4 and copy_table_usage >= discord_table_usage)
        if prefers_copy_safe:
            confidence = min(
                0.95,
                0.55 + (copy_exports * 0.1) + max(copy_table_usage - discord_table_usage, 0) * 0.04,
            )
            candidates.append(
                {
                    "profile_field": "table_style",
                    "recommended_value": "copy-safe",
                    "reason": (
                        "Detected repeat copy-oriented usage "
                        f"(copy exports: {copy_exports}, copy-safe table renders: {copy_table_usage})."
                    ),
                    "confidence": round(confidence, 2),
                }
            )

    if profile.get("report_depth") != "detailed" and recap_count >= 4:
        confidence = min(0.9, 0.5 + recap_count * 0.07)
        candidates.append(
            {
                "profile_field": "report_depth",
                "recommended_value": "detailed",
                "reason": f"Frequent recap requests detected ({recap_count}) in this scope.",
                "confidence": round(confidence, 2),
            }
        )
    return candidates


def _recommendation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "recommendation_id": int(row["recommendation_id"]),
        "channel_id": int(row["channel_id"]),
        "thread_id": int(row["thread_id"]) or None,
        "profile_field": str(row["profile_field"]),
        "recommended_value": str(row["recommended_value"]),
        "reason": str(row["reason"]),
        "confidence": float(row["confidence"]),
        "status": str(row["status"]),
        "baseline_value": row["baseline_value"],
        "created_at": float(row["created_at"]),
        "decided_at": float(row["decided_at"]) if row["decided_at"] else None,
        "decision_actor": row["decision_actor"],
        "applied_at": float(row["applied_at"]) if row["applied_at"] else None,
        "reverted_at": float(row["reverted_at"]) if row["reverted_at"] else None,
    }


def refresh_channel_profile_recommendations(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
) -> list[dict[str, Any]]:
    if not channel_id:
        return []

    profile = get_channel_profile(channel_id, thread_id=thread_id)
    signals = get_channel_profile_usage_signals(channel_id, thread_id=thread_id)
    candidates = _recommendation_candidates(profile, signals)
    db = _get_channel_profile_db()
    now = time.time()
    scoped_thread_id = _scope_thread_id(thread_id)

    for candidate in candidates:
        existing = db.execute(
            """
            SELECT recommendation_id
            FROM channel_profile_recommendations
            WHERE channel_id = ?
              AND thread_id = ?
              AND profile_field = ?
              AND recommended_value = ?
              AND status IN ('suggested', 'approved', 'applied')
            ORDER BY recommendation_id DESC
            LIMIT 1
            """,
            (
                int(channel_id),
                scoped_thread_id,
                candidate["profile_field"],
                candidate["recommended_value"],
            ),
        ).fetchone()
        if existing:
            continue
        db.execute(
            """
            INSERT INTO channel_profile_recommendations (
                channel_id, thread_id, profile_field, recommended_value, reason, confidence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'suggested', ?)
            """,
            (
                int(channel_id),
                scoped_thread_id,
                candidate["profile_field"],
                candidate["recommended_value"],
                candidate["reason"],
                float(candidate["confidence"]),
                now,
            ),
        )
    db.commit()
    return list_channel_profile_recommendations(
        channel_id,
        thread_id=thread_id,
        include_history=False,
        refresh=False,
    )


def list_channel_profile_recommendations(
    channel_id: int | None,
    *,
    thread_id: int | None = None,
    include_history: bool = False,
    refresh: bool = True,
) -> list[dict[str, Any]]:
    if not channel_id:
        return []
    if refresh:
        refresh_channel_profile_recommendations(channel_id, thread_id=thread_id)

    db = _get_channel_profile_db()
    scoped_thread_id = _scope_thread_id(thread_id)
    where_status = "" if include_history else "AND status IN ('suggested', 'approved', 'applied')"
    rows = db.execute(
        f"""
        SELECT *
        FROM channel_profile_recommendations
        WHERE channel_id = ? AND thread_id = ?
        {where_status}
        ORDER BY recommendation_id DESC
        LIMIT 50
        """,
        (int(channel_id), scoped_thread_id),
    ).fetchall()
    return [_recommendation_row_to_dict(row) for row in rows]


def update_channel_profile_recommendation(
    recommendation_id: int,
    *,
    action: str,
    actor: str | None = None,
) -> dict[str, Any]:
    normalized_action = (action or "").strip().lower()
    if normalized_action not in {"approve", "reject", "apply", "revert"}:
        raise ValueError("Invalid action. Use approve, reject, apply, or revert.")

    db = _get_channel_profile_db()
    row = db.execute(
        "SELECT * FROM channel_profile_recommendations WHERE recommendation_id = ?",
        (int(recommendation_id),),
    ).fetchone()
    if row is None:
        raise ValueError("Recommendation not found.")

    status = str(row["status"])
    channel_id = int(row["channel_id"])
    thread_id = int(row["thread_id"]) or None
    profile_field = str(row["profile_field"])
    recommended_value = str(row["recommended_value"])
    actor_name = (actor or "unknown").strip()[:120]
    now = time.time()

    if normalized_action == "approve":
        if status != "suggested":
            raise ValueError("Only suggested recommendations can be approved.")
        db.execute(
            """
            UPDATE channel_profile_recommendations
            SET status = 'approved', decided_at = ?, decision_actor = ?
            WHERE recommendation_id = ?
            """,
            (now, actor_name, int(recommendation_id)),
        )
    elif normalized_action == "reject":
        if status not in {"suggested", "approved"}:
            raise ValueError("Only suggested or approved recommendations can be rejected.")
        db.execute(
            """
            UPDATE channel_profile_recommendations
            SET status = 'rejected', decided_at = ?, decision_actor = ?
            WHERE recommendation_id = ?
            """,
            (now, actor_name, int(recommendation_id)),
        )
    elif normalized_action == "apply":
        if status != "approved":
            raise ValueError("Only approved recommendations can be applied.")
        current_profile = get_channel_profile(channel_id, thread_id=thread_id)
        baseline_value = str(
            current_profile.get(
                profile_field,
                _CHANNEL_PROFILE_DEFAULTS.get(profile_field, _CHANNEL_PROFILE_INT_DEFAULTS.get(profile_field, "")),
            )
        )
        set_channel_profile(
            channel_id,
            thread_id=thread_id,
            **{profile_field: recommended_value},
        )
        db.execute(
            """
            UPDATE channel_profile_recommendations
            SET status = 'applied',
                baseline_value = COALESCE(baseline_value, ?),
                decided_at = ?,
                decision_actor = ?,
                applied_at = ?
            WHERE recommendation_id = ?
            """,
            (baseline_value, now, actor_name, now, int(recommendation_id)),
        )
    else:
        if status != "applied":
            raise ValueError("Only applied recommendations can be reverted.")
        baseline_value = str(
            row["baseline_value"]
            or _CHANNEL_PROFILE_DEFAULTS.get(profile_field, _CHANNEL_PROFILE_INT_DEFAULTS.get(profile_field, ""))
        )
        set_channel_profile(
            channel_id,
            thread_id=thread_id,
            **{profile_field: baseline_value},
        )
        db.execute(
            """
            UPDATE channel_profile_recommendations
            SET status = 'reverted',
                decided_at = ?,
                decision_actor = ?,
                reverted_at = ?
            WHERE recommendation_id = ?
            """,
            (now, actor_name, now, int(recommendation_id)),
        )

    db.commit()
    updated = db.execute(
        "SELECT * FROM channel_profile_recommendations WHERE recommendation_id = ?",
        (int(recommendation_id),),
    ).fetchone()
    return _recommendation_row_to_dict(updated) if updated else {}


def _reset_channel_profile_store_for_tests() -> None:
    """Reset channel profile DB connection (tests only)."""
    global _CHANNEL_PROFILE_DB
    with _CHANNEL_PROFILE_LOCK:
        if _CHANNEL_PROFILE_DB is not None:
            _CHANNEL_PROFILE_DB.close()
            _CHANNEL_PROFILE_DB = None
            _CHANNEL_CONFIG_STATE.db = None


ANCHOR_EXPIRY_SECONDS = _CONVERSATION_STATE.anchor_expiry_seconds
CONTEXT_LOCK_EXPIRY_SECONDS = _CONVERSATION_STATE.context_lock_expiry_seconds
_ANCHOR_STATE_LOCK = _CONVERSATION_STATE.anchor_state_lock
_LAST_ANCHOR_STATE = _CONVERSATION_STATE.last_anchor_state
_ANCHOR_STATE_BY_SCOPE = _CONVERSATION_STATE.anchor_state_by_scope

_CONTEXT_LOCKS = _CONVERSATION_STATE.context_locks
_CONTEXT_LOCKS_LOCK = _CONVERSATION_STATE.context_locks_lock

_SCOPED_RECALL_ALERTS = _CONVERSATION_STATE.scoped_recall_alerts
_SCOPED_RECALL_ALERTS_LOCK = _CONVERSATION_STATE.scoped_recall_alerts_lock
_MAX_SCOPED_RECALL_ALERTS = _CONVERSATION_STATE.max_scoped_recall_alerts
_MEMORY_COMPACTION_EVENTS = _CONVERSATION_STATE.memory_compaction_events
_MEMORY_COMPACTION_EVENTS_LOCK = _CONVERSATION_STATE.memory_compaction_events_lock
_MAX_MEMORY_COMPACTION_EVENTS = _CONVERSATION_STATE.max_memory_compaction_events


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
    key = _scope_key(channel_id, thread_id)
    with _ANCHOR_STATE_LOCK:
        anchor = _ANCHOR_STATE_BY_SCOPE.get(key)
        if not anchor:
            return None, None
        if _is_anchor_expired(anchor):
            if prune_stale:
                _ANCHOR_STATE_BY_SCOPE.pop(key, None)
                global _LAST_ANCHOR_STATE
                latest = _LAST_ANCHOR_STATE
                if latest and latest.get("channel_id") == int(channel_id):
                    latest_thread = latest.get("thread_id")
                    if latest_thread == (int(thread_id) if thread_id is not None else None):
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


_CROSS_CHANNEL_OPT_IN_RE = re.compile(r"(?i)(--cross-channel\b|#cross-channel\b|\[cross-channel\])")
_FOLLOWUP_HINT_RE = re.compile(r"(?i)^(follow up|what about|and |also |more on |next |continue )")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "]+",
    flags=re.UNICODE,
)
_QUALITY_METRICS = (
    "channel_leakage_prevention",
    "followup_anchor_correctness",
    "profile_adherence",
    "table_readability_copy_safety",
)


def _init_metric_counter() -> dict[str, int]:
    return {"pass": 0, "fail": 0}


def _is_followup_like(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    return len(text.split()) < 10 or _FOLLOWUP_HINT_RE.search(text) is not None


def _contains_markdown_table(text: str) -> bool:
    if not text:
        return False
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        if idx + 1 < len(lines):
            nxt = lines[idx + 1].strip()
            if nxt.startswith("|") and all(c in "|-: " for c in nxt.replace("|", "")):
                return True
    return False


def _contains_discord_table(text: str) -> bool:
    if not text:
        return False
    return "```text" in text and "|" in text and "+" in text


def _contains_copy_safe_table(text: str) -> bool:
    return bool(text and "📋 Table" in text)


def _safe_rate(passes: int, fails: int) -> float:
    total = passes + fails
    return round(passes / total, 3) if total > 0 else 1.0


def build_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    now: float | None = None,
) -> dict[str, Any]:
    """Score recent run/response telemetry across key quality metrics."""
    try:
        from error_tracker import get_recent_outcomes

        runs = list(get_recent_outcomes(hours=window_hours, limit=limit))
    except Exception:
        runs = []

    counters = {name: _init_metric_counter() for name in _QUALITY_METRICS}

    for run in runs:
        if not isinstance(run, dict):
            continue
        question = str(run.get("question") or "")
        response = str(run.get("response_preview") or run.get("response_text") or "")
        scope_mode = str(run.get("scope_mode") or "channel")
        lock_mode = str(run.get("lock_mode") or "none")
        anchor_id = str(run.get("anchor_id") or "").strip()
        profile_values = run.get("profile_values")
        if not isinstance(profile_values, dict):
            profile_values = {}

        # 1) Channel leakage prevention
        if scope_mode in {"channel", "thread", "cross-channel"}:
            opt_in = _CROSS_CHANNEL_OPT_IN_RE.search(question) is not None
            if scope_mode == "cross-channel" and not opt_in:
                counters["channel_leakage_prevention"]["fail"] += 1
            else:
                counters["channel_leakage_prevention"]["pass"] += 1

        # 2) Follow-up anchor correctness
        followup_expected = _is_followup_like(question) or lock_mode == "prior_report"
        if followup_expected or anchor_id:
            if followup_expected and anchor_id:
                counters["followup_anchor_correctness"]["pass"] += 1
            else:
                counters["followup_anchor_correctness"]["fail"] += 1

        # 3) Profile adherence
        if response and profile_values:
            checks: list[bool] = []
            word_count = len(response.split())
            emoji_level = str(profile_values.get("emoji_level") or "light")
            report_depth = str(profile_values.get("report_depth") or "standard")
            tone = str(profile_values.get("tone") or "neutral")

            if emoji_level == "none":
                checks.append(_EMOJI_RE.search(response) is None)
            if report_depth == "brief":
                checks.append(word_count <= 260)
            elif report_depth == "detailed":
                checks.append(word_count >= 80)
            if tone == "concise":
                checks.append(word_count <= 320)

            if all(checks) if checks else True:
                counters["profile_adherence"]["pass"] += 1
            else:
                counters["profile_adherence"]["fail"] += 1

        # 4) Table readability / copy safety
        if response and (
            _contains_markdown_table(response)
            or _contains_discord_table(response)
            or _contains_copy_safe_table(response)
        ):
            expected_style = str(profile_values.get("table_style") or "discord")
            if expected_style == "copy-safe":
                ok = _contains_copy_safe_table(response)
            else:
                ok = _contains_discord_table(response) or _contains_markdown_table(response)
            if ok:
                counters["table_readability_copy_safety"]["pass"] += 1
            else:
                counters["table_readability_copy_safety"]["fail"] += 1

    # Give leakage metric signal credit for blocked attempts
    blocked = [
        item
        for item in get_scoped_recall_alerts(limit=min(100, max(5, limit // 2)))
        if str(item.get("category") or "").strip().lower() == "scope_guard_block"
    ]
    if blocked:
        counters["channel_leakage_prevention"]["pass"] += len(blocked)

    metrics: dict[str, dict[str, Any]] = {}
    summary_passes = 0
    summary_failures = 0
    for name in _QUALITY_METRICS:
        passed = counters[name]["pass"]
        failed = counters[name]["fail"]
        metrics[name] = {
            "pass": passed,
            "fail": failed,
            "sample": passed + failed,
            "rate": _safe_rate(passed, failed),
        }
        summary_passes += passed
        summary_failures += failed

    return {
        "timestamp": float(now if now is not None else time.time()),
        "window_hours": float(window_hours),
        "limit": int(limit),
        "sample_size": int(len(runs)),
        "summary": {
            "pass": summary_passes,
            "fail": summary_failures,
            "rate": _safe_rate(summary_passes, summary_failures),
        },
        "metrics": metrics,
    }


def save_quality_eval_scorecard(
    scorecard: dict[str, Any],
) -> dict[str, Any]:
    """Persist a quality eval scorecard snapshot and return normalized payload."""
    ts = float(scorecard.get("timestamp") or time.time())
    window_hours = float(scorecard.get("window_hours") or 24.0)
    sample_size = int(scorecard.get("sample_size") or 0)
    summary = scorecard.get("summary") if isinstance(scorecard.get("summary"), dict) else {}
    metrics = scorecard.get("metrics") if isinstance(scorecard.get("metrics"), dict) else {}

    summary_passes = int(summary.get("pass") or 0)
    summary_failures = int(summary.get("fail") or 0)
    summary_rate = float(summary.get("rate") or _safe_rate(summary_passes, summary_failures))
    metrics_json = json.dumps(metrics, separators=(",", ":"))

    db = _get_channel_profile_db()
    cur = db.execute(
        """
        INSERT INTO quality_eval_scorecards (
            ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json),
    )
    db.commit()
    return {
        "scorecard_id": int(cur.lastrowid or 0),
        "timestamp": ts,
        "window_hours": window_hours,
        "sample_size": sample_size,
        "summary": {"pass": summary_passes, "fail": summary_failures, "rate": summary_rate},
        "metrics": metrics,
    }


def create_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    persist: bool = True,
) -> dict[str, Any]:
    """Build and optionally persist a quality evaluation scorecard snapshot."""
    scorecard = build_quality_eval_scorecard(window_hours=window_hours, limit=limit)
    if not persist:
        scorecard["scorecard_id"] = None
        return scorecard
    return save_quality_eval_scorecard(scorecard)


def list_quality_eval_scorecards(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent persisted quality scorecards (newest first)."""
    capped = max(1, min(int(limit), 200))
    db = _get_channel_profile_db()
    rows = db.execute(
        """
        SELECT scorecard_id, ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json
        FROM quality_eval_scorecards
        ORDER BY ts DESC, scorecard_id DESC
        LIMIT ?
        """,
        (capped,),
    ).fetchall()
    cards: list[dict[str, Any]] = []
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        except json.JSONDecodeError:
            metrics = {}
        cards.append(
            {
                "scorecard_id": int(row["scorecard_id"]),
                "timestamp": float(row["ts"]),
                "window_hours": float(row["window_hours"]),
                "sample_size": int(row["sample_size"]),
                "summary": {
                    "pass": int(row["summary_passes"]),
                    "fail": int(row["summary_failures"]),
                    "rate": float(row["summary_rate"]),
                },
                "metrics": metrics if isinstance(metrics, dict) else {},
            }
        )
    return cards


def ensure_quality_eval_scorecard(
    *,
    window_hours: float = 24,
    limit: int = 250,
    min_interval_seconds: int = 1800,
) -> dict[str, Any]:
    """Return latest scorecard; create a fresh snapshot when stale."""
    latest = list_quality_eval_scorecards(limit=1)
    if latest:
        age_seconds = time.time() - float(latest[0].get("timestamp") or 0)
        if age_seconds < max(60, int(min_interval_seconds)):
            return latest[0]
    return create_quality_eval_scorecard(window_hours=window_hours, limit=limit, persist=True)

def set_bot(bot: commands.Bot) -> None:
    """Register the live Discord bot instance for runtime helpers."""
    global _BOT
    _BOT = bot
    _INTERACTION_STATE.bot = bot


def get_bot() -> commands.Bot | None:
    """Return the active Discord bot instance if one has been registered."""
    return _INTERACTION_STATE.bot


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
        channel_token = _INTERACTION_STATE.channel_id.set(channel_id)
    if thread_id is not None:
        thread_token = _INTERACTION_STATE.thread_id.set(thread_id)
    if user_id is not None:
        user_token = _INTERACTION_STATE.user_id.set(user_id)
    try:
        yield
    finally:
        if channel_token is not None:
            _INTERACTION_STATE.channel_id.reset(channel_token)
        if thread_token is not None:
            _INTERACTION_STATE.thread_id.reset(thread_token)
        if user_token is not None:
            _INTERACTION_STATE.user_id.reset(user_token)


def get_current_channel_id() -> int | None:
    """Return the active Discord channel bound to the current request, if any."""
    return _INTERACTION_STATE.channel_id.get()


def get_current_thread_id() -> int | None:
    """Return the active Discord thread bound to the current request, if any."""
    return _INTERACTION_STATE.thread_id.get()


def set_current_user_id(user_id: str) -> None:
    """Set the current user ID for the request context."""
    _INTERACTION_STATE.user_id.set(user_id)


def get_current_user_id() -> str | None:
    """Return the active Discord user ID bound to the current request, if any."""
    return _INTERACTION_STATE.user_id.get()
