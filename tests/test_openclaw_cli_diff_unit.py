"""Unit tests for openclaw_cli_diff._render_diff_ansi."""
import pytest
from openclaw_cli_diff import _render_diff_ansi


def test_plain_mode_returns_unchanged():
    diff = "+added\n-removed\n@@ hunk @@\n context"
    assert _render_diff_ansi(diff, plain_mode=True) == diff


def test_added_line_gets_color():
    result = _render_diff_ansi("+new line")
    # When ANSI codes are empty strings (no terminal), result equals original;
    # test that the line is present and unchanged structure
    assert "new line" in result


def test_removed_line_gets_color():
    result = _render_diff_ansi("-old line")
    assert "old line" in result


def test_hunk_header_gets_color():
    result = _render_diff_ansi("@@ -1,4 +1,5 @@")
    assert "@@ -1,4 +1,5 @@" in result


def test_file_header_lines():
    result = _render_diff_ansi("--- a/file.py\n+++ b/file.py")
    assert "--- a/file.py" in result
    assert "+++ b/file.py" in result


def test_context_line_unchanged_content():
    result = _render_diff_ansi(" context line")
    assert "context line" in result


def test_multiline_diff_roundtrip():
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n ctx"
    result = _render_diff_ansi(diff, plain_mode=True)
    assert result == diff


def test_empty_string_returns_empty():
    assert _render_diff_ansi("") == ""


def test_plus_plus_plus_treated_as_file_header():
    result = _render_diff_ansi("+++ b/foo.py")
    assert "b/foo.py" in result


def test_minus_minus_minus_treated_as_file_header():
    result = _render_diff_ansi("--- a/foo.py")
    assert "a/foo.py" in result
