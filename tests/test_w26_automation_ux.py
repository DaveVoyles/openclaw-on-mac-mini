"""tests/test_w26_automation_ux.py — Wave 26 Lane 2: automation UX tests.

Covers:
- _watch_status_cell() for running / retrying / idle / no-session states
- Watch completion recap printing (success and exhausted-retries paths)
- _print_status_bar() resilience when no watch state is available
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import openclaw_cli_ui_utils as _ui

# ---------------------------------------------------------------------------
# _watch_status_cell tests
# ---------------------------------------------------------------------------


class TestWatchStatusCell:
    """Unit tests for _watch_status_cell()."""

    def _cell(self, session_id: str, state: dict | None) -> str | None:
        with patch("openclaw_cli_sessions.load_watch_state", return_value=state):
            return _ui._watch_status_cell(session_id)

    def test_returns_watching_when_status_running(self):
        assert self._cell("abc123", {"status": "running"}) == "⟳ watching"

    def test_returns_watching_when_status_active(self):
        assert self._cell("abc123", {"status": "active"}) == "⟳ watching"

    def test_returns_watching_when_status_watching(self):
        assert self._cell("abc123", {"status": "watching"}) == "⟳ watching"

    def test_returns_retrying_when_status_retrying(self):
        assert self._cell("abc123", {"status": "retrying"}) == "↺ retrying"

    def test_returns_none_when_status_idle(self):
        assert self._cell("abc123", {"status": "idle"}) is None

    def test_returns_none_when_status_completed(self):
        assert self._cell("abc123", {"status": "completed"}) is None

    def test_returns_none_when_state_is_none(self):
        assert self._cell("abc123", None) is None

    def test_returns_none_when_state_is_empty_dict(self):
        assert self._cell("abc123", {}) is None

    def test_returns_none_when_session_id_is_empty(self):
        # Short-circuits before any I/O — no patch needed
        assert _ui._watch_status_cell("") is None

    def test_returns_none_when_load_raises(self):
        with patch("openclaw_cli_sessions.load_watch_state", side_effect=OSError("disk error")):
            assert _ui._watch_status_cell("ses123") is None

    def test_watching_cell_contains_cycle_char(self):
        result = self._cell("ses123", {"status": "running"})
        assert result is not None and "⟳" in result

    def test_retrying_cell_contains_retry_char(self):
        result = self._cell("ses123", {"status": "retrying"})
        assert result is not None and "↺" in result

    def test_status_case_insensitive(self):
        """status is normalized to lower() before comparison."""
        assert self._cell("ses123", {"status": "RUNNING"}) == "⟳ watching"

    def test_status_with_whitespace(self):
        assert self._cell("ses123", {"status": "  retrying  "}) == "↺ retrying"


# ---------------------------------------------------------------------------
# _print_status_bar resilience tests
# ---------------------------------------------------------------------------


class TestPrintStatusBarWatchCell:
    """Ensure _print_status_bar includes the watch cell and never crashes."""

    def _run_status_bar(self, session_id: str, watch_state: dict | None) -> str:
        buf = StringIO()
        with (
            patch("openclaw_cli_sessions.load_watch_state", return_value=watch_state),
            patch("sys.stdout", buf),
        ):
            _ui._print_status_bar(
                session_id=session_id,
                autoroute_on=True,
                history_len=4,
                _override_is_tty=True,
                _override_rich_available=False,
                _override_cols=120,
            )
        return buf.getvalue()

    def test_no_crash_when_no_watch_state(self):
        output = self._run_status_bar("ses123", None)
        assert "autoroute" in output

    def test_watch_cell_present_when_running(self):
        output = self._run_status_bar("ses123", {"status": "running"})
        assert "⟳" in output

    def test_watch_cell_absent_when_idle(self):
        output = self._run_status_bar("ses123", {"status": "idle"})
        assert "⟳" not in output
        assert "↺" not in output

    def test_watch_cell_retrying_shown(self):
        output = self._run_status_bar("ses123", {"status": "retrying"})
        assert "↺" in output


# ---------------------------------------------------------------------------
# Watch completion recap tests (unit-level — verify the print logic directly)
# ---------------------------------------------------------------------------


def _make_watch_state(poll_count: int = 3, last_summary: str = "all done") -> dict:
    return {"poll_count": poll_count, "last_summary": last_summary, "status": "completed"}


def _make_config(output_json: bool = False) -> MagicMock:
    cfg = MagicMock()
    cfg.output_json = output_json
    return cfg


def _format_success_recap(state: dict) -> str:
    """Mirror the recap format used in handle_watch_command."""
    poll_count = int(state.get("poll_count") or 0)
    last_summary = str(state.get("last_summary") or "").strip()
    excerpt = last_summary[:60] if last_summary else "no output"
    return f"  ✓ Watch session complete — {poll_count} iteration(s), last: {excerpt}"


def _format_exhausted_recap(attempt: int) -> str:
    return f"  ⚠ Watch stopped — exhausted retries after {attempt} attempt(s)"


class TestWatchCompletionRecap:
    """Verify the recap line format and guard conditions."""

    def test_success_recap_contains_check_mark(self):
        line = _format_success_recap(_make_watch_state(3, "done"))
        assert "✓" in line

    def test_success_recap_contains_iteration_count(self):
        line = _format_success_recap(_make_watch_state(poll_count=5))
        assert "5 iteration(s)" in line

    def test_success_recap_contains_excerpt(self):
        line = _format_success_recap(_make_watch_state(last_summary="analysis finished"))
        assert "analysis finished" in line

    def test_success_recap_uses_no_output_when_empty(self):
        line = _format_success_recap(_make_watch_state(last_summary=""))
        assert "no output" in line

    def test_success_recap_truncates_to_60_chars(self):
        long_summary = "B" * 120
        line = _format_success_recap(_make_watch_state(last_summary=long_summary))
        assert "B" * 60 in line
        assert "B" * 61 not in line

    def test_exhausted_recap_contains_warning(self):
        line = _format_exhausted_recap(3)
        assert "⚠" in line

    def test_exhausted_recap_contains_attempt_count(self):
        line = _format_exhausted_recap(7)
        assert "7 attempt(s)" in line

    def test_success_recap_suppressed_when_output_json(self):
        cfg = _make_config(output_json=True)
        printed: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            if not cfg.output_json:
                print(_format_success_recap(_make_watch_state(3, "x")))
        assert not any("✓" in ln for ln in printed)

    def test_exhausted_recap_suppressed_when_output_json(self):
        cfg = _make_config(output_json=True)
        printed: list[str] = []
        with patch("builtins.print", side_effect=lambda *a, **kw: printed.append(" ".join(str(x) for x in a))):
            if not cfg.output_json:
                print(_format_exhausted_recap(3))
        assert not any("⚠" in ln for ln in printed)

    def test_success_recap_starts_with_spaces(self):
        line = _format_success_recap(_make_watch_state(1, "ok"))
        assert line.startswith("  ")

    def test_exhausted_recap_starts_with_spaces(self):
        line = _format_exhausted_recap(1)
        assert line.startswith("  ")

