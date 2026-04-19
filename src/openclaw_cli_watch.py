"""openclaw_cli_watch.py — Watch/monitoring subsystem for OpenClaw CLI.

Extracted from openclaw_cli.py. Thin shims are left in the main module.
Do NOT import from openclaw_cli — circular import risk. Use lazy imports
inside function bodies where openclaw_cli symbols are needed at call time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from openclaw_cli_actions import write_text_file
from openclaw_cli_auth import OpenClawCliError
from openclaw_cli_path_utils import missing_feature_hint, output_name_from_title
from openclaw_cli_prefs import _A11Y_PLAIN_MODE, _PREFS
from openclaw_cli_session_display import _context_pressure_snapshot
from openclaw_cli_sessions import (
    SessionSummary,
    append_event,
    build_workspace_signature,
    collect_workspace_context,
    create_session,
    extract_prompt_targets,
    load_conversation_history,
    load_session,
    load_watch_state,
    require_session,
    save_output,
    save_watch_state,
    update_session,
)
from openclaw_cli_ui_core import _DM, _IS_TTY, _R, _get_is_tty

if TYPE_CHECKING:
    from openclaw_cli import CliConfig

# ---------------------------------------------------------------------------
# Rich — optional dependency
# ---------------------------------------------------------------------------

try:
    from rich.console import Console as _RichConsole
    from rich.panel import Panel as _RichPanel
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    _RICH_CONSOLE = None  # type: ignore[assignment]
    _RichPanel = None  # type: ignore[assignment,misc]
    _RichText = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants (mirror openclaw_cli.py)
# ---------------------------------------------------------------------------

WATCH_PROGRESS_LOG_LIMIT = 25
WATCH_RETRY_LIMIT = 3
WATCH_RETRY_MAX_DELAY_SECONDS = 8
WATCH_FOCUS_NOTE_CHARS = 120

TRANSIENT_WATCH_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "unable to reach",
    "connection refused",
    "refused the connection",
    "temporarily unavailable",
    "temporary failure",
    "network is unreachable",
    "connection reset",
    "connection aborted",
    "remote end closed connection",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)

# ---------------------------------------------------------------------------
# Pure utilities (local copies — stdlib only, no circular-import risk)
# ---------------------------------------------------------------------------


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_timestamp(raw_value: Any) -> datetime | None:
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


def _single_line_excerpt(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _truncate_preview(text: str, *, max_chars: int) -> str:
    clipped = str(text or "").strip()
    if len(clipped) <= max_chars:
        return clipped
    return clipped[: max_chars - 15].rstrip() + "\n...[truncated]..."


def _dedupe_preserve_order(lines: list[str]) -> list[str]:
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
# A11Y helpers (using _PREFS from openclaw_cli_prefs)
# ---------------------------------------------------------------------------


def _a11y_plain_mode() -> bool:
    return bool(_PREFS.get(_A11Y_PLAIN_MODE, False))


# ---------------------------------------------------------------------------
# Status display helpers (local copy — avoids circular import)
# ---------------------------------------------------------------------------


def _status_family(status: str) -> str:
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
    family = _status_family(status)
    return {
        "complete": "🟢",
        "active": "🔵",
        "waiting": "⏳",
        "idle": "⚪",
        "retry": "🔄",
        "warn": "🟡",
        "error": "🔴",
        "blocked": "⛔",
        "paused": "⏸",
        "info": "ℹ️",
        "stale": "🕰️",
    }.get(family, "●")


def _status_cell(status: str, *, detail: str = "") -> str:
    label = _status_text(status)
    suffix = f" · {detail}" if detail else ""
    return f"{label}{suffix}"


def _progress_cell(label: str, value: str, *, status: str = "") -> str:
    cell = f"{label}: {value}".strip()
    if not status:
        return cell
    badge = _status_cell(status)
    return f"{badge} · {cell}"


# ---------------------------------------------------------------------------
# Dashboard display helpers (local copy — avoids circular import)
# ---------------------------------------------------------------------------


def _dashboard_section_lines(title: str, lines: list[str]) -> list[str]:
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    if not clean:
        return []
    return [f"{title}:"] + [f"  - {line}" for line in clean]


def _append_dashboard_rich_section(
    body: Any,
    title: str,
    lines: list[str],
    *,
    title_style: str = "bold cyan",
    line_style: str = "",
) -> None:
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


def _print_meta_footer(*pairs: tuple[str, str]) -> None:
    """Print dim label + value metadata lines after a command."""
    if not pairs:
        return
    print()
    if _RICH_AVAILABLE and _IS_TTY:
        for label, value in pairs:
            _RICH_CONSOLE.print(f"  [dim]{label}:[/]  [dim]{value}[/]")
    else:
        for label, value in pairs:
            print(f"  {_DM}{label}:{_R}  {value}")


# ---------------------------------------------------------------------------
# Local replicas of simple openclaw_cli.py helpers
# ---------------------------------------------------------------------------


def parse_prompt(prompt_parts: list[str]) -> str:
    """Resolve a prompt from args or stdin for pipeline-friendly use."""
    joined = " ".join(prompt_parts).strip()
    if joined:
        return joined
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def ensure_cli_session(
    session_id: str,
    *,
    title: str,
    cwd: str | None = None,
    files: list[str] | None = None,
    plan_id: str = "",
    task_id: str = "",
) -> SessionSummary:
    """Load an existing session or create a new one when needed."""
    existing_id = str(session_id or "").strip()
    if existing_id:
        session = load_session(existing_id)
        if session is None:
            raise OpenClawCliError(f"Session '{existing_id}' was not found.")
        return session
    return create_session(title=title, cwd=cwd, files=files or [], plan_id=plan_id, task_id=task_id)


def persist_response(session_id: str, prompt: str, response: str) -> None:
    """Persist a prompt/response turn into the local CLI session store."""
    append_event(session_id, kind="user", content=prompt, metadata={"summary": prompt})
    append_event(session_id, kind="assistant", content=response, metadata={"summary": response})


def run_async(coro: Any) -> Any:
    """Run an async coroutine from the synchronous CLI entrypoint."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Lazy import helper — avoids circular import with openclaw_cli
# ---------------------------------------------------------------------------


