"""Unit tests for openclaw_cli_session_utils.py."""
from __future__ import annotations

from unittest.mock import patch

import openclaw_cli_session_utils as mod
from openclaw_cli_sessions import SessionSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**kwargs) -> SessionSummary:
    defaults = dict(
        session_id="sess-test-01",
        title="Test Session",
        cwd="/tmp/test",
        status="active",
        command_count=5,
        output_count=2,
    )
    defaults.update(kwargs)
    return SessionSummary(**defaults)


# ---------------------------------------------------------------------------
# summarize_session
# ---------------------------------------------------------------------------

@patch("openclaw_cli_session_utils.load_watch_state", return_value=None)
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils._watch_timing_summary", return_value={"active_phase": None, "active_phase_elapsed": None, "latest_duration": None, "retry_delay_total": 0})
def test_summarize_session_contains_key_fields(mock_timing, mock_snap, mock_watch):
    session = _make_session()
    result = mod.summarize_session(session)
    assert "sess-test-01" in result
    assert "Test Session" in result
    assert "status" in result
    assert "commands" in result


@patch("openclaw_cli_session_utils.load_watch_state", return_value=None)
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils._watch_timing_summary", return_value={"active_phase": None, "active_phase_elapsed": None, "latest_duration": None, "retry_delay_total": 0})
def test_summarize_session_freshness_label(mock_timing, mock_snap, mock_watch):
    session = _make_session()
    result = mod.summarize_session(session)
    assert "fresh" in result or "stale" in result


@patch("openclaw_cli_session_utils.load_watch_state", return_value=None)
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils._watch_timing_summary", return_value={"active_phase": None, "active_phase_elapsed": None, "latest_duration": None, "retry_delay_total": 0})
def test_summarize_session_custom_age_fn(mock_timing, mock_snap, mock_watch):
    session = _make_session()
    result = mod.summarize_session(session, _age_label_fn=lambda s: "CUSTOM-AGE")
    assert "CUSTOM-AGE" in result


@patch("openclaw_cli_session_utils.load_watch_state", return_value=None)
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils._watch_timing_summary", return_value={"active_phase": None, "active_phase_elapsed": None, "latest_duration": None, "retry_delay_total": 0})
def test_summarize_session_includes_plan_and_task(mock_timing, mock_snap, mock_watch):
    session = _make_session(plan_id="plan-42", task_id="task-99")
    result = mod.summarize_session(session)
    assert "plan-42" in result
    assert "task-99" in result


@patch("openclaw_cli_session_utils.load_watch_state", side_effect=Exception("db error"))
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils._watch_timing_summary", return_value={"active_phase": None, "active_phase_elapsed": None, "latest_duration": None, "retry_delay_total": 0})
def test_summarize_session_tolerates_watch_state_error(mock_timing, mock_snap, mock_watch):
    session = _make_session()
    result = mod.summarize_session(session)
    assert "sess-test-01" in result


# ---------------------------------------------------------------------------
# _session_preview_lines
# ---------------------------------------------------------------------------

@patch("openclaw_cli_session_utils.build_session_storyline", return_value={"headline": "Did stuff", "timeline": []})
@patch("openclaw_cli_session_utils.list_saved_outputs", return_value=[])
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
def test_session_preview_lines_returns_list(mock_snap, mock_outputs, mock_story):
    session = _make_session()
    lines = mod._session_preview_lines(session)
    assert isinstance(lines, list)


@patch("openclaw_cli_session_utils.build_session_storyline", return_value={"headline": "Big update", "timeline": []})
@patch("openclaw_cli_session_utils.list_saved_outputs", return_value=[])
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
def test_session_preview_lines_includes_headline(mock_snap, mock_outputs, mock_story):
    session = _make_session()
    lines = mod._session_preview_lines(session)
    assert any("Big update" in line for line in lines)


@patch("openclaw_cli_session_utils.build_session_storyline", return_value={"headline": "", "timeline": []})
@patch("openclaw_cli_session_utils.list_saved_outputs", return_value=[{"name": "report.md", "size_bytes": 1024}])
@patch("openclaw_cli_session_utils.load_saved_output_preview", return_value={"preview": "Some content here"})
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
def test_session_preview_lines_includes_output(mock_snap, mock_preview, mock_outputs, mock_story):
    session = _make_session()
    lines = mod._session_preview_lines(session)
    assert any("report.md" in line for line in lines)


@patch("openclaw_cli_session_utils.build_session_storyline", return_value={"headline": "", "timeline": []})
@patch("openclaw_cli_session_utils.list_saved_outputs", return_value=[])
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={
    "actors": [{"name": "alice", "event_count": 3}],
    "recent_decisions": [{"actor": "alice", "summary": "Use Redis"}],
    "recent_notes": [],
})
def test_session_preview_lines_includes_collab(mock_snap, mock_outputs, mock_story):
    session = _make_session()
    lines = mod._session_preview_lines(session)
    assert any("alice" in line for line in lines)


@patch("openclaw_cli_session_utils.build_session_storyline", return_value={"headline": "", "timeline": []})
@patch("openclaw_cli_session_utils.list_saved_outputs", return_value=[])
@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
def test_session_preview_lines_max_six(mock_snap, mock_outputs, mock_story):
    session = _make_session(last_summary="Last summary text")
    lines = mod._session_preview_lines(session)
    assert len(lines) <= 6


