"""openclaw_cli_session_utils.py — Session utility helpers: summarization, preview, alerting.

Extracted from openclaw_cli.py. Contains:
- summarize_session: compact single-session summary
- _session_preview_lines: preview lines for a session
- _collect_operator_alerts: alert collection across sessions
- _last_trace_snapshot: last routing decision snapshot

Allowed imports: openclaw_cli_sessions, openclaw_cli_session_display, openclaw_cli_watch,
                 openclaw_cli_prefs, stdlib only.
Do NOT import from openclaw_cli — circular import.
"""

from __future__ import annotations

import logging
from typing import Any

from openclaw_cli_prefs import _PREFS
from openclaw_cli_session_display import (
    _format_collaboration_entry,
    _format_elapsed_compact,
    _operator_snapshot_lines,
    _progress_cell,
    _session_age_label,
    _session_is_stale,
    _session_mood_cell,
    _session_mood_snapshot,
    _session_operator_snapshot,
    _single_line_excerpt,
)
from openclaw_cli_sessions import (
    SessionSummary,
    build_collaboration_snapshot,
    build_session_storyline,
    get_last_decision_event,
    list_saved_outputs,
    list_sessions,
    load_saved_output_preview,
    load_watch_state,
)
from openclaw_cli_watch import _watch_focus_lines, _watch_timing_summary

_LOG = logging.getLogger(__name__)

SESSION_PREVIEW_OUTPUT_CHARS = 160


def summarize_session(session: SessionSummary, *, _age_label_fn: Any = None) -> str:
    """Render a compact single-session summary for terminal output."""
    try:
        watch_state = load_watch_state(session.session_id)
    except Exception:  # broad: intentional
        _LOG.debug("load_watch_state failed for %s", session.session_id, exc_info=True)
        watch_state = None
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    operator_snapshot = _session_operator_snapshot(
        session,
        watch_state=watch_state,
        collaboration_snapshot=snapshot,
    )
    age_fn = _age_label_fn if _age_label_fn is not None else _session_age_label
    parts = [
        f"session: {session.session_id}",
        f"title: {session.title}",
        _progress_cell("status", str(session.status or "active"), status=session.status or "active"),
        f"cwd: {session.cwd}",
        f"age: {age_fn(session)}",
        f"updated: {session.updated_at}",
        f"freshness: {'stale' if _session_is_stale(session) else 'fresh'}",
        _progress_cell("commands", str(session.command_count), status="active" if session.command_count else "idle"),
        _progress_cell("outputs", str(session.output_count), status="complete" if session.output_count else "idle"),
    ]
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        parts.append(mood_cell)
    parts.extend(_operator_snapshot_lines(operator_snapshot)[:4])
    if session.plan_id:
        parts.append(f"plan: {session.plan_id}")
    if session.task_id:
        parts.append(f"task: {session.task_id}")
    if session.files:
        parts.append("files: " + ", ".join(session.files[:6]))
    if session.last_summary:
        parts.append(f"last: {session.last_summary}")
    if session.automation_mode:
        status = session.automation_status or "active"
        parts.append(_progress_cell("automation", f"{session.automation_mode} ({status})", status=status))
        if watch_state:
            timing = _watch_timing_summary(watch_state)
            timing_parts = []
            if timing["active_phase"]:
                detail = f"{timing['active_phase']}"
                if timing["active_phase_elapsed"] is not None:
                    detail += f" {_format_elapsed_compact(timing['active_phase_elapsed'])}"
                timing_parts.append(f"phase {detail}")
            if timing["latest_duration"] is not None:
                timing_parts.append(f"last run {_format_elapsed_compact(timing['latest_duration'])}")
            if timing["retry_delay_total"]:
                timing_parts.append(f"retry backoff {_format_elapsed_compact(timing['retry_delay_total'])}")
            if timing_parts:
                parts.append("timing: " + " · ".join(timing_parts))
    if session.checkpoint_count:
        parts.append(_progress_cell("checkpoints", str(session.checkpoint_count), status="complete"))
    if session.last_checkpoint_at:
        parts.append(f"last checkpoint: {session.last_checkpoint_at}")
    return "\n".join(parts)


def _session_preview_lines(session: SessionSummary) -> list[str]:
    lines: list[str] = []
    watch_state = None
    story = build_session_storyline(session.session_id, limit=3)
    if story.get("headline"):
        lines.append(f"story: {_single_line_excerpt(str(story.get('headline') or ''), max_chars=100)}")
    if session.last_summary:
        lines.append(f"latest activity: {_single_line_excerpt(session.last_summary, max_chars=100)}")
    if session.automation_mode:
        try:
            watch_state = load_watch_state(session.session_id)
        except Exception:  # broad: intentional
            _LOG.debug("load_watch_state failed for %s", session.session_id, exc_info=True)
            watch_state = None
        if watch_state:
            lines.extend(_watch_focus_lines(watch_state)[:2])
    outputs = list_saved_outputs(session.session_id, limit=1)
    if outputs:
        output_item = outputs[0]
        preview = load_saved_output_preview(
            session.session_id,
            str(output_item.get("name") or "").strip(),
            max_chars=SESSION_PREVIEW_OUTPUT_CHARS,
        )
        output_line = f"latest output: {str(output_item.get('name') or '').strip()}"
        if preview:
            excerpt = _single_line_excerpt(str(preview.get("preview") or ""), max_chars=90)
            if excerpt:
                output_line += f" — {excerpt}"
        lines.append(output_line)
    snapshot = build_collaboration_snapshot(session.session_id, limit=3)
    actors = list(snapshot.get("actors") or [])
    decisions = list(snapshot.get("recent_decisions") or [])
    if actors:
        actor_names = ", ".join(
            str(actor.get("name") or "operator").strip() for actor in actors[:2] if str(actor.get("name") or "").strip()
        )
        if actor_names:
            lines.append(f"collab: {actor_names}")
    if decisions:
        lines.append(f"decision: {_single_line_excerpt(_format_collaboration_entry(decisions[0]), max_chars=100)}")
    mood = _session_mood_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
    mood_cell = _session_mood_cell(mood)
    if mood_cell:
        lines.append(mood_cell)
    timeline = list(story.get("timeline") or [])
    if timeline:
        lead = timeline[0]
        lines.append(
            f"recap: {str(lead.get('label') or 'update')}: "
            f"{_single_line_excerpt(str(lead.get('summary') or ''), max_chars=88)}"
        )
    return lines[:6]


