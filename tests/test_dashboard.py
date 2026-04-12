"""
Tests for dashboard.py — HTML and JSON dashboard handlers.

Covers: dashboard_handler returns HTML, guide_handler returns HTML,
api_dashboard_handler returns JSON, and _command_list structure.

dashboard.py reads HTML template files at import time via _TEMPLATES_DIR.
When running from source (outside Docker), templates live at <repo>/templates/
instead of <repo>/src/templates/, so we patch the module-level constants
before importing.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the module loads templates from the correct location
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"
if not _TEMPLATES_DIR.exists():
    pytest.skip("templates/ directory not found", allow_module_level=True)

# Patch _TEMPLATES_DIR before the module reads the files
# Remove cached module so we can re-import with correct paths
sys.modules.pop("dashboard", None)
# Temporarily monkey-patch pathlib resolution by pre-loading the HTML
_dashboard_html = (_TEMPLATES_DIR / "dashboard.html").read_text()
_guide_html = (_TEMPLATES_DIR / "guide.html").read_text()

with patch.dict("os.environ", {}):
    # We need to intercept the module-level read_text calls.
    # Easiest: patch Path.__truediv__ — but that's fragile.
    # Instead: just mock the two constants after import via a wrapper.
    pass

# Do the actual import — it will try src/templates which may not exist.
# Pre-create the path so the import succeeds.
_src_templates = _REPO_ROOT / "src" / "templates"
_src_templates.mkdir(parents=True, exist_ok=True)
_src_dash = _src_templates / "dashboard.html"
_src_guide = _src_templates / "guide.html"
_created_dash = not _src_dash.exists()
_created_guide = not _src_guide.exists()
if _created_dash:
    _src_dash.write_text(_dashboard_html)
if _created_guide:
    _src_guide.write_text(_guide_html)

try:
    import dashboard as mod
    from dashboard import api_handlers as api_mod
finally:
    # Clean up symlinks/copies we created
    if _created_dash and _src_dash.exists():
        _src_dash.unlink()
    if _created_guide and _src_guide.exists():
        _src_guide.unlink()
    # Remove empty dir if we created it
    try:
        _src_templates.rmdir()
    except OSError:
        pass


def _fake_request(
    app_data: dict | None = None,
    *,
    method: str = "GET",
    query: dict | None = None,
    json_payload: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """Build a minimal mock aiohttp.web.Request."""
    req = MagicMock()
    req.app = app_data or {}
    req.method = method
    req.query = query or {}
    req.headers = headers or {}
    req.json = AsyncMock(return_value=json_payload or {})
    return req


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _installer_test_env(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    home_dir = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home_dir.mkdir()
    fake_bin.mkdir()
    env = {**os.environ, "HOME": str(home_dir), "PATH": f"{fake_bin}:{os.environ['PATH']}", "SHELL": "/bin/bash"}
    return home_dir, fake_bin, env


def _stub_openclaw_cli_source(*, health_exit: int = 0, health_payload: dict | None = None) -> str:
    payload = json.dumps(health_payload or {"status": "ok", "service": "openclaw"})
    return f"""#!/usr/bin/env python3
import sys

if "--health" in sys.argv:
    if {health_exit} != 0:
        print("health failed", file=sys.stderr)
        raise SystemExit({health_exit})
    print({payload!r})
    raise SystemExit(0)

print("stub openclaw cli")
"""


def _fake_curl_script_for_cli_source(cli_source: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      output="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
cat >"$output" <<'EOF'
{cli_source}EOF
"""


# ---------------------------------------------------------------------------
# Static HTML handlers
# ---------------------------------------------------------------------------


class TestDashboardHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.dashboard_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100
        assert "<html" in resp.text.lower() or "<!doctype" in resp.text.lower()
        assert "Run History Timeline" in resp.text
        assert "Channel Memory Inspector" in resp.text
        assert "Channel Profile Assistant" in resp.text
        assert "Inspect Scope / Preview" in resp.text
        assert "Workflow Lanes" in resp.text
        assert "Terminal Agent Sessions" in resp.text
        assert "agent-sessions-list" in resp.text
        assert "openclaw_api_action_token" in resp.text
        assert "Quality Eval Scorecards" in resp.text
        assert "Discord Answer Quality Telemetry" in resp.text
        assert "Quality score distribution" in resp.text
        assert "Retry outcomes" in resp.text
        assert "Discord answer feedback loop" in resp.text
        assert "Helpful rate" in resp.text
        assert "persistence receipts" in resp.text
        assert "rerun action schedules a full research" in resp.text
        assert "cycle every 24h" in resp.text


class TestGuideHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.guide_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100
        assert "guide-command-search" in resp.text
        assert "Live Command Finder" in resp.text
        assert "Workflow Lanes (Fast Navigation)" in resp.text
        assert "Re-run full research in 24h" in resp.text
        assert "Persistence receipts" in resp.text
        assert "Terminal CLI Access" in resp.text
        assert "OpenClaw" in resp.text
        assert "oc-ask" in resp.text
        assert "OpenClaw vs. Copilot CLI" in resp.text
        assert "/install" in resp.text
        assert "Linux, Windows, or WSL" in resp.text
        assert "openclaw --health" in resp.text
        assert "--json" in resp.text
        assert "--skip-verify" in resp.text
        assert "openclaw auth login" in resp.text
        assert "openclaw auth logout" in resp.text
        assert "openclaw analyze" in resp.text
        assert "openclaw research" in resp.text
        assert "openclaw write" in resp.text
        assert "openclaw exec" in resp.text
        assert "/rollback last" in resp.text
        assert "manual-recovery only" in resp.text
        assert "openclaw session resume" in resp.text
        assert "bash</strong> and <strong>zsh" in resp.text


class TestTerminalHandler:
    async def test_returns_html(self):
        req = _fake_request()
        resp = await mod.terminal_handler(req)
        assert resp.content_type == "text/html"
        assert len(resp.text) > 100
        assert "Preferred launcher" in resp.text
        assert "Compatibility shortcut" in resp.text
        assert "openclaw --version" in resp.text
        assert "OpenClaw" in resp.text
        assert "OPENCLAW_TOKEN" in resp.text
        assert "openclaw --health" in resp.text
        assert "--json" in resp.text
        assert "--skip-verify" in resp.text
        assert "openclaw auth login" in resp.text
        assert "openclaw auth status" in resp.text
        assert "openclaw analyze" in resp.text
        assert "openclaw research" in resp.text
        assert "openclaw write" in resp.text
        assert "openclaw exec" in resp.text
        assert "openclaw edit" in resp.text
        assert "openclaw session list" in resp.text
        assert "/rollback last" in resp.text
        assert "manual-recovery only" in resp.text
        assert "Terminal Agent Sessions" in resp.text
        assert "bash</code> and <code>zsh</code> are auto-configured" in resp.text


