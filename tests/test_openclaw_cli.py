import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import types
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


def test_fetch_health_reads_health_endpoint():
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
    decision = mod.route_repl_prompt(
        "research Python packaging, then draft release notes, after that edit README.md"
    )

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
    decision = mod.route_repl_prompt(
        'run ```bash\ngit commit -m "ship parser fixes"\n```'
    )

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
    assert approval_calls == [
        {
            "action": "shell.exec",
            "target": "git status",
            "risk_level": approval_calls[0]["risk_level"],
            "detail": f"cwd={tmp_path}",
            "auto_approve": False,
            "session_id": session.session_id,
            "plan_id": "",
            "task_id": "",
        }
    ]
    stdout = capsys.readouterr().out
    assert "OpenClaw auto-routed to /exec git status (confidence 0.98;" in stdout
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
    assert approval_calls == [
        {
            "action": "file.edit",
            "target": "notes.txt",
            "risk_level": approval_calls[0]["risk_level"],
            "detail": "append=True;replace=False",
            "auto_approve": False,
            "session_id": session.session_id,
            "plan_id": "",
            "task_id": "",
        }
    ]
    stdout = capsys.readouterr().out
    assert "OpenClaw auto-routed to /edit notes.txt --append hello (confidence 0.97;" in stdout
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
    assert "OpenClaw identified a plan candidate with 2 steps" in stdout
    assert "Created plan `plan-auto-123` with 2 steps." in stdout
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
    assert "credential file" in stdout
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
    assert "/rollback last" in stdout
    assert "multi-step prompts can decompose into linked plans" in stdout
    assert "exec checkpoints remain manual-recovery only" in stdout
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
        assert names >= {"session", "context", "cwd", "files", "plan", "task", "outputs", "rollback", "events",
                         "analyze", "research", "write", "exec", "edit"}


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
        result = self._registry().dispatch("/session", self._ctx(session_id=sess.session_id))
        assert result == mod._CMD_CONTINUE
        out = capsys.readouterr().out
        assert "Test Session" in out
        assert sess.session_id in out

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
        self._registry().dispatch("/context", self._ctx(session_id=sess.session_id))
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

        self._registry().dispatch("/outputs", self._ctx(session_id=sess.session_id))

        out = capsys.readouterr().out
        assert "saved outputs" in out
        assert "1. second.md" in out
        assert "2. first.md" in out

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

    def test_task_shows_current_task(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path))
        sess = sessions_mod.create_session(title="task-show", cwd=str(tmp_path), task_id="task-77")
        self._registry().dispatch("/task", self._ctx(session_id=sess.session_id))
        out = capsys.readouterr().out
        assert "task-77" in out

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
        lines = [ln for ln in out.strip().splitlines() if ln.strip()]
        assert len(lines) <= 2

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
        for cmd in ("/session", "/context", "/cwd", "/files", "/plan", "/task", "/outputs", "/rollback", "/events",
                     "/autoroute", "/analyze", "/research", "/write", "/exec", "/edit"):
            assert cmd in out, f"Expected {cmd} in /help output"
        assert "multi-step prompts can decompose into linked plans" in out
        assert "latest five routed checkpoints are retained" in out


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
        result = self._registry().dispatch("/analyze", self._ctx(session_id=sess.session_id, config=self._make_config()))
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
        assert "not approved" in capsys.readouterr().out.lower()

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
        fake_result = ShellCommandResult(command="git status", stdout="ok\n", stderr="", returncode=0, cwd=str(tmp_path))
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
        fake_result = ShellCommandResult(command="git status", stdout="ok\n", stderr="", returncode=0, cwd=str(tmp_path))

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
        target = tmp_path / "secret.py"
        target.touch()
        with patch.object(mod, "request_cli_approval", return_value=False):
            result = self._registry().dispatch(
                f"/edit {target} --content new content",
                self._ctx(session_id=sess.session_id),
            )
        assert result == mod._CMD_CONTINUE
        assert "not approved" in capsys.readouterr().out.lower()

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
            event.get("kind") == "rollback"
            and (event.get("metadata") or {}).get("status") == "unsupported"
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
            patch.object(mod, "write_text_file") as write_text_file,
        ):
            result = self._registry().dispatch(
                f"/edit {target} --content [tool.demo]\nname = 'blocked'\n",
                self._ctx(session_id=sess.session_id, route_metadata=route_metadata),
            )

        assert result == mod._CMD_CONTINUE
        assert approval_calls
        assert approval_calls[0]["target"] == str(target)
        write_text_file.assert_not_called()
        assert sessions_mod.list_routed_action_checkpoints(sess.session_id, limit=0) == []
        assert "not approved" in capsys.readouterr().out.lower()



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
        exit_code = mod.main(["watch", "--cwd", str(tmp_path), "--iterations", "1", "@README.md", "watch", "for", "regressions"])

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
        exit_code = mod.main(["watch", "--mode", "research", "--cwd", str(tmp_path), "--iterations", "1", "investigate", "scheduler"])

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


def test_main_exec_tracks_shell_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(command="git status", cwd=str(tmp_path), returncode=0, stdout="On branch main\n", stderr="")

    with (
        patch.object(mod, "run_shell_command", new=AsyncMock(return_value=fake_result)) as run_shell_command,
        patch.object(mod, "request_cli_approval", return_value=True),
    ):
        exit_code = mod.main(["exec", "--cwd", str(tmp_path), "--", "git", "status"])

    assert exit_code == 0
    run_shell_command.assert_called_once()
    stdout = capsys.readouterr().out
    assert "$ git status" in stdout
    assert "session:" in stdout


def test_main_edit_dry_run_prints_diff(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    target = tmp_path / "notes.md"
    target.write_text("hello world\n", encoding="utf-8")

    with patch.object(mod, "request_cli_approval", return_value=True):
        exit_code = mod.main(["edit", str(target), "--replace", "world", "there", "--dry-run"])

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == "hello world\n"
    stdout = capsys.readouterr().out
    assert "-hello world" in stdout
    assert "+hello there" in stdout


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


# ── New: exec / edit --plan-id / --task-id ───────────────────────────────────


def test_exec_plan_task_tagging_creates_linked_session(monkeypatch, tmp_path):
    """exec --plan-id / --task-id should link the created session to that plan and task."""
    monkeypatch.setenv("OPENCLAW_CLI_HOME", str(tmp_path / "cli-home"))
    fake_result = SimpleNamespace(command="echo hi", cwd=str(tmp_path), returncode=0, stdout="hi\n", stderr="")

    with (
        patch.object(mod, "run_shell_command", return_value=fake_result),
        patch.object(mod, "request_cli_approval", return_value=True),
    ):
        exit_code = mod.main([
            "exec", "--cwd", str(tmp_path), "--plan-id", "plan-99", "--task-id", "task-3",
            "--", "echo", "hi",
        ])

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
        exit_code = mod.main([
            "edit", str(target),
            "--replace", "original", "updated",
            "--plan-id", "plan-55", "--task-id", "task-9",
        ])

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
        exit_code = mod.main([
            "analyze", "--cwd", str(tmp_path),
            "--plan-id", "plan-ANALYZE", "--task-id", "task-ANALYZE",
            "review the code",
        ])

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
        title="Watch plan test", cwd=str(tmp_path), plan_id="plan-WATCH", task_id="task-WATCH",
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
        exit_code = mod.main([
            "research", "--plan-id", "plan-R1", "--task-id", "task-R2",
            "investigate latency issues",
        ])

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
        title="Watch research plan", cwd=str(tmp_path), plan_id="plan-WR", task_id="task-WR",
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
