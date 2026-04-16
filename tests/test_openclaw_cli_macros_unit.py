"""Unit tests for openclaw_cli_macros helpers."""
from __future__ import annotations

from types import SimpleNamespace

from openclaw_cli_macros import (
    _history_command_texts,
    _print_macro_progress,
    _print_workflow_preview,
    _render_workflow_step,
    _workflow_store,
)

# ---------------------------------------------------------------------------
# _workflow_store
# ---------------------------------------------------------------------------

def test_workflow_store_initialises_macros_key():
    prefs: dict = {}
    store = _workflow_store(prefs)
    assert "macros" in prefs
    assert isinstance(store, dict)


def test_workflow_store_returns_existing():
    prefs = {"macros": {"greet": ["echo hi"]}}
    store = _workflow_store(prefs)
    assert store == {"greet": ["echo hi"]}


def test_workflow_store_resets_non_dict():
    prefs = {"macros": "bad value"}
    store = _workflow_store(prefs)
    assert store == {}
    assert prefs["macros"] == {}


# ---------------------------------------------------------------------------
# _history_command_texts
# ---------------------------------------------------------------------------

def test_history_command_texts_string_entries():
    prefs = {"cmd_history": ["ls", "pwd", "echo hello"]}
    result = _history_command_texts(prefs, limit=2)
    assert result == ["pwd", "echo hello"]


def test_history_command_texts_dict_entries():
    prefs = {"cmd_history": [{"text": "git status"}, {"cmd": "make test"}]}
    result = _history_command_texts(prefs, limit=10)
    assert "git status" in result
    assert "make test" in result


def test_history_command_texts_empty_prefs():
    result = _history_command_texts({}, limit=5)
    assert result == []


def test_history_command_texts_skips_blank():
    prefs = {"cmd_history": ["", "   ", "valid"]}
    result = _history_command_texts(prefs, limit=5)
    assert result == ["valid"]


def test_history_command_texts_limit_at_least_one():
    prefs = {"cmd_history": ["a", "b", "c"]}
    result = _history_command_texts(prefs, limit=0)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _render_workflow_step
# ---------------------------------------------------------------------------

def test_render_workflow_step_no_session():
    ctx = SimpleNamespace(session_id=None)
    result = _render_workflow_step("echo {session}", ctx)
    assert result == "echo "


def test_render_workflow_step_no_placeholders():
    ctx = SimpleNamespace(session_id=None)
    result = _render_workflow_step("echo hello", ctx)
    assert result == "echo hello"


def test_render_workflow_step_empty_command():
    ctx = SimpleNamespace(session_id=None)
    assert _render_workflow_step("", ctx) == ""


# ---------------------------------------------------------------------------
# _print_workflow_preview (output captured)
# ---------------------------------------------------------------------------

def test_print_workflow_preview_output(capsys):
    ctx = SimpleNamespace(session_id=None)
    _print_workflow_preview("deploy", ["echo hi", "ls"], ctx)
    captured = capsys.readouterr()
    assert "deploy" in captured.out
    assert "echo hi" in captured.out


# ---------------------------------------------------------------------------
# _print_macro_progress
# ---------------------------------------------------------------------------

def test_print_macro_progress_a11y_plain_skips(capsys):
    _print_macro_progress(["step1", "step2"], 0, set(), a11y_plain=True)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_print_macro_progress_marks_done(capsys):
    _print_macro_progress(["step1", "step2"], 1, {0}, a11y_plain=False)
    captured = capsys.readouterr()
    assert "step1" in captured.out
    assert "step2" in captured.out


def test_print_macro_progress_current_step(capsys):
    _print_macro_progress(["step1", "step2", "step3"], 1, {0}, a11y_plain=False)
    captured = capsys.readouterr()
    assert "Step 2/3" in captured.out
