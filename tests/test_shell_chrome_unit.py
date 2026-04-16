"""Unit tests for the always-on shell chrome bars.

Covers:
  - _print_shell_top_bar: Rich mock path, plain/non-TTY degradation, narrow layout
  - _print_shell_bottom_bar: hint rendering, plain/non-TTY degradation, narrow layout
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import openclaw_cli_ui_utils as ui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_top(capsys, **kwargs):
    """Call _print_shell_top_bar with test overrides and return captured output."""
    defaults = dict(
        session_id="20260416-abcdef",
        model_name="gemini-2.0-flash",
        autoroute_on=True,
        watch_active=False,
        _override_is_tty=True,
        _override_rich_available=False,
        _override_cols=100,
    )
    defaults.update(kwargs)
    ui._print_shell_top_bar(**defaults)
    return capsys.readouterr().out


def _run_bottom(capsys, **kwargs):
    """Call _print_shell_bottom_bar with test overrides and return captured output."""
    defaults = dict(
        mode="chat",
        _override_is_tty=True,
        _override_rich_available=False,
        _override_cols=100,
    )
    defaults.update(kwargs)
    ui._print_shell_bottom_bar(**defaults)
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# _print_shell_top_bar
# ---------------------------------------------------------------------------


def test_top_bar_shows_session_and_model(capsys):
    out = _run_top(capsys)
    assert "session:" in out
    assert "20260416" in out
    assert "gemini-2.0-flash" in out


def test_top_bar_shows_autoroute_on(capsys):
    out = _run_top(capsys, autoroute_on=True)
    assert "autoroute: on" in out


def test_top_bar_shows_autoroute_off(capsys):
    out = _run_top(capsys, autoroute_on=False)
    assert "autoroute: off" in out


def test_top_bar_shows_watch_when_active(capsys):
    out = _run_top(capsys, watch_active=True)
    assert "watch" in out


def test_top_bar_omits_watch_when_inactive(capsys):
    out = _run_top(capsys, watch_active=False)
    assert "watch" not in out


def test_top_bar_non_tty_no_output(capsys):
    """Non-TTY + non-plain mode should produce no output."""
    with patch.dict(ui._PREFS, {}, clear=True):
        ui._print_shell_top_bar(
            session_id="abc",
            model_name="gpt-4",
            autoroute_on=True,
            _override_is_tty=False,
            _override_rich_available=False,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert out == ""


def test_top_bar_plain_mode_uses_text_separator(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_shell_top_bar(
            session_id="sess-xyz",
            model_name="gpt-4",
            autoroute_on=True,
            _override_is_tty=True,
            _override_rich_available=False,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert "---" in out
    assert "session:" in out


def test_top_bar_narrow_omits_model(capsys):
    """Narrow (<60 cols) should collapse and drop the model name."""
    out = _run_top(capsys, _override_cols=40)
    assert "gemini-2.0-flash" not in out


def test_top_bar_narrow_keeps_session_and_autoroute(capsys):
    out = _run_top(capsys, _override_cols=40)
    assert "session:" in out
    assert "ar:" in out


def test_top_bar_rich_path(capsys):
    """When Rich is available, output should contain the ╸ accent."""
    mock_console = MagicMock()
    with patch.object(ui, "_RICH_CONSOLE", mock_console):
        ui._print_shell_top_bar(
            session_id="20260416",
            model_name="gemini-2.0-flash",
            autoroute_on=True,
            _override_is_tty=True,
            _override_rich_available=True,
            _override_cols=100,
        )
    # Rich console.print should have been called with something containing ╸
    assert mock_console.print.called
    call_arg = str(mock_console.print.call_args)
    assert "╸" in call_arg


def test_top_bar_no_session_id(capsys):
    """Works without a session_id — model and autoroute still appear."""
    out = _run_top(capsys, session_id="")
    assert "gemini-2.0-flash" in out
    assert "autoroute:" in out


# ---------------------------------------------------------------------------
# _print_shell_bottom_bar
# ---------------------------------------------------------------------------


def test_bottom_bar_default_hints(capsys):
    out = _run_bottom(capsys)
    assert "/help" in out
    assert "/quit" in out


def test_bottom_bar_shows_mode(capsys):
    out = _run_bottom(capsys, mode="research")
    assert "mode: research" in out


def test_bottom_bar_custom_hints(capsys):
    out = _run_bottom(capsys, hints=["press Tab to autocomplete", "/bookmark to save"])
    assert "Tab" in out
    assert "bookmark" in out


def test_bottom_bar_non_tty_no_output(capsys):
    with patch.dict(ui._PREFS, {}, clear=True):
        ui._print_shell_bottom_bar(
            mode="chat",
            _override_is_tty=False,
            _override_rich_available=False,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert out == ""


def test_bottom_bar_plain_mode_uses_text_separator(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_shell_bottom_bar(
            mode="chat",
            _override_is_tty=True,
            _override_rich_available=False,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert "---" in out


def test_bottom_bar_narrow_minimal_hints(capsys):
    """Narrow terminal should still produce output with minimal hints."""
    out = _run_bottom(capsys, _override_cols=40)
    assert out  # something printed
    assert "/help" in out or "/quit" in out


def test_bottom_bar_rich_path(capsys):
    """When Rich is available, output should contain the ╸ accent."""
    mock_console = MagicMock()
    with patch.object(ui, "_RICH_CONSOLE", mock_console):
        ui._print_shell_bottom_bar(
            mode="chat",
            _override_is_tty=True,
            _override_rich_available=True,
            _override_cols=100,
        )
    assert mock_console.print.called
    call_arg = str(mock_console.print.call_args)
    assert "╸" in call_arg


def test_bottom_bar_tab_completes_hint_in_full_width(capsys):
    """Full-width terminal includes Tab-completes hint."""
    out = _run_bottom(capsys, _override_cols=120)
    assert "Tab" in out
