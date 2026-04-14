"""
openclaw_cli_layout — Terminal layout modes and workspace preset display.

Imports from:
  - openclaw_cli_ui_core  (_get_is_tty)
  - openclaw_cli_sessions (load_session, load_watch_state, SessionSummary, …)
Does NOT import from openclaw_cli.py.
"""
from __future__ import annotations

import os
import textwrap
from typing import Any, Callable

from openclaw_cli_ui_core import _get_is_tty

from openclaw_cli_sessions import (
    SessionSummary,
    build_collaboration_snapshot,
    list_saved_outputs,
    load_saved_output_preview,
    load_session,
    load_watch_state,
)

# Constant also used by the main module (kept in sync).
_OUTPUT_DASHBOARD_EXCERPT_CHARS = 220

# ---------------------------------------------------------------------------
# Internal plain-text helpers (no dependency on openclaw_cli.py)
# ---------------------------------------------------------------------------

def _terminal_width_layout(*, fallback: int = 80) -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return fallback


def _status_family_layout(status: str) -> str:
    s = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"ok", "healthy", "done", "completed", "success", "succeeded", "complete"}:
        return "complete"
    if s in {"active", "running", "in_progress", "working", "processing", "streaming"}:
        return "active"
    if s in {"pending", "queued", "waiting", "scheduled"}:
        return "waiting"
    if s == "idle":
        return "idle"
    if s in {"retry", "retrying", "backoff", "recovering"}:
        return "retry"
    if s in {"warn", "warning", "degraded", "attention"}:
        return "warn"
    if s in {"error", "failed", "failure", "unhealthy"}:
        return "error"
    if s in {"blocked", "stuck", "needs_input", "needs_input"}:
        return "blocked"
    if s in {"paused", "stopped", "cancelled", "canceled"}:
        return "paused"
    if s in {"info", "note", "fresh", "new"}:
        return "info"
    if s in {"stale", "old", "expired"}:
        return "stale"
    return "unknown"


def _status_text_layout(status: str) -> str:
    family = _status_family_layout(status)
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


def _status_cell_layout(status: str, *, detail: str = "") -> str:
    """Plain-text status cell (no rich/ANSI — layout surfaces are always plain)."""
    label = _status_text_layout(status)
    suffix = f" · {detail}" if detail else ""
    return f"{label}{suffix}"


def _progress_cell_layout(label: str, value: str, *, status: str = "") -> str:
    cell = f"{label}: {value}".strip()
    if not status:
        return cell
    badge = _status_cell_layout(status)
    return f"{badge} · {cell}"


def _truncate_preview_layout(text: str, *, max_chars: int) -> str:
    clipped = str(text or "").strip()
    if len(clipped) <= max_chars:
        return clipped
    return clipped[: max_chars - 15].rstrip() + "\n...[truncated]..."


def _single_line_excerpt_layout(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "…"


def _preview_block_lines_layout(title: str, text: str, *, max_chars: int, max_lines: int = 3) -> list[str]:
    preview = _truncate_preview_layout(text, max_chars=max_chars)
    if not preview:
        return []
    lines = preview.splitlines()[:max_lines]
    block = [f"{title}:"]
    block.extend(f"  {line}" for line in lines if line.strip())
    return block


def _format_byte_count_layout(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes or 0)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(size)} B"


def _format_elapsed_compact_layout(seconds: Any) -> str:
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


def _format_collaboration_entry_layout(entry: dict[str, Any]) -> str:
    actor = str(entry.get("actor") or "operator").strip()
    summary = str(entry.get("summary") or entry.get("content") or "").strip()
    tags = [str(tag or "").strip() for tag in list(entry.get("tags") or []) if str(tag or "").strip()]
    suffix = f" [{' '.join('#' + tag for tag in tags)}]" if tags else ""
    return f"{actor}: {summary}{suffix}".strip()


# ---------------------------------------------------------------------------
# Layout mode / preset config (pure prefs-based)
# ---------------------------------------------------------------------------

def _effective_layout_mode(prefs: dict) -> str:
    """Return the normalized active layout mode."""
    layout = str(prefs.get("layout", "normal") or "normal").strip().lower()
    if layout not in {"compact", "normal", "verbose", "plain"}:
        return "normal"
    return layout


