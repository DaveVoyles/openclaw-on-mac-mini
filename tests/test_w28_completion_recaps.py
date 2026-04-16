"""tests/test_w28_completion_recaps.py — Wave 28, Lane 2: completion recaps & _preview_panel."""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — word count / source count logic mirrored from cmd_core
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


def _source_count(text: str) -> int:
    import re
    found = set(re.findall(r'\[\d+\]', text))
    return len(found) if found else text.count("http")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cmd_core_mod():
    """Import openclaw_cli_cmd_core with openclaw_cli monkeypatched to avoid real I/O."""
    import openclaw_cli_cmd_core as mod
    return mod


@pytest.fixture()
def ui_utils_mod():
    """Import openclaw_cli_ui_utils."""
    import openclaw_cli_ui_utils as mod
    return mod


# ---------------------------------------------------------------------------
# _preview_panel — unit tests (no LLM deps)
# ---------------------------------------------------------------------------

class TestPreviewPanel:
    def test_plain_text_prints_title_header(self, ui_utils_mod, capsys):
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", False),
            patch.object(ui_utils_mod, "_IS_TTY", False),
        ):
            ui_utils_mod._preview_panel("My Title", "line one\nline two")
        out = capsys.readouterr().out
        assert "[My Title]" in out

    def test_plain_text_prints_content_lines(self, ui_utils_mod, capsys):
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", False),
            patch.object(ui_utils_mod, "_IS_TTY", False),
        ):
            ui_utils_mod._preview_panel("T", "alpha\nbeta\ngamma")
        out = capsys.readouterr().out
        assert "  alpha" in out
        assert "  beta" in out

    def test_plain_text_truncates_to_max_lines(self, ui_utils_mod, capsys):
        content = "\n".join(f"line{i}" for i in range(20))
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", False),
            patch.object(ui_utils_mod, "_IS_TTY", False),
        ):
            ui_utils_mod._preview_panel("T", content, max_lines=5)
        out = capsys.readouterr().out
        assert "line4" in out
        assert "line5" not in out

    def test_plain_text_empty_content_no_crash(self, ui_utils_mod, capsys):
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", False),
            patch.object(ui_utils_mod, "_IS_TTY", False),
        ):
            ui_utils_mod._preview_panel("Empty", "")
        out = capsys.readouterr().out
        assert "[Empty]" in out

    def test_rich_available_calls_console_print(self, ui_utils_mod):
        mock_console = MagicMock()
        mock_panel_cls = MagicMock(return_value=MagicMock())
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", True),
            patch.object(ui_utils_mod, "_IS_TTY", True),
            patch.object(ui_utils_mod, "_RICH_CONSOLE", mock_console),
            patch.object(ui_utils_mod, "_RichPanel", mock_panel_cls),
        ):
            ui_utils_mod._preview_panel("Title", "content here")
        mock_console.print.assert_called_once()

    def test_rich_panel_receives_truncated_body(self, ui_utils_mod):
        mock_console = MagicMock()
        captured_body = []

        def fake_panel(body, **kwargs):
            captured_body.append(body)
            return MagicMock()

        content = "\n".join(f"L{i}" for i in range(20))
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", True),
            patch.object(ui_utils_mod, "_IS_TTY", True),
            patch.object(ui_utils_mod, "_RICH_CONSOLE", mock_console),
            patch.object(ui_utils_mod, "_RichPanel", fake_panel),
        ):
            ui_utils_mod._preview_panel("T", content, max_lines=3)
        assert captured_body[0] == "L0\nL1\nL2"

    def test_rich_panel_passes_width(self, ui_utils_mod):
        mock_console = MagicMock()
        captured_kwargs: dict = {}

        def fake_panel(body, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", True),
            patch.object(ui_utils_mod, "_IS_TTY", True),
            patch.object(ui_utils_mod, "_RICH_CONSOLE", mock_console),
            patch.object(ui_utils_mod, "_RichPanel", fake_panel),
        ):
            ui_utils_mod._preview_panel("T", "content", width=60)
        assert captured_kwargs.get("width") == 60

    def test_rich_no_width_arg_not_passed(self, ui_utils_mod):
        mock_console = MagicMock()
        captured_kwargs: dict = {}

        def fake_panel(body, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock()

        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", True),
            patch.object(ui_utils_mod, "_IS_TTY", True),
            patch.object(ui_utils_mod, "_RICH_CONSOLE", mock_console),
            patch.object(ui_utils_mod, "_RichPanel", fake_panel),
        ):
            ui_utils_mod._preview_panel("T", "content")
        assert "width" not in captured_kwargs

    def test_default_max_lines_is_8(self, ui_utils_mod, capsys):
        content = "\n".join(f"row{i}" for i in range(12))
        with (
            patch.object(ui_utils_mod, "_RICH_AVAILABLE", False),
            patch.object(ui_utils_mod, "_IS_TTY", False),
        ):
            ui_utils_mod._preview_panel("T", content)
        out = capsys.readouterr().out
        assert "row7" in out
        assert "row8" not in out


