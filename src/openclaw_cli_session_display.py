"""openclaw_cli_session_display.py — Pure session data formatting and display helpers.

Extracted from openclaw_cli.py. Contains session inspection, summary, share-text,
runbook, mood, and operator-snapshot formatters.

Allowed imports: openclaw_cli_sessions, openclaw_cli_ui_core, openclaw_cli_prefs,
                 openclaw_cli_auth, stdlib only.
Do NOT import from openclaw_cli — circular import.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from openclaw_cli_auth import OpenClawCliError
from openclaw_cli_prefs import (
    _A11Y_PLAIN_MODE,
    _EMOJI_PACKS,
    _PREFS,
    _emoji_pack_name,
)
from openclaw_cli_sessions import (
    SessionSummary,
    build_collaboration_snapshot,
    build_session_storyline,
    export_session,
    list_session_bookmarks,
    load_conversation_history,
    load_watch_state,
    require_session,
)
from openclaw_cli_ui_core import _IS_TTY, _get_is_tty

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rich — graceful fallback when not in a TTY or rich absent
# ---------------------------------------------------------------------------
try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (local copies — keep in sync with openclaw_cli.py)
# ---------------------------------------------------------------------------
WATCH_RETRY_LIMIT = 3
WATCH_PROGRESS_LOG_LIMIT = 25
WATCH_RETRY_MAX_DELAY_SECONDS = 8
WATCH_FOCUS_NOTE_CHARS = 120

_GENERIC_CONTEXT_LIMIT_TOKENS = 128_000
_CONTEXT_LIMIT_SUFFIX_RE = re.compile(r"(?<!\d)(\d{2,4})k(?![a-z0-9])", re.IGNORECASE)
_CONTEXT_LIMIT_M_RE = re.compile(r"(?<!\d)(\d+)m(?![a-z0-9])", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Emoji fallbacks (status-display subset; no status emojis in this dict)
# ---------------------------------------------------------------------------
_EMOJI_FALLBACKS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# A11y helpers
# ---------------------------------------------------------------------------

def _a11y_plain_mode() -> bool:
    """Return True when plain/screen-reader mode is active."""
    return bool(_PREFS.get(_A11Y_PLAIN_MODE, False))


# ---------------------------------------------------------------------------
# Emoji helper
# ---------------------------------------------------------------------------

def _e(emoji: str, fallback: str = "") -> str:
    """Return *emoji* or its ASCII fallback depending on the emoji pref."""
    pack = _emoji_pack_name()
    if pack == "classic":
        return emoji
    if pack == "minimal":
        return _EMOJI_PACKS["minimal"].get(emoji, fallback or _EMOJI_FALLBACKS.get(emoji, ""))
    return fallback or _EMOJI_FALLBACKS.get(emoji, "")


# ---------------------------------------------------------------------------
# Timestamp / elapsed helpers (pure stdlib)
# ---------------------------------------------------------------------------

def _parse_utc_timestamp(raw_value: Any) -> datetime | None:
    """Parse an ISO8601 timestamp used by persisted CLI/session state."""
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(started_at: Any, finished_at: Any | None = None) -> float | None:
    start_dt = _parse_utc_timestamp(started_at)
    if start_dt is None:
        return None
    end_dt = _parse_utc_timestamp(finished_at) if finished_at else datetime.now(timezone.utc)
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)
    return max(0.0, (end_dt - start_dt).total_seconds())


def _format_elapsed_compact(seconds: Any) -> str:
    """Format a duration in seconds to a compact human-readable string."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "0s"
    if value < 1:
        return f"{value:.1f}s"
    if value < 60:
        return f"{value:.1f}s" if value < 10 else f"{value:.0f}s"
    minutes, rem = divmod(int(round(value)), 60)
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _estimate_token_count(value: object) -> int:
    """Estimate token count using the CLI's shared rough character heuristic."""
    return max(0, len(str(value or "")) // 4)


def _history_token_breakdown(history: list[dict[str, object]]) -> dict[str, object]:
    """Summarize estimated token usage for session history."""
    roles: dict[str, dict[str, int]] = {}
    total_chars = 0
    total_tokens = 0
    total_messages = len(history)
    for message in history:
        role = str(message.get("role") or "unknown").strip().lower() or "unknown"
        content = str(message.get("content") or "")
        chars = len(content)
        tokens = _estimate_token_count(content)
        total_chars += chars
        total_tokens += tokens
        bucket = roles.setdefault(role, {"messages": 0, "chars": 0, "tokens": 0})
        bucket["messages"] += 1
        bucket["chars"] += chars
        bucket["tokens"] += tokens
    return {
        "total_chars": total_chars,
        "total_tokens": total_tokens,
        "total_messages": total_messages,
        "roles": sorted(roles.items(), key=lambda item: (-item[1]["tokens"], item[0])),
    }


def _format_context_limit_label(limit_tokens: int, *, approximate: bool = False) -> str:
    """Return a compact human label for a token window."""
    limit = max(0, int(limit_tokens or 0))
    if limit >= 1_000_000 and limit % 1_000_000 == 0:
        label = f"{limit // 1_000_000}m"
    elif limit >= 1_000 and limit % 1_000 == 0:
        label = f"{limit // 1_000}k"
    else:
        label = f"{limit:,}"
    return f"~{label}" if approximate and label else label


def _extract_context_limit_from_hint(model_hint: object) -> int | None:
    """Pull an explicit context window from a model name like `128k` or `1m`."""
    text = str(model_hint or "").strip().lower()
    if not text:
        return None
    million_match = _CONTEXT_LIMIT_M_RE.search(text)
    if million_match:
        return int(million_match.group(1)) * 1_000_000
    thousand_match = _CONTEXT_LIMIT_SUFFIX_RE.search(text)
    if thousand_match:
        return int(thousand_match.group(1)) * 1_000
    return None


def _resolve_context_limit_profile(
    *,
    model_hint: object = "",
    route_hint: object = "",
) -> dict[str, object]:
    """Resolve the best available context-window guidance for the active model."""
    raw_model = str(model_hint or _PREFS.get("last_model", "") or "").strip()
    raw_route = str(route_hint or _PREFS.get("route_mode", "") or "").strip()
    candidates = [value for value in (raw_model, raw_route) if value]
    normalized = " ".join(candidates).lower()

    explicit_limit = None
    explicit_source = ""
    for candidate in candidates:
        explicit_limit = _extract_context_limit_from_hint(candidate)
        if explicit_limit:
            explicit_source = str(candidate)
            break

    display_model = raw_model or raw_route or "current route"
    if explicit_limit:
        limit_label = _format_context_limit_label(explicit_limit)
        return {
            "limit_tokens": explicit_limit,
            "limit_label": limit_label,
            "limit_display": f"{limit_label} window",
            "limit_note": f"Resolved from model name `{explicit_source}`.",
            "source": "model-name",
            "model_label": display_model,
            "model_aware": True,
            "approximate": False,
        }

    if "gemini" in normalized:
        limit_tokens = 125_000
        limit_label = _format_context_limit_label(limit_tokens, approximate=True)
        return {
            "limit_tokens": limit_tokens,
            "limit_label": limit_label,
            "limit_display": f"{limit_label} Gemini-class window",
            "limit_note": "Gemini-family route detected; shown as an approximate family window.",
            "source": "family-gemini",
            "model_label": display_model,
            "model_aware": True,
            "approximate": True,
        }

    if "gemma" in normalized:
        limit_tokens = 100_000
        limit_label = _format_context_limit_label(limit_tokens, approximate=True)
        return {
            "limit_tokens": limit_tokens,
            "limit_label": limit_label,
            "limit_display": f"{limit_label} Gemma-class window",
            "limit_note": "Gemma-family route detected; shown as an approximate family window.",
            "source": "family-gemma",
            "model_label": display_model,
            "model_aware": True,
            "approximate": True,
        }

    limit_label = _format_context_limit_label(_GENERIC_CONTEXT_LIMIT_TOKENS)
    return {
        "limit_tokens": _GENERIC_CONTEXT_LIMIT_TOKENS,
        "limit_label": limit_label,
        "limit_display": f"{limit_label} heuristic window",
        "limit_note": "Current route does not expose a trustworthy model window; using the shared fallback heuristic.",
        "source": "fallback",
        "model_label": display_model,
        "model_aware": False,
        "approximate": True,
    }


def _context_pressure_snapshot(
    history: list[dict[str, object]] | None,
    *,
    system_prompt: str = "",
    pending_inject: str = "",
    limit_tokens: int = 0,
    model_hint: object = "",
    route_hint: object = "",
) -> dict[str, object]:
    """Estimate context pressure and recovery cues for the next send."""
    profile = _resolve_context_limit_profile(model_hint=model_hint, route_hint=route_hint)
    resolved_limit = int(limit_tokens or profile["limit_tokens"] or _GENERIC_CONTEXT_LIMIT_TOKENS)
    breakdown = _history_token_breakdown(list(history or []))
    history_tokens = int(breakdown["total_tokens"])
    system_tokens = _estimate_token_count(system_prompt) if system_prompt else 0
    inject_tokens = _estimate_token_count(pending_inject) if pending_inject else 0
    next_tokens = history_tokens + system_tokens + inject_tokens
    pct_history_raw = round(history_tokens / resolved_limit * 100) if resolved_limit else 0
    pct_next_raw = round(next_tokens / resolved_limit * 100) if resolved_limit else 0
    pct_history = min(100, pct_history_raw)
    pct_next = min(100, pct_next_raw)
    if pct_next_raw >= 100:
        band = "overflow"
    elif pct_next_raw >= 90:
        band = "critical"
    elif pct_next_raw >= 80:
        band = "high"
    elif pct_next_raw >= 50:
        band = "medium"
    else:
        band = "low"
    hidden_pressure = bool((system_tokens or inject_tokens) and pct_next_raw > pct_history_raw)
    return {
        "history_tokens": history_tokens,
        "system_tokens": system_tokens,
        "inject_tokens": inject_tokens,
        "next_tokens": next_tokens,
        "pct_history": pct_history,
        "pct_history_raw": pct_history_raw,
        "pct_next": pct_next,
        "pct_next_raw": pct_next_raw,
        "band": band,
        "hidden_pressure": hidden_pressure,
        "has_pending_inject": bool(inject_tokens),
        "has_system_prompt": bool(system_tokens),
        "overflow": pct_next_raw > 100,
        "limit_tokens": resolved_limit,
        "limit_label": str(profile["limit_label"]),
        "limit_display": str(profile["limit_display"]),
        "limit_note": str(profile["limit_note"]),
        "limit_source": str(profile["source"]),
        "limit_model_label": str(profile["model_label"]),
        "limit_model_aware": bool(profile["model_aware"]),
        "limit_is_approximate": bool(profile["approximate"]),
    }


# ---------------------------------------------------------------------------
# String helpers (pure stdlib)
# ---------------------------------------------------------------------------

def _single_line_excerpt(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _format_byte_count(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes or 0)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(size)} B"


# ---------------------------------------------------------------------------
# Status cell helpers
# ---------------------------------------------------------------------------

def _status_family(status: str) -> str:
    """Normalize related status words into a shared rendering family."""
    s = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"ok", "healthy", "done", "completed", "success", "succeeded", "complete"}:
        return "complete"
    if s in {"active", "running", "in_progress", "working", "processing", "streaming"}:
        return "active"
    if s in {"pending", "queued", "waiting", "idle", "scheduled"}:
        return "waiting" if s != "idle" else "idle"
    if s in {"retry", "retrying", "backoff", "recovering"}:
        return "retry"
    if s in {"warn", "warning", "degraded", "attention"}:
        return "warn"
    if s in {"error", "failed", "failure", "unhealthy"}:
        return "error"
    if s in {"blocked", "stuck", "needs_input", "needs-input"}:
        return "blocked"
    if s in {"paused", "stopped", "cancelled", "canceled"}:
        return "paused"
    if s in {"info", "note", "fresh", "new"}:
        return "info"
    if s in {"stale", "old", "expired"}:
        return "stale"
    return "unknown"


