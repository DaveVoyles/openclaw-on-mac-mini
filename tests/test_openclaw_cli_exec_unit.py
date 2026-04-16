"""Unit tests for openclaw_cli_exec helpers."""
from __future__ import annotations

from openclaw_cli_exec import (
    _analyze_exec_error,
    _motion_pause,
    _progress_bar,
    _response_footer_lines,
    _separator_fill,
    _spinner_phase_label,
    _spinner_progress_snapshot,
)

# ---------------------------------------------------------------------------
# _separator_fill
# ---------------------------------------------------------------------------

def test_separator_fill_default_char():
    result = _separator_fill(10)
    assert len(result) == 10
    assert result == "─" * 10


def test_separator_fill_high_contrast():
    result = _separator_fill(5, high_contrast=True)
    assert result == "=" * 5


def test_separator_fill_plain_mode():
    result = _separator_fill(5, plain_mode=True)
    assert result == "=" * 5


def test_separator_fill_min_width_one():
    result = _separator_fill(0)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _motion_pause  (just verify it doesn't error and returns None)
# ---------------------------------------------------------------------------

def test_motion_pause_noop_when_not_tty():
    # plain_mode=True → should skip sleep entirely
    result = _motion_pause("banner", is_tty=False, plain_mode=False, reduced_motion=False)
    assert result is None


def test_motion_pause_noop_reduced_motion():
    result = _motion_pause("banner", is_tty=True, plain_mode=False, reduced_motion=True)
    assert result is None


def test_motion_pause_unknown_stage_no_error():
    _motion_pause("unknown_stage", is_tty=False)


# ---------------------------------------------------------------------------
# _spinner_progress_snapshot / _spinner_phase_label
# ---------------------------------------------------------------------------

def test_spinner_snapshot_warming_up():
    snap = _spinner_progress_snapshot(0.5)
    assert snap["phase"] == "warming up"
    assert snap["step_index"] == 1


def test_spinner_snapshot_working():
    snap = _spinner_progress_snapshot(2.0)
    assert snap["phase"] == "working"
    assert snap["step_index"] == 2


def test_spinner_snapshot_wrapping_up():
    snap = _spinner_progress_snapshot(10.0)
    assert snap["phase"] == "wrapping up"
    assert snap["step_index"] == 3


def test_spinner_phase_label_matches_snapshot():
    label = _spinner_phase_label(0.5)
    assert label == "warming up"


# ---------------------------------------------------------------------------
# _response_footer_lines
# ---------------------------------------------------------------------------

def test_footer_lines_with_elapsed():
    headline, detail = _response_footer_lines(elapsed=3.7)
    assert "3.7s" in headline
    assert "3.7s" in detail


def test_footer_lines_with_tokens():
    headline, detail = _response_footer_lines(elapsed=1.0, tokens=512)
    assert "512" in headline
    assert "512" in detail


def test_footer_lines_with_model():
    headline, detail = _response_footer_lines(elapsed=0.0, model="gpt-4")
    assert "gpt-4" in detail


def test_footer_lines_no_elapsed():
    headline, _ = _response_footer_lines(elapsed=0.0)
    assert "complete" in headline
    assert "0.0s" not in headline


def test_footer_lines_all_fields():
    headline, detail = _response_footer_lines(elapsed=2.0, tokens=100, model="claude", done_symbol="✅")
    assert "✅" in headline
    assert "claude" in detail


# ---------------------------------------------------------------------------
# _progress_bar
# ---------------------------------------------------------------------------

def test_progress_bar_zero_total():
    assert _progress_bar(5, 0) == ""


def test_progress_bar_full():
    bar = _progress_bar(30, 30)
    assert "100%" in bar


def test_progress_bar_with_label():
    bar = _progress_bar(15, 30, label="Loading")
    assert "Loading" in bar


def test_progress_bar_low_pct():
    # < 33%
    bar = _progress_bar(1, 100)
    assert bar != ""


def test_progress_bar_does_not_exceed_width():
    bar = _progress_bar(200, 100)  # current > total → clamped to 100%
    assert "100%" in bar


# ---------------------------------------------------------------------------
# _analyze_exec_error
# ---------------------------------------------------------------------------

def test_analyze_exec_error_returncode_0_empty():
    assert _analyze_exec_error("ls", "", 0) == []


def test_analyze_exec_error_permission_denied():
    hints = _analyze_exec_error("./script.sh", "permission denied", 1)
    assert any("sudo" in h for h in hints)


def test_analyze_exec_error_command_not_found():
    hints = _analyze_exec_error("foobar", "command not found", 127)
    assert any("foobar" in h for h in hints)


def test_analyze_exec_error_modulenotfounderror():
    hints = _analyze_exec_error("python app.py", "ModuleNotFoundError: No module named 'requests'", 1)
    assert any("pip install" in h for h in hints)


def test_analyze_exec_error_port_in_use():
    hints = _analyze_exec_error("python server.py", "address already in use", 1)
    assert any("port" in h.lower() for h in hints)


def test_analyze_exec_error_no_such_file():
    hints = _analyze_exec_error("cat missing.txt", "no such file or directory", 1)
    assert any("ls" in h or "mkdir" in h for h in hints)


def test_analyze_exec_error_generic_exit_1():
    hints = _analyze_exec_error("make", "", 1)
    assert any("Exit code 1" in h for h in hints)


def test_analyze_exec_error_exit_127():
    hints = _analyze_exec_error("notfound", "", 127)
    assert any("127" in h for h in hints)


def test_analyze_exec_error_capped_at_3():
    # Trigger many hint conditions at once
    stderr = "permission denied\ncommand not found\nno such file or directory\nport in use"
    hints = _analyze_exec_error("docker run", stderr, 1)
    assert len(hints) <= 3


def test_analyze_exec_error_docker_hints():
    hints = _analyze_exec_error("docker run myapp", "error response from daemon", 1)
    assert any("docker" in h.lower() for h in hints)