def _get_cli_module() -> Any:
    """Return the openclaw_cli module, loading it lazily to avoid circular imports."""
    import openclaw_cli as _m  # noqa: PLC0415
    return _m


# ---------------------------------------------------------------------------
# Watch state management
# ---------------------------------------------------------------------------


def build_watch_state(
    *,
    session: SessionSummary,
    mode: str,
    goal: str,
    interval_seconds: int,
    max_polls: int,
    on_change: bool,
) -> dict[str, Any]:
    """Create the persisted watch-mode state payload."""
    now = utc_timestamp()
    return {
        "session_id": session.session_id,
        "mode": mode,
        "goal": goal,
        "cwd": session.cwd,
        "files": list(session.files or []),
        "plan_id": session.plan_id,
        "task_id": session.task_id,
        "interval_seconds": interval_seconds,
        "max_polls": max_polls,
        "poll_count": 0,
        "on_change": on_change,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
        "last_run_at": "",
        "last_output_path": "",
        "last_summary": "",
        "last_error": "",
        "workspace_signature": "",
        "failure_count": 0,
        "consecutive_failures": 0,
        "retry_limit": WATCH_RETRY_LIMIT,
        "retry_history": [],
        "progress_log": [],
        "active_checkpoint": {},
        "checkpoints": [],
    }


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


def watch_retry_delay_seconds(attempt: int) -> int:
    """Return a capped exponential backoff delay for transient watch retries."""
    return min(WATCH_RETRY_MAX_DELAY_SECONDS, max(1, 2 ** max(0, attempt - 1)))


def is_transient_watch_error(exc: "Exception | str") -> bool:
    """Classify whether a watch failure is worth retrying automatically."""
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return any(marker in message for marker in TRANSIENT_WATCH_ERROR_MARKERS)


def start_watch_checkpoint(*, iteration: int, mode: str) -> dict[str, Any]:
    """Create the mutable state object for an in-flight watch checkpoint."""
    now = utc_timestamp()
    return {
        "poll": iteration,
        "mode": mode,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "progress": [],
        "attempts": [],
    }


def record_watch_progress(
    *,
    session_id: str,
    state: dict[str, Any],
    iteration: int,
    mode: str,
    phase: str,
    message: str,
    output_json: bool,
) -> None:
    """Persist and optionally render watch progress updates."""
    entry = {
        "poll": iteration,
        "mode": mode,
        "phase": phase,
        "message": message,
        "created_at": utc_timestamp(),
    }
    progress_log = list(state.get("progress_log") or [])
    progress_log.append(entry)
    state["progress_log"] = progress_log[-WATCH_PROGRESS_LOG_LIMIT:]
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        active_progress = list(active_checkpoint.get("progress") or [])
        active_progress.append(entry)
        active_checkpoint["progress"] = active_progress[-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint["phase"] = phase
        active_checkpoint["last_message"] = message
        active_checkpoint["updated_at"] = entry["created_at"]
    state["updated_at"] = entry["created_at"]
    save_watch_state(session_id, normalize_watch_state(state))
    if not output_json:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim][[/][cyan]{iteration}[/][dim]][/] [dim]{mode}/{phase}:[/] {message}")
        else:
            print(f"[watch {iteration}] {mode}/{phase}: {message}")


def _watch_retry_delay_total(state: dict[str, Any]) -> int:
    total = 0
    for entry in list(state.get("retry_history") or []):
        try:
            delay = int(entry.get("delay_seconds") or watch_retry_delay_seconds(int(entry.get("attempt") or 1)))
        except (TypeError, ValueError):
            delay = 0
        total += max(0, delay)
    return total


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


def print_watch_resume_snapshot(session_id: str, state: dict[str, Any], *, output_json: bool) -> None:
    """Print the most useful persisted state when resuming a watch session."""
    if output_json:
        return
    status = str(state.get("status") or "unknown").strip() or "unknown"
    poll_count = int(state.get("poll_count") or 0)
    last_summary = str(state.get("last_summary") or "").strip()
    last_error = str(state.get("last_error") or "").strip()
    active_checkpoint = state.get("active_checkpoint")
    recent_progress = list(state.get("progress_log") or [])[-3:]

    if _RICH_AVAILABLE and _IS_TTY:
        emoji = _status_emoji(status)
        border = "green" if status in ("active", "running") else ("yellow" if status in ("paused", "idle") else ("red" if status in ("failed", "error") else "dim"))
        body = _RichText()
        body.append(f"{emoji} status    ", style="dim")
        body.append(f"{status}", style=f"bold {border}")
        body.append("\n🔢 polls    ", style="dim")
        body.append(f"{poll_count}", style="cyan")
        if last_summary:
            body.append("\n📝 last     ", style="dim")
            body.append(last_summary, style="white")
        if last_error:
            body.append("\n⚠️  error    ", style="dim")
            body.append(last_error, style="red")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                body.append("\n⏳ partial  ", style="dim")
                body.append(partial, style="yellow")
        if recent_progress:
            body.append("\n📋 recent   ", style="dim")
            for entry in recent_progress:
                body.append(f"\n   • {entry.get('message', '')}", style="dim")
        _RICH_CONSOLE.print(_RichPanel(body, title=f"[bold]resuming watch[/] [dim]{session_id}[/]", border_style=border, padding=(0, 1)))
    else:
        print(f"Resuming watch {session_id} (status={status}, completed polls={poll_count}).")
        if last_summary:
            print(f"Last checkpoint: {last_summary}")
        if last_error:
            print(f"Last error: {last_error}")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                print(f"Partial progress: {partial}")
        if recent_progress:
            print("Recent progress:")
            for entry in recent_progress:
                print(f"  - {entry.get('message', '')}")