def _status_text(status: str) -> str:
    """Return the canonical plain-text status label."""
    family = _status_family(status)
    return {
        "complete": "COMPLETE",
        "active": "ACTIVE",
        "waiting": "WAITING",
        "idle": "IDLE",
        "retry": "RETRY",
        "warn": "WARN",
        "error": "ERROR",
        "blocked": "BLOCKED",
        "paused": "PAUSED",
        "info": "INFO",
        "stale": "STALE",
        "unknown": "STATUS",
    }.get(family, "STATUS")


def _status_style(status: str) -> str:
    """Return the Rich/ANSI style token for a status family."""
    family = _status_family(status)
    return {
        "complete": "green",
        "active": "cyan",
        "waiting": "yellow",
        "idle": "dim",
        "retry": "magenta",
        "warn": "bold yellow",
        "error": "bold red",
        "blocked": "red",
        "paused": "yellow",
        "info": "blue",
        "stale": "dim",
        "unknown": "dim",
    }.get(family, "dim")


def _status_emoji(status: str) -> str:
    """Map a status string to a representative emoji."""
    family = _status_family(status)
    if family == "complete":
        return _e("🟢", "[ok]")
    if family == "active":
        return _e("🔵", "[run]")
    if family == "waiting":
        return _e("⏳", "[wait]")
    if family == "idle":
        return _e("⚪", "[idle]")
    if family == "retry":
        return _e("🔄", "[retry]")
    if family == "warn":
        return _e("🟡", "[warn]")
    if family == "error":
        return _e("🔴", "[err]")
    if family == "blocked":
        return _e("⛔", "[block]")
    if family == "paused":
        return _e("⏸", "[pause]")
    if family == "info":
        return _e("ℹ️", "[info]")
    if family == "stale":
        return _e("🕰️", "[stale]")
    return _e("●", "[*]")


def _status_cell(status: str, *, detail: str = "", rich: bool = False) -> str:
    """Return a compact badge-like status cell with plain-text parity."""
    label = _status_text(status)
    suffix = f" · {detail}" if detail else ""
    if rich and _RICH_AVAILABLE and _IS_TTY and not _a11y_plain_mode():
        emoji = _status_emoji(status)
        style = _status_style(status)
        return f"[{style}]{emoji} {label}[/]{suffix}"
    return f"{label}{suffix}"


def _progress_cell(label: str, value: str, *, status: str = "", rich: bool = False) -> str:
    """Return a dense progress/status cell that degrades to readable plain text."""
    cell = f"{label}: {value}".strip()
    if not status:
        return cell
    badge = _status_cell(status, rich=rich)
    return f"{badge} · {cell}"


# ---------------------------------------------------------------------------
# Watch-state helpers (local copies of pure functions from openclaw_cli.py)
# ---------------------------------------------------------------------------

def watch_retry_delay_seconds(attempt: int) -> int:
    """Return a capped exponential backoff delay for transient watch retries."""
    return min(WATCH_RETRY_MAX_DELAY_SECONDS, max(1, 2 ** max(0, attempt - 1)))


def _watch_retry_delay_total(state: dict[str, Any]) -> int:
    total = 0
    for entry in list(state.get("retry_history") or []):
        try:
            delay = int(entry.get("delay_seconds") or watch_retry_delay_seconds(int(entry.get("attempt") or 1)))
        except (TypeError, ValueError):
            delay = 0
        total += max(0, delay)
    return total


