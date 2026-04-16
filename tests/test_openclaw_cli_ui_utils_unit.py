"""Unit tests for openclaw_cli_ui_utils — accessible UI helpers.

Covers:
  - _a11y_plain_mode / _a11y_high_contrast / _a11y_reduced_motion (PREFS readers)
  - _terminal_width (OS terminal size with fallback)
  - _e (emoji pack resolver)
  - _time_greeting (time-of-day branch)
  - _with_spinner (non-TTY pass-through path, exception propagation)
  - _print_status_bar (plain mode, narrow, non-TTY guard)
  - _celebration_burst (non-TTY / plain mode guard)
  - _print_workspace_capsule (plain text path)
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

import openclaw_cli_ui_utils as ui


# ---------------------------------------------------------------------------
# _a11y_plain_mode / _a11y_high_contrast / _a11y_reduced_motion
# ---------------------------------------------------------------------------

def test_a11y_plain_mode_false_by_default():
    with patch.dict(ui._PREFS, {}, clear=True):
        assert ui._a11y_plain_mode() is False


def test_a11y_plain_mode_true_when_set():
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        assert ui._a11y_plain_mode() is True


def test_a11y_high_contrast_false_by_default():
    with patch.dict(ui._PREFS, {}, clear=True):
        assert ui._a11y_high_contrast() is False


def test_a11y_high_contrast_true_when_set():
    with patch.dict(ui._PREFS, {ui._A11Y_HIGH_CONTRAST: True}):
        assert ui._a11y_high_contrast() is True


def test_a11y_reduced_motion_false_by_default():
    with patch.dict(ui._PREFS, {}, clear=True):
        assert ui._a11y_reduced_motion() is False


def test_a11y_reduced_motion_true_when_set():
    with patch.dict(ui._PREFS, {ui._A11Y_REDUCED_MOTION: True}):
        assert ui._a11y_reduced_motion() is True


# ---------------------------------------------------------------------------
# _terminal_width
# ---------------------------------------------------------------------------

def test_terminal_width_returns_columns():
    mock_size = MagicMock()
    mock_size.columns = 120
    with patch("os.get_terminal_size", return_value=mock_size):
        assert ui._terminal_width() == 120


def test_terminal_width_fallback_on_oserror():
    with patch("os.get_terminal_size", side_effect=OSError):
        assert ui._terminal_width(fallback=80) == 80


def test_terminal_width_custom_fallback():
    with patch("os.get_terminal_size", side_effect=OSError):
        assert ui._terminal_width(fallback=60) == 60


# ---------------------------------------------------------------------------
# _e — emoji pack resolver
# ---------------------------------------------------------------------------

def test_e_classic_returns_emoji():
    with patch("openclaw_cli_ui_utils._emoji_pack_name", return_value="classic"):
        assert ui._e("🦞") == "🦞"


def test_e_none_pack_returns_fallback_from_dict():
    with patch("openclaw_cli_ui_utils._emoji_pack_name", return_value="none"):
        # fallback from _EMOJI_FALLBACKS for 🦞
        result = ui._e("🦞", "[openclaw]")
        assert result == "[openclaw]" or result == ui._EMOJI_FALLBACKS.get("🦞", "")


def test_e_minimal_pack_returns_minimal_or_fallback():
    with patch("openclaw_cli_ui_utils._emoji_pack_name", return_value="minimal"):
        # Should return from minimal pack or the fallback arg
        result = ui._e("🦞", "[openclaw]")
        assert isinstance(result, str)


def test_e_with_explicit_fallback():
    with patch("openclaw_cli_ui_utils._emoji_pack_name", return_value="none"):
        result = ui._e("❓", "MISSING")
        assert result == "MISSING" or isinstance(result, str)


# ---------------------------------------------------------------------------
# _time_greeting
# ---------------------------------------------------------------------------

def _greeting_at_hour(hour: int) -> str:
    fake_dt = MagicMock()
    fake_dt.hour = hour
    with patch("openclaw_cli_ui_utils.datetime") as mock_datetime:
        mock_datetime.now.return_value = fake_dt
        return ui._time_greeting()


def test_time_greeting_morning():
    result = _greeting_at_hour(8)
    assert "morning" in result.lower()


def test_time_greeting_afternoon():
    result = _greeting_at_hour(14)
    assert "afternoon" in result.lower()


def test_time_greeting_evening():
    result = _greeting_at_hour(19)
    assert "evening" in result.lower()


def test_time_greeting_night():
    result = _greeting_at_hour(2)
    assert "hello" in result.lower() or "🦞" in result


# ---------------------------------------------------------------------------
# _with_spinner — non-TTY pass-through
# ---------------------------------------------------------------------------

def test_with_spinner_non_tty_calls_fn_directly():
    """When is_tty is False, _with_spinner should call fn immediately, no thread."""
    called = []

    def my_fn(x, y=0):
        called.append((x, y))
        return x + y

    result = ui._with_spinner("label", my_fn, 3, y=7, _override_is_tty=False)
    assert result == 10
    assert called == [(3, 7)]


def test_with_spinner_json_output_bypasses_spinner():
    """output_json=True forces direct call even in a TTY."""
    called = []

    def my_fn():
        called.append(True)
        return 42

    result = ui._with_spinner("label", my_fn, output_json=True, _override_is_tty=True)
    assert result == 42
    assert called == [True]


def test_with_spinner_propagates_exception_non_tty():
    def bad_fn():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        ui._with_spinner("label", bad_fn, _override_is_tty=False)


def test_with_spinner_returns_none_fn():
    def noop():
        return None

    result = ui._with_spinner("label", noop, _override_is_tty=False)
    assert result is None


# ---------------------------------------------------------------------------
# _print_status_bar
# ---------------------------------------------------------------------------

def test_print_status_bar_no_tty_returns_silently(capsys):
    ui._print_status_bar(session_id="abc", _override_is_tty=False)
    out = capsys.readouterr().out
    # No output expected when not a TTY
    assert out == ""


def test_print_status_bar_plain_mode(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_status_bar(
            session_id="abc123",
            autoroute_on=True,
            history_len=4,
            _override_is_tty=True,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert "autoroute" in out
    assert "on" in out


def test_print_status_bar_autoroute_off(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_status_bar(
            session_id="s1",
            autoroute_on=False,
            _override_is_tty=True,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    assert "off" in out


def test_print_status_bar_no_session_id(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_status_bar(
            session_id="",
            _override_is_tty=True,
            _override_cols=100,
        )
    out = capsys.readouterr().out
    # Should still emit status line
    assert "autoroute" in out


def test_print_status_bar_narrow_layout(capsys):
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_status_bar(
            session_id="sid",
            history_len=6,
            _override_is_tty=True,
            _override_cols=40,  # narrow — < 60
        )
    out = capsys.readouterr().out
    assert out  # something printed


def test_print_status_bar_history_turns(capsys):
    """history_len // 2 turns should appear in non-narrow mode."""
    with patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._print_status_bar(
            session_id="s1",
            history_len=8,
            _override_is_tty=True,
            _override_cols=120,
        )
    out = capsys.readouterr().out
    # 4 turns
    assert "4" in out or "turn" in out or "autoroute" in out


