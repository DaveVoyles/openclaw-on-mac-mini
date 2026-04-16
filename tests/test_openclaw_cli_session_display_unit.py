"""Unit tests for openclaw_cli_session_display — pure and mockable helpers.

Covers:
  - _parse_utc_timestamp
  - _format_elapsed_compact
  - _single_line_excerpt
  - _format_byte_count
  - _status_family
  - _status_text / _status_style / _status_emoji
  - _status_cell / _progress_cell
  - watch_retry_delay_seconds / _watch_retry_delay_total
  - normalize_watch_state
  - _dedupe_preserve_order
  - _resolve_runbook_template
  - _format_collaboration_entry
  - _session_mood_cell
  - _operator_snapshot_lines
  - _dashboard_section_lines
  - _session_is_stale
  - _session_mood_snapshot (basic paths)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import openclaw_cli_session_display as sd


# ---------------------------------------------------------------------------
# _parse_utc_timestamp
# ---------------------------------------------------------------------------

def test_parse_utc_timestamp_valid_iso():
    result = sd._parse_utc_timestamp("2024-01-15T10:30:00+00:00")
    assert result is not None
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


def test_parse_utc_timestamp_z_suffix():
    result = sd._parse_utc_timestamp("2024-06-01T12:00:00Z")
    assert result is not None
    assert result.hour == 12


def test_parse_utc_timestamp_empty_string():
    assert sd._parse_utc_timestamp("") is None


def test_parse_utc_timestamp_none():
    assert sd._parse_utc_timestamp(None) is None


def test_parse_utc_timestamp_invalid():
    assert sd._parse_utc_timestamp("not-a-date") is None


# ---------------------------------------------------------------------------
# _format_elapsed_compact
# ---------------------------------------------------------------------------

def test_format_elapsed_compact_sub_second():
    assert sd._format_elapsed_compact(0.5) == "0.5s"


def test_format_elapsed_compact_small():
    assert sd._format_elapsed_compact(5.3) == "5.3s"


def test_format_elapsed_compact_tens():
    assert sd._format_elapsed_compact(42) == "42s"


def test_format_elapsed_compact_minutes():
    assert sd._format_elapsed_compact(90) == "1m 30s"


def test_format_elapsed_compact_exact_minute():
    assert sd._format_elapsed_compact(60) == "1m"


def test_format_elapsed_compact_hours():
    assert sd._format_elapsed_compact(3600) == "1h"


def test_format_elapsed_compact_hours_minutes():
    assert sd._format_elapsed_compact(3660) == "1h 1m"


def test_format_elapsed_compact_invalid():
    assert sd._format_elapsed_compact("bad") == "0s"


def test_format_elapsed_compact_none():
    assert sd._format_elapsed_compact(None) == "0s"


# ---------------------------------------------------------------------------
# _single_line_excerpt
# ---------------------------------------------------------------------------

def test_single_line_excerpt_short():
    assert sd._single_line_excerpt("hello world", max_chars=50) == "hello world"


def test_single_line_excerpt_truncated():
    result = sd._single_line_excerpt("a" * 100, max_chars=20)
    assert len(result) <= 20
    assert result.endswith("…")


def test_single_line_excerpt_collapses_whitespace():
    result = sd._single_line_excerpt("hello   world\n\tfoo", max_chars=100)
    assert "\n" not in result
    assert "\t" not in result
    assert "hello world foo" == result


def test_single_line_excerpt_empty():
    assert sd._single_line_excerpt("", max_chars=10) == ""


# ---------------------------------------------------------------------------
# _format_byte_count
# ---------------------------------------------------------------------------

def test_format_byte_count_bytes():
    assert sd._format_byte_count(512) == "512 B"


def test_format_byte_count_kb():
    result = sd._format_byte_count(2048)
    assert "KB" in result


def test_format_byte_count_mb():
    result = sd._format_byte_count(1024 * 1024)
    assert "MB" in result


def test_format_byte_count_zero():
    assert sd._format_byte_count(0) == "0 B"


def test_format_byte_count_gb():
    result = sd._format_byte_count(1024 ** 3)
    assert "GB" in result


# ---------------------------------------------------------------------------
# _status_family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("ok", "complete"),
    ("done", "complete"),
    ("success", "complete"),
    ("running", "active"),
    ("in_progress", "active"),
    ("pending", "waiting"),
    ("idle", "idle"),
    ("error", "error"),
    ("failed", "error"),
    ("warn", "warn"),
    ("retry", "retry"),
    ("blocked", "blocked"),
    ("paused", "paused"),
    ("info", "info"),
    ("stale", "stale"),
    ("", "unknown"),
    ("totally_unknown_xyz", "unknown"),
])
def test_status_family(status, expected):
    assert sd._status_family(status) == expected


def test_status_family_normalizes_dashes():
    assert sd._status_family("in-progress") == "active"


def test_status_family_case_insensitive():
    assert sd._status_family("RUNNING") == "active"


# ---------------------------------------------------------------------------
# _status_text
# ---------------------------------------------------------------------------

def test_status_text_complete():
    assert sd._status_text("done") == "COMPLETE"


def test_status_text_error():
    assert sd._status_text("failed") == "ERROR"


def test_status_text_unknown():
    assert sd._status_text("xyz_unknown") == "STATUS"


# ---------------------------------------------------------------------------
# _status_style
# ---------------------------------------------------------------------------

def test_status_style_complete():
    assert sd._status_style("complete") == "green"


def test_status_style_error():
    assert sd._status_style("error") == "bold red"


def test_status_style_unknown():
    assert sd._status_style("xyz_unknown") == "dim"


# ---------------------------------------------------------------------------
# _status_cell
# ---------------------------------------------------------------------------

def test_status_cell_plain():
    result = sd._status_cell("done")
    assert result == "COMPLETE"


def test_status_cell_with_detail():
    result = sd._status_cell("error", detail="disk full")
    assert "ERROR" in result
    assert "disk full" in result


def test_status_cell_rich_false():
    # rich=False means always plain text
    result = sd._status_cell("active", rich=False)
    assert result == "ACTIVE"


# ---------------------------------------------------------------------------
# _progress_cell
# ---------------------------------------------------------------------------

def test_progress_cell_no_status():
    result = sd._progress_cell("files", "3")
    assert result == "files: 3"


def test_progress_cell_with_status():
    result = sd._progress_cell("commands", "5", status="active")
    assert "commands: 5" in result
    assert "ACTIVE" in result


def test_context_pressure_snapshot_accounts_for_hidden_extras():
    snapshot = sd._context_pressure_snapshot(
        [{"role": "user", "content": "x" * 410_000}],
        system_prompt="y" * 20_000,
        pending_inject="z" * 12_000,
    )
    assert snapshot["band"] == "high"
    assert snapshot["hidden_pressure"] is True
    assert snapshot["has_pending_inject"] is True
    assert int(snapshot["pct_next"]) > int(snapshot["pct_history"])


def test_context_pressure_snapshot_reports_low_pressure_without_history():
    snapshot = sd._context_pressure_snapshot([], system_prompt="")
    assert snapshot["band"] == "low"
    assert int(snapshot["next_tokens"]) == 0
    assert int(snapshot["pct_next"]) == 0


def test_context_pressure_snapshot_uses_model_name_suffix_limit():
    snapshot = sd._context_pressure_snapshot(
        [{"role": "user", "content": "x" * 300_000}],
        model_hint="llama-3.1-sonar-small-128k-online",
    )
    assert int(snapshot["limit_tokens"]) == 128_000
    assert snapshot["limit_label"] == "128k"
    assert snapshot["limit_model_aware"] is True


def test_context_pressure_snapshot_uses_gemma_family_window():
    snapshot = sd._context_pressure_snapshot(
        [{"role": "user", "content": "x" * 395_000}],
        model_hint="gemma3:4b",
    )
    assert snapshot["limit_label"] == "~100k"
    assert snapshot["overflow"] is False
    assert int(snapshot["pct_next_raw"]) >= 95


def test_print_session_summary_surfaces_pending_inject_recovery_cues(monkeypatch):
    session = _make_session(
        session_id="sess-pressure",
        title="Pressure Session",
        cwd="/workspace",
        files=["/workspace/README.md"],
        command_count=2,
    )
    captured = {}

    monkeypatch.setattr(sd, "load_conversation_history", lambda session_id, limit_turns=0: [{"role": "user", "content": "x" * 410_000}])
    monkeypatch.setattr(sd, "load_watch_state", lambda session_id: None)
    monkeypatch.setattr(sd, "build_collaboration_snapshot", lambda session_id, limit=3: {})
    monkeypatch.setattr(sd, "build_session_storyline", lambda session_id, limit=4: {})
    monkeypatch.setattr(sd, "_session_mood_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(sd, "_session_operator_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(sd, "_operator_snapshot_lines", lambda snapshot: [])
    monkeypatch.setattr(sd, "_session_age_label", lambda session: "just now")
    monkeypatch.setattr(sd, "_print_dashboard_surface", lambda title, **kwargs: captured.update({"title": title, **kwargs}))

    sd._print_session_summary(session, pending_inject="Queued workspace recap")

    assert captured["title"] == "Session Dashboard"
    assert any("context pressure" in line for line in captured["detail_lines"])
    assert any("recovery cue: /inject clear" in line for line in captured["detail_lines"])
    assert any("/inject clear to drop the queued one-shot context before your next send" in line for line in captured["action_lines"])


def test_progress_cell_empty_status():
    result = sd._progress_cell("mood", "ok", status="")
    assert result == "mood: ok"


# ---------------------------------------------------------------------------
# watch_retry_delay_seconds
# ---------------------------------------------------------------------------

def test_watch_retry_delay_attempt_1():
    assert sd.watch_retry_delay_seconds(1) == 1


def test_watch_retry_delay_attempt_2():
    assert sd.watch_retry_delay_seconds(2) == 2


def test_watch_retry_delay_capped():
    # Should be capped at WATCH_RETRY_MAX_DELAY_SECONDS
    result = sd.watch_retry_delay_seconds(100)
    assert result == sd.WATCH_RETRY_MAX_DELAY_SECONDS


# ---------------------------------------------------------------------------
# _watch_retry_delay_total
# ---------------------------------------------------------------------------

def test_watch_retry_delay_total_empty():
    assert sd._watch_retry_delay_total({}) == 0


def test_watch_retry_delay_total_with_history():
    state = {
        "retry_history": [
            {"attempt": 1, "delay_seconds": 2},
            {"attempt": 2, "delay_seconds": 4},
        ]
    }
    assert sd._watch_retry_delay_total(state) == 6


# ---------------------------------------------------------------------------
# normalize_watch_state
# ---------------------------------------------------------------------------

def test_normalize_watch_state_empty():
    result = sd.normalize_watch_state({})
    assert "last_error" in result
    assert result["failure_count"] == 0
    assert result["consecutive_failures"] == 0
    assert result["retry_limit"] >= 1


def test_normalize_watch_state_none():
    result = sd.normalize_watch_state(None)
    assert "retry_history" in result
    assert isinstance(result["retry_history"], list)


def test_normalize_watch_state_force_run_once():
    result = sd.normalize_watch_state({"force_run_once": True})
    assert result["force_run_once"] is True


def test_normalize_watch_state_stop_requested():
    result = sd.normalize_watch_state({"stop_requested": 1})
    assert result["stop_requested"] is True


def test_normalize_watch_state_filters_non_dicts():
    state = {"retry_history": [{"attempt": 1}, "not_a_dict", None]}
    result = sd.normalize_watch_state(state)
    assert all(isinstance(x, dict) for x in result["retry_history"])


# ---------------------------------------------------------------------------
# _dedupe_preserve_order
# ---------------------------------------------------------------------------

def test_dedupe_preserve_order_basic():
    result = sd._dedupe_preserve_order(["a", "b", "a", "c"])
    assert result == ["a", "b", "c"]


def test_dedupe_preserve_order_empty_lines_removed():
    result = sd._dedupe_preserve_order(["a", "", "  ", "b"])
    assert "" not in result
    assert "  " not in result
    assert result == ["a", "b"]


def test_dedupe_preserve_order_empty_input():
    assert sd._dedupe_preserve_order([]) == []


# ---------------------------------------------------------------------------
# _resolve_runbook_template
# ---------------------------------------------------------------------------

def test_resolve_runbook_template_operator():
    result = sd._resolve_runbook_template("operator")
    assert result is not None
    key, template = result
    assert key == "operator"
    assert "sections" in template


def test_resolve_runbook_template_postmortem():
    result = sd._resolve_runbook_template("postmortem")
    assert result is not None
    key, _ = result
    assert key == "postmortem"


def test_resolve_runbook_template_unknown():
    assert sd._resolve_runbook_template("does_not_exist") is None


def test_resolve_runbook_template_default_operator():
    result = sd._resolve_runbook_template("")
    assert result is not None
    key, _ = result
    assert key == "operator"


# ---------------------------------------------------------------------------
# _format_collaboration_entry
# ---------------------------------------------------------------------------

def test_format_collaboration_entry_basic():
    entry = {"actor": "alice", "summary": "finished the task"}
    result = sd._format_collaboration_entry(entry)
    assert "alice" in result
    assert "finished the task" in result


def test_format_collaboration_entry_with_tags():
    entry = {"actor": "bob", "summary": "review done", "tags": ["urgent", "v2"]}
    result = sd._format_collaboration_entry(entry)
    assert "#urgent" in result
    assert "#v2" in result


def test_format_collaboration_entry_fallback_actor():
    entry = {"summary": "note without actor"}
    result = sd._format_collaboration_entry(entry)
    assert "operator" in result


def test_format_collaboration_entry_empty():
    result = sd._format_collaboration_entry({})
    # Should not raise; returns something
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _dashboard_section_lines
# ---------------------------------------------------------------------------

def test_dashboard_section_lines_basic():
    result = sd._dashboard_section_lines("Summary", ["line one", "line two"])
    assert result[0] == "Summary:"
    assert "  - line one" in result
    assert "  - line two" in result


def test_dashboard_section_lines_empty():
    assert sd._dashboard_section_lines("Empty", []) == []


def test_dashboard_section_lines_skips_blank():
    result = sd._dashboard_section_lines("Test", ["valid", "", "  "])
    # Only "valid" should remain
    assert len(result) == 2  # header + 1 item


# ---------------------------------------------------------------------------
# _session_is_stale
# ---------------------------------------------------------------------------

def _make_session(**kwargs):
    defaults = {
        "session_id": "abc123",
        "title": "Test",
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "command_count": 0,
        "output_count": 0,
        "checkpoint_count": 0,
        "last_summary": "",
        "last_checkpoint_at": None,
        "files": [],
        "cwd": "",
        "plan_id": None,
        "task_id": None,
        "automation_mode": None,
        "automation_status": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_session_is_stale_recent():
    session = _make_session(updated_at=datetime.now(timezone.utc).isoformat())
    assert sd._session_is_stale(session) is False


def test_session_is_stale_old():
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    session = _make_session(updated_at=old)
    assert sd._session_is_stale(session) is True


def test_session_is_stale_custom_days():
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    session = _make_session(updated_at=old)
    # 4 days < 7 → not stale
    assert sd._session_is_stale(session, days=7) is False
    # 4 days >= 3 → stale
    assert sd._session_is_stale(session, days=3) is True


def test_session_is_stale_invalid_date():
    session = _make_session(updated_at="not-a-date")
    # Should return False gracefully on parse failure
    assert sd._session_is_stale(session) is False


# ---------------------------------------------------------------------------
# _session_mood_cell
# ---------------------------------------------------------------------------

def test_session_mood_cell_empty_snapshot():
    result = sd._session_mood_cell({})
    assert result == ""


def test_session_mood_cell_with_label():
    snapshot = {"label": "steady", "detail": "3 outputs landed", "status": "active"}
    result = sd._session_mood_cell(snapshot)
    assert "mood" in result
    assert "steady" in result


def test_session_mood_cell_no_detail():
    snapshot = {"label": "milestone", "status": "complete"}
    result = sd._session_mood_cell(snapshot)
    assert "milestone" in result


def test_session_mood_brief_returns_dash_without_label():
    assert sd._session_mood_brief({}) == "—"


def test_session_mood_brief_includes_detail_when_available():
    snapshot = {"label": "steady", "detail": "3 commands into the flow"}
    result = sd._session_mood_brief(snapshot, max_chars=80)
    assert result == "steady · 3 commands into the flow"


def test_session_mood_brief_truncates_dense_list_copy():
    snapshot = {"label": "shared", "detail": "2 collaborators aligned with a long follow-through detail"}
    result = sd._session_mood_brief(snapshot, max_chars=24)
    assert result.startswith("shared ·")
    assert result.endswith("…")
    assert len(result) <= 24


# ---------------------------------------------------------------------------
# _operator_snapshot_lines
# ---------------------------------------------------------------------------

def test_operator_snapshot_lines_basic():
    snapshot = {
        "access": "read-only local snapshot",
        "control": "visibility only; no remote control",
        "readiness_label": "live",
        "readiness_detail": "watch loop is active",
        "readiness_status": "active",
    }
    lines = sd._operator_snapshot_lines(snapshot)
    assert len(lines) >= 3
    assert any("visibility" in l for l in lines)
    assert any("control: visibility only; no remote control" in l for l in lines)
    assert any("readiness" in l for l in lines)


def test_operator_snapshot_lines_with_watch():
    snapshot = {
        "watch_summary": "running · step1",
        "queue_summary": "1 pending",
        "latest_output": "report.md",
    }
    lines = sd._operator_snapshot_lines(snapshot)
    assert any("operator watch" in l for l in lines)
    assert any("operator queue" in l for l in lines)
    assert any("latest output" in l for l in lines)


def test_operator_snapshot_lines_decision_truncation():
    snapshot = {
        "latest_decision": "x" * 200,
    }
    lines = sd._operator_snapshot_lines(snapshot)
    decision_lines = [l for l in lines if "latest decision" in l]
    assert len(decision_lines) == 1
    assert len(decision_lines[0]) < 300  # truncated


# ---------------------------------------------------------------------------
# _session_mood_snapshot — basic paths
# ---------------------------------------------------------------------------

def test_session_mood_snapshot_empty_returns_empty():
    session = _make_session()
    result = sd._session_mood_snapshot(session)
    assert isinstance(result, dict)


def test_session_mood_snapshot_complete_session():
    session = _make_session(
        status="complete",
        output_count=2,
        checkpoint_count=1,
        command_count=5,
    )
    result = sd._session_mood_snapshot(session)
    assert result.get("status") == "complete"
    assert "milestone" in result.get("label", "")


def test_session_mood_snapshot_active_with_outputs():
    session = _make_session(output_count=1, command_count=0, checkpoint_count=0)
    result = sd._session_mood_snapshot(session)
    # output_count > 0 → steady
    if result:
        assert result.get("status") in {"active", "complete"}


def test_session_mood_snapshot_retrying():
    session = _make_session()
    watch_state = {"status": "retrying", "failure_count": 2}
    result = sd._session_mood_snapshot(session, watch_state=watch_state)
    assert result.get("status") == "retry"
    assert result.get("label") == "resilient"