def normalize_watch_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Backfill watch-state fields introduced after the first CLI releases."""
    normalized = dict(state or {})
    normalized.setdefault("last_error", "")
    normalized.setdefault("failure_count", 0)
    normalized.setdefault("consecutive_failures", 0)
    normalized.setdefault("retry_limit", WATCH_RETRY_LIMIT)
    normalized["retry_limit"] = max(1, int(normalized.get("retry_limit") or WATCH_RETRY_LIMIT))
    normalized["retry_history"] = [
        item for item in list(normalized.get("retry_history") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["progress_log"] = [
        item for item in list(normalized.get("progress_log") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["interventions"] = [
        item for item in list(normalized.get("interventions") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["force_run_once"] = bool(normalized.get("force_run_once"))
    normalized["stop_requested"] = bool(normalized.get("stop_requested"))
    normalized["stop_requested_at"] = str(normalized.get("stop_requested_at", "") or "")
    normalized["last_intervention_at"] = str(normalized.get("last_intervention_at", "") or "")
    active_checkpoint = normalized.get("active_checkpoint")
    if not isinstance(active_checkpoint, dict):
        active_checkpoint = {}
    if active_checkpoint:
        active_checkpoint.setdefault("progress", [])
        active_checkpoint["progress"] = [
            item for item in list(active_checkpoint.get("progress") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint.setdefault("attempts", [])
        active_checkpoint["attempts"] = [
            item for item in list(active_checkpoint.get("attempts") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint.setdefault(
            "duration_seconds",
            _elapsed_seconds(active_checkpoint.get("started_at"), active_checkpoint.get("completed_at")),
        )
    normalized["active_checkpoint"] = active_checkpoint
    checkpoints = [item for item in list(normalized.get("checkpoints") or []) if isinstance(item, dict)]
    for checkpoint in checkpoints:
        checkpoint.setdefault(
            "duration_seconds",
            _elapsed_seconds(checkpoint.get("started_at") or checkpoint.get("created_at"), checkpoint.get("completed_at")),
        )
    normalized["checkpoints"] = checkpoints
    for entry in normalized["retry_history"]:
        entry.setdefault("delay_seconds", watch_retry_delay_seconds(int(entry.get("attempt") or 1)))
    return normalized


def _watch_timing_summary(state: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_watch_state(state)
    active_checkpoint = normalized.get("active_checkpoint")
    checkpoints = [item for item in list(normalized.get("checkpoints") or []) if isinstance(item, dict)]
    latest_checkpoint = checkpoints[-1] if checkpoints else {}
    active_phase = ""
    active_phase_elapsed = None

    if isinstance(active_checkpoint, dict) and active_checkpoint:
        active_phase = str(active_checkpoint.get("phase") or "").strip()
        phase_started_at = ""
        for item in reversed(list(active_checkpoint.get("progress") or [])):
            if str(item.get("phase") or "").strip() == active_phase:
                phase_started_at = str(item.get("created_at") or "").strip()
                break
        if not phase_started_at:
            phase_started_at = str(active_checkpoint.get("updated_at") or active_checkpoint.get("started_at") or "").strip()
        active_phase_elapsed = _elapsed_seconds(phase_started_at)

    latest_duration = (
        latest_checkpoint.get("duration_seconds")
        or _elapsed_seconds(latest_checkpoint.get("started_at"), latest_checkpoint.get("completed_at"))
        or _elapsed_seconds(latest_checkpoint.get("created_at"), latest_checkpoint.get("completed_at"))
    )
    current_elapsed = _elapsed_seconds(normalized.get("last_run_at")) if normalized.get("status") in {"running", "retrying"} else None
    return {
        "active_phase": active_phase,
        "active_phase_elapsed": active_phase_elapsed,
        "latest_duration": latest_duration,
        "retry_delay_total": _watch_retry_delay_total(normalized),
        "current_elapsed": current_elapsed,
    }


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _session_age_label(session: SessionSummary) -> str:
    """Return a compact age label for a persisted session."""
    age_seconds = _elapsed_seconds(session.created_at)
    if age_seconds is None:
        return "unknown"
    return _format_elapsed_compact(age_seconds)


def _session_is_stale(s: SessionSummary, days: int = 7) -> bool:
    try:
        updated = datetime.fromisoformat(s.updated_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - updated
        return age.days >= days
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Collaboration / display helpers
# ---------------------------------------------------------------------------

def _format_collaboration_entry(entry: dict[str, Any]) -> str:
    actor = str(entry.get("actor") or "operator").strip()
    summary = str(entry.get("summary") or entry.get("content") or "").strip()
    tags = [str(tag or "").strip() for tag in list(entry.get("tags") or []) if str(tag or "").strip()]
    suffix = f" [{' '.join('#' + tag for tag in tags)}]" if tags else ""
    return f"{actor}: {summary}{suffix}".strip()


def _session_mood_cell(snapshot: dict[str, str], *, rich: bool = False) -> str:
    """Render a compact mood/momentum cell with text-first fallback."""
    label = str(snapshot.get("label") or "").strip()
    detail = str(snapshot.get("detail") or "").strip()
    if not label:
        return ""
    value = label if not detail else f"{label} · {detail}"
    return _progress_cell("mood", value, status=str(snapshot.get("status") or "info"), rich=rich)


def _session_mood_brief(snapshot: dict[str, str], *, max_chars: int = 38) -> str:
    """Return a restrained one-cell mood summary for dense list views."""
    label = str(snapshot.get("label") or "").strip()
    detail = str(snapshot.get("detail") or "").strip()
    if not label:
        return "—"
    if not detail:
        return label
    return _single_line_excerpt(f"{label} · {detail}", max_chars=max_chars)


def _operator_snapshot_lines(snapshot: dict[str, Any]) -> list[str]:
    """Render human-readable lines for the operator snapshot."""
    lines = [
        _progress_cell("visibility", str(snapshot.get("access") or "read-only local snapshot"), status="info"),
    ]
    control = str(snapshot.get("control") or "").strip()
    if control:
        lines.append(f"control: {control}")
    readiness_label = str(snapshot.get("readiness_label") or "").strip()
    readiness_detail = str(snapshot.get("readiness_detail") or "").strip()
    if readiness_label:
        readiness_value = readiness_label if not readiness_detail else f"{readiness_label} · {readiness_detail}"
        lines.append(
            _progress_cell(
                "readiness",
                readiness_value,
                status=str(snapshot.get("readiness_status") or "info"),
            )
        )
    watch_summary = str(snapshot.get("watch_summary") or "").strip()
    if watch_summary:
        lines.append(f"operator watch: {watch_summary}")
    queue_summary = str(snapshot.get("queue_summary") or "").strip()
    if queue_summary:
        lines.append(f"operator queue: {queue_summary}")
    latest_output = str(snapshot.get("latest_output") or "").strip()
    if latest_output:
        lines.append(f"latest output: {latest_output}")
    latest_decision = str(snapshot.get("latest_decision") or "").strip()
    if latest_decision:
        lines.append(f"latest decision: {_single_line_excerpt(latest_decision, max_chars=100)}")
    latest_note = str(snapshot.get("latest_note") or "").strip()
    if latest_note:
        lines.append(f"latest note: {_single_line_excerpt(latest_note, max_chars=100)}")
    latest_handoff = str(snapshot.get("latest_handoff") or "").strip()
    if latest_handoff:
        lines.append(f"latest handoff: {latest_handoff}")
    control = str(snapshot.get("control") or "").strip()
    if control:
        lines.append(f"control: {control}")
    return lines


# ---------------------------------------------------------------------------
# Dashboard surface helpers
# ---------------------------------------------------------------------------

def _dashboard_section_lines(title: str, lines: list[str]) -> list[str]:
    """Return normalized lines for a plain-text dashboard section."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    if not clean:
        return []
    return [f"{title}:"] + [f"  - {line}" for line in clean]


def _append_dashboard_rich_section(
    body: "_RichText",
    title: str,
    lines: list[str],
    *,
    title_style: str = "bold cyan",
    line_style: str = "",
) -> None:
    """Append a dashboard section to a Rich text buffer."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    if not clean:
        return
    if body.plain:
        body.append("\n")
    body.append(f"{title}\n", style=title_style)
    for line in clean:
        body.append("  • ", style="dim")
        body.append(f"{line}\n", style=line_style)


def _print_dashboard_surface(
    title: str,
    *,
    summary_lines: list[str],
    detail_lines: list[str] | None = None,
    action_lines: list[str] | None = None,
    border_style: str = "dim",
) -> None:
    """Render a summary → details → actions dashboard surface with safe fallbacks."""
    detail_lines = detail_lines or []
    action_lines = action_lines or []
    is_tty = _get_is_tty()
    if _RICH_AVAILABLE and is_tty and not _a11y_plain_mode():
        body = _RichText()
        _append_dashboard_rich_section(body, "Summary", summary_lines)
        _append_dashboard_rich_section(body, "Details", detail_lines, title_style="bold white")
        _append_dashboard_rich_section(body, "Actions", action_lines, title_style="bold yellow")
        _RICH_CONSOLE.print(
            _RichPanel(body, title=f"[bold]{title}[/]", border_style=border_style, padding=(0, 1))
        )
        return
    lines = [title, *(_dashboard_section_lines("Summary", summary_lines))]
    detail_block = _dashboard_section_lines("Details", detail_lines)
    if detail_block:
        lines.extend(["", *detail_block])
    action_block = _dashboard_section_lines("Actions", action_lines)
    if action_block:
        lines.extend(["", *action_block])
    print("\n".join(lines))


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
    """Return non-empty lines without duplicates, preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


