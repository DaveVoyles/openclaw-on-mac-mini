"""Unit tests for openclaw_cli_sessions.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import openclaw_cli_sessions as mod  # type: ignore

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all session file I/O to a temporary directory."""
    home = tmp_path / "openclaw_home"
    home.mkdir()
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# SessionSummary – construction and defaults
# ---------------------------------------------------------------------------


def test_session_summary_defaults():
    s = mod.SessionSummary(session_id="abc", title="T", cwd="/cwd")
    assert s.status == "active"
    assert s.command_count == 0
    assert s.repl_auto_route is True
    assert s.created_at != ""
    assert s.updated_at != ""


def test_session_summary_bookmarks_normalized():
    raw_bookmarks = [{"id": "b1", "label": "First"}]
    s = mod.SessionSummary(session_id="x", title="T", cwd="/c", bookmarks=raw_bookmarks)
    assert isinstance(s.bookmarks, list)
    assert s.bookmarks[0]["id"] == "b1"


# ---------------------------------------------------------------------------
# _normalize_session_id
# ---------------------------------------------------------------------------


def test_normalize_session_id_strips_special_chars():
    result = mod._normalize_session_id("hello world!!")
    assert " " not in result
    assert "!" not in result
    assert result == "hello-world"


def test_normalize_session_id_raises_on_empty():
    with pytest.raises(ValueError):
        mod._normalize_session_id("")


def test_normalize_session_id_truncates_long():
    long_id = "a" * 200
    result = mod._normalize_session_id(long_id)
    assert len(result) <= 160


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert mod._slugify("Hello World") == "hello-world"


def test_slugify_default_on_empty():
    assert mod._slugify("") == "session"
    assert mod._slugify("", default="x") == "x"


def test_slugify_truncates_to_40():
    result = mod._slugify("a" * 80)
    assert len(result) <= 40


# ---------------------------------------------------------------------------
# _short_summary
# ---------------------------------------------------------------------------


def test_short_summary_within_limit():
    text = "hello world"
    assert mod._short_summary(text, limit=50) == "hello world"


def test_short_summary_truncates_with_ellipsis():
    text = "a" * 200
    result = mod._short_summary(text, limit=mod.MAX_EVENT_SUMMARY_CHARS)
    assert result.endswith("…")
    assert len(result) <= mod.MAX_EVENT_SUMMARY_CHARS


def test_short_summary_collapses_whitespace():
    assert mod._short_summary("foo   bar\nbaz") == "foo bar baz"


# ---------------------------------------------------------------------------
# _normalize_session_bookmarks
# ---------------------------------------------------------------------------


def test_normalize_session_bookmarks_empty():
    assert mod._normalize_session_bookmarks([]) == []
    assert mod._normalize_session_bookmarks(None) == []  # type: ignore[arg-type]


def test_normalize_session_bookmarks_deduplicates():
    raw = [{"id": "b1", "label": "A"}, {"id": "b1", "label": "B"}]
    result = mod._normalize_session_bookmarks(raw)
    assert len(result) == 1


def test_normalize_session_bookmarks_respects_limit():
    raw = [{"id": f"b{i}", "label": f"Bookmark {i}"} for i in range(100)]
    result = mod._normalize_session_bookmarks(raw)
    assert len(result) <= mod.SESSION_BOOKMARK_LIMIT


def test_normalize_session_bookmarks_skips_non_dicts():
    raw = ["not_a_dict", {"id": "b1", "label": "ok"}]
    result = mod._normalize_session_bookmarks(raw)
    assert len(result) == 1
    assert result[0]["id"] == "b1"


# ---------------------------------------------------------------------------
# cli_data_root
# ---------------------------------------------------------------------------


def test_cli_data_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
    assert mod.cli_data_root() == tmp_path


def test_cli_data_root_darwin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENCLAW_CLI_HOME", raising=False)
    root = mod.cli_data_root(platform_name="darwin")
    assert "OpenClaw" in str(root) or "openclaw" in str(root).lower()


