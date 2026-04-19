"""Unit tests for openclaw_cli_session_cmds helpers."""
from __future__ import annotations

import pytest

from openclaw_cli_session_cmds import (
    _build_event_label,
    _build_handoff_check_lines,
    _build_plan_focus_lines,
    _build_workspace_capsule_plain_lines,
    _format_elapsed_compact,
    _highlight_ansi,
    _highlight_rich,
)

# ---------------------------------------------------------------------------
# _format_elapsed_compact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0.0s"),
    (0.5, "0.5s"),
    (45, "45s"),
    (5, "5.0s"),
    (90, "1m 30s"),
    (120, "2m"),
    (3600, "1h"),
    (3660, "1h 1m"),
    ("bad", "0s"),
    (None, "0s"),
], ids=["zero", "sub_second", "seconds", "short_seconds", "minutes", "full_minutes", "hours", "hours_and_minutes", "invalid", "none"])
def test_elapsed_compact(seconds, expected):
    assert _format_elapsed_compact(seconds) == expected


# ---------------------------------------------------------------------------
# _build_event_label
# ---------------------------------------------------------------------------

def test_build_event_label_uses_summary():
    ev = {"metadata": {"summary": "Did something"}, "content": "raw", "kind": ""}
    assert _build_event_label(ev).startswith("Did something")


def test_build_event_label_falls_back_to_content():
    ev = {"metadata": {}, "content": "raw content", "kind": ""}
    assert "raw content" in _build_event_label(ev)


def test_build_event_label_timing_bits():
    ev = {
        "metadata": {"summary": "S", "elapsed_seconds": 2.5},
        "content": "",
        "kind": "",
    }
    label = _build_event_label(ev)
    assert "2.5s" in label


def test_build_event_label_checkpoint_suffix():
    ev = {"metadata": {}, "content": "chk", "kind": "checkpoint"}
    assert "milestone" in _build_event_label(ev)


def test_build_event_label_error_suffix():
    ev = {"metadata": {}, "content": "err", "kind": "error"}
    assert "recovery needed" in _build_event_label(ev)


# ---------------------------------------------------------------------------
# _highlight_ansi
# ---------------------------------------------------------------------------

def test_highlight_ansi_found():
    result = _highlight_ansi("Hello World", "world", "world", "[HL]", "[/HL]")
    assert "[HL]" in result
    assert "World" in result


def test_highlight_ansi_not_found():
    result = _highlight_ansi("Hello", "xyz", "xyz", "[HL]", "[/HL]")
    assert result == "Hello"


def test_highlight_ansi_case_insensitive():
    result = _highlight_ansi("PYTHON is great", "python", "python", ">>", "<<")
    assert ">>" in result


# ---------------------------------------------------------------------------
# _highlight_rich
# ---------------------------------------------------------------------------

def test_highlight_rich_wraps_match():
    result = _highlight_rich("git status", "git")
    assert "[bold yellow]git[/]" in result


def test_highlight_rich_case_insensitive():
    result = _highlight_rich("Git status", "git")
    assert "bold yellow" in result


# ---------------------------------------------------------------------------
# _build_plan_focus_lines
# ---------------------------------------------------------------------------

def test_build_plan_focus_lines_basic():
    lines = ["- [ ] task 1", "  detail here", "- [ ] task 2"]
    unchecked = [(0, "- [ ] task 1"), (2, "- [ ] task 2")]
    result = _build_plan_focus_lines(lines, "plan-1", 0, unchecked, summary="My Goal")
    assert "Goal: My Goal" in result
    assert "▶ Current:" in result
    assert "→ Next:" in result


def test_build_plan_focus_lines_no_summary():
    lines = ["- [ ] only task"]
    unchecked = [(0, "- [ ] only task")]
    result = _build_plan_focus_lines(lines, "plan-1", 1, unchecked, summary=None)
    assert any("Done: 1" in line for line in result)
    assert any("Remaining: 1" in line for line in result)


def test_build_plan_focus_lines_single_task():
    lines = ["- [ ] single"]
    unchecked = [(0, "- [ ] single")]
    result = _build_plan_focus_lines(lines, "plan-1", 0, unchecked, summary=None)
    assert "→ Next:" not in result


# ---------------------------------------------------------------------------
# _build_handoff_check_lines
# ---------------------------------------------------------------------------

def test_build_handoff_check_lines_basic():
    check = {
        "readiness": "ready",
        "checks": [("docs", True, "all good"), ("tests", False, "missing")],
        "open_risks": [],
        "open_incidents": [],
    }
    lines = _build_handoff_check_lines(check)
    assert any("ready" in line for line in lines)
    assert any("OK" in line for line in lines)
    assert any("WARN" in line for line in lines)


def test_build_handoff_check_lines_open_risks():
    check = {
        "readiness": "needs-attention",
        "checks": [],
        "open_risks": [{"risk_level": "high", "content": "Something risky"}],
        "open_incidents": [],
    }
    lines = _build_handoff_check_lines(check)
    assert any("Something risky" in line for line in lines)


def test_build_handoff_check_lines_open_incidents():
    check = {
        "readiness": "needs-attention",
        "checks": [],
        "open_risks": [],
        "open_incidents": [{"content": "Ongoing incident"}],
    }
    lines = _build_handoff_check_lines(check)
    assert any("Ongoing incident" in line for line in lines)


# ---------------------------------------------------------------------------
# _build_workspace_capsule_plain_lines
# ---------------------------------------------------------------------------

def test_workspace_capsule_plain_lines_minimal():
    capsule = {"cwd": "/home/user", "tracked_files": [], "bookmarks": [], "recent_outputs": []}
    lines = _build_workspace_capsule_plain_lines(capsule)
    assert any("cwd:" in line for line in lines)
    assert any("files:" in line for line in lines)


def test_workspace_capsule_plain_lines_watch_status():
    capsule = {
        "cwd": "/home", "tracked_files": [], "bookmarks": [], "recent_outputs": [],
        "watch_status": "active",
    }
    lines = _build_workspace_capsule_plain_lines(capsule)
    assert any("watch: active" in line for line in lines)


def test_workspace_capsule_plain_lines_recent_outputs():
    capsule = {
        "cwd": "/", "tracked_files": [], "bookmarks": [],
        "recent_outputs": [{"name": "out1"}, {"name": "out2"}],
    }
    lines = _build_workspace_capsule_plain_lines(capsule)
    assert any("out1" in line for line in lines)


def test_workspace_capsule_plain_lines_plan_task():
    capsule = {
        "cwd": "/", "tracked_files": [], "bookmarks": [], "recent_outputs": [],
        "plan_id": "plan-42", "task_id": "task-7",
    }
    lines = _build_workspace_capsule_plain_lines(capsule)
    assert any("plan-42" in line for line in lines)
    assert any("task-7" in line for line in lines)
