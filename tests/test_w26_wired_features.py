"""W26 integration-path tests for 5 wired features from Wave 25.

Tests exercise the live call paths (not just unit behaviour), covering:
  1. Approval recap live path — request_cli_approval() → _print_approval_recap()
  2. Risk explanation text in the approval block stdout
  3. Draft badge in _make_prompt() return value
  4. Watch retry message printed by handle_watch_command() retry loop
"""

from __future__ import annotations

import argparse
import sys
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

sys.path.insert(0, "src")

import openclaw_cli_actions as _act  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_store() -> MagicMock:
    store = MagicMock()
    req = MagicMock()
    req.request_id = "test-req-id"
    store.create.return_value = req
    store.resolve.return_value = None
    return store


def _run_approval(
    risk_name: str,
    response: str = "y",
) -> tuple[bool, str, MagicMock]:
    """Call request_cli_approval and return (result, stdout, recap_mock).

    _print_approval_recap is always patched; check call_count to determine
    whether it was actually invoked (LOW/MEDIUM return before the call site).
    """
    try:
        rl = _act.RiskLevel[risk_name]
    except KeyError:
        rl = MagicMock()
        rl.value = risk_name

    mock_store = _make_mock_store()
    buf = StringIO()
    buf.isatty = lambda: False  # type: ignore[attr-defined]
    recap_mock = MagicMock()

    with (
        patch("openclaw_cli_actions.approval_store", mock_store),
        patch("openclaw_cli_actions._print_approval_recap", recap_mock),
        patch("sys.stdin") as mock_stdin,
        patch("sys.stdout", buf),
    ):
        mock_stdin.isatty.return_value = True
        result = _act.request_cli_approval(
            action="shell.exec",
            target="test_target",
            risk_level=rl,
            input_func=lambda _: response,
        )

    return result, buf.getvalue(), recap_mock


# ---------------------------------------------------------------------------
# Group 1 — Approval recap live path (9 tests)
# ---------------------------------------------------------------------------


class TestApprovalRecapLivePath:
    """request_cli_approval() must call _print_approval_recap for HIGH/CRITICAL only."""

    def test_high_risk_calls_recap(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "y")
        recap_mock.assert_called_once()

    def test_critical_risk_calls_recap(self) -> None:
        _, _, recap_mock = _run_approval("CRITICAL", "y")
        recap_mock.assert_called_once()

    def test_low_risk_does_not_call_recap(self) -> None:
        _, _, recap_mock = _run_approval("LOW", "y")
        assert recap_mock.call_count == 0

    def test_medium_risk_does_not_call_recap(self) -> None:
        _, _, recap_mock = _run_approval("MEDIUM", "y")
        assert recap_mock.call_count == 0

    def test_recap_dict_has_action_key(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "y")
        recap_dict = recap_mock.call_args[0][0]
        assert "action" in recap_dict

    def test_recap_dict_has_target_key(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "y")
        recap_dict = recap_mock.call_args[0][0]
        assert "target" in recap_dict

    def test_recap_dict_has_decision_key(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "y")
        recap_dict = recap_mock.call_args[0][0]
        assert "decision" in recap_dict

    def test_decision_approved_when_y(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "y")
        recap_dict = recap_mock.call_args[0][0]
        assert recap_dict["decision"] == "approved"

    def test_decision_denied_when_n(self) -> None:
        _, _, recap_mock = _run_approval("HIGH", "n")
        recap_dict = recap_mock.call_args[0][0]
        assert recap_dict["decision"] == "denied"

    def test_returns_true_on_approve(self) -> None:
        result, _, _ = _run_approval("HIGH", "y")
        assert result is True

    def test_returns_false_on_deny(self) -> None:
        result, _, _ = _run_approval("HIGH", "n")
        assert result is False


# ---------------------------------------------------------------------------
# Group 2 — Risk explanation text in the approval block (5 tests)
# ---------------------------------------------------------------------------


