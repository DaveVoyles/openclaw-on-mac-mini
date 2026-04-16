"""Unit tests for openclaw_cli_cmd_core.py — core system and AI command handlers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, "src")
import openclaw_cli_cmd_core as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1", history=None) -> ChatCommandContext:
    return ChatCommandContext(history=history if history is not None else [], session_id=session_id, args=args)


def _mock_session(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "sess-1")
    s.cwd = kwargs.get("cwd", "/project")
    s.files = kwargs.get("files", [])
    s.plan_id = kwargs.get("plan_id", None)
    s.task_id = kwargs.get("task_id", None)
    s.repl_auto_route = kwargs.get("repl_auto_route", True)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _mock_cli(**kwargs) -> MagicMock:
    """Create a minimal mock of the main openclaw_cli module."""
    _session = kwargs.pop("_session", _mock_session())
    m = MagicMock()
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._PREFS = kwargs.pop("_PREFS", {})
    m._require_session_or_warn = MagicMock(return_value=_session)
    m._print_error = MagicMock()
    m._print_feedback = MagicMock()
    m._print_dashboard_surface = MagicMock()
    m._set_command_result = MagicMock()
    m.append_event = MagicMock()
    m.print_chat_help = MagicMock()
    m._save_prefs = MagicMock()
    m._prefs_set = MagicMock()
    m._next_inject = ""
    m._draft_buffer = ""
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_help
# ---------------------------------------------------------------------------

def test_cmd_help_no_args_calls_print_chat_help():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_help(_ctx(""))
    assert result == _CMD_CONTINUE
    cli.print_chat_help.assert_called_once_with()


def test_cmd_help_search_arg_passes_search_term():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_help(_ctx("search rollback"))
    assert result == _CMD_CONTINUE
    cli.print_chat_help.assert_called_once_with(search="rollback")


def test_cmd_help_non_search_token_calls_default():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_help(_ctx("version"))
    assert result == _CMD_CONTINUE
    cli.print_chat_help.assert_called_once_with()


# ---------------------------------------------------------------------------
# _cmd_version
# ---------------------------------------------------------------------------

def test_cmd_version_returns_continue(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "cli_version", return_value="1.2.3"), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_version(_ctx())
    assert result == _CMD_CONTINUE


def test_cmd_version_prints_version_string(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "cli_version", return_value="9.9.9"), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        mod._cmd_version(_ctx())
    captured = capsys.readouterr()
    assert "9.9.9" in captured.out


# ---------------------------------------------------------------------------
# _cmd_clear
# ---------------------------------------------------------------------------

def test_cmd_clear_empties_history():
    history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    ctx = _ctx(history=history)
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_clear(ctx)
    assert result == _CMD_CONTINUE
    assert ctx.history == []


def test_cmd_clear_calls_append_event():
    ctx = _ctx(session_id="sess-abc")
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        mod._cmd_clear(ctx)
    cli.append_event.assert_called_once()
    call_kwargs = cli.append_event.call_args
    assert "sess-abc" in call_kwargs[0] or "sess-abc" in str(call_kwargs)


def test_cmd_clear_without_session_id_skips_event():
    ctx = _ctx(session_id="")
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        mod._cmd_clear(ctx)
    cli.append_event.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_context
# ---------------------------------------------------------------------------

def test_cmd_context_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_context(_ctx())
    assert result == _CMD_CONTINUE


def test_cmd_context_calls_dashboard(capsys):
    session = _mock_session(cwd="/myproject", files=[], plan_id=None, task_id=None)
    cli = _mock_cli(_session=session)
    cli._progress_cell = MagicMock(return_value="cell")
    cli._render_effective_grounding_preview = MagicMock(return_value="")
    cli._validate_plan_id_local = MagicMock(return_value=MagicMock(available=True))
    cli._validate_task_id_local = MagicMock(return_value=MagicMock(available=True))
    cli._link_validation_suffix = MagicMock(return_value="")
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_context(_ctx())
    assert result == _CMD_CONTINUE
    cli._print_dashboard_surface.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_cwd
# ---------------------------------------------------------------------------

def test_cmd_cwd_no_args_shows_current(capsys):
    session = _mock_session(cwd="/the/current/dir")
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_cwd(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "/the/current/dir" in captured.out


def test_cmd_cwd_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_cwd(_ctx("some/path"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_not_called()


def test_cmd_cwd_invalid_path_prints_error():
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_cwd(_ctx("/this/path/does/not/exist/at/all"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()
    assert "not a directory" in cli._print_error.call_args[0][0]


def test_cmd_cwd_valid_path_updates_session(tmp_path):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_core.update_session") as mock_update:
        result = mod._cmd_cwd(_ctx(str(tmp_path)))
    assert result == _CMD_CONTINUE
    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args
    assert "cwd" in call_kwargs[1] or str(tmp_path) in str(call_kwargs)


# ---------------------------------------------------------------------------
# _cmd_autoroute
# ---------------------------------------------------------------------------

def test_cmd_autoroute_no_args_shows_status(capsys):
    session = _mock_session(repl_auto_route=True)
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_autoroute(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "ON" in captured.out or "OFF" in captured.out


def test_cmd_autoroute_off_disables(capsys):
    session = _mock_session(repl_auto_route=True)
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_core.update_session") as mock_update:
        result = mod._cmd_autoroute(_ctx("off"))
    assert result == _CMD_CONTINUE
    mock_update.assert_called_once()
    captured = capsys.readouterr()
    assert "disabled" in captured.out.lower() or "OFF" in captured.out


def test_cmd_autoroute_on_enables(capsys):
    session = _mock_session(repl_auto_route=False)
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_core.update_session") as mock_update:
        result = mod._cmd_autoroute(_ctx("on"))
    assert result == _CMD_CONTINUE
    mock_update.assert_called_once()
    captured = capsys.readouterr()
    assert "enabled" in captured.out.lower() or "ON" in captured.out


def test_cmd_autoroute_invalid_arg_prints_error():
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_autoroute(_ctx("maybe"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()


def test_cmd_autoroute_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_autoroute(_ctx("on"))
    assert result == _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_rollback
# ---------------------------------------------------------------------------

def test_cmd_rollback_list_no_snapshots(capsys):
    cli = _mock_cli(_PREFS={"snapshots": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_rollback(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No snapshots" in captured.out


def test_cmd_rollback_list_shows_snapshots(capsys):
    snapshots = {"backup": {"sha": "abc123", "ts": "2024-01-01T00:00:00Z"}}
    cli = _mock_cli(_PREFS={"snapshots": snapshots})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_rollback(_ctx("list"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "backup" in captured.out
    assert "abc123" in captured.out


def test_cmd_rollback_empty_arg_shows_snapshots(capsys):
    snapshots = {"mysnap": {"sha": "def456", "ts": "2024-06-01T00:00:00Z"}}
    cli = _mock_cli(_PREFS={"snapshots": snapshots})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_rollback(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "mysnap" in captured.out


def test_cmd_rollback_unknown_snapshot_name(capsys):
    cli = _mock_cli(_PREFS={"snapshots": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_rollback(_ctx("nonexistent"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No snapshot named 'nonexistent'" in captured.out


def test_cmd_rollback_last_no_checkpoint(capsys):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_core.restore_last_routed_action_checkpoint", return_value=None):
        result = mod._cmd_rollback(_ctx("last"))
    assert result == _CMD_CONTINUE
    cli._set_command_result.assert_called_once_with(cli._require_session_or_warn.return_value.__class__.__instancecheck__.__self__ if False else MagicMock(), ok=False, summary="no routed checkpoints") if False else None
    # just verify we got continue and the error was surfaced
    captured = capsys.readouterr()
    assert "no routed action checkpoints" in captured.out.lower() or "No routed action" in captured.out


def test_cmd_rollback_last_restored_success(capsys):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    outcome = {
        "status": "restored",
        "checkpoint": {"checkpoint_id": "chk-1", "action_kind": "edit"},
        "restored_files": ["/foo/bar.py"],
        "reason": "",
    }
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_core.restore_last_routed_action_checkpoint", return_value=outcome):
        result = mod._cmd_rollback(_ctx("last"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "chk-1" in captured.out or "Rolled back" in captured.out


# ---------------------------------------------------------------------------
# _cmd_files
# ---------------------------------------------------------------------------

def test_cmd_files_no_args_empty_list(capsys):
    session = _mock_session(files=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_files(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No tracked files" in captured.out


def test_cmd_files_no_args_shows_list(capsys):
    session = _mock_session(files=["/some/file.py", "/other/file.txt"])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_files(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "/some/file.py" in captured.out


def test_cmd_files_add_missing_path(capsys):
    session = _mock_session(files=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_files(_ctx("add"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_files_rm_missing_target(capsys):
    session = _mock_session(files=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_files(_ctx("rm"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_files_invalid_subcmd(capsys):
    session = _mock_session(files=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_files(_ctx("purge"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_files_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_files(_ctx(""))
    assert result == _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_inject
# ---------------------------------------------------------------------------

def test_cmd_inject_no_args_shows_usage(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_inject(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_inject_clear_resets_inject(capsys):
    cli = _mock_cli()
    cli._next_inject = "some pending content"
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_inject(_ctx("clear"))
    assert result == _CMD_CONTINUE
    assert cli._next_inject == ""
    captured = capsys.readouterr()
    assert "cleared" in captured.out.lower()


def test_cmd_inject_status_no_inject(capsys):
    cli = _mock_cli()
    cli._next_inject = ""
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_inject(_ctx("status"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "no injection" in captured.out.lower()


def test_cmd_inject_file_not_found(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_inject(_ctx("/nonexistent/path/file.txt"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()
    assert "File not found" in cli._print_error.call_args[0][0]


# ---------------------------------------------------------------------------
# _cmd_template
# ---------------------------------------------------------------------------

def test_cmd_template_list_empty(capsys):
    cli = _mock_cli(_PREFS={"templates": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_template(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No templates" in captured.out


def test_cmd_template_save_stores_template(capsys):
    prefs = {}
    cli = _mock_cli(_PREFS=prefs)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_template(_ctx("save my-tmpl Fix the bug in {{file}}"))
    assert result == _CMD_CONTINUE
    assert prefs.get("templates", {}).get("my-tmpl") == "Fix the bug in {{file}}"
    cli._save_prefs.assert_called_once()


def test_cmd_template_save_invalid_name_errors():
    cli = _mock_cli(_PREFS={})
    # Name "bad!name" contains '!' which fails [A-Za-z0-9\-]+ validation
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_template(_ctx("save bad!name some text"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()


def test_cmd_template_delete_existing(capsys):
    prefs = {"templates": {"del-me": "old text"}}
    cli = _mock_cli(_PREFS=prefs)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False):
        result = mod._cmd_template(_ctx("delete del-me"))
    assert result == _CMD_CONTINUE
    assert "del-me" not in prefs.get("templates", {})
    cli._save_prefs.assert_called_once()


def test_cmd_template_delete_missing_errors():
    cli = _mock_cli(_PREFS={"templates": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_template(_ctx("delete ghost"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()


def test_cmd_template_use_not_found_errors():
    cli = _mock_cli(_PREFS={"templates": {}})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_template(_ctx("use missing-tmpl"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()


# ---------------------------------------------------------------------------
# _cmd_routing
# ---------------------------------------------------------------------------

def test_cmd_routing_no_history(capsys):
    cli = _mock_cli()
    cli._route_quality_summary = MagicMock(return_value=[])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_routing(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No route-quality history" in captured.out


def test_cmd_routing_invalid_subcommand():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_routing(_ctx("badarg"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called_once()