def _layout_preset_name(prefs: dict) -> str:
    """Return the normalized active layout preset name, if any."""
    preset = str(prefs.get("layout_preset", "") or "").strip().lower()
    return preset if preset in {"focus", "watch-monitor", "handoff"} else ""


def _layout_focus_name(prefs: dict) -> str:
    """Return the active pane within the current layout preset."""
    focus = str(prefs.get("layout_focus", "primary") or "primary").strip().lower()
    return focus if focus in {"primary", "supporting"} else "primary"


def _layout_preset_config(prefs: dict, name: str = "") -> dict[str, str]:
    """Return the documented surface pairing for a layout preset."""
    preset = name or _layout_preset_name(prefs)
    return {
        "focus": {
            "label": "focus",
            "primary": "/session",
            "supporting": "/context",
        },
        "watch-monitor": {
            "label": "watch-monitor",
            "primary": "/watch status",
            "supporting": "/watch history + /outputs",
        },
        "handoff": {
            "label": "handoff",
            "primary": "/collab",
            "supporting": "session summary + recent outputs",
        },
    }.get(preset, {})


def _layout_preset_fallback(
    prefs: dict,
    *,
    width: int | None = None,
    is_tty: bool | None = None,
) -> str:
    """Return the current preset rendering fallback label."""
    if not _layout_preset_name(prefs):
        return "single-pane"
    tty = _get_is_tty() if is_tty is None else bool(is_tty)
    cols = _terminal_width_layout() if width is None else int(width)
    plain_mode = bool(prefs.get("plain_mode", False))
    if not tty or plain_mode or cols < 100:
        return "single-pane"
    if cols < 140:
        return "stacked"
    return "multi-pane"


def _layout_pane_line_limit(prefs: dict) -> int:
    """Return the maximum number of lines shown per preset pane."""
    return {
        "compact": 6,
        "normal": 9,
        "verbose": 14,
        "plain": 9,
    }.get(_effective_layout_mode(prefs), 9)


def _layout_pane_block(
    prefs: dict,
    title: str,
    lines: list[str],
    *,
    active: bool = False,
) -> list[str]:
    """Return a bounded plain-text pane block for workspace presets."""
    clean = [str(line).strip() for line in lines if str(line or "").strip()]
    limit = _layout_pane_line_limit(prefs)
    clipped = clean[:limit]
    if len(clean) > limit:
        clipped.append(f"… {len(clean) - limit} more line(s); open the source surface for full detail")
    status = "ACTIVE" if active else "READY"
    return [f"{status} · {title}"] + [f"  {line}" for line in clipped]