# ---------------------------------------------------------------------------
# _collect_operator_alerts
# ---------------------------------------------------------------------------

@patch("openclaw_cli_session_utils.list_sessions", return_value=[])
def test_collect_operator_alerts_empty_sessions(mock_list):
    alerts = mod._collect_operator_alerts()
    assert alerts == []


@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils.load_watch_state", return_value={"status": "retrying", "failure_count": 2, "interventions": []})
@patch("openclaw_cli_session_utils.list_sessions", return_value=[_make_session()])
def test_collect_operator_alerts_retry_alert(mock_list, mock_watch, mock_snap):
    alerts = mod._collect_operator_alerts()
    retry_alerts = [a for a in alerts if a["kind"] == "retry"]
    assert len(retry_alerts) >= 1
    assert retry_alerts[0]["severity"] == "warn"


@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils.load_watch_state", return_value={
    "status": "running",
    "failure_count": 0,
    "interventions": [{"status": "pending"}, {"status": "done"}],
})
@patch("openclaw_cli_session_utils.list_sessions", return_value=[_make_session()])
def test_collect_operator_alerts_pending_interventions(mock_list, mock_watch, mock_snap):
    alerts = mod._collect_operator_alerts()
    pending_alerts = [a for a in alerts if a["kind"] == "pending"]
    assert len(pending_alerts) >= 1


@patch("openclaw_cli_session_utils.build_collaboration_snapshot", return_value={"actors": [], "recent_decisions": [], "recent_notes": []})
@patch("openclaw_cli_session_utils.load_watch_state", return_value={"status": "idle", "failure_count": 0, "interventions": []})
@patch("openclaw_cli_session_utils.list_sessions", return_value=[_make_session()])
def test_collect_operator_alerts_no_issues(mock_list, mock_watch, mock_snap):
    alerts = mod._collect_operator_alerts()
    # Idle session with no failures → no high-severity alerts
    warn_alerts = [a for a in alerts if a["severity"] == "warn"]
    assert len(warn_alerts) == 0


# ---------------------------------------------------------------------------
# _last_trace_snapshot
# ---------------------------------------------------------------------------

@patch("openclaw_cli_session_utils.get_last_decision_event", return_value=None)
def test_last_trace_snapshot_none_when_no_event(mock_get):
    result = mod._last_trace_snapshot("sess-01")
    assert result is None


@patch("openclaw_cli_session_utils.get_last_decision_event", return_value={
    "kind": "slash",
    "metadata": {"slash_command": "analyze", "rationale": "User asked", "confidence": 0.90},
    "content": "route to analyze",
    "timestamp": "2024-01-01T00:00:00Z",
})
def test_last_trace_snapshot_high_confidence(mock_get):
    with patch.dict("openclaw_cli_session_utils._PREFS", {"ratings": []}):
        result = mod._last_trace_snapshot("sess-01")
    assert result is not None
    assert "HIGH" in result["conf_label"]
    assert result["border_style"] == "green"


@patch("openclaw_cli_session_utils.get_last_decision_event", return_value={
    "kind": "slash",
    "metadata": {"slash_command": "chat", "confidence": 0.40},
    "content": "low confidence route",
    "timestamp": "2024-01-02T00:00:00Z",
})
def test_last_trace_snapshot_low_confidence(mock_get):
    with patch.dict("openclaw_cli_session_utils._PREFS", {"ratings": []}):
        result = mod._last_trace_snapshot("sess-01")
    assert result is not None
    assert "LOW" in result["conf_label"]
    assert result["border_style"] == "red"


@patch("openclaw_cli_session_utils.get_last_decision_event", return_value={
    "kind": "auto",
    "metadata": {},
    "content": "some content",
    "timestamp": "2024-01-03T00:00:00Z",
})
def test_last_trace_snapshot_no_confidence_label(mock_get):
    with patch.dict("openclaw_cli_session_utils._PREFS", {"ratings": []}):
        result = mod._last_trace_snapshot("sess-01")
    assert result is not None
    assert result["conf_label"] == "(unknown)"
    assert result["border_style"] == "dim"


@patch("openclaw_cli_session_utils.get_last_decision_event", return_value={
    "kind": "slash",
    "metadata": {"slash_command": "exec", "confidence": 0.85},
    "content": "exec route",
    "timestamp": "2024-01-04T00:00:00Z",
})
def test_last_trace_snapshot_what_happened_includes_slash_cmd(mock_get):
    with patch.dict("openclaw_cli_session_utils._PREFS", {"ratings": []}):
        result = mod._last_trace_snapshot("sess-01")
    assert result is not None
    assert "/exec" in result["what_happened"]


@patch("openclaw_cli_session_utils.get_last_decision_event", return_value={
    "kind": "slash",
    "metadata": {"confidence": 0.75},
    "content": "mid route",
    "timestamp": "2024-01-05T00:00:00Z",
})
def test_last_trace_snapshot_medium_confidence(mock_get):
    with patch.dict("openclaw_cli_session_utils._PREFS", {"ratings": [{"score": 4, "label": "good"}]}):
        result = mod._last_trace_snapshot("sess-01")
    assert result is not None
    assert "MEDIUM" in result["conf_label"]
    assert result["latest_rating"] == "4/5 (good)"
    assert result["rating_count"] == 1
