"""Unit tests for openclaw_cli_cmd_session.py — session lifecycle command handlers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
import openclaw_cli_cmd_session as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_session(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "sess-1")
    s.tags = kwargs.get("tags", [])
    s.files = kwargs.get("files", [])
    s.plan_id = kwargs.get("plan_id", None)
    s.task_id = kwargs.get("task_id", None)
    s.cwd = kwargs.get("cwd", "/project")
    s.title = kwargs.get("title", "Test Session")
    s.status = kwargs.get("status", "active")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _mock_session_summary(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "abc12345")
    s.title = kwargs.get("title", "A Session")
    s.last_summary = kwargs.get("last_summary", "")
    s.updated_at = kwargs.get("updated_at", "2024-01-01T00:00:00")
    s.command_count = kwargs.get("command_count", 3)
    s.tags = kwargs.get("tags", [])
    s.cwd = kwargs.get("cwd", "/home/user")
    s.status = kwargs.get("status", "active")
    s.files = kwargs.get("files", [])
    s.plan_id = kwargs.get("plan_id", None)
    s.task_id = kwargs.get("task_id", None)
    return s


def _mock_cli(**kwargs) -> MagicMock:
    session = kwargs.pop("_session", _mock_session())
    m = MagicMock()
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._require_session_or_warn = MagicMock(return_value=session)
    m._print_error = MagicMock()
    m._print_session_summary = MagicMock()
    m._print_feedback = MagicMock()
    m._set_command_result = MagicMock()
    m._session_badges = MagicMock(return_value="")
    m._session_is_stale = MagicMock(return_value=False)
    m._status_family = MagicMock(return_value="active")
    m._session_operator_snapshot = MagicMock(return_value={})
    m._session_preview_lines = MagicMock(return_value=[])
    m._print_dashboard_surface = MagicMock()
    m._progress_cell = MagicMock(return_value="")
    m._interactive_overlays_enabled = MagicMock(return_value=False)
    m._content_cmds_mod = MagicMock()
    m._content_cmds_mod._build_export_body = MagicMock(return_value="export content")
    m._RICH_CONSOLE = MagicMock()
    m._RichTable = MagicMock()
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_session — show current session info
# ---------------------------------------------------------------------------

def test_cmd_session_calls_print_session_summary():
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_session(_ctx(""))
    assert result == _CMD_CONTINUE
    cli._print_session_summary.assert_called_once_with(session)


def test_cmd_session_no_session_returns_continue():
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_session(_ctx(""))
    assert result == _CMD_CONTINUE
    cli._print_session_summary.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_events — show session events
# ---------------------------------------------------------------------------

def test_cmd_events_no_events(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.load_events", return_value=[]):
        result = mod._cmd_events(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No events" in out


def test_cmd_events_shows_events(capsys):
    cli = _mock_cli()
    events = [
        {"kind": "chat", "content": "hello", "timestamp": "2024-01-01T10:00:00"},
        {"kind": "analyze", "content": "result", "timestamp": "2024-01-01T11:00:00"},
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.load_events", return_value=events), \
         patch("openclaw_cli_cmd_session._build_event_label", return_value="label"):
        result = mod._cmd_events(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "chat" in out or "analyze" in out
    cli._print_dashboard_surface.assert_called_once()


def test_cmd_events_invalid_count(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_events(_ctx("abc"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_events_decisions_filter(capsys):
    cli = _mock_cli()
    events = [
        {"kind": "chat", "content": "chat message", "timestamp": "2024-01-01T10:00:00"},
        {"kind": "route", "content": "routing decision", "timestamp": "2024-01-01T11:00:00"},
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.load_events", return_value=events), \
         patch("openclaw_cli_cmd_session._build_event_label", return_value="label"):
        result = mod._cmd_events(_ctx("decisions"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "route" in out
    assert "chat" not in out
    _, kwargs = cli._print_dashboard_surface.call_args
    assert any("decision-only" in line for line in kwargs["summary_lines"])
    assert any("/session" in line for line in kwargs["action_lines"])


def test_cmd_events_numeric_limit(capsys):
    cli = _mock_cli()
    events = [{"kind": "chat", "content": f"msg {i}", "timestamp": "2024-01-01T10:00:00"} for i in range(10)]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.load_events", return_value=events[:2]) as mock_load, \
         patch("openclaw_cli_cmd_session._build_event_label", return_value="label"):
        result = mod._cmd_events(_ctx("2"))
    assert result == _CMD_CONTINUE
    mock_load.assert_called_once_with("sess-1", limit=2)


def test_cmd_events_preview_strip_adds_recovery_actions(capsys):
    cli = _mock_cli()
    events = [
        {
            "kind": "error",
            "content": "network timeout while retrying write",
            "timestamp": "2024-01-01T11:00:00",
            "metadata": {"summary": "Retry loop hit a network timeout"},
        },
        {
            "kind": "watch",
            "content": "polling workspace",
            "timestamp": "2024-01-01T10:59:00",
        },
        {
            "kind": "checkpoint",
            "content": "saved rollback point",
            "timestamp": "2024-01-01T10:58:00",
        },
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.load_events", return_value=events):
        result = mod._cmd_events(_ctx(""))
    assert result == _CMD_CONTINUE
    args, kwargs = cli._print_dashboard_surface.call_args
    assert args[0] == "Event Preview Strip"
    assert any("latest kind: error" in line for line in kwargs["summary_lines"])
    assert any("error" in line and "Retry loop hit a network timeout" in line for line in kwargs["detail_lines"])
    assert any("/watch status" in line for line in kwargs["action_lines"])
    assert any("/watch history" in line for line in kwargs["action_lines"])
    assert any("/bookmark" in line for line in kwargs["action_lines"])


# ---------------------------------------------------------------------------
# _cmd_sessions — list sessions
# ---------------------------------------------------------------------------

def test_cmd_sessions_no_sessions(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.list_sessions", return_value=[]):
        result = mod._cmd_sessions(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No sessions" in out


def test_cmd_sessions_lists_sessions(capsys):
    cli = _mock_cli()
    sessions = [_mock_session_summary(session_id="abc12345", title="My Project")]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.list_sessions", return_value=sessions):
        result = mod._cmd_sessions(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "My Project" in out or "abc12345" in out


def test_cmd_sessions_search_filter(capsys):
    cli = _mock_cli()
    sessions = [
        _mock_session_summary(session_id="aaa00001", title="Alpha Project", last_summary=""),
        _mock_session_summary(session_id="bbb00002", title="Beta Project", last_summary=""),
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.list_sessions", return_value=sessions):
        result = mod._cmd_sessions(_ctx("search alpha"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "Beta" not in out


def test_cmd_sessions_open_shows_instructions(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_sessions(_ctx("open abc12345"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "resume" in out.lower() or "openclaw" in out


# ---------------------------------------------------------------------------
# _cmd_tag — tag a session
# ---------------------------------------------------------------------------

def test_cmd_tag_list_no_tags(capsys):
    session = _mock_session(tags=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tag(_ctx("list"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No tags" in out


def test_cmd_tag_list_with_tags(capsys):
    session = _mock_session(tags=["feature", "wip"])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tag(_ctx("list"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "feature" in out


def test_cmd_tag_add_new_tag(capsys):
    session = _mock_session(tags=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.save_session", MagicMock()):
        result = mod._cmd_tag(_ctx("add newtag"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "newtag" in out


def test_cmd_tag_add_duplicate_tag(capsys):
    session = _mock_session(tags=["existing"])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tag(_ctx("add existing"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "already present" in out


def test_cmd_tag_remove_tag(capsys):
    session = _mock_session(tags=["mytag"])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.save_session", MagicMock()):
        result = mod._cmd_tag(_ctx("rm mytag"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "Removed" in out or "mytag" in out


def test_cmd_tag_remove_nonexistent_tag(capsys):
    session = _mock_session(tags=[])
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tag(_ctx("rm ghost"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "not found" in out


# ---------------------------------------------------------------------------
# _cmd_bookmark / _cmd_bookmarks — bookmark management
# ---------------------------------------------------------------------------

def test_cmd_bookmark_creates_bookmark(capsys):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    bookmark = {"id": "bm-1", "label": "my checkpoint", "turn_index": 3}
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.create_session_bookmark", return_value=bookmark):
        result = mod._cmd_bookmark(_ctx("my checkpoint"))
    assert result == _CMD_CONTINUE
    cli._print_feedback.assert_called_once()


def test_cmd_bookmark_no_session(capsys):
    cli = _mock_cli()
    cli._require_session_or_warn = MagicMock(return_value=None)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_bookmark(_ctx("label"))
    assert result == _CMD_CONTINUE
    cli._print_feedback.assert_not_called()


def test_cmd_bookmarks_no_bookmarks(capsys):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.list_session_bookmarks", return_value=[]):
        result = mod._cmd_bookmarks(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No bookmarks" in out


def test_cmd_bookmarks_lists_bookmarks(capsys):
    session = _mock_session()
    cli = _mock_cli(_session=session)
    bookmarks = [
        {"id": "bm-1", "label": "first", "turn_index": 1, "summary": ""},
        {"id": "bm-2", "label": "second", "turn_index": 5, "summary": "A summary"},
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch("openclaw_cli_cmd_session.list_session_bookmarks", return_value=bookmarks):
        result = mod._cmd_bookmarks(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "first" in out
    assert "second" in out


# ---------------------------------------------------------------------------
# _cmd_export — export session history
# ---------------------------------------------------------------------------

def test_cmd_export_no_history(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_PREFS", {"cmd_history": []}), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_export(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No history" in out


def test_cmd_export_writes_file(capsys, tmp_path, monkeypatch):
    cli = _mock_cli()
    hist = [{"text": "analyze foo", "ts": "2024-01-01T10:00:00"}]
    export_content = "# Export\n\nanalyze foo\n"
    cli._content_cmds_mod._build_export_body.return_value = export_content

    output_file = tmp_path / "export_test.md"
    monkeypatch.chdir(tmp_path)

    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_PREFS", {"cmd_history": hist}), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_export(_ctx("md"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "Exported" in out or "export" in out.lower()


def test_cmd_export_default_format_is_md(capsys, tmp_path, monkeypatch):
    cli = _mock_cli()
    hist = [{"text": "some command", "ts": "2024-01-01T10:00:00"}]
    cli._content_cmds_mod._build_export_body.return_value = "content"

    monkeypatch.chdir(tmp_path)

    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_PREFS", {"cmd_history": hist}), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_export(_ctx(""))
    assert result == _CMD_CONTINUE
    cli._content_cmds_mod._build_export_body.assert_called_once()
    call_args = cli._content_cmds_mod._build_export_body.call_args[0]
    assert call_args[1] == "md"
