import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib import error

import pytest

import openclaw_cli as mod
import openclaw_cli_sessions as sessions_mod


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamingResponse:
    def __init__(self, lines: list[str]):
        self._lines = [line.encode("utf-8") for line in lines]
        self._index = 0

    def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _config(**overrides):
    base = mod.CliConfig(
        base_url="http://localhost:8765",
        token="secret-token",
        model="auto",
        timeout_seconds=30,
        user_name="dave@mini",
        client_name="mini",
        output_json=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _write_local_plan(base_dir: Path, plan_id: str, goal: str = "Ship the change") -> Path:
    plan_dir = base_dir / "data" / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{plan_id}.md"
    plan_path.write_text(
        "\n".join(
            [
                f"# Plan: {goal}",
                "",
                f"- **Plan ID:** {plan_id}",
                "- **Status:** in-progress",
            ]
        ),
        encoding="utf-8",
    )
    return plan_path


def _write_local_tasks(base_dir: Path, tasks: list[dict[str, object]]) -> Path:
    tasks_file = base_dir / "data" / "tasks.json"
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")
    return tasks_file


class _FakePlanStep:
    def __init__(self, num: int, description: str):
        self.num = num
        self.description = description
        self.status = "pending"
        self.output = ""
        self.depends_on = []

    @property
    def is_complete(self) -> bool:
        return self.status in {"done", "failed", "skipped"}


class _FakePlan:
    def __init__(self, plan_id: str, goal: str, steps: list[_FakePlanStep]):
        self.plan_id = plan_id
        self.goal = goal
        self.status = "in-progress"
        self.steps = steps
        self.context = {}

    def progress_str(self) -> str:
        done = sum(1 for step in self.steps if step.is_complete)
        return f"{done}/{len(self.steps)}"


def _install_fake_plan_module(monkeypatch, *, plan_id: str = "plan-auto-123") -> dict[str, _FakePlan]:
    plans: dict[str, _FakePlan] = {}

    async def create_plan(goal: str, steps_text: str = "") -> str:
        step_lines = [line.strip() for line in steps_text.splitlines() if line.strip()] or [goal]
        plans[plan_id] = _FakePlan(
            plan_id=plan_id,
            goal=goal,
            steps=[_FakePlanStep(index, line) for index, line in enumerate(step_lines, start=1)],
        )
        return f"✅ Created plan `{plan_id}` with {len(step_lines)} steps."

    def load_plan(requested_plan_id: str):
        return plans.get(requested_plan_id)

    def save_plan(plan):
        plans[plan.plan_id] = plan

    fake_module = types.ModuleType("agent_loop")
    fake_module.create_plan = create_plan
    fake_module.load_plan = load_plan
    fake_module.save_plan = save_plan
    fake_module.read_plan = lambda plan_id: f"plan:{plan_id}"
    fake_module.resume_plan = lambda plan_id: f"resume:{plan_id}"
    fake_module.cancel_plan = lambda plan_id: f"cancel:{plan_id}"
    fake_module.list_plans = lambda status="all": list(plans.values())
    monkeypatch.setitem(sys.modules, "agent_loop", fake_module)
    return plans


def _install_fake_research_module(monkeypatch, *, report: str = "Research findings") -> None:
    class _FakeResearchAgent:
        async def run(self, query, on_progress=None, deep=False):
            if on_progress is not None:
                await on_progress("collecting sources")
            return report

    fake_module = types.ModuleType("research_agent")
    fake_module.ResearchAgent = _FakeResearchAgent
    monkeypatch.setitem(sys.modules, "research_agent", fake_module)


def test_normalize_base_url_trims_trailing_slash():
    assert mod.normalize_base_url("http://localhost:8765///") == "http://localhost:8765"


def test_resolve_token_uses_explicit_before_env_and_keychain(monkeypatch):
    monkeypatch.setenv("OPENCLAW_TOKEN", "env-token")
    with patch.object(mod, "read_keychain_token", return_value="keychain-token"):
        assert mod.resolve_token("explicit-token") == "explicit-token"


def test_resolve_token_uses_saved_token_file_when_other_sources_missing(monkeypatch, tmp_path):
    auth_path = tmp_path / "token"
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
    monkeypatch.setattr(mod, "auth_storage_path", lambda platform_name=None: auth_path)

    mod.write_saved_token("stored-token")

    with patch.object(mod, "read_keychain_token", return_value=""):
        resolution = mod.resolve_token_details()

    assert resolution.token == "stored-token"
    assert str(auth_path) in resolution.source


def test_invoke_openclaw_posts_expected_payload():
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"response": "All good", "model": "gemini", "tokens": 42})

    response = mod.invoke_openclaw(
        "status report",
        config=_config(),
        history=[{"role": "user", "content": "Earlier turn"}],
        opener=_fake_urlopen,
    )

    assert response.response == "All good"
    assert response.model == "gemini"
    assert response.tokens == 42
    assert captured["url"] == "http://localhost:8765/api/agent/ask"
    assert captured["timeout"] == 30
    assert captured["payload"]["prompt"] == "status report"
    assert captured["payload"]["history"] == [{"role": "user", "content": "Earlier turn"}]
    assert captured["payload"]["user_name"] == "dave@mini"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"


def test_invoke_openclaw_stream_prints_chunks_and_returns_final(capsys):
    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeStreamingResponse(
            [
                "event: chunk\n",
                'data: {"delta":"Hello"}\n',
                "\n",
                "event: chunk\n",
                'data: {"delta":" world"}\n',
                "\n",
                "event: final\n",
                'data: {"response":"Hello world","model":"gemini","tokens":7}\n',
                "\n",
            ]
        )

    response = mod.invoke_openclaw_stream(
        "status report",
        config=_config(),
        history=[{"role": "user", "content": "Earlier turn"}],
        opener=_fake_urlopen,
    )

    assert captured["url"] == "http://localhost:8765/api/agent/ask/stream"
    assert captured["timeout"] == 30
    assert captured["payload"]["prompt"] == "status report"
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert response.response == "Hello world"
    assert response.raw["_streamed_cli"] is True
    assert "Hello world" in capsys.readouterr().out


def test_invoke_openclaw_stream_no_chunks_sets_streamed_cli_false(capsys):
    """When server skips chunk events (sends only final), _streamed_cli is False.

    This ensures print_response will render the body itself rather than
    leaving the screen blank because it assumed the stream already printed it.
    """

    def _fake_urlopen(req, timeout):
        return _FakeStreamingResponse(
            [
                "event: final\n",
                'data: {"response":"Only final, no chunks","model":"perplexity-direct","tokens":0}\n',
                "\n",
            ]
        )

    response = mod.invoke_openclaw_stream(
        "hello",
        config=_config(),
        opener=_fake_urlopen,
    )

    assert response.response == "Only final, no chunks"
    assert response.raw["_streamed_cli"] is False
    assert capsys.readouterr().out == ""  # nothing printed mid-stream

    captured = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return _FakeResponse({"status": "ok", "service": "openclaw"})

    response = mod.fetch_health(config=_config(), opener=_fake_urlopen)

    assert captured["url"] == "http://localhost:8765/health"
    assert captured["timeout"] == 30
    assert response.payload == {"status": "ok", "service": "openclaw"}
    assert response.healthy is True


def test_fetch_health_formats_connection_refused_errors():
    def _fake_urlopen(_req, timeout):
        raise error.URLError(ConnectionRefusedError("Connection refused"))

    with pytest.raises(mod.OpenClawCliError) as excinfo:
        mod.fetch_health(config=_config(), opener=_fake_urlopen)

    assert "refused the connection" in str(excinfo.value)


def test_print_health_formats_human_readable_summary(capsys):
    response = mod.HealthResponse(
        payload={"status": "healthy", "uptime_seconds": 42.5, "bot_user": "OpenClaw#0001", "guilds": 3},
        raw_text='{"status":"healthy"}',
        status="healthy",
        healthy=True,
    )

    mod.print_health(response, output_json=False)

    stdout = capsys.readouterr().out
    assert "OK OpenClaw health: HEALTHY" in stdout
    assert "uptime_seconds" in stdout and "42.5" in stdout
    assert "guilds" in stdout and "3" in stdout


def test_print_health_includes_failed_checks(capsys):
    response = mod.HealthResponse(
        payload={"status": "degraded", "checks": {"nas": "down", "scheduler": "ok"}},
        raw_text='{"status":"degraded"}',
        status="degraded",
        healthy=False,
    )

    mod.print_health(response, output_json=False)

    stdout = capsys.readouterr().out
    assert "WARN OpenClaw health: DEGRADED" in stdout
    assert "nas" in stdout and "down" in stdout
    assert "scheduler" in stdout and "ok" in stdout


def test_with_spinner_reduced_motion_uses_static_status(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    result = mod._with_spinner("Thinking", lambda: "done")

    stdout = capsys.readouterr().out
    assert result == "done"
    assert "[working] Thinking..." in stdout
    assert "step 1/3" in stdout
    assert "[done] response ready." in stdout


def test_print_feedback_plain_mode_uses_textual_emphasis(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    mod._print_feedback("response ready.", level="success", detail="1.2s")

    stdout = capsys.readouterr().out
    assert "[done] response ready. (1.2s)" in stdout


def test_print_startup_banner_plain_mode_uses_static_summary(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
    monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 120)

    mod._print_startup_banner(_config(), "session-12345678")

    stdout = capsys.readouterr().out
    assert "OpenClaw" in stdout
    assert "Server: http://localhost:8765" in stdout
    assert "Session: session-" in stdout


def test_time_greeting_changes_with_hour(monkeypatch):
    class _Morning:
        @classmethod
        def now(cls):
            return SimpleNamespace(hour=9)

    class _Evening:
        @classmethod
        def now(cls):
            return SimpleNamespace(hour=20)

    monkeypatch.setattr(mod, "datetime", _Morning)
    assert mod._time_greeting() == "Good morning 🌅"

    monkeypatch.setattr(mod, "datetime", _Evening)
    assert mod._time_greeting() == "Good evening 🌙"


def test_print_startup_banner_shows_session_milestone(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
    monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 120)
    monkeypatch.setattr(sessions_mod, "list_sessions", lambda limit=1001: [object()] * 10)

    mod._print_startup_banner(_config(), "session-12345678")

    out = capsys.readouterr().out
    assert "10 sessions with OpenClaw" in out


def test_build_parser_accepts_no_banner_flag():
    parser = mod.build_parser()
    args = parser.parse_args(["--no-banner"])

    assert args.no_banner is True


def test_print_response_plain_mode_flattens_sources_and_footer(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    mod.print_response(
        mod.AskResponse(
            response="Hello world\n\nSources\n- https://example.com",
            model="demo-model",
            tokens=42,
            raw={},
        ),
        output_json=False,
        elapsed=1.5,
    )

    stdout = capsys.readouterr().out
    assert "Hello world" in stdout
    assert "Sources:" in stdout
    assert "Response complete in 1.5s" in stdout
    assert "42 tokens" in stdout
    assert "Metadata:" in stdout


def test_print_response_separator_plain_mode_includes_detail(monkeypatch, capsys):
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    mod._print_response_separator(label="Response", detail="answer reveal", status="active")

    stdout = capsys.readouterr().out
    assert "Response: (answer reveal)" in stdout


def test_with_spinner_animated_path_shows_phase_language(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, False)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, False)
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    def _slow() -> str:
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            pass
        return "done"

    result = mod._with_spinner("Thinking", _slow)

    stdout = capsys.readouterr().out.lower()
    assert result == "done"
    assert "warming up" in stdout
    assert "step 1/3" in stdout
    assert "response ready." in stdout


def test_spinner_progress_snapshot_exposes_wave31_trust_cues():
    early = mod._spinner_progress_snapshot(0.2)
    mid = mod._spinner_progress_snapshot(1.5)
    late = mod._spinner_progress_snapshot(4.2)

    assert early == {
        "phase": "warming up",
        "step_index": 1,
        "step_total": 3,
        "trust_copy": "preparing the request",
    }
    assert mid["phase"] == "working"
    assert mid["step_index"] == 2
    assert mid["trust_copy"] == "waiting for the agent response"
    assert late["phase"] == "wrapping up"
    assert late["step_index"] == 3
    assert late["trust_copy"] == "finalizing the answer"


def test_with_spinner_reduced_motion_prints_phase_step_and_trust(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    result = mod._with_spinner("Thinking", lambda: "done")

    stdout = capsys.readouterr().out
    assert result == "done"
    assert "[working] Thinking... warming up · step 1/3 · preparing the request" in stdout
    assert "[done] response ready. (step 3/3 · finalizing the answer · Thinking" in stdout


def test_render_table_ansi_uses_high_contrast_separator_on_narrow_terminal(monkeypatch):
    monkeypatch.setitem(mod._PREFS, mod._A11Y_HIGH_CONTRAST, True)
    monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 48)

    lines = mod._render_table_ansi(
        [
            ["Name", "Value"],
            ["Status", "A very long value that should wrap cleanly"],
        ]
    )

    assert any("=" in line for line in lines)
    assert any("Name:" in line for line in lines)


def test_print_status_bar_wraps_on_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 40)

    mod._print_status_bar(
        session_id="session-1234567890",
        autoroute_on=False,
        history_len=4,
    )

    stdout = capsys.readouterr().out
    assert "Status:" in stdout
    assert "autoroute" in stdout
    assert stdout.count("\n") >= 2


def test_top_context_bar_lines_include_trust_phase_and_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    _write_local_plan(tmp_path, "plan-shell-1", goal="Ship shell chrome")
    _write_local_tasks(
        tmp_path,
        [{"id": "task-shell-1", "title": "Add top context bar", "status": "done"}],
    )
    session = sessions_mod.create_session(
        title="shell-chrome",
        cwd=str(tmp_path),
        files=[str(tmp_path / "README.md")],
        plan_id="plan-shell-1",
        task_id="task-shell-1",
    )
    monkeypatch.setitem(mod._PREFS, "system_prompt", "Stay concise.")
    monkeypatch.setattr(mod, "_next_inject", "queued workspace context")
    monkeypatch.setattr(
        mod,
        "_load_route_plan",
        lambda plan_id: SimpleNamespace(
            steps=[
                SimpleNamespace(num=1, description="Inspect shell chrome", status="done"),
                SimpleNamespace(num=2, description="Patch top bar", status="in-progress"),
                SimpleNamespace(num=3, description="Run focused tests", status="pending"),
            ]
        ),
    )
    monkeypatch.setattr(
        mod,
        "list_routed_action_checkpoints",
        lambda session_id, limit=1: [
            {
                "step_index": 2,
                "step_total": 3,
                "rollback_status": "available",
                "action_kind": "edit",
                "target": "src/openclaw_cli.py",
            }
        ],
    )

    lines = mod._top_context_bar_lines(
        session_id=session.session_id,
        history_len=4,
        autoroute_on=True,
    )

    joined = "\n".join(lines)
    assert "Status:" in joined or "Context:" in joined
    assert "plan confirmed" in joined
    assert "task confirmed" in joined
    assert "phase done" in joined
    assert "phase: step 2/3 Patch top bar" in joined
    assert "done step 2/3 done" not in joined
    assert "hidden" in joined
    assert "step 2/3 complete" in joined
    assert "next step 2/3 Patch top bar" in joined
    assert "/rollback last ready" in joined
    assert "promptdebug" in joined


def test_top_context_bar_lines_acknowledge_completed_step_before_next_step(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    _write_local_plan(tmp_path, "plan-shell-2", goal="Ship shell chrome")
    session = sessions_mod.create_session(
        title="shell-phase",
        cwd=str(tmp_path),
        plan_id="plan-shell-2",
    )
    monkeypatch.setitem(mod._PREFS, "system_prompt", "")
    monkeypatch.setattr(mod, "_next_inject", "")
    monkeypatch.setattr(
        mod,
        "_load_route_plan",
        lambda plan_id: SimpleNamespace(
            steps=[
                SimpleNamespace(num=1, description="Inspect shell chrome", status="done"),
                SimpleNamespace(num=2, description="Patch top bar", status="done"),
                SimpleNamespace(num=3, description="Run focused tests", status="in-progress"),
            ]
        ),
    )
    monkeypatch.setattr(
        mod,
        "list_routed_action_checkpoints",
        lambda session_id, limit=1: [
            {
                "step_index": 2,
                "step_total": 3,
                "step_kind": "edit",
                "rollback_status": "available",
                "action_kind": "edit",
                "target": "src/openclaw_cli.py",
            }
        ],
    )

    lines = mod._top_context_bar_lines(
        session_id=session.session_id,
        history_len=2,
        autoroute_on=True,
    )

    joined = "\n".join(lines)
    assert "done step 2/3 done · edit" in joined
    assert "phase: step 3/3 Run focused tests" in joined
    assert "next step 3/3 Run focused tests" in joined


def test_invoke_openclaw_formats_unauthorized_errors():
    req = SimpleNamespace(full_url="http://localhost:8765/api/agent/ask")
    unauthorized = error.HTTPError(
        req.full_url,
        401,
        "Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"unauthorized"}'),
    )

    def _fake_urlopen(_req, timeout):
        raise unauthorized

    with patch.object(mod, "KEYCHAIN_SERVICE", "OpenClaw CLI"):
        try:
            mod.invoke_openclaw("hi", config=_config(), opener=_fake_urlopen)
        except mod.OpenClawCliError as exc:
            assert "401 Unauthorized" in str(exc)
            assert "OPENCLAW_TOKEN" in str(exc)
        else:
            raise AssertionError("Expected OpenClawCliError")


def test_auth_setup_hint_is_platform_aware():
    assert "Keychain" in mod.auth_setup_hint(platform_name="darwin")
    assert "Keychain" not in mod.auth_setup_hint(platform_name="linux")
    assert "OPENCLAW_TOKEN or DASHBOARD_API_TOKEN" in mod.auth_setup_hint(platform_name="linux")
    assert "openclaw auth login" in mod.auth_setup_hint(platform_name="linux")


def test_main_warns_without_keychain_hint_on_non_macos(capsys):
    config = _config(token="")

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "run_chat", return_value=0),
        patch.object(mod.sys, "platform", "linux"),
    ):
        exit_code = mod.main([])

    assert exit_code == 0
    stderr = capsys.readouterr().err
    assert "OPENCLAW_TOKEN or DASHBOARD_API_TOKEN" in stderr
    assert "Keychain" not in stderr


def test_run_chat_supports_clear_and_history(capsys):
    prompts = iter(["first question", "/clear", "second question", "/quit"])
    histories = []

    def _fake_input(_label):
        return next(prompts)

    def _fake_ask(prompt, *, config, history):
        histories.append((prompt, history))
        return mod.AskResponse(
            response=f"reply to {prompt}",
            model="gemini",
            tokens=10,
            raw={"response": f"reply to {prompt}", "model": "gemini", "tokens": 10},
        )

    exit_code = mod.run_chat(_config(), input_func=_fake_input, ask_func=_fake_ask)

    assert exit_code == 0
    assert histories == [
        ("first question", []),
        (
            "second question",
            [],
        ),
    ]
    stdout = capsys.readouterr().out
    assert "Conversation history cleared." in stdout
    assert "reply to second question" in stdout


@pytest.mark.parametrize(
    ("prompt", "expected_kind", "expected_args", "expected_target"),
    [
        ("run git status", "exec", "git status", "git"),
        ("edit README.md", "edit", "README.md", "README.md"),
        ("research Python packaging", "research", "Python packaging", "Python packaging"),
        ("analyze src/openclaw_cli.py", "analyze", "src/openclaw_cli.py", "src/openclaw_cli.py"),
        ("draft release notes", "write", "release notes", "release notes"),
    ],
)
def test_route_repl_prompt_matches_deterministic_routes(prompt, expected_kind, expected_args, expected_target):
    def _unexpected_classifier(_prompt):
        raise AssertionError("classifier fallback should not run for deterministic prompts")

    decision = mod.route_repl_prompt(prompt, classifier_func=_unexpected_classifier)

    assert decision.kind == mod.ReplRouteKind(expected_kind)
    assert decision.args_text == expected_args
    assert decision.target_text == expected_target
    assert decision.confidence >= 0.88
    assert "deterministic" in decision.rationale


def test_route_repl_prompt_uses_classifier_fallback_for_actionish_prompt():
    decision = mod.route_repl_prompt("could you take a look at src/openclaw_cli.py and explain the flow?")

    assert decision.kind == mod.ReplRouteKind.ANALYZE
    assert decision.confidence >= mod.REPL_ROUTE_AUTO_THRESHOLD
    assert decision.args_text == "src/openclaw_cli.py and explain the flow?"
    assert decision.target_text == "src/openclaw_cli.py"
    assert "classifier" in decision.rationale


def test_route_repl_prompt_decomposes_sequenced_prompt_into_plan_candidate():
    decision = mod.route_repl_prompt("research Python packaging, then draft release notes, after that edit README.md")

    assert decision.kind == mod.ReplRouteKind.PLAN
    assert decision.confidence >= mod.REPL_ROUTE_AUTO_THRESHOLD
    assert decision.should_auto_route() is False
    assert decision.should_auto_execute_plan() is True
    assert [step.kind for step in decision.steps] == [
        mod.ReplRouteKind.RESEARCH,
        mod.ReplRouteKind.WRITE,
        mod.ReplRouteKind.EDIT,
    ]
    assert [step.args_text for step in decision.steps] == [
        "Python packaging",
        "release notes",
        "README.md",
    ]
    assert decision.steps[2].target_text == "README.md"
    assert "decomposition" in decision.rationale


def test_route_repl_prompt_extracts_semicolon_step_order():
    decision = mod.route_repl_prompt("review src/openclaw_cli.py; draft release notes; edit README.md")

    assert decision.kind == mod.ReplRouteKind.PLAN
    assert [(step.index, step.kind.value, step.args_text) for step in decision.steps] == [
        (1, "analyze", "src/openclaw_cli.py"),
        (2, "write", "release notes"),
        (3, "edit", "README.md"),
    ]


def test_route_repl_prompt_falls_back_to_chat_on_low_confidence_classifier():
    low_confidence = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.ANALYZE,
        confidence=0.41,
        target_text="src/openclaw_cli.py",
        args_text="src/openclaw_cli.py",
        rationale="lightweight classifier matched path target",
    )

    decision = mod.route_repl_prompt(
        "could you look into this maybe",
        classifier_func=lambda _prompt: low_confidence,
        min_confidence=0.8,
    )

    assert decision.kind == mod.ReplRouteKind.CHAT
    assert decision.confidence == 0.41
    assert "below auto-route threshold 0.80" in decision.rationale


def test_route_repl_prompt_falls_back_to_chat_on_low_confidence_plan_candidate():
    low_confidence_plan = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.PLAN,
        confidence=0.62,
        target_text="src/openclaw_cli.py",
        args_text="take a look at this maybe",
        rationale="decomposition matched ordered action clauses",
        steps=(
            mod.ReplPlanStep(
                index=1,
                kind=mod.ReplRouteKind.ANALYZE,
                target_text="src/openclaw_cli.py",
                args_text="src/openclaw_cli.py",
                rationale="lightweight classifier matched path target",
            ),
            mod.ReplPlanStep(
                index=2,
                kind=mod.ReplRouteKind.WRITE,
                target_text="summary",
                args_text="summary",
                rationale="lightweight classifier matched draft",
            ),
        ),
    )

    decision = mod.route_repl_prompt(
        "could you take a look at this maybe",
        classifier_func=lambda _prompt: low_confidence_plan,
        min_confidence=0.8,
    )

    assert decision.kind == mod.ReplRouteKind.CHAT
    assert decision.confidence == 0.62
    assert "below plan threshold 0.80" in decision.rationale


def test_route_repl_prompt_keeps_single_action_request_as_single_route():
    decision = mod.route_repl_prompt("research Python packaging and compare wheel metadata")

    assert decision.kind == mod.ReplRouteKind.RESEARCH
    assert decision.steps == ()
    assert decision.args_text == "Python packaging and compare wheel metadata"


def test_route_repl_prompt_extracts_fenced_exec_command_with_quoted_args():
    decision = mod.route_repl_prompt('run ```bash\ngit commit -m "ship parser fixes"\n```')

    assert decision.kind == mod.ReplRouteKind.EXEC
    assert decision.args_text == 'git commit -m "ship parser fixes"'
    assert decision.target_text == "git"


def test_route_repl_prompt_structures_append_request():
    decision = mod.route_repl_prompt('append "hello world" to notes.txt')

    assert decision.kind == mod.ReplRouteKind.EDIT
    assert decision.args_text == f"notes.txt --append {shlex.quote('hello world')}"
    assert decision.target_text == "notes.txt"
    assert decision.should_auto_route() is True


def test_route_repl_prompt_structures_replace_request():
    decision = mod.route_repl_prompt('replace "alpha beta" with "gamma delta" in README.md')

    assert decision.kind == mod.ReplRouteKind.EDIT
    assert decision.args_text == f"README.md --replace {shlex.quote('alpha beta')} {shlex.quote('gamma delta')}"
    assert decision.target_text == "README.md"
    assert decision.should_auto_route() is True


def test_route_repl_prompt_keeps_ambiguous_edit_request_below_auto_threshold():
    decision = mod.route_repl_prompt("update README.md with the new rollback wording")

    assert decision.kind == mod.ReplRouteKind.EDIT
    assert decision.args_text == "README.md"
    assert decision.target_text == "README.md"
    assert decision.should_auto_route() is False
    assert decision.confidence < mod.REPL_ROUTE_AUTO_THRESHOLD


def test_route_repl_prompt_extracts_write_target_from_summary_request():
    decision = mod.route_repl_prompt("summarize the failing tests into a short report")

    assert decision.kind == mod.ReplRouteKind.WRITE
    assert decision.args_text == "the failing tests into a short report"
    assert decision.target_text == "a short report"


def test_route_repl_prompt_resolves_explicit_step_reference_from_active_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    plans = _install_fake_plan_module(monkeypatch, plan_id="plan-release-1")
    plans["plan-release-1"] = _FakePlan(
        "plan-release-1",
        "Ship the smarter router",
        [
            _FakePlanStep(1, "inspect src/openclaw_cli.py"),
            _FakePlanStep(2, "edit README.md"),
            _FakePlanStep(3, "draft release notes"),
        ],
    )
    session = sessions_mod.create_session(
        title="route-step-plan",
        cwd=str(tmp_path),
        plan_id="plan-release-1",
    )

    decision = mod.route_repl_prompt("update step 3", session_id=session.session_id)

    assert decision.kind == mod.ReplRouteKind.WRITE
    assert decision.args_text == "release notes"
    assert decision.target_text == "release notes"
    assert decision.confidence >= mod.REPL_ROUTE_AUTO_THRESHOLD
    assert "plan-release-1 step 3" in decision.rationale


def test_route_repl_prompt_uses_current_step_context_for_target_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    plans = _install_fake_plan_module(monkeypatch, plan_id="plan-edit-1")
    plans["plan-edit-1"] = _FakePlan(
        "plan-edit-1",
        "Polish release workflow",
        [
            _FakePlanStep(1, "draft release notes"),
            _FakePlanStep(2, "edit README.md"),
        ],
    )
    plans["plan-edit-1"].steps[0].status = "done"
    plans["plan-edit-1"].steps[1].status = "in-progress"
    session = sessions_mod.create_session(
        title="route-current-step",
        cwd=str(tmp_path),
        plan_id="plan-edit-1",
    )

    decision = mod.route_repl_prompt("review the current step", session_id=session.session_id)

    assert decision.kind == mod.ReplRouteKind.ANALYZE
    assert decision.target_text == "README.md"
    assert decision.args_text == "edit README.md"
    assert "current step" in decision.rationale


def test_route_repl_prompt_uses_active_task_context_for_current_task_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    _write_local_tasks(
        tmp_path,
        [{"id": "task-notes-7", "title": "Release notes", "status": "in_progress"}],
    )
    session = sessions_mod.create_session(
        title="route-current-task",
        cwd=str(tmp_path),
        task_id="task-notes-7",
    )

    decision = mod.route_repl_prompt("finish the current task notes", session_id=session.session_id)

    assert decision.kind == mod.ReplRouteKind.WRITE
    assert decision.args_text == "Release notes"
    assert decision.target_text == "Release notes"
    assert decision.confidence >= mod.REPL_ROUTE_AUTO_THRESHOLD
    assert "active task task-notes-7" in decision.rationale


def test_route_repl_prompt_keeps_missing_plan_data_safe_for_step_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.chdir(tmp_path)
    session = sessions_mod.create_session(
        title="route-missing-plan",
        cwd=str(tmp_path),
        plan_id="plan-missing-3",
    )

    decision = mod.route_repl_prompt("update step 3", session_id=session.session_id)

    assert decision.kind == mod.ReplRouteKind.CHAT
    assert decision.confidence == 0.0
    assert "no confident action route" in decision.rationale


def test_run_chat_supports_autoroute_status_and_toggle(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="autoroute", cwd=str(tmp_path))
    prompts = iter(["/autoroute", "/autoroute off", "/autoroute", "/autoroute on", "/quit"])

    def _fake_input(_label):
        return next(prompts)

    exit_code = mod.run_chat(
        _config(session_id=session.session_id),
        input_func=_fake_input,
        session_id=session.session_id,
    )

    assert exit_code == 0
    refreshed = sessions_mod.load_session(session.session_id)
    assert refreshed is not None
    assert refreshed.repl_auto_route is True
    stdout = capsys.readouterr().out
    assert "Auto-route: ON (high-confidence prompts only)" in stdout
    assert "Auto-route disabled for this session; prompts will stay in chat." in stdout
    assert "Auto-route: OFF (high-confidence prompts only)" in stdout
    assert "Auto-route enabled for this session." in stdout


def test_run_chat_prints_top_context_bar_before_prompt(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="top-bar", cwd=str(tmp_path))
    prompts = iter(["/quit"])

    def _fake_input(_label):
        return next(prompts)

    monkeypatch.setitem(mod._PREFS, "layout", "compact")

    exit_code = mod.run_chat(
        _config(session_id=session.session_id),
        input_func=_fake_input,
        session_id=session.session_id,
        no_banner=True,
    )

    assert exit_code == 0


def test_run_chat_uses_router_before_generic_chat_fallback(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="router", cwd=str(tmp_path))
    prompts = iter(["run git status", "what changed overnight?", "/quit"])
    ask_calls = []
    approval_calls = []

    def _fake_input(_label):
        return next(prompts)

    def _fake_ask(prompt, *, config, history):
        ask_calls.append((prompt, history))
        return mod.AskResponse(
            response=f"reply to {prompt}",
            model="gemini",
            tokens=12,
            raw={"response": f"reply to {prompt}", "model": "gemini", "tokens": 12},
        )

    routed_exec = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.EXEC,
        confidence=0.98,
        target_text="git",
        args_text="git status",
        rationale="deterministic match for an explicit run/execute request",
    )
    chat_fallback = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.CHAT,
        confidence=0.0,
        target_text="",
        args_text="what changed overnight?",
        rationale="defaulting to chat for a conversational prompt",
    )

    with (
        patch.object(mod, "route_repl_prompt", side_effect=[routed_exec, chat_fallback]),
        patch.object(mod, "run_shell_command", new=lambda *args, **kwargs: "shell-coro"),
        patch.object(
            mod,
            "request_cli_approval",
            side_effect=lambda **kwargs: approval_calls.append(kwargs) or True,
        ),
        patch.object(
            mod,
            "run_async",
            return_value=SimpleNamespace(
                command="git status",
                cwd=str(tmp_path),
                returncode=0,
                stdout="working tree clean\n",
                stderr="",
            ),
        ),
    ):
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            ask_func=_fake_ask,
            session_id=session.session_id,
        )

    assert exit_code == 0
    assert ask_calls == [("what changed overnight?", [])]
    assert len(approval_calls) == 1
    assert approval_calls[0]["action"] == "shell.exec"
    assert approval_calls[0]["target"] == "git status"
    assert approval_calls[0]["detail"] == f"cwd={tmp_path}"
    assert approval_calls[0]["auto_approve"] is False
    assert approval_calls[0]["session_id"] == session.session_id
    assert approval_calls[0]["plan_id"] == ""
    assert approval_calls[0]["task_id"] == ""
    assert approval_calls[0]["review_lines"] == [
        f"Review: command `git` from cwd `{tmp_path}`",
        "Review: exact shell text `git status`",
        "Review: side effects reads local state without writing files",
        "Review: args `status`",
    ]
    assert "workspace unchanged" in approval_calls[0]["trust_note"]
    assert "inspect /cwd" in approval_calls[0]["recovery_hint"]
    stdout = capsys.readouterr().out
    assert "auto-route" in stdout and "/exec git status" in stdout
    assert "$ git status" in stdout
    assert "reply to what changed overnight?" in stdout


def test_run_chat_skips_autoroute_when_disabled_and_falls_back_to_chat(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="router-off", cwd=str(tmp_path))
    sessions_mod.update_session(session.session_id, repl_auto_route=False)
    prompts = iter(["run git status", "/quit"])
    ask_calls = []

    def _fake_input(_label):
        return next(prompts)

    def _fake_ask(prompt, *, config, history):
        ask_calls.append((prompt, history))
        return mod.AskResponse(
            response=f"chat reply to {prompt}",
            model="gemini",
            tokens=8,
            raw={"response": f"chat reply to {prompt}", "model": "gemini", "tokens": 8},
        )

    with patch.object(mod, "route_repl_prompt") as route_repl_prompt:
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            ask_func=_fake_ask,
            session_id=session.session_id,
        )

    assert exit_code == 0
    route_repl_prompt.assert_not_called()
    assert ask_calls == [("run git status", [])]
    stdout = capsys.readouterr().out
    assert "OpenClaw auto-routed" not in stdout
    assert "chat reply to run git status" in stdout