# ---------------------------------------------------------------------------
# Runbook templates (local copy — keep in sync with openclaw_cli.py)
# ---------------------------------------------------------------------------

_RUNBOOK_TEMPLATES: dict[str, dict[str, Any]] = {
    "operator": {
        "label": "Operator Runbook",
        "audience": "CLI operator handoff",
        "sections": ("summary", "milestones", "decisions", "timeline", "outputs", "commands"),
    },
    "stakeholder": {
        "label": "Stakeholder Update",
        "audience": "status recap for non-operators",
        "sections": ("summary", "milestones", "outputs", "commands"),
    },
    "postmortem": {
        "label": "Postmortem Draft",
        "audience": "incident recap and follow-up review",
        "sections": ("summary", "decisions", "timeline", "outputs", "commands"),
    },
}


def _resolve_runbook_template(name: str) -> tuple[str, dict[str, Any]] | None:
    token = str(name or "operator").strip().lower()
    if not token:
        token = "operator"
    template = _RUNBOOK_TEMPLATES.get(token)
    if template is None:
        return None
    return token, template


# ---------------------------------------------------------------------------
# Primary extracted functions
# ---------------------------------------------------------------------------

def _session_mood_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Derive a restrained mood/momentum cue from objective session state."""
    try:
        normalized_watch = normalize_watch_state(watch_state or {}) if watch_state else {}
    except Exception:
        _LOG.debug("normalize_watch_state failed", exc_info=True)
        normalized_watch = {}
    snapshot = collaboration_snapshot or {}
    actors = [item for item in list(snapshot.get("actors") or []) if isinstance(item, dict)]
    decisions = [item for item in list(snapshot.get("recent_decisions") or []) if isinstance(item, dict)]
    latest_handoff = snapshot.get("latest_handoff") or {}

    outputs = int(session.output_count or 0)
    checkpoints = int(session.checkpoint_count or 0)
    commands = int(session.command_count or 0)
    failures = int(normalized_watch.get("failure_count") or 0)
    watch_status = str(normalized_watch.get("status") or "").strip().lower()
    active_phase = str(_watch_timing_summary(normalized_watch).get("active_phase") or "").strip()
    actor_count = len(actors)

    if (
        session.status in {"complete", "completed"}
        or watch_status in {"complete", "completed"}
        or (outputs > 0 and checkpoints > 0 and commands > 0)
    ):
        detail = "outputs ready to review" if outputs else "checkpoint captured cleanly"
        if actor_count >= 2:
            detail += f" · {actor_count} collaborators in the loop"
        return {
            "status": "complete",
            "label": "milestone",
            "detail": detail,
            "headline": f"milestone: {detail}",
            "share_line": f"momentum   : milestone reached; {detail}",
        }

    if watch_status == "retrying" or failures > 0:
        detail = "recovering with checkpoints" if checkpoints else "retry loop staying engaged"
        if active_phase:
            detail += f" · phase {active_phase}"
        return {
            "status": "retry",
            "label": "resilient",
            "detail": detail,
            "headline": f"mood: resilient recovery · {detail}",
            "share_line": f"momentum   : resilient recovery; {detail}",
        }

    if actor_count >= 2 or decisions or latest_handoff:
        detail = f"{max(actor_count, 1)} collaborators aligned" if actor_count else "handoff context is ready"
        if decisions:
            detail += f" · {len(decisions)} recent decision{'s' if len(decisions) != 1 else ''}"
        return {
            "status": "info",
            "label": "shared",
            "detail": detail,
            "headline": f"mood: shared momentum · {detail}",
            "share_line": f"momentum   : shared momentum; {detail}",
        }

    if commands >= 3 or outputs > 0 or checkpoints > 0:
        detail = "signals are stacking up"
        if outputs > 0:
            detail = f"{outputs} output{'s' if outputs != 1 else ''} landed"
        elif checkpoints > 0:
            detail = f"{checkpoints} checkpoint{'s' if checkpoints != 1 else ''} recorded"
        elif commands >= 3:
            detail = f"{commands} command{'s' if commands != 1 else ''} into the flow"
        return {
            "status": "active",
            "label": "steady",
            "detail": detail,
            "headline": f"mood: building momentum · {detail}",
            "share_line": f"momentum   : building momentum; {detail}",
        }

    return {}


def _session_operator_snapshot(
    session: SessionSummary,
    *,
    watch_state: dict[str, Any] | None = None,
    collaboration_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a read-only operator snapshot for monitoring and handoff surfaces."""
    try:
        normalized_watch = normalize_watch_state(watch_state or {}) if watch_state else {}
    except Exception:
        _LOG.debug("normalize_watch_state failed", exc_info=True)
        normalized_watch = {}
    snapshot = collaboration_snapshot or {}
    decisions = [item for item in list(snapshot.get("recent_decisions") or []) if isinstance(item, dict)]
    notes = [item for item in list(snapshot.get("recent_notes") or []) if isinstance(item, dict)]
    outputs = [item for item in list(snapshot.get("recent_outputs") or []) if isinstance(item, dict)]
    handoff = snapshot.get("latest_handoff") or {}
    interventions = [item for item in list(normalized_watch.get("interventions") or []) if isinstance(item, dict)]
    pending_interventions = [
        item for item in interventions if str(item.get("status") or "").strip().lower() == "pending"
    ]
    watch_status = str(normalized_watch.get("status") or "").strip().lower()
    failures = int(normalized_watch.get("failure_count") or 0)
    fresh = not _session_is_stale(session)
    stop_requested = bool(normalized_watch.get("stop_requested"))

    readiness_status = "info"
    readiness_label = "warming"
    readiness_detail = "local snapshot is still forming"
    if stop_requested:
        readiness_status = "warn"
        readiness_label = "attention"
        readiness_detail = "stop requested; verify the next clean handoff point"
    elif watch_status == "retrying" or failures > 0:
        readiness_status = "retry"
        readiness_label = "attention"
        readiness_detail = "automation is recovering; keep operator eyes on retries"
    elif watch_status in {"running", "active"}:
        readiness_status = "active"
        readiness_label = "live"
        readiness_detail = "watch loop is active; summary is safe to monitor"
    elif outputs or decisions or session.last_summary:
        readiness_status = "complete"
        readiness_label = "handoff-ready"
        readiness_detail = "local read-only snapshot is ready to share"
    elif fresh:
        readiness_status = "active"
        readiness_label = "warming"
        readiness_detail = "fresh session context is available for operators"

    watch_bits: list[str] = []
    if watch_status:
        watch_bits.append(watch_status)
    timing = _watch_timing_summary(normalized_watch) if normalized_watch else {}
    active_phase = str(timing.get("active_phase") or "").strip()
    if active_phase:
        watch_bits.append(active_phase)
    poll_count = int(normalized_watch.get("poll_count") or 0)
    max_polls = int(normalized_watch.get("max_polls") or 0)
    if poll_count or max_polls:
        watch_bits.append(f"{poll_count}/{max_polls or '∞'} polls")

    queue_bits: list[str] = []
    if pending_interventions:
        queue_bits.append(f"{len(pending_interventions)} pending")
    if stop_requested:
        queue_bits.append("stop requested")

    latest_output = str((outputs[0] or {}).get("name") or "").strip() if outputs else ""
    latest_decision = _format_collaboration_entry(decisions[0]) if decisions else ""
    latest_note = _format_collaboration_entry(notes[0]) if notes else ""
    latest_handoff = str(handoff.get("id") or "").strip()

    return {
        "access": "read-only local snapshot",
        "control": "visibility only; no remote control",
        "readiness_status": readiness_status,
        "readiness_label": readiness_label,
        "readiness_detail": readiness_detail,
        "watch_summary": " · ".join(watch_bits),
        "queue_summary": " · ".join(queue_bits),
        "latest_output": latest_output,
        "latest_decision": latest_decision,
        "latest_note": latest_note,
        "latest_handoff": latest_handoff,
    }