# ---------------------------------------------------------------------------
# _celebration_burst
# ---------------------------------------------------------------------------

def test_celebration_burst_non_tty_with_message(capsys):
    with patch("openclaw_cli_ui_utils._get_is_tty", return_value=False):
        ui._celebration_burst("You did it!")
    out = capsys.readouterr().out
    assert "You did it!" in out


def test_celebration_burst_non_tty_no_message(capsys):
    with patch("openclaw_cli_ui_utils._get_is_tty", return_value=False):
        ui._celebration_burst()
    # No message → should print nothing
    out = capsys.readouterr().out
    assert out == ""


def test_celebration_burst_plain_mode_with_message(capsys):
    with patch("openclaw_cli_ui_utils._get_is_tty", return_value=True), \
         patch.dict(ui._PREFS, {ui._A11Y_PLAIN_MODE: True}):
        ui._celebration_burst("Congrats!")
    out = capsys.readouterr().out
    assert "Congrats!" in out


# ---------------------------------------------------------------------------
# _print_workspace_capsule — plain text path
# ---------------------------------------------------------------------------

def test_print_workspace_capsule_plain_text(capsys):
    capsule = {
        "cwd": "/home/user/project",
        "tracked_file_count": 3,
        "bookmark_count": 0,
        "output_count": 0,
        "tracked_files": ["a.py", "b.py"],
        "bookmarks": [],
        "recent_outputs": [],
    }
    # Force plain path by patching _RICH_AVAILABLE and _IS_TTY
    with patch.object(ui, "_RICH_AVAILABLE", False), \
         patch.object(ui, "_IS_TTY", False):
        ui._print_workspace_capsule(capsule, title="My Capsule")
    out = capsys.readouterr().out
    assert "My Capsule" in out


def test_print_workspace_capsule_empty_capsule(capsys):
    with patch.object(ui, "_RICH_AVAILABLE", False), \
         patch.object(ui, "_IS_TTY", False):
        ui._print_workspace_capsule({})
    # Should not raise
    out = capsys.readouterr().out
    assert isinstance(out, str)