# ---------------------------------------------------------------------------
# Word count helper
# ---------------------------------------------------------------------------

class TestWordCount:
    def test_single_word(self):
        assert _word_count("hello") == 1

    def test_multi_word(self):
        assert _word_count("hello world foo bar") == 4

    def test_empty_string(self):
        assert _word_count("") == 0

    def test_extra_whitespace(self):
        assert _word_count("  a  b  c  ") == 3


# ---------------------------------------------------------------------------
# Source count helper
# ---------------------------------------------------------------------------

class TestSourceCount:
    def test_numbered_citations_counted(self):
        text = "See [1] and [2] for details."
        assert _source_count(text) == 2

    def test_duplicate_citations_deduplicated(self):
        text = "[1] intro [1] repeated [2] extra"
        assert _source_count(text) == 2

    def test_no_citations_falls_back_to_http(self):
        text = "See https://example.com and http://other.org for more."
        assert _source_count(text) == 2

    def test_no_citations_no_http_returns_zero(self):
        text = "No citations here at all."
        assert _source_count(text) == 0


# ---------------------------------------------------------------------------
# _cmd_analyze recap (via stdout capture)
# ---------------------------------------------------------------------------

def _make_fake_response(text: str) -> MagicMock:
    r = MagicMock()
    r.response = text
    return r


def _make_mock_cli_mod(response_text: str) -> MagicMock:
    m = MagicMock()
    m._require_config_or_warn.return_value = MagicMock(output_json=False)
    m._require_session_or_warn.return_value = MagicMock(
        cwd="/tmp", files=[], session_id="s1", plan_id=None, task_id=None
    )
    m.collect_workspace_context.return_value = ("", "")
    m.bind_config_to_session.return_value = MagicMock(output_json=False)
    m.build_analysis_prompt.return_value = "prompt"
    m._with_spinner.return_value = _make_fake_response(response_text)
    m.print_response.return_value = None
    m.persist_response.return_value = None
    m.append_event.return_value = None
    m._set_command_result.return_value = None
    m._summarize_terminal_result.return_value = "summary"
    return m


def _make_analyze_ctx(goal: str = "test goal") -> MagicMock:
    ctx = MagicMock()
    ctx.args = goal
    ctx.history = []
    return ctx


class TestAnalyzeRecap:
    def test_recap_printed_when_response_nonempty(self, cmd_core_mod, capsys):
        mock_m = _make_mock_cli_mod("Hello world foo bar baz")
        ctx = _make_analyze_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_analyze(ctx)
        out = capsys.readouterr().out
        assert "✓ Analysis complete" in out
        assert "5 words" in out

    def test_recap_not_printed_when_response_empty(self, cmd_core_mod, capsys):
        mock_m = _make_mock_cli_mod("")
        ctx = _make_analyze_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_analyze(ctx)
        out = capsys.readouterr().out
        assert "✓ Analysis complete" not in out

    def test_recap_word_count_correct(self, cmd_core_mod, capsys):
        text = "one two three four five six seven eight nine ten"
        mock_m = _make_mock_cli_mod(text)
        ctx = _make_analyze_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_analyze(ctx)
        out = capsys.readouterr().out
        assert "10 words" in out


# ---------------------------------------------------------------------------
# _cmd_research recap
# ---------------------------------------------------------------------------

def _make_research_ctx(query: str = "test query") -> MagicMock:
    ctx = MagicMock()
    ctx.args = query
    ctx.history = []
    return ctx


def _make_research_mock_cli_mod(report: str) -> MagicMock:
    m = MagicMock()
    m._require_session_or_warn.return_value = MagicMock(
        cwd="/tmp", files=[], session_id="s1", plan_id=None, task_id=None
    )
    m.collect_workspace_context.return_value = ("", "")
    m._plan_task_context_snippet.return_value = ""
    m.run_async.return_value = report
    m.save_output.return_value = "outputs/research.md"
    m.output_name_from_title.return_value = "research.md"
    m.append_event.return_value = None
    m._print_meta_footer.return_value = None
    m._set_command_result.return_value = None
    m._LOG = MagicMock()
    return m


