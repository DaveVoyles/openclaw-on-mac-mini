"""Unit tests for openclaw_cli_cmd_settings.py — settings and appearance handlers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
import openclaw_cli_cmd_settings as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_cli(**kwargs) -> MagicMock:
    """Create a minimal mock of openclaw_cli for settings tests."""
    m = MagicMock()
    m._get_is_tty = MagicMock(return_value=False)
    m._PREFS = kwargs.pop("_PREFS", {})
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._prefs_set = MagicMock()
    m._print_error = MagicMock()
    m._print_feedback = MagicMock()
    m._theme_ansi = MagicMock(return_value="")
    m._status_emoji = MagicMock(return_value="●")
    m._e = MagicMock(side_effect=lambda emoji, fallback: emoji)
    m._interactive_overlays_enabled = MagicMock(return_value=False)
    m._overlay_available = MagicMock(return_value=False)
    m._handle_simple_toggle_pref = MagicMock(return_value=_CMD_CONTINUE)
    m._EXTENDED_SCHEMES = {
        "default": {"primary": "", "label": "Default"},
        "ocean": {"primary": "", "label": "Ocean blue"},
    }
    m._a11y_reduced_motion = MagicMock(return_value=False)
    m._a11y_plain_mode = MagicMock(return_value=False)
    m._a11y_high_contrast = MagicMock(return_value=False)
    m._effective_layout_mode = MagicMock(return_value="normal")
    m._layout_preset_name = MagicMock(return_value="")
    m._layout_preset_fallback = MagicMock(return_value="single-pane")
    m._layout_preset_config = MagicMock(return_value={"label": "Focus", "primary": "chat", "supporting": "plan"})
    m._layout_focus_name = MagicMock(return_value="primary")
    m._print_layout_preset_workspace = MagicMock()
    m._apply_custom_keybind = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_theme
# ---------------------------------------------------------------------------

def test_cmd_theme_list_shows_current(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "default" in captured.out
    assert "Available themes" in captured.out


def test_cmd_theme_set_valid(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx("green"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_once_with("theme", "green")


def test_cmd_theme_set_invalid_prints_error(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx("nonexistent_theme_xyz"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "error" in captured.out.lower() or "Unknown theme" in captured.out


def test_cmd_theme_next_cycles_forward(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx("next"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called()


def test_cmd_theme_prev_cycles_backward(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx("prev"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called()


def test_cmd_theme_reset_sets_default(capsys):
    cli = _mock_cli(_PREFS={"theme": "green"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_theme(_ctx("reset"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("theme", "default")


# ---------------------------------------------------------------------------
# _cmd_emoji
# ---------------------------------------------------------------------------

def test_cmd_emoji_status_shows_state(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic", "emoji": True})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"):
        result = mod._cmd_emoji(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "on" in captured.out.lower() or "classic" in captured.out


def test_cmd_emoji_off_disables(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic", "emoji": True})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"), \
         patch("openclaw_cli_cmd_settings._save_prefs"):
        result = mod._cmd_emoji(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("emoji_pack", "ascii")
    captured = capsys.readouterr()
    assert "disabled" in captured.out.lower() or "ASCII" in captured.out


def test_cmd_emoji_on_enables(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic", "emoji": False})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"), \
         patch("openclaw_cli_cmd_settings._save_prefs"):
        result = mod._cmd_emoji(_ctx("on"))
    assert result == _CMD_CONTINUE
    assert cli._PREFS["emoji"] is True
    captured = capsys.readouterr()
    assert "enabled" in captured.out.lower()


def test_cmd_emoji_invalid_token_prints_error(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic"})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"):
        result = mod._cmd_emoji(_ctx("blarg"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "error" in captured.out.lower()


def test_cmd_emoji_pack_valid(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic", "emoji": True})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"):
        result = mod._cmd_emoji(_ctx("pack minimal"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "minimal" in captured.out.lower()


def test_cmd_emoji_pack_invalid(capsys):
    cli = _mock_cli(_PREFS={"emoji_pack": "classic"})
    with patch.object(mod, "_m", return_value=cli), \
         patch("openclaw_cli_cmd_settings._emoji_pack_name", return_value="classic"):
        result = mod._cmd_emoji(_ctx("pack ultraemoji"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "error" in captured.out.lower() or "Unknown" in captured.out


# ---------------------------------------------------------------------------
# _cmd_overlay
# ---------------------------------------------------------------------------

def test_cmd_overlay_status_shows_state(capsys):
    cli = _mock_cli()
    cli._interactive_overlays_enabled.return_value = False
    cli._overlay_available.return_value = False
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_overlay(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "OFF" in captured.out or "off" in captured.out.lower()


def test_cmd_overlay_on_enables(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_overlay(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("interactive_overlays", True)


def test_cmd_overlay_off_disables(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_overlay(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("interactive_overlays", False)


def test_cmd_overlay_invalid_token(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_overlay(_ctx("maybe"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_pasteguard
# ---------------------------------------------------------------------------

def test_cmd_pasteguard_on_enables(capsys):
    cli = _mock_cli(_PREFS={"paste_guard": False})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_pasteguard(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("paste_guard", True)
    captured = capsys.readouterr()
    assert "enabled" in captured.out.lower()


def test_cmd_pasteguard_off_disables(capsys):
    cli = _mock_cli(_PREFS={"paste_guard": True})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_pasteguard(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("paste_guard", False)


def test_cmd_pasteguard_status_shows_current(capsys):
    cli = _mock_cli(_PREFS={"paste_guard": True})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_pasteguard(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "on" in captured.out.lower() or "currently" in captured.out.lower()


# ---------------------------------------------------------------------------
# _cmd_links (delegates to toggle pref)
# ---------------------------------------------------------------------------

def test_cmd_links_delegates_to_toggle(capsys):
    cli = _mock_cli()
    cli._handle_simple_toggle_pref.return_value = _CMD_CONTINUE
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_links(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._handle_simple_toggle_pref.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_keybind
# ---------------------------------------------------------------------------

def test_cmd_keybind_list_empty(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No custom keybinds" in captured.out


def test_cmd_keybind_list_shows_bindings(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {"Ctrl+H": "/histsearch"}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Ctrl+H" in captured.out
    assert "/histsearch" in captured.out


def test_cmd_keybind_bind_valid(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("Ctrl+H /histsearch"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called()
    cli._apply_custom_keybind.assert_called_with("Ctrl+H", "/histsearch")


def test_cmd_keybind_invalid_key_prefix(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("F1 /help"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Ctrl+" in captured.out or "Alt+" in captured.out


def test_cmd_keybind_action_not_slash_command(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("Ctrl+H notacommand"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "slash" in captured.out.lower() or "/" in captured.out


def test_cmd_keybind_clear_existing(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {"Ctrl+H": "/histsearch"}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("clear Ctrl+H"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called()


def test_cmd_keybind_clear_missing(capsys):
    cli = _mock_cli(_PREFS={"custom_keybinds": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_keybind(_ctx("clear Ctrl+Q"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No keybind" in captured.out


# ---------------------------------------------------------------------------
# _cmd_colorscheme
# ---------------------------------------------------------------------------

def test_cmd_colorscheme_list_shows_schemes(capsys):
    cli = _mock_cli(_PREFS={"color_scheme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_colorscheme(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "default" in captured.out or "Color Schemes" in captured.out


def test_cmd_colorscheme_set_valid(capsys):
    cli = _mock_cli(_PREFS={"color_scheme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_colorscheme(_ctx("ocean"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("color_scheme", "ocean")


def test_cmd_colorscheme_set_invalid(capsys):
    cli = _mock_cli(_PREFS={"color_scheme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_colorscheme(_ctx("nonexistent_scheme"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Unknown scheme" in captured.out


def test_cmd_colorscheme_reset_sets_default(capsys):
    cli = _mock_cli(_PREFS={"color_scheme": "ocean"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_colorscheme(_ctx("reset"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("color_scheme", "default")


# ---------------------------------------------------------------------------
# _cmd_emojiheaders
# ---------------------------------------------------------------------------

def test_cmd_emojiheaders_on(capsys):
    cli = _mock_cli(_PREFS={"emoji_headers": False})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_emojiheaders(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("emoji_headers", True)


def test_cmd_emojiheaders_off(capsys):
    cli = _mock_cli(_PREFS={"emoji_headers": True})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_emojiheaders(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("emoji_headers", False)


def test_cmd_emojiheaders_status_shows_current(capsys):
    cli = _mock_cli(_PREFS={"emoji_headers": True})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_emojiheaders(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "on" in captured.out.lower()


# ---------------------------------------------------------------------------
# _cmd_layout
# ---------------------------------------------------------------------------

def test_cmd_layout_status_no_arg(capsys):
    cli = _mock_cli(_PREFS={"layout": "normal"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_layout(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "normal" in captured.out.lower() or "layout" in captured.out.lower()


def test_cmd_layout_set_valid(capsys):
    cli = _mock_cli(_PREFS={"layout": "normal"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_layout(_ctx("compact"))
    assert result == _CMD_CONTINUE
    assert cli._PREFS.get("layout") == "compact" or cli._prefs_set.called


def test_cmd_layout_invalid_token(capsys):
    cli = _mock_cli(_PREFS={"layout": "normal"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_layout(_ctx("superextreme"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "error" in captured.out.lower() or "Expected" in captured.out


def test_cmd_layout_reset(capsys):
    cli = _mock_cli(_PREFS={"layout": "compact", "layout_preset": "focus"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_layout(_ctx("reset"))
    assert result == _CMD_CONTINUE
    cli._print_feedback.assert_called()


# ---------------------------------------------------------------------------
# _cycle_theme helper
# ---------------------------------------------------------------------------

def test_cycle_theme_next_advances(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        mod._cycle_theme("next")
    cli._prefs_set.assert_called()
    # Should have set a theme different from current index position
    call_args = cli._prefs_set.call_args_list
    assert any(args[0][0] == "theme" for args in call_args)


def test_cycle_theme_prev_goes_back(capsys):
    cli = _mock_cli(_PREFS={"theme": "default"})
    with patch.object(mod, "_m", return_value=cli):
        mod._cycle_theme("prev")
    cli._prefs_set.assert_called()