# ---------------------------------------------------------------------------
# create_session / save_session / load_session round-trip
# ---------------------------------------------------------------------------


def test_create_and_load_session(session_home: Path):
    summary = mod.create_session(title="Test Session", cwd=str(session_home))
    loaded = mod.load_session(summary.session_id)
    assert loaded is not None
    assert loaded.session_id == summary.session_id
    assert loaded.title == "Test Session"


def test_load_session_returns_none_for_missing(session_home: Path):
    result = mod.load_session("does-not-exist-xyz")
    assert result is None


def test_load_session_returns_none_for_corrupted_file(session_home: Path):
    summary = mod.create_session(title="Corrupt", cwd=str(session_home))
    metadata_path = mod._metadata_path(summary.session_id)
    metadata_path.write_text("not valid json", encoding="utf-8")
    result = mod.load_session(summary.session_id)
    assert result is None


# ---------------------------------------------------------------------------
# update_session
# ---------------------------------------------------------------------------


def test_update_session_changes_field(session_home: Path):
    summary = mod.create_session(title="Old Title", cwd=str(session_home))
    updated = mod.update_session(summary.session_id, title="New Title")
    assert updated.title == "New Title"
    reloaded = mod.load_session(summary.session_id)
    assert reloaded is not None
    assert reloaded.title == "New Title"


# ---------------------------------------------------------------------------
# append_event / load_events
# ---------------------------------------------------------------------------


def test_append_and_load_events(session_home: Path):
    summary = mod.create_session(title="Events Test", cwd=str(session_home))
    mod.append_event(summary.session_id, kind="chat", content="hello")
    mod.append_event(summary.session_id, kind="assistant", content="world")
    events = mod.load_events(summary.session_id)
    assert len(events) == 2
    assert events[0]["kind"] == "chat"
    assert events[1]["kind"] == "assistant"


def test_load_events_limit(session_home: Path):
    summary = mod.create_session(title="Limit Test", cwd=str(session_home))
    for i in range(10):
        mod.append_event(summary.session_id, kind="chat", content=f"msg {i}")
    events = mod.load_events(summary.session_id, limit=3)
    assert len(events) == 3


def test_append_event_increments_command_count(session_home: Path):
    summary = mod.create_session(title="Counter", cwd=str(session_home))
    mod.append_event(summary.session_id, kind="chat", content="do something")
    reloaded = mod.load_session(summary.session_id)
    assert reloaded is not None
    assert reloaded.command_count == 1


def test_load_events_empty_for_missing_session(session_home: Path):
    events = mod.load_events("no-such-session-here")
    assert events == []


# ---------------------------------------------------------------------------
# require_session
# ---------------------------------------------------------------------------


def test_require_session_raises_for_missing(session_home: Path):
    with pytest.raises((FileNotFoundError, Exception)):
        mod.require_session("missing-session-id")


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def test_list_sessions_returns_created_sessions(session_home: Path):
    mod.create_session(title="Session A", cwd=str(session_home))
    mod.create_session(title="Session B", cwd=str(session_home))
    sessions = mod.list_sessions()
    assert len(sessions) >= 2
    titles = [s.title for s in sessions]
    assert "Session A" in titles
    assert "Session B" in titles


# ---------------------------------------------------------------------------
# _normalize_watch_interventions
# ---------------------------------------------------------------------------


def test_normalize_watch_interventions_filters_non_dicts():
    state: dict = {"interventions": ["bad", {"ok": True}, 42]}
    result = mod._normalize_watch_interventions(state)
    assert all(isinstance(item, dict) for item in result["interventions"])


def test_normalize_watch_interventions_sets_flags():
    state: dict = {}
    result = mod._normalize_watch_interventions(state)
    assert result["force_run_once"] is False
    assert result["stop_requested"] is False