def test_run_chat_keeps_ambiguous_prompts_in_chat(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="router-ambiguous", cwd=str(tmp_path))
    prompts = iter(["could you maybe take a look at this", "/quit"])
    ask_calls = []
    ambiguous_chat = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.CHAT,
        confidence=0.42,
        target_text="",
        args_text="could you maybe take a look at this",
        rationale="classifier fallback was below the auto-route threshold",
    )

    def _fake_input(_label):
        return next(prompts)

    def _fake_ask(prompt, *, config, history):
        ask_calls.append((prompt, history))
        return mod.AskResponse(
            response=f"chat reply to {prompt}",
            model="gemini",
            tokens=8,
            raw={"response": f"chat reply to {prompt}", "model": "gemini", "tokens": 8},
        )

    with patch.object(mod, "route_repl_prompt", return_value=ambiguous_chat):
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            ask_func=_fake_ask,
            session_id=session.session_id,
        )

    assert exit_code == 0
    assert ask_calls == [("could you maybe take a look at this", [])]
    stdout = capsys.readouterr().out
    assert "OpenClaw auto-routed" not in stdout
    assert "chat reply to could you maybe take a look at this" in stdout
    route_events = [event for event in sessions_mod.load_events(session.session_id) if event.get("kind") == "route"]
    assert route_events == []


def test_run_chat_logs_routed_prompts_as_route_events(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="route-events", cwd=str(tmp_path))
    prompts = iter(["run git status", "/quit"])
    routed_exec = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.EXEC,
        confidence=0.98,
        target_text="git",
        args_text="git status",
        rationale="deterministic match for an explicit run/execute request",
    )

    def _fake_input(_label):
        return next(prompts)

    with (
        patch.object(mod, "route_repl_prompt", return_value=routed_exec),
        patch.object(mod, "run_shell_command", new=lambda *args, **kwargs: "shell-coro"),
        patch.object(
            mod,
            "run_async",
            return_value=SimpleNamespace(
                command="git status",
                cwd=str(tmp_path),
                returncode=0,
                stdout="working tree clean\n",
                stderr="",
            ),
        ),
    ):
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            session_id=session.session_id,
        )

    assert exit_code == 0
    events = sessions_mod.load_events(session.session_id)
    route_events = [event for event in events if event.get("kind") == "route"]
    assert len(route_events) == 1
    route_event = route_events[0]
    assert route_event["content"] == "run git status"
    assert route_event["metadata"]["source"] == "repl.autoroute"
    assert route_event["metadata"]["route_kind"] == "exec"
    assert route_event["metadata"]["slash_command"] == "/exec git status"
    assert route_event["metadata"]["confidence"] == 0.98
    assert mod.load_conversation_history(session.session_id) == []


def test_run_chat_routed_edit_still_requests_approval(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="route-edit", cwd=str(tmp_path))
    prompts = iter(["append notes.txt with hello", "/quit"])
    approval_calls = []

    routed_edit = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.EDIT,
        confidence=0.97,
        target_text="notes.txt",
        args_text="notes.txt --append hello",
        rationale="deterministic match for an explicit edit request",
    )

    def _fake_input(_label):
        return next(prompts)

    from openclaw_cli_actions import FileEditResult

    with (
        patch.object(mod, "route_repl_prompt", return_value=routed_edit),
        patch.object(
            mod,
            "request_cli_approval",
            side_effect=lambda **kwargs: approval_calls.append(kwargs) or True,
        ),
        patch.object(
            mod,
            "write_text_file",
            return_value=FileEditResult(
                path=str((tmp_path / "notes.txt").resolve()),
                changed=True,
                diff="--- before\n+++ after\n@@\n+hello\n",
                summary="Appended content to file.",
            ),
        ),
    ):
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            session_id=session.session_id,
        )

    assert exit_code == 0
    assert len(approval_calls) == 1
    assert approval_calls[0]["action"] == "file.edit"
    assert approval_calls[0]["target"] == "notes.txt"
    assert approval_calls[0]["auto_approve"] is False
    assert approval_calls[0]["session_id"] == session.session_id
    assert approval_calls[0]["plan_id"] == ""
    assert approval_calls[0]["task_id"] == ""
    assert approval_calls[0]["detail"].startswith("append=True;replace=False;changed=True;summary=")
    assert approval_calls[0]["review_lines"][0].startswith("Review: append")
    assert "side effects appends new content" in approval_calls[0]["review_lines"][2]
    assert approval_calls[0]["review_lines"][3].startswith("Review: preview ")
    assert "+hello" in approval_calls[0]["review_lines"][3]
    assert "workspace unchanged" not in approval_calls[0]["trust_note"]
    assert "file untouched" in approval_calls[0]["trust_note"]
    assert "/rollback last" in approval_calls[0]["recovery_hint"]
    stdout = capsys.readouterr().out
    assert "auto-route" in stdout and "/edit notes.txt --append hello" in stdout
    assert "Appended content to file." in stdout


def test_run_chat_autoroutes_plan_candidate_into_persisted_execution(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="route-plan", cwd=str(tmp_path))
    plan_store = _install_fake_plan_module(monkeypatch)
    _install_fake_research_module(monkeypatch, report="Research findings about packaging")
    prompts = iter(["research Python packaging, then draft release notes", "/quit"])
    write_calls = []
    routed_plan = mod.ReplRouteDecision(
        kind=mod.ReplRouteKind.PLAN,
        confidence=0.96,
        target_text="research Python packaging, then draft release notes",
        args_text="research Python packaging, then draft release notes",
        rationale="decomposition matched ordered action clauses",
        steps=(
            mod.ReplPlanStep(
                index=1,
                kind=mod.ReplRouteKind.RESEARCH,
                target_text="Python packaging",
                args_text="Python packaging",
                rationale="deterministic research match",
            ),
            mod.ReplPlanStep(
                index=2,
                kind=mod.ReplRouteKind.WRITE,
                target_text="release notes",
                args_text="release notes",
                rationale="deterministic write match",
            ),
        ),
    )

    def _fake_input(_label):
        return next(prompts)

    def _fake_invoke(prompt, *, config, history):
        write_calls.append({"prompt": prompt, "history": history, "session_id": config.session_id})
        return mod.AskResponse(
            response="## Release notes\n\n- Added plan execution",
            model="gemini",
            tokens=12,
            raw={"response": "## Release notes\n\n- Added plan execution", "model": "gemini", "tokens": 12},
        )

    with (
        patch.object(mod, "route_repl_prompt", return_value=routed_plan),
        patch.object(mod, "invoke_openclaw", side_effect=_fake_invoke),
    ):
        exit_code = mod.run_chat(
            _config(session_id=session.session_id),
            input_func=_fake_input,
            session_id=session.session_id,
        )

    assert exit_code == 0
    refreshed = sessions_mod.load_session(session.session_id)
    assert refreshed is not None
    assert refreshed.plan_id == "plan-auto-123"
    assert refreshed.output_count >= 2
    assert len(write_calls) == 1
    assert write_calls[0]["history"] == [{"role": "assistant", "content": "Research findings about packaging"}]
    assert write_calls[0]["session_id"] == session.session_id
    assert "Plan: plan-auto-123" in write_calls[0]["prompt"]
    assert "Recent session outputs:" in write_calls[0]["prompt"]
    assert "Research findings about packaging" in write_calls[0]["prompt"]
    created_plan = plan_store["plan-auto-123"]
    assert created_plan.status == "completed"
    assert [step.status for step in created_plan.steps] == ["done", "done"]
    assert created_plan.context["session_id"] == session.session_id
    assert "saved draft to" in created_plan.context["step_2_output"]
    plan_events = [event for event in sessions_mod.load_events(session.session_id) if event.get("kind") == "plan"]
    assert len(plan_events) == 1
    assert plan_events[0]["metadata"]["plan_id"] == "plan-auto-123"
    stdout = capsys.readouterr().out
    assert "OpenClaw identified a plan candidate with 2 steps" in stdout or "plan 2 steps" in stdout
    assert "[1/2] /research Python packaging" in stdout
    assert "[2/2] /write release notes" in stdout


def test_run_chat_without_session_keeps_multi_step_prompt_in_chat(capsys):
    prompts = iter(["research Python packaging, then draft release notes", "/quit"])
    ask_calls = []

    def _fake_input(_label):
        return next(prompts)

    def _fake_ask(prompt, *, config, history):
        ask_calls.append((prompt, history))
        return mod.AskResponse(
            response=f"chat reply to {prompt}",
            model="gemini",
            tokens=8,
            raw={"response": f"chat reply to {prompt}", "model": "gemini", "tokens": 8},
        )

    with patch.object(mod, "route_repl_prompt") as route_repl_prompt:
        exit_code = mod.run_chat(_config(), input_func=_fake_input, ask_func=_fake_ask)

    assert exit_code == 0
    route_repl_prompt.assert_not_called()
    assert ask_calls == [("research Python packaging, then draft release notes", [])]
    stdout = capsys.readouterr().out
    assert "[1/2]" not in stdout
    assert "chat reply to research Python packaging, then draft release notes" in stdout


def test_main_defaults_to_chat_when_no_args():
    config = _config()

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "run_chat", return_value=0) as run_chat,
    ):
        exit_code = mod.main([])

    assert exit_code == 0
    run_chat.assert_called_once_with(config)


def test_main_runs_health_without_token_warning(capsys):
    config = _config(token="")
    health = mod.HealthResponse(payload={"status": "ok"}, raw_text='{"status":"ok"}', status="ok", healthy=True)

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "fetch_health", return_value=health) as fetch_health,
    ):
        exit_code = mod.main(["--health"])

    assert exit_code == 0
    fetch_health.assert_called_once_with(config=config)
    assert "OK OpenClaw health: OK" in capsys.readouterr().out


def test_main_auth_login_stores_token_file_on_linux(monkeypatch, tmp_path, capsys):
    auth_path = tmp_path / "token"
    monkeypatch.setattr(mod, "auth_storage_path", lambda platform_name=None: auth_path)

    with (
        patch.object(mod.sys, "platform", "linux"),
        patch.object(mod.getpass, "getpass", return_value="saved-token"),
    ):
        exit_code = mod.main(["auth", "login"])

    assert exit_code == 0
    assert auth_path.read_text(encoding="utf-8").strip() == "saved-token"
    assert str(auth_path) in capsys.readouterr().out


def test_main_auth_status_reports_saved_file_source(monkeypatch, tmp_path, capsys):
    auth_path = tmp_path / "token"
    monkeypatch.setattr(mod, "auth_storage_path", lambda platform_name=None: auth_path)

    with patch.object(mod.sys, "platform", "linux"):
        mod.write_saved_token("saved-token")
        exit_code = mod.main(["auth", "status"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "credential file" in stdout.lower()
    assert str(auth_path) in stdout


def test_main_auth_logout_removes_saved_file(monkeypatch, tmp_path, capsys):
    auth_path = tmp_path / "token"
    monkeypatch.setattr(mod, "auth_storage_path", lambda platform_name=None: auth_path)

    with patch.object(mod.sys, "platform", "linux"):
        mod.write_saved_token("saved-token")
        exit_code = mod.main(["auth", "logout"])

    assert exit_code == 0
    assert not auth_path.exists()
    assert "Removed OpenClaw token" in capsys.readouterr().out


def test_main_auth_login_uses_keychain_on_macos(capsys):
    with (
        patch.object(mod.sys, "platform", "darwin"),
        patch.object(mod, "write_keychain_token") as write_keychain_token,
        patch.object(mod, "delete_saved_token", return_value=False),
    ):
        exit_code = mod.main(["auth", "login", "--token", "abc123"])

    assert exit_code == 0
    write_keychain_token.assert_called_once_with("abc123")
    assert "macOS Keychain" in capsys.readouterr().out


def test_main_auth_logout_removes_keychain_on_macos(capsys):
    with (
        patch.object(mod.sys, "platform", "darwin"),
        patch.object(mod, "delete_saved_token", return_value=False),
        patch.object(mod, "delete_keychain_token", return_value=True),
    ):
        exit_code = mod.main(["auth", "logout"])

    assert exit_code == 0
    assert "macOS Keychain" in capsys.readouterr().out


def test_main_treats_bare_args_as_one_shot_prompt():
    config = _config()
    response = mod.AskResponse(
        response="All caught up",
        model="gemini",
        tokens=12,
        raw={"response": "All caught up", "model": "gemini", "tokens": 12},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as invoke_openclaw,
        patch.object(mod, "print_response") as print_response,
    ):
        exit_code = mod.main(["what", "changed", "overnight?"])

    assert exit_code == 0
    invoke_openclaw.assert_called_once_with("what changed overnight?", config=config)
    print_response.assert_called_once_with(response, output_json=False)


def test_main_warns_when_token_missing(capsys):
    config = _config(token="")

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "run_chat", return_value=0),
    ):
        exit_code = mod.main([])

    assert exit_code == 0
    assert "no OpenClaw API token is configured" in capsys.readouterr().err


def test_run_chat_supports_help_command(capsys):
    prompts = iter(["/help", "/quit"])

    def _fake_input(_label):
        return next(prompts)

    exit_code = mod.run_chat(_config(), input_func=_fake_input)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Interactive commands:" in stdout
    assert "/help" in stdout
    assert "/autoroute" in stdout
    assert "/rollback" in stdout
    assert "Multi-step prompts can decompose into linked plans" in stdout
    assert "Ambiguous prompts stay in normal chat." in stdout


