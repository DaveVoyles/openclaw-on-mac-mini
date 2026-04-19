"""Unit tests for openclaw_cli_cmd_misc.py — miscellaneous command handlers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import openclaw_cli_cmd_misc as mod  # type: ignore
from openclaw_cli_types import ChatCommandContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CMD_CONTINUE = "continue"


def _ctx(args: str = "", session_id: str = "sess-1") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_cli(**kwargs) -> MagicMock:
    """Create a minimal mock of the main openclaw_cli module."""
    m = MagicMock()
    m._PREFS = kwargs.get("_PREFS", {})
    m._last_response_text = kwargs.get("_last_response_text", "")
    m._next_inject = None
    m._IS_TTY = False
    m._RICH_AVAILABLE = False
    m._OPENCLAW_TIPS = kwargs.get("_OPENCLAW_TIPS", ["Use /help for assistance."])
    m._a11y_plain_mode = MagicMock(return_value=False)
    m._save_prefs = MagicMock()
    m._last_trace_snapshot = MagicMock(return_value=None)
    m._render_diff_ansi = MagicMock(return_value="")
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


# ---------------------------------------------------------------------------
# _cmd_recall
# ---------------------------------------------------------------------------


def test_cmd_recall_no_history_shows_message(capsys):
    cli = _mock_cli(_PREFS={"cmd_history": []})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_recall(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No prompt history" in captured.out


def test_cmd_recall_lists_prompts_when_no_arg(capsys):
    history = [{"text": "write a test"}, {"text": "explain this"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_recall(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "write a test" in captured.out or "explain this" in captured.out


def test_cmd_recall_injects_prompt_by_index(capsys):
    history = [{"text": "first prompt"}, {"text": "second prompt"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_recall(_ctx("1"))
    assert result == _CMD_CONTINUE
    # The most recent prompt is index 1 (reversed)
    assert cli._next_inject is not None


def test_cmd_recall_out_of_range(capsys):
    history = [{"text": "only prompt"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_recall(_ctx("99"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No prompt #99" in captured.out


def test_cmd_recall_skips_slash_commands():
    history = [{"text": "/help"}, {"text": "real prompt"}, {"text": "/clear"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        mod._cmd_recall(_ctx("1"))
    # Should inject "real prompt", not a slash command
    assert cli._next_inject == "real prompt"


# ---------------------------------------------------------------------------
# _cmd_histsearch
# ---------------------------------------------------------------------------


def test_cmd_histsearch_no_query(capsys):
    cli = _mock_cli(_PREFS={})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_histsearch(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_cmd_histsearch_finds_match(capsys):
    history = [{"text": "write unit tests"}, {"text": "explain code"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_histsearch(_ctx("unit"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "unit" in captured.out.lower()


def test_cmd_histsearch_no_match(capsys):
    history = [{"text": "write code"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_histsearch(_ctx("nonexistent"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No history matches" in captured.out


def test_cmd_histsearch_case_insensitive(capsys):
    history = [{"text": "Write a Test", "timestamp": "2024-01-01T10:00:00"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_histsearch(_ctx("write"))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Write" in captured.out


# ---------------------------------------------------------------------------
# _cmd_rate
# ---------------------------------------------------------------------------


def test_cmd_rate_no_args():
    cli = _mock_cli()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        with patch.object(mod, "_print_error") as mock_err:
            result = mod._cmd_rate(_ctx(""))
    assert result == _CMD_CONTINUE
    mock_err.assert_called_once()
    assert "Usage" in mock_err.call_args[0][0]


def test_cmd_rate_unknown_rating(capsys):
    cli = _mock_cli(_last_response_text="some response")
    cli._print_error = MagicMock()
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        with patch.object(mod, "_print_error") as mock_err:
            result = mod._cmd_rate(_ctx("excellent"))
    assert result == _CMD_CONTINUE


def test_cmd_rate_good_saves_prefs():
    cli = _mock_cli(
        _PREFS={"ratings": []},
        _last_response_text="great response",
    )
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_rate(_ctx("good"))
    assert result == _CMD_CONTINUE
    cli._save_prefs.assert_called_once()
    assert len(cli._PREFS["ratings"]) == 1
    assert cli._PREFS["ratings"][0]["score"] == 5


def test_cmd_rate_numeric_1_maps_to_bad():
    cli = _mock_cli(
        _PREFS={"ratings": []},
        _last_response_text="poor response",
    )
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_rate(_ctx("1"))
    assert result == _CMD_CONTINUE
    assert cli._PREFS["ratings"][0]["label"] == "bad"


def test_cmd_rate_no_response_text(capsys):
    cli = _mock_cli(_last_response_text="")
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        with patch.object(mod, "_print_error") as mock_err:
            result = mod._cmd_rate(_ctx("good"))
    assert result == _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_freq
# ---------------------------------------------------------------------------


def test_cmd_freq_no_history(capsys):
    cli = _mock_cli(_PREFS={"cmd_history": []})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_freq(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No slash command history" in captured.out


def test_cmd_freq_with_slash_commands(capsys):
    history = [{"text": "/help"}, {"text": "/help"}, {"text": "/clear"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_freq(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "/help" in captured.out


def test_cmd_freq_ignores_non_slash_entries(capsys):
    history = [{"text": "regular prompt"}, {"text": "/help"}]
    cli = _mock_cli(_PREFS={"cmd_history": history})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_freq(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "/help" in captured.out
    assert "regular prompt" not in captured.out


# ---------------------------------------------------------------------------
# _cmd_tip
# ---------------------------------------------------------------------------


def test_cmd_tip_prints_a_tip(capsys):
    cli = _mock_cli(_OPENCLAW_TIPS=["Use /help for more info."])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tip(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Use /help" in captured.out


def test_cmd_tip_returns_continue():
    cli = _mock_cli(_OPENCLAW_TIPS=["A tip."])
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tip(_ctx(""))
    assert result == _CMD_CONTINUE


# ---------------------------------------------------------------------------
# _cmd_shortcuts
# ---------------------------------------------------------------------------


def test_cmd_shortcuts_returns_continue(capsys):
    cli = _mock_cli()
    # Need _RichPanel available — mock it
    with patch.object(mod, "_get_cli_mod", return_value=cli), patch.object(mod, "_RICH_AVAILABLE", False):
        result = mod._cmd_shortcuts(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "Shortcuts" in captured.out or "Keyboard" in captured.out


# ---------------------------------------------------------------------------
# _cmd_changes
# ---------------------------------------------------------------------------


def test_cmd_changes_no_edits(capsys):
    cli = _mock_cli(_PREFS={"session_edits": []})
    with patch.object(mod, "_get_cli_mod", return_value=cli), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="")
        result = mod._cmd_changes(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "No session edits" in captured.out


def test_cmd_changes_lists_edits(capsys):
    cli = _mock_cli(_PREFS={"session_edits": ["src/main.py", "README.md"]})
    with patch.object(mod, "_get_cli_mod", return_value=cli), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="")
        result = mod._cmd_changes(_ctx(""))
    assert result == _CMD_CONTINUE
    captured = capsys.readouterr()
    assert "src/main.py" in captured.out


# ---------------------------------------------------------------------------
# _print_ascii_trophy
# ---------------------------------------------------------------------------


def test_print_ascii_trophy_plain_mode(capsys):
    cli = _mock_cli()
    cli._a11y_plain_mode = MagicMock(return_value=True)
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        mod._print_ascii_trophy(5)
    captured = capsys.readouterr()
    assert "streak" in captured.out.lower() or "5" in captured.out


def test_print_ascii_trophy_normal_mode(capsys):
    cli = _mock_cli()
    cli._a11y_plain_mode = MagicMock(return_value=False)
    with patch.object(mod, "_get_cli_mod", return_value=cli), patch.object(mod, "_RICH_AVAILABLE", False):
        mod._print_ascii_trophy(3)
    captured = capsys.readouterr()
    assert "Streak" in captured.out or "streak" in captured.out.lower()


# ---------------------------------------------------------------------------
# _cmd_tldr
# ---------------------------------------------------------------------------


def test_cmd_tldr_no_last_response(capsys):
    cli = _mock_cli(_last_response_text="")
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tldr(_ctx(""))
    assert result == _CMD_CONTINUE
    assert "No previous response" in capsys.readouterr().out


def test_cmd_tldr_sets_tldr_prompt_and_returns_special(capsys):
    cli = _mock_cli(_last_response_text="Some AI response text here.")
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_tldr(_ctx(""))
    assert result == "_tldr"
    assert "Summarize" in cli._PREFS["_tldr_prompt"]
    assert "Some AI response text here." in cli._PREFS["_tldr_prompt"]
    assert "3 concise bullet points" in cli._PREFS["_tldr_prompt"]


# ---------------------------------------------------------------------------
# _cmd_retry qualifiers
# ---------------------------------------------------------------------------


def test_cmd_retry_no_last_prompt(capsys):
    cli = _mock_cli(_PREFS={})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx(""))
    assert result == _CMD_CONTINUE
    assert "No previous prompt" in capsys.readouterr().out


def test_cmd_retry_no_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "what is python?"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx(""))
    assert result == "_retry"
    assert cli._PREFS["_retry_prompt"] == "what is python?"
    assert "retrying" in capsys.readouterr().out


def test_cmd_retry_shorter_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "what is python?"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("shorter"))
    assert result == "_retry"
    assert cli._PREFS["_retry_prompt"].startswith("Please give a shorter")
    assert "what is python?" in cli._PREFS["_retry_prompt"]
    assert "shorter" in capsys.readouterr().out


def test_cmd_retry_simpler_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "explain recursion"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("simpler"))
    assert result == "_retry"
    assert "simply" in cli._PREFS["_retry_prompt"]


def test_cmd_retry_code_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "reverse a list"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("code"))
    assert result == "_retry"
    assert "code only" in cli._PREFS["_retry_prompt"]


def test_cmd_retry_bullet_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "pros of python"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("bullet"))
    assert result == "_retry"
    assert "bullet points" in cli._PREFS["_retry_prompt"]


def test_cmd_retry_freetext_qualifier(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "explain async"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("explain step by step"))
    assert result == "_retry"
    assert "Please explain step by step" in cli._PREFS["_retry_prompt"]
    assert "explain async" in cli._PREFS["_retry_prompt"]


def test_cmd_retry_model_override(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "hello"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("--model copilot"))
    assert result == "_retry"
    assert cli._PREFS.get("_retry_model") == "copilot"
    assert cli._PREFS["_retry_prompt"] == "hello"


def test_cmd_retry_qualifier_and_model(capsys):
    cli = _mock_cli(_PREFS={"_last_prompt": "what is a closure?"})
    with patch.object(mod, "_get_cli_mod", return_value=cli):
        result = mod._cmd_retry(_ctx("shorter --model copilot"))
    assert result == "_retry"
    assert cli._PREFS.get("_retry_model") == "copilot"
    assert cli._PREFS["_retry_prompt"].startswith("Please give a shorter")