class TestRiskExplanationText:
    """request_cli_approval() must print the correct risk rationale line."""

    def _capture_high(self, response: str = "y") -> str:
        mock_store = _make_mock_store()
        buf = StringIO()
        buf.isatty = lambda: False  # type: ignore[attr-defined]
        with (
            patch("openclaw_cli_actions.approval_store", mock_store),
            patch("openclaw_cli_actions._print_approval_recap"),
            patch("sys.stdin") as mock_stdin,
            patch("sys.stdout", buf),
        ):
            mock_stdin.isatty.return_value = True
            _act.request_cli_approval(
                action="shell.exec",
                target="tgt",
                risk_level=_act.RiskLevel.HIGH,
                input_func=lambda _: response,
            )
        return buf.getvalue()

    def _capture_critical(self, response: str = "y") -> str:
        mock_store = _make_mock_store()
        buf = StringIO()
        buf.isatty = lambda: False  # type: ignore[attr-defined]
        with (
            patch("openclaw_cli_actions.approval_store", mock_store),
            patch("openclaw_cli_actions._print_approval_recap"),
            patch("sys.stdin") as mock_stdin,
            patch("sys.stdout", buf),
        ):
            mock_stdin.isatty.return_value = True
            _act.request_cli_approval(
                action="shell.exec",
                target="tgt",
                risk_level=_act.RiskLevel.CRITICAL,
                input_func=lambda _: response,
            )
        return buf.getvalue()

    def test_high_risk_explanation_contains_high(self) -> None:
        out = self._capture_high()
        assert "HIGH" in out

    def test_critical_risk_explanation_contains_critical(self) -> None:
        out = self._capture_critical()
        assert "CRITICAL" in out

    def test_high_risk_rationale_line_content(self) -> None:
        out = self._capture_high()
        assert "HIGH risk:" in out

    def test_critical_risk_rationale_line_content(self) -> None:
        out = self._capture_critical()
        assert "CRITICAL risk:" in out

    def test_low_risk_no_high_critical_text(self) -> None:
        """LOW risk returns True immediately; no HIGH/CRITICAL rationale printed."""
        mock_store = _make_mock_store()
        buf = StringIO()
        buf.isatty = lambda: False  # type: ignore[attr-defined]
        with (
            patch("openclaw_cli_actions.approval_store", mock_store),
            patch("sys.stdin") as mock_stdin,
            patch("sys.stdout", buf),
        ):
            mock_stdin.isatty.return_value = True
            _act.request_cli_approval(
                action="shell.exec",
                target="tgt",
                risk_level=_act.RiskLevel.LOW,
                input_func=lambda _: "y",
            )
        out = buf.getvalue()
        assert "HIGH risk:" not in out
        assert "CRITICAL risk:" not in out


# ---------------------------------------------------------------------------
# Group 3 — Draft badge in _make_prompt() (5 tests)
# ---------------------------------------------------------------------------


class TestDraftBadgeInPrompt:
    """_make_prompt() must include [draft] badge iff draft_active=True."""

    @pytest.fixture(autouse=True)
    def _patch_prompt_guards(self) -> None:
        """Disable TTY, a11y, and custom-format guards so draft path is reachable."""
        import openclaw_cli as _cli_mod

        with (
            patch.object(_cli_mod, "_a11y_plain_mode", return_value=False),
            patch.object(_cli_mod, "_get_is_tty", return_value=False),
            patch.object(_cli_mod, "_PREFS", {"prompt_format": ""}),
        ):
            yield

    def _make(self, **kw: object) -> str:
        from openclaw_cli import _make_prompt

        return _make_prompt(**kw)  # type: ignore[arg-type]

    def test_draft_active_true_badge_present(self) -> None:
        result = self._make(draft_active=True)
        assert "[draft]" in result

    def test_draft_active_false_badge_absent(self) -> None:
        result = self._make(draft_active=False)
        assert "[draft]" not in result

    def test_draft_active_default_false(self) -> None:
        result = self._make()
        assert "[draft]" not in result

    def test_draft_and_multiline_both_present(self) -> None:
        result = self._make(draft_active=True, multiline=True)
        assert "[draft]" in result
        assert "[multiline]" in result

    def test_make_prompt_returns_string(self) -> None:
        result = self._make(draft_active=True)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Group 4 — Watch retry message (4 tests)
# ---------------------------------------------------------------------------


def _build_watch_session(session_id: str = "watch-sess-001") -> MagicMock:
    sess = MagicMock()
    sess.session_id = session_id
    sess.cwd = "/tmp"
    sess.files = []
    sess.plan_id = ""
    sess.task_id = ""
    return sess


def _build_watch_config(output_json: bool = False) -> MagicMock:
    cfg = MagicMock()
    cfg.output_json = output_json
    cfg.session_id = ""
    return cfg