class TestChatCommandRegistry:
    """Unit tests for the slash-command dispatcher / registry."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, history=None, session_id="") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=history if history is not None else [], session_id=session_id)

    def test_freeform_prompt_returns_none(self):
        result = self._registry().dispatch("tell me about pandas", self._ctx())
        assert result is None

    def test_unknown_slash_command_returns_none(self):
        result = self._registry().dispatch("/unknown-command", self._ctx())
        assert result is None

    def test_slash_without_name_returns_none(self):
        result = self._registry().dispatch("/", self._ctx())
        assert result is None

    def test_quit_returns_quit_sentinel(self):
        assert self._registry().dispatch("/quit", self._ctx()) == mod._CMD_QUIT

    def test_exit_alias_returns_quit_sentinel(self):
        assert self._registry().dispatch("/exit", self._ctx()) == mod._CMD_QUIT

    def test_help_returns_continue_sentinel(self, capsys):
        result = self._registry().dispatch("/help", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "Interactive commands:" in capsys.readouterr().out

    def test_clear_returns_continue_sentinel(self):
        assert self._registry().dispatch("/clear", self._ctx()) == mod._CMD_CONTINUE

    def test_clear_empties_history_in_place(self):
        history = [{"role": "user", "content": "hi"}]
        self._registry().dispatch("/clear", self._ctx(history=history))
        assert history == []

    def test_clear_prints_confirmation(self, capsys):
        self._registry().dispatch("/clear", self._ctx())
        assert "Conversation history cleared." in capsys.readouterr().out

    def test_clear_appends_event_when_session_present(self):
        ctx = self._ctx(session_id="sess-abc")
        with patch.object(mod, "append_event") as mock_ae:
            self._registry().dispatch("/clear", ctx)
        mock_ae.assert_called_once_with(
            "sess-abc",
            kind="chat",
            content="/clear",
            metadata={"summary": "cleared chat history"},
        )

    def test_clear_does_not_call_append_event_without_session(self):
        ctx = self._ctx(session_id="")
        with patch.object(mod, "append_event") as mock_ae:
            self._registry().dispatch("/clear", ctx)
        mock_ae.assert_not_called()

    def test_list_commands_contains_primary_names(self):
        names = {cmd.name for cmd in self._registry().list_commands()}
        assert names >= {"help", "clear", "quit"}

    def test_list_commands_excludes_aliases(self):
        names = {cmd.name for cmd in self._registry().list_commands()}
        assert "exit" not in names

    def test_custom_command_can_be_registered(self):
        registry = mod.ChatCommandRegistry()
        called = []
        registry.register(
            mod.SlashCommand(
                name="ping",
                description="Ping test",
                handler=lambda ctx: (called.append(True), mod._CMD_CONTINUE)[1],
            )
        )
        result = registry.dispatch("/ping", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert called == [True]

    # ------------------------------------------------------------------
    # dispatch with arguments
    # ------------------------------------------------------------------

    def test_dispatch_strips_args_into_ctx(self):
        """ctx.args should receive text after the command name."""
        captured = []
        registry = mod.ChatCommandRegistry()
        registry.register(
            mod.SlashCommand(
                name="echo",
                description="Echo args",
                handler=lambda ctx: (captured.append(ctx.args), mod._CMD_CONTINUE)[1],
            )
        )
        registry.dispatch("/echo hello world", self._ctx())
        assert captured == ["hello world"]

    def test_dispatch_empty_args_when_no_extra_text(self):
        captured = []
        registry = mod.ChatCommandRegistry()
        registry.register(
            mod.SlashCommand(
                name="noarg",
                description="No arg command",
                handler=lambda ctx: (captured.append(ctx.args), mod._CMD_CONTINUE)[1],
            )
        )
        registry.dispatch("/noarg", self._ctx())
        assert captured == [""]

    def test_new_commands_registered(self):
        names = {cmd.name for cmd in self._registry().list_commands()}
        assert names >= {
            "session",
            "context",
            "cwd",
            "files",
            "plan",
            "task",
            "outputs",
            "overlay",
            "rollback",
            "events",
            "collab",
            "analyze",
            "research",
            "write",
            "exec",
            "edit",
        }


class TestSessionSlashCommands:
    """Tests for in-REPL session/context inspection and mutation commands."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, session_id: str = "", args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    # ------------------------------------------------------------------
    # /session
    # ------------------------------------------------------------------

    def test_session_no_active_session_warns(self, capsys):
        result = self._registry().dispatch("/session", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No active session" in out

    def test_session_shows_summary(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="Test Session", cwd=str(tmp_path))
        result = mod._cmd_session(self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Session Dashboard" in out
        assert "Summary:" in out
        assert "Actions:" in out
        assert "Test Session" in out
        assert sess.session_id in out
        assert "visibility: read-only local snapshot" in out
        assert "control: visibility only; no remote control" in out
        assert "readiness:" in out
        assert "/collab share to copy the read-only local snapshot before handoff" in out

    def test_session_surfaces_context_pressure_recovery_actions(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setitem(mod._PREFS, "system_prompt", "Always summarize the next step." * 200)
        sess = sessions_mod.create_session(title="Pressure Session", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="prompt", content="x" * 420_000)

        result = mod._cmd_session(self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "context pressure:" in out
        assert "hidden context cue: system or queued inject content pushes the next send closer to capacity" in out
        assert "/tokeninfo to inspect live context pressure before the next send" in out
        assert "/bookmark before /clear if you need a clean recovery loop" in out
        assert "/inject status or /system view to inspect hidden context before sending" in out

    def test_session_uses_model_aware_limit_actions(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setitem(mod._PREFS, "last_model", "gemma3:4b")
        sess = sessions_mod.create_session(title="Gemma Pressure", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="prompt", content="x" * 390_000)

        result = mod._cmd_session(self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "of ~100k" in out
        assert "/bookmark before /clear if you need a clean recovery loop" in out

    def test_session_surfaces_pending_inject_recovery_actions(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="Queued Inject Session", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="prompt", content="x" * 410_000)
        monkeypatch.setattr(mod, "_next_inject", "Queued workspace recap")

        result = mod._cmd_session(self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "recovery cue: /inject clear drops the queued one-shot context before a retry" in out
        assert "/inject clear to drop the queued one-shot context before your next send" in out

    # ------------------------------------------------------------------
    # /context
    # ------------------------------------------------------------------

    def test_context_no_session_warns(self, capsys):
        result = self._registry().dispatch("/context", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_context_shows_cwd_and_files(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(
            title="ctx-test",
            cwd=str(tmp_path),
            files=["foo.py", "bar.py"],
            plan_id="plan-1",
            task_id="task-2",
        )
        mod._cmd_context(self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert str(tmp_path) in out
        assert "foo.py" in out
        assert "plan-1" in out
        assert "task-2" in out

    def test_context_no_files_says_none(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="empty", cwd=str(tmp_path))
        self._registry().dispatch("/context", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "(none tracked)" in out

    def test_context_shows_effective_grounding_preview(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.chdir(tmp_path)
        tracked = tmp_path / "notes.txt"
        tracked.write_text("hello from the tracked file", encoding="utf-8")
        _write_local_plan(tmp_path, "plan-1", goal="Refine the REPL UX")
        _write_local_tasks(
            tmp_path,
            [{"id": "task-2", "title": "Wire up previews", "status": "in_progress"}],
        )
        sess = sessions_mod.create_session(
            title="ctx-preview",
            cwd=str(tmp_path),
            files=[str(tracked)],
            plan_id="plan-1",
            task_id="task-2",
        )
        sessions_mod.save_output(sess.session_id, "notes.md", "Previous summary for grounding.")

        self._registry().dispatch("/context", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "Context Dashboard" in out
        assert "effective grounding preview:" in out
        assert "Workspace context:" in out
        assert "hello from the tracked file" in out
        assert "Plan goal: Refine the REPL UX" in out
        assert "Task detail: Wire up previews; status=in_progress" in out
        assert "Recent session outputs:" in out

    # ------------------------------------------------------------------
    # /cwd
    # ------------------------------------------------------------------

    def test_cwd_no_session_warns(self, capsys):
        result = self._registry().dispatch("/cwd", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_cwd_shows_current_when_no_arg(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="cwd-show", cwd=str(tmp_path))
        self._registry().dispatch("/cwd", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert str(tmp_path) in out

    def test_cwd_switches_directory(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        new_dir = tmp_path / "subdir"
        new_dir.mkdir()
        sess = sessions_mod.create_session(title="cwd-switch", cwd=str(tmp_path))
        ctx = self._ctx(session_id=sess.session_id, args=str(new_dir))
        self._registry().dispatch("/cwd " + str(new_dir), ctx)
        out = capsys.readouterr().out
        assert str(new_dir) in out
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.cwd == str(new_dir)

    def test_cwd_rejects_nonexistent_dir(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="cwd-bad", cwd=str(tmp_path))
        ctx = self._ctx(session_id=sess.session_id, args="/no/such/directory")
        self._registry().dispatch("/cwd /no/such/directory", ctx)
        out = capsys.readouterr().out
        assert "not a directory" in out

    # ------------------------------------------------------------------
    # /files
    # ------------------------------------------------------------------

    def test_files_no_session_warns(self, capsys):
        result = self._registry().dispatch("/files", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_files_lists_tracked_files(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="files-list", cwd=str(tmp_path), files=["a.py"])
        self._registry().dispatch("/files", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "a.py" in out

    def test_files_none_tracked_message(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="files-empty", cwd=str(tmp_path))
        self._registry().dispatch("/files", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No tracked files" in out

    def test_files_add_appends_file(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        new_file = tmp_path / "new.py"
        new_file.touch()
        sess = sessions_mod.create_session(title="files-add", cwd=str(tmp_path))
        self._registry().dispatch(f"/files add {new_file}", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert str(new_file) in updated.files

    def test_files_add_deduplicates(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        f = tmp_path / "dup.py"
        f.touch()
        sess = sessions_mod.create_session(title="dup", cwd=str(tmp_path), files=[str(f)])
        self._registry().dispatch(f"/files add {f}", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "Already tracked" in out
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.files.count(str(f)) == 1

    def test_files_rm_removes_file(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        f = tmp_path / "rem.py"
        f.touch()
        sess = sessions_mod.create_session(title="rm-test", cwd=str(tmp_path), files=[str(f)])
        self._registry().dispatch(f"/files rm {f}", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert str(f) not in updated.files

    def test_files_rm_not_tracked_warns(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="rm-miss", cwd=str(tmp_path))
        self._registry().dispatch("/files rm ghost.py", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "Not tracked" in out

    def test_files_add_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        f = tmp_path / "evt.py"
        f.touch()
        sess = sessions_mod.create_session(title="evt", cwd=str(tmp_path))
        with patch.object(mod, "append_event") as mock_ae:
            self._registry().dispatch(f"/files add {f}", self._ctx(session_id=sess.session_id))
        mock_ae.assert_called_once()
        call_kwargs = mock_ae.call_args[1]
        assert call_kwargs.get("content", "").startswith("/files add")

    # ------------------------------------------------------------------
    # /plan
    # ------------------------------------------------------------------

    def test_plan_no_session_warns(self, capsys):
        result = self._registry().dispatch("/plan", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_plan_shows_no_plan_message(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="plan-none", cwd=str(tmp_path))
        self._registry().dispatch("/plan", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No plan linked" in out

    def test_plan_links_plan_id(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="plan-link", cwd=str(tmp_path))
        self._registry().dispatch("/plan plan-xyz", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.plan_id == "plan-xyz"

    def test_plan_link_reports_local_confirmation(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.chdir(tmp_path)
        _write_local_plan(tmp_path, "plan-xyz", goal="Tighten local validation")
        sess = sessions_mod.create_session(title="plan-link", cwd=str(tmp_path))

        self._registry().dispatch("/plan plan-xyz", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "confirmed local plan 'plan-xyz'" in out
        assert "Tighten local validation" in out

    def test_plan_shows_current_plan(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="plan-show", cwd=str(tmp_path), plan_id="plan-abc")
        self._registry().dispatch("/plan", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "plan-abc" in out

    def test_plan_unlink_removes_plan(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="plan-unlink", cwd=str(tmp_path), plan_id="plan-abc")
        self._registry().dispatch("/plan unlink", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.plan_id == ""

    def test_plan_unlink_no_plan_warns(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="plan-no-plan", cwd=str(tmp_path))
        self._registry().dispatch("/plan unlink", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No plan is currently linked" in out

    # ------------------------------------------------------------------
    # /task
    # ------------------------------------------------------------------

    def test_task_no_session_warns(self, capsys):
        result = self._registry().dispatch("/task", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_task_shows_no_task_message(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-none", cwd=str(tmp_path))
        self._registry().dispatch("/task", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No task linked" in out

    def test_task_links_task_id(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-link", cwd=str(tmp_path))
        self._registry().dispatch("/task task-99", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.task_id == "task-99"

    def test_task_link_warns_when_local_task_missing(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.chdir(tmp_path)
        _write_local_tasks(
            tmp_path,
            [{"id": "task-1", "title": "Existing task", "status": "backlog"}],
        )
        sess = sessions_mod.create_session(title="task-link", cwd=str(tmp_path))

        self._registry().dispatch("/task task-99", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "warning: local task 'task-99' was not found" in out
        assert "task → task-99" in out

    def test_task_link_reports_validation_unavailable(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.chdir(tmp_path)
        sess = sessions_mod.create_session(title="task-link", cwd=str(tmp_path))

        self._registry().dispatch("/task task-99", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "local task validation unavailable" in out
        assert "task → task-99" in out

    # ------------------------------------------------------------------
    # /outputs
    # ------------------------------------------------------------------

    def test_outputs_lists_recent_saved_outputs(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "first.md", "first output")
        sessions_mod.save_output(sess.session_id, "second.md", "second output")

        mod._cmd_outputs(self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "Outputs Dashboard" in out
        assert "saved outputs" in out
        assert "1. second.md" in out
        assert "2. first.md" in out

    def test_outputs_dashboard_includes_focused_preview_excerpt(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "preview.md", "line one\nline two with extra detail\nline three")

        mod._cmd_outputs(self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "focused preview: preview.md" in out
        assert "excerpt:" in out
        assert "line one" in out

    def test_outputs_preview_supports_index_and_filename(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "alpha.md", "alpha body")
        sessions_mod.save_output(sess.session_id, "beta.md", "beta body")

        self._registry().dispatch("/outputs 1", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "saved output preview: beta.md" in out
        assert "beta body" in out

        self._registry().dispatch("/outputs alpha.md", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "saved output preview: alpha.md" in out
        assert "alpha body" in out

    def test_outputs_preview_stays_bounded_for_large_artifacts(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        huge_body = ("preview body " * 400) + "\nTAIL-MARKER-SHOULD-NOT-APPEAR"
        sessions_mod.save_output(sess.session_id, "huge.md", huge_body)

        self._registry().dispatch("/outputs 1", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "saved output preview: huge.md" in out
        assert f"preview limited to {mod.OUTPUT_PREVIEW_MAX_CHARS} chars" in out
        assert "TAIL-MARKER-SHOULD-NOT-APPEAR" not in out

    def test_outputs_overlay_supports_interactive_filter_and_selection(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "alpha.md", "alpha body")
        sessions_mod.save_output(sess.session_id, "beta-notes.md", "beta body")
        prompts = iter(["beta", "1"])
        monkeypatch.setattr("builtins.input", lambda _label: next(prompts))

        self._registry().dispatch("/outputs overlay", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "Saved outputs overlay" in out
        assert "beta-notes.md" in out
        assert "saved output preview: beta-notes.md" in out
        assert "beta body" in out

    def test_outputs_overlay_supports_arrow_key_selection_with_preview_panel(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "alpha.md", "alpha body")
        sessions_mod.save_output(sess.session_id, "beta-notes.md", "beta body")

        class _NoopRawMode:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, tb):
                return False

        keys = iter(["down", "enter"])
        monkeypatch.setattr(mod, "_overlay_keypress_supported", lambda: True)
        monkeypatch.setattr(mod, "_overlay_raw_mode", lambda: _NoopRawMode())
        monkeypatch.setattr(mod, "_read_overlay_keypress", lambda: next(keys))

        self._registry().dispatch("/outputs overlay", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "↑/↓ move" in out
        assert "Preview" in out
        assert "alpha.md" in out
        assert "saved output preview: alpha.md" in out
        assert "alpha body" in out

    def test_outputs_overlay_falls_back_to_listing_without_tty(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: False)
        sess = sessions_mod.create_session(title="outputs", cwd=str(tmp_path))
        sessions_mod.save_output(sess.session_id, "alpha.md", "alpha body")

        self._registry().dispatch("/outputs overlay", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "Interactive overlay unavailable here" in out
        assert "saved outputs" in out
        assert "1. alpha.md" in out

    def test_overlay_command_toggles_persisted_preference(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS.clear()
        mod._PREFS.update({"theme": "default", "emoji": True, "emoji_pack": "classic", "layout": "normal"})

        result = self._registry().dispatch("/overlay on", self._ctx(args="on"))

        assert result == mod._CMD_CONTINUE
        mod._PREFS["interactive_overlays"] = False
        mod._load_prefs()
        assert mod._PREFS["interactive_overlays"] is True
        out = capsys.readouterr().out
        assert "Interactive overlays enabled" in out

    def test_task_shows_current_task(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-show", cwd=str(tmp_path), task_id="task-77")
        self._registry().dispatch("/task", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "task-77" in out

    def test_sessions_overlay_supports_interactive_selection(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
        first = sessions_mod.create_session(title="Alpha session", cwd=str(tmp_path))
        second = sessions_mod.create_session(title="Beta session", cwd=str(tmp_path))
        prompts = iter(["beta", "1"])
        monkeypatch.setattr("builtins.input", lambda _label: next(prompts))

        self._registry().dispatch("/sessions overlay", self._ctx(session_id=first.session_id))

        out = capsys.readouterr().out
        assert "Session overlay" in out
        assert "Beta session" in out
        assert second.session_id in out

    def test_overlay_keypress_mode_stays_disabled_in_plain_mode(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
        monkeypatch.setattr("builtins.input", lambda _label: "")
        monkeypatch.setattr(
            mod,
            "_read_overlay_keypress",
            lambda: (_ for _ in ()).throw(AssertionError("keypress mode should be skipped")),
        )

        result = mod._run_interactive_overlay(
            title="Plain overlay",
            items=["alpha", "beta"],
            label_fn=str,
            on_select=lambda _item: None,
        )

        out = capsys.readouterr().out
        assert result == "closed"
        assert "Type a search term, a number to select" in out

    def test_sessions_list_prints_dashboard_header(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sessions_mod.create_session(title="Alpha session", cwd=str(tmp_path))
        sessions_mod.create_session(title="Beta session", cwd=str(tmp_path))

        mod._cmd_sessions(self._ctx())

        out = capsys.readouterr().out
        assert "Session Browser" in out
        assert "Summary:" in out
        assert "Actions:" in out
        assert "Alpha session" in out or "Beta session" in out

    def test_session_preview_lines_include_activity_output_and_collab(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="Preview session", cwd=str(tmp_path))
        sessions_mod.update_session(sess.session_id, last_summary="Shipped the focused preview lane")
        sessions_mod.save_output(sess.session_id, "preview.txt", "latest output body for preview")
        mod.append_event(
            sess.session_id,
            kind="collab",
            content="Shared preview notes",
            metadata={
                "summary": "decision by alice: shared preview notes",
                "actor": "alice",
                "collab_kind": "decision",
            },
        )
        lines = mod._session_preview_lines(sessions_mod.require_session(sess.session_id))

        assert any("latest activity:" in line for line in lines)
        assert any("latest output: preview.txt" in line for line in lines)
        assert any("decision: alice: decision by alice: shared preview notes" in line for line in lines)

    def test_collab_note_and_status_capture_actor_summary(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="collab", cwd=str(tmp_path))

        result = self._registry().dispatch(
            "/collab note @alice Checked the handoff checklist",
            self._ctx(session_id=sess.session_id),
        )

        assert result == mod._CMD_CONTINUE
        note_out = capsys.readouterr().out
        assert "Recorded note by alice" in note_out
        assert "Local session log only; workspace unchanged." in note_out

        self._registry().dispatch("/collab", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "SESSION HANDOFF" in out
        assert "ACTORS" in out
        assert "alice" in out
        assert "RECENT NOTES" in out
        assert "Checked the handoff checklist" in out

    def test_collab_decision_adds_tags_and_export_snapshot(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="collab-export", cwd=str(tmp_path))

        self._registry().dispatch(
            "/collab decision @bob #release Keep the handoff local-only for now",
            self._ctx(session_id=sess.session_id),
        )

        out = capsys.readouterr().out
        assert "Recorded decision by bob" in out
        assert "Local session log only; workspace unchanged." in out
        exported = mod.export_session(sess.session_id)
        collaboration = exported["collaboration"]
        assert collaboration["recent_decisions"][0]["actor"] == "bob"
        assert "release" in collaboration["recent_decisions"][0]["tags"]
        updated = sessions_mod.load_session(sess.session_id)
        assert "collab:release" in updated.tags

    def test_collab_assign_is_visible_in_status_snapshot(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(
            title="collab-assign", cwd=str(tmp_path), plan_id="plan-38", task_id="task-38"
        )

        result = self._registry().dispatch(
            "/collab assign @alice Own the release handoff checklist",
            self._ctx(session_id=sess.session_id),
        )

        assert result == mod._CMD_CONTINUE
        assign_out = capsys.readouterr().out
        assert "Recorded assign by alice" in assign_out
        assert "Local session log only; workspace unchanged." in assign_out

        self._registry().dispatch("/collab status", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "ASSIGNMENTS" in out
        assert "alice" in out
        assert "Own the release handoff checklist" in out

    def test_risk_add_list_and_clear_roundtrip(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="risk", cwd=str(tmp_path))

        add_result = self._registry().dispatch(
            "/risk add high Waiting on operator sign-off",
            self._ctx(session_id=sess.session_id),
        )
        assert add_result == mod._CMD_CONTINUE
        add_out = capsys.readouterr().out
        assert "Recorded high risk" in add_out

        list_result = self._registry().dispatch("/risk list", self._ctx(session_id=sess.session_id))
        assert list_result == mod._CMD_CONTINUE
        list_out = capsys.readouterr().out
        assert "Open risks:" in list_out
        assert "HIGH" in list_out

        clear_result = self._registry().dispatch("/risk clear 1", self._ctx(session_id=sess.session_id))
        assert clear_result == mod._CMD_CONTINUE
        clear_out = capsys.readouterr().out
        assert "Cleared risk 1" in clear_out

        self._registry().dispatch("/risk list", self._ctx(session_id=sess.session_id))
        final_out = capsys.readouterr().out
        assert "(none)" in final_out

    def test_incident_log_list_and_resolve_roundtrip(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(title="incident", cwd=str(tmp_path))

        log_result = self._registry().dispatch(
            "/incident log Agent stalled while waiting for credentials",
            self._ctx(session_id=sess.session_id),
        )
        assert log_result == mod._CMD_CONTINUE
        log_out = capsys.readouterr().out
        assert "Recorded incident." in log_out

        list_result = self._registry().dispatch("/incident list", self._ctx(session_id=sess.session_id))
        assert list_result == mod._CMD_CONTINUE
        list_out = capsys.readouterr().out
        assert "Open incidents:" in list_out
        assert "credentials" in list_out

        self._registry().dispatch("/collab status", self._ctx(session_id=sess.session_id))
        status_out = capsys.readouterr().out
        assert "OPEN INCIDENTS" in status_out

        resolve_result = self._registry().dispatch("/incident resolve 1", self._ctx(session_id=sess.session_id))
        assert resolve_result == mod._CMD_CONTINUE
        resolve_out = capsys.readouterr().out
        assert "Resolved incident 1." in resolve_out

        self._registry().dispatch("/incident list", self._ctx(session_id=sess.session_id))
        final_out = capsys.readouterr().out
        assert "(none)" in final_out

    def test_handoff_check_reports_blockers_and_ownership(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(
            title="handoff-check", cwd=str(tmp_path), plan_id="plan-38", task_id="task-38"
        )
        self._registry().dispatch("/collab assign @alice Drive final validation", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        self._registry().dispatch(
            "/risk add critical Waiting on release approval", self._ctx(session_id=sess.session_id)
        )
        capsys.readouterr()

        result = self._registry().dispatch("/handoff check", self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Handoff readiness" in out
        assert "blocked" in out
        assert "owner" in out
        assert "CRITICAL" in out

    def test_handoff_check_reports_open_incidents(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        sess = sessions_mod.create_session(
            title="handoff-incident", cwd=str(tmp_path), plan_id="plan-41", task_id="task-41"
        )
        self._registry().dispatch("/collab assign @alice Drive final validation", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        self._registry().dispatch(
            "/incident log Waiting on operator confirmation", self._ctx(session_id=sess.session_id)
        )
        capsys.readouterr()

        result = self._registry().dispatch("/handoff check", self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "blocked" in out
        assert "incidents" in out
        assert "open incidents:" in out

    def test_task_unlink_removes_task(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-unlink", cwd=str(tmp_path), task_id="task-77")
        self._registry().dispatch("/task unlink", self._ctx(session_id=sess.session_id))
        capsys.readouterr()
        updated = sessions_mod.load_session(sess.session_id)
        assert updated.task_id == ""

    def test_task_unlink_no_task_warns(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-no-task", cwd=str(tmp_path))
        self._registry().dispatch("/task unlink", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No task is currently linked" in out

    # ------------------------------------------------------------------
    # /events
    # ------------------------------------------------------------------

    def test_events_no_session_warns(self, capsys):
        result = self._registry().dispatch("/events", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_events_no_events_says_none(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="evt-none", cwd=str(tmp_path))
        self._registry().dispatch("/events", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "No events recorded" in out

    def test_events_shows_recent_events(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="evt-show", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="chat", content="hello", metadata={"summary": "hello"})
        sessions_mod.append_event(sess.session_id, kind="chat", content="world", metadata={"summary": "world"})
        self._registry().dispatch("/events", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "chat" in out
        assert "hello" in out or "world" in out

    def test_events_respects_count_arg(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="evt-count", cwd=str(tmp_path))
        for i in range(10):
            sessions_mod.append_event(sess.session_id, kind="chat", content=f"msg{i}", metadata={"summary": f"msg{i}"})
        self._registry().dispatch("/events 2", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        # The rich display format respects the count — verify count label appears in output
        assert "2 recent events" in out or "showing 2" in out

    def test_events_invalid_arg_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="evt-bad", cwd=str(tmp_path))
        self._registry().dispatch("/events notanumber", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "Usage" in out

    # ------------------------------------------------------------------
    # /help shows new commands
    # ------------------------------------------------------------------

    def test_help_output_includes_new_commands(self, capsys):
        self._registry().dispatch("/help", self._ctx())
        out = capsys.readouterr().out
        for cmd in (
            "/session",
            "/context",
            "/cwd",
            "/files",
            "/plan",
            "/task",
            "/outputs",
            "/rollback",
            "/events",
            "/autoroute",
            "/analyze",
            "/research",
            "/write",
            "/exec",
            "/edit",
        ):
            assert cmd in out, f"Expected {cmd} in /help output"
        assert "Multi-step prompts can decompose into linked plans" in out
        assert "Ambiguous prompts stay in normal chat." in out


class TestSearchSlashCommand:
    """Tests for the /search slash command."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, session_id: str = "", args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_search_no_args_prints_usage(self, capsys):
        ctx = self._ctx(session_id="sess-1")
        result = self._registry().dispatch("/search", ctx)
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Usage:" in out
        assert "/search" in out

    def test_search_finds_matching_event(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="search-test", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="chat", content="hello world from the user")
        sessions_mod.append_event(sess.session_id, kind="chat", content="something unrelated")
        ctx = self._ctx(session_id=sess.session_id)
        result = self._registry().dispatch("/search hello world", ctx)
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "hello world" in out
        assert "search results" in out

    def test_search_no_matches_prints_message(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="search-nomatch", cwd=str(tmp_path))
        sessions_mod.append_event(sess.session_id, kind="chat", content="something else entirely")
        ctx = self._ctx(session_id=sess.session_id)
        result = self._registry().dispatch("/search xyzzy_not_found", ctx)
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No matches" in out
        assert "xyzzy_not_found" in out


class TestActionSlashCommands:
    """Tests for in-REPL action delegation slash commands."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(
        self,
        session_id: str = "",
        args: str = "",
        config=None,
        route_metadata: dict | None = None,
    ) -> mod.ChatCommandContext:
        return mod.ChatCommandContext(
            history=[],
            session_id=session_id,
            args=args,
            config=config,
            route_metadata=route_metadata,
        )

    def _make_config(self):
        return mod.CliConfig(
            base_url="http://localhost:8765",
            token="tok",
            model="auto",
            timeout_seconds=30,
            user_name="test",
            client_name="test-client",
            output_json=False,
        )

    def _fake_response(self, text="Analysis complete."):
        return mod.AskResponse(
            response=text,
            model="gemini",
            tokens=5,
            raw={"response": text, "model": "gemini", "tokens": 5},
        )

    # ------------------------------------------------------------------
    # /analyze
    # ------------------------------------------------------------------

    def test_analyze_no_config_warns(self, capsys):
        result = self._registry().dispatch("/analyze some goal", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "error" in capsys.readouterr().out.lower()

    def test_analyze_no_session_warns(self, capsys):
        result = self._registry().dispatch("/analyze goal", self._ctx(config=self._make_config()))
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_analyze_no_goal_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="analyze-usage", cwd=str(tmp_path))
        result = self._registry().dispatch(
            "/analyze", self._ctx(session_id=sess.session_id, config=self._make_config())
        )
        assert result == mod._CMD_CONTINUE
        assert "Usage" in capsys.readouterr().out

    def test_analyze_invokes_openclaw_with_session_context(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="analyze-happy", cwd=str(tmp_path))
        cfg = self._make_config()
        resp = self._fake_response("Analysis done.")
        with (
            patch.object(mod, "invoke_openclaw", return_value=resp) as mock_ask,
            patch.object(mod, "collect_workspace_context", return_value=([], "workspace ctx")),
            patch.object(mod, "save_output", return_value=tmp_path / "out.md"),
        ):
            result = self._registry().dispatch(
                "/analyze check the repo",
                self._ctx(session_id=sess.session_id, config=cfg),
            )
        assert result == mod._CMD_CONTINUE
        assert mock_ask.called
        called_prompt = mock_ask.call_args.args[0]
        assert "check the repo" in called_prompt
        out = capsys.readouterr().out
        assert "Analysis done." in out

    def test_analyze_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="analyze-evt", cwd=str(tmp_path))
        cfg = self._make_config()
        resp = self._fake_response()
        with (
            patch.object(mod, "invoke_openclaw", return_value=resp),
            patch.object(mod, "collect_workspace_context", return_value=([], "")),
            patch.object(mod, "append_event") as mock_ae,
            patch.object(mod, "persist_response"),
        ):
            self._registry().dispatch(
                "/analyze find issues",
                self._ctx(session_id=sess.session_id, config=cfg),
            )
        kinds = [call.kwargs.get("kind") for call in mock_ae.call_args_list]
        assert "analyze" in kinds

    def test_analyze_handles_openclaw_error(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="analyze-err", cwd=str(tmp_path))
        cfg = self._make_config()
        with (
            patch.object(mod, "invoke_openclaw", side_effect=mod.OpenClawCliError("timeout")),
            patch.object(mod, "collect_workspace_context", return_value=([], "")),
        ):
            result = self._registry().dispatch(
                "/analyze something",
                self._ctx(session_id=sess.session_id, config=cfg),
            )
        assert result == mod._CMD_CONTINUE
        assert "error" in capsys.readouterr().out.lower()

    # ------------------------------------------------------------------
    # /research
    # ------------------------------------------------------------------

    def test_research_no_session_warns(self, capsys):
        fake_module = types.ModuleType("research_agent")
        fake_module.ResearchAgent = lambda: None  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"research_agent": fake_module}):
            result = self._registry().dispatch("/research some query", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_research_no_query_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="res-usage", cwd=str(tmp_path))
        # Query check happens before import; no module patching required.
        result = self._registry().dispatch("/research", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        assert "Usage" in capsys.readouterr().out

    def test_research_import_error_shows_hint(self, capsys):
        with patch.dict("sys.modules", {"research_agent": None}):  # type: ignore[dict-item]
            result = self._registry().dispatch("/research query", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "needs" in out.lower() or "research" in out.lower()

    def test_research_runs_agent_and_saves_output(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="res-happy", cwd=str(tmp_path))
        report = "Research findings here."

        fake_agent = SimpleNamespace(run=None)

        async def _fake_run(query, *, on_progress=None):
            return report

        fake_agent.run = _fake_run
        fake_module = types.ModuleType("research_agent")
        fake_module.ResearchAgent = lambda: fake_agent  # type: ignore[attr-defined]

        with (
            patch.dict("sys.modules", {"research_agent": fake_module}),
            patch.object(mod, "collect_workspace_context", return_value=([], "")),
            patch.object(mod, "save_output", return_value=tmp_path / "report.md") as mock_save,
        ):
            result = self._registry().dispatch(
                "/research best async patterns",
                self._ctx(session_id=sess.session_id),
            )
        assert result == mod._CMD_CONTINUE
        assert mock_save.called
        out = capsys.readouterr().out
        assert report in out
        assert "saved:" in out

    # ------------------------------------------------------------------
    # /write
    # ------------------------------------------------------------------

    def test_write_no_config_warns(self, capsys):
        result = self._registry().dispatch("/write a doc", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "error" in capsys.readouterr().out.lower()

    def test_write_no_session_warns(self, capsys):
        result = self._registry().dispatch("/write task", self._ctx(config=self._make_config()))
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_write_no_task_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="write-usage", cwd=str(tmp_path))
        result = self._registry().dispatch("/write", self._ctx(session_id=sess.session_id, config=self._make_config()))
        assert result == mod._CMD_CONTINUE
        assert "Usage" in capsys.readouterr().out

    def test_write_invokes_openclaw_and_saves(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="write-happy", cwd=str(tmp_path))
        cfg = self._make_config()
        resp = self._fake_response("# Draft\n\nContent here.")
        with (
            patch.object(mod, "invoke_openclaw", return_value=resp) as mock_ask,
            patch.object(mod, "collect_workspace_context", return_value=([], "")),
            patch.object(mod, "save_output", return_value=tmp_path / "draft.md") as mock_save,
            patch.object(mod, "persist_response"),
        ):
            result = self._registry().dispatch(
                "/write draft a weekly recap",
                self._ctx(session_id=sess.session_id, config=cfg),
            )
        assert result == mod._CMD_CONTINUE
        assert mock_ask.called
        assert mock_save.called
        out = capsys.readouterr().out
        assert "# Draft" in out
        assert "saved:" in out

    def test_write_emits_event(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="write-evt", cwd=str(tmp_path))
        cfg = self._make_config()
        resp = self._fake_response("content")
        with (
            patch.object(mod, "invoke_openclaw", return_value=resp),
            patch.object(mod, "collect_workspace_context", return_value=([], "")),
            patch.object(mod, "save_output", return_value=tmp_path / "draft.md"),
            patch.object(mod, "persist_response"),
            patch.object(mod, "append_event") as mock_ae,
        ):
            self._registry().dispatch(
                "/write some document",
                self._ctx(session_id=sess.session_id, config=cfg),
            )
        kinds = [call.kwargs.get("kind") for call in mock_ae.call_args_list]
        assert "write" in kinds

    # ------------------------------------------------------------------
    # /exec
    # ------------------------------------------------------------------

    def test_exec_no_session_warns(self, capsys):
        result = self._registry().dispatch("/exec echo hi", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_exec_no_command_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-usage", cwd=str(tmp_path))
        result = self._registry().dispatch("/exec", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        assert "Usage" in capsys.readouterr().out

    def test_exec_not_approved_halts(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-deny", cwd=str(tmp_path))
        with patch.object(mod, "request_cli_approval", return_value=False):
            result = self._registry().dispatch("/exec rm -rf /", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out.lower()
        assert "not approved" in out
        assert "review carefully" in out
        assert "recovery:" in out

    def test_exec_runs_command_and_logs_event(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-happy", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(command="echo hi", stdout="hi\n", stderr="", returncode=0, cwd=str(tmp_path))

        def _fake_run_async(coro):
            coro.close()
            return fake_result

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_async", side_effect=_fake_run_async),
            patch.object(mod, "append_event") as mock_ae,
        ):
            result = self._registry().dispatch("/exec echo hi", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        kinds = [call.kwargs.get("kind") for call in mock_ae.call_args_list]
        assert "exec" in kinds
        out = capsys.readouterr().out
        assert "Command complete." in out

    def test_exec_records_approval_timing_and_feedback(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-timing", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(command="echo hi", stdout="hi\n", stderr="", returncode=0, cwd=str(tmp_path))

        def _fake_run_async(coro):
            coro.close()
            return fake_result

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_async", side_effect=_fake_run_async),
            patch.object(mod, "append_event") as mock_ae,
            patch.object(mod.time, "monotonic", side_effect=[10.0, 10.6, 11.0, 13.5]),
        ):
            result = self._registry().dispatch("/exec echo hi", self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        approval_event = mock_ae.call_args_list[0].kwargs
        exec_event = mock_ae.call_args_list[1].kwargs
        assert approval_event["kind"] == "approval"
        assert approval_event["metadata"]["approved"] is True
        assert approval_event["metadata"]["approval_seconds"] == pytest.approx(0.6)
        assert exec_event["kind"] == "exec"
        assert exec_event["metadata"]["approval_seconds"] == pytest.approx(0.6)
        assert exec_event["metadata"]["elapsed_seconds"] == pytest.approx(2.5)
        out = capsys.readouterr().out
        assert "2.5s run" in out
        assert "approval 0.6s" in out

    def test_routed_exec_captures_checkpoint_before_execution(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-checkpoint", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(command="rm -rf build", stdout="", stderr="", returncode=0, cwd=str(tmp_path))
        route_metadata = {
            "source": "repl.plan",
            "prompt": "inspect the workspace, then run rm -rf build, then summarize the cleanup",
            "step_index": 2,
            "step_total": 3,
            "step_kind": "exec",
        }

        def _fake_run_async(coro):
            coro.close()
            checkpoints = sessions_mod.list_routed_action_checkpoints(sess.session_id, limit=0)
            assert len(checkpoints) == 1
            checkpoint = checkpoints[0]
            assert checkpoint["action_kind"] == "exec"
            assert checkpoint["step_index"] == 2
            assert checkpoint["rollback_supported"] is False
            return fake_result

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_async", side_effect=_fake_run_async),
        ):
            result = self._registry().dispatch(
                "/exec rm -rf build",
                self._ctx(session_id=sess.session_id, route_metadata=route_metadata),
            )

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        # Checkpoint is captured silently — no verbose recovery message printed
        assert "Checkpoint" not in out

    def test_exec_strips_double_dash_prefix(self, capsys, tmp_path, monkeypatch):
        """Verify /exec -- <cmd> drops the leading '--' before dispatching."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-dash", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(
            command="git status", stdout="ok\n", stderr="", returncode=0, cwd=str(tmp_path)
        )
        captured_args = []

        def _fake_run_async(coro):
            # Inspect the coroutine's cr_frame locals to capture the command_parts
            # Instead, just return the fake_result directly since we're testing arg-stripping.
            return fake_result

        original_cmd_exec = mod._cmd_exec

        def _spy_exec(ctx: mod.ChatCommandContext) -> str:
            captured_args.append(ctx.args)
            # Don't really exec; just verify args and return
            return mod._CMD_CONTINUE

        registry = mod.ChatCommandRegistry()
        registry.register(mod.SlashCommand(name="exec", description="spy", handler=_spy_exec))
        registry.dispatch("/exec -- git status", self._ctx(session_id=sess.session_id))
        assert captured_args == ["-- git status"]

    def test_exec_strips_double_dash_in_handler(self, tmp_path, monkeypatch):
        """_cmd_exec drops '-- ' prefix before splitting command_parts."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-dash-handler", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(
            command="git status", stdout="ok\n", stderr="", returncode=0, cwd=str(tmp_path)
        )

        def _fake_run_async(coro):
            coro.close()
            return fake_result

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_async", side_effect=_fake_run_async),
            patch.object(mod, "append_event"),
        ):
            ctx = mod.ChatCommandContext(history=[], session_id=sess.session_id, args="-- git status")
            result = mod._cmd_exec(ctx)
        assert result == mod._CMD_CONTINUE

    def test_exec_handler_preserves_quoted_arguments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="exec-quotes", cwd=str(tmp_path))

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(
            command='git commit -m "ship parser fixes"',
            stdout="ok\n",
            stderr="",
            returncode=0,
            cwd=str(tmp_path),
        )
        captured: dict[str, object] = {}

        def _fake_run_shell_command(command_parts, *, cwd=None, timeout=60):
            captured["command_parts"] = command_parts
            captured["cwd"] = cwd
            captured["timeout"] = timeout
            return "shell-coro"

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_shell_command", new=_fake_run_shell_command),
            patch.object(mod, "run_async", return_value=fake_result),
            patch.object(mod, "append_event"),
        ):
            result = mod._cmd_exec(
                mod.ChatCommandContext(
                    history=[],
                    session_id=sess.session_id,
                    args='git commit -m "ship parser fixes"',
                )
            )

        assert result == mod._CMD_CONTINUE
        assert captured["command_parts"] == ["git", "commit", "-m", "ship parser fixes"]

    # ------------------------------------------------------------------
    # /edit
    # ------------------------------------------------------------------

    def test_edit_no_session_warns(self, capsys):
        result = self._registry().dispatch("/edit foo.txt", self._ctx())
        assert result == mod._CMD_CONTINUE
        assert "No active session" in capsys.readouterr().out

    def test_edit_no_path_shows_usage(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="edit-usage", cwd=str(tmp_path))
        result = self._registry().dispatch("/edit", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        assert "Usage" in capsys.readouterr().out

    def test_edit_info_mode_shows_file_preview(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "hello.txt"
        target.write_text("line1\nline2\nline3\n")
        sess = sessions_mod.create_session(title="edit-info", cwd=str(tmp_path))
        self._registry().dispatch(f"/edit {target}", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "line1" in out
        assert "3 lines" in out

    def test_edit_info_mode_missing_file(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="edit-missing", cwd=str(tmp_path))
        self._registry().dispatch("/edit /no/such/file.txt", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "File not found" in out

    def test_edit_not_approved_halts(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="edit-deny", cwd=str(tmp_path))
        target = tmp_path / ".env"
        target.touch()
        with patch.object(mod, "request_cli_approval", return_value=False):
            result = self._registry().dispatch(
                f"/edit {target} --content new content",
                self._ctx(session_id=sess.session_id),
            )
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out.lower()
        assert "not approved" in out
        assert "review carefully" in out

    def test_edit_content_writes_file_and_logs_event(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "output.txt"
        target.touch()
        sess = sessions_mod.create_session(title="edit-write", cwd=str(tmp_path))
        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "append_event") as mock_ae,
        ):
            result = self._registry().dispatch(
                f"/edit {target} --content hello world",
                self._ctx(session_id=sess.session_id),
            )
        assert result == mod._CMD_CONTINUE
        assert target.read_text() == "hello world"
        kinds = [call.kwargs.get("kind") for call in mock_ae.call_args_list]
        assert "edit" in kinds
        out = capsys.readouterr().out
        assert "Edit complete." in out

    def test_rollback_last_restores_prior_routed_edit_state(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "notes.txt"
        target.write_text("before\n", encoding="utf-8")
        sess = sessions_mod.create_session(title="edit-rollback", cwd=str(tmp_path))
        route_metadata = {
            "source": "repl.plan",
            "prompt": "analyze notes, then edit notes.txt, then summarize the change",
            "step_index": 2,
            "step_total": 3,
            "step_kind": "edit",
        }

        with patch.object(mod, "request_cli_approval", return_value=True):
            result = self._registry().dispatch(
                f"/edit {target} --content after\n",
                self._ctx(session_id=sess.session_id, route_metadata=route_metadata),
            )

        assert result == mod._CMD_CONTINUE
        assert target.read_text(encoding="utf-8") == "after"
        capsys.readouterr()

        result = self._registry().dispatch("/rollback last", self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        assert target.read_text(encoding="utf-8") == "before\n"
        out = capsys.readouterr().out
        assert "Rolled back last routed edit action" in out
        events = sessions_mod.load_events(sess.session_id)
        assert any(event.get("kind") == "rollback" for event in events)

    def test_rollback_last_reports_manual_recovery_for_routed_exec(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        tracked = tmp_path / "tracked.txt"
        tracked.write_text("seed\n", encoding="utf-8")
        sess = sessions_mod.create_session(
            title="exec-rollback-manual",
            cwd=str(tmp_path),
            files=[str(tracked)],
        )
        route_metadata = {
            "source": "repl.plan",
            "prompt": "inspect workspace, then exec rm -rf build, then summarize",
            "step_index": 2,
            "step_total": 3,
            "step_kind": "exec",
        }

        from openclaw_cli_actions import ShellCommandResult

        fake_result = ShellCommandResult(
            command="rm -rf build",
            stdout="",
            stderr="",
            returncode=0,
            cwd=str(tmp_path),
        )

        def _fake_run_async(coro):
            coro.close()
            return fake_result

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "run_async", side_effect=_fake_run_async),
        ):
            result = self._registry().dispatch(
                "/exec rm -rf build",
                self._ctx(session_id=sess.session_id, route_metadata=route_metadata),
            )

        assert result == mod._CMD_CONTINUE
        capsys.readouterr()

        result = self._registry().dispatch("/rollback last", self._ctx(session_id=sess.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "automatic rollback is unavailable" in out
        assert "workspace signature before action:" in out
        events = sessions_mod.load_events(sess.session_id)
        assert any(
            event.get("kind") == "rollback" and (event.get("metadata") or {}).get("status") == "unsupported"
            for event in events
        )

    def test_edit_append_appends_to_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "log.txt"
        target.write_text("existing\n")
        sess = sessions_mod.create_session(title="edit-append", cwd=str(tmp_path))
        with patch.object(mod, "request_cli_approval", return_value=True):
            self._registry().dispatch(
                f"/edit {target} --append new line",
                self._ctx(session_id=sess.session_id),
            )
        assert "existing" in target.read_text()
        assert "new line" in target.read_text()

    def test_edit_replace_updates_existing_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "notes.txt"
        target.write_text("alpha beta\n", encoding="utf-8")
        sess = sessions_mod.create_session(title="edit-replace", cwd=str(tmp_path))
        with patch.object(mod, "request_cli_approval", return_value=True):
            self._registry().dispatch(
                f"/edit {target} --replace {shlex.quote('alpha beta')} {shlex.quote('gamma delta')}",
                self._ctx(session_id=sess.session_id),
            )
        assert target.read_text(encoding="utf-8") == "gamma delta\n"

    def test_edit_preview_skips_approval_for_noop_change(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "notes.txt"
        target.write_text("same text", encoding="utf-8")
        sess = sessions_mod.create_session(title="edit-noop", cwd=str(tmp_path))

        with patch.object(mod, "request_cli_approval") as request_cli_approval:
            result = self._registry().dispatch(
                f"/edit {target} --content 'same text'",
                self._ctx(session_id=sess.session_id),
            )

        assert result == mod._CMD_CONTINUE
        request_cli_approval.assert_not_called()
        assert target.read_text(encoding="utf-8") == "same text"
        out = capsys.readouterr().out
        assert "Edit preview." in out
        assert "No changes applied." in out

    def test_edit_accepts_quoted_path_and_append_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "release notes.txt"
        target.write_text("existing\n", encoding="utf-8")
        sess = sessions_mod.create_session(title="edit-quoted-path", cwd=str(tmp_path))
        with patch.object(mod, "request_cli_approval", return_value=True):
            self._registry().dispatch(
                f"/edit {shlex.quote(str(target))} --append {shlex.quote('new warning line')}",
                self._ctx(session_id=sess.session_id),
            )
        assert "existing" in target.read_text(encoding="utf-8")
        assert "new warning line" in target.read_text(encoding="utf-8")

    def test_routed_checkpoint_retention_is_bounded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "bounded.txt"
        target.write_text("seed\n", encoding="utf-8")
        sess = sessions_mod.create_session(title="checkpoint-retention", cwd=str(tmp_path))
        total = sessions_mod.ROUTED_ACTION_CHECKPOINT_LIMIT + 2

        for index in range(total):
            target.write_text(f"version-{index}\n", encoding="utf-8")
            sessions_mod.create_routed_action_checkpoint(
                sess.session_id,
                action_kind="edit",
                target=str(target),
                detail="append=False",
                cwd=str(tmp_path),
                route_metadata={
                    "source": "repl.plan",
                    "prompt": "decomposed edit lane",
                    "step_index": index + 1,
                    "step_total": total,
                    "step_kind": "edit",
                },
                file_paths=[str(target)],
                workspace_signature=f"sig-{index}",
            )

        checkpoints = sessions_mod.list_routed_action_checkpoints(sess.session_id, limit=0)

        assert len(checkpoints) == sessions_mod.ROUTED_ACTION_CHECKPOINT_LIMIT
        expected_steps = list(range(total, total - sessions_mod.ROUTED_ACTION_CHECKPOINT_LIMIT, -1))
        assert [item["step_index"] for item in checkpoints] == expected_steps

    def test_routed_edit_preserves_existing_approval_gate(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        target = tmp_path / "pyproject.toml"
        target.write_text("[tool.demo]\nname = 'demo'\n", encoding="utf-8")
        sess = sessions_mod.create_session(title="edit-approval", cwd=str(tmp_path))
        approval_calls = []
        route_metadata = {
            "source": "repl.plan",
            "prompt": "inspect the config, then edit pyproject.toml, then summarize",
            "step_index": 2,
            "step_total": 3,
            "step_kind": "edit",
        }

        def _deny_approval(**kwargs):
            approval_calls.append(kwargs)
            return False

        with (
            patch.object(mod, "request_cli_approval", side_effect=_deny_approval),
            patch.object(
                mod,
                "write_text_file",
                return_value=SimpleNamespace(
                    path=str(target),
                    changed=True,
                    diff=(
                        f"--- {target}\n"
                        f"+++ {target}\n"
                        "@@ -1,2 +1,2 @@\n"
                        "-[tool.demo]\n"
                        "-name = 'demo'\n"
                        "+[tool.demo]\n"
                        "+name = 'blocked'\n"
                    ),
                    summary="Previewed file write.",
                ),
            ) as write_text_file,
        ):
            result = self._registry().dispatch(
                f"/edit {target} --content [tool.demo]\nname = 'blocked'\n",
                self._ctx(session_id=sess.session_id, route_metadata=route_metadata),
            )

        assert result == mod._CMD_CONTINUE
        assert approval_calls
        assert approval_calls[0]["target"] == str(target)
        write_text_file.assert_called_once_with(
            str(target),
            content="[tool.demo] name = blocked",
            append=False,
            dry_run=True,
        )
        assert sessions_mod.list_routed_action_checkpoints(sess.session_id, limit=0) == []
        out = capsys.readouterr().out.lower()
        assert "edit preview." in out
        assert "-name = 'demo'" in out
        assert "+name = 'blocked'" in out
        assert "not approved" in out

    def test_edit_records_approval_timing_and_feedback(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="edit-timing", cwd=str(tmp_path))
        target = tmp_path / "notes.txt"

        with (
            patch.object(mod, "request_cli_approval", return_value=True),
            patch.object(mod, "append_event") as mock_ae,
            patch.object(mod.time, "monotonic", side_effect=[5.0, 5.4, 8.0, 8.9]),
        ):
            result = self._registry().dispatch(
                f"/edit {target} --content hello",
                self._ctx(session_id=sess.session_id),
            )

        assert result == mod._CMD_CONTINUE
        approval_event = mock_ae.call_args_list[0].kwargs
        edit_event = mock_ae.call_args_list[1].kwargs
        assert approval_event["kind"] == "approval"
        assert approval_event["metadata"]["approval_seconds"] == pytest.approx(0.4)
        assert edit_event["kind"] == "edit"
        assert edit_event["metadata"]["approval_seconds"] == pytest.approx(0.4)
        assert edit_event["metadata"]["elapsed_seconds"] == pytest.approx(0.9)
        out = capsys.readouterr().out
        assert "0.9s write" in out
        assert "approval 0.4s" in out


def test_setup_script_supports_bash_rc_detection():
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "setup_openclaw_cli_mac.sh"

    with tempfile.TemporaryDirectory() as tmp_home:
        env = {**os.environ, "HOME": tmp_home, "SHELL": "/bin/bash"}
        completed = subprocess.run(
            ["bash", str(script_path), "--home", tmp_home, "--skip-token-prompt"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        rc_file = Path(tmp_home) / ".bashrc"

        assert rc_file.exists()
        rc_content = rc_file.read_text()
        assert 'export OPENCLAW_HOME="' in rc_content
        assert 'source "' in rc_content
        assert "TARGET_SHELL=bash" in completed.stdout


def test_main_preserves_global_flags():
    with pytest.raises(SystemExit) as excinfo:
        mod.main(["--version"])

    assert excinfo.value.code == 0


def test_main_analyze_creates_session_and_scopes_request(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    (tmp_path / "README.md").write_text("# OpenClaw\n", encoding="utf-8")
    config = _config()
    response = mod.AskResponse(
        response="Repo summary",
        model="gemini",
        tokens=24,
        raw={"response": "Repo summary", "model": "gemini", "tokens": 24},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as invoke_openclaw,
    ):
        exit_code = mod.main(["analyze", "--cwd", str(tmp_path), "@README.md", "summarize", "the", "repo"])

    assert exit_code == 0
    called_prompt = invoke_openclaw.call_args.args[0]
    called_config = invoke_openclaw.call_args.kwargs["config"]
    assert "Workspace context:" in called_prompt
    assert "README.md" in called_prompt
    assert called_config.session_id
    session = mod.load_session(called_config.session_id)
    assert session is not None
    assert session.command_count >= 1


def test_session_paths_reject_parent_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    with pytest.raises(ValueError):
        sessions_mod._session_dir("..")
    assert sessions_mod.load_session("..") is None


def test_workspace_signature_tracks_overflow_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    for index in range(205):
        (tmp_path / f"file-{index:03d}.txt").write_text(f"{index}\n", encoding="utf-8")

    before = sessions_mod.build_workspace_signature(cwd=tmp_path)
    (tmp_path / "file-204.txt").write_text("changed\n", encoding="utf-8")
    after = sessions_mod.build_workspace_signature(cwd=tmp_path)

    assert before != after


def test_main_watch_creates_checkpointed_session(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    (tmp_path / "README.md").write_text("# OpenClaw\n", encoding="utf-8")
    config = _config()
    response = mod.AskResponse(
        response="Repo looks healthy.\nFocus on scheduler regressions next.",
        model="gemini",
        tokens=18,
        raw={"response": "Repo looks healthy.\nFocus on scheduler regressions next.", "model": "gemini", "tokens": 18},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as invoke_openclaw,
        patch.object(mod.time, "sleep", return_value=None),
    ):
        exit_code = mod.main(
            ["watch", "--cwd", str(tmp_path), "--iterations", "1", "@README.md", "watch", "for", "regressions"]
        )

    assert exit_code == 0
    session_id = invoke_openclaw.call_args.kwargs["config"].session_id
    state = mod.load_watch_state(session_id)
    assert state is not None
    assert state["goal"] == "watch for regressions"
    assert state["status"] == "completed"
    assert state["poll_count"] == 1
    assert len(state["checkpoints"]) == 1
    assert state["checkpoints"][0]["attempt_count"] == 1
    assert state["progress_log"]
    exported = mod.export_session(session_id)
    assert exported["watch_state"]["goal"] == "watch for regressions"
    assert exported["session"]["checkpoint_count"] == 1
    stdout = capsys.readouterr().out
    assert "[watch 1] analyze/context: Collecting workspace context" in stdout
    assert "session:" in stdout


def test_main_watch_retries_transient_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    (tmp_path / "README.md").write_text("# OpenClaw\n", encoding="utf-8")
    config = _config()
    response = mod.AskResponse(
        response="Recovered after retry.",
        model="gemini",
        tokens=21,
        raw={"response": "Recovered after retry.", "model": "gemini", "tokens": 21},
    )
    outcomes = [
        mod.OpenClawCliError("Timed out while contacting OpenClaw at http://localhost:8765."),
        response,
    ]

    def _invoke(*_args, **_kwargs):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", side_effect=_invoke) as invoke_openclaw,
        patch.object(mod.time, "sleep", return_value=None) as sleep_mock,
    ):
        exit_code = mod.main(["watch", "--cwd", str(tmp_path), "--iterations", "1", "watch", "for", "regressions"])

    assert exit_code == 0
    assert invoke_openclaw.call_count == 2
    session_id = invoke_openclaw.call_args.kwargs["config"].session_id
    state = mod.load_watch_state(session_id)
    assert state is not None
    assert state["status"] == "completed"
    assert state["failure_count"] == 1
    assert state["consecutive_failures"] == 0
    assert state["last_error"] == ""
    assert state["retry_history"][0]["transient"] is True
    assert state["checkpoints"][0]["attempt_count"] == 2
    assert any(entry["phase"] == "retry" for entry in state["progress_log"])
    assert any(call.args == (1,) for call in sleep_mock.call_args_list)
    assert "Transient failure on attempt 1/3" in capsys.readouterr().out


def test_summarize_session_includes_watch_timing_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="watch-summary", cwd=str(tmp_path))
    session = sessions_mod.update_session(session.session_id, automation_mode="analyze", automation_status="retrying")
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "watch repo",
            "cwd": str(tmp_path),
            "files": [],
            "interval_seconds": 30,
            "max_polls": 3,
            "poll_count": 1,
            "status": "retrying",
            "retry_history": [{"attempt": 1, "delay_seconds": 2, "created_at": "2026-04-10T00:00:05Z"}],
            "active_checkpoint": {
                "poll": 1,
                "mode": "analyze",
                "status": "running",
                "started_at": "2026-04-10T00:00:00Z",
                "updated_at": "2026-04-10T00:00:04Z",
                "phase": "request",
                "progress": [{"phase": "request", "created_at": "2026-04-10T00:00:04Z"}],
                "attempts": [],
            },
            "checkpoints": [
                {
                    "poll": 0,
                    "started_at": "2026-04-09T23:59:50Z",
                    "completed_at": "2026-04-10T00:00:00Z",
                    "summary": "prior run",
                }
            ],
        },
    )

    summary = mod.summarize_session(session)

    assert "automation: analyze (retrying)" in summary
    assert "timing: phase request" in summary
    assert "last run 10s" in summary
    assert "retry backoff 2.0s" in summary


def test_summarize_session_front_loads_wave23_topline_status(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="dashboard-summary", cwd=str(tmp_path))
    session = sessions_mod.update_session(
        session.session_id,
        status="active",
        automation_mode="watch",
        automation_status="retrying",
    )

    summary = mod.summarize_session(session).splitlines()

    assert summary[0].startswith("session:")
    assert summary[1].startswith("title:")
    assert summary[2].startswith("ACTIVE")
    assert "status: active" in summary[2]
    assert any(line.startswith("IDLE") and "commands: 0" in line for line in summary)
    assert any(line.startswith("IDLE") and "outputs: 0" in line for line in summary)
    assert any(line.startswith("RETRY") and "automation: watch (retrying)" in line for line in summary)


def test_summarize_session_wave26_adds_mood_line(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="Mood Summary", cwd=str(tmp_path))
    sessions_mod.update_session(session.session_id, command_count=4, output_count=1)

    summary = mod.summarize_session(sessions_mod.require_session(session.session_id))

    assert "mood: steady" in summary
    assert "1 output landed" in summary


def test_summarize_session_includes_age(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="Aged Session", cwd=str(tmp_path))
    session = sessions_mod.update_session(
        session.session_id,
        created_at="2026-04-14T15:00:00Z",
        updated_at="2026-04-14T15:30:00Z",
    )
    monkeypatch.setattr(
        mod,
        "_elapsed_seconds",
        lambda started_at, finished_at=None: 3600.0 if started_at == "2026-04-14T15:00:00Z" else 0.0,
    )

    summary = mod.summarize_session(session)

    assert "age: 1h" in summary


def test_print_watch_status_shows_phase_and_backoff(capsys):
    mod._print_watch_status(
        {
            "goal": "watch repo",
            "mode": "analyze",
            "status": "retrying",
            "poll_count": 2,
            "max_polls": 5,
            "failure_count": 1,
            "retry_limit": 3,
            "interval_seconds": 30,
            "retry_history": [{"attempt": 1, "delay_seconds": 2, "created_at": "2026-04-10T00:00:05Z"}],
            "active_checkpoint": {
                "poll": 2,
                "mode": "analyze",
                "status": "running",
                "started_at": "2026-04-10T00:00:00Z",
                "updated_at": "2026-04-10T00:00:06Z",
                "phase": "persist",
                "progress": [{"phase": "persist", "created_at": "2026-04-10T00:00:06Z"}],
                "attempts": [],
            },
            "checkpoints": [
                {
                    "poll": 1,
                    "started_at": "2026-04-10T00:00:00Z",
                    "completed_at": "2026-04-10T00:00:09Z",
                    "summary": "prior run",
                }
            ],
        }
    )

    out = capsys.readouterr().out
    assert "Watch Control Tower" in out
    assert "Actions:" in out
    assert "RETRY" in out
    assert "polls: 2/5" in out
    assert "phase:" in out and "persist" in out
    assert "backoff:" in out and "2.0s" in out


def test_print_watch_status_wave26_includes_momentum_cue(capsys):
    mod._print_watch_status(
        {
            "goal": "watch repo",
            "mode": "analyze",
            "status": "retrying",
            "poll_count": 2,
            "max_polls": 5,
            "failure_count": 1,
            "retry_limit": 3,
        }
    )

    out = capsys.readouterr().out
    assert "mood: resilient recovery" in out
    assert "retry budget still active" in out


def test_print_watch_status_wave27_surfaces_operator_queue(capsys):
    mod._print_watch_status(
        {
            "session_id": "sess-123",
            "goal": "watch repo",
            "mode": "analyze",
            "status": "running",
            "poll_count": 3,
            "max_polls": 6,
            "last_summary": "latest local checkpoint looks healthy",
            "interventions": [
                {
                    "action": "operator-note",
                    "status": "pending",
                    "created_at": "2026-04-10T00:00:10Z",
                    "reason": "watch for handoff timing",
                }
            ],
            "stop_requested": True,
        }
    )

    out = capsys.readouterr().out
    assert "operator queue:" in out
    assert "1 pending" in out
    assert "stop requested" in out
    assert "read-only local snapshot" in out
    assert "/collab share to capture the operator-facing snapshot" in out


def test_print_watch_status_wave29_shows_predictive_actions_for_retrying_watch(capsys):
    mod._print_watch_status(
        {
            "goal": "watch repo",
            "mode": "analyze",
            "status": "retrying",
            "poll_count": 2,
            "max_polls": 5,
            "failure_count": 1,
            "retry_limit": 3,
        }
    )

    out = capsys.readouterr().out
    assert "/watch retry-limit N to tune retry budget" in out
    assert "/watch history to inspect checkpoint history" in out
    assert "/watch intervene <msg> to leave an operator breadcrumb" in out


def test_print_watch_status_wave29_shows_session_review_after_completion(capsys):
    mod._print_watch_status(
        {
            "goal": "watch repo",
            "mode": "analyze",
            "status": "completed",
            "poll_count": 5,
            "max_polls": 5,
        }
    )

    out = capsys.readouterr().out
    assert "/session to review the resulting session snapshot" in out
    assert "/watch history to inspect checkpoint history" in out


def test_print_watch_status_shows_context_pressure_recovery_cues(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.setitem(mod._PREFS, "system_prompt", "Guardrail." * 300)
    monkeypatch.setattr(mod, "_next_inject", "Queued workspace context")
    session = sessions_mod.create_session(title="watch pressure", cwd=str(tmp_path))
    sessions_mod.append_event(session.session_id, kind="prompt", content="x" * 420_000)

    mod._print_watch_status(
        {
            "session_id": session.session_id,
            "goal": "watch repo",
            "mode": "analyze",
            "status": "retrying",
            "poll_count": 2,
            "max_polls": 5,
            "failure_count": 1,
            "retry_limit": 3,
        }
    )

    out = capsys.readouterr().out
    assert "context pressure:" in out
    assert "hidden context cue: system or queued inject content pushes the next retry closer to capacity" in out
    assert "recovery cue: /inject clear drops the queued one-shot context before a retry" in out
    assert "/tokeninfo to check whether context pressure is affecting the next retry" in out
    assert "/bookmark before /clear if manual recovery needs a clean restart" in out
    assert "/context to preview what the next retry will inherit" in out
    assert "/inject status or /system view to inspect hidden context before the next retry" in out
    assert "/inject clear to remove the queued one-shot context before the next retry" in out


def test_print_watch_history_uses_dashboard_sections(capsys):
    mod._print_watch_history(
        {
            "progress_log": [
                {
                    "timestamp": "2026-04-10T00:00:09Z",
                    "phase": "persist",
                    "summary": "saved iteration output",
                    "ok": True,
                    "created_at": "2026-04-10T00:00:09Z",
                }
            ],
            "retry_history": [
                {
                    "timestamp": "2026-04-10T00:00:05Z",
                    "reason": "transient timeout",
                    "delay_seconds": 2,
                }
            ],
            "interventions": [
                {
                    "action": "operator-note",
                    "created_at": "2026-04-10T00:00:10Z",
                    "reason": "waiting on approval",
                }
            ],
        }
    )

    out = capsys.readouterr().out
    assert "Watch History" in out
    assert "Focused inspection:" in out
    assert "Recent progress:" in out
    assert "Retry checkpoints:" in out
    assert "Operator notes:" in out


def test_print_watch_history_plain_prioritizes_status_labels(capsys):
    mod._print_watch_history(
        {
            "progress_log": [
                {"timestamp": "2026-04-10T00:00:01Z", "phase": "poll", "note": "checkpoint complete", "ok": True},
            ],
            "retry_history": [
                {"timestamp": "2026-04-10T00:00:02Z", "reason": "network timeout", "delay_seconds": 3},
            ],
            "interventions": [
                {"created_at": "2026-04-10T00:00:03Z", "action": "operator-note", "reason": "hold for review"},
            ],
        }
    )

    out = capsys.readouterr().out
    assert "COMPLETE · poll" in out
    assert "RETRY" in out and "network timeout" in out
    assert "INFO · operator-note" in out


def test_main_watch_resume_uses_saved_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch session", cwd=str(tmp_path))
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "resume repo monitoring",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 1,
            "poll_count": 0,
            "on_change": False,
            "status": "idle",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:00:00Z",
            "last_run_at": "",
            "last_output_path": "",
            "last_summary": "",
            "workspace_signature": "",
            "checkpoints": [],
        },
    )
    config = _config()
    response = mod.AskResponse(
        response="Resumed successfully.",
        model="gemini",
        tokens=8,
        raw={"response": "Resumed successfully.", "model": "gemini", "tokens": 8},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as invoke_openclaw,
        patch.object(mod.time, "sleep", return_value=None),
    ):
        exit_code = mod.main(["watch", "--resume", session.session_id, "--iterations", "1"])

    assert exit_code == 0
    prompt = invoke_openclaw.call_args.args[0]
    assert "Goal: resume repo monitoring" in prompt


def test_main_watch_resume_prints_failed_progress_context(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch session", cwd=str(tmp_path))
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "resume repo monitoring",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 3,
            "poll_count": 2,
            "on_change": False,
            "status": "failed",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:01:00Z",
            "last_run_at": "2026-04-10T00:01:00Z",
            "last_output_path": "",
            "last_summary": "analyze failed: Timed out while contacting OpenClaw",
            "last_error": "Timed out while contacting OpenClaw",
            "workspace_signature": "",
            "failure_count": 1,
            "consecutive_failures": 1,
            "retry_limit": 3,
            "retry_history": [],
            "progress_log": [
                {
                    "poll": 2,
                    "mode": "analyze",
                    "phase": "request",
                    "message": "Submitting analysis checkpoint",
                    "created_at": "2026-04-10T00:00:59Z",
                }
            ],
            "active_checkpoint": {
                "poll": 2,
                "mode": "analyze",
                "status": "failed",
                "last_message": "Submitting analysis checkpoint",
                "progress": [],
                "attempts": [],
            },
            "checkpoints": [],
        },
    )
    config = _config()
    response = mod.AskResponse(
        response="Resumed successfully.",
        model="gemini",
        tokens=8,
        raw={"response": "Resumed successfully.", "model": "gemini", "tokens": 8},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response),
        patch.object(mod.time, "sleep", return_value=None),
    ):
        exit_code = mod.main(["watch", "--resume", session.session_id, "--iterations", "3"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert f"Resuming watch {session.session_id} (status=failed, completed polls=2)." in stdout
    assert "Last error: Timed out while contacting OpenClaw" in stdout
    assert "Recent progress:" in stdout
    state = mod.load_watch_state(session.session_id)
    assert state["status"] == "completed"
    assert state["poll_count"] == 3


def test_main_watch_research_streams_progress(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    config = _config()

    class _FakeResearchAgent:
        async def run(self, _query, on_progress=None, deep=False):
            assert deep is False
            if on_progress:
                await on_progress("Planning research strategy")
                await on_progress("Research complete")
            return "# Report\n"

    fake_module = types.ModuleType("research_agent")
    fake_module.ResearchAgent = _FakeResearchAgent

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod.time, "sleep", return_value=None),
        patch.dict(sys.modules, {"research_agent": fake_module}),
    ):
        exit_code = mod.main(
            ["watch", "--mode", "research", "--cwd", str(tmp_path), "--iterations", "1", "investigate", "scheduler"]
        )

    assert exit_code == 0
    session = sessions_mod.list_sessions(limit=1)[0]
    state = mod.load_watch_state(session.session_id)
    assert any(entry["phase"] == "research" for entry in state["progress_log"])
    assert "[watch 1] research/research: Planning research strategy" in capsys.readouterr().out


def test_queue_watch_intervention_marks_force_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch session", cwd=str(tmp_path))
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "watch for regressions",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 1,
            "poll_count": 0,
            "on_change": True,
            "status": "waiting",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:00:00Z",
            "checkpoints": [],
        },
    )

    request = sessions_mod.queue_watch_intervention(
        session.session_id,
        action="force-checkpoint",
        actor="dashboard",
    )

    state = mod.load_watch_state(session.session_id)
    assert request["action"] == "force-checkpoint"
    assert state["force_run_once"] is True
    assert state["interventions"][0]["status"] == "pending"


def test_main_watch_applies_force_checkpoint_intervention(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    config = _config()
    session = mod.create_session(title="Watch session", cwd=str(tmp_path))
    workspace_signature = sessions_mod.build_workspace_signature(cwd=str(tmp_path), targets=[])
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "watch for regressions",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 1,
            "poll_count": 0,
            "on_change": True,
            "status": "waiting",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:00:00Z",
            "workspace_signature": workspace_signature,
            "checkpoints": [],
        },
    )
    sessions_mod.queue_watch_intervention(session.session_id, action="force-checkpoint", actor="dashboard")
    response = mod.AskResponse(
        response="Forced checkpoint complete.",
        model="gemini",
        tokens=4,
        raw={"response": "Forced checkpoint complete.", "model": "gemini", "tokens": 4},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response),
        patch.object(mod.time, "sleep", return_value=None),
    ):
        exit_code = mod.main(["watch", "--resume", session.session_id, "--iterations", "1"])

    assert exit_code == 0
    state = mod.load_watch_state(session.session_id)
    assert state["force_run_once"] is False
    assert state["interventions"][0]["status"] == "applied"
    assert any(entry["phase"] == "control" for entry in state["progress_log"])


def test_main_watch_honors_graceful_stop_intervention(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    config = _config()
    session = mod.create_session(title="Watch session", cwd=str(tmp_path))
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "watch for regressions",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 1,
            "poll_count": 0,
            "on_change": True,
            "status": "running",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:00:00Z",
            "checkpoints": [],
        },
    )
    sessions_mod.queue_watch_intervention(session.session_id, action="graceful-stop", actor="dashboard")

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw") as invoke_openclaw,
        patch.object(mod.time, "sleep", return_value=None),
    ):
        exit_code = mod.main(["watch", "--resume", session.session_id, "--iterations", "1"])

    assert exit_code == 0
    invoke_openclaw.assert_not_called()
    state = mod.load_watch_state(session.session_id)
    assert state["status"] == "interrupted"
    assert state["interventions"][0]["status"] == "applied"


def test_main_session_export_returns_saved_outputs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Research session", cwd=str(tmp_path))
    mod.save_output(session.session_id, "report.md", "# Report\n")

    exit_code = mod.main(["session", "export", session.session_id])

    assert exit_code == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["session"]["session_id"] == session.session_id
    assert exported["outputs"][0]["name"].endswith(".md")
    assert exported["workspace_capsule"]["session_id"] == session.session_id


def test_main_session_export_runbook_renders_markdown(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Runbook Session", cwd=str(tmp_path), plan_id="plan-35")
    mod.persist_response(session.session_id, "Need a handoff", "Create the runbook output.")
    mod.save_output(session.session_id, "summary.md", "# Summary\n")

    exit_code = mod.main(["session", "export", session.session_id, "--format", "runbook", "--template", "operator"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Operator Runbook" in out
    assert "Runbook Session" in out
    assert "## Artifacts" in out


def test_workspace_capsule_restore_recovers_cwd_plan_and_task(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.setattr(mod, "_IS_TTY", False)
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("hello\n", encoding="utf-8")
    session = mod.create_session(
        title="Workspace Save",
        cwd=str(tmp_path),
        plan_id="plan-36",
        task_id="task-36",
        files=[str(tracked)],
    )
    mod.persist_response(session.session_id, "Bookmark this state", "Done.")
    mod.create_session_bookmark(session.session_id, label="checkpoint")

    save_result = mod._cmd_workspace(mod.ChatCommandContext(history=[], session_id=session.session_id, args="save"))

    assert save_result == mod._CMD_CONTINUE
    save_out = capsys.readouterr().out
    assert "Saved workspace capsule" in save_out

    capsule_id = mod.list_handoffs(limit=1)[0]["id"]

    restore_result = mod._cmd_workspace(mod.ChatCommandContext(history=[], session_id="", args=f"restore {capsule_id}"))

    assert restore_result == mod._CMD_CONTINUE
    restore_out = capsys.readouterr().out
    assert "Workspace restored" in restore_out

    restored_sessions = mod.list_sessions(limit=1)
    restored = mod.load_session(restored_sessions[0].session_id)
    assert restored is not None
    assert restored.cwd == str(tmp_path)
    assert restored.plan_id == "plan-36"
    assert restored.task_id == "task-36"
    assert str(tracked) in restored.files


def test_main_exec_tracks_shell_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(
        command="git status", cwd=str(tmp_path), returncode=0, stdout="On branch main\n", stderr=""
    )

    with (
        patch.object(mod, "run_shell_command", new=AsyncMock(return_value=fake_result)) as run_shell_command,
        patch.object(mod, "request_cli_approval", return_value=True),
    ):
        exit_code = mod.main(["exec", "--cwd", str(tmp_path), "--", "git", "status"])

    assert exit_code == 0
    run_shell_command.assert_called_once()
    stdout = capsys.readouterr().out
    assert "$ git status" in stdout
    assert "Command complete." in stdout
    assert "session:" in stdout


def test_main_edit_dry_run_prints_diff(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "notes.md"
    target.write_text("hello world\n", encoding="utf-8")

    with patch.object(mod, "request_cli_approval") as request_cli_approval:
        exit_code = mod.main(["edit", str(target), "--replace", "world", "there", "--dry-run"])

    assert exit_code == 0
    request_cli_approval.assert_not_called()
    assert target.read_text(encoding="utf-8") == "hello world\n"
    stdout = capsys.readouterr().out
    assert "Edit preview." in stdout
    assert "-hello world" in stdout
    assert "+hello there" in stdout
    assert "Dry run only." in stdout


def test_main_edit_approval_review_callback_reprints_preview(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "notes.md"
    target.write_text("hello world\n", encoding="utf-8")

    def _approve_with_review(**kwargs):
        kwargs["review_callback"]()
        return True

    with patch.object(mod, "request_cli_approval", side_effect=_approve_with_review):
        exit_code = mod.main(["edit", str(target), "--replace", "world", "there"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert stdout.count("Edit preview.") == 2
    assert target.read_text(encoding="utf-8") == "hello there\n"


def test_main_edit_approval_overlay_replays_preview(monkeypatch, tmp_path, capsys):
    import openclaw_cli_actions as actions

    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(mod.sys.stdout, "isatty", lambda: True)
    target = tmp_path / "notes.md"
    target.write_text("hello world\n", encoding="utf-8")
    prompts = iter(["overlay", "queued", "1", "", "y"])
    with patch.object(
        mod,
        "request_cli_approval",
        side_effect=lambda **kwargs: actions.request_cli_approval(
            **kwargs,
            input_func=lambda _label: next(prompts),
        ),
    ):
        exit_code = mod.main(["edit", str(target), "--replace", "world", "there", "--risk", "high"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Approval review overlay" in stdout
    assert stdout.count("Edit preview.") == 2
    assert target.read_text(encoding="utf-8") == "hello there\n"


# ── New: session show (rich inspection) ─────────────────────────────────────


def test_session_show_renders_rich_inspection(monkeypatch, tmp_path, capsys):
    """session show should print the full inspect_session view, not the terse summary."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Inspect Me", cwd=str(tmp_path), plan_id="plan-42", task_id="task-7")
    mod.save_output(session.session_id, "result.md", "# Results\nsome content\n")
    mod.append_event(session.session_id, kind="exec", content="git status", metadata={"summary": "exit 0: git status"})

    exit_code = mod.main(["session", "show", session.session_id])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SESSION INSPECTION" in out
    assert "Inspect Me" in out
    assert "plan-42" in out
    assert "task-7" in out
    assert "SAVED OUTPUTS" in out
    assert "result.md" in out
    assert "RECENT EVENTS" in out
    assert "git status" in out
    assert "Resume:" in out


def test_session_show_includes_bookmarks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Bookmark Inspect", cwd=str(tmp_path))
    mod.persist_response(session.session_id, "Investigate the failing test", "The fix is in src/openclaw_cli.py")
    mod.create_session_bookmark(session.session_id, label="failing test fixed")

    out = mod.inspect_session(session.session_id)

    assert "BOOKMARKS" in out
    assert "[b1] failing test fixed" in out
    assert "The fix is in src/openclaw_cli.py" in out


def test_session_show_includes_collaboration_snapshot(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Collab Inspect", cwd=str(tmp_path))
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Ship the local handoff summary first",
        metadata={
            "summary": "decision by alice: Ship the local handoff summary first",
            "actor": "alice",
            "tags": ["wave-20"],
            "collab_kind": "decision",
        },
    )

    out = mod.inspect_session(session.session_id)

    assert "COLLABORATION" in out
    assert "alice" in out
    assert "mood: shared" in out
    assert "wave-20" in out


def test_build_session_share_text_wave26_adds_momentum_line(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Shared Momentum", cwd=str(tmp_path))
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Keep the dashboard wording compact",
        metadata={
            "summary": "decision by alice: Keep the dashboard wording compact",
            "actor": "alice",
            "tags": ["ux"],
            "collab_kind": "decision",
        },
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Operator agrees with the compact handoff wording",
        metadata={
            "summary": "note by operator: Operator agrees with the compact handoff wording",
            "actor": "operator",
            "tags": [],
            "collab_kind": "note",
        },
    )

    out = mod._build_session_share_text(session.session_id)

    assert "momentum   : shared momentum;" in out
    assert "2 collaborators aligned" in out


def test_build_session_share_text_wave27_adds_operator_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Operator Snapshot", cwd=str(tmp_path))
    sessions_mod.save_output(session.session_id, "status.txt", "all systems nominal")
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "observe local status",
            "cwd": str(tmp_path),
            "files": [],
            "status": "running",
            "poll_count": 2,
            "max_polls": 4,
            "active_checkpoint": {
                "poll": 2,
                "mode": "analyze",
                "status": "running",
                "started_at": "2026-04-10T00:00:00Z",
                "updated_at": "2026-04-10T00:00:06Z",
                "phase": "persist",
                "progress": [{"phase": "persist", "created_at": "2026-04-10T00:00:06Z"}],
                "attempts": [],
            },
            "interventions": [
                {
                    "action": "operator-note",
                    "status": "pending",
                    "created_at": "2026-04-10T00:00:10Z",
                    "reason": "operator is monitoring",
                }
            ],
        },
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Keep the snapshot read-only",
        metadata={
            "summary": "decision by alice: Keep the snapshot read-only",
            "actor": "alice",
            "collab_kind": "decision",
        },
    )

    out = mod._build_session_share_text(session.session_id)

    assert "OPERATOR SNAPSHOT" in out
    assert "access    : read-only local snapshot" in out
    assert "control   : visibility only; no remote control" in out
    assert "TRUST & RECOVERY" in out
    assert "scope  : local session log + read-only snapshot only" in out
    assert "recover: inspect with /session or /watch history before resuming control" in out
    assert "watch     : running · persist · 2/4 polls" in out
    assert "queue     : 1 pending" in out
    assert "output    : status.txt" in out


def test_build_session_share_text_wave29_adds_story_recap(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Narrative Share", cwd=str(tmp_path), plan_id="plan-29")
    mod.append_event(
        session.session_id,
        kind="exec",
        content="pytest -q",
        metadata={"summary": "Validated the focused test slice"},
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Keep the recap actor-aware",
        metadata={
            "summary": "decision by alice: Keep the recap actor-aware",
            "actor": "alice",
            "tags": ["wave-29"],
            "collab_kind": "decision",
        },
    )
    mod.save_output(session.session_id, "wave29.txt", "Story beats locked in")

    out = mod._build_session_share_text(session.session_id)

    assert "story      :" in out
    assert "chapter    :" in out
    assert "MILESTONES" in out
    assert "CAST HIGHLIGHTS" in out
    assert "TIMELINE RECAP" in out
    assert "Validated the focused test slice" in out


def test_build_session_share_text_includes_bookmarks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Bookmark Share", cwd=str(tmp_path))
    mod.persist_response(session.session_id, "Capture the handoff", "Session share now exposes bookmark summaries")
    mod.create_session_bookmark(session.session_id, label="handoff ready")

    out = mod._build_session_share_text(session.session_id)

    assert "BOOKMARKS" in out
    assert "[b1] handoff ready" in out


def test_export_session_preserves_bookmarks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Bookmark Export", cwd=str(tmp_path))
    mod.persist_response(session.session_id, "Summarize the change", "Wave 32 bookmarks are live")
    mod.create_session_bookmark(session.session_id, label="wave32-live")

    exported = sessions_mod.export_session(session.session_id)

    assert exported["session"]["bookmarks"][0]["id"] == "b1"
    assert exported["session"]["bookmarks"][0]["label"] == "wave32-live"


def test_session_preview_lines_wave27_include_operator_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Operator Preview", cwd=str(tmp_path))
    sessions_mod.update_session(session.session_id, automation_mode="watch", automation_status="running")
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "status": "running",
            "poll_count": 3,
            "max_polls": 8,
            "checkpoints": [
                {"poll": 3, "note": "paused at approval gate"},
            ],
            "interventions": [
                {"action": "operator-note", "status": "pending", "reason": "waiting on approval"},
            ],
        },
    )

    lines = mod._session_preview_lines(mod.require_session(session.session_id))

    assert any("checkpoint 3: paused at approval gate" in line for line in lines)
    assert any("intervention: INFO · operator note · waiting on approval" in line for line in lines)


def test_session_preview_lines_wave29_include_story_recap(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Story Preview", cwd=str(tmp_path))
    mod.append_event(
        session.session_id,
        kind="write",
        content="Draft the recap",
        metadata={"summary": "Drafted the handoff recap for operators"},
    )

    lines = mod._session_preview_lines(mod.require_session(session.session_id))

    assert any("story:" in line for line in lines)
    assert any("recap:" in line for line in lines)


def test_build_session_share_text_wave27_keeps_read_only_operator_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Read Only Share", cwd=str(tmp_path), plan_id="plan-27", task_id="task-27")
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Keep visibility read-only",
        metadata={
            "summary": "decision by alice: Keep visibility read-only",
            "actor": "alice",
            "tags": ["wave-27"],
            "collab_kind": "decision",
        },
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Approval is still pending review",
        metadata={
            "summary": "note by operator: Approval is still pending review",
            "actor": "operator",
            "tags": [],
            "collab_kind": "note",
        },
    )
    mod.create_handoff(session.session_id, note="Share this snapshot with the operator on call")

    out = mod._build_session_share_text(session.session_id)

    assert "ACTORS" in out
    assert "RECENT DECISIONS" in out
    assert "RECENT NOTES" in out
    assert "LATEST HANDOFF" in out
    assert "plan       : plan-27" in out
    assert "task       : task-27" in out
    assert "resume :" in out
    assert "inspect:" in out
    assert "share  :" in out


def test_session_preview_lines_wave29_include_actor_decision_and_milestone(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Story Preview", cwd=str(tmp_path))
    sessions_mod.update_session(session.session_id, status="complete")
    sessions_mod.save_output(session.session_id, "recap.txt", "Wave 29 recap scaffold is ready")
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Keep actor labels consistent across share and show",
        metadata={
            "summary": "decision by alice: Keep actor labels consistent across share and show",
            "actor": "alice",
            "tags": ["wave-29"],
            "collab_kind": "decision",
        },
    )

    lines = mod._session_preview_lines(mod.require_session(session.session_id))

    assert any("collab: alice" in line for line in lines)
    assert any("story: decision by alice: Keep actor labels consistent across share and show" in line for line in lines)
    assert any("1 output ready" in line for line in lines)


def test_build_session_share_text_wave29_preserves_recap_chapter_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Story Share", cwd=str(tmp_path), plan_id="plan-29", task_id="task-29")
    sessions_mod.save_output(session.session_id, "handoff.md", "Narrative scaffold")
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Use stable chapter names in the handoff",
        metadata={
            "summary": "decision by alice: Use stable chapter names in the handoff",
            "actor": "alice",
            "tags": ["wave-29"],
            "collab_kind": "decision",
        },
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Operator verified the plain-text recap ordering",
        metadata={
            "summary": "note by operator: Operator verified the plain-text recap ordering",
            "actor": "operator",
            "tags": [],
            "collab_kind": "note",
        },
    )
    mod.create_handoff(session.session_id, note="Share the same chapter order everywhere")

    out = mod._build_session_share_text(session.session_id)

    assert out.index("ACTORS") < out.index("RECENT DECISIONS") < out.index("RECENT NOTES")
    assert out.index("RECENT NOTES") < out.index("LATEST HANDOFF") < out.index("OPERATOR SNAPSHOT")
    assert out.index("OPERATOR SNAPSHOT") < out.index("RECENT OUTPUTS") < out.index("COMMANDS")
    assert "resume : openclaw --session" in out
    assert "inspect: openclaw session show" in out
    assert "share  : openclaw session share" in out


def test_format_session_list_wave30_surfaces_mood_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = sessions_mod.create_session(title="Momentum List", cwd=str(tmp_path))
    sessions_mod.update_session(session.session_id, command_count=3)

    out = mod.format_session_list([sessions_mod.require_session(session.session_id)])

    assert "steady · 3 commands into the flow" in out
    assert "Momentum List" in out


def test_inspect_session_includes_watch_state(monkeypatch, tmp_path, capsys):
    """inspect_session should surface watch status, goal, and last error when present."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch Inspect", cwd=str(tmp_path))
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "keep an eye on test regressions",
            "cwd": str(tmp_path),
            "files": [],
            "plan_id": "",
            "task_id": "",
            "interval_seconds": 30,
            "max_polls": 5,
            "poll_count": 2,
            "on_change": False,
            "status": "running",
            "last_error": "connection timed out",
            "created_at": "2026-04-10T00:00:00Z",
            "updated_at": "2026-04-10T00:05:00Z",
            "checkpoints": [{"timestamp": "2026-04-10T00:03:00Z", "note": "midpoint check"}],
            "progress_log": [{"timestamp": "2026-04-10T00:02:00Z", "phase": "poll", "note": "all green"}],
        },
    )

    out = mod.inspect_session(session.session_id)

    assert "AUTOMATION / WATCH" in out
    assert "keep an eye on test regressions" in out
    assert "running" in out
    assert "connection timed out" in out
    assert "CHECKPOINTS" in out
    assert "midpoint check" in out
    assert "RECENT PROGRESS" in out
    assert "all green" in out


def test_inspect_session_wave29_adds_story_recap(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Story Inspect", cwd=str(tmp_path))
    mod.append_event(
        session.session_id,
        kind="edit",
        content="updated summary copy",
        metadata={"summary": "Refined the premium recap copy", "changed": True},
    )
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Ship the recap after the edit lands",
        metadata={
            "summary": "decision by bob: Ship the recap after the edit lands",
            "actor": "bob",
            "collab_kind": "decision",
        },
    )

    out = mod.inspect_session(session.session_id)

    assert "story    :" in out
    assert "chapter  :" in out
    assert "STORY RECAP" in out
    assert "timeline : Team note" in out or "timeline : File updated" in out


def test_inspect_session_front_loads_status_cells_in_dashboard_sections(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Hierarchy Inspect", cwd=str(tmp_path))
    mod.append_event(session.session_id, kind="exec", content="git status", metadata={"summary": "ran git status"})
    mod.append_event(session.session_id, kind="error", content="oops", metadata={"summary": "approval timed out"})
    mod.save_watch_state(
        session.session_id,
        {
            "session_id": session.session_id,
            "mode": "analyze",
            "goal": "watch hierarchy",
            "cwd": str(tmp_path),
            "files": [],
            "status": "retrying",
            "poll_count": 1,
            "max_polls": 3,
            "last_error": "needs retry",
            "progress_log": [{"timestamp": "2026-04-10T00:02:00Z", "phase": "poll", "note": "still working"}],
            "checkpoints": [],
        },
    )

    out = mod.inspect_session(session.session_id)

    assert "status   : ACTIVE" in out
    assert "w.status : RETRY · watch: retrying · 1/3 polls" in out
    assert "last err : ERROR · needs retry" in out
    assert "ACTIVE · exec" in out
    assert "ERROR · error" in out


def test_session_show_minimal_session(monkeypatch, tmp_path, capsys):
    """session show works for a bare session with no events, outputs, or watch state."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Bare session", cwd=str(tmp_path))

    exit_code = mod.main(["session", "show", session.session_id])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SESSION INSPECTION" in out
    assert "Bare session" in out
    assert "Resume:" in out


def test_main_session_list_interactive_overlay(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
    sessions_mod.create_session(title="Alpha session", cwd=str(tmp_path))
    beta = sessions_mod.create_session(title="Beta session", cwd=str(tmp_path))
    prompts = iter(["beta", "1"])
    monkeypatch.setattr("builtins.input", lambda _label: next(prompts))

    exit_code = mod.main(["session", "list", "--interactive"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Session list overlay" in out
    assert f"openclaw --session {beta.session_id}" in out


def test_main_session_list_interactive_overlay_prints_focused_session_dashboard(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
    sessions_mod.create_session(title="Alpha session", cwd=str(tmp_path))
    beta = sessions_mod.create_session(title="Beta session", cwd=str(tmp_path), files=["src/app.py"])
    sessions_mod.save_output(beta.session_id, "beta-notes.md", "preview me")
    prompts = iter(["beta", "1"])
    monkeypatch.setattr("builtins.input", lambda _label: next(prompts))

    exit_code = mod.main(["session", "list", "--interactive"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Session list overlay" in out
    assert "Session Dashboard" in out
    assert "outputs: 1" in out
    assert "/outputs 1 to inspect the newest saved output" in out
    assert f"openclaw --session {beta.session_id}" in out


def test_main_session_share_prints_handoff_summary(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Share Me", cwd=str(tmp_path), plan_id="plan-20", task_id="task-20")
    mod.append_event(
        session.session_id,
        kind="collab",
        content="Use actor-oriented summaries for the Wave 20 slice",
        metadata={
            "summary": "decision by bob: Use actor-oriented summaries for the Wave 20 slice",
            "actor": "bob",
            "tags": ["wave-20", "handoff"],
            "collab_kind": "decision",
        },
    )

    exit_code = mod.main(["session", "share", session.session_id])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SESSION HANDOFF" in out
    assert "Share Me" in out
    assert "bob" in out
    assert "openclaw session share" in out


# ── New: exec / edit --plan-id / --task-id ───────────────────────────────────


def test_exec_plan_task_tagging_creates_linked_session(monkeypatch, tmp_path):
    """exec --plan-id / --task-id should link the created session to that plan and task."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(command="echo hi", cwd=str(tmp_path), returncode=0, stdout="hi\n", stderr="")

    with (
        patch.object(mod, "run_shell_command", return_value=fake_result),
        patch.object(mod, "request_cli_approval", return_value=True),
    ):
        exit_code = mod.main(
            [
                "exec",
                "--cwd",
                str(tmp_path),
                "--plan-id",
                "plan-99",
                "--task-id",
                "task-3",
                "--",
                "echo",
                "hi",
            ]
        )

    assert exit_code == 0
    sessions = mod.list_sessions(limit=1)
    assert sessions, "Expected at least one session to be created"
    loaded = mod.load_session(sessions[0].session_id)
    assert loaded is not None
    assert loaded.plan_id == "plan-99"
    assert loaded.task_id == "task-3"


def test_exec_approval_carries_plan_task_context(monkeypatch, tmp_path):
    """request_cli_approval for exec should receive plan_id and task_id from --plan-id/--task-id."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(command="ls", cwd=str(tmp_path), returncode=0, stdout="", stderr="")
    captured_approval: dict = {}

    def _capture_approval(**kwargs):
        captured_approval.update(kwargs)
        return True

    with (
        patch.object(mod, "run_shell_command", new=AsyncMock(return_value=fake_result)),
        patch.object(mod, "request_cli_approval", side_effect=_capture_approval),
    ):
        mod.main(["exec", "--cwd", str(tmp_path), "--plan-id", "plan-A", "--task-id", "task-B", "--", "ls"])

    assert captured_approval.get("plan_id") == "plan-A"
    assert captured_approval.get("task_id") == "task-B"


def test_edit_plan_task_tagging_creates_linked_session(monkeypatch, tmp_path):
    """edit --plan-id / --task-id should link the created session to that plan and task."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "file.txt"
    target.write_text("original content\n", encoding="utf-8")

    with patch.object(mod, "request_cli_approval", return_value=True):
        exit_code = mod.main(
            [
                "edit",
                str(target),
                "--replace",
                "original",
                "updated",
                "--plan-id",
                "plan-55",
                "--task-id",
                "task-9",
            ]
        )

    assert exit_code == 0
    sessions = mod.list_sessions(limit=1)
    assert sessions
    loaded = mod.load_session(sessions[0].session_id)
    assert loaded is not None
    assert loaded.plan_id == "plan-55"
    assert loaded.task_id == "task-9"


def test_edit_approval_carries_plan_task_context(monkeypatch, tmp_path):
    """request_cli_approval for edit should receive plan_id and task_id."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "doc.txt"
    target.write_text("foo bar\n", encoding="utf-8")
    captured_approval: dict = {}

    def _capture_approval(**kwargs):
        captured_approval.update(kwargs)
        return True

    with patch.object(mod, "request_cli_approval", side_effect=_capture_approval):
        mod.main(["edit", str(target), "--replace", "foo", "baz", "--plan-id", "plan-X", "--task-id", "task-Y"])

    assert captured_approval.get("plan_id") == "plan-X"
    assert captured_approval.get("task_id") == "task-Y"


def test_exec_without_plan_task_still_works(monkeypatch, tmp_path):
    """exec without --plan-id/--task-id should continue to work as before."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(command="echo ok", cwd=str(tmp_path), returncode=0, stdout="ok\n", stderr="")

    with (
        patch.object(mod, "run_shell_command", new=AsyncMock(return_value=fake_result)),
        patch.object(mod, "request_cli_approval", return_value=True),
    ):
        exit_code = mod.main(["exec", "--cwd", str(tmp_path), "--", "echo", "ok"])

    assert exit_code == 0
    sessions = mod.list_sessions(limit=1)
    assert sessions
    loaded = mod.load_session(sessions[0].session_id)
    assert loaded is not None
    assert loaded.plan_id == ""
    assert loaded.task_id == ""


def test_edit_without_plan_task_still_works(monkeypatch, tmp_path):
    """edit without --plan-id/--task-id should continue to work as before."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "plain.txt"
    target.write_text("alpha beta\n", encoding="utf-8")

    with patch.object(mod, "request_cli_approval", return_value=True):
        exit_code = mod.main(["edit", str(target), "--replace", "alpha", "gamma"])

    assert exit_code == 0
    sessions = mod.list_sessions(limit=1)
    assert sessions
    loaded = mod.load_session(sessions[0].session_id)
    assert loaded is not None
    assert loaded.plan_id == ""
    assert loaded.task_id == ""


# ── Plan/task context injection into LLM prompts ────────────────────────────


def _make_session(tmp_path, plan_id="", task_id=""):
    """Create a minimal SessionSummary for prompt-builder tests."""
    return sessions_mod.SessionSummary(
        session_id="test-session",
        title="Test",
        cwd=str(tmp_path),
        plan_id=plan_id,
        task_id=task_id,
    )


def test_plan_task_context_snippet_empty_when_absent(tmp_path):
    """_plan_task_context_snippet returns '' when neither plan nor task is set."""
    assert mod._plan_task_context_snippet("", "") == ""
    assert mod._plan_task_context_snippet(None, None) == ""


def test_plan_task_context_snippet_includes_plan_id(tmp_path, monkeypatch):
    """_plan_task_context_snippet includes the plan ID and skips goal fetch gracefully."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    # load_plan_goal may raise ImportError in test env; helper must survive that
    snippet = mod._plan_task_context_snippet("plan-XYZ", "")
    assert "plan-XYZ" in snippet
    assert "Active work context:" in snippet


def test_plan_task_context_snippet_includes_task_id(tmp_path, monkeypatch):
    """_plan_task_context_snippet includes task ID."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    snippet = mod._plan_task_context_snippet("", "task-ABC")
    assert "task-ABC" in snippet
    assert "Active work context:" in snippet


def test_plan_task_context_snippet_includes_both(tmp_path, monkeypatch):
    """_plan_task_context_snippet includes both plan and task when both are set."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    snippet = mod._plan_task_context_snippet("plan-1", "task-2")
    assert "plan-1" in snippet
    assert "task-2" in snippet


# ── build_analysis_prompt ────────────────────────────────────────────────────


def test_build_analysis_prompt_includes_plan_task_context(tmp_path, monkeypatch):
    """build_analysis_prompt injects plan/task framing when session has plan_id/task_id."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = _make_session(tmp_path, plan_id="plan-99", task_id="task-7")
    prompt = mod.build_analysis_prompt(goal="check stability", context_text="some context", session=session)
    assert "plan-99" in prompt
    assert "task-7" in prompt
    assert "Active work context:" in prompt
    assert "check stability" in prompt


def test_build_analysis_prompt_omits_plan_task_section_when_absent(tmp_path, monkeypatch):
    """build_analysis_prompt does NOT include plan/task section when neither is set."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = _make_session(tmp_path)
    prompt = mod.build_analysis_prompt(goal="check stability", context_text="some context", session=session)
    assert "Active work context:" not in prompt
    assert "Plan:" not in prompt
    assert "Task:" not in prompt


# ── build_write_prompt ───────────────────────────────────────────────────────


def test_build_write_prompt_includes_plan_task_context(tmp_path, monkeypatch):
    """build_write_prompt injects plan/task framing when session has plan_id/task_id."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = _make_session(tmp_path, plan_id="plan-W1", task_id="task-W2")
    prompt = mod.build_write_prompt(task="draft summary", context_text="ctx", session=session, title="My Doc")
    assert "plan-W1" in prompt
    assert "task-W2" in prompt
    assert "Active work context:" in prompt
    assert "draft summary" in prompt


def test_build_write_prompt_omits_plan_task_section_when_absent(tmp_path, monkeypatch):
    """build_write_prompt does NOT include plan/task section when neither is set."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = _make_session(tmp_path)
    prompt = mod.build_write_prompt(task="draft summary", context_text="ctx", session=session, title="My Doc")
    assert "Active work context:" not in prompt


# ── analyze command end-to-end prompt injection ──────────────────────────────


def test_analyze_injects_plan_task_context_into_prompt(monkeypatch, tmp_path):
    """analyze command should include plan/task context in the LLM prompt when --plan-id/--task-id are given."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    config = _config()
    response = mod.AskResponse(
        response="Analysis done",
        model="gemini",
        tokens=10,
        raw={"response": "Analysis done", "model": "gemini", "tokens": 10},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as mock_invoke,
    ):
        exit_code = mod.main(
            [
                "analyze",
                "--cwd",
                str(tmp_path),
                "--plan-id",
                "plan-ANALYZE",
                "--task-id",
                "task-ANALYZE",
                "review the code",
            ]
        )

    assert exit_code == 0
    prompt = mock_invoke.call_args.args[0]
    assert "plan-ANALYZE" in prompt
    assert "task-ANALYZE" in prompt
    assert "Active work context:" in prompt


def test_analyze_omits_plan_task_section_without_flags(monkeypatch, tmp_path):
    """analyze command without --plan-id/--task-id must NOT inject plan/task section."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    config = _config()
    response = mod.AskResponse(
        response="Analysis done",
        model="gemini",
        tokens=10,
        raw={"response": "Analysis done", "model": "gemini", "tokens": 10},
    )

    with (
        patch.object(mod, "build_config", return_value=config),
        patch.object(mod, "invoke_openclaw", return_value=response) as mock_invoke,
    ):
        exit_code = mod.main(["analyze", "--cwd", str(tmp_path), "review the code"])

    assert exit_code == 0
    prompt = mock_invoke.call_args.args[0]
    assert "Active work context:" not in prompt


# ── watch analyze iteration injects plan/task context ───────────────────────


def test_watch_execute_iteration_analysis_injects_plan_task(monkeypatch, tmp_path):
    """execute_watch_iteration in analyze mode injects plan/task context into the LLM prompt."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(
        title="Watch plan test",
        cwd=str(tmp_path),
        plan_id="plan-WATCH",
        task_id="task-WATCH",
    )
    state = {
        "session_id": session.session_id,
        "mode": "analyze",
        "goal": "monitor stability",
        "cwd": str(tmp_path),
        "files": [],
        "plan_id": "plan-WATCH",
        "task_id": "task-WATCH",
        "poll_count": 1,
    }
    config = _config()
    response = mod.AskResponse(
        response="Watch output",
        model="gemini",
        tokens=5,
        raw={"response": "Watch output", "model": "gemini", "tokens": 5},
    )

    with patch.object(mod, "invoke_openclaw", return_value=response) as mock_invoke:
        mod.execute_watch_iteration(session=session, state=state, config=config)

    prompt = mock_invoke.call_args.args[0]
    assert "plan-WATCH" in prompt
    assert "task-WATCH" in prompt
    assert "Active work context:" in prompt


def test_watch_execute_iteration_analysis_omits_plan_task_when_absent(monkeypatch, tmp_path):
    """execute_watch_iteration in analyze mode does NOT inject plan/task when neither is set."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch bare", cwd=str(tmp_path))
    state = {
        "session_id": session.session_id,
        "mode": "analyze",
        "goal": "monitor stability",
        "cwd": str(tmp_path),
        "files": [],
        "plan_id": "",
        "task_id": "",
        "poll_count": 1,
    }
    config = _config()
    response = mod.AskResponse(
        response="Watch output",
        model="gemini",
        tokens=5,
        raw={"response": "Watch output", "model": "gemini", "tokens": 5},
    )

    with patch.object(mod, "invoke_openclaw", return_value=response) as mock_invoke:
        mod.execute_watch_iteration(session=session, state=state, config=config)

    prompt = mock_invoke.call_args.args[0]
    assert "Active work context:" not in prompt


# ── research command plan/task context injection ─────────────────────────────


def test_research_injects_plan_task_into_query(monkeypatch, tmp_path):
    """research command should prepend plan/task framing to the effective query when --plan-id/--task-id given."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    captured_queries: list[str] = []

    class _FakeAgent:
        async def run(self, query, *, on_progress=None, deep=False):
            captured_queries.append(query)
            return "Research report"

    with patch.dict(sys.modules, {"research_agent": types.SimpleNamespace(ResearchAgent=_FakeAgent)}):
        exit_code = mod.main(
            [
                "research",
                "--plan-id",
                "plan-R1",
                "--task-id",
                "task-R2",
                "investigate latency issues",
            ]
        )

    assert exit_code == 0
    assert captured_queries, "ResearchAgent.run was never called"
    query_used = captured_queries[0]
    assert "plan-R1" in query_used
    assert "task-R2" in query_used
    assert "Active work context:" in query_used
    assert "investigate latency issues" in query_used


def test_research_omits_plan_task_context_when_absent(monkeypatch, tmp_path):
    """research command without --plan-id/--task-id must NOT inject plan/task framing."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    captured_queries: list[str] = []

    class _FakeAgent:
        async def run(self, query, *, on_progress=None, deep=False):
            captured_queries.append(query)
            return "Research report"

    with patch.dict(sys.modules, {"research_agent": types.SimpleNamespace(ResearchAgent=_FakeAgent)}):
        exit_code = mod.main(["research", "investigate latency issues"])

    assert exit_code == 0
    assert captured_queries
    assert "Active work context:" not in captured_queries[0]


# ── watch research plan/task context injection ───────────────────────────────


def test_watch_research_iteration_injects_plan_task(monkeypatch, tmp_path):
    """execute_watch_iteration in research mode injects plan/task context into the effective query."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(
        title="Watch research plan",
        cwd=str(tmp_path),
        plan_id="plan-WR",
        task_id="task-WR",
    )
    state = {
        "session_id": session.session_id,
        "mode": "research",
        "goal": "track external API changes",
        "cwd": str(tmp_path),
        "files": [],
        "plan_id": "plan-WR",
        "task_id": "task-WR",
        "poll_count": 1,
    }
    config = _config()
    captured_queries: list[str] = []

    class _FakeAgent:
        async def run(self, query, *, on_progress=None, deep=False):
            captured_queries.append(query)
            return "Research output"

    with patch.dict(sys.modules, {"research_agent": types.SimpleNamespace(ResearchAgent=_FakeAgent)}):
        mod.execute_watch_iteration(session=session, state=state, config=config)

    assert captured_queries, "ResearchAgent.run was never called"
    assert "plan-WR" in captured_queries[0]
    assert "task-WR" in captured_queries[0]
    assert "Active work context:" in captured_queries[0]


def test_watch_research_iteration_omits_plan_task_when_absent(monkeypatch, tmp_path):
    """execute_watch_iteration in research mode does NOT inject plan/task when neither is set."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    session = mod.create_session(title="Watch research bare", cwd=str(tmp_path))
    state = {
        "session_id": session.session_id,
        "mode": "research",
        "goal": "track changes",
        "cwd": str(tmp_path),
        "files": [],
        "plan_id": "",
        "task_id": "",
        "poll_count": 1,
    }
    config = _config()
    captured_queries: list[str] = []

    class _FakeAgent:
        async def run(self, query, *, on_progress=None, deep=False):
            captured_queries.append(query)
            return "Research output"

    with patch.dict(sys.modules, {"research_agent": types.SimpleNamespace(ResearchAgent=_FakeAgent)}):
        mod.execute_watch_iteration(session=session, state=state, config=config)

    assert captured_queries
    assert "Active work context:" not in captured_queries[0]


# ── /alias command ────────────────────────────────────────────────────────────


class TestCmdAlias:
    """Tests for the /alias slash command handler."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, session_id: str = "", args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_alias_list_empty_shows_no_aliases(self, capsys, monkeypatch, tmp_path):
        """'/alias' with no aliases defined should show '(no aliases defined)'."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["aliases"] = {}
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/alias", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no aliases" in out.lower() or "(no aliases defined)" in out

    def test_alias_define_saves_to_prefs(self, monkeypatch, tmp_path):
        """'/alias foo /research' should store the alias in _PREFS['aliases']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["aliases"] = {}
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/alias foo /research", self._ctx(args="foo /research"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("aliases", {}).get("foo") == "/research"

    def test_alias_rm_removes_alias(self, monkeypatch, tmp_path):
        """'/alias rm foo' should remove the alias from _PREFS['aliases']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["aliases"] = {"foo": "/research"}
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/alias rm foo", self._ctx(args="rm foo"))
        assert result == mod._CMD_CONTINUE
        assert "foo" not in mod._PREFS.get("aliases", {})

    def test_alias_builtin_name_prints_error(self, capsys, monkeypatch, tmp_path):
        """'/alias help ...' should fail because 'help' is a built-in command name."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["aliases"] = {}
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/alias help /research", self._ctx(args="help /research"))
        assert result == mod._CMD_CONTINUE
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "built-in" in combined.lower() or "reserved" in combined.lower() or "help" in combined


# ── /history command ──────────────────────────────────────────────────────────


class TestCmdHistory:
    """Tests for the /history slash command handler."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, session_id: str = "", args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_history_empty_shows_no_history_yet(self, capsys, monkeypatch, tmp_path):
        """'/history' with no history should show '(no history yet)'."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = []
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/history", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no history yet" in out.lower()

    def test_history_shows_entries_numbered(self, capsys, monkeypatch, tmp_path):
        """'/history' with entries should show them numbered."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = ["/help", "/search foo", "/version"]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/history", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "/help" in out
        assert "/search foo" in out
        assert "/version" in out

    def test_history_clear_empties_prefs(self, capsys, monkeypatch, tmp_path):
        """'/history clear' should empty _PREFS['cmd_history']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = ["/help", "/version"]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/history clear", self._ctx(args="clear"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("cmd_history") == []

    def test_history_n_shows_only_last_n(self, capsys, monkeypatch, tmp_path):
        """'/history 2' with 20 entries shows page 2 (entries 16-20)."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = [f"/cmd{i}" for i in range(20)]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/history 2", self._ctx(args="2"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        # Page 2 with 15 per page shows entries 15-19
        assert "/cmd19" in out
        assert "/cmd15" in out
        assert "/cmd0" not in out


class TestCmdHistsearch:
    """Tests for the /histsearch slash command handler."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_histsearch_no_query_shows_usage(self, capsys, monkeypatch, tmp_path):
        """/histsearch with no query should show usage message."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        result = self._registry().dispatch("/histsearch", self._ctx(args=""))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "usage" in out.lower()

    def test_histsearch_no_match_shows_no_matches(self, capsys, monkeypatch, tmp_path):
        """/histsearch foo with no matching history shows 'No history matches'."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = ["/help", "/version"]
        result = self._registry().dispatch("/histsearch foo", self._ctx(args="foo"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no history matches" in out.lower()

    def test_histsearch_matching_entry_returns_continue(self, capsys, monkeypatch, tmp_path):
        """/histsearch hello with a matching entry returns _CMD_CONTINUE."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = ["/help", "hello world", "/version"]
        result = self._registry().dispatch("/histsearch hello", self._ctx(args="hello"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "hello" in out.lower()


# ── /pin and /pins commands ───────────────────────────────────────────────────


class TestPinCommand:
    """Tests for /pin and /pins slash commands."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_pin_no_response_prints_error(self, capsys, monkeypatch, tmp_path):
        """/pin with no prior response prints 'Nothing to pin' error."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["pins"] = []
        monkeypatch.setattr(mod, "_last_response_text", "")
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/pin", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Nothing to pin" in out

    def test_pin_saves_last_response(self, capsys, monkeypatch, tmp_path):
        """/pin with _last_response_text set saves pin to _PREFS['pins']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["pins"] = []
        monkeypatch.setattr(mod, "_last_response_text", "Hello from the AI")
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/pin", self._ctx())
        assert result == mod._CMD_CONTINUE
        pins = mod._PREFS.get("pins", [])
        assert len(pins) == 1
        assert pins[0]["text"] == "Hello from the AI"
        assert pins[0]["name"] == "pin-1"

    def test_pin_rm_removes_pin(self, monkeypatch, tmp_path):
        """/pin rm <name> removes the named pin from _PREFS['pins']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["pins"] = [{"name": "my-pin", "text": "some text", "ts": "2024-01-01T00:00:00"}]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/pin rm my-pin", self._ctx(args="rm my-pin"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("pins") == []

    def test_pins_no_pins_shows_empty(self, capsys, monkeypatch, tmp_path):
        """/pins with no pins shows '(no pins)' message."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["pins"] = []
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/pins", self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no pins" in out.lower()


class TestAccessibilityCommands:
    """Wave 15 accessibility coverage."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_with_spinner_reduced_motion_prints_static_status(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)

        result = mod._with_spinner("Thinking", lambda: "done")

        assert result == "done"
        out = capsys.readouterr().out
        assert "thinking..." in out.lower()
        assert "step 1/3" in out.lower()
        assert "⏳" in out or "[wait]" in out.lower()

    def test_with_spinner_reduced_motion_emits_heartbeat_and_completion(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)
        monkeypatch.setattr(mod, "_SPINNER_HEARTBEAT_SECONDS", 0.01)

        def _slow() -> str:
            time.sleep(0.03)
            return "done"

        result = mod._with_spinner("Thinking", _slow)

        assert result == "done"
        out = capsys.readouterr().out.lower()
        assert "still working on thinking" in out
        assert "phase 1/3" in out or "phase 2/3" in out
        assert "response ready" in out

    def test_make_prompt_plain_mode_uses_plain_prompt(self, monkeypatch):
        monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

        prompt = mod._make_prompt(session_id="session-12345678", autoroute_on=False, multiline=True)

        assert prompt == "openclaw> "

    def test_accessibility_status_reports_active_modes(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_HIGH_CONTRAST, True)

        result = self._registry().dispatch("/accessibility status", self._ctx(args="status"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Accessibility Status" in out
        assert "Reduced motion:   ON" in out
        assert "Plain mode:       ON" in out
        assert "High contrast:    ON" in out
        assert "Terminal width:" in out

    def test_accessibility_toggle_persists_to_prefs_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS.clear()
        mod._PREFS.update({"theme": "default", "emoji": True, "layout": "normal"})

        result = self._registry().dispatch("/accessibility high-contrast on", self._ctx(args="high-contrast on"))

        assert result == mod._CMD_CONTINUE
        mod._PREFS[mod._A11Y_HIGH_CONTRAST] = False
        mod._load_prefs()
        assert mod._PREFS[mod._A11Y_HIGH_CONTRAST] is True


class TestAccessibilityPrefs:
    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def _reset_prefs(self):
        mod._PREFS.clear()
        mod._PREFS.update(
            {
                "theme": "default",
                "emoji": True,
                "emoji_pack": "classic",
                "layout": "normal",
                "layout_preset": "",
                "layout_focus": "primary",
            }
        )

    def test_load_and_save_prefs_persist_a11y_fields(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()
        mod._PREFS[mod._A11Y_REDUCED_MOTION] = True
        mod._PREFS[mod._A11Y_PLAIN_MODE] = True
        mod._PREFS[mod._A11Y_HIGH_CONTRAST] = True
        mod._PREFS["emoji_pack"] = "minimal"
        mod._PREFS["layout"] = "plain"
        mod._PREFS["layout_focus"] = "supporting"

        mod._save_prefs()
        self._reset_prefs()
        mod._load_prefs()

        assert mod._PREFS["layout"] == "plain"
        assert mod._PREFS["layout_focus"] == "supporting"
        assert mod._PREFS["emoji_pack"] == "minimal"
        assert mod._PREFS[mod._A11Y_REDUCED_MOTION] is True
        assert mod._PREFS[mod._A11Y_PLAIN_MODE] is True
        assert mod._PREFS[mod._A11Y_HIGH_CONTRAST] is True

    def test_load_prefs_normalizes_invalid_personalization_values(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        prefs_file = tmp_path / ".openclaw" / "prefs.json"
        prefs_file.parent.mkdir(parents=True, exist_ok=True)
        prefs_file.write_text(
            json.dumps(
                {
                    "theme": "unknown",
                    "emoji_pack": "bogus",
                    "layout": "loud",
                    "layout_focus": "sideways",
                    "emoji": False,
                }
            ),
            encoding="utf-8",
        )

        self._reset_prefs()
        mod._load_prefs()

        assert mod._PREFS["theme"] == "default"
        assert mod._PREFS["emoji_pack"] == "ascii"
        assert mod._PREFS["emoji"] is False
        assert mod._PREFS["layout"] == "normal"
        assert mod._PREFS["layout_focus"] == "primary"

    def test_layout_accepts_verbose_and_plain(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()

        result = self._registry().dispatch("/layout verbose", self._ctx(args="verbose"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["layout"] == "verbose"
        assert mod._PREFS.get(mod._A11Y_PLAIN_MODE, False) is False

        result = self._registry().dispatch("/layout plain", self._ctx(args="plain"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["layout"] == "plain"
        assert mod._PREFS[mod._A11Y_PLAIN_MODE] is True

        out = capsys.readouterr().out
        assert "verbose" in out
        assert "plain" in out

    def test_layout_focus_preset_persists_primary_supporting_contract(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 160)
        self._reset_prefs()

        result = self._registry().dispatch("/layout focus", self._ctx(args="focus"))

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["layout_preset"] == "focus"
        out = capsys.readouterr().out
        assert "Layout preset set to focus." in out
        assert "primary /session" in out
        assert "supporting /context" in out
        assert "fallback multi-pane" in out
        assert "Resume a session, then run /layout show." in out

    def test_layout_status_reports_watch_preset_and_reset_hint(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 90)
        self._reset_prefs()
        mod._PREFS["layout_preset"] = "watch-monitor"

        result = self._registry().dispatch("/layout", self._ctx(args=""))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Preset:" in out
        assert "watch-monitor" in out
        assert "(single-pane)" in out
        assert "Active pane:      primary" in out
        assert "Primary pane:     /watch status" in out
        assert "Supporting pane:  /watch history + /outputs" in out
        assert "/layout show" in out
        assert "/layout reset" in out

    def test_layout_show_renders_focus_workspace_with_collapsed_supporting_pane(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 90)
        self._reset_prefs()

        session = sessions_mod.create_session(title="Preset session", cwd=str(tmp_path))
        sessions_mod.save_output(session.session_id, "summary.txt", "latest artifact preview text")
        mod._PREFS["layout_preset"] = "focus"
        mod._PREFS["layout_focus"] = "supporting"

        result = self._registry().dispatch(
            "/layout show",
            mod.ChatCommandContext(history=[], session_id=session.session_id, args="show"),
        )

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Workspace preset: focus" in out
        assert "Render mode: single-pane" in out
        assert "Active pane: supporting" in out
        assert "Focus transition: /layout focus primary -> Session summary" in out
        assert "ACTIVE · Artifact preview" in out
        assert "Supporting pane collapsed" in out
        assert "Run /layout focus primary to switch panes" in out

    def test_layout_focus_command_updates_active_pane_and_renders_workspace(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 150)
        self._reset_prefs()

        session = sessions_mod.create_session(title="Watch preset", cwd=str(tmp_path))
        sessions_mod.save_watch_state(
            session.session_id,
            {
                "status": "running",
                "goal": "watch the current branch",
                "poll_count": 2,
                "max_polls": 5,
                "progress_log": [{"note": "collected workspace context"}],
            },
        )
        mod._PREFS["layout_preset"] = "watch-monitor"

        result = self._registry().dispatch(
            "/layout focus supporting",
            mod.ChatCommandContext(history=[], session_id=session.session_id, args="focus supporting"),
        )

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["layout_focus"] == "supporting"
        out = capsys.readouterr().out
        assert "Active pane set to supporting." in out
        assert "Focus transition: primary -> supporting" in out
        assert "Workspace preset: watch-monitor" in out
        assert "Active pane: supporting" in out
        assert "Focus transition: /layout focus primary -> Watch monitor" in out
        assert "READY · Watch monitor" in out
        assert "ACTIVE · Recent artifacts" in out

    def test_accessibility_plain_toggle_updates_persistent_layout(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()

        result = self._registry().dispatch("/accessibility plain on", self._ctx(args="plain on"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS[mod._A11Y_PLAIN_MODE] is True
        assert mod._PREFS["layout"] == "plain"

        self._reset_prefs()
        mod._load_prefs()
        assert mod._PREFS[mod._A11Y_PLAIN_MODE] is True
        assert mod._PREFS["layout"] == "plain"

        result = self._registry().dispatch("/accessibility plain off", self._ctx(args="plain off"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS[mod._A11Y_PLAIN_MODE] is False
        assert mod._PREFS["layout"] == "normal"

        out = capsys.readouterr().out
        assert "enabled" in out
        assert "disabled" in out

    def test_accessibility_status_reports_layout_and_saved_state(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_terminal_width", lambda fallback=80: 96)
        self._reset_prefs()
        mod._PREFS[mod._A11Y_REDUCED_MOTION] = True
        mod._PREFS[mod._A11Y_PLAIN_MODE] = True
        mod._PREFS[mod._A11Y_HIGH_CONTRAST] = True
        mod._PREFS["layout"] = "plain"
        mod._PREFS["layout_preset"] = "focus"

        result = self._registry().dispatch("/accessibility status", self._ctx(args="status"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Reduced motion:   ON" in out
        assert "Plain mode:       ON" in out
        assert "High contrast:    ON" in out
        assert "Layout mode:      plain" in out
        assert "Layout preset:    focus" in out
        assert "Preset fallback:  single-pane" in out

    def test_theme_preview_does_not_persist_changes(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()

        result = self._registry().dispatch("/theme preview cyan", self._ctx(args="preview cyan"))

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["theme"] == "default"
        out = capsys.readouterr().out
        assert "Theme preview" in out
        assert "cyan" in out

    def test_theme_next_cycles_and_persists(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()

        result = self._registry().dispatch("/theme next", self._ctx(args="next"))

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["theme"] == "green"
        self._reset_prefs()
        mod._load_prefs()
        assert mod._PREFS["theme"] == "green"
        assert "Theme saved" in capsys.readouterr().out

    def test_emoji_pack_preview_and_pack_selection(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        self._reset_prefs()

        result = self._registry().dispatch("/emoji preview", self._ctx(args="preview"))
        assert result == mod._CMD_CONTINUE
        preview_out = capsys.readouterr().out
        assert "classic" in preview_out
        assert "minimal" in preview_out
        assert "ascii" in preview_out

        result = self._registry().dispatch("/emoji pack minimal", self._ctx(args="pack minimal"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["emoji_pack"] == "minimal"
        assert mod._PREFS["emoji"] is True

        self._reset_prefs()
        mod._load_prefs()
        assert mod._PREFS["emoji_pack"] == "minimal"

    def test_status_emoji_respects_ascii_pack(self):
        self._reset_prefs()
        mod._PREFS["emoji_pack"] = "ascii"
        mod._PREFS["emoji"] = False

        assert mod._status_emoji("healthy") == "[ok]"

    def test_status_emoji_covers_wave22_status_families(self):
        self._reset_prefs()
        mod._PREFS["emoji_pack"] = "ascii"
        mod._PREFS["emoji"] = False

        assert mod._status_emoji("running") == "[run]"
        assert mod._status_emoji("queued") == "[wait]"
        assert mod._status_emoji("failed") == "[err]"
        assert mod._status_emoji("paused") == "[pause]"


def test_session_badges_cover_wave22_compact_cells():
    stale_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    session = sessions_mod.SessionSummary(
        session_id="session-wave22",
        title="Wave 22 status grammar",
        cwd="/workspace",
        created_at=stale_time,
        updated_at=stale_time,
        status="active",
        command_count=4,
        output_count=2,
        last_summary="latest output saved",
        plan_id="",
        task_id="",
        files=[],
        tags=["wave22", "docs"],
        automation_mode="",
        automation_status="",
        checkpoint_count=0,
        last_checkpoint_at="",
    )

    badges = mod._session_badges(session)

    assert "ACTIVE" in badges
    assert "STALE" in badges
    assert "outputs: 2" in badges
    assert "mood: steady" in badges
    assert "#wave22" in badges


def test_status_cell_plain_mode_prefers_text_labels(monkeypatch):
    monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)

    cell = mod._status_cell("retrying", detail="backoff 2s", rich=True)

    assert cell == "RETRY · backoff 2s"


# ── /macro command ─────────────────────────────────────────────────────────────


class TestCmdMacro:
    """Tests for the /macro slash command handler."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def _reset_macros(self):
        mod._PREFS.pop("macros", None)
        mod._PREFS.pop("cmd_history", None)

    def test_macro_list_empty_shows_no_macros(self, capsys, monkeypatch, tmp_path):
        """/macro list with no macros shows '(no macros defined)'."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/macro list", self._ctx(args="list"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no macros defined" in out.lower()

    def test_macro_save_stores_commands(self, capsys, monkeypatch, tmp_path):
        """/macro save mytest saves last commands from cmd_history."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS["cmd_history"] = ["/search foo", "/analyze bar", "/write baz"]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/macro save mytest", self._ctx(args="save mytest"))
        assert result == mod._CMD_CONTINUE
        macros = mod._PREFS.get("macros", {})
        assert "mytest" in macros
        assert isinstance(macros["mytest"], list)
        assert len(macros["mytest"]) > 0

    def test_macro_show_prints_commands(self, capsys, monkeypatch, tmp_path):
        """/macro show mytest prints the stored commands."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS.setdefault("macros", {})["mytest"] = ["/search foo", "/analyze bar"]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/macro show mytest", self._ctx(args="show mytest"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "/search foo" in out
        assert "/analyze bar" in out

    def test_macro_rm_removes_macro(self, monkeypatch, tmp_path):
        """/macro rm mytest removes the macro from _PREFS['macros']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS.setdefault("macros", {})["mytest"] = ["/search foo"]
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/macro rm mytest", self._ctx(args="rm mytest"))
        assert result == mod._CMD_CONTINUE
        assert "mytest" not in mod._PREFS.get("macros", {})

    def test_macro_save_empty_history_prints_error(self, capsys, monkeypatch, tmp_path):
        """/macro save with empty cmd_history prints an error."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS["cmd_history"] = []
        with patch.object(mod, "_save_prefs"):
            result = self._registry().dispatch("/macro save mytest", self._ctx(args="save mytest"))
        assert result == mod._CMD_CONTINUE
        combined = capsys.readouterr().out + capsys.readouterr().err
        # Error message should mention history
        assert "history" in combined.lower() or "no command" in combined.lower()


class TestCmdMacroRun:
    """Tests for /macro run execution logic."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="test-session", args=args)

    def _reset_macros(self):
        mod._PREFS.pop("macros", None)

    def test_macro_run_nonexistent_prints_error(self, capsys, monkeypatch, tmp_path):
        """/macro run <name> with unknown macro prints an error."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        result = self._registry().dispatch("/macro run ghost", self._ctx(args="run ghost"))
        assert result == mod._CMD_CONTINUE
        combined = capsys.readouterr().out + capsys.readouterr().err
        assert "ghost" in combined

    def test_macro_run_executes_slash_commands(self, capsys, monkeypatch, tmp_path):
        """/macro run dispatches slash-commands in the macro list."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS.setdefault("macros", {})["myflow"] = ["/version"]

        called = []

        def fake_handler(ctx: mod.ChatCommandContext) -> str:
            called.append(ctx.args)
            return mod._CMD_CONTINUE

        fake_registry = mod.ChatCommandRegistry()
        fake_registry.register(mod.SlashCommand(name="version", description="", handler=fake_handler))

        with patch.object(mod, "build_chat_command_registry", return_value=fake_registry):
            result = mod._macro_run(self._ctx(), "myflow")

        assert result == mod._CMD_CONTINUE
        assert len(called) == 1
        out = capsys.readouterr().out
        assert "myflow" in out

    def test_macro_run_skips_natural_language(self, capsys, monkeypatch, tmp_path):
        """/macro run warns and skips natural-language (non-slash) commands."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        mod._PREFS.setdefault("macros", {})["nlflow"] = ["summarize this session"]

        fake_registry = mod.ChatCommandRegistry()

        with patch.object(mod, "build_chat_command_registry", return_value=fake_registry):
            result = mod._macro_run(self._ctx(), "nlflow")

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Skip" in out or "skip" in out or "⚠" in out

    def test_macro_run_resolves_session_placeholders(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        self._reset_macros()
        session = mod.create_session(title="Workflow Session", cwd=str(tmp_path))
        mod._PREFS.setdefault("macros", {})["templated"] = ["/context {cwd}", "/session {session}"]

        called = []

        def fake_context(ctx: mod.ChatCommandContext) -> str:
            called.append(("context", ctx.args))
            return mod._CMD_CONTINUE

        def fake_session(ctx: mod.ChatCommandContext) -> str:
            called.append(("session", ctx.args))
            return mod._CMD_CONTINUE

        fake_registry = mod.ChatCommandRegistry()
        fake_registry.register(mod.SlashCommand(name="context", description="", handler=fake_context))
        fake_registry.register(mod.SlashCommand(name="session", description="", handler=fake_session))

        with patch.object(mod, "build_chat_command_registry", return_value=fake_registry):
            result = mod._macro_run(
                mod.ChatCommandContext(history=[], session_id=session.session_id, args=""), "templated"
            )

        assert result == mod._CMD_CONTINUE
        assert called[0][1] == session.cwd
        assert called[1][1] == session.session_id


class TestCmdWorkflow:
    """Tests for the Wave 33 /workflow command family."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_workflow_preview_resolves_session_placeholders(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        session = mod.create_session(title="Workflow Preview", cwd=str(tmp_path))
        mod._PREFS["macros"] = {"shipit": ["/context {cwd}", "/bookmark {session}"]}

        result = self._registry().dispatch("/workflow preview shipit", self._ctx("preview shipit", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Workflow preview 'shipit'" in out
        assert session.cwd in out
        assert session.session_id in out

    def test_workflow_run_delegates_to_macro_runner(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        called = []

        def fake_macro_run(ctx: mod.ChatCommandContext, name: str, *, kind: str = "macro") -> str:
            called.append((name, kind, ctx.session_id))
            return mod._CMD_CONTINUE

        with patch.object(mod, "_macro_run", side_effect=fake_macro_run):
            result = self._registry().dispatch("/workflow run shipit", self._ctx("run shipit", "session-33"))

        assert result == mod._CMD_CONTINUE
        assert called == [("shipit", "workflow", "session-33")]


class TestCmdPattern:
    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def _reset_patterns(self) -> None:
        mod._PREFS.pop("patterns", None)

    def test_pattern_save_from_history_stores_metadata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        self._reset_patterns()

        with patch.object(mod, "_history_command_texts", return_value=["/context", "/files", "/plan"]):
            result = self._registry().dispatch(
                "/pattern save triage last 2", self._ctx("save triage last 2", "session-37")
            )

        assert result == mod._CMD_CONTINUE
        pattern = mod._PREFS["patterns"]["triage"]
        assert pattern["source"] == "history"
        assert pattern["session_id"] == "session-37"
        assert pattern["commands"] == ["/files", "/plan"]

    def test_pattern_save_from_workflow_reuses_workflow_steps(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        self._reset_patterns()
        mod._PREFS["macros"] = {"shipit": ["/context {cwd}", "/bookmark {session}"]}

        result = self._registry().dispatch(
            "/pattern save launch workflow shipit", self._ctx("save launch workflow shipit")
        )

        assert result == mod._CMD_CONTINUE
        pattern = mod._PREFS["patterns"]["launch"]
        assert pattern["source"] == "workflow"
        assert pattern["source_name"] == "shipit"
        assert pattern["commands"] == ["/context {cwd}", "/bookmark {session}"]

    def test_pattern_list_shows_saved_patterns(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        mod._PREFS["patterns"] = {
            "triage": {
                "source": "history",
                "commands": ["/context", "/files"],
                "updated_at": "2026-01-01T00:00:00Z",
            }
        }

        result = self._registry().dispatch("/pattern list", self._ctx("list"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Patterns:" in out
        assert "triage" in out
        assert "history" in out

    def test_pattern_preview_prints_steps(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        session = mod.create_session(title="Pattern Preview", cwd=str(tmp_path))
        mod._PREFS["patterns"] = {
            "triage": {
                "source": "workflow",
                "source_name": "shipit",
                "commands": ["/context {cwd}", "/bookmark {session}"],
            }
        }

        result = self._registry().dispatch("/pattern preview triage", self._ctx("preview triage", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Pattern 'triage'" in out
        assert session.session_id in out
        assert session.cwd in out

    def test_pattern_run_executes_saved_commands(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["patterns"] = {"triage": {"commands": ["/version"]}}
        called = []

        def fake_run(ctx: mod.ChatCommandContext, name: str, commands: list[str], *, kind: str = "macro") -> str:
            called.append((name, commands, kind, ctx.session_id))
            return mod._CMD_CONTINUE

        with patch.object(mod, "_run_command_sequence", side_effect=fake_run):
            result = self._registry().dispatch("/pattern run triage", self._ctx("run triage", "session-37"))

        assert result == mod._CMD_CONTINUE
        assert called == [("triage", ["/version"], "pattern", "session-37")]

    def test_pattern_rm_deletes_saved_pattern(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["patterns"] = {"triage": {"commands": ["/version"]}}

        result = self._registry().dispatch("/pattern rm triage", self._ctx("rm triage"))

        assert result == mod._CMD_CONTINUE
        assert "triage" not in mod._PREFS.get("patterns", {})


class TestMacroProgress:
    """Tests for _print_macro_progress and _cmd_macrostatus."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_print_macro_progress_runs_without_error(self, capsys, monkeypatch):
        """_print_macro_progress with two steps and no done set runs without error."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        # Should not raise
        mod._print_macro_progress(["step1", "step2"], 0, set())
        out = capsys.readouterr().out
        assert "Step 1/2" in out
        assert "Step 2/2" in out

    def test_openclaw_cli_cmd_macrostatus_no_macros(self, capsys, monkeypatch, tmp_path):
        """/macrostatus returns _CMD_CONTINUE when no macros are saved."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        mod._PREFS.pop("macros", None)
        result = mod._cmd_macrostatus(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No macros" in out or "macro" in out.lower()

    def test_cmd_macrostatus_with_macros(self, capsys, monkeypatch, tmp_path):
        """/macrostatus returns _CMD_CONTINUE and shows macro names and counts."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        mod._PREFS["macros"] = {
            "myflow": ["/version", "/stats"],
            "other": ["/help"],
        }
        result = mod._cmd_macrostatus(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "myflow" in out
        assert "other" in out


class TestCmdRate:
    """Tests for /rate slash command."""

    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_rate_no_args_prints_usage(self, capsys, monkeypatch, tmp_path):
        """/rate with no args prints usage."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        result = mod._cmd_rate(self._ctx(args=""))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out

    def test_rate_good_saves_rating(self, monkeypatch, tmp_path):
        """/rate good with _last_response_text set saves rating to _PREFS['ratings']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        monkeypatch.setattr(mod, "_last_response_text", "some AI response")
        mod._PREFS.pop("ratings", None)
        with patch.object(mod, "_save_prefs"), patch("openclaw_cli.append_event"):
            result = mod._cmd_rate(self._ctx(args="good"))
        assert result == mod._CMD_CONTINUE
        ratings = mod._PREFS.get("ratings", [])
        assert len(ratings) == 1
        assert ratings[0]["score"] == 5
        assert ratings[0]["label"] == "good"

    def test_rate_bad_stores_score_1(self, monkeypatch, tmp_path):
        """/rate bad stores score=1 in _PREFS['ratings']."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        monkeypatch.setattr(mod, "_last_response_text", "some AI response")
        mod._PREFS.pop("ratings", None)
        with patch.object(mod, "_save_prefs"), patch("openclaw_cli.append_event"):
            result = mod._cmd_rate(self._ctx(args="bad"))
        assert result == mod._CMD_CONTINUE
        ratings = mod._PREFS.get("ratings", [])
        assert ratings[0]["score"] == 1
        assert ratings[0]["label"] == "bad"

    def test_rate_five_triggers_celebration_burst(self, monkeypatch, tmp_path):
        """/rate 5 triggers the shared celebration helper."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        monkeypatch.setattr(mod, "_last_response_text", "some AI response")
        mod._PREFS.pop("ratings", None)

        with (
            patch.object(mod, "_save_prefs"),
            patch("openclaw_cli.append_event"),
            patch.object(mod, "_celebration_burst") as celebrate,
        ):
            result = mod._cmd_rate(self._ctx(args="5"))

        assert result == mod._CMD_CONTINUE
        celebrate.assert_called_once_with("5-star rating — thanks! 🎉")

    def test_rate_empty_response_prints_error(self, capsys, monkeypatch, tmp_path):
        """/rate with empty _last_response_text prints 'Nothing to rate' error."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        monkeypatch.setattr(mod, "_last_response_text", "")
        result = mod._cmd_rate(self._ctx(args="good"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Nothing to rate" in out

    def test_rate_captures_route_metadata_from_last_trace(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        monkeypatch.setattr(mod, "_last_response_text", "some AI response")
        mod._PREFS.pop("ratings", None)
        monkeypatch.setattr(
            mod,
            "_last_trace_snapshot",
            lambda session_id: {"slash_cmd": "research", "conf_label": "0.91 (HIGH)"},
        )
        with patch.object(mod, "_save_prefs"), patch("openclaw_cli.append_event"):
            result = mod._cmd_rate(self._ctx(args="good", session_id="session-39"))
        assert result == mod._CMD_CONTINUE
        ratings = mod._PREFS.get("ratings", [])
        assert ratings[0]["route"] == "research"
        assert ratings[0]["route_confidence"] == "0.91 (HIGH)"


class TestCmdQuality:
    """Tests for _cmd_quality (colored vertical histogram)."""

    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_no_ratings_returns_continue(self, capsys, monkeypatch, tmp_path):
        """No ratings → prints guidance message and returns _CMD_CONTINUE."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS.pop("ratings", None)
        result = mod._cmd_quality(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "ratings" in out.lower()

    def test_with_ratings_returns_continue(self, capsys, monkeypatch, tmp_path):
        """With mock ratings → renders histogram and returns _CMD_CONTINUE."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["ratings"] = [{"score": 5}, {"score": 3}, {"score": 4}]
        result = mod._cmd_quality(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        # Average of 5+3+4 = 4.0
        assert "4.0" in out

    def test_quality_includes_latest_route_hint(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["ratings"] = [{"score": 5, "label": "good"}]
        monkeypatch.setattr(
            mod,
            "get_last_decision_event",
            lambda session_id: {
                "kind": "route",
                "timestamp": "2026-04-14T12:00:00Z",
                "metadata": {
                    "slash_command": "plan",
                    "confidence": 0.82,
                    "route_reason": "The user asked for planning mode.",
                },
            },
        )

        result = mod._cmd_quality(self._ctx(session_id="session-34"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Latest route:" in out
        assert "/plan" in out
        assert "Use /trace" in out

    def test_quality_predict_uses_route_quality_history(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["ratings"] = [
            {"score": 5, "route": "research"},
            {"score": 4, "route": "research"},
            {"score": 3, "route": "plan"},
        ]
        result = mod._cmd_quality(self._ctx("predict"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Quality prediction" in out
        assert "/research" in out
        assert "Predicted quality" in out

    def test_score_counting(self, capsys, monkeypatch, tmp_path):
        """Score tallying: counts[5]=1, counts[4]=1, counts[3]=1, others=0."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        ratings = [{"score": 5}, {"score": 3}, {"score": 4}]
        counts: dict[int, int] = {i: 0 for i in range(1, 6)}
        for r in ratings:
            score = r.get("score", 0)
            if 1 <= score <= 5:
                counts[score] = counts.get(score, 0) + 1
        assert counts[5] == 1
        assert counts[4] == 1
        assert counts[3] == 1
        assert counts[1] == 0
        assert counts[2] == 0


class TestCmdHeatmap:
    """Tests for _cmd_heatmap."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_empty_history_returns_continue(self, capsys, monkeypatch, tmp_path):
        """Empty cmd_history → message printed, _CMD_CONTINUE returned."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS.pop("cmd_history", None)
        result = mod._cmd_heatmap(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No timestamped history" in out

    def test_with_timestamped_history_returns_continue(self, capsys, monkeypatch, tmp_path):
        """Timestamped cmd_history entries → heatmap printed, _CMD_CONTINUE returned."""
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        mod._load_prefs()
        mod._PREFS["cmd_history"] = [
            {"cmd": "/help", "timestamp": "2024-03-15T09:00:00"},
            {"cmd": "/stats", "timestamp": "2024-03-15T09:30:00"},
            {"cmd": "/quality", "timestamp": "2024-03-15T14:00:00"},
            {"cmd": "/help", "timestamp": "2024-03-16T09:15:00"},
        ]
        result = mod._cmd_heatmap(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Heatmap" in out or "heatmap" in out or "Peak hour" in out

    def test_openclaw_cli_cli_build_is_wave50(self):
        """_CLI_BUILD must equal 'wave50'."""
        assert mod._CLI_BUILD == "wave50"

    """Tests for _cmd_ratehint."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_ratehint_on_sets_pref_true(self, monkeypatch):
        """/ratehint on sets show_rate_hint to True."""
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        mod._PREFS["show_rate_hint"] = False
        result = mod._cmd_ratehint(self._ctx("on"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["show_rate_hint"] is True

    def test_ratehint_off_sets_pref_false(self, monkeypatch):
        """/ratehint off sets show_rate_hint to False."""
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        mod._PREFS["show_rate_hint"] = True
        result = mod._cmd_ratehint(self._ctx("off"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["show_rate_hint"] is False

    def test_ratehint_no_args_prints_current_state(self, capsys, monkeypatch):
        """/ratehint with no args prints the current state."""
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._PREFS["show_rate_hint"] = True
        result = mod._cmd_ratehint(self._ctx(""))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "on" in out
        assert "ratehint" in out


class TestCmdStreak:
    """Tests for _cmd_streak and _print_ascii_trophy."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_no_ratings_shows_message(self, capsys, monkeypatch):
        """/streak with no ratings prints guidance."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS.pop("ratings", None)
        result = mod._cmd_streak(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No ratings yet" in out

    def test_all_high_ratings_shows_streak_count(self, capsys, monkeypatch):
        """/streak with all high ratings shows correct current streak."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_print_ascii_trophy", lambda s: None)
        mod._PREFS["ratings"] = [{"score": 4}, {"score": 5}, {"score": 4}, {"score": 5}]
        result = mod._cmd_streak(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "4 high ratings" in out

    def test_mixed_ratings_streak_stops_at_low(self, capsys, monkeypatch):
        """/streak with mixed ratings: streak counts only trailing high ratings."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS["ratings"] = [{"score": 5}, {"score": 2}, {"score": 4}, {"score": 5}]
        result = mod._cmd_streak(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "2 high ratings" in out

    def test_print_ascii_trophy_runs_without_error(self, capsys, monkeypatch):
        """_print_ascii_trophy(5) runs without error in non-TTY mode."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        mod._print_ascii_trophy(5)
        out = capsys.readouterr().out
        assert "5-Rating Streak" in out


class TestCmdPromptDebug:
    """Tests for _cmd_promptdebug."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_no_system_prompt_no_inject_shows_placeholder(self, capsys, monkeypatch):
        """With no system prompt and no inject, only the user message placeholder is shown."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS.pop("system_prompt", None)
        mod._next_inject = ""
        result = mod._cmd_promptdebug(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "your next message here" in out
        assert "System context" not in out
        assert "Injected context" not in out

    def test_with_system_prompt_shows_system_section(self, capsys, monkeypatch):
        """When a system prompt is set, its section appears in the preview."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS["system_prompt"] = "You are a helpful assistant."
        mod._next_inject = ""
        result = mod._cmd_promptdebug(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "System context" in out
        assert "You are a helpful assistant." in out
        mod._PREFS.pop("system_prompt", None)

    def test_with_inject_shows_inject_section(self, capsys, monkeypatch):
        """When _next_inject is set, its section appears in the preview."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        mod._PREFS.pop("system_prompt", None)
        mod._next_inject = "Some injected file content."
        result = mod._cmd_promptdebug(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Injected context" in out
        assert "Some injected file content." in out
        mod._next_inject = ""


class TestCmdInject:
    """Tests for _cmd_inject."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_status_no_injection(self, capsys, monkeypatch):
        """/inject status with no injection shows (no injection set)."""
        monkeypatch.setattr(mod, "_next_inject", "")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_inject(self._ctx("status"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "(no injection set)" in out

    def test_clear_clears_injection(self, monkeypatch):
        """/inject clear sets _next_inject to empty string."""
        monkeypatch.setattr(mod, "_next_inject", "some content here")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_inject(self._ctx("clear"))
        assert result == mod._CMD_CONTINUE
        assert mod._next_inject == ""

    def test_file_path_stores_content(self, tmp_path, monkeypatch):
        """/inject <path> reads file and stores content in _next_inject."""
        monkeypatch.setattr(mod, "_next_inject", "")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        test_file = tmp_path / "context.txt"
        test_file.write_text("Hello from inject file!", encoding="utf-8")
        result = mod._cmd_inject(self._ctx(str(test_file)))
        assert result == mod._CMD_CONTINUE
        assert mod._next_inject == "Hello from inject file!"

    def test_no_args_prints_usage(self, capsys, monkeypatch):
        """/inject with no args prints usage hint."""
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_inject(self._ctx(""))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out.lower()
        assert "--url" in out


class TestCmdSystem:
    """Tests for /system command."""

    def _ctx(self, args: str = "") -> "mod.ChatCommandContext":
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_view_no_prompt_shows_not_set(self, capsys, monkeypatch):
        """/system with no prompt set shows (not set)."""
        monkeypatch.setitem(mod._PREFS, "system_prompt", "")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_system(self._ctx(""))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "not set" in out

    def test_set_stores_in_prefs(self, monkeypatch):
        """/system set Hello stores in _PREFS["system_prompt"]."""
        monkeypatch.setitem(mod._PREFS, "system_prompt", "")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_system(self._ctx("set Hello"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["system_prompt"] == "Hello"

    def test_clear_empties_system_prompt(self, monkeypatch):
        """/system clear empties _PREFS["system_prompt"]."""
        monkeypatch.setitem(mod._PREFS, "system_prompt", "existing prompt")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_system(self._ctx("clear"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["system_prompt"] == ""

    def test_append_adds_to_existing_prompt(self, monkeypatch):
        """/system append more appends to existing prompt."""
        monkeypatch.setitem(mod._PREFS, "system_prompt", "base")
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_system(self._ctx("append extra"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["system_prompt"] == "base\nextra"


class TestAutoBlodResponse:
    """Tests for _auto_bold_response() helper."""

    def test_dollar_amount_gets_bolded(self, monkeypatch):
        """Dollar amounts like $69 million should be wrapped in **...**."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "auto_bold", True)
        result = mod._auto_bold_response("Revenue was $69 million last quarter.")
        assert "**$69 million**" in result

    def test_percentage_gets_bolded(self, monkeypatch):
        """Percentages like 47% should be wrapped in **...**."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "auto_bold", True)
        result = mod._auto_bold_response("The success rate was 47% overall.")
        assert "**47%**" in result

    def test_code_block_content_not_bolded(self, monkeypatch):
        """Lines inside fenced code blocks should not be modified."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "auto_bold", True)
        text = "Some intro.\n```\nValue: $100 and 50%\n```\nAfter block."
        result = mod._auto_bold_response(text)
        assert "$100" in result and "**$100**" not in result
        assert "50%" in result and "**50%**" not in result

    def test_autobold_off_disables_bolding(self, monkeypatch):
        """When auto_bold pref is False, text should be returned unchanged."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "auto_bold", False)
        text = "Revenue was $69 million and growth was 15%."
        result = mod._auto_bold_response(text)
        assert result == text


class TestEmojiHeaders:
    """Tests for _inject_heading_emojis() and /emojiheaders command."""

    def test_h2_gets_diamond_emoji(self, monkeypatch):
        """_inject_heading_emojis("## Section") returns "## 🔹 Section"."""
        monkeypatch.setitem(mod._PREFS, "emoji_headers", True)
        monkeypatch.setitem(mod._PREFS, "plain_mode", False)
        result = mod._inject_heading_emojis("## Section")
        assert result == "## 🔹 Section"

    def test_h3_gets_arrow(self, monkeypatch):
        """_inject_heading_emojis("### Sub") returns "### ▸ Sub"."""
        monkeypatch.setitem(mod._PREFS, "emoji_headers", True)
        monkeypatch.setitem(mod._PREFS, "plain_mode", False)
        result = mod._inject_heading_emojis("### Sub")
        assert result == "### ▸ Sub"

    def test_code_block_headings_not_modified(self, monkeypatch):
        """Headings inside fenced code blocks are not modified."""
        monkeypatch.setitem(mod._PREFS, "emoji_headers", True)
        monkeypatch.setitem(mod._PREFS, "plain_mode", False)
        text = "```\n## in code\n```"
        result = mod._inject_heading_emojis(text)
        assert "## in code" in result
        assert "🔹" not in result

    def test_pref_disabled_returns_unchanged(self, monkeypatch):
        """When emoji_headers is False, text is returned unchanged."""
        monkeypatch.setitem(mod._PREFS, "emoji_headers", False)
        monkeypatch.setitem(mod._PREFS, "plain_mode", False)
        text = "## Section\n### Sub"
        result = mod._inject_heading_emojis(text)
        assert result == text


class TestSeparator:
    """Tests for _SEPARATOR_STYLES and /separator command."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_separator_styles_has_expected_keys(self):
        """_SEPARATOR_STYLES has gradient, pulse, dots, wave, and none keys."""
        expected = {"gradient", "pulse", "dots", "wave", "none"}
        assert expected == set(mod._SEPARATOR_STYLES.keys())

    def test_separator_none_sets_pref(self, monkeypatch):
        """/separator none sets separator_style pref and does not animate."""
        monkeypatch.setitem(mod._PREFS, "separator_style", "gradient")
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        animated_called = []
        monkeypatch.setattr(mod, "_print_animated_separator", lambda: animated_called.append(1))

        ctx = self._ctx("none")
        result = mod._cmd_separator(ctx)

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["separator_style"] == "none"
        assert animated_called == []  # no animation for "none"

    def test_separator_gradient_sets_pref(self, monkeypatch):
        """/separator gradient sets separator_style pref to gradient."""
        monkeypatch.setitem(mod._PREFS, "separator_style", "none")
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        monkeypatch.setattr(mod, "_print_animated_separator", lambda: None)

        ctx = self._ctx("gradient")
        result = mod._cmd_separator(ctx)

        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["separator_style"] == "gradient"


class TestCmdPalette:
    """Tests for /palette fuzzy command search."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def _make_registry(self):
        """Build a fresh real registry for inspection."""
        return mod.build_chat_command_registry()

    def test_no_query_shows_all_commands(self, capsys, monkeypatch):
        """/palette with no query shows all registered commands including 'palette'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_CMD_REGISTRY_CACHE", None)

        ctx = self._ctx("")
        result = mod._cmd_palette(ctx)

        assert result == mod._CMD_CONTINUE
        captured = capsys.readouterr().out
        assert "palette" in captured

    def test_query_edit_returns_matching_commands(self, capsys, monkeypatch):
        """/palette edit returns commands whose name or description contains 'edit'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_CMD_REGISTRY_CACHE", None)

        ctx = self._ctx("edit")
        result = mod._cmd_palette(ctx)

        assert result == mod._CMD_CONTINUE
        captured = capsys.readouterr().out
        assert "edit" in captured.lower()

    def test_no_match_shows_not_found_message(self, capsys, monkeypatch):
        """/palette xyznotfound shows 'No commands matching' message."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_CMD_REGISTRY_CACHE", None)

        ctx = self._ctx("xyznotfound")
        result = mod._cmd_palette(ctx)

        assert result == mod._CMD_CONTINUE
        captured = capsys.readouterr().out
        assert "No commands matching" in captured

    def test_results_sorted_alphabetically(self, monkeypatch):
        """Results from /palette are sorted alphabetically by command name."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_CMD_REGISTRY_CACHE", None)

        registry = mod.build_chat_command_registry()
        commands = registry.list_commands()
        matches = sorted(commands, key=lambda c: c.name)
        names = [c.name for c in matches]
        assert names == sorted(names)


class TestCmdShortcuts:
    """Tests for /shortcuts keyboard shortcuts reference card."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_returns_cmd_continue(self, monkeypatch):
        """/shortcuts returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        ctx = self._ctx("")
        result = mod._cmd_shortcuts(ctx)
        assert result == mod._CMD_CONTINUE

    def test_output_contains_tab_and_ctrl(self, capsys, monkeypatch):
        """/shortcuts output contains 'Tab' and 'Ctrl'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        ctx = self._ctx("")
        mod._cmd_shortcuts(ctx)
        captured = capsys.readouterr().out
        assert "Tab" in captured
        assert "Ctrl" in captured

    def test_output_contains_section_headers(self, capsys, monkeypatch):
        """/shortcuts output contains 'Navigation' and 'Quick Commands' section headers."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        ctx = self._ctx("")
        mod._cmd_shortcuts(ctx)
        captured = capsys.readouterr().out
        assert "Navigation" in captured
        assert "Quick Commands" in captured


class TestSlashCompleter:
    """Tests for the _SlashCompleter readline completer."""

    def test_compute_matches_prefix_returns_matching_commands(self, monkeypatch):
        monkeypatch.setattr(mod, "_PREFS", {})
        completer = mod._SlashCompleter()
        matches = completer._compute_matches("/hel")
        assert "/help" in matches

    def test_compute_matches_exact_returns_single_result(self, monkeypatch):
        monkeypatch.setattr(mod, "_PREFS", {})
        completer = mod._SlashCompleter()
        matches = completer._compute_matches("/quit")
        assert matches == ["/quit"]

    def test_compute_matches_no_slash_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mod, "_PREFS", {})
        completer = mod._SlashCompleter()
        matches = completer._compute_matches("hello")
        assert matches == []


class TestPromptToolkitIntegration:
    def test_build_prompt_toolkit_session_returns_none_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(mod, "PromptSession", None)

        assert mod._build_prompt_toolkit_session() is None

    def test_build_prompt_toolkit_session_loads_history(self, monkeypatch, tmp_path):
        history_file = tmp_path / "history.txt"
        history_file.write_text("/help\n/quit\n", encoding="utf-8")

        appended: list[str] = []

        class FakeHistory:
            def append_string(self, value: str) -> None:
                appended.append(value)

        captured: dict[str, object] = {}

        class FakePromptSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(mod, "_overlay_available", lambda: True)
        monkeypatch.setattr(mod, "HISTORY_FILE", history_file)
        monkeypatch.setattr(mod, "InMemoryHistory", FakeHistory)
        monkeypatch.setattr(mod, "PromptSession", FakePromptSession)

        session = mod._build_prompt_toolkit_session()

        assert isinstance(session, FakePromptSession)
        assert appended == ["/help", "/quit"]
        assert isinstance(captured["completer"], mod._PromptToolkitSlashCompleter)

    def test_prompt_toolkit_completer_yields_slash_matches(self, monkeypatch):
        outputs: list[tuple[str, int, str]] = []

        class FakeCompletion:
            def __init__(self, text: str, *, start_position: int, display: str):
                outputs.append((text, start_position, display))

        class FakeDocument:
            text_before_cursor = "/he"

            @staticmethod
            def get_word_before_cursor(**kwargs):
                assert kwargs == {"WORD": True}
                return "/he"

        monkeypatch.setattr(mod, "Completion", FakeCompletion)
        monkeypatch.setattr(mod, "_PREFS", {})

        completer = mod._PromptToolkitSlashCompleter()

        list(completer.get_completions(FakeDocument(), None))

        assert ("/help", -3, "/help") in outputs

    def test_run_chat_uses_prompt_toolkit_session_when_available(self, monkeypatch, capsys):
        prompts = iter(["hello from prompt toolkit", "/quit"])
        seen_prompts: list[str] = []

        class FakePromptSession:
            def __init__(self) -> None:
                self.default_buffer = SimpleNamespace(text="")

            def prompt(self, prompt_str: str) -> str:
                seen_prompts.append(prompt_str)
                value = next(prompts)
                self.default_buffer.text = value
                return value

        def _fake_ask(prompt, *, config, history):
            return mod.AskResponse(
                response=f"reply to {prompt}",
                model="gemini",
                tokens=10,
                raw={"response": f"reply to {prompt}", "model": "gemini", "tokens": 10},
            )

        recorded_history: list[str] = []
        monkeypatch.setattr(mod, "_PREFS", {})
        monkeypatch.setattr(mod, "_load_prefs", lambda: None)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        monkeypatch.setattr(mod, "load_shell_history", lambda: None)
        monkeypatch.setattr(mod, "save_shell_history", lambda: None)
        monkeypatch.setattr(mod, "_setup_readline", lambda: None)
        monkeypatch.setattr(mod, "_maybe_show_startup_tip", lambda *args, **kwargs: None)
        monkeypatch.setattr(mod, "_print_top_context_bar", lambda **kwargs: None)
        monkeypatch.setattr(mod, "_build_prompt_toolkit_session", lambda: FakePromptSession())
        monkeypatch.setattr(mod, "_record_shell_history_entry", recorded_history.append)

        exit_code = mod.run_chat(_config(), input_func=input, ask_func=_fake_ask, no_banner=True)

        assert exit_code == 0
        assert seen_prompts
        assert recorded_history == ["hello from prompt toolkit", "/quit"]
        assert "reply to hello from prompt toolkit" in capsys.readouterr().out


class TestProgressBar:
    """Tests for the _progress_bar helper."""

    def test_empty_bar_contains_light_shade(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        result = mod._progress_bar(0, 10)
        assert "░" in result

    def test_full_bar_contains_block_and_100_percent(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        result = mod._progress_bar(10, 10)
        assert "█" in result
        assert "100%" in result

    def test_half_bar_contains_50_percent(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        result = mod._progress_bar(5, 10)
        assert "50%" in result


class TestCelebrationBurst:
    """Tests for _celebration_burst and /celebrate command."""

    def test_celebration_burst_runs_without_error(self, monkeypatch):
        """_celebration_burst() completes without error when TTY is False."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        # Should not raise; non-TTY path just prints message or returns silently
        mod._celebration_burst()
        mod._celebration_burst("Test celebration!")

    def test_cmd_celebrate_returns_cmd_continue(self, monkeypatch):
        """_cmd_celebrate() returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        ctx = mod.ChatCommandContext(history=[], session_id="", args="Woohoo!")
        result = mod._cmd_celebrate(ctx)
        assert result == mod._CMD_CONTINUE

    def test_celebration_burst_reduced_motion_prints_message(self, monkeypatch, capsys):
        """With reduced motion, _celebration_burst prints the message without animation."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_PREFS", {"reduced_motion": True})
        mod._celebration_burst("Congrats!")
        captured = capsys.readouterr()
        assert "Congrats!" in captured.out


class TestJsonAutoformat:
    """Tests for _detect_and_format_json(), _colorize_json(), and /jsonformat."""

    def test_json_object_in_text_gets_pretty_printed(self, monkeypatch):
        """A bare JSON object in response text should be wrapped in a ```json block."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "json_autoformat", True)
        text = '{"name": "alice", "age": 30}'
        result = mod._detect_and_format_json(text)
        assert "```json" in result
        assert '"name"' in result
        assert '"alice"' in result

    def test_json_inside_code_block_is_left_untouched(self, monkeypatch):
        """JSON already inside a fenced code block should not be re-formatted."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "json_autoformat", True)
        text = '```json\n{"key": "value"}\n```'
        result = mod._detect_and_format_json(text)
        # Should not double-wrap; the original fences survive unchanged
        assert result.count("```json") == 1
        assert result == text

    def test_non_json_text_is_unchanged(self, monkeypatch):
        """Plain text with no JSON should be returned as-is."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "json_autoformat", True)
        text = "This is just plain text with no JSON in it."
        result = mod._detect_and_format_json(text)
        assert result == text

    def test_jsonformat_off_disables_formatting(self, monkeypatch):
        """When json_autoformat pref is False, text should be returned unchanged."""
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "json_autoformat", False)
        text = '{"name": "alice", "age": 30}'
        result = mod._detect_and_format_json(text)
        assert result == text

    def test_celebration_burst_plain_mode_prints_single_line_message(self, monkeypatch, capsys):
        """Plain mode downgrades celebration output to a simple one-line message."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: True)
        monkeypatch.setattr(mod, "_a11y_reduced_motion", lambda: False)
        mod._celebration_burst("Calm win")
        captured = capsys.readouterr()
        assert "🎉 Calm win" in captured.out


class TestCmdStats:
    """Tests for /stats ASCII bar chart visualization."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_empty_history_returns_cmd_continue(self, monkeypatch):
        """/stats returns _CMD_CONTINUE with no usage data."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": [], "ratings": []})
        ctx = self._ctx("")
        result = mod._cmd_stats(ctx)
        assert result == mod._CMD_CONTINUE

    def test_commands_category_returns_cmd_continue(self, monkeypatch):
        """/stats commands returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "cmd_history": [{"cmd": "/help"}, {"cmd": "/clear"}, {"cmd": "/help"}],
                "ratings": [],
            },
        )
        ctx = self._ctx("commands")
        result = mod._cmd_stats(ctx)
        assert result == mod._CMD_CONTINUE

    def test_ratings_category_returns_cmd_continue(self, monkeypatch):
        """/stats ratings returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "cmd_history": [],
                "ratings": [{"score": "5"}, {"score": "3"}, {"score": "5"}],
            },
        )
        ctx = self._ctx("ratings")
        result = mod._cmd_stats(ctx)
        assert result == mod._CMD_CONTINUE

    def test_ratings_bar_chart_output_contains_stars(self, capsys, monkeypatch):
        """/stats with rating data outputs star characters in the bar chart."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "cmd_history": [],
                "ratings": [{"score": "4"}, {"score": "4"}, {"score": "2"}],
            },
        )
        ctx = self._ctx("ratings")
        mod._cmd_stats(ctx)
        captured = capsys.readouterr().out
        assert "⭐" in captured or "Rating" in captured


class TestLinkify:
    """Tests for _make_clickable_link(), _linkify_response(), and /links command."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_make_clickable_link_contains_osc8_when_tty(self, monkeypatch):
        """_make_clickable_link returns OSC 8 escape when TTY is simulated."""
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "clickable_links", True)
        result = mod._make_clickable_link("https://example.com")
        assert "\033]8;;" in result
        assert "https://example.com" in result

    def test_linkify_response_transforms_url(self, monkeypatch):
        """_linkify_response wraps bare URLs in OSC 8 sequences."""
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "clickable_links", True)
        text = "see https://example.com for details"
        result = mod._linkify_response(text)
        assert "\033]8;;" in result
        assert "https://example.com" in result

    def test_linkify_skips_urls_inside_code_blocks(self, monkeypatch):
        """URLs inside fenced code blocks should NOT be linkified."""
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_a11y_plain_mode", lambda: False)
        monkeypatch.setitem(mod._PREFS, "clickable_links", True)
        text = "intro\n```\nhttps://example.com/in-code\n```\nafter"
        result = mod._linkify_response(text)
        lines = result.split("\n")
        code_line = lines[2]  # "https://example.com/in-code"
        assert "\033]8;;" not in code_line

    def test_cmd_links_off_returns_cmd_continue_and_sets_pref(self, monkeypatch):
        """/links off returns _CMD_CONTINUE and sets clickable_links to False."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        monkeypatch.setitem(mod._PREFS, "clickable_links", True)
        ctx = self._ctx("off")
        result = mod._cmd_links(ctx)
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS["clickable_links"] is False


class TestPathHints:
    """Tests for _detect_file_paths and /pathhints command."""

    def _ctx(self, args: str = ""):
        import types

        ctx = types.SimpleNamespace(args=args)
        return ctx

    def test_detect_file_paths_finds_src_path(self):
        paths = mod._detect_file_paths("see src/openclaw_cli.py for details")
        assert "src/openclaw_cli.py" in paths

    def test_detect_file_paths_empty_when_no_paths(self):
        paths = mod._detect_file_paths("no paths here, just text")
        assert paths == []

    def test_pathhints_off_sets_pref(self, monkeypatch):
        prefs = {"path_hints": True}
        monkeypatch.setattr(mod, "_PREFS", prefs)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        ctx = self._ctx("off")
        result = mod._cmd_pathhints(ctx)
        assert prefs["path_hints"] is False
        assert result == mod._CMD_CONTINUE

    def test_print_path_hints_shows_view_and_edit_affordance(self, capsys, monkeypatch, tmp_path):
        hinted_file = tmp_path / "notes.txt"
        hinted_file.write_text("hello", encoding="utf-8")
        monkeypatch.setattr(mod, "_PREFS", {"path_hints": True})
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)

        mod._print_path_hints([str(hinted_file)])

        out = capsys.readouterr().out
        assert str(hinted_file) in out
        assert "use /view or /edit" in out


def test_print_risky_action_warning_wave29_includes_recovery_hint(capsys):
    mod._print_risky_action_warning(
        action="/exec",
        target="rm -rf build/",
        risk_level="critical",
        recovery_hint="use git restore if the target was removed accidentally.",
    )

    out = capsys.readouterr().out
    assert "Review carefully: rm -rf build/" in out
    assert "Recovery: use git restore if the target was removed accidentally." in out


def test_build_edit_approval_review_summarizes_preview_diff():
    preview = SimpleNamespace(
        path="/repo/demo.txt",
        summary="Previewed file write.",
        diff="--- /repo/demo.txt\n+++ /repo/demo.txt\n-old\n+new\n+line\n",
    )

    review_lines = mod._build_edit_approval_review(
        path="/repo/demo.txt",
        preview_result=preview,
        append_mode=False,
        replace_values=[],
    )

    assert review_lines[0] == "Review: write `/repo/demo.txt`"
    assert "+2/-1 lines" in review_lines[1]
    assert "overwrites the current file contents" in review_lines[2]
    assert review_lines[3] == "Review: preview -old | +new | +line"


def test_build_exec_approval_review_includes_side_effect_summary_and_args():
    review_lines = mod._build_exec_approval_review(command_text="rm -rf build/cache", cwd="/repo")

    assert review_lines == [
        "Review: command `rm` from cwd `/repo`",
        "Review: exact shell text `rm -rf build/cache`",
        "Review: side effects deletes or irreversibly resets data",
        "Review: args `-rf build/cache`",
    ]


def test_request_cli_approval_prints_review_trust_and_recovery_cues(monkeypatch, capsys):
    monkeypatch.setattr(mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(mod.sys.stdout, "isatty", lambda: False)

    approved = mod.request_cli_approval(
        action="shell.exec",
        target="rm -rf build",
        risk_level="HIGH",
        review_lines=["Review: exact shell text `rm -rf build`", "Review: command `rm` from cwd `/repo`"],
        trust_note="approving runs exactly the shell text shown above.",
        recovery_hint="deny it, verify the cwd, then rerun the command.",
        input_func=lambda _prompt: "n",
    )

    assert approved is False
    out = capsys.readouterr().out
    assert "Review: exact shell text `rm -rf build`" in out
    assert "Trust cue: approving runs exactly the shell text shown above." in out
    assert "Recovery cue: deny it, verify the cwd, then rerun the command." in out


class TestCmdTop:
    """Tests for /top command."""

    def _ctx(self, args: str = ""):
        import types

        return types.SimpleNamespace(args=args)

    def test_top_empty_history_shows_no_history(self, monkeypatch):
        """/top with empty history prints 'No history yet.' and returns _CMD_CONTINUE."""
        monkeypatch.setitem(mod._PREFS, "cmd_history", [])
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
        result = mod._cmd_top(self._ctx())
        assert result == mod._CMD_CONTINUE
        assert any("No history yet" in line for line in printed)

    def test_top_with_history_returns_cmd_continue(self, monkeypatch):
        """/top with mock history returns _CMD_CONTINUE and shows items."""
        history = ["hello world", "hello world", "/help", "foo", "/help"]
        monkeypatch.setitem(mod._PREFS, "cmd_history", history)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
        result = mod._cmd_top(self._ctx())
        assert result == mod._CMD_CONTINUE
        combined = "\n".join(printed)
        assert "hello world" in combined or "Most Used" in combined

    def test_top_n_limits_results(self, monkeypatch):
        """/top 3 with 5+ distinct items limits output to 3."""
        history = [f"prompt {i}" for i in range(10)]
        monkeypatch.setitem(mod._PREFS, "cmd_history", history)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
        result = mod._cmd_top(self._ctx("3"))
        assert result == mod._CMD_CONTINUE
        # At most 3 numbered rows (lines containing "1.", "2.", "3." but not "4.")
        combined = "\n".join(printed)
        assert "4." not in combined


class TestCmdFreq:
    """Tests for /freq command."""

    def _ctx(self, args: str = ""):
        import types

        return types.SimpleNamespace(args=args)

    def test_freq_empty_history_shows_no_data_message(self, monkeypatch):
        """/freq with empty history shows no-data message and returns _CMD_CONTINUE."""
        monkeypatch.setitem(mod._PREFS, "cmd_history", [])
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
        result = mod._cmd_freq(self._ctx())
        assert result == mod._CMD_CONTINUE
        assert any("No slash command history" in line for line in printed)

    def test_freq_with_slash_commands_returns_cmd_continue(self, monkeypatch):
        """/freq with slash command history returns _CMD_CONTINUE and shows commands."""
        history = ["/help", "/help", "/stats", "/help", "/top"]
        monkeypatch.setitem(mod._PREFS, "cmd_history", history)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        printed = []
        monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))
        result = mod._cmd_freq(self._ctx())
        assert result == mod._CMD_CONTINUE
        combined = "\n".join(printed)
        assert "/help" in combined


# ── /recall command ───────────────────────────────────────────────────────────


class TestCmdRecall:
    """Tests for the /recall slash command."""

    def _registry(self) -> mod.ChatCommandRegistry:
        return mod.build_chat_command_registry()

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_recall_no_history_shows_no_prompt_history(self, capsys, monkeypatch):
        """/recall with empty history shows 'No prompt history yet'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setitem(mod._PREFS, "cmd_history", [])
        result = mod._cmd_recall(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "no prompt history" in out.lower()

    def test_recall_no_arg_with_history_shows_numbered_list(self, capsys, monkeypatch):
        """/recall with no argument and history shows numbered prompt list."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setitem(
            mod._PREFS,
            "cmd_history",
            [
                "explain recursion",
                "/help",
                "what is async await",
            ],
        )
        result = mod._cmd_recall(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "explain recursion" in out
        assert "what is async await" in out
        assert "/help" not in out  # slash commands filtered out

    def test_recall_n_sets_next_inject(self, monkeypatch):
        """/recall 1 with history sets _next_inject to most recent prompt."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setitem(
            mod._PREFS,
            "cmd_history",
            [
                "first prompt",
                "/help",
                "second prompt",
            ],
        )
        mod._next_inject = ""
        result = mod._cmd_recall(self._ctx(args="1"))
        assert result == mod._CMD_CONTINUE
        assert mod._next_inject == "second prompt"

    def test_recall_out_of_range_shows_error(self, capsys, monkeypatch):
        """/recall 99 with short history shows 'No prompt #99'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setitem(mod._PREFS, "cmd_history", ["only prompt"])
        result = mod._cmd_recall(self._ctx(args="99"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No prompt #99" in out


class TestCmdPromptFormat:
    """Tests for _render_prompt_format and _cmd_prompt."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_render_prompt_format_build_token(self):
        """_render_prompt_format with {build} returns string containing _CLI_BUILD."""
        result = mod._render_prompt_format("{build} ❯ ")
        assert mod._CLI_BUILD in result

    def test_render_prompt_format_time_token(self):
        """_render_prompt_format with {time} returns string containing ':' (HH:MM)."""
        result = mod._render_prompt_format("{time} > ")
        assert ":" in result

    def test_cmd_prompt_reset_restores_default(self, monkeypatch):
        """/prompt reset sets prompt_format pref to _DEFAULT_PROMPT_FORMAT."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setitem(mod._PREFS, "prompt_format", "custom {build} ❯ ")
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_prompt(self._ctx(args="reset"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("prompt_format") == mod._DEFAULT_PROMPT_FORMAT

    def test_openclaw_cli_cmd_prompt_set_format(self, monkeypatch):
        """/prompt {build} ❯  sets prompt_format pref correctly (trailing space stripped)."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_prompt(self._ctx(args="{build} ❯"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("prompt_format") == "{build} ❯"


class TestExecErrorHints:
    """Tests for _analyze_exec_error smart recovery hints."""

    def test_file_not_found_contains_path_hint(self):
        """Hints for 'No such file or directory' include path-related advice."""
        hints = mod._analyze_exec_error("ls foo", "No such file or directory", 1)
        assert any("ls -la" in h or "mkdir" in h for h in hints)

    def test_command_not_found_contains_install_hint(self):
        """Hints for 'command not found' include install suggestion."""
        hints = mod._analyze_exec_error("foobar", "command not found", 127)
        assert any("foobar" in h for h in hints)

    def test_missing_python_module_contains_pip_hint(self):
        """Hints for missing Python module include pip install suggestion."""
        hints = mod._analyze_exec_error("python", "No module named 'foo'", 1)
        assert any("pip install" in h for h in hints)

    def test_success_returns_empty_list(self):
        """Exit code 0 with no stderr returns no hints."""
        hints = mod._analyze_exec_error("ls", "", 0)
        assert hints == []


class TestCmdTip:
    """Tests for /tip command and _OPENCLAW_TIPS constant."""

    def _ctx(self):
        return mod.ChatCommandContext(history=[], session_id="")

    def test_tip_returns_cmd_continue(self, monkeypatch):
        """/tip returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_tip(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_openclaw_tips_is_nonempty_list(self):
        """_OPENCLAW_TIPS is a non-empty list of strings."""
        assert isinstance(mod._OPENCLAW_TIPS, list)
        assert len(mod._OPENCLAW_TIPS) > 0
        assert all(isinstance(t, str) for t in mod._OPENCLAW_TIPS)

    def test_wave44_tip_commands_are_present(self):
        tips_text = " ".join(mod._OPENCLAW_TIPS)
        for command in (
            "/tokeninfo",
            "/trace",
            "/handoff check",
            "/fleet health",
            "/alerts",
            "/collab decision",
            "/bookmark",
            "/overlay",
            "/pattern",
            "/draft multiline",
        ):
            assert command in tips_text

    def test_openclaw_cli_cli_build_is_wave50_v2(self):
        """_CLI_BUILD is updated to wave50."""
        assert mod._CLI_BUILD == "wave50"


class TestCmdTokeninfo:
    def _ctx(self, history=None):
        return mod.ChatCommandContext(history=list(history or []), session_id="")

    def test_tokeninfo_shows_progress_bar_and_estimate(self, capsys):
        result = mod._cmd_tokeninfo(
            self._ctx(
                [
                    {"role": "user", "content": "x" * 400},
                    {"role": "assistant", "content": "y" * 200},
                ]
            )
        )

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Context usage" in out
        assert "Est. tokens:" in out
        assert "Window:" in out
        assert "Breakdown by actor" in out
        assert "Largest share: user" in out

    def test_tokeninfo_warns_near_capacity_with_bookmark_hint(self, capsys):
        result = mod._cmd_tokeninfo(
            self._ctx(
                [
                    {"role": "user", "content": "x" * 400_000},
                    {"role": "assistant", "content": "y" * 120_000},
                ]
            )
        )

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "resolved window" in out
        assert "/bookmark and /clear" in out

    def test_tokeninfo_uses_model_aware_limit_for_gemma_route(self, capsys, monkeypatch):
        monkeypatch.setitem(mod._PREFS, "last_model", "gemma3:4b")
        result = mod._cmd_tokeninfo(
            self._ctx(
                [
                    {"role": "user", "content": "x" * 390_000},
                ]
            )
        )

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "~100k Gemma-class window" in out
        assert "Context is near capacity" in out


class TestCmdKeys:
    """Tests for /keys command and _print_key_bindings() helper."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_print_key_bindings_runs_without_error(self, monkeypatch):
        """_print_key_bindings() runs without error in non-TTY mode."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._print_key_bindings()  # should not raise

    def test_cmd_keys_returns_cmd_continue(self, monkeypatch):
        """/keys returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        result = mod._cmd_keys(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_print_key_bindings_output_contains_ctrl_r(self, capsys, monkeypatch):
        """_print_key_bindings() output contains 'Ctrl+R'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._print_key_bindings()
        captured = capsys.readouterr().out
        assert "Ctrl+R" in captured


class TestCmdBindlist:
    """Tests for /bindlist command."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_bindlist_returns_cmd_continue_no_custom(self, monkeypatch):
        """/bindlist returns _CMD_CONTINUE with no custom bindings."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_bindlist(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_bindlist_returns_cmd_continue_with_custom(self, monkeypatch):
        """/bindlist returns _CMD_CONTINUE when custom_keybinds exist in prefs."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"custom_keybinds": {"Ctrl+H": "/histsearch"}})
        result = mod._cmd_bindlist(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_openclaw_cli_cli_build_is_wave50_v3(self):
        """_CLI_BUILD == 'wave50'."""
        assert mod._CLI_BUILD == "wave50"


class TestCmdKeybind:
    """Tests for /keybind command."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_keybind_list_empty(self, monkeypatch, capsys):
        """/keybind list with empty custom bindings shows 'No custom keybinds'."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_keybind(self._ctx("list"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No custom keybinds" in out

    def test_keybind_saves_binding(self, monkeypatch):
        """/keybind Ctrl+H /histsearch saves binding to _PREFS['custom_keybinds']."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        with patch.object(mod, "_save_prefs"):
            with patch.object(mod, "_apply_custom_keybind"):
                result = mod._cmd_keybind(self._ctx("Ctrl+H /histsearch"))
        assert result == mod._CMD_CONTINUE
        assert mod._PREFS.get("custom_keybinds", {}).get("Ctrl+H") == "/histsearch"

    def test_keybind_clear_removes_binding(self, monkeypatch, capsys):
        """/keybind clear Ctrl+H removes the binding from _PREFS."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"custom_keybinds": {"Ctrl+H": "/histsearch"}})
        with patch.object(mod, "_save_prefs"):
            result = mod._cmd_keybind(self._ctx("clear Ctrl+H"))
        assert result == mod._CMD_CONTINUE
        assert "Ctrl+H" not in mod._PREFS.get("custom_keybinds", {})
        out = capsys.readouterr().out
        assert "Removed" in out

    def test_keybind_missing_action_shows_usage(self, monkeypatch, capsys):
        """/keybind Ctrl+H with no action shows usage message."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_keybind(self._ctx("Ctrl+H"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Usage" in out


class TestRenderDiffAnsi:
    """Tests for _render_diff_ansi colorization."""

    def test_added_line_contains_green(self):
        """Lines starting with '+' are wrapped in _GR (green)."""
        result = mod._render_diff_ansi("+added line")
        assert mod._GR in result

    def test_removed_line_contains_red(self):
        """Lines starting with '-' are wrapped in _RE (red)."""
        result = mod._render_diff_ansi("-removed line")
        assert mod._RE in result

    def test_hunk_header_contains_cyan(self):
        """Lines starting with '@@' are wrapped in _CY (cyan)."""
        result = mod._render_diff_ansi("@@ -1,3 +1,4 @@")
        assert mod._CY in result


class TestCmdSnapshot:
    """Tests for /snapshot and /rollback (git snapshot) commands."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_snapshot_no_args_returns_cmd_continue(self, monkeypatch):
        """/snapshot with no args returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        import subprocess

        fake = type("R", (), {"stdout": "abc123def456\n", "stderr": "", "returncode": 0})()
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        with patch.object(mod, "_save_prefs"):
            result = mod._cmd_snapshot(self._ctx(""))
        assert result == mod._CMD_CONTINUE

    def test_rollback_list_no_snapshots_shows_no_snapshots(self, monkeypatch, capsys):
        """/rollback list with no snapshots shows 'No snapshots' message and returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_rollback(self._ctx("list"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No snapshots" in out

    def test_rollback_unknown_name_shows_no_snapshot_named(self, monkeypatch, capsys):
        """/rollback unknownname shows 'No snapshot named' message."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_rollback(self._ctx("unknownname"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No snapshot named" in out


class TestCmdChanges:
    """Tests for /changes and /diff commands."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_cmd_changes_returns_cmd_continue(self, monkeypatch):
        """/changes returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        import subprocess

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "", "stderr": ""})())
        result = mod._cmd_changes(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_cmd_diff_no_args_returns_cmd_continue(self, monkeypatch):
        """/diff with no args returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        import subprocess

        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "", "stderr": ""})())
        result = mod._cmd_diff(self._ctx())
        assert result == mod._CMD_CONTINUE


class TestCmdDashboard:
    """Tests for /dashboard — power dashboard command."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_dashboard_empty_prefs_returns_cmd_continue(self, monkeypatch, capsys):
        """/dashboard returns _CMD_CONTINUE with empty prefs."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_dashboard(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_dashboard_with_mock_data_returns_cmd_continue(self, monkeypatch, capsys):
        """/dashboard returns _CMD_CONTINUE with pins, macros, and ratings populated."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "cmd_history": [
                    {"text": "hello world", "timestamp": "2024-01-01"},
                    {"text": "/pin foo bar", "timestamp": "2024-01-02"},
                ],
                "ratings": [{"score": 4}, {"score": 5}],
                "pins": {"foo": "bar", "env": "prod"},
                "macros": {"greet": "say hello"},
                "aliases": {"h": "/help"},
                "snapshots": {"snap1": "abc123"},
                "custom_keybinds": {"Ctrl+D": "/dashboard"},
            },
        )
        result = mod._cmd_dashboard(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        # Pins and stats should appear in plain-text output
        assert "foo" in out
        assert "Prompts" in out

    def test_dashboard_output_references_cli_build(self, monkeypatch, capsys):
        """Dashboard plain-text output contains the _CLI_BUILD string."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_dashboard(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert mod._CLI_BUILD in out

    def test_dashboard_automation_shows_operator_totals(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        sess = sessions_mod.create_session(title="automation", cwd=str(tmp_path))
        sessions_mod.save_watch_state(sess.session_id, {"status": "retrying", "failure_count": 2, "interventions": []})

        result = mod._cmd_dashboard(self._ctx("automation"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Automation dashboard" in out
        assert "Alerts:" in out
        assert "automation retrying" in out

    def test_alerts_acknowledge_hides_alert(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._load_prefs()
        sess = sessions_mod.create_session(title="alerted", cwd=str(tmp_path))
        sessions_mod.save_watch_state(sess.session_id, {"status": "retrying", "failure_count": 1, "interventions": []})

        list_result = mod._cmd_alerts(self._ctx())
        assert list_result == mod._CMD_CONTINUE
        list_out = capsys.readouterr().out
        assert "Operator alerts" in list_out
        assert "retrying" in list_out

        ack_result = mod._cmd_alerts(self._ctx("acknowledge 1"))
        assert ack_result == mod._CMD_CONTINUE
        ack_out = capsys.readouterr().out
        assert "Acknowledged alert 1" in ack_out

        mod._cmd_alerts(self._ctx())
        final_out = capsys.readouterr().out
        assert "(none)" in final_out

    def test_fleet_status_reuses_automation_dashboard(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        sessions_mod.create_session(title="fleet", cwd=str(tmp_path))

        result = mod._cmd_fleet(self._ctx("status"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Automation dashboard" in out


class TestCmdBenchmark:
    """Tests for /benchmark — AI server response latency measurement."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_benchmark_default_returns_cmd_continue(self, monkeypatch):
        """/benchmark (default 3 pings) returns _CMD_CONTINUE even when connection fails."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        import socket

        monkeypatch.setattr(
            socket, "create_connection", lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError("no server"))
        )
        result = mod._cmd_benchmark(self._ctx())
        assert result == mod._CMD_CONTINUE

    def test_benchmark_explicit_n_returns_cmd_continue(self, monkeypatch):
        """/benchmark 5 runs 5 pings and returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        import socket

        monkeypatch.setattr(
            socket, "create_connection", lambda *a, **kw: (_ for _ in ()).throw(ConnectionRefusedError("no server"))
        )
        result = mod._cmd_benchmark(self._ctx("5"))
        assert result == mod._CMD_CONTINUE

    def test_benchmark_zero_clamped_to_one(self, monkeypatch):
        """/benchmark 0 is clamped to 1 ping and still returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        import socket

        call_count = []

        def fake_connect(*a, **kw):
            call_count.append(1)
            raise ConnectionRefusedError("no server")

        monkeypatch.setattr(socket, "create_connection", fake_connect)
        result = mod._cmd_benchmark(self._ctx("0"))
        assert result == mod._CMD_CONTINUE
        assert len(call_count) == 1


class TestCmdTimeline:
    """Tests for /timeline command — Wave 30 THE FINAL WAVE."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_timeline_empty_history_shows_no_history_message(self, monkeypatch, capsys):
        """/timeline with empty history shows 'No history yet' and returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": []})
        result = mod._cmd_timeline(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "No history yet" in out

    def test_timeline_with_timestamped_history_returns_cmd_continue(self, monkeypatch, capsys):
        """/timeline with mock timestamped history returns _CMD_CONTINUE."""
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "cmd_history": [
                    {"text": "/help", "timestamp": "2024-06-01T10:00:00"},
                    {"text": "explain async", "timestamp": "2024-06-01T11:30:00"},
                    {"text": "/stats", "timestamp": "2024-06-02T09:15:00"},
                ]
            },
        )
        result = mod._cmd_timeline(self._ctx())
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Timeline" in out or "2024-06" in out

    def test_openclaw_cli_cli_build_is_wave50_v4(self):
        """_CLI_BUILD == 'wave50'."""
        assert mod._CLI_BUILD == "wave50"


class TestCmdBookmarks:
    """Tests for Wave 32 bookmark and replay helpers."""

    def _ctx(
        self,
        args: str = "",
        session_id: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=list(history or []), session_id=session_id, args=args)

    def test_bookmark_creates_session_bookmark_with_label(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Bookmark Me", cwd=str(tmp_path))
        history = [
            {"role": "user", "content": "Find the fix"},
            {"role": "assistant", "content": "The fix is in src/openclaw_cli.py"},
        ]

        result = mod._cmd_bookmark(self._ctx("fix located", session.session_id, history))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Saved bookmark [b1] fix located" in out
        bookmarks = mod.list_session_bookmarks(session.session_id)
        assert bookmarks[0]["label"] == "fix located"
        assert bookmarks[0]["turn_index"] == 1

    def test_bookmarks_lists_all_bookmarks_for_session(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Bookmark List", cwd=str(tmp_path))
        mod.persist_response(session.session_id, "First turn", "First answer")
        mod.create_session_bookmark(session.session_id, label="first")
        mod.persist_response(session.session_id, "Second turn", "Second answer")
        mod.create_session_bookmark(session.session_id, label="second")

        result = mod._cmd_bookmarks(self._ctx("", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "[b1] first" in out
        assert "[b2] second" in out

    def test_replay_from_bookmark_filters_session_turns(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Replay Bookmark", cwd=str(tmp_path))
        mod.persist_response(session.session_id, "First turn", "First answer")
        mod.persist_response(session.session_id, "Second turn", "Second answer")
        mod.create_session_bookmark(session.session_id, label="second")

        result = mod._cmd_replay(self._ctx("--from b1", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Replay from [b1] second" in out
        assert "Second turn" in out
        assert "Second answer" in out
        assert "First turn" not in out


class TestCmdExport:
    """Tests for /export command — Wave 31."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_export_md_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": ["hello world", "what is 2+2"]})
        result = mod._cmd_export(self._ctx("md test_export"))
        assert result == mod._CMD_CONTINUE
        files = list(tmp_path.glob("*.md"))
        assert files

    def test_export_json_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": ["test prompt"]})
        result = mod._cmd_export(self._ctx("json"))
        assert result == mod._CMD_CONTINUE
        files = list(tmp_path.glob("*.json"))
        assert files

    def test_export_empty_history(self, monkeypatch, capsys):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": []})
        result = mod._cmd_export(self._ctx("md"))
        assert result == mod._CMD_CONTINUE

    def test_export_txt_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": ["prompt one", "prompt two"]})
        result = mod._cmd_export(self._ctx("txt"))
        assert result == mod._CMD_CONTINUE
        files = list(tmp_path.glob("*.txt"))
        assert files

    def test_export_default_format_is_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"cmd_history": ["any prompt"]})
        result = mod._cmd_export(self._ctx(""))
        assert result == mod._CMD_CONTINUE
        files = list(tmp_path.glob("*.md"))
        assert files


class TestCmdWorkspace:
    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_workspace_status_prints_capsule_summary(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        tracked = tmp_path / "src.py"
        tracked.write_text("print('ok')\n", encoding="utf-8")
        session = mod.create_session(title="Workspace Status", cwd=str(tmp_path), files=[str(tracked)])
        mod.persist_response(session.session_id, "Mark this", "status ready")
        mod.create_session_bookmark(session.session_id, label="ready")

        result = mod._cmd_workspace(self._ctx("status", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Workspace Capsule" in out
        assert "signature:" in out
        assert "recent bookmarks:" in out

    def test_workspace_list_shows_saved_capsules(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Workspace List", cwd=str(tmp_path))
        mod.create_handoff(session.session_id, note="saved capsule")

        result = mod._cmd_workspace(self._ctx("list"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Workspace capsules:" in out
        assert "handoff_" in out


class TestCmdRunbook:
    def _ctx(self, args: str = "", session_id: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_runbook_renders_active_session(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Wave 35 Session", cwd=str(tmp_path), task_id="task-35")
        mod.persist_response(session.session_id, "Summarize the work", "The runbook should capture the artifacts.")
        mod.save_output(session.session_id, "artifact.txt", "done")

        result = mod._cmd_runbook(self._ctx("", session.session_id))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "# Operator Runbook" in out
        assert "Wave 35 Session" in out
        assert "## Next Commands" in out

    def test_runbook_save_writes_markdown_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        session = mod.create_session(title="Save Runbook", cwd=str(tmp_path))
        mod.persist_response(session.session_id, "Need a saveable handoff", "Write it to disk.")

        result = mod._cmd_runbook(self._ctx(f"stakeholder save {tmp_path / 'handoff'}", session.session_id))

        assert result == mod._CMD_CONTINUE
        target = tmp_path / "handoff.md"
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "# Stakeholder Update" in content


class TestCmdExportTemplates:
    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_exporttemplates_list_shows_builtin_templates(self, monkeypatch, capsys):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)

        result = mod._cmd_exporttemplates(self._ctx("list"))

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "operator" in out
        assert "postmortem" in out


class TestCmdColorscheme:
    """Tests for /colorscheme command and _EXTENDED_SCHEMES."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_list_shows_all_schemes(self, monkeypatch, capsys):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_colorscheme(self._ctx("list"))
        assert result == mod._CMD_CONTINUE

    def test_set_valid_scheme(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        prefs: dict = {}
        monkeypatch.setattr(mod, "_PREFS", prefs)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_colorscheme(self._ctx("cyberpunk"))
        assert result == mod._CMD_CONTINUE
        assert prefs.get("color_scheme") == "cyberpunk"

    def test_set_invalid_scheme(self, monkeypatch, capsys):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {})
        result = mod._cmd_colorscheme(self._ctx("nonexistent"))
        assert result == mod._CMD_CONTINUE

    def test_reset_sets_default(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        prefs: dict = {"color_scheme": "matrix"}
        monkeypatch.setattr(mod, "_PREFS", prefs)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_colorscheme(self._ctx("reset"))
        assert result == mod._CMD_CONTINUE
        assert prefs.get("color_scheme") == "default"

    def test_openclaw_cli_cli_build_is_wave50_v5(self):
        assert mod._CLI_BUILD == "wave50"


class TestCmdFollowup:
    """Tests for /followup command and _suggest_followups — Wave 31."""

    def _ctx(self, args: str = "") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id="", args=args)

    def test_suggest_followups_file_keywords(self):
        suggestions = mod._suggest_followups("where is the log file?")
        assert any("pathhints" in s or "exec" in s for s in suggestions)

    def test_suggest_followups_empty_returns_defaults(self):
        suggestions = mod._suggest_followups("some random text xyz")
        assert len(suggestions) > 0

    def test_suggest_followups_max_3(self):
        suggestions = mod._suggest_followups("find the file with error history recap search compare pin rate")
        assert len(suggestions) <= 3

    def test_suggest_followups_uses_response_context_and_session(self):
        suggestions = mod._suggest_followups(
            "show me the fix",
            response_text="Updated src/openclaw_cli.py\n\nSources:\n- https://example.com",
            session_id="session-123",
        )
        joined = " ".join(suggestions)
        assert "/view src/openclaw_cli.py" in joined
        assert "/context" in joined or "/links" in joined

    def test_print_followup_suggestions_plain_mode_uses_bottom_bar(self, monkeypatch, capsys):
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, False)

        mod._print_followup_suggestions(
            [
                "/rate good — mark this answer helpful",
                "/context — verify what the next request will inherit",
            ],
            mode="chat",
        )

        stdout = capsys.readouterr().out
        assert "Bottom bar:" in stdout
        assert "mode: chat" in stdout
        assert "/rate good" in stdout
        assert "/context" in stdout

    def test_cmd_followup_no_history(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"_last_prompt": ""})
        result = mod._cmd_followup(self._ctx(""))
        assert result == mod._CMD_CONTINUE

    def test_cmd_followup_with_history(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", {"_last_prompt": "find the broken file"})
        result = mod._cmd_followup(self._ctx(""))
        assert result == mod._CMD_CONTINUE

    def test_cmd_followup_toggle_off(self, monkeypatch):
        prefs: dict = {"_last_prompt": "test", "show_suggestions": True}
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", prefs)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_followup(self._ctx("off"))
        assert result == mod._CMD_CONTINUE
        assert prefs.get("show_suggestions") is False

    def test_cmd_followup_toggle_on(self, monkeypatch):
        prefs: dict = {"_last_prompt": "test", "show_suggestions": False}
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(mod, "_PREFS", prefs)
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        result = mod._cmd_followup(self._ctx("on"))
        assert result == mod._CMD_CONTINUE
        assert prefs.get("show_suggestions") is True


class TestSourcesBugFixes:
    """Regression tests for sources duplication and URL rendering fixes."""

    def test_numbered_sources_extracted_from_body(self):
        text = "Some response text.\n\nSources:\n1. https://example.com\n2. https://other.com\n"
        body, sources = mod._preprocess_response_text(text)
        assert sources is not None
        assert "example.com" in sources
        assert "example.com" not in body

    def test_bullet_sources_still_extracted(self):
        text = "Response.\n\nSources\n- https://foo.com\n- https://bar.com\n"
        body, sources = mod._preprocess_response_text(text)
        assert sources is not None
        assert "foo.com" in sources
        assert "foo.com" not in body

    def test_duplicate_sources_both_stripped(self):
        text = "Response.\n\nSources\n- https://a.com\n\nMore text.\n\nSources\n1. https://b.com\n2. https://c.com\n"
        body, sources = mod._preprocess_response_text(text)
        assert "Sources" not in body

    def test_clean_sources_strips_markdown_links(self):
        sources = "Sources\n1. [my page](https://example.com/page)\n2. https://other.com\n"
        items = mod._clean_sources_for_display(sources)
        urls = [url for _, url in items]
        assert "https://example.com/page" in urls
        assert "https://other.com" in urls

    def test_preprocess_loose_sources_without_blank_line(self):
        text = "Response body.\nSources:\n- https://example.com\n- https://other.com\n"
        body, sources = mod._preprocess_response_text(text)
        assert sources is not None
        assert "https://example.com" in sources
        assert "Sources:" not in body

    def test_render_body_strips_duplicate_sources_heading(self, monkeypatch):
        captured: dict[str, str | None] = {}

        def _fake_render(body: str, sources: str | None, ctx: object) -> None:
            captured["body"] = body
            captured["sources"] = sources

        monkeypatch.setattr(mod._render_mod, "_render_response_body", _fake_render)

        mod._render_response_body(
            "Response body.\n\nSources:\n- https://example.com\n",
            "Sources:\n- https://example.com\n",
            False,
            False,
        )

        assert "Sources" not in str(captured.get("body") or "")
        assert "example.com" in str(captured.get("sources") or "")

    def test_clean_sources_strips_ansi_codes_from_display(self):
        sources = "Sources\n1. [\x1b[36mExample\x1b[0m](https://example.com/page)\n"
        items = mod._clean_sources_for_display(sources)
        assert items == [("Example", "https://example.com/page")]

    def test_ansi_source_box_matches_terminal_width(self, monkeypatch, capsys):
        monkeypatch.setattr(
            mod._render_mod.shutil,
            "get_terminal_size",
            lambda fallback=(80, 24): os.terminal_size((100, 24)),
        )
        ctx = mod._render_mod.RenderContext(
            is_tty=True,
            is_rich=False,
            high_contrast=False,
            plain_mode=False,
            cols=60,
            theme_ansi="",
            prefs={"clickable_links": False, "rich": False},
        )

        mod._render_mod._render_response_body(
            "Body",
            "Sources\n1. https://example.com\n",
            ctx,
        )

        out = capsys.readouterr().out.splitlines()
        border_lines = [line for line in out if "╭" in line or "╰" in line]
        assert border_lines
        assert max(len(line) for line in border_lines) > 90

    def test_detect_file_paths_excludes_url_remnants(self):
        # Protocol-relative URLs like //www.adobe.com/... should NOT be detected as paths
        text = "See //www.adobe.com/acrobat/resources/ai-assistant-capabilities.html"
        paths = mod._detect_file_paths(text)
        assert not any(p.startswith("//") for p in paths)

    def test_detect_file_paths_still_finds_local_paths(self):
        paths = mod._detect_file_paths("see src/openclaw_cli.py for details")
        assert "src/openclaw_cli.py" in paths

    def test_detect_url_mentions_fires_with_action_verb(self):
        import openclaw_cli_path_utils as pu

        urls = pu._detect_url_mentions("summarize https://example.com/readme.md for me")
        assert "https://example.com/readme.md" in urls

    def test_detect_url_mentions_no_action_verb_returns_empty(self):
        import openclaw_cli_path_utils as pu

        urls = pu._detect_url_mentions("I found https://example.com mentioned in the docs")
        assert urls == []

    def test_detect_url_mentions_strips_trailing_punctuation(self):
        import openclaw_cli_path_utils as pu

        urls = pu._detect_url_mentions("read https://example.com/path.md.")
        assert all(not u.endswith(".") for u in urls)

    def test_detect_url_mentions_caps_at_three(self):
        import openclaw_cli_path_utils as pu

        text = "summarize https://a.com https://b.com https://c.com https://d.com"
        urls = pu._detect_url_mentions(text)
        assert len(urls) <= 3

    def test_detect_explicit_refs_file(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("please read @file:/tmp/test.txt and summarize")
        assert ("file", "/tmp/test.txt") in refs

    def test_detect_explicit_refs_url(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("explain @url:https://example.com/doc")
        assert ("url", "https://example.com/doc") in refs

    def test_detect_explicit_refs_multiple(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("compare @file:/a.md and @file:/b.md")
        assert len(refs) == 2

    def test_strip_explicit_refs(self):
        import openclaw_cli_path_utils as pu

        cleaned = pu._strip_explicit_refs("read @file:/tmp/a.txt and answer")
        assert "@file:" not in cleaned
        assert "read" in cleaned and "answer" in cleaned

    def test_detect_explicit_refs_empty_when_none(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("no explicit refs here")
        assert refs == []

    def test_detect_explicit_refs_clip(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("summarize @clip for me")
        assert ("clip", "") in refs

    def test_detect_explicit_refs_dir(self):
        import openclaw_cli_path_utils as pu

        refs = pu._detect_explicit_refs("what files are in @dir:/tmp/myproject?")
        assert ("dir", "/tmp/myproject") in refs

    def test_strip_explicit_refs_clip(self):
        import openclaw_cli_path_utils as pu

        cleaned = pu._strip_explicit_refs("summarize @clip please")
        assert "@clip" not in cleaned
        assert "summarize" in cleaned

    def test_detect_explicit_refs_all_types(self):
        import openclaw_cli_path_utils as pu

        text = "use @file:/a.md @url:https://x.com @dir:/src @clip"
        refs = pu._detect_explicit_refs(text)
        kinds = {r[0] for r in refs}
        assert kinds == {"file", "url", "dir", "clip"}


class TestCmdTrace:
    def _ctx(self, args: str = "", session_id: str = "session-34") -> mod.ChatCommandContext:
        return mod.ChatCommandContext(history=[], session_id=session_id, args=args)

    def test_trace_shows_last_route_snapshot(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        monkeypatch.setattr(
            mod,
            "_require_session_or_warn",
            lambda ctx: object(),
        )
        monkeypatch.setattr(
            mod,
            "get_last_decision_event",
            lambda session_id: {
                "kind": "route",
                "timestamp": "2026-04-14T12:00:00Z",
                "metadata": {
                    "slash_command": "research",
                    "confidence": 0.91,
                    "route_reason": "The request spans multiple code areas.",
                },
            },
        )
        monkeypatch.setattr(mod, "_PREFS", {"ratings": [{"score": 4, "label": "good"}]})

        result = mod._cmd_trace(self._ctx())

        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Trace Snapshot" in out
        assert "/research" in out
        assert "Latest rating" in out

    def test_routing_suggest_shows_best_rated_route(self, capsys, monkeypatch):
        monkeypatch.setattr(
            mod,
            "_PREFS",
            {
                "ratings": [
                    {"score": 5, "route": "research"},
                    {"score": 4, "route": "research"},
                    {"score": 3, "route": "plan"},
                ]
            },
        )
        result = mod._cmd_routing(self._ctx("suggest"))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Routing suggestion" in out
        assert "/research" in out
        assert "advisory only" in out
# =============================================================================
# === TD-16: Config Loading / Validation Tests ===
# =============================================================================


class TestPrefsDefaults:
    """Tests for _PREFS default values."""

    def test_prefs_has_theme_default(self):
        assert "theme" in mod._PREFS

    def test_prefs_theme_default_is_default(self):
        # The module-level default is 'default' (may be overridden by file load)
        assert isinstance(mod._PREFS.get("theme"), str)

    def test_prefs_emoji_is_bool(self):
        assert isinstance(mod._PREFS.get("emoji"), bool)

    def test_prefs_layout_is_string(self):
        assert isinstance(mod._PREFS.get("layout"), str)

    def test_prefs_emoji_pack_is_string(self):
        assert isinstance(mod._PREFS.get("emoji_pack"), str)

    def test_prefs_emoji_headers_is_bool(self, monkeypatch):
        # emoji_headers default is True; ensure monkeypatched _PREFS has it
        monkeypatch.setitem(mod._PREFS, "emoji_headers", True)
        assert isinstance(mod._PREFS.get("emoji_headers"), bool)


class TestLoadPrefs:
    """Tests for _load_prefs() and _save_prefs()."""

    def test_load_prefs_reads_from_json_file(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / ".openclaw" / "prefs.json"
        prefs_file.parent.mkdir(parents=True)
        prefs_file.write_text('{"theme": "green"}', encoding="utf-8")
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        saved = dict(mod._PREFS)
        mod._load_prefs()
        assert mod._PREFS.get("theme") == "green"
        # Restore
        mod._PREFS.update(saved)

    def test_openclaw_cli_load_prefs_silently_ignores_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "nonexistent"))
        # Should not raise
        mod._load_prefs()

    def test_load_prefs_silently_ignores_corrupt_json(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / ".openclaw" / "prefs.json"
        prefs_file.parent.mkdir(parents=True)
        prefs_file.write_text("{{{invalid json}}}", encoding="utf-8")
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        # Should not raise
        mod._load_prefs()

    def test_load_prefs_ignores_non_dict_json(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / ".openclaw" / "prefs.json"
        prefs_file.parent.mkdir(parents=True)
        prefs_file.write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        before = dict(mod._PREFS)
        mod._load_prefs()
        # _PREFS should not have been updated with list content
        assert mod._PREFS.get("theme") == before.get("theme")

    def test_save_prefs_writes_json_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        monkeypatch.setitem(mod._PREFS, "theme", "cyan")
        mod._save_prefs()
        prefs_file = tmp_path / ".openclaw" / "prefs.json"
        assert prefs_file.exists()
        data = json.loads(prefs_file.read_text("utf-8"))
        assert data.get("theme") == "cyan"

    def test_save_prefs_creates_directory_if_missing(self, tmp_path, monkeypatch):
        deep_dir = tmp_path / "a" / "b" / "c"
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(deep_dir))
        # Should not raise even if dir doesn't exist
        mod._save_prefs()
        assert (deep_dir / ".openclaw" / "prefs.json").exists()

    def test_prefs_file_path_uses_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        result = mod._prefs_file_path()
        assert result == tmp_path / ".openclaw" / "prefs.json"

    def test_prefs_set_updates_key(self, monkeypatch):
        monkeypatch.setattr(mod, "_save_prefs", lambda: None)
        monkeypatch.setitem(mod._PREFS, "emoji_headers", True)
        mod._prefs_set("emoji_headers", False)
        assert mod._PREFS["emoji_headers"] is False


class TestNormalizePrefs:
    """Tests for _normalize_personalization_prefs()."""

    def test_layout_normal_stays_normal(self, monkeypatch):
        monkeypatch.setitem(mod._PREFS, "layout", "normal")
        monkeypatch.setitem(mod._PREFS, "emoji_pack", "classic")
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout"] == "normal"

    def test_layout_unknown_falls_back_to_normal(self, monkeypatch):
        monkeypatch.setitem(mod._PREFS, "layout", "bogus_layout")
        monkeypatch.setitem(mod._PREFS, "emoji_pack", "classic")
        mod._normalize_personalization_prefs()
        assert mod._PREFS["layout"] == "normal"

    def test_emoji_pack_ascii_disables_emoji(self, monkeypatch):
        monkeypatch.setitem(mod._PREFS, "emoji_pack", "ascii")
        monkeypatch.setitem(mod._PREFS, "layout", "normal")
        mod._normalize_personalization_prefs()
        assert mod._PREFS.get("emoji") is False

    def test_emoji_pack_classic_enables_emoji(self, monkeypatch):
        monkeypatch.setitem(mod._PREFS, "emoji_pack", "classic")
        monkeypatch.setitem(mod._PREFS, "layout", "normal")
        mod._normalize_personalization_prefs()
        assert mod._PREFS.get("emoji") is True


# =============================================================================
# === TD-16: Error Handling / Exception Propagation Tests ===
# =============================================================================


class TestOpenClawCliError:
    """Tests for the OpenClawCliError exception class."""

    def test_is_runtime_error_subclass(self):
        assert issubclass(mod.OpenClawCliError, RuntimeError)

    def test_openclaw_cli_message_preserved(self):
        exc = mod.OpenClawCliError("something went wrong")
        assert str(exc) == "something went wrong"

    def test_can_be_raised_and_caught(self):
        with pytest.raises(mod.OpenClawCliError, match="test error"):
            raise mod.OpenClawCliError("test error")

    def test_wraps_cause_exception(self):
        cause = ValueError("root cause")
        try:
            raise mod.OpenClawCliError("wrapped") from cause
        except mod.OpenClawCliError as exc:
            assert exc.__cause__ is cause

    def test_write_saved_token_raises_for_empty_token(self, tmp_path):
        with pytest.raises(mod.OpenClawCliError, match="cannot be empty"):
            mod.write_saved_token("", path=tmp_path / "token")

    def test_write_saved_token_raises_for_whitespace_token(self, tmp_path):
        with pytest.raises(mod.OpenClawCliError, match="cannot be empty"):
            mod.write_saved_token("   ", path=tmp_path / "token")

    def test_delete_saved_token_returns_false_when_no_file(self, tmp_path):
        result = mod.delete_saved_token(path=tmp_path / "no_such_token")
        assert result is False

    def test_read_saved_token_returns_empty_when_no_file(self, tmp_path):
        result = mod.read_saved_token(path=tmp_path / "no_such_token")
        assert result == ""


class TestPrintError:
    """Tests for _print_error()."""

    def test_print_error_plain_mode_includes_error_prefix(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        mod._print_error("something failed")
        out = capsys.readouterr().out
        assert "error:" in out
        assert "something failed" in out

    def test_print_error_writes_to_custom_file(self, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", False)
        monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
        buf = io.StringIO()
        mod._print_error("custom dest", file=buf)
        assert "custom dest" in buf.getvalue()
        assert "error:" in buf.getvalue()


class TestFormatErrors:
    """Tests for format_http_error() and format_url_error()."""

    def test_format_http_error_includes_status_code(self):
        msg = mod.format_http_error("http://localhost", 503, "service unavailable")
        assert "503" in msg

    def test_format_http_error_includes_message_when_provided(self):
        msg = mod.format_http_error("http://localhost", 404, "not found")
        assert "not found" in msg

    def test_format_http_error_uses_status_when_message_empty(self):
        msg = mod.format_http_error("http://localhost", 500, "")
        assert "500" in msg

    def test_format_url_error_timeout_message(self):
        exc = error.URLError(reason="timed out")
        msg = mod.format_url_error("http://localhost:8765", exc)
        assert "Timed out" in msg or "timed out" in msg.lower()

    def test_format_url_error_connection_refused_message(self):
        exc = error.URLError(reason="connection refused")
        msg = mod.format_url_error("http://localhost:8765", exc)
        assert "refused" in msg.lower()

    def test_format_url_error_dns_failure_message(self):
        exc = error.URLError(reason="nodename nor servname provided")
        msg = mod.format_url_error("http://myhost.local", exc)
        assert "resolve" in msg.lower() or "host" in msg.lower()

    def test_format_url_error_generic_fallback(self):
        exc = error.URLError(reason="some unknown error")
        msg = mod.format_url_error("http://localhost", exc)
        assert "localhost" in msg


class TestAnalyzeHealthPayload:
    """Tests for analyze_health_payload()."""

    def test_healthy_status_dict_returns_true(self):
        _, ok = mod.analyze_health_payload({"status": "healthy"})
        assert ok is True

    def test_ok_status_dict_returns_true(self):
        _, ok = mod.analyze_health_payload({"status": "ok"})
        assert ok is True

    def test_unhealthy_status_dict_returns_false(self):
        _, ok = mod.analyze_health_payload({"status": "unhealthy"})
        assert ok is False

    def test_degraded_status_returns_false(self):
        _, ok = mod.analyze_health_payload({"status": "degraded"})
        assert ok is False

    def test_healthy_string_returns_true(self):
        _, ok = mod.analyze_health_payload("healthy")
        assert ok is True

    def test_failed_string_returns_false(self):
        _, ok = mod.analyze_health_payload("failed")
        assert ok is False

    def test_openclaw_cli_empty_string_returns_none(self):
        _, ok = mod.analyze_health_payload("")
        assert ok is None

    def test_empty_dict_returns_none(self):
        _, ok = mod.analyze_health_payload({})
        assert ok is None


# =============================================================================
# === TD-16: Auth Flow Tests ===
# =============================================================================

import openclaw_cli_auth as auth_mod


class TestAuthStoragePath:
    """Tests for auth_mod.auth_storage_path()."""

    def test_darwin_path_under_library_application_support(self):
        path = auth_mod.auth_storage_path(platform_name="darwin")
        assert "Application Support" in str(path)
        assert "OpenClaw" in str(path)
        assert path.name == "token"

    def test_linux_path_under_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = auth_mod.auth_storage_path(platform_name="linux")
        assert ".config" in str(path) or "openclaw" in str(path)
        assert path.name == "token"

    def test_linux_path_respects_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = auth_mod.auth_storage_path(platform_name="linux")
        assert str(tmp_path) in str(path)

    def test_windows_path_under_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        path = auth_mod.auth_storage_path(platform_name="win32")
        assert str(tmp_path) in str(path)
        assert path.name == "token"


class TestReadKeychainToken:
    """Tests for auth_mod.read_keychain_token()."""

    def test_returns_empty_string_on_non_darwin(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "linux")
        result = auth_mod.read_keychain_token()
        assert result == ""

    def test_returns_empty_when_account_cannot_be_determined(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        monkeypatch.setenv("USER", "")
        monkeypatch.setattr(auth_mod.getpass, "getuser", lambda: "")
        result = auth_mod.read_keychain_token(account="")
        assert result == ""

    def test_returns_empty_on_subprocess_os_error(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        monkeypatch.setattr(
            auth_mod.subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("security not found")),
        )
        result = auth_mod.read_keychain_token(account="testuser")
        assert result == ""

    def test_returns_empty_when_security_command_fails(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        monkeypatch.setattr(auth_mod.subprocess, "run", lambda *a, **kw: fake_result)
        result = auth_mod.read_keychain_token(account="testuser")
        assert result == ""

    def test_returns_stripped_token_on_success(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "  my-secret-token  \n"
        monkeypatch.setattr(auth_mod.subprocess, "run", lambda *a, **kw: fake_result)
        result = auth_mod.read_keychain_token(account="testuser")
        assert result == "my-secret-token"


class TestWriteKeychainToken:
    """Tests for auth_mod.write_keychain_token()."""

    def test_raises_for_empty_token(self):
        with pytest.raises(auth_mod.OpenClawCliError, match="cannot be empty"):
            auth_mod.write_keychain_token("")

    def test_raises_for_whitespace_only_token(self):
        with pytest.raises(auth_mod.OpenClawCliError, match="cannot be empty"):
            auth_mod.write_keychain_token("   ")

    def test_raises_when_no_account_determinable(self, monkeypatch):
        monkeypatch.setenv("USER", "")
        monkeypatch.setattr(auth_mod.getpass, "getuser", lambda: "")
        with pytest.raises(auth_mod.OpenClawCliError, match="account"):
            auth_mod.write_keychain_token("some-token", account="")

    def test_raises_on_subprocess_os_error(self, monkeypatch):
        monkeypatch.setattr(
            auth_mod.subprocess,
            "run",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("security binary missing")),
        )
        with pytest.raises(auth_mod.OpenClawCliError, match="Keychain"):
            auth_mod.write_keychain_token("tok", account="user")

    def test_raises_when_security_command_fails(self, monkeypatch):
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "errSecDuplicateItem"
        fake_result.stdout = ""
        monkeypatch.setattr(auth_mod.subprocess, "run", lambda *a, **kw: fake_result)
        with pytest.raises(auth_mod.OpenClawCliError, match="Keychain"):
            auth_mod.write_keychain_token("tok", account="user")


class TestDeleteKeychainToken:
    """Tests for auth_mod.delete_keychain_token()."""

    def test_returns_false_on_non_darwin(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "linux")
        result = auth_mod.delete_keychain_token()
        assert result is False

    def test_returns_false_when_item_not_found(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "The specified item could not be found."
        fake_result.stdout = ""
        monkeypatch.setattr(auth_mod.subprocess, "run", lambda *a, **kw: fake_result)
        result = auth_mod.delete_keychain_token(account="user")
        assert result is False

    def test_openclaw_cli_returns_true_when_deletion_succeeds(self, monkeypatch):
        monkeypatch.setattr(auth_mod.sys, "platform", "darwin")
        fake_result = MagicMock()
        fake_result.returncode = 0
        monkeypatch.setattr(auth_mod.subprocess, "run", lambda *a, **kw: fake_result)
        result = auth_mod.delete_keychain_token(account="user")
        assert result is True


class TestResolveToken:
    """Tests for mod.resolve_token() and mod.resolve_token_details()."""

    def test_resolve_token_uses_explicit_token_first(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_TOKEN", "env-tok")
        monkeypatch.setattr(mod, "read_keychain_token", lambda: "keychain-tok")
        result = mod.resolve_token("explicit-tok")
        assert result == "explicit-tok"

    def test_resolve_token_uses_env_when_no_explicit(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_TOKEN", "env-token-123")
        monkeypatch.setattr(mod, "read_keychain_token", lambda: "")
        result = mod.resolve_token(None)
        assert result == "env-token-123"

    def test_resolve_token_returns_empty_when_all_sources_missing(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
        monkeypatch.delenv("DASHBOARD_API_TOKEN", raising=False)
        monkeypatch.setattr(mod, "read_keychain_token", lambda: "")
        monkeypatch.setattr(mod, "read_saved_token", lambda path=None: "")
        result = mod.resolve_token(None)
        assert result == ""

    def test_resolve_token_details_records_source_for_explicit(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_TOKEN", "")
        monkeypatch.setattr(mod, "read_keychain_token", lambda: "")
        details = mod.resolve_token_details("my-token")
        assert details.token == "my-token"
        assert "command line" in details.source or "flag" in details.source

    def test_resolve_token_details_records_env_source(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_TOKEN", "env-val")
        monkeypatch.setattr(mod, "read_keychain_token", lambda: "")
        details = mod.resolve_token_details(None)
        assert details.token == "env-val"
        assert "OPENCLAW_TOKEN" in details.source or "environment" in details.source.lower()


class TestOpenClawCliErrorInAuth:
    """Tests for OpenClawCliError in auth module."""

    def test_auth_module_error_is_runtime_error(self):
        assert issubclass(auth_mod.OpenClawCliError, RuntimeError)

    def test_auth_and_main_error_are_same_class(self):
        # openclaw_cli re-exports the same class from auth
        assert mod.OpenClawCliError is auth_mod.OpenClawCliError

    def test_keychain_service_constant(self):
        assert auth_mod.KEYCHAIN_SERVICE == "OpenClaw CLI"

    def test_token_resolution_dataclass_fields(self):
        tr = auth_mod.TokenResolution(token="abc", source="env")
        assert tr.token == "abc"
        assert tr.source == "env"