def _layout_column_lines(left: list[str], right: list[str], *, width: int) -> list[str]:
    """Lay out two pane blocks side-by-side using safe plain text."""
    separator = " │ "
    column_width = max(28, (max(width, 72) - len(separator)) // 2)

    def _wrap(block: list[str]) -> list[str]:
        rows: list[str] = []
        for line in block:
            rows.extend(
                textwrap.wrap(
                    str(line),
                    width=column_width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                or [""]
            )
        return rows

    left_rows = _wrap(left)
    right_rows = _wrap(right)
    total_rows = max(len(left_rows), len(right_rows))
    merged: list[str] = []
    for index in range(total_rows):
        left_line = left_rows[index] if index < len(left_rows) else ""
        right_line = right_rows[index] if index < len(right_rows) else ""
        merged.append(f"{left_line:<{column_width}}{separator}{right_line:<{column_width}}".rstrip())
    return merged


# ---------------------------------------------------------------------------
# Content rendering helpers (session/watch/collab/outputs)
# ---------------------------------------------------------------------------

def _layout_outputs_lines(prefs: dict, session_id: str) -> list[str]:
    """Return compact recent-output lines for layout presets."""
    outputs = list_saved_outputs(session_id, limit=3)
    if not outputs:
        return [
            _status_cell_layout("idle", detail="no saved outputs"),
            "/outputs to inspect artifacts once something is saved",
        ]
    lines = [
        _progress_cell_layout("artifacts", str(len(list_saved_outputs(session_id, limit=0))), status="complete"),
    ]
    preview = load_saved_output_preview(session_id, "1", max_chars=_OUTPUT_DASHBOARD_EXCERPT_CHARS)
    if preview:
        lines.append(
            f"focused preview: {str(preview.get('name') or '').strip()} · "
            f"{_format_byte_count_layout(int(preview.get('size_bytes') or 0))}"
        )
        lines.extend(_preview_block_lines_layout("excerpt", str(preview.get("preview") or ""), max_chars=_OUTPUT_DASHBOARD_EXCERPT_CHARS))
    for index, item in enumerate(outputs, start=1):
        lines.append(
            f"{index}. {str(item.get('name') or '').strip()} · "
            f"{_format_byte_count_layout(int(item.get('size_bytes') or 0))}"
        )
    return lines


def _layout_collab_lines(prefs: dict, session_id: str) -> list[str]:
    """Return collaboration snapshot lines for layout presets."""
    snapshot = build_collaboration_snapshot(session_id, limit=3)
    actors = list(snapshot.get("actors") or [])
    decisions = list(snapshot.get("recent_decisions") or [])
    notes = list(snapshot.get("recent_notes") or [])
    latest_handoff = snapshot.get("latest_handoff") or {}
    lines = [
        _progress_cell_layout("actors", str(len(actors)), status="info" if actors else "idle"),
        _progress_cell_layout("decisions", str(len(decisions)), status="complete" if decisions else "idle"),
    ]
    for actor in actors[:2]:
        lines.append(
            f"actor: {str(actor.get('name') or 'operator').strip()} · "
            f"{int(actor.get('event_count') or 0)} touchpoints"
        )
    if decisions:
        lines.append(f"decision: {_single_line_excerpt_layout(_format_collaboration_entry_layout(decisions[0]), max_chars=96)}")
    if notes:
        lines.append(f"note: {_single_line_excerpt_layout(_format_collaboration_entry_layout(notes[0]), max_chars=96)}")
    if latest_handoff:
        lines.append(
            f"handoff: {str(latest_handoff.get('id') or '').strip()} · "
            f"{str(latest_handoff.get('created_at') or '').strip()}"
        )
    lines.append("/collab share to print the full handoff bundle")
    return lines


def _layout_watch_lines(
    prefs: dict,
    state: dict[str, Any] | None,
    *,
    normalize_watch_state_fn: Callable[[dict], dict] | None = None,
    watch_timing_summary_fn: Callable[[dict], dict] | None = None,
    watch_focus_lines_fn: Callable[[dict], list[str]] | None = None,
) -> list[str]:
    """Return watch-monitor lines for layout presets."""
    if not state:
        return [
            _status_cell_layout("idle", detail="no active watch"),
            "Start one with: openclaw watch --goal …",
            "/watch status to inspect the live control tower when a watch exists",
        ]
    if normalize_watch_state_fn is not None:
        state = normalize_watch_state_fn(state)
    timing: dict[str, Any] = {}
    if watch_timing_summary_fn is not None:
        timing = watch_timing_summary_fn(state)
    lines = [
        _progress_cell_layout("status", str(state.get("status") or "active"), status=str(state.get("status") or "active")),
        _progress_cell_layout("polls", f"{int(state.get('poll_count') or 0)}/{int(state.get('max_polls') or 0) or '∞'}", status=str(state.get("status") or "active")),
    ]
    goal = str(state.get("goal") or "").strip()
    if goal:
        lines.append(f"goal: {_single_line_excerpt_layout(goal, max_chars=96)}")
    active_phase = timing.get("active_phase", "")
    active_phase_elapsed = timing.get("active_phase_elapsed")
    if active_phase:
        phase_line = active_phase
        if active_phase_elapsed is not None:
            phase_line += f" · {_format_elapsed_compact_layout(active_phase_elapsed)}"
        lines.append(_progress_cell_layout("phase", phase_line, status="active"))
    if watch_focus_lines_fn is not None:
        lines.extend(watch_focus_lines_fn(state)[:4])
    progress_log = list(state.get("progress_log") or [])
    if progress_log:
        latest = progress_log[-1]
        note = str(latest.get("note") or latest.get("summary") or latest.get("content") or "").strip()
        if note:
            lines.append(f"latest checkpoint: {_single_line_excerpt_layout(note, max_chars=96)}")
    lines.append("/watch intervene <msg> to leave an operator breadcrumb")
    return lines


def _layout_session_lines(
    prefs: dict,
    session: SessionSummary,
    *,
    session_preview_lines_fn: Callable[[SessionSummary], list[str]] | None = None,
) -> list[str]:
    """Return session health lines for layout presets."""
    lines = [
        session.title or session.session_id,
        _progress_cell_layout("status", str(session.status or "active"), status=session.status or "active"),
        _progress_cell_layout("updated", session.updated_at or "—", status="info"),
        _progress_cell_layout("files", str(len(session.files or [])), status="active" if session.files else "idle"),
    ]
    if session.cwd:
        lines.append(f"cwd: {session.cwd}")
    if session.plan_id:
        lines.append(f"plan: {session.plan_id}")
    if session.task_id:
        lines.append(f"task: {session.task_id}")
    if session_preview_lines_fn is not None:
        lines.extend(session_preview_lines_fn(session))
    return lines


def _print_layout_preset_workspace(
    prefs: dict,
    session_id: str,
    *,
    width: int | None = None,
    is_tty: bool | None = None,
) -> None:
    """Render the active layout preset as a pane-like workspace view."""
    preset = _layout_preset_name(prefs)
    if not preset:
        print("Workspace preset is single-pane. Use /layout preset focus|watch-monitor|handoff to opt in.")
        return
    sid = str(session_id or "").strip()
    if not sid:
        print(f"Workspace preset {_layout_preset_config(prefs, preset).get('label', preset)} saved. Resume a session, then run /layout show.")
        return
    session = load_session(sid)
    if session is None:
        print(f"Workspace preset {_layout_preset_config(prefs, preset).get('label', preset)} saved. Resume a session, then run /layout show.")
        return

    focus = _layout_focus_name(prefs)
    watch_state = load_watch_state(session.session_id)
    if preset == "focus":
        primary_title = "Session summary"
        primary_lines = _layout_session_lines(prefs, session)
        if watch_state:
            supporting_title = "Watch monitor"
            supporting_lines = _layout_watch_lines(prefs, watch_state)
        elif session.output_count:
            supporting_title = "Artifact preview"
            supporting_lines = _layout_outputs_lines(prefs, session.session_id)
        else:
            supporting_title = "Collaboration snapshot"
            supporting_lines = _layout_collab_lines(prefs, session.session_id)
    elif preset == "watch-monitor":
        primary_title = "Watch monitor"
        primary_lines = _layout_watch_lines(prefs, watch_state)
        supporting_title = "Recent artifacts"
        supporting_lines = _layout_outputs_lines(prefs, session.session_id)
    else:
        primary_title = "Collaboration snapshot"
        primary_lines = _layout_collab_lines(prefs, session.session_id)
        supporting_title = "Session health"
        supporting_lines = _layout_session_lines(prefs, session)

    render_mode = _layout_preset_fallback(prefs, width=width, is_tty=is_tty)
    col_width = width if width is not None else _terminal_width_layout(fallback=100)
    header = [
        f"Workspace preset: {_layout_preset_config(prefs, preset).get('label', preset)}",
        f"Render mode: {render_mode}",
        f"Active pane: {focus}",
        "",
    ]
    primary_block = _layout_pane_block(prefs, primary_title, primary_lines, active=focus == "primary")
    supporting_block = _layout_pane_block(prefs, supporting_title, supporting_lines, active=focus == "supporting")
    if render_mode == "multi-pane":
        body = _layout_column_lines(primary_block, supporting_block, width=col_width)
    elif render_mode == "stacked":
        body = [*primary_block, "", *supporting_block]
    else:
        active_block = primary_block if focus == "primary" else supporting_block
        collapsed = supporting_title if focus == "primary" else primary_title
        body = [
            *active_block,
            "",
            f"Supporting pane collapsed. Open {collapsed.lower()} via its source command or widen the terminal.",
        ]
    print("\n".join(header + body))
