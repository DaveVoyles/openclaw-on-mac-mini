"""openclaw_cli_session_cmds.py — Pure formatting/data-building helpers for session command handlers.

Extracted from the large session command handlers in openclaw_cli.py.
Handlers (_cmd_*) remain in openclaw_cli.py; only inner pure helpers live here.

Allowed imports: openclaw_cli_sessions, openclaw_cli_ui_core, stdlib only.
Do NOT import from openclaw_cli — circular import.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Local copy of _format_elapsed_compact (pure stdlib; avoids circular import)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _cmd_events helpers
# ---------------------------------------------------------------------------

def _build_event_label(ev: dict, excerpt_len: int = 80) -> str:
    """Build the display label for a single event row, including timing metadata.

    Extracted from the duplicated label-building logic in _cmd_events (both
    the Rich and plain-text rendering branches).
    """
    meta = ev.get("metadata") or {}
    content = str(ev.get("content") or "").strip()
    summary = str((meta.get("summary") if isinstance(meta, dict) else "") or "").strip()
    label = (summary or content[:excerpt_len]).replace("\n", " ")

    if isinstance(meta, dict):
        timing_bits: list[str] = []
        if meta.get("elapsed_seconds") is not None:
            timing_bits.append(_format_elapsed_compact(meta.get("elapsed_seconds")))
        if meta.get("approval_seconds") is not None:
            timing_bits.append(f"approval {_format_elapsed_compact(meta.get('approval_seconds'))}")
        if meta.get("retry_delay_seconds") is not None:
            timing_bits.append(f"backoff {_format_elapsed_compact(meta.get('retry_delay_seconds'))}")
        if timing_bits:
            label = f"{label}  ({', '.join(timing_bits)})"

    kind = str(ev.get("kind") or "").strip()
    if kind == "checkpoint":
        label = f"{label} · milestone"
    elif kind == "collab":
        label = f"{label} · shared momentum"
    elif kind == "error":
        label = f"{label} · recovery needed"
    return label


def _event_preview_lines(
    events: list[dict],
    *,
    max_items: int = 3,
    excerpt_len: int = 72,
) -> list[str]:
    """Build bounded preview rows for the latest events."""
    preview_lines: list[str] = []
    for ev in list(events or [])[: max(1, max_items)]:
        ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
        ts_short = ts[11:19] if len(ts) > 10 else ts or "—"
        kind = str(ev.get("kind") or "event").strip() or "event"
        label = _build_event_label(ev, excerpt_len=excerpt_len)
        preview_lines.append(f"{ts_short} · {kind} · {label}")
    return preview_lines


def _event_recovery_actions(
    events: list[dict],
    *,
    decisions_only: bool = False,
) -> list[str]:
    """Suggest bounded inspection or recovery follow-through for recent events."""
    actions: list[str] = []
    kinds = [str(ev.get("kind") or "").strip().lower() for ev in list(events or [])]
    latest = list(events or [])[:1]
    latest_event = latest[0] if latest else {}
    latest_kind = str(latest_event.get("kind") or "").strip().lower()
    latest_meta = latest_event.get("metadata") if isinstance(latest_event.get("metadata"), dict) else {}
    latest_summary = str((latest_meta or {}).get("summary") or latest_event.get("content") or "").strip().lower()
    if decisions_only:
        actions.append("/session to compare the latest decisions with session health")
    else:
        actions.append("/events decisions to isolate routing and approval decisions")
    if "watch" in kinds:
        actions.append("/watch status to inspect the live retry/control snapshot")
    if "error" in kinds or "recovery needed" in latest_summary:
        actions.append("/watch history to inspect retries, checkpoints, and operator notes")
        actions.append("/context to preview what the next recovery attempt will inherit")
    if latest_kind in {"exec", "edit"} or "exec" in kinds or "edit" in kinds:
        actions.append("/outputs 1 to inspect the newest artifact or diff context")
    if "checkpoint" in kinds:
        actions.append("/bookmark to capture this recovery point before clearing context")
    if "approval" in kinds:
        actions.append("/session to confirm approval state and follow-up actions")
    deduped: list[str] = []
    seen: set[str] = set()
    for action in actions:
        text = str(action or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


# ---------------------------------------------------------------------------
# _cmd_search helpers
# ---------------------------------------------------------------------------

def _highlight_ansi(text: str, query: str, ql: str, hl_on: str, hl_off: str) -> str:
    """Highlight the first query match in *text* using ANSI escape codes.

    Args:
        text:   The string to search in.
        query:  Original-case query (used for the replacement span length).
        ql:     Lower-cased query (used for case-insensitive find).
        hl_on:  ANSI sequence to start highlight (e.g. bold-yellow).
        hl_off: ANSI reset sequence.
    """
    idx = text.lower().find(ql)
    if idx == -1:
        return text
    return text[:idx] + hl_on + text[idx : idx + len(query)] + hl_off + text[idx + len(query) :]


def _highlight_rich(text: str, query: str) -> str:
    """Highlight all query matches in *text* using Rich markup (bold yellow)."""
    return re.sub(
        re.escape(query),
        f"[bold yellow]{query}[/]",
        text,
        flags=re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# _cmd_plan helpers
# ---------------------------------------------------------------------------

def _build_plan_focus_lines(
    lines: list[str],
    plan_id: str,
    done_count: int,
    unchecked: list[tuple[int, str]],
    summary: str | None,
) -> list[str]:
    """Build the focus-view lines for a plan file when unchecked tasks exist.

    The caller is responsible for the early-exit case when *unchecked* is empty
    (all tasks done / no task items).  This helper is only called when at least
    one unchecked task is present.

    Args:
        lines:      All lines from the plan file.
        plan_id:    Plan identifier (used for display).
        done_count: Number of already-checked tasks.
        unchecked:  List of (line_index, line_text) for unchecked tasks.
        summary:    Optional goal/summary string from plan metadata.

    Returns:
        A list of plain-text lines for rendering via Rich panel or print().
    """
    focus_lines: list[str] = []
    if summary:
        focus_lines.append(f"Goal: {summary}")
        focus_lines.append("")
    focus_lines.append(f"Done: {done_count}  Remaining: {len(unchecked)}")
    focus_lines.append("")

    cur_idx, cur_line = unchecked[0]
    focus_lines.append("▶ Current:")
    focus_lines.append(f"  {cur_line.strip()}")
    for ctx_line in lines[cur_idx + 1 : cur_idx + 4]:
        if ctx_line.strip() and not re.match(r"^\s*-\s+\[ \]", ctx_line):
            focus_lines.append(f"    {ctx_line.strip()}")
        else:
            break

    if len(unchecked) > 1:
        _, nxt_line = unchecked[1]
        focus_lines.append("")
        focus_lines.append("→ Next:")
        focus_lines.append(f"  {nxt_line.strip()}")

    return focus_lines


# ---------------------------------------------------------------------------
# _cmd_handoff helpers
# ---------------------------------------------------------------------------

def _build_handoff_check_lines(check: dict) -> list[str]:
    """Build plain-text display lines for a handoff readiness check result.

    Extracted from the ``/handoff check`` branch of _cmd_handoff.
    Returns a list of strings ready to print() one-by-one.
    """
    readiness = str(check.get("readiness") or "needs-attention")
    checks: list[tuple[str, bool, str]] = list(check.get("checks") or [])
    open_risks: list[dict] = list(check.get("open_risks") or [])
    open_incidents: list[dict] = list(check.get("open_incidents") or [])

    out: list[str] = [
        "Handoff readiness",
        "-----------------",
        f"state: {readiness}",
    ]
    for name, ok, detail in checks:
        badge = "OK" if ok else "WARN"
        out.append(f"  {badge:<4} {name:<8} {detail}")
    if open_risks:
        out.append("open risks:")
        for entry in open_risks[:5]:
            level = str(entry.get("risk_level") or "medium").upper()
            content_str = str(entry.get("content") or entry.get("summary") or "").strip()
            out.append(f"  - {level} · {content_str}")
    if open_incidents:
        out.append("open incidents:")
        for entry in open_incidents[:5]:
            content_str = str(entry.get("content") or entry.get("summary") or "").strip()
            out.append(f"  - {content_str}")
    return out


# ---------------------------------------------------------------------------
# _print_workspace_capsule / _cmd_workspace helpers
# ---------------------------------------------------------------------------

def _build_workspace_capsule_plain_lines(capsule: dict) -> list[str]:
    """Build plain-text display lines for a workspace capsule.

    Used by the plain-text branch of _print_workspace_capsule and by
    _cmd_workspace.  Returns strings without ANSI codes or Rich markup so
    the caller can choose how to render them.
    """
    tracked_files: list = list(capsule.get("tracked_files") or [])
    bookmarks: list = list(capsule.get("bookmarks") or [])
    recent_outputs: list = list(capsule.get("recent_outputs") or [])

    lines: list[str] = [
        f"cwd: {capsule.get('cwd', '')}",
        f"files: {capsule.get('tracked_file_count', len(tracked_files))}",
        f"bookmarks: {capsule.get('bookmark_count', len(bookmarks))}",
        f"outputs: {capsule.get('output_count', len(recent_outputs))}",
    ]

    watch_status = str(capsule.get("watch_status") or "").strip()
    if watch_status:
        lines.append(f"watch: {watch_status}")

    signature = str(capsule.get("workspace_signature") or "").strip()
    if signature:
        lines.append(f"signature: {signature}")

    if capsule.get("plan_id"):
        lines.append(f"plan: {capsule.get('plan_id')}")
    if capsule.get("task_id"):
        lines.append(f"task: {capsule.get('task_id')}")

    if recent_outputs:
        lines.append("recent outputs:")
        lines.extend(f"  - {item.get('name', '')}" for item in recent_outputs[:3])
    if bookmarks:
        lines.append("recent bookmarks:")
        lines.extend(
            f"  - [{item.get('id', '')}] {item.get('label', '')}" for item in bookmarks[-3:]
        )
    return lines
