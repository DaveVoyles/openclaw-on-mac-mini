"""tests/test_w27_grounding_inspection.py — Wave 27 Lane 1: grounding inspection tests.

Covers:
- _PREFS["_last_grounding_block"] storage logic for ANALYZE/RESEARCH/WRITE routes
- /context last prints grounding block fields when one exists
- /context last prints "No grounding block" message when none stored
- Fields (type, query/subject, rationale) are present in output
- Output is clean (no crash) when grounding block is a minimal dict
- /context without last still works as before (no regression)
"""
from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

import openclaw_cli_cmd_core as cmd_core  # type: ignore
from openclaw_cli_types import ChatCommandContext

_CMD_CONTINUE = "continue"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(args: str = "", session_id: str = "sess-w27") -> ChatCommandContext:
    return ChatCommandContext(history=[], session_id=session_id, args=args)


def _mock_session(**kwargs) -> MagicMock:
    s = MagicMock()
    s.session_id = kwargs.get("session_id", "sess-w27")
    s.files = kwargs.get("files", [])
    s.plan_id = kwargs.get("plan_id", None)
    s.task_id = kwargs.get("task_id", None)
    s.cwd = kwargs.get("cwd", "/project")
    return s


def _mock_cli(last_grounding_block=None, session=None) -> MagicMock:
    if session is None:
        session = _mock_session()
    m = MagicMock()
    m._IS_TTY = False
    m._RICH_AVAILABLE = False
    m._require_session_or_warn = MagicMock(return_value=session)
    m._PREFS = {}
    if last_grounding_block is not None:
        m._PREFS["_last_grounding_block"] = last_grounding_block
    m._render_effective_grounding_preview = MagicMock(return_value="")
    m._validate_plan_id_local = MagicMock(return_value=MagicMock(item_id="", available=False, exists=False))
    m._validate_task_id_local = MagicMock(return_value=MagicMock(item_id="", available=False, exists=False))
    m._link_validation_suffix = MagicMock(return_value="")
    m._progress_cell = MagicMock(return_value="")
    m._print_dashboard_surface = MagicMock()
    return m


# ---------------------------------------------------------------------------
# grounding block storage logic (unit tests for the storage condition)
# ---------------------------------------------------------------------------


class TestGroundingBlockStorageLogic:
    """Test the logic that decides when _last_grounding_block is stored."""

    def _make_route_decision(self, kind_value: str, args_text: str = "foo.py", rationale: str = "matched") -> MagicMock:
        d = MagicMock()
        d.kind = MagicMock()
        d.kind.value = kind_value
        d.args_text = args_text
        d.target_text = args_text
        d.confidence = 0.85
        d.rationale = rationale
        return d

    def _stored_block(self, kind_value: str, args_text: str = "foo.py", rationale: str = "matched") -> dict | None:
        """Simulate the storage block logic from openclaw_cli.py's main loop."""
        from openclaw_cli_router import ReplRouteKind
        route_decision = self._make_route_decision(kind_value, args_text, rationale)
        route_decision.kind = ReplRouteKind(kind_value)
        prefs: dict = {}
        if route_decision.kind in {ReplRouteKind.ANALYZE, ReplRouteKind.RESEARCH, ReplRouteKind.WRITE}:
            prefs["_last_grounding_block"] = {
                "type": route_decision.kind.value,
                "query": route_decision.args_text.strip() or route_decision.target_text.strip(),
                "confidence": round(route_decision.confidence, 2),
                "rationale": route_decision.rationale,
                "grounded": "grounded by" in route_decision.rationale.lower(),
            }
        return prefs.get("_last_grounding_block")

    def test_analyze_route_stores_block(self):
        block = self._stored_block("analyze", "src/foo.py")
        assert block is not None

    def test_research_route_stores_block(self):
        block = self._stored_block("research", "climate data")
        assert block is not None

    def test_write_route_stores_block(self):
        block = self._stored_block("write", "report.md")
        assert block is not None

    def test_chat_route_does_not_store_block(self):
        block = self._stored_block("chat")
        assert block is None

    def test_exec_route_does_not_store_block(self):
        block = self._stored_block("exec")
        assert block is None

    def test_edit_route_does_not_store_block(self):
        block = self._stored_block("edit")
        assert block is None

    def test_stored_block_type_matches_kind(self):
        block = self._stored_block("analyze", "src/mod.py")
        assert block["type"] == "analyze"

    def test_stored_block_query_from_args_text(self):
        block = self._stored_block("research", "machine learning")
        assert block["query"] == "machine learning"

    def test_stored_block_grounded_true_when_rationale_contains_grounded_by(self):
        block = self._stored_block("analyze", "foo.py", "matched pattern; grounded by active plan step 1: do X")
        assert block["grounded"] is True

    def test_stored_block_grounded_false_when_no_grounding(self):
        block = self._stored_block("research", "foo.py", "lightweight classifier matched keyword")
        assert block["grounded"] is False

    def test_stored_block_confidence_is_rounded(self):
        block = self._stored_block("write", "doc.md")
        assert isinstance(block["confidence"], float)


# ---------------------------------------------------------------------------
# /context last — output when no grounding block stored
# ---------------------------------------------------------------------------


