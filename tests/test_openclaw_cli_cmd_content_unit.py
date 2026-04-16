"""Unit tests for openclaw_cli_cmd_content.py — content, search, and analytics handlers."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
import openclaw_cli_cmd_content as mod  # type: ignore
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
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _mock_cli(**kwargs) -> MagicMock:
    m = MagicMock()
    m._RICH_AVAILABLE = False
    m._IS_TTY = False
    m._last_response_text = kwargs.pop("_last_response_text", "")
    m._require_session_or_warn = MagicMock(return_value=kwargs.pop("_session", _mock_session()))
    m._print_error = MagicMock()
    m._format_byte_count = MagicMock(return_value="1 KB")
    m._interactive_overlays_enabled = MagicMock(return_value=False)
    m._print_dashboard_surface = MagicMock()
    m._preview_block_lines = MagicMock(return_value=[])
    m._progress_cell = MagicMock(return_value="")
    m._dedupe_preserve_order = MagicMock(side_effect=lambda x: x)
    m._print_predictive_affordances = MagicMock()
    m._single_line_excerpt = MagicMock(return_value="excerpt")
    m._history_command_texts = MagicMock(return_value=[])
    m._PREFS = kwargs.pop("_PREFS", {})
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _mock_session_summary(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "abc12345")
    s.title = kwargs.get("title", "Test Session")
    s.last_summary = kwargs.get("last_summary", "")
    s.updated_at = kwargs.get("updated_at", "2024-01-01T00:00:00")
    s.command_count = kwargs.get("command_count", 5)
    s.tags = kwargs.get("tags", [])
    s.cwd = kwargs.get("cwd", "/project")
    return s


# ---------------------------------------------------------------------------
# _relative_time — pure helper
# ---------------------------------------------------------------------------

def test_relative_time_recent_seconds():
    from datetime import datetime, timedelta
    # Use naive datetime (no tzinfo) to match what _relative_time expects
    ts = (datetime.utcnow() - timedelta(seconds=30)).isoformat()
    result = mod._relative_time(ts)
    assert "s ago" in result


def test_relative_time_minutes():
    from datetime import datetime, timedelta
    ts = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    result = mod._relative_time(ts)
    assert "m ago" in result


def test_relative_time_invalid_returns_empty():
    result = mod._relative_time("not-a-timestamp")
    assert result == ""


# ---------------------------------------------------------------------------
# _parse_collab_entry — pure helper
# ---------------------------------------------------------------------------

def test_parse_collab_entry_basic():
    actor, tags, text = mod._parse_collab_entry("@alice #feature do the thing")
    assert actor == "alice"
    assert "feature" in tags
    assert text == "do the thing"


def test_parse_collab_entry_no_actor():
    actor, tags, text = mod._parse_collab_entry("just some text")
    assert actor == ""
    assert tags == []
    assert text == "just some text"


def test_parse_collab_entry_empty():
    actor, tags, text = mod._parse_collab_entry("")
    assert actor == ""
    assert tags == []
    assert text == ""


# ---------------------------------------------------------------------------
# _cmd_search — no query
# ---------------------------------------------------------------------------

def test_cmd_search_empty_query_shows_usage(capsys):
    with patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_search(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "Usage" in out


def test_cmd_search_no_results(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch("openclaw_cli_cmd_content.load_events", return_value=[]):
        result = mod._cmd_search(_ctx("missing-term-xyz"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No matches" in out


def test_cmd_search_finds_matching_event(capsys):
    cli = _mock_cli()
    events = [{"kind": "chat", "content": "hello world search", "timestamp": "2024-01-01T00:00:00"}]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch("openclaw_cli_cmd_content.load_events", return_value=events):
        result = mod._cmd_search(_ctx("hello"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "hello" in out


def test_cmd_search_cross_session_flag(capsys):
    sessions = [_mock_session_summary(session_id="ses12345")]
    events = [{"kind": "chat", "content": "cross session data", "timestamp": "2024-01-01T00:00:00"}]
    with patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch("openclaw_cli_cmd_content.list_sessions", return_value=sessions), \
         patch("openclaw_cli_cmd_content.load_events", return_value=events):
        result = mod._cmd_search(_ctx("--all cross"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "cross" in out


# ---------------------------------------------------------------------------
# _cmd_outputs — list saved outputs
# ---------------------------------------------------------------------------

def test_cmd_outputs_no_outputs(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_content.list_saved_outputs", return_value=[]):
        result = mod._cmd_outputs(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No saved outputs" in out


def test_cmd_outputs_lists_files(capsys):
    cli = _mock_cli()
    outputs = [
        {"name": "report.md", "size_bytes": 1024, "modified_at": "2024-01-01"},
        {"name": "notes.txt", "size_bytes": 512, "modified_at": "2024-01-02"},
    ]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_content.list_saved_outputs", return_value=outputs), \
         patch("openclaw_cli_cmd_content.load_saved_output_preview", return_value=None):
        result = mod._cmd_outputs(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "report.md" in out or "saved outputs" in out


def test_cmd_outputs_preview_not_found(capsys):
    cli = _mock_cli()
    outputs = [{"name": "file.md", "size_bytes": 100, "modified_at": "2024-01-01"}]
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_IS_TTY", False), \
         patch("openclaw_cli_cmd_content.list_saved_outputs", return_value=outputs), \
         patch("openclaw_cli_cmd_content.load_saved_output_preview", return_value=None):
        result = mod._cmd_outputs(_ctx("missing-file"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "not found" in out.lower() or "Saved output not found" in out


# ---------------------------------------------------------------------------
# _cmd_stats — session statistics
# ---------------------------------------------------------------------------

def test_cmd_stats_no_sessions(capsys):
    # The active _cmd_stats (second definition) uses _PREFS directly for cmd_history/ratings
    with patch.object(mod, "_PREFS", {"cmd_history": [], "ratings": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch("openclaw_cli_cmd_content.list_sessions", return_value=[]):
        result = mod._cmd_stats(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No usage data yet" in out or "Chat a bit first" in out


def test_cmd_stats_with_sessions(capsys):
    # The active _cmd_stats reads cmd_history from _PREFS and shows frequency charts
    hist = [{"text": "/analyze foo", "ts": ""}, {"text": "/analyze bar", "ts": ""}]
    with patch.object(mod, "_PREFS", {"cmd_history": hist, "ratings": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch("openclaw_cli_cmd_content.list_sessions", return_value=[]), \
         patch("openclaw_cli_cmd_content._compute_cmd_freq", return_value={"/analyze": 2}), \
         patch("openclaw_cli_cmd_content._build_ascii_bar_rows", return_value=[("/analyze", "██", 2)]):
        result = mod._cmd_stats(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "Command Frequency" in out or "/analyze" in out


# ---------------------------------------------------------------------------
# _cmd_history — command history display
# ---------------------------------------------------------------------------

def test_cmd_history_empty(capsys):
    with patch.object(mod, "_PREFS", {"cmd_history": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_history(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "no history" in out.lower() or "Command History" in out


def test_cmd_history_shows_entries(capsys):
    hist = [{"text": "analyze this", "ts": ""}, {"text": "/help", "ts": ""}]
    with patch.object(mod, "_PREFS", {"cmd_history": hist}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_history(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "analyze this" in out


def test_cmd_history_clear(capsys):
    hist = [{"text": "some command", "ts": ""}]
    prefs = {"cmd_history": hist}
    with patch.object(mod, "_PREFS", prefs), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_save_prefs", MagicMock()), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_history(_ctx("clear"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "cleared" in out.lower() or "OK" in out


def test_cmd_history_invalid_arg(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_PREFS", {"cmd_history": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_history(_ctx("notanumber"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


# ---------------------------------------------------------------------------
# _cmd_pin / _cmd_pins — pin management
# ---------------------------------------------------------------------------

def test_cmd_pin_list_no_pins(capsys):
    with patch.object(mod, "_PREFS", {"pins": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_pin(_ctx("list"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "no pins" in out.lower() or "Pins" in out


def test_cmd_pin_list_with_pins(capsys):
    pins = [{"name": "mypin", "text": "Hello pinned content", "ts": "2024-01-01T00:00:00"}]
    with patch.object(mod, "_PREFS", {"pins": pins}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_pin(_ctx("list"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "mypin" in out


def test_cmd_pin_saves_last_response(capsys):
    cli = _mock_cli(_last_response_text="Response to pin")
    with patch.object(mod, "_PREFS", {"pins": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_save_prefs", MagicMock()), \
         patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_pin(_ctx("myname"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "myname" in out or "pinned" in out.lower()


def test_cmd_pin_nothing_to_pin(capsys):
    cli = _mock_cli(_last_response_text="")
    with patch.object(mod, "_PREFS", {"pins": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_pin(_ctx("mypin"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_pin_rm_existing(capsys):
    pins = [{"name": "oldpin", "text": "Some content", "ts": "2024-01-01"}]
    cli = _mock_cli()
    with patch.object(mod, "_PREFS", {"pins": pins}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_save_prefs", MagicMock()), \
         patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_pin(_ctx("rm oldpin"))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "removed" in out.lower() or "OK" in out


def test_cmd_pin_rm_nonexistent(capsys):
    cli = _mock_cli()
    with patch.object(mod, "_PREFS", {"pins": []}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_pin(_ctx("rm ghost"))
    assert result == _CMD_CONTINUE
    cli._print_error.assert_called()


def test_cmd_pins_is_alias_for_pin_list(capsys):
    pins = [{"name": "p1", "text": "text content", "ts": "2024-01-01"}]
    with patch.object(mod, "_PREFS", {"pins": pins}), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False), \
         patch.object(mod, "_get_cli_mod", return_value=_mock_cli()):
        result = mod._cmd_pins(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "p1" in out


# ---------------------------------------------------------------------------
# _cmd_timeline — activity timeline
# ---------------------------------------------------------------------------

def test_cmd_timeline_no_history(capsys):
    cli = _mock_cli(_PREFS={"cmd_history": []})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_timeline(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No history" in out or "timeline" in out.lower()


def test_cmd_timeline_with_timestamped_entries(capsys):
    hist = [
        {"text": "/analyze foo", "timestamp": "2024-06-01T10:00:00"},
        {"text": "explain this", "timestamp": "2024-06-01T11:00:00"},
    ]
    cli = _mock_cli(_PREFS={"cmd_history": hist})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_timeline(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "2024-06-01" in out or "Timeline" in out


def test_cmd_timeline_entries_without_timestamp_skipped(capsys):
    hist = [{"text": "no timestamp here"}]
    cli = _mock_cli(_PREFS={"cmd_history": hist})
    with patch.object(mod, "_get_cli_mod", return_value=cli), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch.object(mod, "_get_is_tty", return_value=False):
        result = mod._cmd_timeline(_ctx(""))
    assert result == _CMD_CONTINUE
    out = capsys.readouterr().out
    assert "No timestamped history" in out or "timeline" in out.lower()