class TestWatchRetryMessage:
    """handle_watch_command prints '↺ Watch auto-retried' on transient failure."""

    def _run_watch_with_transient_then_success(self) -> tuple[str, MagicMock]:
        """
        Drive handle_watch_command through one poll that:
          - attempt 1 → raises a transient error (connection timed out)
          - attempt 2 → succeeds (returns ("result text", ""))
        max_polls=1 so the outer loop exits after the one poll.
        """
        import openclaw_cli_watch as _w

        mock_session = _build_watch_session()
        args = argparse.Namespace(
            resume="",
            session="",
            goal=["do the thing"],
            cwd=None,
            plan_id="",
            task_id="",
            mode="analyze",
            interval=1,
            iterations=1,
            on_change=False,
            files=[],
            output="",
            deep=False,
            title="",
        )
        config = _build_watch_config()

        call_count = {"n": 0}

        def _execute_side_effect(**_kw: object) -> tuple[str, str]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("connection timed out")
            return ("checkpoint done", "")

        buf = StringIO()
        execute_mock = MagicMock(side_effect=_execute_side_effect)

        with (
            patch.object(_w, "load_watch_state", return_value=None),
            patch.object(_w, "require_session", return_value=mock_session),
            patch.object(_w, "ensure_cli_session", return_value=mock_session),
            patch.object(_w, "update_session", return_value=mock_session),
            patch.object(_w, "save_watch_state"),
            patch.object(_w, "collect_workspace_context", return_value=([], "")),
            patch.object(_w, "build_workspace_signature", return_value="sig1"),
            patch.object(_w, "refresh_watch_controls", side_effect=lambda _sid, state: state),
            patch.object(_w, "execute_watch_iteration", execute_mock),
            patch.object(_w, "record_watch_progress"),
            patch.object(_w, "append_event"),
            patch.object(_w, "render_watch_iteration"),
            patch.object(_w, "extract_prompt_targets", return_value=(["do the thing"], [])),
            patch.object(_w, "parse_prompt", return_value="do the thing"),
            patch.object(_w.time, "sleep"),
            redirect_stdout(buf),
        ):
            _w.handle_watch_command(args, config=config)

        return buf.getvalue(), execute_mock

    def test_retry_message_appears_on_transient_failure(self) -> None:
        out, _ = self._run_watch_with_transient_then_success()
        assert "↺ Watch auto-retried" in out

    def test_retry_message_contains_attempt_number(self) -> None:
        out, _ = self._run_watch_with_transient_then_success()
        # attempt 1 fires the retry message
        assert "attempt 1" in out

    def test_retry_message_contains_error_text(self) -> None:
        out, _ = self._run_watch_with_transient_then_success()
        assert "connection timed out" in out

    def test_no_retry_message_when_first_attempt_succeeds(self) -> None:
        """When execute_watch_iteration succeeds on first try, no retry line appears."""
        import openclaw_cli_watch as _w

        mock_session = _build_watch_session()
        args = argparse.Namespace(
            resume="",
            session="",
            goal=["do the thing"],
            cwd=None,
            plan_id="",
            task_id="",
            mode="analyze",
            interval=1,
            iterations=1,
            on_change=False,
            files=[],
            output="",
            deep=False,
            title="",
        )
        config = _build_watch_config()

        buf = StringIO()
        with (
            patch.object(_w, "load_watch_state", return_value=None),
            patch.object(_w, "require_session", return_value=mock_session),
            patch.object(_w, "ensure_cli_session", return_value=mock_session),
            patch.object(_w, "update_session", return_value=mock_session),
            patch.object(_w, "save_watch_state"),
            patch.object(_w, "collect_workspace_context", return_value=([], "")),
            patch.object(_w, "build_workspace_signature", return_value="sig1"),
            patch.object(_w, "refresh_watch_controls", side_effect=lambda _sid, state: state),
            patch.object(_w, "execute_watch_iteration", return_value=("all good", "")),
            patch.object(_w, "record_watch_progress"),
            patch.object(_w, "append_event"),
            patch.object(_w, "render_watch_iteration"),
            patch.object(_w, "extract_prompt_targets", return_value=(["do the thing"], [])),
            patch.object(_w, "parse_prompt", return_value="do the thing"),
            patch.object(_w.time, "sleep"),
            redirect_stdout(buf),
        ):
            _w.handle_watch_command(args, config=config)

        assert "↺ Watch auto-retried" not in buf.getvalue()