def _collect_operator_alerts() -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for session in list_sessions(limit=50):
        watch_state = load_watch_state(session.session_id) or {}
        snapshot = build_collaboration_snapshot(session.session_id, limit=5)
        operator = _session_operator_snapshot(session, watch_state=watch_state, collaboration_snapshot=snapshot)
        watch_status = str((watch_state or {}).get("status") or "").strip().lower()
        failures = int((watch_state or {}).get("failure_count") or 0)
        pending = len(
            [
                item
                for item in list((watch_state or {}).get("interventions") or [])
                if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "pending"
            ]
        )
        latest_handoff = str(operator.get("latest_handoff") or "").strip()
        readiness = str(operator.get("readiness_label") or "").strip().lower()
        if watch_status in {"retrying"} or failures > 0:
            alerts.append(
                {
                    "id": f"{session.session_id}:retry:{failures}:{watch_status}",
                    "session_id": session.session_id,
                    "title": session.title,
                    "severity": "warn",
                    "kind": "retry",
                    "message": f"automation retrying · failures {failures}",
                }
            )
        if pending:
            alerts.append(
                {
                    "id": f"{session.session_id}:pending:{pending}",
                    "session_id": session.session_id,
                    "title": session.title,
                    "severity": "info",
                    "kind": "pending",
                    "message": f"{pending} pending operator intervention{'s' if pending != 1 else ''}",
                }
            )
        if readiness == "handoff-ready" and not latest_handoff:
            alerts.append(
                {
                    "id": f"{session.session_id}:handoff-ready",
                    "session_id": session.session_id,
                    "title": session.title,
                    "severity": "info",
                    "kind": "handoff",
                    "message": "ready to hand off · create a snapshot",
                }
            )
        if _session_is_stale(session) and watch_status in {"running", "active"}:
            alerts.append(
                {
                    "id": f"{session.session_id}:stale-watch",
                    "session_id": session.session_id,
                    "title": session.title,
                    "severity": "warn",
                    "kind": "stale",
                    "message": "watch looks stale while still active",
                }
            )
    severity_order = {"warn": 0, "retry": 0, "error": 0, "info": 1, "idle": 2}
    alerts.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity") or ""), 9),
            str(item.get("title") or ""),
            str(item.get("message") or ""),
        )
    )
    return alerts


def _last_trace_snapshot(session_id: str) -> dict[str, Any] | None:
    last_ev = get_last_decision_event(session_id)
    if last_ev is None:
        return None
    kind = str(last_ev.get("kind") or "").strip()
    meta = last_ev.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    content = str(last_ev.get("content") or "").strip()
    ts = str(last_ev.get("timestamp") or last_ev.get("at") or last_ev.get("created_at") or "").strip()

    slash_cmd = meta.get("slash_command") or ""
    rationale = meta.get("rationale") or content[:200] or "(no rationale recorded)"
    target_text = meta.get("target_text") or ""
    args_text = meta.get("args_text") or ""

    raw_conf = meta.get("confidence")
    try:
        confidence = float(raw_conf) if raw_conf is not None else None
    except (ValueError, TypeError):
        confidence = None

    if confidence is not None and confidence >= 0.80:
        conf_label = f"{confidence:.2f} (HIGH)"
        conf_color = "green"
        border_style = "green"
    elif confidence is not None and confidence >= 0.50:
        conf_label = f"{confidence:.2f} (MEDIUM)"
        conf_color = "yellow"
        border_style = "yellow"
    elif confidence is not None:
        conf_label = f"{confidence:.2f} (LOW)"
        conf_color = "red"
        border_style = "red"
    else:
        conf_label = "(unknown)"
        conf_color = "dim"
        border_style = "dim"

    ratings = _PREFS.get("ratings", [])
    latest_rating = ratings[-1] if ratings else None
    latest_rating_label = ""
    if isinstance(latest_rating, dict):
        latest_rating_label = (
            f"{latest_rating.get('score', latest_rating.get('rating', '?'))}/5 ({latest_rating.get('label', 'rated')})"
        )

    return {
        "kind": kind,
        "meta": meta,
        "content": content,
        "ts": ts,
        "slash_cmd": slash_cmd,
        "rationale": rationale,
        "target_text": target_text,
        "args_text": args_text,
        "conf_label": conf_label,
        "conf_color": conf_color,
        "border_style": border_style,
        "what_happened": f"{kind}" + (f" → /{slash_cmd}" if slash_cmd else (f" — {content[:60]}" if content else "")),
        "latest_rating": latest_rating_label,
        "rating_count": len(ratings),
    }
