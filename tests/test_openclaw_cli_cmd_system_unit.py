"""Unit tests for openclaw_cli_cmd_system.py — system, prompt, and display handlers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")
import openclaw_cli_cmd_system as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_cli(**kwargs) -> MagicMock:
    """Create a minimal mock of openclaw_cli for system tests."""
    m = MagicMock()
    m._get_is_tty = MagicMock(return_value=False)
    m._PREFS = kwargs.pop("_PREFS", {})
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._prefs_set = MagicMock()
    m._print_error = MagicMock()
    m._handle_simple_toggle_pref = MagicMock(return_value=_CMD_CONTINUE)
    m._SYSTEM_PROMPT_MAX = 2000
    m._SEPARATOR_STYLES = {"gradient": {}, "pulse": {}, "dots": {}, "wave": {}, "none": {}}
    m._print_animated_separator = MagicMock()
    m._get_cmd_registry = MagicMock()
    m._DEFAULT_PROMPT_FORMAT = "openclaw>"
    m._render_prompt_format = MagicMock(side_effect=lambda fmt: fmt)
    m._BUILTIN_COMMAND_NAMES = frozenset({"alias", "system", "help"})
    m._MAX_ALIASES = 20
    m._save_prefs = MagicMock()
    m._e = MagicMock(side_effect=lambda emoji, fallback: emoji)
    # _next_inject attribute for promptdebug — must be str for vars().get().strip()
    m._next_inject = ""
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_system
# ---------------------------------------------------------------------------

def test_cmd_system_view_empty(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "not set" in captured.out.lower() or "system prompt" in captured.out.lower()


def test_cmd_system_view_with_content(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": "You are a helpful assistant."})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("view"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "helpful assistant" in captured.out


def test_cmd_system_set_prompt(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("set You are a bot."))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("system_prompt", "You are a bot.")


def test_cmd_system_set_empty_shows_usage(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("set"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_system_set_too_long(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("set " + "x" * 2001))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_system_clear_removes_prompt(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": "old prompt"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("clear"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("system_prompt", "")
    captured = capsys.readouterr()
    assert "cleared" in captured.out.lower()


def test_cmd_system_append_to_existing(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": "First part."})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("append Second part."))
    assert result == _CMD_CONTINUE
    call_args = cli._prefs_set.call_args
    saved = call_args[0][1]
    assert "First part." in saved
    assert "Second part." in saved


def test_cmd_system_append_empty_shows_usage(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("append"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_system_unknown_sub_command(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_system(_ctx("foobar"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_promptdebug
# ---------------------------------------------------------------------------

def test_cmd_promptdebug_shows_preview(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": "Be concise."})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_promptdebug(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Be concise." in captured.out or "preview" in captured.out.lower()


def test_cmd_promptdebug_no_system_prompt(capsys):
    cli = _mock_cli(_PREFS={"system_prompt": ""})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_promptdebug(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "User message" in captured.out or "preview" in captured.out.lower()


# ---------------------------------------------------------------------------
# _cmd_autobold and _cmd_jsonformat (delegate to toggle pref)
# ---------------------------------------------------------------------------

def test_cmd_autobold_delegates_to_toggle():
    cli = _mock_cli()
    cli._handle_simple_toggle_pref.return_value = _CMD_CONTINUE
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_autobold(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._handle_simple_toggle_pref.assert_called_once()


def test_cmd_jsonformat_delegates_to_toggle():
    cli = _mock_cli()
    cli._handle_simple_toggle_pref.return_value = _CMD_CONTINUE
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_jsonformat(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._handle_simple_toggle_pref.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_separator
# ---------------------------------------------------------------------------

def test_cmd_separator_set_valid(capsys):
    cli = _mock_cli(_PREFS={"separator_style": "gradient"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_separator(_ctx("dots"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("separator_style", "dots")


def test_cmd_separator_set_invalid(capsys):
    cli = _mock_cli(_PREFS={"separator_style": "gradient"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_separator(_ctx("sparkle"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Unknown style" in captured.out or "valid" in captured.out.lower()


def test_cmd_separator_show_current(capsys):
    cli = _mock_cli(_PREFS={"separator_style": "wave"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_separator(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "wave" in captured.out


def test_cmd_separator_none_does_not_print_separator(capsys):
    cli = _mock_cli(_PREFS={"separator_style": "gradient"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_separator(_ctx("none"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("separator_style", "none")
    cli._print_animated_separator.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_palette
# ---------------------------------------------------------------------------

def test_cmd_palette_lists_all_when_no_query(capsys):
    cmd_mock = MagicMock()
    cmd_mock.name = "help"
    cmd_mock.description = "Show help text"
    registry = MagicMock()
    registry.list_commands.return_value = [cmd_mock]
    cli = _mock_cli()
    cli._get_cmd_registry.return_value = registry
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_palette(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "help" in captured.out.lower()


def test_cmd_palette_filters_by_query(capsys):
    cmd1 = MagicMock()
    cmd1.name = "histsearch"
    cmd1.description = "Search history"
    cmd2 = MagicMock()
    cmd2.name = "theme"
    cmd2.description = "Change theme"
    registry = MagicMock()
    registry.list_commands.return_value = [cmd1, cmd2]
    cli = _mock_cli()
    cli._get_cmd_registry.return_value = registry
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_palette(_ctx("hist"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "histsearch" in captured.out


def test_cmd_palette_no_matches(capsys):
    cmd1 = MagicMock()
    cmd1.name = "theme"
    cmd1.description = "Change theme"
    registry = MagicMock()
    registry.list_commands.return_value = [cmd1]
    cli = _mock_cli()
    cli._get_cmd_registry.return_value = registry
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_palette(_ctx("zzznomatch"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No commands" in captured.out


# ---------------------------------------------------------------------------
# _cmd_prompt
# ---------------------------------------------------------------------------

def test_cmd_prompt_view_current(capsys):
    cli = _mock_cli(_PREFS={"prompt_format": "openclaw>"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_prompt(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "openclaw>" in captured.out


def test_cmd_prompt_set_format(capsys):
    cli = _mock_cli(_PREFS={"prompt_format": "openclaw>"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_prompt(_ctx("{route} ❯"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("prompt_format", "{route} ❯")


def test_cmd_prompt_reset(capsys):
    cli = _mock_cli(_PREFS={"prompt_format": "custom>"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_prompt(_ctx("reset"))
    assert result == _CMD_CONTINUE
    cli._prefs_set.assert_called_with("prompt_format", "openclaw>")


def test_cmd_prompt_too_short(capsys):
    cli = _mock_cli(_PREFS={"prompt_format": "openclaw>"})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_prompt(_ctx("x"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "too short" in captured.out.lower() or "short" in captured.out.lower()


# ---------------------------------------------------------------------------
# _cmd_alias
# ---------------------------------------------------------------------------

def test_cmd_alias_list_empty(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "no aliases" in captured.out.lower()


def test_cmd_alias_define_new(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("myalias /help"))
    assert result == _CMD_CONTINUE
    assert "myalias" in cli._PREFS["aliases"]
    assert cli._PREFS["aliases"]["myalias"] == "/help"


def test_cmd_alias_reserved_name(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("alias /help"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_alias_builtin_name(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("system /help"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_alias_remove_existing(capsys):
    cli = _mock_cli(_PREFS={"aliases": {"myalias": "/help"}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("rm myalias"))
    assert result == _CMD_CONTINUE
    assert "myalias" not in cli._PREFS["aliases"]


def test_cmd_alias_remove_nonexistent(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("rm doesnotexist"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_alias_no_expansion_shows_usage(capsys):
    cli = _mock_cli(_PREFS={"aliases": {}})
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_alias(_ctx("myalias"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_pathhints and _cmd_ratehint (delegates)
# ---------------------------------------------------------------------------

def test_cmd_pathhints_delegates_to_toggle():
    cli = _mock_cli()
    cli._handle_simple_toggle_pref.return_value = _CMD_CONTINUE
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_pathhints(_ctx("on"))
    assert result == _CMD_CONTINUE
    cli._handle_simple_toggle_pref.assert_called_once()


def test_cmd_ratehint_delegates_to_toggle():
    cli = _mock_cli()
    cli._handle_simple_toggle_pref.return_value = _CMD_CONTINUE
    with patch.object(mod, "_m", return_value=cli):
        result = mod._cmd_ratehint(_ctx("off"))
    assert result == _CMD_CONTINUE
    cli._handle_simple_toggle_pref.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_benchmark (smoke test — TCP connection expected to fail in CI)
# ---------------------------------------------------------------------------

def test_cmd_benchmark_returns_continue(capsys):
    cli = _mock_cli()
    ctx = _ctx("1")
    ctx.config = None  # no config; falls back to env
    with patch.object(mod, "_m", return_value=cli), \
         patch("socket.create_connection", side_effect=OSError("refused")):
        result = mod._cmd_benchmark(ctx)
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    # Should show benchmark header and at least one result line
    assert "Benchmark" in captured.out or "ping" in captured.out.lower() or "Min" in captured.out
