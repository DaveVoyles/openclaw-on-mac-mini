"""Unit tests for openclaw_cli_router.py — pure routing logic."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import openclaw_cli_router as mod  # type: ignore


# ---------------------------------------------------------------------------
# ReplRouteDecision – should_auto_route
# ---------------------------------------------------------------------------


def _make_decision(
    kind: mod.ReplRouteKind,
    confidence: float = 0.8,
    args_text: str = "src/main.py",
    target_text: str = "src/main.py",
    steps: tuple = (),
) -> mod.ReplRouteDecision:
    return mod.ReplRouteDecision(
        kind=kind,
        confidence=confidence,
        target_text=target_text,
        args_text=args_text,
        rationale="test",
        steps=steps,
    )


def test_should_auto_route_edit_high_confidence():
    d = _make_decision(mod.ReplRouteKind.EDIT, confidence=0.9)
    assert d.should_auto_route() is True


def test_should_auto_route_chat_never():
    d = _make_decision(mod.ReplRouteKind.CHAT, confidence=1.0)
    assert d.should_auto_route() is False


def test_should_auto_route_plan_never():
    d = _make_decision(mod.ReplRouteKind.PLAN, confidence=1.0)
    assert d.should_auto_route() is False


def test_should_auto_route_below_threshold():
    d = _make_decision(mod.ReplRouteKind.EDIT, confidence=0.5)
    assert d.should_auto_route() is False


def test_should_auto_route_empty_args():
    d = _make_decision(mod.ReplRouteKind.ANALYZE, confidence=0.9, args_text="  ")
    assert d.should_auto_route() is False


# ---------------------------------------------------------------------------
# ReplRouteDecision – should_auto_execute_plan
# ---------------------------------------------------------------------------


def test_should_auto_execute_plan_with_two_steps():
    steps = (
        mod.ReplPlanStep(index=1, kind=mod.ReplRouteKind.ANALYZE, target_text="", args_text="", rationale=""),
        mod.ReplPlanStep(index=2, kind=mod.ReplRouteKind.WRITE, target_text="", args_text="", rationale=""),
    )
    d = _make_decision(mod.ReplRouteKind.PLAN, confidence=0.9, steps=steps)
    assert d.should_auto_execute_plan() is True


def test_should_auto_execute_plan_only_one_step():
    steps = (
        mod.ReplPlanStep(index=1, kind=mod.ReplRouteKind.ANALYZE, target_text="", args_text="", rationale=""),
    )
    d = _make_decision(mod.ReplRouteKind.PLAN, confidence=0.9, steps=steps)
    assert d.should_auto_execute_plan() is False


def test_should_auto_execute_plan_non_plan_kind():
    d = _make_decision(mod.ReplRouteKind.EDIT, confidence=0.9)
    assert d.should_auto_execute_plan() is False


# ---------------------------------------------------------------------------
# ReplRouteDecision – to_slash_command
# ---------------------------------------------------------------------------


def test_to_slash_command_edit():
    d = _make_decision(mod.ReplRouteKind.EDIT, args_text="src/foo.py")
    assert d.to_slash_command() == "/edit src/foo.py"


def test_to_slash_command_chat_returns_empty():
    d = _make_decision(mod.ReplRouteKind.CHAT, args_text="anything")
    assert d.to_slash_command() == ""


def test_to_slash_command_analyze():
    d = _make_decision(mod.ReplRouteKind.ANALYZE, args_text="src/")
    assert d.to_slash_command() == "/analyze src/"


# ---------------------------------------------------------------------------
# _normalize_prompt_text
# ---------------------------------------------------------------------------


def test_normalize_prompt_text_collapses_whitespace():
    assert mod._normalize_prompt_text("  hello   world\n") == "hello world"


def test_normalize_prompt_text_empty():
    assert mod._normalize_prompt_text("") == ""
    assert mod._normalize_prompt_text(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _looks_like_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected", [
    ("src/main.py", True),
    ("./config.json", True),
    ("~/Documents/file.txt", True),
    ("README.md", True),
    ("readme", True),
    ("Makefile", True),
    ("hello", False),
    ("", False),
    ("world", False),
])
def test_looks_like_path(token: str, expected: bool):
    assert mod._looks_like_path(token) is expected


# ---------------------------------------------------------------------------
# _extract_first_path
# ---------------------------------------------------------------------------


def test_extract_first_path_from_prompt():
    result = mod._extract_first_path("please edit `src/utils.py` to fix the bug")
    assert result == "src/utils.py"


def test_extract_first_path_none_when_no_path():
    result = mod._extract_first_path("hello world how are you")
    assert result == ""


# ---------------------------------------------------------------------------
# _first_shell_token
# ---------------------------------------------------------------------------


def test_first_shell_token_basic():
    assert mod._first_shell_token("git status") == "git"


def test_first_shell_token_empty():
    assert mod._first_shell_token("") == ""


def test_first_shell_token_quoted():
    assert mod._first_shell_token('"git status"') == "git status"


# ---------------------------------------------------------------------------
# lightweight_classify_repl_prompt
# ---------------------------------------------------------------------------


def test_lightweight_classify_edit_prompt():
    result = mod.lightweight_classify_repl_prompt("edit src/main.py to fix the login bug")
    assert result is not None
    assert result.kind == mod.ReplRouteKind.EDIT


def test_lightweight_classify_analyze_prompt():
    result = mod.lightweight_classify_repl_prompt("take a look at the repository structure")
    assert result is not None
    assert result.kind == mod.ReplRouteKind.ANALYZE


def test_lightweight_classify_write_prompt():
    result = mod.lightweight_classify_repl_prompt("draft a summary document for the project")
    assert result is not None
    assert result.kind == mod.ReplRouteKind.WRITE


def test_lightweight_classify_returns_none_for_weak_signal():
    # Very short / generic prompts shouldn't reach threshold
    result = mod.lightweight_classify_repl_prompt("hi")
    assert result is None


def test_lightweight_classify_empty_prompt():
    assert mod.lightweight_classify_repl_prompt("") is None


# ---------------------------------------------------------------------------
# _confidence_badge
# ---------------------------------------------------------------------------


def test_confidence_badge_high():
    badge = mod._confidence_badge(0.85)
    assert "HIGH" in badge


def test_confidence_badge_medium():
    badge = mod._confidence_badge(0.65)
    assert "MED" in badge


def test_confidence_badge_low():
    badge = mod._confidence_badge(0.3)
    assert "LOW" in badge


# ---------------------------------------------------------------------------
# _session_auto_route_enabled
# ---------------------------------------------------------------------------


def test_session_auto_route_enabled_empty_id():
    assert mod._session_auto_route_enabled("") is False


def test_session_auto_route_enabled_missing_session():
    with patch("openclaw_cli_router.load_session", return_value=None):
        assert mod._session_auto_route_enabled("nonexistent-id") is False


def test_session_auto_route_enabled_true():
    fake = MagicMock()
    fake.repl_auto_route = True
    with patch("openclaw_cli_router.load_session", return_value=fake):
        assert mod._session_auto_route_enabled("some-id") is True


def test_session_auto_route_enabled_false():
    fake = MagicMock()
    fake.repl_auto_route = False
    with patch("openclaw_cli_router.load_session", return_value=fake):
        assert mod._session_auto_route_enabled("some-id") is False


# ---------------------------------------------------------------------------
# _extract_created_plan_id
# ---------------------------------------------------------------------------


def test_extract_created_plan_id_found():
    text = "Created plan `my-plan-123`"
    assert mod._extract_created_plan_id(text) == "my-plan-123"


def test_extract_created_plan_id_not_found():
    assert mod._extract_created_plan_id("no plan here") == ""
    assert mod._extract_created_plan_id("") == ""


# ---------------------------------------------------------------------------
# route_repl_prompt – end-to-end (no session grounding)
# ---------------------------------------------------------------------------


def test_route_repl_prompt_empty_returns_chat():
    decision = mod.route_repl_prompt("")
    assert decision.kind == mod.ReplRouteKind.CHAT


def test_route_repl_prompt_chat_passthrough():
    decision = mod.route_repl_prompt("what is the capital of France?")
    assert decision.kind == mod.ReplRouteKind.CHAT


def test_route_repl_prompt_edit_route():
    decision = mod.route_repl_prompt(
        "edit src/main.py and replace the broken login function",
        min_confidence=0.5,
    )
    assert decision.kind in {mod.ReplRouteKind.EDIT, mod.ReplRouteKind.CHAT}


# ---------------------------------------------------------------------------
# _truncate_repl_route_text
# ---------------------------------------------------------------------------


def test_truncate_repl_route_text_within_limit():
    assert mod._truncate_repl_route_text("hello world", limit=50) == "hello world"


def test_truncate_repl_route_text_truncates():
    long_text = "a" * 100
    result = mod._truncate_repl_route_text(long_text, limit=20)
    assert result.endswith("…")
    assert len(result) <= 20
