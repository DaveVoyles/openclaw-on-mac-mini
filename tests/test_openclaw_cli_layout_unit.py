"""Unit tests for openclaw_cli_layout.py — pure helper functions."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import openclaw_cli_layout as mod


# ---------------------------------------------------------------------------
# _status_family_layout
# ---------------------------------------------------------------------------

def test_status_family_complete():
    for s in ("ok", "healthy", "done", "completed", "success", "succeeded", "complete"):
        assert mod._status_family_layout(s) == "complete"


def test_status_family_active():
    for s in ("active", "running", "in_progress", "working", "processing", "streaming"):
        assert mod._status_family_layout(s) == "active"


def test_status_family_error():
    for s in ("error", "failed", "failure", "unhealthy"):
        assert mod._status_family_layout(s) == "error"


def test_status_family_waiting():
    for s in ("pending", "queued", "waiting", "scheduled"):
        assert mod._status_family_layout(s) == "waiting"


def test_status_family_unknown():
    assert mod._status_family_layout("some-weird-status") == "unknown"
    assert mod._status_family_layout("") == "unknown"


def test_status_family_normalizes_dashes_and_spaces():
    assert mod._status_family_layout("in-progress") == "active"
    assert mod._status_family_layout("in progress") == "active"


# ---------------------------------------------------------------------------
# _status_text_layout
# ---------------------------------------------------------------------------

def test_status_text_layout_known_families():
    assert mod._status_text_layout("ok") == "COMPLETE"
    assert mod._status_text_layout("running") == "ACTIVE"
    assert mod._status_text_layout("error") == "ERROR"
    assert mod._status_text_layout("pending") == "WAITING"
    assert mod._status_text_layout("idle") == "IDLE"


def test_status_text_layout_unknown_falls_back_to_status():
    assert mod._status_text_layout("totally-unknown") == "STATUS"


# ---------------------------------------------------------------------------
# _status_cell_layout
# ---------------------------------------------------------------------------

def test_status_cell_layout_no_detail():
    result = mod._status_cell_layout("ok")
    assert result == "COMPLETE"


def test_status_cell_layout_with_detail():
    result = mod._status_cell_layout("running", detail="step 2")
    assert result == "ACTIVE · step 2"


# ---------------------------------------------------------------------------
# _progress_cell_layout
# ---------------------------------------------------------------------------

def test_progress_cell_layout_no_status():
    result = mod._progress_cell_layout("files", "3")
    assert result == "files: 3"


def test_progress_cell_layout_with_status():
    result = mod._progress_cell_layout("files", "3", status="ok")
    assert "COMPLETE" in result
    assert "files: 3" in result


# ---------------------------------------------------------------------------
# _truncate_preview_layout
# ---------------------------------------------------------------------------

def test_truncate_preview_short_text_unchanged():
    text = "Hello world"
    assert mod._truncate_preview_layout(text, max_chars=100) == text


def test_truncate_preview_long_text_clipped():
    long_text = "A" * 300
    result = mod._truncate_preview_layout(long_text, max_chars=50)
    assert "[truncated]" in result
    assert len(result) < 300


def test_truncate_preview_empty_string():
    assert mod._truncate_preview_layout("", max_chars=50) == ""


def test_truncate_preview_none_like():
    assert mod._truncate_preview_layout(None, max_chars=50) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _single_line_excerpt_layout
# ---------------------------------------------------------------------------

def test_single_line_excerpt_fits():
    assert mod._single_line_excerpt_layout("Hello", max_chars=20) == "Hello"


def test_single_line_excerpt_truncates():
    long = "word " * 50
    result = mod._single_line_excerpt_layout(long, max_chars=30)
    assert result.endswith("…")
    assert len(result) <= 31  # max_chars + ellipsis char


def test_single_line_excerpt_collapses_whitespace():
    text = "Hello   world\n  extra"
    result = mod._single_line_excerpt_layout(text, max_chars=200)
    assert "\n" not in result
    assert "  " not in result


# ---------------------------------------------------------------------------
# _format_byte_count_layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size,expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1.0 KB"),
    (1024 * 1024, "1.0 MB"),
    (1536 * 1024, "1.5 MB"),
])
def test_format_byte_count_layout(size, expected):
    assert mod._format_byte_count_layout(size) == expected


def test_format_byte_count_layout_negative_clamps_to_zero():
    assert mod._format_byte_count_layout(-100) == "0 B"


# ---------------------------------------------------------------------------
# _format_elapsed_compact_layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0.0s"),
    (0.5, "0.5s"),
    (9, "9.0s"),
    (30, "30s"),
    (60, "1m"),
    (90, "1m 30s"),
    (3600, "1h"),
    (3660, "1h 1m"),
])
def test_format_elapsed_compact_layout(seconds, expected):
    assert mod._format_elapsed_compact_layout(seconds) == expected


def test_format_elapsed_compact_layout_invalid():
    assert mod._format_elapsed_compact_layout("bad") == "0s"
    assert mod._format_elapsed_compact_layout(None) == "0s"


# ---------------------------------------------------------------------------
# _format_collaboration_entry_layout
# ---------------------------------------------------------------------------

def test_format_collaboration_entry_basic():
    entry = {"actor": "alice", "summary": "Approved PR"}
    result = mod._format_collaboration_entry_layout(entry)
    assert result == "alice: Approved PR"


def test_format_collaboration_entry_with_tags():
    entry = {"actor": "bob", "summary": "Merged", "tags": ["deploy", "prod"]}
    result = mod._format_collaboration_entry_layout(entry)
    assert "#deploy" in result
    assert "#prod" in result


def test_format_collaboration_entry_empty_actor_defaults():
    entry = {"summary": "Some note"}
    result = mod._format_collaboration_entry_layout(entry)
    assert result.startswith("operator:")


# ---------------------------------------------------------------------------
# _effective_layout_mode
# ---------------------------------------------------------------------------

def test_effective_layout_mode_valid():
    for mode in ("compact", "normal", "verbose", "plain"):
        assert mod._effective_layout_mode({"layout": mode}) == mode


def test_effective_layout_mode_invalid_defaults_to_normal():
    assert mod._effective_layout_mode({"layout": "weird"}) == "normal"
    assert mod._effective_layout_mode({}) == "normal"


# ---------------------------------------------------------------------------
# _layout_preset_name
# ---------------------------------------------------------------------------

def test_layout_preset_name_known():
    for preset in ("focus", "watch-monitor", "handoff"):
        assert mod._layout_preset_name({"layout_preset": preset}) == preset


def test_layout_preset_name_unknown_returns_empty():
    assert mod._layout_preset_name({"layout_preset": "anything-else"}) == ""
    assert mod._layout_preset_name({}) == ""


# ---------------------------------------------------------------------------
# _layout_focus_name
# ---------------------------------------------------------------------------

def test_layout_focus_name_valid():
    assert mod._layout_focus_name({"layout_focus": "primary"}) == "primary"
    assert mod._layout_focus_name({"layout_focus": "supporting"}) == "supporting"


def test_layout_focus_name_invalid_defaults_to_primary():
    assert mod._layout_focus_name({"layout_focus": "other"}) == "primary"
    assert mod._layout_focus_name({}) == "primary"


# ---------------------------------------------------------------------------
# _layout_focus_transition_line
# ---------------------------------------------------------------------------

def test_layout_focus_transition_line_from_primary():
    assert (
        mod._layout_focus_transition_line("primary", "Session summary", "Artifact preview")
        == "Focus transition: /layout focus supporting -> Artifact preview"
    )


def test_layout_focus_transition_line_from_supporting():
    assert (
        mod._layout_focus_transition_line("supporting", "Watch monitor", "Recent artifacts")
        == "Focus transition: /layout focus primary -> Watch monitor"
    )


# ---------------------------------------------------------------------------
# _layout_preset_config
# ---------------------------------------------------------------------------

def test_layout_preset_config_focus():
    cfg = mod._layout_preset_config({}, "focus")
    assert cfg["label"] == "focus"
    assert "/session" in cfg["primary"]


def test_layout_preset_config_watch_monitor():
    cfg = mod._layout_preset_config({}, "watch-monitor")
    assert cfg["label"] == "watch-monitor"


def test_layout_preset_config_handoff():
    cfg = mod._layout_preset_config({}, "handoff")
    assert "collab" in cfg["primary"]


def test_layout_preset_config_unknown_returns_empty():
    assert mod._layout_preset_config({}, "not-a-preset") == {}


# ---------------------------------------------------------------------------
# _layout_pane_line_limit
# ---------------------------------------------------------------------------

def test_layout_pane_line_limit_modes():
    assert mod._layout_pane_line_limit({"layout": "compact"}) == 6
    assert mod._layout_pane_line_limit({"layout": "normal"}) == 9
    assert mod._layout_pane_line_limit({"layout": "verbose"}) == 14


# ---------------------------------------------------------------------------
# _layout_pane_block
# ---------------------------------------------------------------------------

def test_layout_pane_block_basic():
    lines = ["line one", "line two"]
    block = mod._layout_pane_block({"layout": "normal"}, "My Pane", lines, active=False)
    assert block[0].startswith("READY ·")
    assert "My Pane" in block[0]
    assert any("line one" in b for b in block)


def test_layout_pane_block_active():
    block = mod._layout_pane_block({"layout": "normal"}, "Title", ["a"], active=True)
    assert block[0].startswith("ACTIVE ·")


def test_layout_pane_block_clips_to_limit():
    lines = [f"line {i}" for i in range(20)]
    block = mod._layout_pane_block({"layout": "compact"}, "T", lines)
    content_lines = [b for b in block[1:] if not b.strip().startswith("…")]
    assert len(content_lines) <= 6


def test_layout_pane_block_shows_overflow_hint():
    lines = [f"line {i}" for i in range(20)]
    block = mod._layout_pane_block({"layout": "compact"}, "T", lines)
    assert any("more line" in b for b in block)


# ---------------------------------------------------------------------------
# _layout_column_lines
# ---------------------------------------------------------------------------

def test_layout_column_lines_produces_output():
    left = ["left line 1", "left line 2"]
    right = ["right line 1", "right line 2"]
    result = mod._layout_column_lines(left, right, width=120)
    assert len(result) >= 2
    assert all("│" in line for line in result)


def test_layout_column_lines_unequal_lengths():
    left = ["a", "b", "c"]
    right = ["x"]
    result = mod._layout_column_lines(left, right, width=80)
    assert len(result) >= 3


# ---------------------------------------------------------------------------
# _layout_preset_fallback
# ---------------------------------------------------------------------------

def test_layout_preset_fallback_no_preset():
    result = mod._layout_preset_fallback({}, width=200, is_tty=True)
    assert result == "single-pane"


def test_layout_preset_fallback_narrow_terminal():
    prefs = {"layout_preset": "focus"}
    result = mod._layout_preset_fallback(prefs, width=80, is_tty=True)
    assert result == "single-pane"


def test_layout_preset_fallback_medium_terminal():
    prefs = {"layout_preset": "focus"}
    result = mod._layout_preset_fallback(prefs, width=120, is_tty=True)
    assert result == "stacked"


def test_layout_preset_fallback_wide_terminal():
    prefs = {"layout_preset": "focus"}
    result = mod._layout_preset_fallback(prefs, width=160, is_tty=True)
    assert result == "multi-pane"


def test_layout_preset_fallback_not_tty():
    prefs = {"layout_preset": "focus"}
    result = mod._layout_preset_fallback(prefs, width=200, is_tty=False)
    assert result == "single-pane"


def test_layout_preset_fallback_plain_mode():
    prefs = {"layout_preset": "focus", "plain_mode": True}
    result = mod._layout_preset_fallback(prefs, width=200, is_tty=True)
    assert result == "single-pane"


# ---------------------------------------------------------------------------
# _terminal_width_layout
# ---------------------------------------------------------------------------

def test_terminal_width_layout_fallback():
    with patch("os.get_terminal_size", side_effect=OSError):
        assert mod._terminal_width_layout(fallback=80) == 80


def test_terminal_width_layout_from_os():
    fake = type("TS", (), {"columns": 132})()
    with patch("os.get_terminal_size", return_value=fake):
        assert mod._terminal_width_layout() == 132