def refresh_watch_controls(session_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted intervention flags into the in-memory watch state."""
    latest = load_watch_state(session_id)
    if latest is None:
        return state
    latest = normalize_watch_state(latest)
    state["interventions"] = list(latest.get("interventions") or [])
    state["force_run_once"] = bool(latest.get("force_run_once"))
    state["stop_requested"] = bool(latest.get("stop_requested"))
    state["stop_requested_at"] = str(latest.get("stop_requested_at", "") or "")
    state["last_intervention_at"] = str(latest.get("last_intervention_at", "") or "")
    return state


def resolve_watch_intervention(
    state: dict[str, Any],
    *,
    action: str,
    status: str,
    note: str = "",
) -> bool:
    """Resolve the newest pending intervention of the requested action."""
    for item in reversed(list(state.get("interventions") or [])):
        if str(item.get("action") or "") != action or str(item.get("status") or "") != "pending":
            continue
        item["status"] = status
        item["applied_at"] = utc_timestamp()
        if note:
            item["note"] = note[:240]
        return True
    return False


def stop_watch_from_intervention(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    mode: str,
    output_json: bool,
) -> int:
    """Persist a graceful watch stop requested through the dashboard."""
    interrupted_at = utc_timestamp()
    summary = "Watch stopped by dashboard intervention."
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        partial = str(active_checkpoint.get("last_message") or "").strip()
        active_checkpoint.update(
            {
                "status": "interrupted",
                "completed_at": interrupted_at,
                "summary": partial[:160] if partial else "checkpoint interrupted by dashboard intervention",
            }
        )
        state.setdefault("checkpoints", []).append(dict(active_checkpoint))
        state["active_checkpoint"] = {}
    resolve_watch_intervention(
        state,
        action="graceful-stop",
        status="applied",
        note="Watch loop exited cleanly after dashboard stop request.",
    )
    state["status"] = "interrupted"
    state["updated_at"] = interrupted_at
    state["last_run_at"] = interrupted_at
    state["last_summary"] = summary
    state["stop_requested"] = False
    save_watch_state(session.session_id, state)
    append_event(
        session.session_id,
        kind="intervention",
        content=summary,
        metadata={
            "summary": summary,
            "mode": mode,
            "action": "graceful-stop",
            "plan_id": session.plan_id,
            "task_id": session.task_id,
        },
    )
    update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
    if not output_json:
        _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
    return 0


def render_watch_iteration(
    *,
    iteration: int,
    mode: str,
    summary: str,
    output_path: str,
    output_json: bool,
    max_polls: int = 0,
) -> None:
    """Print a compact watch checkpoint result."""
    payload = {
        "iteration": iteration,
        "mode": mode,
        "summary": summary,
        "saved": output_path,
    }
    if output_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    iter_label = f"{iteration}/{max_polls}" if max_polls > 0 else f"iter {iteration}"
    if _RICH_AVAILABLE and _IS_TTY:
        _MODE_COLORS = {"analyze": "cyan", "research": "blue", "write": "yellow"}
        mode_color = _MODE_COLORS.get(str(mode).lower(), "white")
        _RICH_CONSOLE.print(f"\U0001f504 [bold]watch [{iter_label}][/]  [{mode_color}]{mode}[/]  [dim]·[/]  {summary}")
        _print_meta_footer(("saved", output_path))
    else:
        print(f"[watch {iter_label}] {mode}: {summary}")
        print(f"saved: {output_path}")


# ---------------------------------------------------------------------------
# Watch focus and timing display helpers
# ---------------------------------------------------------------------------


def _watch_focus_lines(state: dict[str, Any]) -> list[str]:
    state = normalize_watch_state(state)
    timing = _watch_timing_summary(state)
    lines: list[str] = []
    active_checkpoint = state.get("active_checkpoint") or {}
    if timing["active_phase"]:
        phase_line = timing["active_phase"]
        if timing["active_phase_elapsed"] is not None:
            phase_line += f" · {_format_elapsed_compact(timing['active_phase_elapsed'])}"
        lines.append(f"focus: {_progress_cell('phase', phase_line, status='active')}")
    latest_checkpoint = None
    checkpoints = list(state.get("checkpoints") or [])
    if checkpoints:
        latest_checkpoint = checkpoints[-1]
    if not latest_checkpoint and active_checkpoint:
        latest_checkpoint = active_checkpoint
    if latest_checkpoint:
        poll_value = latest_checkpoint.get("poll")
        note = str(
            latest_checkpoint.get("note")
            or latest_checkpoint.get("summary")
            or latest_checkpoint.get("status")
            or latest_checkpoint.get("phase")
            or ""
        ).strip()
        checkpoint_label = f"checkpoint {poll_value}" if poll_value else "checkpoint"
        if note:
            lines.append(f"{checkpoint_label}: {_single_line_excerpt(note, max_chars=WATCH_FOCUS_NOTE_CHARS)}")
    interventions = [item for item in list(state.get("interventions") or []) if isinstance(item, dict)]
    if interventions:
        latest = interventions[-1]
        action = str(latest.get("action") or "intervention").strip().replace("-", " ")
        reason = _single_line_excerpt(str(latest.get("reason") or "").strip(), max_chars=WATCH_FOCUS_NOTE_CHARS)
        status = str(latest.get("status") or "info").strip()
        detail = action if not reason else f"{action} · {reason}"
        lines.append(f"intervention: {_status_cell(status if status != 'pending' else 'info', detail=detail)}")
    last_error = str(state.get("last_error") or "").strip()
    if last_error:
        lines.append(f"focus error: {_single_line_excerpt(last_error, max_chars=WATCH_FOCUS_NOTE_CHARS)}")
    return lines


def load_plan_goal(plan_id: str) -> str:
    """Resolve a plan goal when watch mode is attached to an existing plan."""
    normalized = str(plan_id or "").strip()
    if not normalized:
        return ""
    from agent_loop import load_plan as load_agent_plan  # noqa: PLC0415

    plan = load_agent_plan(normalized)
    return str(plan.goal or "").strip() if plan else ""


# ---------------------------------------------------------------------------
# Watch status display (uses lazy import for protected-area session helpers)
# ---------------------------------------------------------------------------


def _print_watch_status(state: dict[str, Any]) -> None:
    """Render a compact watch-state status panel."""
    _cli = _get_cli_module()
    _session_operator_snapshot = _cli._session_operator_snapshot
    _operator_snapshot_lines = _cli._operator_snapshot_lines

    state = normalize_watch_state(state)
    goal = str(state.get("goal") or "").strip()
    mode = str(state.get("mode") or "").strip()
    w_status = str(state.get("status") or "").strip()
    poll_count = int(state.get("poll_count") or 0)
    max_polls = int(state.get("max_polls") or 0)
    failure_count = int(state.get("failure_count") or 0)
    retry_limit = int(state.get("retry_limit") or 3)
    last_run_at = str(state.get("last_run_at") or "").strip()
    interval_seconds = int(state.get("interval_seconds") or 0)
    last_error = str(state.get("last_error") or "").strip()
    last_summary = str(state.get("last_summary") or "").strip()
    history = load_conversation_history(str(state.get("session_id") or ""), limit_turns=0) if state.get("session_id") else []
    pressure = _context_pressure_snapshot(
        history,
        system_prompt=str(_PREFS.get("system_prompt", "") or ""),
        pending_inject=str(getattr(_cli, "_next_inject", "") or ""),
        model_hint=_PREFS.get("last_model", ""),
        route_hint=_PREFS.get("route_mode", ""),
    )
    timing = _watch_timing_summary(state)
    operator_snapshot = _session_operator_snapshot(
        SessionSummary(
            session_id=str(state.get("session_id") or "watch"),
            title=str(goal or "Watch session"),
            cwd=str(state.get("cwd") or ""),
            files=list(state.get("files") or []),
            plan_id=str(state.get("plan_id") or ""),
            task_id=str(state.get("task_id") or ""),
            status=str(w_status or "active"),
            last_summary=last_summary,
        ),
        watch_state=state,
    )
    polls_value = f"{poll_count}/{max_polls or '∞'}"

    phase_status = "retry" if w_status == "retrying" else "active"
    summary_lines = []
    if goal:
        summary_lines.append(goal[:80])
    summary_lines.extend(
        [
            _progress_cell("mode", mode or "watch", status=w_status or "active"),
            _progress_cell("status", w_status or "unknown", status=w_status or "unknown"),
            _progress_cell("polls", polls_value, status=w_status or "active"),
        ]
    )
    if w_status in {"completed", "complete"}:
        summary_lines.append(_progress_cell("mood", "milestone reached · latest watch loop finished cleanly", status="complete"))
    elif w_status == "retrying" or failure_count:
        summary_lines.append(_progress_cell("mood", "resilient recovery · retry budget still active", status="retry"))
    elif poll_count >= 2 or last_summary:
        summary_lines.append(_progress_cell("mood", "building momentum · signals are settling in", status="active"))
    detail_lines = []
    if failure_count:
        detail_lines.append(_progress_cell("failures", f"{failure_count}/{retry_limit}", status="retry"))
    else:
        detail_lines.append(_progress_cell("retry budget", str(retry_limit), status="idle"))
    if interval_seconds:
        detail_lines.append(_progress_cell("interval", f"{interval_seconds}s", status="waiting"))
    if timing["active_phase"]:
        phase_line = timing["active_phase"]
        if timing["active_phase_elapsed"] is not None:
            phase_line += f" · {_format_elapsed_compact(timing['active_phase_elapsed'])}"
        detail_lines.append(_progress_cell("phase", phase_line, status=phase_status))
    if timing["latest_duration"] is not None:
        detail_lines.append(_progress_cell("last duration", _format_elapsed_compact(timing["latest_duration"]), status="info"))
    if timing["retry_delay_total"]:
        detail_lines.append(_progress_cell("backoff", _format_elapsed_compact(timing["retry_delay_total"]), status="retry"))
    if last_run_at:
        detail_lines.append(f"last run: {last_run_at}")
    if last_summary:
        detail_lines.append(f"last output: {last_summary[:80]}")
    if last_error:
        detail_lines.append(f"last error: {last_error[:80]}")
    if int(pressure["pct_next"]) >= 50:
        detail_lines.append(
            _progress_cell(
                "context pressure",
                f"~{int(pressure['next_tokens']):,} tok next retry ({int(pressure['pct_next_raw'])}% of {pressure['limit_label']})",
                status="warn" if int(pressure["pct_next"]) < 80 else "retry",
            )
        )
        if bool(pressure["overflow"]):
            detail_lines.append("overflow cue: next retry likely exceeds the resolved window")
    if pressure["hidden_pressure"]:
        detail_lines.append("hidden context cue: system or queued inject content pushes the next retry closer to capacity")
    if pressure["has_pending_inject"]:
        detail_lines.append("recovery cue: /inject clear drops the queued one-shot context before a retry")
    detail_lines.extend(_watch_focus_lines(state))
    detail_lines.extend(_operator_snapshot_lines(operator_snapshot)[:5])
    action_lines = [
        "/watch history to inspect checkpoint history",
        "/watch intervene <msg> to leave an operator breadcrumb",
    ]
    if w_status in {"completed", "complete"}:
        action_lines.insert(0, "/session to review the resulting session snapshot")
    else:
        action_lines.insert(0, "/watch retry-limit N to tune retry budget")
    if last_error or failure_count:
        action_lines.append('/watch intervene "recovery note" to guide the next loop')
    if int(pressure["pct_next"]) >= 50:
        action_lines.append("/tokeninfo to check whether context pressure is affecting the next retry")
    if int(pressure["pct_next"]) >= 80:
        action_lines.append("/bookmark before /clear if manual recovery needs a clean restart")
        action_lines.append("/context to preview what the next retry will inherit")
    if bool(pressure["overflow"]) or pressure["hidden_pressure"]:
        action_lines.append("/promptdebug to verify hidden context before the next retry")
        action_lines.append("/inject status or /system view to inspect hidden context before the next retry")
    if pressure["has_pending_inject"]:
        action_lines.append("/inject clear to remove the queued one-shot context before the next retry")
    if list(state.get("interventions") or []):
        action_lines.append("/collab share to capture the operator-facing snapshot")
    action_lines = _dedupe_preserve_order(action_lines)
    _print_dashboard_surface(
        "Watch Control Tower",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=action_lines,
        border_style="cyan",
    )


def _print_watch_history(state: dict[str, Any]) -> None:
    """Render recent watch progress log, retries, and operator notes."""
    state = normalize_watch_state(state)
    progress_log = list(state.get("progress_log") or [])
    retry_history = list(state.get("retry_history") or [])
    notes = [e for e in list(state.get("interventions") or []) if e.get("action") == "operator-note"]

    if not progress_log and not retry_history and not notes:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No watch history yet.[/]")
        else:
            print("No watch history yet.")
        return

    summary_lines = [
        _progress_cell("recent checkpoints", str(len(progress_log[-10:])), status="active" if progress_log else "idle"),
        _progress_cell("retries", str(len(retry_history[-3:])), status="retry" if retry_history else "idle"),
        _progress_cell("operator notes", str(len(notes[-3:])), status="info" if notes else "idle"),
    ]
    detail_lines = []
    focus_lines = _watch_focus_lines(state)
    if focus_lines:
        detail_lines.append("Focused inspection:")
        detail_lines.extend(focus_lines)
    if progress_log:
        detail_lines.append("Recent progress:")
        for entry in progress_log[-10:]:
            ts = str(entry.get("timestamp") or entry.get("at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            phase = str(entry.get("phase") or "poll").strip()
            note = str(entry.get("note") or entry.get("summary") or entry.get("content") or "").strip()
            elapsed = _elapsed_seconds(entry.get("created_at"))
            suffix = f" ({_format_elapsed_compact(elapsed)} ago)" if elapsed is not None else ""
            entry_status = "complete" if entry.get("ok") else "warn" if entry.get("warning") else "active"
            detail_lines.append(f"{ts_short}  {_status_cell(entry_status, detail=phase)}  {note[:100]}{suffix}")
    if retry_history:
        detail_lines.append("Retry checkpoints:")
        for entry in retry_history[-3:]:
            ts = str(entry.get("at") or entry.get("timestamp") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            reason = str(entry.get("reason") or entry.get("error") or "").strip()
            delay = entry.get("delay_seconds")
            delay_text = f" · backoff {_format_elapsed_compact(delay)}" if delay else ""
            detail_lines.append(f"{ts_short}  {_status_cell('retry')}  {reason[:100]}{delay_text}")
    if notes:
        detail_lines.append("Operator notes:")
        for note_entry in notes[-3:]:
            ts = str(note_entry.get("created_at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts
            reason = str(note_entry.get("reason") or "").strip()
            detail_lines.append(f"{ts_short}  {_status_cell('info', detail='operator-note')}  {reason[:100]}")
    _print_dashboard_surface(
        "Watch History",
        summary_lines=summary_lines,
        detail_lines=detail_lines,
        action_lines=_dedupe_preserve_order(
            [
                "/watch status to return to the live control tower",
                "/watch intervene <msg> to annotate the next checkpoint",
                "/watch retry-limit N to tune recovery budget after repeated retries" if retry_history else "",
                "/collab share to carry forward the latest operator note" if notes else "",
            ]
        ),
        border_style="dim",
    )


# ---------------------------------------------------------------------------
# execute_watch_iteration — single watch loop checkpoint
# ---------------------------------------------------------------------------


def execute_watch_iteration(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    config: "CliConfig",
    output_override: str = "",
    deep_research: bool = False,
    title: str = "",
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[str, str]:
    """Run a single watch-mode checkpoint and persist its output."""
    _cli = _get_cli_module()
    invoke_openclaw = _cli.invoke_openclaw
    bind_config_to_session = _cli.bind_config_to_session
    _plan_task_context_snippet = _cli._plan_task_context_snippet
    build_analysis_prompt = _cli.build_analysis_prompt
    build_write_prompt = _cli.build_write_prompt

    goal = str(state.get("goal") or "").strip()
    mode = str(state.get("mode") or "analyze").strip().lower()
    cwd = str(state.get("cwd") or session.cwd or "").strip() or None
    targets = list(state.get("files") or session.files or [])
    if on_progress:
        on_progress("context", "Collecting workspace context")
    normalized_targets, context_text = collect_workspace_context(cwd=cwd, targets=targets)
    if normalized_targets != session.files or (cwd and cwd != session.cwd):
        session = update_session(session.session_id, cwd=cwd or session.cwd, files=normalized_targets)
        state["cwd"] = session.cwd
        state["files"] = list(session.files or [])

    output_path = str(output_override or "").strip()
    if mode == "analyze":
        if on_progress:
            on_progress("request", "Submitting analysis checkpoint")
        prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
        append_event(
            session.session_id,
            kind="analyze",
            content=goal,
            metadata={
                "summary": goal,
                "cwd": session.cwd,
                "files": normalized_targets,
                "plan_id": session.plan_id,
                "task_id": session.task_id,
                "automation_mode": "watch",
            },
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving analysis checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved_path = output_path
        else:
            saved_path = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-analysis", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved_path

    if mode == "research":
        try:
            from research_agent import ResearchAgent  # noqa: PLC0415
        except ImportError as exc:
            raise OpenClawCliError(missing_feature_hint("openclaw watch --mode research")) from exc

        effective_query = goal
        plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
        if plan_ctx:
            effective_query = f"{plan_ctx}\n\n{effective_query}"
        if context_text and normalized_targets:
            effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

        if on_progress:
            on_progress("request", "Starting research checkpoint")

        async def _progress(message: str) -> None:
            if on_progress:
                on_progress("research", message)

        append_event(
            session.session_id,
            kind="research",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        report = run_async(ResearchAgent().run(effective_query, on_progress=_progress, deep=deep_research))
        if on_progress:
            on_progress("persist", "Saving research checkpoint")
        if output_path:
            write_text_file(output_path, content=report)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-research", suffix=".md"),
                    report,
                )
            )
        append_event(session.session_id, kind="assistant", content=report, metadata={"summary": f"saved research to {saved}"})
        return report, saved

    if mode == "write":
        document_title = title or goal[:80] or "OpenClaw Watch Draft"
        if on_progress:
            on_progress("request", "Submitting writing checkpoint")
        prompt = build_write_prompt(task=goal, context_text=context_text, session=session, title=document_title)
        append_event(
            session.session_id,
            kind="write",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving writing checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{document_title}-{state.get('poll_count', 0)}", default_stem="watch-draft", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved

    raise OpenClawCliError(f"Unsupported watch mode: {mode}")


# ---------------------------------------------------------------------------
# cmd_watch_bell — /watch bell subcommand handler
# ---------------------------------------------------------------------------


def cmd_watch_bell(rest: str) -> None:
    """Handle the /watch bell [on|off] subcommand.

    Called from _cmd_watch in openclaw_cli_cmd_workflow when ``sub == "bell"``.
    Modifies _PREFS["watch_bell"] in-place and prints a confirmation.
    With no argument (or empty rest) it prints the current state.
    """
    arg = rest.strip().lower()
    if arg == "on":
        _PREFS["watch_bell"] = True
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[green]✓[/] watch bell [green]on[/]")
        else:
            print("watch bell on")
    elif arg == "off":
        _PREFS["watch_bell"] = False
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[green]✓[/] watch bell [red]off[/]")
        else:
            print("watch bell off")
    elif arg == "":
        current = bool(_PREFS.get("watch_bell", False))
        state_str = "on" if current else "off"
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"watch bell is [cyan]{state_str}[/]")
        else:
            print(f"watch bell is {state_str}")
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[red]Usage: /watch bell [on|off][/]")
        else:
            print("Usage: /watch bell [on|off]")


# ---------------------------------------------------------------------------
# handle_watch_command — main watch orchestrator
# ---------------------------------------------------------------------------


def _watch_init_args(args: argparse.Namespace, config: "CliConfig") -> dict:
    """Parse and validate watch command arguments; return a dict of resolved values."""
    resume_id = str(getattr(args, "resume", "") or "").strip()
    requested_session = str(getattr(args, "session", "") or config.session_id or "").strip()
    if resume_id and requested_session and resume_id != requested_session:
        raise OpenClawCliError("Use either --resume or --session for watch mode, not both.")

    existing_state = load_watch_state(resume_id or requested_session) if (resume_id or requested_session) else None
    session_seed = require_session(resume_id or requested_session) if (resume_id or requested_session) else None
    goal_parts, prompt_targets = extract_prompt_targets(
        list(getattr(args, "goal", []) or []),
        cwd=getattr(args, "cwd", None) or (session_seed.cwd if session_seed else None),
    )
    prompt_goal = parse_prompt(goal_parts) if goal_parts else ""
    plan_id = str(getattr(args, "plan_id", "") or (existing_state or {}).get("plan_id") or (session_seed.plan_id if session_seed else "")).strip()
    task_id = str(getattr(args, "task_id", "") or (existing_state or {}).get("task_id") or (session_seed.task_id if session_seed else "")).strip()
    goal = prompt_goal or str((existing_state or {}).get("goal") or "").strip() or load_plan_goal(plan_id)
    if task_id and not goal:
        goal = f"Continue task {task_id}"
    if not goal:
        raise OpenClawCliError("Watch mode needs a goal, plan, or task to follow.")

    mode = str(getattr(args, "mode", "") or (existing_state or {}).get("mode") or "analyze").strip().lower()
    interval_seconds = max(1, int(getattr(args, "interval", 0) or (existing_state or {}).get("interval_seconds") or 60))
    max_polls = max(0, int(getattr(args, "iterations", 0) or (existing_state or {}).get("max_polls") or 0))
    on_change = bool(getattr(args, "on_change", False) or (existing_state or {}).get("on_change"))
    cwd = str(getattr(args, "cwd", "") or (existing_state or {}).get("cwd") or (session_seed.cwd if session_seed else "")).strip() or None
    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    if not explicit_targets:
        explicit_targets = list((existing_state or {}).get("files") or (session_seed.files if session_seed else []) or [])
    normalized_targets, _ = collect_workspace_context(cwd=cwd, targets=explicit_targets)

    return {
        "resume_id": resume_id,
        "requested_session": requested_session,
        "existing_state": existing_state,
        "goal": goal,
        "mode": mode,
        "interval_seconds": interval_seconds,
        "max_polls": max_polls,
        "on_change": on_change,
        "cwd": cwd,
        "normalized_targets": normalized_targets,
        "plan_id": plan_id,
        "task_id": task_id,
    }


def _watch_setup_session(
    session_id: str,
    *,
    goal: str,
    mode: str,
    cwd: "str | None",
    normalized_targets: list,
    plan_id: str,
    task_id: str,
    interval_seconds: int,
    max_polls: int,
    on_change: bool,
    existing_state: "dict | None",
) -> tuple:
    """Create/update the CLI session and build normalised watch state; return (session, state, resume_snapshot)."""
    session = ensure_cli_session(
        session_id,
        title=f"Watch: {goal[:60]}",
        cwd=cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
    )
    session = update_session(
        session.session_id,
        cwd=cwd or session.cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
        automation_mode=mode,
        automation_status="watching",
        watch_interval_seconds=interval_seconds,
    )
    resume_snapshot = normalize_watch_state(existing_state) if existing_state else None
    state = existing_state or build_watch_state(
        session=session,
        mode=mode,
        goal=goal,
        interval_seconds=interval_seconds,
        max_polls=max_polls,
        on_change=on_change,
    )
    state = normalize_watch_state(state)
    state.update(
        {
            "mode": mode,
            "goal": goal,
            "cwd": session.cwd,
            "files": list(normalized_targets),
            "plan_id": plan_id,
            "task_id": task_id,
            "interval_seconds": interval_seconds,
            "max_polls": max_polls,
            "on_change": on_change,
            "status": "running",
            "updated_at": utc_timestamp(),
        }
    )
    save_watch_state(session.session_id, state)
    return session, state, resume_snapshot


def _watch_print_header(
    session: Any,
    *,
    mode: str,
    goal: str,
    interval_seconds: int,
    max_polls: int,
    resume_snapshot: "dict | None",
    config: "CliConfig",
) -> None:
    """Print the rich (or plain-text) watch startup banner."""
    if config.output_json:
        return
    if resume_snapshot:
        print_watch_resume_snapshot(session.session_id, resume_snapshot, output_json=config.output_json)
    if _RICH_AVAILABLE and _IS_TTY:
        _body = _RichText()
        _body.append("  session  ", style="dim")
        _body.append(f"{session.session_id}\n")
        _body.append("  mode     ", style="dim")
        _body.append(f"{mode}\n")
        _body.append("  goal     ", style="dim")
        _body.append(f"{goal[:60]}\n")
        _body.append("  interval ", style="dim")
        _body.append(f"{interval_seconds}s")
        _body.append("  ·  max ", style="dim")
        _body.append(f"{'infinite' if max_polls == 0 else max_polls}\n")
        _body.append("  Ctrl-C to pause & resume", style="dim")
        _RICH_CONSOLE.print(_RichPanel(_body, border_style="cyan", title="[bold cyan]👁  watch[/]"))
    else:
        print(
            f"Watching session {session.session_id} in {mode} mode "
            f"(interval={interval_seconds}s, max polls={'infinite' if max_polls == 0 else max_polls})."
        )
        print("Press Ctrl-C to stop and resume later with `openclaw watch --resume <session_id>`.")


def _watch_run_iteration_with_retry(
    session: Any,
    state: dict,
    *,
    mode: str,
    plan_id: str,
    task_id: str,
    interval_seconds: int,
    max_polls: int,
    config: "CliConfig",
    args: argparse.Namespace,
    workspace_signature: str,
    force_run_once: bool,
) -> dict:
    """Execute one watch iteration with retry logic; return the updated state."""
    if force_run_once:
        state["force_run_once"] = False
        resolve_watch_intervention(
            state,
            action="force-checkpoint",
            status="applied",
            note="Forced one checkpoint despite unchanged workspace.",
        )
        record_watch_progress(
            session_id=session.session_id,
            state=state,
            iteration=state["poll_count"],
            mode=mode,
            phase="control",
            message="Dashboard requested a forced checkpoint; running anyway.",
            output_json=config.output_json,
        )
    state["active_checkpoint"] = start_watch_checkpoint(iteration=state["poll_count"], mode=mode)
    save_watch_state(session.session_id, state)
    retry_limit = max(1, int(state.get("retry_limit") or WATCH_RETRY_LIMIT))
    attempt = 0
    while True:
        attempt += 1
        active_checkpoint = state.setdefault("active_checkpoint", start_watch_checkpoint(iteration=state["poll_count"], mode=mode))
        attempts = list(active_checkpoint.get("attempts") or [])
        attempts.append({"attempt": attempt, "started_at": utc_timestamp(), "status": "running"})
        active_checkpoint["attempts"] = attempts[-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint["updated_at"] = utc_timestamp()
        save_watch_state(session.session_id, state)

        try:
            result_text, output_path = execute_watch_iteration(
                session=require_session(session.session_id),
                state=state,
                config=config,
                output_override=str(getattr(args, "output", "") or "").strip(),
                deep_research=bool(getattr(args, "deep", False)),
                title=str(getattr(args, "title", "") or "").strip(),
                on_progress=lambda phase, message: record_watch_progress(
                    session_id=session.session_id,
                    state=state,
                    iteration=state["poll_count"],
                    mode=mode,
                    phase=phase,
                    message=message,
                    output_json=config.output_json,
                ),
            )
            finished_at = utc_timestamp()
            active_checkpoint["attempts"][-1].update(
                {
                    "finished_at": finished_at,
                    "status": "completed",
                    "duration_seconds": _elapsed_seconds(
                        active_checkpoint["attempts"][-1].get("started_at"),
                        finished_at,
                    ),
                }
            )
            break
        except Exception as exc:  # broad: intentional  # noqa: BLE001
            error_message = str(exc).strip() or exc.__class__.__name__
            transient = is_transient_watch_error(error_message)
            finished_at = utc_timestamp()
            active_checkpoint["attempts"][-1].update(
                {
                    "finished_at": finished_at,
                    "status": "failed",
                    "error": error_message,
                    "transient": transient,
                    "duration_seconds": _elapsed_seconds(
                        active_checkpoint["attempts"][-1].get("started_at"),
                        finished_at,
                    ),
                }
            )
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
            state["last_error"] = error_message
            retry_entry = {
                "poll": state["poll_count"],
                "attempt": attempt,
                "error": error_message,
                "transient": transient,
                "created_at": utc_timestamp(),
                "delay_seconds": watch_retry_delay_seconds(attempt) if transient and attempt < retry_limit else 0,
            }
            retry_history = list(state.get("retry_history") or [])
            retry_history.append(retry_entry)
            state["retry_history"] = retry_history[-WATCH_PROGRESS_LOG_LIMIT:]
            state["status"] = "retrying" if transient and attempt < retry_limit else "failed"
            state["updated_at"] = utc_timestamp()
            save_watch_state(session.session_id, state)
            update_session(
                session.session_id,
                automation_mode=mode,
                automation_status="retrying" if transient and attempt < retry_limit else "failed",
                watch_interval_seconds=interval_seconds,
            )
            if transient and attempt < retry_limit:
                delay_seconds = int(retry_entry.get("delay_seconds") or watch_retry_delay_seconds(attempt))
                record_watch_progress(
                    session_id=session.session_id,
                    state=state,
                    iteration=state["poll_count"],
                    mode=mode,
                    phase="retry",
                    message=(
                        f"Transient failure on attempt {attempt}/{retry_limit}: "
                        f"{error_message}. Retrying in {delay_seconds}s."
                    ),
                    output_json=config.output_json,
                )
                print(f"  ↺ Watch auto-retried (attempt {attempt}): {error_message}")
                time.sleep(delay_seconds)
                continue
            failure_summary = f"{mode} failed: {error_message[:160]}"
            checkpoint_completed_at = utc_timestamp()
            active_checkpoint.update(
                {
                    "status": "failed",
                    "completed_at": checkpoint_completed_at,
                    "summary": failure_summary,
                    "error": error_message,
                    "transient": transient,
                    "duration_seconds": _elapsed_seconds(
                        active_checkpoint.get("started_at"),
                        checkpoint_completed_at,
                    ),
                }
            )
            state.setdefault("checkpoints", []).append(dict(active_checkpoint))
            state["last_run_at"] = active_checkpoint["completed_at"]
            state["last_summary"] = failure_summary
            state["active_checkpoint"] = {}
            save_watch_state(session.session_id, state)
            append_event(
                session.session_id,
                kind="checkpoint",
                content=failure_summary,
                metadata={
                    "summary": failure_summary,
                    "mode": mode,
                    "poll": state["poll_count"],
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": error_message,
                    "retry_count": attempt,
                    "retry_delay_seconds": _watch_retry_delay_total(state),
                    "elapsed_seconds": active_checkpoint.get("duration_seconds"),
                },
            )
            if not config.output_json:
                print(f"  ⚠ Watch stopped — exhausted retries after {attempt} attempt(s)")
                if _PREFS.get("watch_bell", False):
                    print("\a", end="", flush=True)
            raise OpenClawCliError(
                f"Watch poll {state['poll_count']} failed after {attempt} attempt(s): {error_message}"
            ) from exc
    checkpoint_summary = str(result_text or "").strip().splitlines()[0][:160] if str(result_text or "").strip() else f"{mode} checkpoint"
    checkpoint = {
        "poll": state["poll_count"],
        "created_at": utc_timestamp(),
        "completed_at": utc_timestamp(),
        "summary": checkpoint_summary,
        "output_path": output_path,
        "workspace_signature": workspace_signature,
        "status": "completed",
        "attempt_count": attempt,
        "progress": list(state.get("active_checkpoint", {}).get("progress") or []),
        "attempts": list(state.get("active_checkpoint", {}).get("attempts") or []),
        "started_at": str(state.get("active_checkpoint", {}).get("started_at") or ""),
    }
    checkpoint["duration_seconds"] = _elapsed_seconds(checkpoint.get("started_at") or checkpoint.get("created_at"), checkpoint.get("completed_at"))
    state.setdefault("checkpoints", []).append(checkpoint)
    state["workspace_signature"] = workspace_signature
    state["last_run_at"] = checkpoint["completed_at"]
    state["last_output_path"] = output_path
    state["last_summary"] = checkpoint_summary
    state["last_error"] = ""
    state["consecutive_failures"] = 0
    state["status"] = "running"
    state["updated_at"] = checkpoint["completed_at"]
    state["active_checkpoint"] = {}
    save_watch_state(session.session_id, state)
    append_event(
        session.session_id,
        kind="checkpoint",
        content=checkpoint_summary,
        metadata={
            "summary": checkpoint_summary,
            "mode": mode,
            "poll": state["poll_count"],
            "output_path": output_path,
            "plan_id": plan_id,
            "task_id": task_id,
            "elapsed_seconds": checkpoint.get("duration_seconds"),
            "retry_delay_seconds": _watch_retry_delay_total(state),
        },
    )
    update_session(
        session.session_id,
        automation_mode=mode,
        automation_status="running",
        watch_interval_seconds=interval_seconds,
    )
    render_watch_iteration(
        iteration=state["poll_count"],
        mode=mode,
        summary=checkpoint_summary,
        output_path=output_path,
        output_json=config.output_json,
        max_polls=max_polls,
    )
    return state


def handle_watch_command(args: argparse.Namespace, *, config: "CliConfig") -> int:
    """Run a resumable watch loop over a session workspace."""
    init = _watch_init_args(args, config)
    session, state, resume_snapshot = _watch_setup_session(
        init["resume_id"] or init["requested_session"],
        goal=init["goal"],
        mode=init["mode"],
        cwd=init["cwd"],
        normalized_targets=init["normalized_targets"],
        plan_id=init["plan_id"],
        task_id=init["task_id"],
        interval_seconds=init["interval_seconds"],
        max_polls=init["max_polls"],
        on_change=init["on_change"],
        existing_state=init["existing_state"],
    )
    _watch_print_header(
        session,
        mode=init["mode"],
        goal=init["goal"],
        interval_seconds=init["interval_seconds"],
        max_polls=init["max_polls"],
        resume_snapshot=resume_snapshot,
        config=config,
    )
    mode = init["mode"]
    plan_id = init["plan_id"]
    task_id = init["task_id"]
    interval_seconds = init["interval_seconds"]
    max_polls = init["max_polls"]
    on_change = init["on_change"]

    try:
        while max_polls == 0 or int(state.get("poll_count", 0) or 0) < max_polls:
            state = refresh_watch_controls(session.session_id, state)
            if state.get("stop_requested"):
                return stop_watch_from_intervention(
                    session=session,
                    state=state,
                    mode=mode,
                    output_json=config.output_json,
                )
            state["poll_count"] = int(state.get("poll_count", 0) or 0) + 1
            workspace_signature = build_workspace_signature(cwd=state.get("cwd"), targets=list(state.get("files") or []))
            force_run_once = bool(state.get("force_run_once"))
            if on_change and state.get("workspace_signature") and workspace_signature == state.get("workspace_signature") and not force_run_once:
                state["updated_at"] = utc_timestamp()
                state["status"] = "waiting"
                save_watch_state(session.session_id, state)
                update_session(session.session_id, automation_status="waiting", automation_mode=mode)
                if not config.output_json:
                    _iter_label = f"{state['poll_count']}/{max_polls}" if max_polls > 0 else f"iter {state['poll_count']}"
                    print(f"[watch {_iter_label}] unchanged; waiting for workspace updates.")
            else:
                state = _watch_run_iteration_with_retry(
                    session, state,
                    mode=mode,
                    plan_id=plan_id,
                    task_id=task_id,
                    interval_seconds=interval_seconds,
                    max_polls=max_polls,
                    config=config,
                    args=args,
                    workspace_signature=workspace_signature,
                    force_run_once=force_run_once,
                )
            if max_polls and int(state.get("poll_count", 0) or 0) >= max_polls:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        active_checkpoint = state.get("active_checkpoint")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            interrupted_at = utc_timestamp()
            interruption_summary = str(active_checkpoint.get("last_message") or f"{mode} interrupted").strip()[:160]
            active_checkpoint.update(
                {
                    "status": "interrupted",
                    "completed_at": interrupted_at,
                    "summary": interruption_summary,
                }
            )
            state.setdefault("checkpoints", []).append(dict(active_checkpoint))
            state["last_run_at"] = interrupted_at
            state["last_summary"] = interruption_summary
            state["active_checkpoint"] = {}
        state["status"] = "interrupted"
        state["updated_at"] = utc_timestamp()
        save_watch_state(session.session_id, state)
        update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
        if not config.output_json:
            _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
        return 130

    state["status"] = "completed" if max_polls else "idle"
    state["updated_at"] = utc_timestamp()
    save_watch_state(session.session_id, state)
    update_session(
        session.session_id,
        automation_mode=mode,
        automation_status="completed" if max_polls else "idle",
        watch_interval_seconds=interval_seconds,
    )
    if not config.output_json:
        poll_count = int(state.get("poll_count") or 0)
        last_summary = str(state.get("last_summary") or "").strip()
        excerpt = last_summary[:60] if last_summary else "no output"
        print(f"  ✓ Watch session complete — {poll_count} iteration(s), last: {excerpt}")
        if _PREFS.get("watch_bell", False):
            print("\a", end="", flush=True)
        _print_meta_footer(("session", session.session_id))
    return 0
