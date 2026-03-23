"""
Tests for analyzer.py — _basic_analysis (pure logic) and analyze_logs.

analyze_logs is tested with mocked subprocess and LLM calls to avoid
requiring a running Docker daemon or Gemini API key.
"""

import types
import pytest
from unittest.mock import patch, AsyncMock

from analyzer import _basic_analysis, analyze_logs


# ---------------------------------------------------------------------------
# _basic_analysis — pure pattern-matching, no external calls
# ---------------------------------------------------------------------------


class TestBasicAnalysis:
    def test_identifies_errors(self):
        log_text = "2024-01-01 INFO: Service started\n2024-01-01 ERROR: Database connection failed"
        result = _basic_analysis("myservice", log_text)
        assert "🔴" in result or "Errors" in result
        assert "Database connection failed" in result

    def test_identifies_warnings(self):
        log_text = "INFO: all good\nWARN: disk usage at 85%"
        result = _basic_analysis("myservice", log_text)
        assert "🟡" in result or "Warning" in result
        assert "disk usage at 85%" in result

    def test_identifies_fatal(self):
        log_text = "fatal: out of memory"
        result = _basic_analysis("myservice", log_text)
        assert "🔴" in result or "Errors" in result

    def test_identifies_exception(self):
        log_text = "traceback: exception in handler"
        result = _basic_analysis("myservice", log_text)
        assert "🔴" in result or "Errors" in result

    def test_identifies_panic(self):
        log_text = "panic: nil pointer dereference"
        result = _basic_analysis("myservice", log_text)
        assert "🔴" in result or "Errors" in result

    def test_identifies_deprecated(self):
        log_text = "deprecated: old API endpoint called"
        result = _basic_analysis("myservice", log_text)
        assert "🟡" in result or "Warning" in result

    def test_clean_logs_no_errors_or_warnings(self):
        log_text = "INFO: starting\nINFO: ready\nINFO: serving requests"
        result = _basic_analysis("myservice", log_text)
        # No error/warning sections should appear
        assert "🔴" not in result
        assert "🟡" not in result
        assert "✅" in result or "no issues" in result.lower() or "0" in result

    def test_shows_line_count(self):
        log_text = "line one\nline two\nline three"
        result = _basic_analysis("myservice", log_text)
        assert "3" in result  # 3 lines

    def test_includes_service_name(self):
        result = _basic_analysis("sonarr", "normal log line")
        assert "sonarr" in result

    def test_limits_errors_to_five_displayed(self):
        errors = "\n".join(f"ERROR: issue {i}" for i in range(10))
        result = _basic_analysis("service", errors)
        # Should show at most 5 error snippets
        error_lines = [l for l in result.split("\n") if "issue" in l]
        assert len(error_lines) <= 5

    def test_limits_warnings_to_five_displayed(self):
        warns = "\n".join(f"WARN: warning {i}" for i in range(10))
        result = _basic_analysis("service", warns)
        warn_lines = [l for l in result.split("\n") if "warning" in l]
        assert len(warn_lines) <= 5

    def test_case_insensitive_error_detection(self):
        log_text = "Error: something bad\nerror: lowercase too"
        result = _basic_analysis("service", log_text)
        assert "🔴" in result or "Errors" in result

    def test_empty_log_text(self):
        result = _basic_analysis("service", "")
        # Should not crash; should produce some output
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# analyze_logs — integration with mocked subprocess + LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAnalyzeLogs:
    async def test_returns_analysis_string(self):
        sample_logs = "2024-01-01T10:00:00 INFO: service started\n2024-01-01T10:00:01 ERROR: db timeout"
        mock_llm = types.SimpleNamespace(
            is_configured=lambda: False,
            chat=AsyncMock(return_value=("analysis", [])),
        )
        with patch("analyzer._run", new=AsyncMock(return_value=(0, sample_logs, ""))):
            with patch.dict("sys.modules", {"llm": mock_llm}):
                result = await analyze_logs("sonarr", lines=20)
                assert isinstance(result, str)
                assert len(result) > 0

    async def test_returns_error_on_docker_failure(self):
        with patch("analyzer._run", new=AsyncMock(return_value=(1, "", "No such container: xyz"))):
            result = await analyze_logs("xyz", lines=20)
            assert "❌" in result
            assert "xyz" in result

    async def test_returns_empty_log_message_on_no_output(self):
        with patch("analyzer._run", new=AsyncMock(return_value=(0, "", ""))):
            result = await analyze_logs("emptyservice", lines=20)
            assert "✅" in result or "empty" in result.lower()

    async def test_clamps_lines_to_minimum_10(self):
        """Lines below 10 should be clamped up to 10."""
        captured_cmd = []

        async def fake_run(cmd, timeout=15):
            captured_cmd.extend(cmd)
            return (0, "INFO: ok", "")

        mock_llm = types.SimpleNamespace(is_configured=lambda: False)
        with patch("analyzer._run", new=fake_run):
            with patch.dict("sys.modules", {"llm": mock_llm}):
                await analyze_logs("sonarr", lines=2)
                # The --tail argument should be at least 10
                tail_idx = captured_cmd.index("--tail")
                assert int(captured_cmd[tail_idx + 1]) >= 10

    async def test_clamps_lines_to_maximum_200(self):
        """Lines above 200 should be clamped down to 200."""
        captured_cmd = []

        async def fake_run(cmd, timeout=15):
            captured_cmd.extend(cmd)
            return (0, "INFO: ok", "")

        mock_llm = types.SimpleNamespace(is_configured=lambda: False)
        with patch("analyzer._run", new=fake_run):
            with patch.dict("sys.modules", {"llm": mock_llm}):
                await analyze_logs("sonarr", lines=9999)
                tail_idx = captured_cmd.index("--tail")
                assert int(captured_cmd[tail_idx + 1]) <= 200

    async def test_uses_llm_when_configured(self):
        sample_logs = "INFO: all good"
        llm_response = "No issues found in the logs."

        mock_llm = types.SimpleNamespace(
            is_configured=lambda: True,
            chat=AsyncMock(return_value=(llm_response, [])),
        )

        with patch("analyzer._run", new=AsyncMock(return_value=(0, sample_logs, ""))):
            with patch.dict("sys.modules", {"llm": mock_llm}):
                result = await analyze_logs("sonarr", lines=20)
                assert result == llm_response

    async def test_falls_back_to_basic_on_llm_exception(self):
        sample_logs = "ERROR: something crashed"

        mock_llm = types.SimpleNamespace(
            is_configured=lambda: True,
            chat=AsyncMock(side_effect=RuntimeError("LLM unavailable")),
        )

        with patch("analyzer._run", new=AsyncMock(return_value=(0, sample_logs, ""))):
            with patch.dict("sys.modules", {"llm": mock_llm}):
                result = await analyze_logs("sonarr", lines=20)
                # Falls back to _basic_analysis which includes error patterns
                assert isinstance(result, str)
                assert len(result) > 0