class TestCliDownloadHandlers:
    async def test_cli_python_download_returns_source(self):
        req = _fake_request()
        resp = await mod.openclaw_cli_download_handler(req)
        assert resp.content_type == "text/plain"
        assert "attachment; filename=\"openclaw_cli.py\"" == resp.headers["Content-Disposition"]
        assert "class OpenClawCliError" in resp.text
        assert "def main(" in resp.text

    async def test_cli_support_download_returns_source(self):
        req = _fake_request()
        req.match_info = {"name": "openclaw_cli_actions.py"}
        resp = await mod.openclaw_cli_support_download_handler(req)
        assert resp.content_type == "text/plain"
        assert "attachment; filename=\"openclaw_cli_actions.py\"" == resp.headers["Content-Disposition"]
        assert "class ShellCommandResult" in resp.text

    async def test_cli_installer_download_returns_shell_script(self):
        req = _fake_request()
        req.scheme = "http"
        req.host = "192.168.1.93:8765"
        resp = await mod.openclaw_cli_installer_handler(req)
        assert resp.content_type == "text/plain"
        assert "attachment; filename=\"openclaw-cli-installer.sh\"" == resp.headers["Content-Disposition"]
        assert "/downloads/openclaw_cli.py" in resp.text
        assert "/downloads/openclaw-cli-support/openclaw_cli_actions.py" in resp.text
        assert "/downloads/openclaw-cli-support/openclaw_cli_sessions.py" in resp.text
        assert "/downloads/openclaw-cli-support/subprocess_utils.py" in resp.text
        assert "--shell SHELL" in resp.text
        assert "--skip-verify" in resp.text
        assert "TARGET_RC_FILE" in resp.text
        assert 'cat >"$BIN_DIR/openclaw"' in resp.text
        assert "OpenClaw()" in resp.text
        assert "--enable-remote-login" in resp.text
        assert "http://192.168.1.93:8765" in resp.text

    async def test_cli_remote_installer_defaults_remote_login_on(self):
        req = _fake_request()
        req.scheme = "http"
        req.host = "192.168.1.93:8765"
        resp = await mod.openclaw_cli_remote_installer_handler(req)
        assert resp.content_type == "text/plain"
        assert "attachment; filename=\"openclaw-cli-remote-installer.sh\"" == resp.headers["Content-Disposition"]
        assert "ENABLE_REMOTE_LOGIN=1" in resp.text

    def test_cli_installer_script_runs_end_to_end(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            home_dir, fake_bin, env = _installer_test_env(tmp_path)

            _write_executable(
                fake_bin / "curl",
                _fake_curl_script_for_cli_source(_stub_openclaw_cli_source()),
            )

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            completed = subprocess.run(
                ["bash", str(installer_path), "--shell", "bash", "--skip-token-prompt"],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

            assert (home_dir / ".local/bin/openclaw").exists()
            assert (home_dir / ".local/bin/openclaw-cli").exists()
            assert (home_dir / ".local/share/openclaw-cli/openclaw_aliases.sh").exists()
            assert (home_dir / ".local/share/openclaw-cli/openclaw_cli_actions.py").exists()
            assert (home_dir / ".local/share/openclaw-cli/openclaw_cli_sessions.py").exists()
            assert (home_dir / ".local/share/openclaw-cli/subprocess_utils.py").exists()
            bashrc = home_dir / ".bashrc"
            assert bashrc.exists()
            bashrc_text = bashrc.read_text()
            assert 'export OPENCLAW_URL="http://example.test"' in bashrc_text
            assert 'source "' in bashrc_text
            assert "TARGET_SHELL=bash" in completed.stdout
            assert "export OPENCLAW_TOKEN" in completed.stdout
            assert "STATUS=passed" in completed.stdout
            assert '--url http://example.test --health' in completed.stdout

    def test_cli_installer_reports_download_failures(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _home_dir, fake_bin, env = _installer_test_env(tmp_path)
            _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 22\n")

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            completed = subprocess.run(
                ["bash", str(installer_path), "--shell", "bash", "--skip-token-prompt"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            assert completed.returncode != 0
            assert "Failed to download OpenClaw CLI support" in completed.stderr
            assert "http://example.test/downloads/openclaw_cli.py" in completed.stderr

    def test_cli_installer_reports_unwritable_rc_target(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _home_dir, fake_bin, env = _installer_test_env(tmp_path)
            locked_dir = tmp_path / "locked"
            locked_dir.mkdir()
            locked_dir.chmod(0o500)
            _write_executable(
                fake_bin / "curl",
                _fake_curl_script_for_cli_source(_stub_openclaw_cli_source()),
            )

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            try:
                completed = subprocess.run(
                    [
                        "bash",
                        str(installer_path),
                        "--shell",
                        "bash",
                        "--rc-file",
                        str(locked_dir / "profile"),
                        "--skip-token-prompt",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
            finally:
                locked_dir.chmod(0o700)

            assert completed.returncode != 0
            assert "Directory is not writable" in completed.stderr

    def test_cli_installer_skip_token_prompt_avoids_keychain_calls(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _home_dir, fake_bin, env = _installer_test_env(tmp_path)
            security_marker = tmp_path / "security-called"
            _write_executable(
                fake_bin / "curl",
                _fake_curl_script_for_cli_source(_stub_openclaw_cli_source()),
            )
            _write_executable(
                fake_bin / "security",
                f"""#!/usr/bin/env bash
set -euo pipefail
touch "{security_marker}"
exit 0
""",
            )

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            completed = subprocess.run(
                ["bash", str(installer_path), "--shell", "bash", "--skip-token-prompt"],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

            assert not security_marker.exists()
            assert "export OPENCLAW_TOKEN" in completed.stdout
            assert "STATUS=passed" in completed.stdout

    def test_cli_installer_reports_post_install_verification_failures(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _home_dir, fake_bin, env = _installer_test_env(tmp_path)
            _write_executable(
                fake_bin / "curl",
                _fake_curl_script_for_cli_source(_stub_openclaw_cli_source(health_exit=9)),
            )

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            completed = subprocess.run(
                ["bash", str(installer_path), "--shell", "bash", "--skip-token-prompt"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            assert completed.returncode != 0
            assert "health failed" in completed.stderr
            assert "Post-install verification failed" in completed.stderr
            assert "--url http://example.test --health" in completed.stderr

    def test_cli_installer_skip_verify_bypasses_health_check(self):
        from dashboard.helpers import build_openclaw_cli_installer

        script_body = build_openclaw_cli_installer("http://example.test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            _home_dir, fake_bin, env = _installer_test_env(tmp_path)
            _write_executable(
                fake_bin / "curl",
                _fake_curl_script_for_cli_source(_stub_openclaw_cli_source(health_exit=9)),
            )

            installer_path = tmp_path / "installer.sh"
            installer_path.write_text(script_body)
            installer_path.chmod(0o755)

            completed = subprocess.run(
                [
                    "bash",
                    str(installer_path),
                    "--shell",
                    "bash",
                    "--skip-token-prompt",
                    "--skip-verify",
                ],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

            assert "STATUS=skipped" in completed.stdout


# ---------------------------------------------------------------------------
# _command_list
# ---------------------------------------------------------------------------


class TestCommandList:
    def test_returns_list_of_categories(self):
        from dashboard.helpers import _command_list
        cmds = _command_list()
        assert isinstance(cmds, list)
        assert len(cmds) > 0
        first = cmds[0]
        assert "category" in first
        assert "commands" in first
        assert isinstance(first["commands"], list)
        assert "name" in first["commands"][0]
        assert "desc" in first["commands"][0]
        assert "keywords" in first["commands"][0]

    def test_metadata_fields_are_consistent(self):
        from dashboard.helpers import _command_list

        cmds = _command_list()
        for category in cmds:
            assert isinstance(category["category"], str)
            for cmd in category["commands"]:
                assert isinstance(cmd["name"], str) and cmd["name"]
                assert isinstance(cmd["desc"], str) and cmd["desc"]
                assert isinstance(cmd["keywords"], list)
                assert len(cmd["keywords"]) > 0


# ---------------------------------------------------------------------------
# api_dashboard_handler (heavy mocking — verifies JSON shape)
# ---------------------------------------------------------------------------


class TestApiDashboard:
    async def test_returns_json_response(self):
        mock_bot = MagicMock()
        mock_bot.start_time = 0
        mock_bot.user = MagicMock(__str__=lambda s: "TestBot#0001")
        mock_bot.guilds = [1, 2]
        mock_bot.latency = 0.05

        req = _fake_request({"bot": mock_bot})

        fake_skills = {"skill_a": lambda: None}
        with (
            patch.dict(
                "sys.modules",
                {
                    "skills": MagicMock(
                        SKILLS=fake_skills,
                        list_containers=AsyncMock(return_value="❌ docker offline"),
                        get_docker_stats=AsyncMock(return_value="❌ no stats"),
                        get_system_stats=AsyncMock(return_value="**CPU**: 10%\n**Memory**: 4/16 GB\n**Disk**: 50%"),
                    ),
                    "ontology_skills": MagicMock(
                        ontology_query=AsyncMock(return_value="❌ empty"),
                    ),
                    "llm": MagicMock(
                        _TOOL_DECLARATIONS=[{"name": "skill_a", "description": "A skill"}],
                        get_rate_info=MagicMock(return_value="ok"),
                        MODEL_NAME="test-model",
                        OLLAMA_MODEL="",
                        LOCAL_LLM_ENABLED=False,
                    ),
                },
            ),
        ):
            resp = await mod.api_dashboard_handler(req)

        assert resp.content_type == "application/json"
        payload = json.loads(resp.text)
        assert "commands" in payload and isinstance(payload["commands"], list)
        assert "command_quickstart" in payload and isinstance(payload["command_quickstart"], list)
        assert "agent_sessions" in payload


class TestAgentSessionApis:
    async def test_agent_sessions_list_returns_local_sessions(self):
        req = _fake_request(query={"limit": "5"})
        fake_session = SimpleNamespace(
            session_id="sess-123",
            title="Analyze repo",
            cwd="/tmp/project",
            files=["/tmp/project/README.md"],
            plan_id="plan-1",
            task_id="task-9",
            status="active",
            created_at="2026-04-10T12:00:00Z",
            updated_at="2026-04-10T12:05:00Z",
            last_command="analyze",
            last_summary="summarized repo",
            command_count=3,
            file_edit_count=1,
            output_count=2,
            automation_mode="analyze",
            automation_status="running",
            watch_interval_seconds=30,
            checkpoint_count=2,
            last_checkpoint_at="2026-04-10T12:04:00Z",
        )

        with patch.object(api_mod, "list_cli_sessions", return_value=[fake_session]):
            resp = await api_mod.api_agent_sessions_handler(req)

        payload = json.loads(resp.text)
        assert payload["meta"]["count"] == 1
        assert payload["meta"]["active"] == 1
        assert payload["sessions"][0]["session_id"] == "sess-123"
        assert payload["sessions"][0]["plan_id"] == "plan-1"
        assert payload["sessions"][0]["automation_mode"] == "analyze"
        assert payload["sessions"][0]["checkpoint_count"] == 2

    async def test_agent_session_detail_returns_export(self):
        req = _fake_request()
        req.match_info = {"session_id": "sess-123"}

        with patch.object(
            api_mod,
            "export_cli_session",
            return_value={
                "session": {"session_id": "sess-123", "checkpoint_count": 2},
                "events": [{"kind": "analyze", "content": "summarize repo"}],
                "outputs": [{"name": "report.md"}],
                "watch_state": {"goal": "watch for regressions"},
            },
        ):
            resp = await api_mod.api_agent_session_detail_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["session"]["session_id"] == "sess-123"
        assert payload["outputs"][0]["name"] == "report.md"
        assert payload["watch_state"]["goal"] == "watch for regressions"
        assert "watch_insights" in payload
        assert isinstance(payload["watch_insights"], dict)

    async def test_agent_session_detail_watch_insights_fields(self):
        """Session detail exposes structured watch insights derived from watch_state."""
        req = _fake_request()
        req.match_info = {"session_id": "sess-watch"}

        fake_watch_state = {
            "status": "running",
            "mode": "analyze",
            "goal": "keep repo healthy",
            "poll_count": 4,
            "retry_limit": 3,
            "last_summary": "Analysis complete — 2 issues flagged",
            "failure_count": 1,
            "consecutive_failures": 0,
            "last_error": "",
            "progress_log": [],
            "interventions": [],
            "checkpoints": [
                {
                    "poll": 1,
                    "status": "completed",
                    "summary": "First checkpoint done",
                    "phase": "persist",
                    "completed_at": "2026-04-10T12:01:00Z",
                    "attempts": [{"attempt": 1}],
                },
                {
                    "poll": 3,
                    "status": "completed",
                    "summary": "Third checkpoint done",
                    "phase": "request",
                    "completed_at": "2026-04-10T12:03:00Z",
                    "attempts": [{"attempt": 1}, {"attempt": 2}],
                },
            ],
            "retry_history": [
                {
                    "poll": 2,
                    "attempt": 1,
                    "error": "provider timeout",
                    "transient": True,
                    "created_at": "2026-04-10T12:02:00Z",
                },
            ],
            "active_checkpoint": {
                "poll": 4,
                "status": "running",
                "phase": "request",
                "last_message": "Submitting analysis",
                "attempts": [{"attempt": 1}, {"attempt": 2}],
                "progress": [],
            },
        }

        with patch.object(
            api_mod,
            "export_cli_session",
            return_value={
                "session": {"session_id": "sess-watch", "checkpoint_count": 3},
                "events": [],
                "outputs": [],
                "watch_state": fake_watch_state,
            },
        ):
            resp = await api_mod.api_agent_session_detail_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["supports_interventions"] is True

        insights = payload.get("watch_insights", {})
        assert isinstance(insights, dict)

        # Recent checkpoints
        checkpoints = insights["recent_checkpoints"]
        assert len(checkpoints) == 2
        assert checkpoints[-1]["poll"] == 3
        assert checkpoints[-1]["status"] == "completed"
        assert checkpoints[-1]["summary"] == "Third checkpoint done"
        assert checkpoints[-1]["phase"] == "request"
        assert checkpoints[-1]["completed_at"] == "2026-04-10T12:03:00Z"
        assert checkpoints[-1]["attempt_count"] == 2

        # Retry history
        retries = insights["retry_history"]
        assert len(retries) == 1
        assert retries[0]["poll"] == 2
        assert retries[0]["attempt"] == 1
        assert retries[0]["error"] == "provider timeout"
        assert retries[0]["transient"] is True

        # Latest summary
        assert insights["latest_checkpoint_summary"] == "Analysis complete — 2 issues flagged"

        # Active phase derived from active_checkpoint
        assert insights["active_phase"] == "request"
        assert insights["active_attempt"] == 2

        # Poll / retry_limit scalars
        assert insights["poll_count"] == 4
        assert insights["retry_limit"] == 3

    async def test_agent_session_detail_watch_insights_empty_when_no_watch_state(self):
        """watch_insights is empty dict when session has no watch_state."""
        req = _fake_request()
        req.match_info = {"session_id": "sess-plain"}

        with patch.object(
            api_mod,
            "export_cli_session",
            return_value={
                "session": {"session_id": "sess-plain"},
                "events": [],
                "outputs": [],
            },
        ):
            resp = await api_mod.api_agent_session_detail_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload.get("watch_insights") == {}
        assert payload.get("supports_interventions") is False

    async def test_build_watch_insights_limits_to_five_entries(self):
        """_build_watch_insights caps recent_checkpoints and retry_history to 5."""
        many_checkpoints = [
            {"poll": i, "status": "completed", "summary": f"cp {i}", "completed_at": "", "attempts": []}
            for i in range(10)
        ]
        many_retries = [
            {"poll": i, "attempt": 1, "error": f"err {i}", "transient": True, "created_at": ""}
            for i in range(8)
        ]
        watch_state = {
            "checkpoints": many_checkpoints,
            "retry_history": many_retries,
            "last_summary": "",
            "poll_count": 10,
            "retry_limit": 3,
            "active_checkpoint": {},
        }
        result = api_mod._build_watch_insights(watch_state)
        assert len(result["recent_checkpoints"]) == 5
        assert len(result["retry_history"]) == 5
        # Last 5 should be the tail entries
        assert result["recent_checkpoints"][-1]["poll"] == 9
        assert result["retry_history"][-1]["poll"] == 7

    async def test_agent_session_detail_includes_linked_plan_and_task_context(self):
        req = _fake_request()
        req.match_info = {"session_id": "sess-123"}
        fake_session_summary = SimpleNamespace(
            session_id="sess-123",
            title="Analyze repo",
            cwd="/workspace",
            files=[],
            plan_id="plan-77",
            task_id="task-42",
            status="active",
            created_at="2026-04-10T12:00:00Z",
            updated_at="2026-04-10T12:05:00Z",
            last_command="analyze",
            last_summary="summarized repo",
            command_count=3,
            file_edit_count=1,
            output_count=1,
            automation_mode="watch",
            automation_status="watching",
            watch_interval_seconds=30,
            checkpoint_count=2,
            last_checkpoint_at="2026-04-10T12:04:00Z",
        )
        fake_plan = SimpleNamespace(
            plan_id="plan-77",
            goal="Ship control plane",
            status="in-progress",
            initiator="user:cli",
            channel_id=0,
            created_at="2026-04-10T12:00:00Z",
            updated_at="2026-04-10T12:05:00Z",
            lessons=[],
            context={},
            steps=[
                SimpleNamespace(num=1, description="Audit APIs", status="done", output="", worker_id="", depends_on=[], is_complete=True),
                SimpleNamespace(num=2, description="Wire dashboard", status="in-progress", output="", worker_id="", depends_on=[1], is_complete=False),
            ],
        )

        with patch.object(
            api_mod,
            "export_cli_session",
            return_value={
                "session": {"session_id": "sess-123", "plan_id": "plan-77", "task_id": "task-42", "checkpoint_count": 2},
                "events": [{"kind": "analyze", "content": "summarize repo"}],
                "outputs": [{"name": "report.md"}],
                "watch_state": {"goal": "watch for regressions"},
            },
        ), patch.object(api_mod, "list_cli_sessions", return_value=[fake_session_summary]), patch.dict(
            "sys.modules",
            {
                "agent_loop": MagicMock(load_plan=MagicMock(return_value=fake_plan), list_plans=MagicMock(return_value=[fake_plan])),
                "mission_control": MagicMock(
                    _load_tasks=MagicMock(
                        return_value={
                            "tasks": [
                                {
                                    "id": "task-42",
                                    "title": "Review dashboard",
                                    "status": "in_progress",
                                    "priority": "high",
                                    "description": "Validate the new control-plane views.",
                                    "subtasks": [{"title": "API", "done": True}],
                                    "comments": [{"author": "Dave", "text": "Looks good"}],
                                }
                            ]
                        }
                    )
                ),
            },
        ):
            resp = await api_mod.api_agent_session_detail_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["plan"]["plan_id"] == "plan-77"
        assert payload["plan"]["current_step"]["description"] == "Wire dashboard"
        assert payload["task"]["id"] == "task-42"
        assert payload["task"]["source"] == "mission-control"
        assert payload["session"]["plan_goal"] == "Ship control plane"
        assert payload["session"]["task_title"] == "Review dashboard"

    async def test_agent_session_intervention_handler_queues_watch_action(self):
        req = _fake_request(method="POST", json_payload={"reason": "Need fresh output"})
        req.match_info = {"session_id": "sess-123", "action": "force-checkpoint"}
        req.headers = {"X-OpenClaw-Actor": "dashboard-ui"}

        with patch.object(api_mod, "require_cli_session", return_value=SimpleNamespace(session_id="sess-123")), patch.object(
            api_mod,
            "load_cli_watch_state",
            return_value={"status": "running", "mode": "analyze", "interventions": []},
        ), patch.object(
            api_mod,
            "queue_cli_watch_intervention",
            return_value={"request_id": "ctl-1", "action": "force-checkpoint", "status": "pending"},
        ) as queue_intervention, patch.object(
            api_mod,
            "export_cli_session",
            return_value={"session": {"session_id": "sess-123"}, "watch_state": {"interventions": [{"status": "pending"}]}},
        ):
            resp = await api_mod.api_agent_session_intervention_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["intervention"]["action"] == "force-checkpoint"
        assert payload["pending_count"] == 1
        queue_intervention.assert_called_once()


class TestControlPlaneApis:
    async def test_plans_list_and_detail_include_linked_context(self):
        req = _fake_request(query={"status": "in-progress", "limit": "5"})
        detail_req = _fake_request()
        detail_req.match_info = {"plan_id": "plan-9"}
        fake_session = SimpleNamespace(
            session_id="sess-9",
            title="Plan session",
            cwd="/workspace",
            files=[],
            plan_id="plan-9",
            task_id="task-9",
            status="active",
            created_at="2026-04-10T12:00:00Z",
            updated_at="2026-04-10T12:10:00Z",
            last_command="plan",
            last_summary="working",
            command_count=1,
            file_edit_count=0,
            output_count=0,
            automation_mode="",
            automation_status="",
            watch_interval_seconds=0,
            checkpoint_count=0,
            last_checkpoint_at="",
        )
        fake_plan = SimpleNamespace(
            plan_id="plan-9",
            goal="Add control-plane endpoints",
            status="in-progress",
            initiator="user:cli",
            channel_id=0,
            created_at="2026-04-10T11:59:00Z",
            updated_at="2026-04-10T12:10:00Z",
            lessons=["Keep it additive"],
            context={"summary": "Dashboard work"},
            steps=[
                SimpleNamespace(num=1, description="Add APIs", status="done", output="", worker_id="", depends_on=[], is_complete=True),
                SimpleNamespace(num=2, description="Add UI", status="in-progress", output="In progress", worker_id="", depends_on=[1], is_complete=False),
            ],
        )

        with patch.object(api_mod, "list_cli_sessions", return_value=[fake_session]), patch.dict(
            "sys.modules",
            {
                "agent_loop": MagicMock(load_plan=MagicMock(return_value=fake_plan), list_plans=MagicMock(return_value=[fake_plan])),
                "mission_control": MagicMock(_load_tasks=MagicMock(return_value={"tasks": [{"id": "task-9", "title": "Review", "status": "review"}]})),
            },
        ):
            resp = await api_mod.api_plans_handler(req)
            detail_resp = await api_mod.api_plan_detail_handler(detail_req)

        payload = json.loads(resp.text)
        detail_payload = json.loads(detail_resp.text)
        assert payload["plans"][0]["plan_id"] == "plan-9"
        assert payload["plans"][0]["linked_session_count"] == 1
        assert detail_payload["ok"] is True
        assert detail_payload["plan"]["steps"][1]["description"] == "Add UI"
        assert detail_payload["linked_sessions"][0]["session_id"] == "sess-9"
        assert detail_payload["linked_tasks"][0]["id"] == "task-9"

    async def test_unified_task_status_merges_mission_control_and_scheduled_tasks(self):
        req = _fake_request(query={"limit": "10"})
        detail_req = _fake_request()
        detail_req.match_info = {"source": "scheduled", "task_id": "sched-1"}
        fake_session = SimpleNamespace(
            session_id="sess-1",
            title="Scheduler watch",
            cwd="/workspace",
            files=[],
            plan_id="plan-1",
            task_id="sched-1",
            status="active",
            created_at="2026-04-10T12:00:00Z",
            updated_at="2026-04-10T12:10:00Z",
            last_command="watch",
            last_summary="watching",
            command_count=1,
            file_edit_count=0,
            output_count=0,
            automation_mode="watch",
            automation_status="watching",
            watch_interval_seconds=60,
            checkpoint_count=0,
            last_checkpoint_at="",
        )
        scheduled_task = SimpleNamespace(
            task_id="sched-1",
            action="nightly_report",
            interval_minutes=60,
            cron_expression="",
            cron_hour=-1,
            cron_minute=0,
            prompt="run nightly report",
            last_run="2026-04-10T11:00:00Z",
            last_result="OK",
            next_run_str="in 50m",
            enabled=True,
            created_by="dashboard",
            created_at="2026-04-10T10:00:00Z",
            run_count=2,
            args={"scope": "all"},
        )
        fake_scheduler = MagicMock(list_tasks=MagicMock(return_value=[scheduled_task]), get=MagicMock(return_value=scheduled_task))
        fake_plan = SimpleNamespace(
            plan_id="plan-1",
            goal="Control plane",
            status="in-progress",
            initiator="user:cli",
            channel_id=0,
            created_at="2026-04-10T10:00:00Z",
            updated_at="2026-04-10T12:10:00Z",
            lessons=[],
            context={},
            steps=[],
        )

        with patch.object(api_mod, "list_cli_sessions", return_value=[fake_session]), patch.dict(
            "sys.modules",
            {
                "mission_control": MagicMock(
                    _load_tasks=MagicMock(
                        return_value={
                            "tasks": [
                                {
                                    "id": "task-1",
                                    "title": "Mission task",
                                    "status": "in_progress",
                                    "priority": "medium",
                                    "description": "Track review work",
                                    "subtasks": [{"title": "API", "done": False}],
                                    "comments": [],
                                }
                            ]
                        }
                    )
                ),
                "scheduler": MagicMock(scheduler=fake_scheduler),
                "agent_loop": MagicMock(load_plan=MagicMock(return_value=fake_plan), list_plans=MagicMock(return_value=[fake_plan])),
            },
        ):
            resp = await api_mod.api_task_status_handler(req)
            detail_resp = await api_mod.api_task_status_detail_handler(detail_req)

        payload = json.loads(resp.text)
        detail_payload = json.loads(detail_resp.text)
        sources = {item["source"] for item in payload["tasks"]}
        assert {"mission-control", "scheduled"} <= sources
        scheduled = next(item for item in payload["tasks"] if item["id"] == "sched-1")
        assert scheduled["linked_session_count"] == 1
        assert detail_payload["ok"] is True
        assert detail_payload["task"]["id"] == "sched-1"
        assert detail_payload["linked_plans"][0]["plan_id"] == "plan-1"

    def test_serialize_approval_includes_cli_context(self):
        approval = SimpleNamespace(
            request_id="req-1",
            action="shell.exec",
            target="git status",
            detail="cwd=/workspace",
            risk_level=SimpleNamespace(value="HIGH"),
            requester_name="openclaw-cli",
            resolver_name=None,
            session_id="sess-1",
            plan_id="plan-1",
            task_id="task-1",
            age_seconds=12,
            resolved=False,
            approved=False,
            is_expired=False,
        )

        payload = api_mod._serialize_approval(approval, now_epoch=1_700_000_000)

        assert payload["session_id"] == "sess-1"
        assert payload["plan_id"] == "plan-1"
        assert payload["task_id"] == "task-1"
        assert payload["detail"] == "cwd=/workspace"


class TestScheduleDashboardApis:
    async def test_schedule_toggle_handler_uses_scheduler(self):
        req = _fake_request(method="POST")
        req.match_info = {"task_id": "sched-1"}
        fake_task = SimpleNamespace(
            task_id="sched-1",
            action="nightly_report",
            interval_minutes=60,
            cron_expression="",
            cron_hour=-1,
            cron_minute=0,
            prompt="",
            last_run="",
            last_result="OK",
            next_run_str="in 60m",
            enabled=False,
            created_by="dashboard",
            run_count=2,
            args={"scope": "all"},
        )
        scheduler_mod = MagicMock(scheduler=MagicMock(toggle=MagicMock(return_value=False), get=MagicMock(return_value=fake_task)))

        with patch.dict("sys.modules", {"scheduler": scheduler_mod}):
            resp = await api_mod.api_schedule_toggle_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["task"]["id"] == "sched-1"
        assert payload["task"]["enabled"] is False

    async def test_schedule_update_handler_updates_task(self):
        req = _fake_request(
            method="POST",
            json_payload={"name": "nightly_report", "prompt": "run report", "cron_expression": "0 6 * * *", "interval_minutes": ""},
        )
        req.match_info = {"task_id": "sched-2"}
        updated_task = SimpleNamespace(
            task_id="sched-2",
            action="nightly_report",
            interval_minutes=0,
            cron_expression="0 6 * * *",
            cron_hour=-1,
            cron_minute=0,
            prompt="run report",
            last_run="",
            last_result="",
            next_run_str="Mon 06:00",
            enabled=True,
            created_by="dashboard",
            run_count=0,
            args={},
        )
        fake_scheduler = MagicMock(update=MagicMock(return_value=updated_task))

        with patch.dict("sys.modules", {"scheduler": MagicMock(scheduler=fake_scheduler)}):
            resp = await api_mod.api_schedule_update_handler(req)

        payload = json.loads(resp.text)
        assert payload["ok"] is True
        fake_scheduler.update.assert_called_once()
        assert payload["task"]["cron_expression"] == "0 6 * * *"


class TestApiAgentAsk:
    async def test_uses_supplied_user_name(self):
        fake_stream = MagicMock()
        fake_run_stream = AsyncMock(
            return_value=SimpleNamespace(
                response_text="Done",
                model_used="gemini",
                final_meta={"total_tokens": 12},
            )
        )
        fake_with_requested_item_target = MagicMock(side_effect=lambda meta, question: dict(meta))
        fake_quality_score = MagicMock(return_value={"status": "high", "score": 92})
        fake_repair = AsyncMock(
            return_value={
                "response_text": "Done",
                "model_used": "gemini",
                "final_meta": {"total_tokens": 12},
            }
        )
        req = _fake_request(
            method="POST",
            json_payload={
                "prompt": "summarize the overnight alerts",
                "model": "auto",
                "history": [{"role": "user", "content": "Earlier"}],
                "user_name": "CLI user",
            },
        )

        with patch.dict(
            "sys.modules",
            {
                "llm": MagicMock(chat_stream=fake_stream),
                "ask_orchestrator": MagicMock(run_ask_stream=fake_run_stream),
                "quality_helpers": MagicMock(
                    _with_requested_item_target=fake_with_requested_item_target,
                    _safe_score_answer_quality=fake_quality_score,
                    _run_quality_auto_repair=fake_repair,
                    _build_ask_recovery_block=MagicMock(return_value=None),
                ),
            },
        ):
            resp = await api_mod.api_agent_ask_handler(req)

        payload = json.loads(resp.text)
        assert payload["response"] == "Done"
        assert payload["model"] == "gemini"
        assert payload["tokens"] == 12
        fake_run_stream.assert_awaited_once()
        kwargs = fake_run_stream.await_args.kwargs
        assert kwargs["llm_stream"] is fake_stream
        assert kwargs["user_message"] == "summarize the overnight alerts"
        assert kwargs["history"] == [{"role": "user", "content": "Earlier"}]
        assert kwargs["user_name"] == "CLI user"
        assert kwargs["model_preference"] == "auto"

    async def test_appends_recovery_block_when_present(self):
        fake_stream = MagicMock()
        fake_run_stream = AsyncMock(
            return_value=SimpleNamespace(
                response_text="Short answer",
                model_used="gemini",
                final_meta={},
            )
        )
        req = _fake_request(
            method="POST",
            json_payload={"prompt": "hello"},
        )

        with patch.dict(
            "sys.modules",
            {
                "llm": MagicMock(chat_stream=fake_stream),
                "ask_orchestrator": MagicMock(run_ask_stream=fake_run_stream),
                "quality_helpers": MagicMock(
                    _with_requested_item_target=MagicMock(side_effect=lambda meta, question: dict(meta)),
                    _safe_score_answer_quality=MagicMock(return_value={"status": "high"}),
                    _run_quality_auto_repair=AsyncMock(
                        return_value={
                            "response_text": "Short answer",
                            "model_used": "gemini",
                            "final_meta": {},
                        }
                    ),
                    _build_ask_recovery_block=MagicMock(return_value="\n\nRecovery note: broaden query."),
                ),
            },
        ):
            resp = await api_mod.api_agent_ask_handler(req)

        payload = json.loads(resp.text)
        assert payload["response"].endswith("Recovery note: broaden query.")

    async def test_rejects_non_list_history(self):
        req = _fake_request(
            method="POST",
            json_payload={"prompt": "hello", "history": {"role": "user", "content": "bad"}},
        )

        resp = await api_mod.api_agent_ask_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert payload["error"] == "history must be a list"


class TestQualityMetricsApi:
    @pytest.mark.asyncio
    async def test_quality_metrics_returns_signal_summary(self):
        req = _fake_request()

        metrics_mod = MagicMock(
            get_quality_event_snapshot=MagicMock(
                return_value={
                    "total_events": 12,
                    "event_counts": {
                        "search_fallback_activation": 5,
                        "search_low_results_incident": 2,
                        "recap_fallback_activation": 3,
                        "recap_partial_coverage_warning": 2,
                        "ask_feedback_helpful": 7,
                        "ask_feedback_not_helpful": 1,
                        "ask_feedback_accepted": 8,
                        "ask_feedback_suppressed": 3,
                        "ask_feedback_suppressed_dedupe": 2,
                        "ask_feedback_suppressed_rate_limited_user": 1,
                    },
                    "context_counts": {"sports_recap": 10, "search": 2},
                    "top_events": [
                        {"event": "search_fallback_activation", "count": 5},
                        {"event": "recap_fallback_activation", "count": 3},
                    ],
                    "top_contexts": [
                        {"context": "sports_recap", "count": 10},
                    ],
                }
            )
        )
        error_tracker_mod = MagicMock(
            get_recent_outcomes=MagicMock(
                return_value=[
                    {
                        "explainability": {
                            "answer_quality": {
                                "status": "low",
                                "reasons": ["Limited item coverage detected."],
                            },
                            "answer_quality_retry": {"attempted": True, "outcome": "improved"},
                        },
                    },
                    {
                        "explainability": {
                            "answer_quality": {"status": "high", "reasons": []},
                            "answer_quality_retry": {"attempted": False, "outcome": "skipped"},
                        },
                    },
                ]
            )
        )

        with (
            patch.dict("sys.modules", {"metrics_collector": metrics_mod, "error_tracker": error_tracker_mod}),
            patch(
                "dashboard.api_handlers._build_offline_quality_calibration_payload",
                return_value={
                    "available": True,
                    "drift": {
                        "baseline_available": True,
                        "status": "drifted",
                        "regressed_metrics": ["coverage_proxy"],
                        "severity": {"level": "severe", "severe": True, "score": 5, "reasons": ["test"]},
                    },
                },
            ),
        ):
            resp = await mod.api_quality_metrics_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["total_events"] == 12
        assert payload["signals"]["search_fallback_activation"] == 5
        assert payload["signals"]["recap_partial_coverage_warning"] == 2
        assert payload["status"] == "degraded"
        assert payload["calibration_drift"]["severe"] is True
        assert payload["calibration_drift"]["severity_level"] == "severe"
        assert payload["score_distribution"]["high"] == 1
        assert payload["score_distribution"]["low"] == 1
        assert payload["low_confidence"]["prompt_count"] >= 1
        assert payload["low_confidence"]["top_reasons"][0]["reason"] == "Limited item coverage detected."
        assert payload["retry_outcomes"]["attempted"] >= 1
        assert payload["retry_outcomes"]["improved"] >= 1
        assert payload["retry_outcomes"]["skipped"] >= 1
        assert payload["feedback"]["helpful"] == 7
        assert payload["feedback"]["not_helpful"] == 1
        assert payload["feedback"]["total"] == 8
        assert payload["feedback"]["helpful_rate"] == 0.875
        assert payload["feedback"]["accepted"] == 8
        assert payload["feedback"]["suppressed"] == 3
        assert payload["feedback"]["suppressed_dedupe"] == 2
        assert payload["feedback"]["suppressed_rate_limited"] == 1
        assert isinstance(payload["domain_trends"], list)
        assert len(payload["domain_trends"]) <= 6
        assert any(item.get("domain") == "search" for item in payload["domain_trends"])
        assert isinstance(payload["top_recurring_failures"], list)
        assert payload["top_recurring_failures"][0]["count"] >= payload["top_recurring_failures"][-1]["count"]
        assert isinstance(payload["top_quality_failure_categories"], list)
        assert len(payload["top_quality_failure_categories"]) <= 6
        assert payload["top_quality_failure_categories"][0]["count"] >= payload["top_quality_failure_categories"][-1]["count"]
        assert "quality_failure_categories" in payload
        assert isinstance(payload["quality_failure_categories"]["counts"], dict)
        assert payload["quality_failure_categories"]["counts"].get("requested_item_shortfall", 0) >= 2
        assert "recent_signal_slices" in payload
        assert set(payload["recent_signal_slices"].keys()) == {"mitigation", "degrade"}
        assert isinstance(payload["recent_signal_slices"]["degrade"], list)

    @pytest.mark.asyncio
    async def test_quality_metrics_fallback_contains_new_sections(self):
        req = _fake_request()
        metrics_mod = MagicMock(
            get_quality_event_snapshot=MagicMock(side_effect=RuntimeError("boom"))
        )
        with patch.dict("sys.modules", {"metrics_collector": metrics_mod}):
            resp = await mod.api_quality_metrics_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["score_distribution"] == {"high": 0, "medium": 0, "low": 0}
        assert payload["calibration_drift"]["severity_level"] == "unknown"
        assert payload["calibration_drift"]["severe"] is False
        assert payload["low_confidence"]["prompt_count"] == 0
        assert payload["retry_outcomes"]["attempted"] == 0
        assert payload["feedback"] == {
            "helpful": 0,
            "not_helpful": 0,
            "total": 0,
            "helpful_rate": None,
            "accepted": 0,
            "suppressed": 0,
            "suppressed_dedupe": 0,
            "suppressed_rate_limited": 0,
        }
        assert payload["domain_trends"] == []
        assert payload["top_recurring_failures"] == []
        assert payload["top_quality_failure_categories"] == []
        assert payload["quality_failure_categories"] == {
            "counts": {},
            "top": [],
            "total_classified_failures": 0,
            "total_failure_events": 0,
        }
        assert payload["recent_signal_slices"] == {"mitigation": [], "degrade": []}


class TestSmsDashboardApi:
    @pytest.mark.asyncio
    async def test_sms_settings_get_requires_user_id(self):
        req = _fake_request(query={})
        resp = await mod.api_sms_settings_handler(req)
        assert resp.content_type == "application/json"
        assert "needs_user_id" in resp.text

    @pytest.mark.asyncio
    async def test_sms_settings_post_updates_phone(self, monkeypatch, tmp_path):
        import sms_ux

        monkeypatch.setattr(sms_ux, "sms_prefs", sms_ux.SMSPrefsStore(tmp_path / "sms_prefs.json"))
        req = _fake_request(
            method="POST",
            json_payload={"user_id": 12345, "phone_number": "+15551234567"},
        )

        resp = await mod.api_sms_settings_handler(req)

        assert resp.content_type == "application/json"
        assert "ok" in resp.text
        assert "+15551234567" in resp.text

    @pytest.mark.asyncio
    async def test_sms_status_and_history_returns_data(self, monkeypatch, tmp_path):
        import sms_ux

        monkeypatch.setattr(sms_ux, "sms_prefs", sms_ux.SMSPrefsStore(tmp_path / "sms_prefs.json"))
        prefs = sms_ux.UserSMSPrefs(
            user_id=333,
            phone_number="+15550001111",
            is_verified=True,
            recent_sends=[
                {
                    "sent_at": 1_700_000_000.0,
                    "provider": "twilio",
                    "sid": "SM123",
                    "status": "queued",
                    "preview": "hello",
                    "to": "+15550001111",
                }
            ],
        )
        await sms_ux.sms_prefs.update(prefs)

        status_req = _fake_request(query={"user_id": "333"})
        status_resp = await mod.api_sms_status_handler(status_req)
        assert status_resp.content_type == "application/json"
        assert "configured" in status_resp.text
        assert "true" in status_resp.text.lower()

        history_req = _fake_request(query={"user_id": "333", "limit": "5"})
        history_resp = await mod.api_sms_history_handler(history_req)
        assert history_resp.content_type == "application/json"
        assert "SM123" in history_resp.text


class TestChannelMemoryInspectorApi:
    @pytest.mark.asyncio
    async def test_inspect_requires_channel_id(self):
        req = _fake_request(query={})
        resp = await mod.api_channel_memory_inspect_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "channel_id" in payload["error"]

    @pytest.mark.asyncio
    async def test_inspect_returns_scoped_summary(self):
        req = _fake_request(query={"channel_id": "123", "thread_id": "456", "limit": "3", "include_anchor": "1"})
        vector_store_mock = MagicMock(
            get_scoped_memory_summary=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "collections": {"memories": {"count": 1, "latest": [{"id": "mem_1"}]}},
                    "total_count": 1,
                    "anchor": {"present": False},
                    "alerts": {"count": 1, "items": [{"category": "scope_guard_block", "message": "blocked"}]},
                    "compaction": {"count": 2, "items": [{"collection": "memories", "pruned_count": 5}]},
                }
            )
        )
        with patch.dict("sys.modules", {"vector_store": vector_store_mock}):
            resp = await mod.api_channel_memory_inspect_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["scope"]["channel_id"] == "123"
        assert payload["warnings"]["scoped_recall_alerts"] == 1
        assert payload["warnings"]["recent_compactions"] == 2
        vector_store_mock.get_scoped_memory_summary.assert_awaited_once_with(
            channel_id="123",
            thread_id="456",
            latest_limit=3,
            include_anchor=True,
        )

    @pytest.mark.asyncio
    async def test_action_clear_runs_clear_and_audit(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "123",
                "thread_id": "456",
                "actor": "dashboard-ui",
                "confirm": True,
            },
        )
        vector_store_mock = MagicMock(
            clear_scoped_memory=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "deleted": {"memories": 2, "conversations": 1, "research": 0},
                    "total_deleted": 3,
                }
            )
        )
        audit_mock = MagicMock(audit_log=MagicMock())

        with patch.dict("sys.modules", {"vector_store": vector_store_mock, "audit": audit_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["clear"]["total_deleted"] == 3
        vector_store_mock.clear_scoped_memory.assert_awaited_once_with(channel_id="123", thread_id="456")
        assert audit_mock.audit_log.call_count == 1

    @pytest.mark.asyncio
    async def test_action_clear_requires_confirmation_preview(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "123",
                "thread_id": "456",
                "actor": "dashboard-ui",
            },
        )
        vector_store_mock = MagicMock(
            get_scoped_memory_summary=AsyncMock(
                return_value={
                    "scope": {"channel_id": "123", "thread_id": "456"},
                    "collections": {"memories": {"count": 2, "latest": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}},
                    "total_count": 2,
                    "anchor": {"present": True},
                    "alerts": {"count": 0, "items": []},
                }
            )
        )
        with patch.dict("sys.modules", {"vector_store": vector_store_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 409
        payload = json.loads(resp.text)
        assert payload["requires_confirmation"] is True
        assert payload["preview"]["total_entries"] == 2
        assert payload["preview"]["collections"]["memories"]["count"] == 2
        assert len(payload["preview"]["collections"]["memories"]["latest"]) == 2
        vector_store_mock.get_scoped_memory_summary.assert_awaited_once_with(
            channel_id="123",
            thread_id="456",
            latest_limit=5,
            include_anchor=True,
        )

    @pytest.mark.asyncio
    async def test_action_retrain_runs_dream_cycle(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "retrain",
                "channel_id": "123",
                "actor": "dashboard-ui",
            },
        )

        class _FakeCycle:
            def __init__(self):
                self.run = AsyncMock(return_value="dream report")

        dream_cycle_mock = MagicMock(DreamCycle=_FakeCycle)
        audit_mock = MagicMock(audit_log=MagicMock())
        with patch.dict("sys.modules", {"dream_cycle": dream_cycle_mock, "audit": audit_mock}):
            resp = await mod.api_channel_memory_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["retrain"]["triggered"] is True
        assert payload["scope"]["thread_id"] is None
        assert audit_mock.audit_log.call_count == 1

    @pytest.mark.asyncio
    async def test_action_rejects_invalid_scope_id(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "action": "clear",
                "channel_id": "chan-1",
            },
        )
        resp = await mod.api_channel_memory_action_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "numeric" in payload["error"]


class TestRunsApi:
    @pytest.mark.asyncio
    async def test_runs_endpoint_exposes_explainability_payload(self):
        req = _fake_request(query={"hours": "24", "limit": "5"})
        fake_entries = [
            {
                "ts": 1_710_000_000.0,
                "trace_id": "trace123abc",
                "user_id": 111,
                "question": "summarize",
                "model_used": "gemini-2.5-pro",
                "success": True,
                "latency_ms": 321,
                "scope_mode": "thread",
                "lock_mode": "prior_report",
                "anchor_id": "report_42",
                "anchor_age_seconds": 75,
                "effective_profile": {"tone": "direct"},
            }
        ]
        with patch.dict("sys.modules", {"error_tracker": MagicMock(get_recent_outcomes=MagicMock(return_value=fake_entries))}):
            resp = await mod.api_runs_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert "runs" in payload
        assert len(payload["runs"]) == 1
        run = payload["runs"][0]
        assert run["scope_mode"] == "thread"
        assert run["lock_mode"] == "prior_report"
        assert run["trace_id"] == "trace123abc"
        assert run["anchor_id"] == "report_42"
        assert run["anchor_age_seconds"] == 75
        assert run["effective_profile_values"] == {"tone": "direct"}
        assert run["profile_values"] == {"tone": "direct"}
        assert run["explainability"]["trace_id"] == "trace123abc"
        assert run["explainability"]["scope_mode"] == "thread"
        assert run["explainability"]["lock_mode"] == "prior_report"
        assert run["explainability"]["anchor_id"] == "report_42"
        assert run["explainability"]["anchor_age_seconds"] == 75
        assert run["explainability"]["effective_profile_values"] == {"tone": "direct"}
        assert payload["filters"]["status"] == ["success"]
        assert payload["filters"]["models"] == ["gemini-2.5-pro"]
        assert payload["filters"]["users"] == ["111"]


class TestQualityEvalApi:
    @pytest.mark.asyncio
    async def test_quality_eval_endpoint_returns_latest_history_and_trend(self):
        req = _fake_request(query={"history": "5"})
        latest = {
            "scorecard_id": 10,
            "timestamp": 1_710_000_123.0,
            "sample_size": 42,
            "summary": {"pass": 12, "fail": 3, "rate": 0.8},
            "metrics": {
                "channel_leakage_prevention": {"pass": 4, "fail": 1, "sample": 5, "rate": 0.8},
                "followup_anchor_correctness": {"pass": 8, "fail": 2, "sample": 10, "rate": 0.8},
            },
        }
        history = [
            latest,
            {
                "scorecard_id": 9,
                "timestamp": 1_709_000_000.0,
                "sample_size": 30,
                "summary": {"pass": 9, "fail": 3, "rate": 0.75},
                "metrics": {
                    "channel_leakage_prevention": {"pass": 3, "fail": 1, "sample": 4, "rate": 0.75},
                    "followup_anchor_correctness": {"pass": 6, "fail": 2, "sample": 8, "rate": 0.75},
                },
            },
        ]
        runtime_state_mock = MagicMock(
            ensure_quality_eval_scorecard=MagicMock(return_value=latest),
            create_quality_eval_scorecard=MagicMock(return_value=latest),
            list_quality_eval_scorecards=MagicMock(return_value=history),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock}):
            resp = await mod.api_quality_eval_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["latest"]["scorecard_id"] == 10
        assert len(payload["history"]) == 2
        assert "summary" in payload["trend"]
        assert "metrics" in payload["trend"]
        assert "channel_leakage_prevention" in payload["trend"]["metrics"]
        assert len(payload["trend"]["metrics"]["channel_leakage_prevention"]) == 2
        assert "calibration" in payload
        assert payload["calibration"]["advisory_only"] is True

    @pytest.mark.asyncio
    async def test_quality_eval_endpoint_exposes_calibration_shape(self):
        req = _fake_request(query={"history": "2", "calibration": "1"})
        latest = {
            "scorecard_id": 11,
            "timestamp": 1_710_000_999.0,
            "sample_size": 12,
            "summary": {"pass": 4, "fail": 1, "rate": 0.8},
            "metrics": {"channel_leakage_prevention": {"pass": 4, "fail": 1, "sample": 5, "rate": 0.8}},
        }
        runtime_state_mock = MagicMock(
            ensure_quality_eval_scorecard=MagicMock(return_value=latest),
            create_quality_eval_scorecard=MagicMock(return_value=latest),
            list_quality_eval_scorecards=MagicMock(return_value=[latest]),
        )
        offline_quality_eval_mock = MagicMock(
            load_replay_fixtures=MagicMock(return_value=[{"id": "case-1"}]),
            load_baseline_report=MagicMock(return_value={"summary": {"coverage_proxy": 0.9}}),
            run_quality_eval=MagicMock(
                return_value={
                    "pass": True,
                    "summary": {"coverage_proxy": 0.92, "warning_rate": 0.2, "max_latency_bucket": "slow"},
                    "calibration": {
                        "advisory_only": True,
                        "auto_apply": False,
                        "drift": {"baseline_available": True, "status": "stable", "metrics": {}},
                        "recommendations": {"advisory_only": True, "auto_apply": False, "proposals": []},
                    },
                }
            ),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock, "offline_quality_eval": offline_quality_eval_mock}):
            resp = await mod.api_quality_eval_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["calibration"]["available"] is True
        assert payload["calibration"]["advisory_only"] is True
        assert payload["calibration"]["auto_apply"] is False
        assert "drift" in payload["calibration"]
        assert "severity" in payload["calibration"]["drift"]
        assert "recommendations" in payload["calibration"]


class TestChannelProfileAssistantApi:
    @pytest.mark.asyncio
    async def test_recommendations_requires_channel_id(self):
        req = _fake_request(query={})
        resp = await mod.api_channel_profile_recommendations_handler(req)
        assert resp.status == 400
        payload = json.loads(resp.text)
        assert "channel_id" in payload["error"]

    @pytest.mark.asyncio
    async def test_recommendations_returns_scope_payload(self):
        req = _fake_request(query={"channel_id": "123", "thread_id": "456"})
        runtime_state_mock = MagicMock(
            refresh_channel_profile_recommendations=MagicMock(return_value=[]),
            list_channel_profile_recommendations=MagicMock(
                return_value=[
                    {
                        "recommendation_id": 9,
                        "channel_id": 123,
                        "thread_id": 456,
                        "profile_field": "table_style",
                        "recommended_value": "copy-safe",
                        "reason": "copy usage",
                        "confidence": 0.8,
                        "status": "suggested",
                    }
                ]
            ),
            get_channel_profile=MagicMock(return_value={"table_style": "discord"}),
            get_channel_profile_usage_signals=MagicMock(return_value={"recap_copy_export": 3}),
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock}):
            resp = await mod.api_channel_profile_recommendations_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["scope"]["channel_id"] == "123"
        assert payload["recommendations"][0]["profile_field"] == "table_style"
        runtime_state_mock.refresh_channel_profile_recommendations.assert_called_once_with(123, thread_id=456)

    @pytest.mark.asyncio
    async def test_recommendation_action_runs_update(self):
        req = _fake_request(
            method="POST",
            json_payload={
                "recommendation_id": 44,
                "action": "approve",
                "actor": "dashboard-ui",
            },
        )
        runtime_state_mock = MagicMock(
            update_channel_profile_recommendation=MagicMock(
                return_value={
                    "recommendation_id": 44,
                    "channel_id": 123,
                    "thread_id": None,
                    "profile_field": "table_style",
                    "recommended_value": "copy-safe",
                    "status": "approved",
                }
            )
        )
        with patch.dict("sys.modules", {"runtime_state": runtime_state_mock, "audit": MagicMock(audit_log=MagicMock())}):
            resp = await mod.api_channel_profile_recommendation_action_handler(req)

        assert resp.status == 200
        payload = json.loads(resp.text)
        assert payload["ok"] is True
        assert payload["recommendation"]["status"] == "approved"