class TestResearchRecap:
    def _run_research(self, cmd_core_mod, report: str, capsys):
        mock_m = _make_research_mock_cli_mod(report)
        ctx = _make_research_ctx()
        fake_agent = MagicMock()
        fake_agent.run.return_value = MagicMock()

        fake_research_agent_module = types.ModuleType("research_agent")
        fake_research_agent_class = MagicMock(return_value=fake_agent)
        fake_research_agent_module.ResearchAgent = fake_research_agent_class

        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch.dict("sys.modules", {"research_agent": fake_research_agent_module}),
            patch("sys.stdout.isatty", return_value=False),
            patch.object(cmd_core_mod, "_IS_TTY", False),
        ):
            mock_m.run_async.return_value = report
            cmd_core_mod._cmd_research(ctx)
        return capsys.readouterr().out

    def test_recap_printed_when_report_nonempty(self, cmd_core_mod, capsys):
        out = self._run_research(cmd_core_mod, "alpha beta gamma", capsys)
        assert "✓ Research complete" in out

    def test_recap_not_printed_when_report_empty(self, cmd_core_mod, capsys):
        out = self._run_research(cmd_core_mod, "", capsys)
        assert "✓ Research complete" not in out

    def test_recap_includes_source_count_with_citations(self, cmd_core_mod, capsys):
        report = "Results [1] show that [2] there are [3] three sources."
        out = self._run_research(cmd_core_mod, report, capsys)
        assert "3 sources" in out

    def test_recap_shows_zero_sources_without_citations(self, cmd_core_mod, capsys):
        report = "No citations or links here at all whatsoever."
        out = self._run_research(cmd_core_mod, report, capsys)
        assert "0 sources" in out

    def test_recap_word_count_in_research(self, cmd_core_mod, capsys):
        report = "one two three four five"
        out = self._run_research(cmd_core_mod, report, capsys)
        assert "5 words" in out


# ---------------------------------------------------------------------------
# _cmd_write recap
# ---------------------------------------------------------------------------

def _make_write_ctx(task: str = "write a report") -> MagicMock:
    ctx = MagicMock()
    ctx.args = task
    ctx.history = []
    return ctx


def _make_write_mock_cli_mod(response_text: str) -> MagicMock:
    m = MagicMock()
    m._require_config_or_warn.return_value = MagicMock(output_json=False)
    m._require_session_or_warn.return_value = MagicMock(
        cwd="/tmp", files=[], session_id="s1", plan_id=None, task_id=None
    )
    m.collect_workspace_context.return_value = ("", "")
    m.bind_config_to_session.return_value = MagicMock(output_json=False)
    m.build_write_prompt.return_value = "prompt"
    m._with_spinner.return_value = _make_fake_response(response_text)
    m.persist_response.return_value = None
    m.save_output.return_value = "outputs/draft.md"
    m.output_name_from_title.return_value = "draft.md"
    m.append_event.return_value = None
    m._print_meta_footer.return_value = None
    m._set_command_result.return_value = None
    return m


class TestWriteRecap:
    def test_recap_printed_when_response_nonempty(self, cmd_core_mod, capsys):
        mock_m = _make_write_mock_cli_mod("Draft text here with some words")
        ctx = _make_write_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_write(ctx)
        out = capsys.readouterr().out
        assert "✓ Draft complete" in out

    def test_recap_not_printed_when_response_empty(self, cmd_core_mod, capsys):
        mock_m = _make_write_mock_cli_mod("")
        ctx = _make_write_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_write(ctx)
        out = capsys.readouterr().out
        assert "✓ Draft complete" not in out

    def test_recap_word_count_correct(self, cmd_core_mod, capsys):
        text = " ".join(["word"] * 42)
        mock_m = _make_write_mock_cli_mod(text)
        ctx = _make_write_ctx()
        with (
            patch.object(cmd_core_mod, "_get_cli_mod", return_value=mock_m),
            patch("openclaw_cli_cmd_core.load_conversation_history", return_value=[]),
            patch("sys.stdout.isatty", return_value=False),
        ):
            cmd_core_mod._cmd_write(ctx)
        out = capsys.readouterr().out
        assert "42 words" in out