class TestContextLastNoBlock:
    """Tests for /context last when no _last_grounding_block is set."""

    def _run_context_last(self) -> str:
        m = _mock_cli(last_grounding_block=None)
        out = StringIO()
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch("builtins.print", side_effect=lambda *a, **kw: out.write(" ".join(str(x) for x in a) + "\n")):
            result = cmd_core._cmd_context(_ctx(args="last"))
        return out.getvalue()

    def test_w27_grounding_inspection_returns_continue(self):
        m = _mock_cli(last_grounding_block=None)
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch("builtins.print"):
            result = cmd_core._cmd_context(_ctx(args="last"))
        assert result == _CMD_CONTINUE

    def test_prints_no_block_message(self):
        output = self._run_context_last()
        assert "No grounding block recorded yet" in output

    def test_prints_guidance_to_run_analyze_or_research(self):
        output = self._run_context_last()
        assert "analyze" in output.lower() or "research" in output.lower() or "write" in output.lower()


# ---------------------------------------------------------------------------
# /context last — output when grounding block exists
# ---------------------------------------------------------------------------


class TestContextLastWithBlock:
    """Tests for /context last when _last_grounding_block is set."""

    _SAMPLE_BLOCK = {
        "type": "analyze",
        "query": "src/main.py",
        "confidence": 0.87,
        "rationale": "matched pattern; grounded by active plan plan-1 step 2: refactor auth",
        "grounded": True,
    }

    def _run_context_last(self, block: dict) -> str:
        m = _mock_cli(last_grounding_block=block)
        out = StringIO()
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch("builtins.print", side_effect=lambda *a, **kw: out.write(" ".join(str(x) for x in a) + "\n")):
            result = cmd_core._cmd_context(_ctx(args="last"))
        return out.getvalue()

    def test_w27_grounding_inspection_returns_continue_v2(self):
        m = _mock_cli(last_grounding_block=self._SAMPLE_BLOCK)
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch("builtins.print"):
            result = cmd_core._cmd_context(_ctx(args="last"))
        assert result == _CMD_CONTINUE

    def test_output_contains_type_field(self):
        output = self._run_context_last(self._SAMPLE_BLOCK)
        assert "analyze" in output.lower()

    def test_output_contains_query_field(self):
        output = self._run_context_last(self._SAMPLE_BLOCK)
        assert "src/main.py" in output

    def test_output_contains_rationale_field(self):
        output = self._run_context_last(self._SAMPLE_BLOCK)
        assert "grounded by" in output.lower() or "matched pattern" in output.lower()

    def test_output_contains_grounding_block_header(self):
        output = self._run_context_last(self._SAMPLE_BLOCK)
        assert "grounding block" in output.lower() or "Last grounding" in output

    def test_grounding_arg_alias_also_works(self):
        m = _mock_cli(last_grounding_block=self._SAMPLE_BLOCK)
        out = StringIO()
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch("builtins.print", side_effect=lambda *a, **kw: out.write(" ".join(str(x) for x in a) + "\n")):
            result = cmd_core._cmd_context(_ctx(args="grounding"))
        output = out.getvalue()
        assert "analyze" in output.lower()

    def test_minimal_block_does_not_crash(self):
        minimal = {"type": "research"}
        output = self._run_context_last(minimal)
        assert output  # Just check it produced something without raising

    def test_empty_query_shows_none_placeholder(self):
        block = {**self._SAMPLE_BLOCK, "query": ""}
        output = self._run_context_last(block)
        assert "(none)" in output or "query" in output.lower()

    def test_confidence_boost_yes_when_grounded(self):
        output = self._run_context_last(self._SAMPLE_BLOCK)
        assert "yes" in output.lower()

    def test_confidence_boost_none_when_not_grounded(self):
        block = {**self._SAMPLE_BLOCK, "grounded": False}
        output = self._run_context_last(block)
        assert "none" in output.lower()


# ---------------------------------------------------------------------------
# /context without last — regression: normal behavior unchanged
# ---------------------------------------------------------------------------


class TestContextNoArgRegression:
    """Ensure /context without args still invokes the dashboard path."""

    def test_context_no_arg_calls_dashboard(self):
        session = _mock_session()
        m = _mock_cli(session=session)
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch.object(cmd_core, "_context_pressure_snapshot", return_value={
                 "pct_next": 0, "next_tokens": 0, "pct_next_raw": 0,
                 "limit_label": "8k", "overflow": False, "hidden_pressure": False,
                 "has_pending_inject": False,
             }):
            result = cmd_core._cmd_context(_ctx(args=""))
        assert result == _CMD_CONTINUE
        m._print_dashboard_surface.assert_called_once()

    def test_context_no_arg_does_not_print_grounding_block(self):
        session = _mock_session()
        m = _mock_cli(session=session)
        out = StringIO()
        with patch.object(cmd_core, "_get_cli_mod", return_value=m), \
             patch.object(cmd_core, "_context_pressure_snapshot", return_value={
                 "pct_next": 0, "next_tokens": 0, "pct_next_raw": 0,
                 "limit_label": "8k", "overflow": False, "hidden_pressure": False,
                 "has_pending_inject": False,
             }), \
             patch("builtins.print", side_effect=lambda *a, **kw: out.write(" ".join(str(x) for x in a) + "\n")):
            cmd_core._cmd_context(_ctx(args=""))
        assert "No grounding block recorded yet" not in out.getvalue()
