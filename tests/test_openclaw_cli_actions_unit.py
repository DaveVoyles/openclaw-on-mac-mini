"""Unit tests for openclaw_cli_actions helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openclaw_cli_actions import (
    FileEditResult,
    ShellCommandResult,
    atomic_write,
    format_shell_result,
    infer_command_risk,
    infer_file_edit_risk,
    normalize_cwd,
    preview_file_result,
    render_diff,
    replace_text_in_file,
    request_cli_approval,
    risk_level_from_name,
    write_text_file,
)

try:
    from approval_models import RiskLevel
except ImportError:
    from openclaw_cli_actions import RiskLevel


# ---------------------------------------------------------------------------
# normalize_cwd
# ---------------------------------------------------------------------------

def test_normalize_cwd_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = normalize_cwd()
    assert result == tmp_path.resolve()


def test_normalize_cwd_explicit_path(tmp_path):
    result = normalize_cwd(tmp_path)
    assert result == tmp_path.resolve()


def test_normalize_cwd_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        normalize_cwd(tmp_path / "nonexistent")


def test_normalize_cwd_file_raises(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        normalize_cwd(f)


# ---------------------------------------------------------------------------
# infer_command_risk
# ---------------------------------------------------------------------------

def test_infer_command_risk_rm_is_critical():
    assert infer_command_risk(["rm", "-rf", "/"]) == RiskLevel.CRITICAL


def test_infer_command_risk_shutdown_is_critical():
    assert infer_command_risk(["sudo", "shutdown", "-h", "now"]) == RiskLevel.CRITICAL


def test_infer_command_risk_docker_is_high():
    assert infer_command_risk(["docker", "run", "ubuntu"]) == RiskLevel.HIGH


def test_infer_command_risk_pip_install_is_high():
    assert infer_command_risk(["pip", "install", "requests"]) == RiskLevel.HIGH


def test_infer_command_risk_pytest_is_medium():
    assert infer_command_risk(["pytest", "tests/"]) == RiskLevel.MEDIUM


def test_infer_command_risk_git_status_is_medium():
    assert infer_command_risk(["git", "status"]) == RiskLevel.MEDIUM


def test_infer_command_risk_unknown_is_low():
    assert infer_command_risk(["myapp", "--help"]) == RiskLevel.LOW


def test_infer_command_risk_git_reset_hard_is_critical():
    assert infer_command_risk(["git", "reset", "--hard"]) == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# infer_file_edit_risk
# ---------------------------------------------------------------------------

def test_infer_file_edit_risk_env_file():
    assert infer_file_edit_risk(".env") == RiskLevel.CRITICAL


def test_infer_file_edit_risk_pem_file():
    assert infer_file_edit_risk("certs/server.pem") == RiskLevel.CRITICAL


def test_infer_file_edit_risk_docker_compose():
    assert infer_file_edit_risk("docker-compose.yml") == RiskLevel.HIGH


def test_infer_file_edit_risk_github_workflow():
    assert infer_file_edit_risk(".github/workflows/ci.yml") == RiskLevel.HIGH


def test_infer_file_edit_risk_pyproject():
    assert infer_file_edit_risk("pyproject.toml") == RiskLevel.HIGH


def test_infer_file_edit_risk_regular_file():
    assert infer_file_edit_risk("src/main.py") == RiskLevel.MEDIUM


# ---------------------------------------------------------------------------
# risk_level_from_name
# ---------------------------------------------------------------------------

def test_risk_level_from_name_high():
    assert risk_level_from_name("high", default=RiskLevel.LOW) == RiskLevel.HIGH


def test_risk_level_from_name_empty_returns_default():
    assert risk_level_from_name("", default=RiskLevel.MEDIUM) == RiskLevel.MEDIUM


def test_risk_level_from_name_none_returns_default():
    assert risk_level_from_name(None, default=RiskLevel.LOW) == RiskLevel.LOW


def test_risk_level_from_name_invalid_raises():
    with pytest.raises(ValueError):
        risk_level_from_name("INVALID", default=RiskLevel.LOW)


def test_risk_level_from_name_case_insensitive():
    assert risk_level_from_name("critical", default=RiskLevel.LOW) == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------

def test_openclaw_cli_actions_unit_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_openclaw_cli_actions_unit_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "dir" / "out.txt"
    atomic_write(target, "data")
    assert target.read_text() == "data"


def test_openclaw_cli_actions_unit_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "file.txt"
    atomic_write(target, "v1")
    atomic_write(target, "v2")
    assert target.read_text() == "v2"


# ---------------------------------------------------------------------------
# render_diff
# ---------------------------------------------------------------------------

def test_render_diff_produces_unified_diff(tmp_path):
    p = tmp_path / "test.py"
    diff = render_diff(p, "line1\nline2\n", "line1\nchanged\n")
    assert "-line2" in diff
    assert "+changed" in diff


def test_render_diff_no_change_empty(tmp_path):
    p = tmp_path / "test.py"
    diff = render_diff(p, "same", "same")
    assert diff == ""


# ---------------------------------------------------------------------------
# replace_text_in_file
# ---------------------------------------------------------------------------

def test_replace_text_in_file_basic(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hello world")
    result = replace_text_in_file(f, old="world", new="pytest")
    assert result.changed
    assert f.read_text() == "hello pytest"


def test_replace_text_in_file_not_found_returns_unchanged(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hello world")
    result = replace_text_in_file(f, old="xyz", new="abc")
    assert not result.changed


def test_replace_text_in_file_dry_run(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("original")
    replace_text_in_file(f, old="original", new="changed", dry_run=True)
    assert f.read_text() == "original"


def test_replace_text_in_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        replace_text_in_file(tmp_path / "missing.txt", old="x", new="y")


def test_replace_text_in_file_on_directory_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        replace_text_in_file(tmp_path, old="x", new="y")


# ---------------------------------------------------------------------------
# write_text_file
# ---------------------------------------------------------------------------

def test_write_text_file_creates(tmp_path):
    f = tmp_path / "new.txt"
    result = write_text_file(f, content="created")
    assert result.changed
    assert f.read_text() == "created"


def test_write_text_file_append(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello ")
    result = write_text_file(f, content="world", append=True)
    assert f.read_text() == "hello world"


def test_write_text_file_dry_run_no_change(tmp_path):
    f = tmp_path / "b.txt"
    write_text_file(f, content="initial")
    write_text_file(f, content="replaced", dry_run=True)
    assert f.read_text() == "initial"


# ---------------------------------------------------------------------------
# format_shell_result
# ---------------------------------------------------------------------------

def test_format_shell_result_basic():
    r = ShellCommandResult(command="ls", cwd="/tmp", returncode=0, stdout="file.py\n", stderr="")
    text = format_shell_result(r)
    assert "$ ls" in text
    assert "exit: 0" in text
    assert "file.py" in text


def test_format_shell_result_stderr():
    r = ShellCommandResult(command="bad", cwd="/", returncode=1, stdout="", stderr="error msg")
    text = format_shell_result(r)
    assert "error msg" in text


def test_format_shell_result_no_stdout_no_stderr():
    r = ShellCommandResult(command="true", cwd="/", returncode=0, stdout="", stderr="")
    text = format_shell_result(r)
    assert "stdout" not in text
    assert "stderr" not in text


# ---------------------------------------------------------------------------
# preview_file_result
# ---------------------------------------------------------------------------

def test_preview_file_result_with_diff():
    r = FileEditResult(path="/f.py", changed=True, diff="--- a\n+++ b", summary="Updated.")
    text = preview_file_result(r)
    assert "Updated." in text
    assert "/f.py" in text
    assert "---" in text


def test_preview_file_result_no_diff():
    r = FileEditResult(path="/f.py", changed=False, diff="", summary="No change.")
    text = preview_file_result(r)
    assert "No change." in text


# ---------------------------------------------------------------------------
# request_cli_approval — auto-approve and non-interactive paths
# ---------------------------------------------------------------------------

def test_request_cli_approval_low_risk_auto_approved():
    result = request_cli_approval(
        action="read", target="file.py", risk_level=RiskLevel.LOW
    )
    assert result is True


def test_request_cli_approval_medium_risk_auto_approved():
    result = request_cli_approval(
        action="run", target="pytest", risk_level=RiskLevel.MEDIUM
    )
    assert result is True


def test_request_cli_approval_high_risk_auto_approve_flag():
    result = request_cli_approval(
        action="delete", target="file.py", risk_level=RiskLevel.HIGH,
        auto_approve=True,
    )
    assert result is True


def test_request_cli_approval_critical_non_interactive():
    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = request_cli_approval(
            action="rm", target="/", risk_level=RiskLevel.CRITICAL,
        )
    assert result is False


def test_request_cli_approval_high_prompt_yes():
    with (
        patch("sys.stdin") as mock_stdin,
        patch("sys.stdout") as mock_stdout,
    ):
        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = False
        result = request_cli_approval(
            action="delete", target="file.py", risk_level=RiskLevel.HIGH,
            input_func=lambda _: "y",
        )
    assert result is True


def test_request_cli_approval_high_prompt_no():
    with (
        patch("sys.stdin") as mock_stdin,
        patch("sys.stdout") as mock_stdout,
    ):
        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = False
        result = request_cli_approval(
            action="delete", target="file.py", risk_level=RiskLevel.HIGH,
            input_func=lambda _: "n",
        )
    assert result is False


def test_request_cli_approval_review_option_reprints_details(monkeypatch, capsys):
    responses = iter(["r", "y"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    result = request_cli_approval(
        action="delete",
        target="file.py",
        risk_level=RiskLevel.HIGH,
        review_lines=["Review: exact shell text `rm -rf build`"],
        trust_note="approve only when the command matches your intent.",
        input_func=lambda _: next(responses),
    )
    assert result is True
    out = capsys.readouterr().out
    assert out.count("Review: exact shell text `rm -rf build`") == 2


def test_request_cli_approval_review_callback_runs_for_preview_alias(monkeypatch):
    responses = iter(["preview", "n"])
    callback = MagicMock()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    result = request_cli_approval(
        action="delete",
        target="file.py",
        risk_level=RiskLevel.HIGH,
        input_func=lambda _: next(responses),
        review_callback=callback,
    )
    assert result is False
    callback.assert_called_once_with()


def test_request_cli_approval_overlay_replays_preview_and_returns(monkeypatch, capsys):
    responses = iter(["overlay", "preview", "1", "", "n"])
    callback = MagicMock()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = request_cli_approval(
        action="edit",
        target="file.py",
        risk_level=RiskLevel.HIGH,
        review_lines=["Review: exact queued diff `-old +new`"],
        trust_note="approve only when the preview matches your intent.",
        recovery_hint="deny to adjust the change first.",
        input_func=lambda _: next(responses),
        review_callback=callback,
    )
    assert result is False
    callback.assert_called_once_with()
    out = capsys.readouterr().out
    assert "Approval review overlay" in out
    assert "Replay exact queued preview" in out
    assert "Overlay closed." in out