def _print_session_summary(session: SessionSummary, *, pending_inject: str = "") -> None:
    """Print a compact session summary, with rich formatting when available."""
    history = load_conversation_history(session.session_id, limit_turns=0)
    pressure = _context_pressure_snapshot(
        history,
        system_prompt=str(_PREFS.get("system_prompt", "") or ""),
        pending_inject=str(pending_inject or ""),
        model_hint=_PREFS.get("last_model", ""),
        route_hint=_PREFS.get("route_mode", ""),
    )
    watch_state = None
    try:
        watch_state = load_watch_state(session.session_id)
    except Exception:
        _LOG.debug("load_watch_state failed for %s", session.session_id, exc_info=True)
        watch_state = None
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    story = build_session_storyline(session.session_id, limit=4)
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    operator_snapshot = _session_operator_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=snapshot,
    )

    summary_lines = [
        session.title,
        f"id {session.session_id}",
        _progress_cell("status", str(session.status or "active"), status=session.status or "active"),
        _status_cell("stale" if _session_is_stale(session) else "info", detail="freshness"),
        _progress_cell("updated", session.updated_at or "—", status="info"),
        _progress_cell("age", _session_age_label(session), status="info"),
    ]
    mood_cell = _session_mood_cell(mood, rich=_RICH_AVAILABLE and _IS_TTY)
    if mood_cell:
        summary_lines.append(mood_cell)
    detail_lines = [
        f"story: {story.get('headline', '')}" if story.get("headline") else "",
        f"chapter: {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}" if story.get("chapter_title") else "",
        _progress_cell("commands", str(session.command_count), status="active" if session.command_count else "idle"),
        _progress_cell("outputs", str(session.output_count), status="complete" if session.output_count else "idle"),
        _progress_cell("checkpoints", str(session.checkpoint_count), status="complete" if session.checkpoint_count else "idle"),
        f"cwd: {session.cwd}" if session.cwd else "",
        f"plan: {session.plan_id}" if session.plan_id else "",
        f"task: {session.task_id}" if session.task_id else "",
        (
            "files: "
            + ", ".join(session.files[:4])
            + ("…" if len(session.files) > 4 else "")
        )
        if session.files
        else "files: none tracked",
        f"last: {session.last_summary[:100]}" if session.last_summary else "",
    ]
    if int(pressure["pct_next"]) >= 50:
        detail_lines.append(
            _progress_cell(
                "context pressure",
                f"~{int(pressure['next_tokens']):,} tok next send ({int(pressure['pct_next_raw'])}% of {pressure['limit_label']})",
                status="warn" if int(pressure["pct_next"]) < 80 else "retry",
            )
        )
        if bool(pressure["overflow"]):
            detail_lines.append("overflow cue: next send likely exceeds the resolved window")
        elif int(pressure["pct_next"]) >= 80:
            detail_lines.append("recovery guardrail: save /bookmark before /clear if you need a lighter restart")
        else:
            detail_lines.append("staleness cue: /tokeninfo can confirm whether context pressure is causing drift")
    elif pressure["has_system_prompt"] or pressure["has_pending_inject"]:
        hidden_bits = []
        if pressure["has_system_prompt"]:
            hidden_bits.append(f"system prompt ~{int(pressure['system_tokens']):,} tok")
        if pressure["has_pending_inject"]:
            hidden_bits.append(f"pending inject ~{int(pressure['inject_tokens']):,} tok")
        detail_lines.append(
            _progress_cell(
                "hidden context",
                " · ".join(hidden_bits),
                status="warn" if pressure["has_pending_inject"] else "info",
            )
        )
    if pressure["hidden_pressure"] and int(pressure["pct_next"]) >= 80:
        detail_lines.append("hidden context cue: system or queued inject content pushes the next send closer to capacity")
    if pressure["has_pending_inject"]:
        detail_lines.append("recovery cue: /inject clear drops the queued one-shot context before a retry")
    detail_lines.extend(_operator_snapshot_lines(operator_snapshot)[:5])
    for milestone in list(story.get("milestones") or [])[:2]:
        detail_lines.append(f"milestone: {milestone}")
    action_lines = []
    if session.automation_mode:
        a_status = session.automation_status or "active"
        detail_lines.append(_progress_cell("automation", f"{session.automation_mode} ({a_status})", status=a_status))
        if watch_state:
            timing = _watch_timing_summary(watch_state)
            polls = int(watch_state.get("poll_count") or 0)
            max_polls = int(watch_state.get("max_polls") or 0)
            failures = int(watch_state.get("failure_count") or 0)
            retry_limit = int(watch_state.get("retry_limit") or 3)
            detail_lines.append(_progress_cell("polls", f"{polls}/{max_polls or '∞'}", status=a_status))
            if failures:
                detail_lines.append(_progress_cell("failures", f"{failures}/{retry_limit}", status="retry"))
            if timing["active_phase"]:
                phase = timing["active_phase"]
                if timing["active_phase_elapsed"] is not None:
                    phase += f" {_format_elapsed_compact(timing['active_phase_elapsed'])}"
                detail_lines.append(_progress_cell("phase", phase, status="active"))
            if timing["latest_duration"] is not None:
                detail_lines.append(f"last run {_format_elapsed_compact(timing['latest_duration'])}")
            if timing["retry_delay_total"]:
                detail_lines.append(f"retry backoff {_format_elapsed_compact(timing['retry_delay_total'])}")
            last_error = str(watch_state.get("last_error") or "").strip()
            if last_error:
                detail_lines.append(f"last error: {last_error[:80]}")
        action_lines.append("/watch status to inspect the live control tower")
        action_lines.append("/watch history to review retries and checkpoints before rerunning")
        if watch_state and (watch_state.get("last_error") or int(watch_state.get("failure_count") or 0) > 0):
            action_lines.append('/watch intervene "recovery note" to steer the next retry')
        if watch_state and list(watch_state.get("interventions") or []):
            action_lines.append("/collab share to copy the latest read-only operator snapshot")
    elif session.output_count:
        action_lines.append("/outputs 1 to inspect the newest saved output")
        if session.output_count > 1:
            action_lines.append("/outputs overlay to jump through saved artifacts")
    elif session.files:
        action_lines.append("/context to preview the next request grounding")
    else:
        action_lines.append("/files add <path> to attach workspace context")
    if session.plan_id or session.task_id:
        action_lines.append("/context to verify linked plan/task grounding")
    if int(pressure["pct_next"]) >= 50 or pressure["hidden_pressure"]:
        action_lines.append("/tokeninfo to inspect live context pressure before the next send")
    if int(pressure["pct_next"]) >= 80:
        action_lines.append("/bookmark before /clear if you need a clean recovery loop")
    if bool(pressure["overflow"]) or pressure["hidden_pressure"] or pressure["has_pending_inject"]:
        action_lines.append("/promptdebug to inspect the next payload before sending")
        action_lines.append("/inject status or /system view to inspect hidden context before sending")
    if pressure["has_pending_inject"]:
        action_lines.append("/inject clear to drop the queued one-shot context before your next send")
    action_lines.append("/collab share to copy the read-only local snapshot before handoff")
    action_lines = _dedupe_preserve_order(action_lines)
    if session.last_checkpoint_at:
        detail_lines.append(f"last checkpoint: {session.last_checkpoint_at}")

    _print_dashboard_surface(
        "Session Dashboard",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
        border_style="cyan",
    )


