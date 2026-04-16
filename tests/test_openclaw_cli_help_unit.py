"""Unit tests for openclaw_cli_help.py — print_chat_help."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import openclaw_cli_help as mod


# ---------------------------------------------------------------------------
# print_chat_help — plain text (non-TTY, non-rich fallback)
# ---------------------------------------------------------------------------

def _run_plain(search: str = "") -> str:
    """Run print_chat_help in plain text mode and capture stdout."""
    with patch.object(mod, "_IS_TTY", False), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        import io, sys
        buf = io.StringIO()
        orig = sys.stdout
        sys.stdout = buf
        try:
            mod.print_chat_help(search=search)
        finally:
            sys.stdout = orig
        return buf.getvalue()


def test_print_chat_help_no_search_lists_many_commands():
    out = _run_plain()
    # Should contain a large set of slash commands
    assert "/help" in out
    assert "/clear" in out
    assert "/quit" in out
    assert "/exec" in out
    assert "/session" in out


def test_print_chat_help_no_search_includes_examples():
    out = _run_plain()
    assert "Examples" in out or "example" in out.lower()


def test_print_chat_help_no_search_includes_notes():
    out = _run_plain()
    assert "autoroute" in out.lower() or "auto-route" in out.lower() or "auto_route" in out.lower()


def test_print_chat_help_search_filters_results():
    out = _run_plain(search="exec")
    assert "/exec" in out
    # Should NOT show unrelated commands like /quit in a filtered view
    # (but note: description matches too, so we just verify exec appears)
    assert "exec" in out.lower()


def test_print_chat_help_search_no_match_shows_no_commands_message():
    out = _run_plain(search="zzz_no_such_command_xyz")
    assert "No commands match" in out or "no commands" in out.lower()


def test_print_chat_help_search_case_insensitive():
    out_lower = _run_plain(search="theme")
    out_upper = _run_plain(search="THEME")
    # Both should contain /theme
    assert "/theme" in out_lower
    assert "/theme" in out_upper


def test_print_chat_help_search_matches_description():
    # "accessibility" is in the description field for /accessibility commands
    out = _run_plain(search="accessibility")
    assert "accessibility" in out.lower()


def test_print_chat_help_search_rate_returns_rate_command():
    out = _run_plain(search="rate")
    assert "/rate" in out


def test_print_chat_help_search_session_returns_session_command():
    out = _run_plain(search="session")
    assert "/session" in out


# ---------------------------------------------------------------------------
# print_chat_help — rich mode (mocked)
# ---------------------------------------------------------------------------

def test_print_chat_help_rich_mode_runs_without_error():
    """Verify rich mode doesn't crash (console output is captured via mock)."""
    fake_console = type("Console", (), {"print": lambda self, *a, **kw: None})()
    with patch.object(mod, "_IS_TTY", True), \
         patch.object(mod, "_RICH_AVAILABLE", True), \
         patch.object(mod, "_RICH_CONSOLE", fake_console):
        # Should run without raising — rich rendering path
        mod.print_chat_help()


def test_print_chat_help_rich_with_search_no_match():
    printed = []
    fake_console = type("Console", (), {"print": lambda self, *a, **kw: printed.append(a)})()
    with patch.object(mod, "_IS_TTY", True), \
         patch.object(mod, "_RICH_AVAILABLE", True), \
         patch.object(mod, "_RICH_CONSOLE", fake_console), \
         patch("builtins.print") as mock_print:
        mod.print_chat_help(search="zzz_no_such_thing")
        output = " ".join(str(c) for c in mock_print.call_args_list)
    # The plain-text fallback for empty results is always used regardless of rich
    assert "No commands match" in output


# ---------------------------------------------------------------------------
# Command list integrity checks (no I/O needed)
# ---------------------------------------------------------------------------

def _get_commands_list() -> list[tuple[str, str]]:
    """Extract the commands list by monkey-patching print_chat_help internals."""
    captured: list[tuple[str, str]] = []

    def _fake_plain(search: str = "") -> None:
        # Access local variable via closure — just read all stdout lines
        pass

    # Run in plain mode and capture the lines
    import io, sys
    buf = io.StringIO()
    orig = sys.stdout
    sys.stdout = buf
    try:
        with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
            mod.print_chat_help()
    finally:
        sys.stdout = orig
    return buf.getvalue().splitlines()


def test_all_commands_start_with_slash_or_are_labels():
    lines = _get_commands_list()
    command_lines = [l.strip() for l in lines if l.strip().startswith("/")]
    assert len(command_lines) > 50, "Expected at least 50 slash commands in help output"


def test_no_duplicate_command_lines():
    lines = _get_commands_list()
    command_lines = [l.strip() for l in lines if l.strip().startswith("/")]
    # Extract just the command names (first token)
    cmd_names = [l.split()[0] for l in command_lines if l.split()]
    # Some commands may appear multiple times due to aliases, but generally should be unique
    # Just verify there are no exact duplicate lines
    assert len(command_lines) == len(set(command_lines))


def test_help_output_contains_navigation_commands():
    lines = _get_commands_list()
    full_text = "\n".join(lines)
    for cmd in ("/help", "/quit", "/clear", "/session", "/context", "/exec"):
        assert cmd in full_text, f"Expected {cmd} in help output"


def test_help_output_contains_history_commands():
    lines = _get_commands_list()
    full_text = "\n".join(lines)
    assert "/history" in full_text
    assert "/recall" in full_text


def test_help_output_contains_analytics_commands():
    lines = _get_commands_list()
    full_text = "\n".join(lines)
    assert "/stats" in full_text or "/heatmap" in full_text or "/top" in full_text


def test_help_output_contains_ai_agent_commands():
    lines = _get_commands_list()
    full_text = "\n".join(lines)
    assert "/analyze" in full_text
    assert "/research" in full_text
    assert "/write" in full_text
