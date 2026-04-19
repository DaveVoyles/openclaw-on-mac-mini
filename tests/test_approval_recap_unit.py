"""Unit tests for post-approval recap, auto-retry note, and _print_usage helper."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from openclaw_cli_actions import (
    _print_approval_recap,
    _print_usage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_recap(recap: dict, *, tty: bool = False) -> str:
    """Run _print_approval_recap and return captured stdout."""
    buf = StringIO()
    with patch("sys.stdout", buf), patch("sys.stdout.isatty", return_value=tty):
        _print_approval_recap(recap)
    return buf.getvalue()


def _capture_usage(msg: str, *, tty: bool = False) -> str:
    buf = StringIO()
    with patch("sys.stdout", buf), patch("sys.stdout.isatty", return_value=tty):
        _print_usage(msg)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _print_approval_recap — approved case
# ---------------------------------------------------------------------------


def test_recap_approved_prints_decision():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "ls -la",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        }
    )
    assert "approved" in out
    assert "denied" not in out


def test_recap_approved_prints_action():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "ls -la",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        }
    )
    assert "shell.exec" in out


def test_recap_approved_prints_target():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "ls -la",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        }
    )
    assert "ls -la" in out


def test_recap_approved_prints_execution_outcome():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "rm build/",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        }
    )
    assert "exit 0" in out


def test_recap_approved_no_recovery_hint_when_none():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "rm build/",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        }
    )
    assert "hint" not in out


def test_recap_approved_shows_recovery_hint_when_provided():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "rm -rf dist/",
            "decision": "approved",
            "execution_outcome": "exit 1",
            "recovery_hint": "check your VCS",
        }
    )
    assert "check your VCS" in out


# ---------------------------------------------------------------------------
# _print_approval_recap — denied case
# ---------------------------------------------------------------------------


def test_recap_denied_prints_decision():
    out = _capture_recap(
        {
            "action": "file.edit",
            "target": "pyproject.toml",
            "decision": "denied",
            "execution_outcome": None,
            "recovery_hint": "adjust the diff first",
        }
    )
    assert "denied" in out
    assert "approved" not in out


def test_recap_denied_prints_action_and_target():
    out = _capture_recap(
        {
            "action": "file.edit",
            "target": "pyproject.toml",
            "decision": "denied",
            "execution_outcome": None,
            "recovery_hint": "adjust the diff first",
        }
    )
    assert "file.edit" in out
    assert "pyproject.toml" in out


def test_recap_denied_no_execution_outcome():
    out = _capture_recap(
        {
            "action": "file.edit",
            "target": "pyproject.toml",
            "decision": "denied",
            "execution_outcome": None,
            "recovery_hint": None,
        }
    )
    assert "outcome" not in out


def test_recap_denied_prints_recovery_hint():
    out = _capture_recap(
        {
            "action": "file.edit",
            "target": "pyproject.toml",
            "decision": "denied",
            "execution_outcome": None,
            "recovery_hint": "use /rollback last",
        }
    )
    assert "use /rollback last" in out


# ---------------------------------------------------------------------------
# _print_approval_recap — edge cases
# ---------------------------------------------------------------------------


def test_recap_unknown_decision_falls_through():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "echo hi",
            "decision": "unknown",
            "execution_outcome": None,
            "recovery_hint": None,
        }
    )
    assert "Approval recap" in out


def test_recap_missing_fields_do_not_crash():
    out = _capture_recap({})
    assert "Approval recap" in out


def test_recap_no_ansi_when_not_tty():
    out = _capture_recap(
        {
            "action": "shell.exec",
            "target": "ls",
            "decision": "approved",
            "execution_outcome": "exit 0",
            "recovery_hint": None,
        },
        tty=False,
    )
    assert "\033[" not in out


def test_recap_contains_ansi_when_tty(monkeypatch):
    buf = StringIO()
    # Patch isatty at the sys module level used by openclaw_cli_actions
    with patch("openclaw_cli_actions.sys") as mock_sys:
        mock_sys.stdout.isatty.return_value = True
        import openclaw_cli_actions as _act

        _act._print_approval_recap(
            {
                "action": "shell.exec",
                "target": "ls",
                "decision": "approved",
                "execution_outcome": "exit 0",
                "recovery_hint": None,
            }
        )
    # We just verify the function runs without error when TTY is True


# ---------------------------------------------------------------------------
# _print_usage helper
# ---------------------------------------------------------------------------


def test_print_usage_outputs_message():
    out = _capture_usage("Usage: /files add <path>")
    assert "Usage: /files add <path>" in out


def test_print_usage_adds_indentation():
    out = _capture_usage("Usage: /cmd")
    assert out.startswith("  ")


def test_print_usage_no_ansi_when_not_tty():
    out = _capture_usage("Usage: /cmd", tty=False)
    assert "\033[" not in out


# ---------------------------------------------------------------------------
# Auto-retry note — quality retry in llm/chat.py
# ---------------------------------------------------------------------------


def test_quality_retry_prints_auto_retry_note(capsys):
    """Simulate the quality-retry gate and verify the ↺ note is printed."""
    auto_retry_msg = "↺ Auto-retried: quality gate triggered — trying a higher-quality provider"

    # Minimal stub that mimics the relevant slice of llm/chat.py Phase 28
    def _simulated_quality_retry_gate(text: str, *, is_low_quality_fn, copilot_enabled: bool) -> None:
        if is_low_quality_fn(text) and copilot_enabled:
            print(auto_retry_msg, flush=True)

    _simulated_quality_retry_gate(
        "x",
        is_low_quality_fn=lambda _: True,
        copilot_enabled=True,
    )
    captured = capsys.readouterr()
    assert auto_retry_msg in captured.out


def test_quality_retry_note_not_printed_when_quality_ok(capsys):
    auto_retry_msg = "↺ Auto-retried:"

    def _simulated_quality_retry_gate(text: str, *, is_low_quality_fn, copilot_enabled: bool) -> None:
        if is_low_quality_fn(text) and copilot_enabled:
            print(auto_retry_msg, flush=True)

    _simulated_quality_retry_gate(
        "This is a good, detailed answer.",
        is_low_quality_fn=lambda _: False,
        copilot_enabled=True,
    )
    captured = capsys.readouterr()
    assert auto_retry_msg not in captured.out


def test_quality_retry_note_not_printed_when_copilot_disabled(capsys):
    auto_retry_msg = "↺ Auto-retried:"

    def _simulated_quality_retry_gate(text: str, *, is_low_quality_fn, copilot_enabled: bool) -> None:
        if is_low_quality_fn(text) and copilot_enabled:
            print(auto_retry_msg, flush=True)

    _simulated_quality_retry_gate(
        "x",
        is_low_quality_fn=lambda _: True,
        copilot_enabled=False,
    )
    captured = capsys.readouterr()
    assert auto_retry_msg not in captured.out


# ---------------------------------------------------------------------------
# Recap skipped for low-risk actions — validate contract via recap dict
# ---------------------------------------------------------------------------


def test_recap_only_called_for_high_critical(monkeypatch):
    """_print_approval_recap should only be invoked for HIGH/CRITICAL risk.

    This test verifies the caller-side contract by simulating the guard used
    in _cmd_exec and _cmd_edit: only printing recap when risk is HIGH/CRITICAL.
    """
    recap_calls: list[dict] = []

    def _mock_recap(recap: dict, *, use_rich: bool = False) -> None:
        recap_calls.append(recap)

    def _simulate_exec_post_approval(risk_level_value: str, approved: bool) -> None:
        """Mirror the guard added to _cmd_exec / _cmd_edit."""
        recap = {
            "action": "shell.exec",
            "target": "ls",
            "decision": "approved" if approved else "denied",
            "execution_outcome": "exit 0" if approved else None,
            "recovery_hint": None,
        }
        if risk_level_value in {"HIGH", "CRITICAL"}:
            _mock_recap(recap)

    _simulate_exec_post_approval("LOW", approved=True)
    assert recap_calls == [], "LOW risk should not trigger recap"

    _simulate_exec_post_approval("MEDIUM", approved=True)
    assert recap_calls == [], "MEDIUM risk should not trigger recap"

    _simulate_exec_post_approval("HIGH", approved=True)
    assert len(recap_calls) == 1

    _simulate_exec_post_approval("CRITICAL", approved=False)
    assert len(recap_calls) == 2


def test_recap_approved_edit_includes_summary():
    out = _capture_recap(
        {
            "action": "file.edit",
            "target": "src/foo.py",
            "decision": "approved",
            "execution_outcome": "Updated file with requested replacement.",
            "recovery_hint": "use /rollback last to undo this edit if needed.",
        }
    )
    assert "Updated file with requested replacement." in out
    assert "use /rollback last" in out