def _build_session_share_text(session_id: str) -> str:
    snapshot = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=5)
    bookmarks = list_session_bookmarks(session_id)
    session_data = snapshot.get("session") or {}
    actors = list(snapshot.get("actors") or [])
    recent_decisions = list(snapshot.get("recent_decisions") or [])
    recent_notes = list(snapshot.get("recent_notes") or [])
    assignments = list(snapshot.get("assignments") or [])
    open_risks = list(snapshot.get("open_risks") or [])
    open_incidents = list(snapshot.get("open_incidents") or [])
    recent_outputs = list(snapshot.get("recent_outputs") or [])
    latest_handoff = snapshot.get("latest_handoff") or {}
    share = snapshot.get("share") or {}
    mood = _session_mood_snapshot(
        require_session(session_id),
        watch_state=load_watch_state(session_id),
        collaboration_snapshot=snapshot,
    )
    operator_snapshot = _session_operator_snapshot(
        require_session(session_id),
        watch_state=load_watch_state(session_id),
        collaboration_snapshot=snapshot,
    )

    lines = [
        "SESSION HANDOFF",
        "-" * 60,
        f"title      : {session_data.get('title', '')}",
        f"session_id : {session_data.get('session_id', session_id)}",
        f"cwd        : {session_data.get('cwd', '')}",
    ]
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id:
        lines.append(f"plan       : {plan_id}")
    if task_id:
        lines.append(f"task       : {task_id}")
    last_summary = str(session_data.get("last_summary") or "").strip()
    if last_summary:
        lines.append(f"summary    : {last_summary}")
    if mood.get("share_line"):
        lines.append(str(mood.get("share_line")))
    if story.get("headline"):
        lines.append(f"story      : {story.get('headline', '')}")
    if story.get("chapter_title"):
        lines.append(f"chapter    : {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    session_tags = [str(tag or "").strip() for tag in list(session_data.get("tags") or []) if str(tag or "").strip()]
    if session_tags:
        lines.append(f"tags       : {', '.join(session_tags[:6])}")
    if actors:
        lines.append("")
        lines.append("ACTORS")
        for actor in actors[:5]:
            lines.append(
                f"  - {actor.get('name', 'operator')} "
                f"({int(actor.get('event_count') or 0)} touchpoints; last {actor.get('last_at', 'n/a')})"
            )
    if recent_decisions:
        lines.append("")
        lines.append("RECENT DECISIONS")
        for entry in recent_decisions[:3]:
            lines.append(f"  - {_format_collaboration_entry(entry)}")
    if recent_notes:
        lines.append("")
        lines.append("RECENT NOTES")
        for entry in recent_notes[:2]:
            lines.append(f"  - {_format_collaboration_entry(entry)}")
    if assignments:
        lines.append("")
        lines.append("ASSIGNMENTS")
        for entry in assignments[:3]:
            assignee = str(entry.get("assignee") or entry.get("actor") or "operator")
            status = str(entry.get("status") or "active")
            lines.append(f"  - {assignee} · {status} · {str(entry.get('content') or entry.get('summary') or '').strip()}")
    if open_risks:
        lines.append("")
        lines.append("OPEN RISKS")
        for entry in open_risks[:3]:
            level = str(entry.get("risk_level") or "medium").upper()
            lines.append(f"  - {level} · {_format_collaboration_entry(entry)}")
    if open_incidents:
        lines.append("")
        lines.append("OPEN INCIDENTS")
        for entry in open_incidents[:3]:
            lines.append(f"  - {_format_collaboration_entry(entry)}")
    if bookmarks:
        lines.append("")
        lines.append("BOOKMARKS")
        for bookmark in bookmarks[-3:]:
            lines.append(
                "  - "
                f"[{bookmark.get('id', '')}] "
                f"{bookmark.get('label', '')} "
                f"(turn {bookmark.get('turn_index', 0)})"
            )
    if latest_handoff:
        lines.append("")
        lines.append("LATEST HANDOFF")
        lines.append(f"  id   : {latest_handoff.get('id', '')}")
        lines.append(f"  when : {latest_handoff.get('created_at', '')}")
        note = str(latest_handoff.get("note") or "").strip()
        if note:
            lines.append(f"  note : {note}")
    lines.append("")
    lines.append("OPERATOR SNAPSHOT")
    lines.append(f"  access    : {operator_snapshot.get('access', 'read-only local snapshot')}")
    lines.append(f"  control   : {operator_snapshot.get('control', 'visibility only; no remote control')}")
    readiness_label = str(operator_snapshot.get("readiness_label") or "").strip()
    readiness_detail = str(operator_snapshot.get("readiness_detail") or "").strip()
    if readiness_label:
        readiness = readiness_label if not readiness_detail else f"{readiness_label} · {readiness_detail}"
        lines.append(f"  readiness : {readiness}")
    watch_summary = str(operator_snapshot.get("watch_summary") or "").strip()
    if watch_summary:
        lines.append(f"  watch     : {watch_summary}")
    queue_summary = str(operator_snapshot.get("queue_summary") or "").strip()
    if queue_summary:
        lines.append(f"  queue     : {queue_summary}")
    latest_output = str(operator_snapshot.get("latest_output") or "").strip()
    if latest_output:
        lines.append(f"  output    : {latest_output}")
    latest_decision = str(operator_snapshot.get("latest_decision") or "").strip()
    if latest_decision:
        lines.append(f"  decision  : {latest_decision}")
    latest_note = str(operator_snapshot.get("latest_note") or "").strip()
    if latest_note:
        lines.append(f"  note      : {latest_note}")
    if recent_outputs:
        lines.append("")
        lines.append("RECENT OUTPUTS")
        for item in recent_outputs[:3]:
            lines.append(f"  - {item.get('name', '')}")
    milestones = list(story.get("milestones") or [])
    actor_highlights = list(story.get("actor_highlights") or [])
    timeline = list(story.get("timeline") or [])
    if milestones:
        lines.append("")
        lines.append("MILESTONES")
        for item in milestones[:4]:
            lines.append(f"  - {item}")
    if actor_highlights:
        lines.append("")
        lines.append("CAST HIGHLIGHTS")
        for item in actor_highlights[:3]:
            lines.append(f"  - {item}")
    if timeline:
        lines.append("")
        lines.append("TIMELINE RECAP")
        for item in timeline[:4]:
            stamp = str(item.get("timestamp") or "").strip()
            prefix = f"{stamp} · " if stamp else ""
            lines.append(f"  - {prefix}{item.get('label', 'Update')}: {item.get('summary', '')}")
    lines.append("")
    lines.append("TRUST & RECOVERY")
    lines.append("  scope  : local session log + read-only snapshot only")
    lines.append("  recover: inspect with /session or /watch history before resuming control")
    lines.append("")
    lines.append("COMMANDS")
    lines.append(f"  resume : {share.get('resume_command', f'openclaw --session {session_id}')}")
    lines.append(f"  inspect: {share.get('inspect_command', f'openclaw session show {session_id}')}")
    lines.append(f"  share  : {share.get('share_command', f'openclaw session share {session_id}')}")
    return "\n".join(lines)


def _build_session_runbook_text(session_id: str, *, template_name: str = "operator") -> str:
    resolved = _resolve_runbook_template(template_name)
    if resolved is None:
        valid = ", ".join(sorted(_RUNBOOK_TEMPLATES))
        raise OpenClawCliError(f"Unknown runbook template '{template_name}'. Available: {valid}")
    template_key, template = resolved
    snapshot = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=6)
    export = export_session(session_id)
    session_data = export.get("session") or {}
    recent_outputs = list(export.get("outputs") or [])
    recent_decisions = list(snapshot.get("recent_decisions") or [])
    commands = snapshot.get("share") or {}
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()

    lines = [
        f"# {template.get('label', 'Runbook')}",
        "",
        f"- **Template:** {template_key}",
        f"- **Audience:** {template.get('audience', 'session review')}",
        f"- **Session:** {session_data.get('title', '') or session_id}",
        f"- **Session ID:** {session_data.get('session_id', session_id)}",
    ]
    cwd = str(session_data.get("cwd") or "").strip()
    if cwd:
        lines.append(f"- **Working directory:** `{cwd}`")
    if plan_id:
        lines.append(f"- **Plan:** `{plan_id}`")
    if task_id:
        lines.append(f"- **Task:** `{task_id}`")
    lines.append("")

    sections = tuple(template.get("sections") or ())
    if "summary" in sections:
        lines.extend(
            [
                "## Summary",
                "",
                f"- **Story:** {story.get('headline', 'Fresh session story is still forming')}",
                f"- **Chapter:** {story.get('chapter_title', 'Session recap')} · {story.get('chapter_detail', '')}",
            ]
        )
        narrative = str(story.get("narrative") or "").strip()
        if narrative:
            lines.append(f"- **Narrative:** {narrative}")
        lines.append("")

    milestones = list(story.get("milestones") or [])
    if "milestones" in sections and milestones:
        lines.append("## Milestones")
        lines.append("")
        lines.extend(f"- {item}" for item in milestones[:5])
        lines.append("")

    if "decisions" in sections and recent_decisions:
        lines.append("## Recent Decisions")
        lines.append("")
        lines.extend(f"- {_format_collaboration_entry(item)}" for item in recent_decisions[:4])
        lines.append("")

    timeline = list(story.get("timeline") or [])
    if "timeline" in sections and timeline:
        lines.append("## Timeline")
        lines.append("")
        for item in timeline[:5]:
            stamp = str(item.get("timestamp") or "").strip()
            prefix = f"{stamp} · " if stamp else ""
            lines.append(f"- {prefix}{item.get('label', 'Update')}: {item.get('summary', '')}")
        lines.append("")

    if "outputs" in sections and recent_outputs:
        lines.append("## Artifacts")
        lines.append("")
        for item in recent_outputs[:5]:
            lines.append(f"- {item.get('name', '')} · {item.get('modified_at', '')}")
        lines.append("")

    if "commands" in sections:
        lines.append("## Next Commands")
        lines.append("")
        lines.append(f"- Resume: `{commands.get('resume_command', f'openclaw --session {session_id}')}`")
        lines.append(f"- Inspect: `{commands.get('inspect_command', f'openclaw session show {session_id}')}`")
        lines.append(f"- Share: `{commands.get('share_command', f'openclaw session share {session_id}')}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def inspect_session(session_id: str) -> str:
    """Render a human-readable inspection view of a persisted session."""
    export = export_session(session_id)
    session_data: dict[str, Any] = export.get("session") or {}
    events: list[dict[str, Any]] = export.get("events") or []
    outputs: list[dict[str, Any]] = export.get("outputs") or []
    watch: dict[str, Any] = export.get("watch_state") or {}
    routed_checkpoints: list[dict[str, Any]] = export.get("routed_action_checkpoints") or []
    collaboration: dict[str, Any] = export.get("collaboration") or {}
    bookmarks: list[dict[str, Any]] = list(session_data.get("bookmarks") or [])
    story = build_session_storyline(session_id, limit=5)
    mood = _session_mood_snapshot(require_session(session_id), watch_state=watch, collaboration_snapshot=collaboration)

    if _RICH_AVAILABLE and _IS_TTY:
        _inspect_session_rich(session_id, session_data, events, outputs, watch, routed_checkpoints)
        return ""

    sep = "-" * 60
    lines: list[str] = []

    # ── Metadata ─────────────────────────────────────────────────
    lines += [
        sep,
        "SESSION INSPECTION",
        sep,
        f"  id       : {session_data.get('session_id', session_id)}",
        f"  title    : {session_data.get('title', '')}",
        f"  status   : {_status_cell(str(session_data.get('status') or 'active'))}",
        f"  cwd      : {session_data.get('cwd', '')}",
        f"  created  : {session_data.get('created_at', '')}",
        f"  updated  : {session_data.get('updated_at', '')}",
        "  "
        + "  |  ".join(
            [
                _progress_cell("commands", str(session_data.get("command_count", 0)), status="active" if int(session_data.get("command_count", 0) or 0) else "idle"),
                _progress_cell("outputs", str(session_data.get("output_count", 0)), status="complete" if int(session_data.get("output_count", 0) or 0) else "idle"),
                _progress_cell("edits", str(session_data.get("file_edit_count", 0)), status="active" if int(session_data.get("file_edit_count", 0) or 0) else "idle"),
            ]
        ),
    ]
    if story.get("headline"):
        lines.append(f"  story    : {story.get('headline', '')}")
    if story.get("chapter_title"):
        lines.append(f"  chapter  : {story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        lines.append(f"  {mood_cell}")

    # ── Plan / task linkage ───────────────────────────────────────
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id or task_id:
        lines.append("")
        lines.append("PLAN / TASK LINKAGE")
        if plan_id:
            lines.append(f"  plan  : {plan_id}")
        if task_id:
            lines.append(f"  task  : {task_id}")

    # ── Tracked files ─────────────────────────────────────────────
    files: list[str] = list(session_data.get("files") or [])
    if files:
        lines.append("")
        lines.append("TRACKED FILES")
        for f in files[:10]:
            lines.append(f"  {f}")
        if len(files) > 10:
            lines.append(f"  … and {len(files) - 10} more")

    # ── Automation / watch status ─────────────────────────────────
    automation_mode = str(session_data.get("automation_mode") or "").strip()
    if automation_mode or watch:
        lines.append("")
        lines.append("AUTOMATION / WATCH")
        if automation_mode:
            a_status = str(session_data.get("automation_status") or "active").strip()
            interval = int(session_data.get("watch_interval_seconds") or 0)
            lines.append(f"  mode     : {_progress_cell('automation', f'{automation_mode} ({a_status})', status=a_status)}")
            if interval:
                lines.append(f"  interval : {_progress_cell('loop', f'{interval}s', status=a_status)}")
        if watch:
            w_status = str(watch.get("status") or "").strip()
            poll_count = int(watch.get("poll_count") or 0)
            max_polls = int(watch.get("max_polls") or 0)
            polls_value = f"{poll_count}/{max_polls or '∞'} polls"
            goal = str(watch.get("goal") or "").strip()
            if goal:
                lines.append(f"  goal     : {goal[:120]}")
            if w_status:
                lines.append(f"  w.status : {_progress_cell('watch', f'{w_status} · {polls_value}', status=w_status)}")
            last_error = str(watch.get("last_error") or "").strip()
            if last_error:
                lines.append(f"  last err : {_status_cell('error', detail=last_error[:180])}")

    # ── Checkpoints ───────────────────────────────────────────────
    checkpoint_count = int(session_data.get("checkpoint_count") or 0)
    last_checkpoint_at = str(session_data.get("last_checkpoint_at") or "").strip()
    watch_checkpoints: list[dict[str, Any]] = list(watch.get("checkpoints") or [])
    if checkpoint_count or watch_checkpoints or routed_checkpoints:
        lines.append("")
        lines.append("CHECKPOINTS")
        lines.append(
            f"  total : {_progress_cell('count', str(checkpoint_count), status='complete' if checkpoint_count else 'idle')}  last: {last_checkpoint_at or 'n/a'}"
        )
        for ckpt in routed_checkpoints[:3]:
            step_index = int(ckpt.get("step_index") or 0)
            step_total = int(ckpt.get("step_total") or 0)
            step_label = (
                f"step {step_index}/{step_total}"
                if step_index > 0 and step_total > 0
                else "routed action"
            )
            lines.append(
                f"  [{ckpt.get('created_at', '')}] {ckpt.get('action_kind', 'action')}"
                f" {step_label} ({ckpt.get('rollback_status', 'available')})"
            )
        for ckpt in watch_checkpoints[-3:]:
            ts = str(ckpt.get("timestamp") or ckpt.get("at") or "").strip()
            note = str(ckpt.get("note") or ckpt.get("summary") or "").strip()
            if ts or note:
                lines.append(f"  [{ts}] {note[:100]}")

    if bookmarks:
        lines.append("")
        lines.append("BOOKMARKS")
        for bookmark in bookmarks[-5:]:
            lines.append(
                f"  [{bookmark.get('id', '')}] "
                f"{bookmark.get('label', '')} "
                f"· turn {bookmark.get('turn_index', 0)}"
            )
            summary_text = str(bookmark.get("summary") or "").strip()
            if summary_text:
                lines.append(f"      {summary_text[:120]}")

    # ── Recent progress log (watch) ───────────────────────────────
    progress_log: list[dict[str, Any]] = list(watch.get("progress_log") or [])
    if progress_log:
        lines.append("")
        lines.append("RECENT PROGRESS (last 5 watch entries)")
        for entry in progress_log[-5:]:
            ts = str(entry.get("timestamp") or entry.get("at") or "").strip()
            phase = str(entry.get("phase") or "").strip()
            note = str(entry.get("note") or entry.get("summary") or entry.get("content") or "").strip()
            entry_status = "warn" if entry.get("warning") else "complete" if entry.get("ok") else "active"
            lines.append(f"  [{ts}] {_status_cell(entry_status, detail=phase or 'progress')} · {note[:120]}")

    # ── Recent events ─────────────────────────────────────────────
    if events:
        lines.append("")
        lines.append("RECENT EVENTS (last 5)")
        for event in events[-5:]:
            ts = str(event.get("timestamp") or event.get("at") or event.get("created_at") or "").strip()
            kind = str(event.get("kind") or "").strip()
            content = str(event.get("content") or "").strip()
            meta = event.get("metadata") or {}
            summary_note = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            label = summary_note or content[:80]
            event_status = "error" if kind == "error" else "complete" if kind in {"assistant", "checkpoint"} else "active" if kind in {"exec", "edit"} else "info"
            lines.append(f"  [{ts}] {_status_cell(event_status, detail=kind or 'event')} · {label}")

    # ── Saved outputs ─────────────────────────────────────────────
    if outputs:
        lines.append("")
        lines.append(f"SAVED OUTPUTS ({len(outputs)})")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = int(out.get("size_bytes") or 0)
            lines.append(f"  {name}  ({size} bytes)")

    actors: list[dict[str, Any]] = list(collaboration.get("actors") or [])
    recent_decisions: list[dict[str, Any]] = list(collaboration.get("recent_decisions") or [])
    latest_handoff = collaboration.get("latest_handoff") or {}
    if actors or recent_decisions or latest_handoff:
        lines.append("")
        lines.append("COLLABORATION")
        for actor in actors[:3]:
            lines.append(
                f"  actor : {actor.get('name', 'operator')} "
                f"({int(actor.get('event_count') or 0)} touchpoints)"
            )
        for entry in recent_decisions[:3]:
            lines.append(f"  decision : {_format_collaboration_entry(entry)}")
        if latest_handoff:
            lines.append(f"  handoff  : {latest_handoff.get('id', '')} @ {latest_handoff.get('created_at', '')}")
    if story.get("milestones") or story.get("timeline"):
        lines.append("")
        lines.append("STORY RECAP")
        for item in list(story.get("milestones") or [])[:4]:
            lines.append(f"  milestone: {item}")
        for item in list(story.get("timeline") or [])[:4]:
            lines.append(f"  timeline : {item.get('label', 'Update')} · {item.get('summary', '')}")

    # ── Last summary ──────────────────────────────────────────────
    last_summary = str(session_data.get("last_summary") or "").strip()
    if last_summary:
        lines.append("")
        lines.append("LAST SUMMARY")
        lines.append(f"  {last_summary}")

    lines.append(sep)
    lines.append(f"Resume: openclaw --session {session_data.get('session_id', session_id)}")
    return "\n".join(lines)


def _inspect_session_rich(
    session_id: str,
    session_data: dict[str, Any],
    events: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    watch: dict[str, Any],
    routed_checkpoints: list[dict[str, Any]],
) -> None:
    """Print a rich-formatted session inspection view."""
    sid = session_data.get("session_id", session_id)
    title = session_data.get("title") or "Session"
    status = str(session_data.get("status") or "active")
    collaboration = build_collaboration_snapshot(session_id, limit=5)
    story = build_session_storyline(session_id, limit=5)
    mood = _session_mood_snapshot(require_session(session_id), watch_state=watch, collaboration_snapshot=collaboration)
    bookmarks: list[dict[str, Any]] = list(session_data.get("bookmarks") or [])
    # Metadata panel
    meta = _RichTable.grid(padding=(0, 2))
    meta.add_column(style="dim", min_width=12)
    meta.add_column()
    meta.add_row("🆔 id", f"[dim]{sid}[/]")
    meta.add_row("status", _status_cell(status, rich=True))
    meta.add_row("📁 cwd", f"[dim]{session_data.get('cwd', '')}[/]")
    meta.add_row("🕐 created", f"[dim]{session_data.get('created_at', '')}[/]")
    meta.add_row("🕐 updated", f"[yellow]{session_data.get('updated_at', '')}[/]")
    meta.add_row(
        "📊 stats",
        "  •  ".join(
            [
                _progress_cell("commands", str(session_data.get("command_count", 0)), status="active" if int(session_data.get("command_count", 0) or 0) else "idle", rich=True),
                _progress_cell("outputs", str(session_data.get("output_count", 0)), status="complete" if int(session_data.get("output_count", 0) or 0) else "idle", rich=True),
                _progress_cell("edits", str(session_data.get("file_edit_count", 0)), status="active" if int(session_data.get("file_edit_count", 0) or 0) else "idle", rich=True),
            ]
        ),
    )
    mood_cell = _session_mood_cell(mood, rich=True)
    if mood_cell:
        meta.add_row("🙂 mood", mood_cell)
    if story.get("headline"):
        meta.add_row("🎬 story", f"[bold]{story.get('headline', '')}[/]")
    if story.get("chapter_title"):
        meta.add_row("📚 chapter", f"{story.get('chapter_title', '')} · {story.get('chapter_detail', '')}")
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id:
        meta.add_row("📋 plan", f"[magenta]{plan_id}[/]")
    if task_id:
        meta.add_row("✅ task", f"[magenta]{task_id}[/]")
    files: list[str] = list(session_data.get("files") or [])
    if files:
        file_str = ", ".join(files[:5]) + (f" … +{len(files)-5}" if len(files) > 5 else "")
        meta.add_row("📄 files", f"[dim]{file_str}[/]")
    _RICH_CONSOLE.print(_RichPanel(meta, title=f"[bold cyan]{title}[/]", border_style="cyan", padding=(0, 1)))

    # Events panel
    if events:
        kind_styles = {"prompt": "cyan", "assistant": "green", "exec": "yellow", "edit": "magenta", "error": "red"}
        ev_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        ev_table.add_column("Time", style="dim", no_wrap=True)
        ev_table.add_column("Status", no_wrap=True)
        ev_table.add_column("Summary")
        for event in events[-8:]:
            ts = str(event.get("timestamp") or event.get("created_at") or "").strip()[-8:]
            kind = str(event.get("kind") or "").strip()
            meta_d = event.get("metadata") or {}
            summary = str(meta_d.get("summary") if isinstance(meta_d, dict) else "") or str(event.get("content") or "")
            style = kind_styles.get(kind, "dim")
            event_status = "error" if kind == "error" else "complete" if kind in {"assistant", "checkpoint"} else "active" if kind in {"exec", "edit"} else "info"
            ev_table.add_row(ts, f"[{style}]{_status_text(event_status)}[/]", f"{kind}: {summary[:80]}")
        _RICH_CONSOLE.print(_RichPanel(ev_table, title="[bold dim]Recent Events[/]", border_style="dim", padding=(0, 1)))

    # Outputs panel
    if outputs:
        out_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        out_table.add_column("Name", style="cyan")
        out_table.add_column("Size", justify="right", style="dim")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = _format_byte_count(int(out.get("size_bytes") or 0))
            out_table.add_row(name, size)
        _RICH_CONSOLE.print(_RichPanel(out_table, title=f"[bold dim]Saved Outputs ({len(outputs)})[/]", border_style="dim", padding=(0, 1)))

    if bookmarks:
        bookmark_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        bookmark_table.add_column("ID", style="cyan", no_wrap=True)
        bookmark_table.add_column("Turn", style="dim", no_wrap=True)
        bookmark_table.add_column("Label")
        for bookmark in bookmarks[-5:]:
            bookmark_table.add_row(
                str(bookmark.get("id") or ""),
                str(bookmark.get("turn_index") or ""),
                str(bookmark.get("label") or ""),
            )
        _RICH_CONSOLE.print(_RichPanel(bookmark_table, title="[bold dim]Bookmarks[/]", border_style="dim", padding=(0, 1)))

    milestones = list(story.get("milestones") or [])
    timeline = list(story.get("timeline") or [])
    cast = list(story.get("actor_highlights") or [])
    if milestones or timeline or cast:
        recap = _RichTable.grid(padding=(0, 1))
        recap.add_column(style="dim", min_width=11)
        recap.add_column()
        for item in milestones[:3]:
            recap.add_row("milestone", item)
        for item in cast[:2]:
            recap.add_row("cast", item)
        for item in timeline[:3]:
            recap.add_row(str(item.get("label") or "update"), str(item.get("summary") or ""))
        _RICH_CONSOLE.print(_RichPanel(recap, title="[bold dim]Story Recap[/]", border_style="magenta", padding=(0, 1)))

    _RICH_CONSOLE.print(f"  [dim]Resume:[/] [cyan]openclaw --session {sid}[/]")
