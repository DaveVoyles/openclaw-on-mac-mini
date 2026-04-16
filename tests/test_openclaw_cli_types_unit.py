"""Unit tests for openclaw_cli_types.py — dataclasses and ChatCommandRegistry."""
from __future__ import annotations

import pytest

from openclaw_cli_types import (
    AskResponse,
    ChatCommandContext,
    ChatCommandRegistry,
    CliConfig,
    LocalLinkValidation,
    SlashCommand,
)


# ---------------------------------------------------------------------------
# AskResponse
# ---------------------------------------------------------------------------

class TestAskResponse:
    def test_basic_construction(self):
        resp = AskResponse(response="Hello", model="gpt-4", tokens=42, raw={"id": "r1"})
        assert resp.response == "Hello"
        assert resp.model == "gpt-4"
        assert resp.tokens == 42
        assert resp.raw == {"id": "r1"}

    def test_empty_response(self):
        resp = AskResponse(response="", model="auto", tokens=0, raw={})
        assert resp.response == ""
        assert resp.tokens == 0


# ---------------------------------------------------------------------------
# LocalLinkValidation
# ---------------------------------------------------------------------------

class TestLocalLinkValidation:
    def test_available_link(self):
        link = LocalLinkValidation(
            kind="plan", item_id="p-1", available=True, exists=True, source="db"
        )
        assert link.available is True
        assert link.exists is True
        assert link.kind == "plan"

    def test_missing_link(self):
        link = LocalLinkValidation(kind="task", item_id="t-99", available=False)
        assert link.available is False
        assert link.exists is False  # default
        assert link.source == ""     # default

    def test_frozen_dataclass(self):
        link = LocalLinkValidation(kind="task", item_id="t-1", available=True)
        with pytest.raises(Exception):
            link.kind = "plan"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CliConfig
# ---------------------------------------------------------------------------

class TestCliConfig:
    def test_required_fields(self):
        cfg = CliConfig(
            base_url="http://localhost:8765",
            token="secret",
            model="auto",
            timeout_seconds=120,
            user_name="alice",
            client_name="laptop",
        )
        assert cfg.base_url == "http://localhost:8765"
        assert cfg.model == "auto"

    def test_default_optional_fields(self):
        cfg = CliConfig(
            base_url="http://localhost:8765",
            token="t",
            model="gemini",
            timeout_seconds=60,
            user_name="bob",
            client_name="server",
        )
        assert cfg.output_json is False
        assert cfg.session_id == ""
        assert cfg.no_stream is False

    def test_custom_optional_fields(self):
        cfg = CliConfig(
            base_url="http://example.com",
            token="t",
            model="openai",
            timeout_seconds=30,
            user_name="u",
            client_name="c",
            output_json=True,
            session_id="sess-123",
            no_stream=True,
        )
        assert cfg.output_json is True
        assert cfg.session_id == "sess-123"
        assert cfg.no_stream is True


# ---------------------------------------------------------------------------
# ChatCommandContext
# ---------------------------------------------------------------------------

class TestChatCommandContext:
    def test_minimal_construction(self):
        ctx = ChatCommandContext(history=[], session_id="s1")
        assert ctx.history == []
        assert ctx.session_id == "s1"
        assert ctx.args == ""
        assert ctx.command_ok is True
        assert ctx.command_summary == ""

    def test_with_history(self):
        history = [{"role": "user", "content": "hello"}]
        ctx = ChatCommandContext(history=history, session_id="s2")
        assert ctx.history[0]["content"] == "hello"

    def test_mutable_fields(self):
        ctx = ChatCommandContext(history=[], session_id="s3")
        ctx.args = "some args"
        ctx.command_ok = False
        ctx.command_summary = "failed"
        assert ctx.args == "some args"
        assert ctx.command_ok is False


# ---------------------------------------------------------------------------
# SlashCommand
# ---------------------------------------------------------------------------

class TestSlashCommand:
    def test_basic_command(self):
        handler = lambda ctx: "ok"
        cmd = SlashCommand(name="help", description="Show help", handler=handler)
        assert cmd.name == "help"
        assert cmd.aliases == ()

    def test_command_with_aliases(self):
        handler = lambda ctx: "ok"
        cmd = SlashCommand(name="quit", description="Exit", handler=handler, aliases=("q", "exit"))
        assert "q" in cmd.aliases
        assert "exit" in cmd.aliases

    def test_handler_callable(self):
        result = []
        def handler(ctx):
            result.append(ctx.args)
            return "done"
        cmd = SlashCommand(name="test", description="Test cmd", handler=handler)
        ctx = ChatCommandContext(history=[], session_id="s", args="hello")
        assert cmd.handler(ctx) == "done"
        assert result == ["hello"]


# ---------------------------------------------------------------------------
# ChatCommandRegistry
# ---------------------------------------------------------------------------

class TestChatCommandRegistry:
    def _make_registry(self):
        return ChatCommandRegistry()

    def _make_cmd(self, name, result="ok", aliases=()):
        handler = lambda ctx: result
        return SlashCommand(name=name, description=f"{name} cmd", handler=handler, aliases=aliases)

    def test_empty_registry_dispatch_returns_none(self):
        reg = self._make_registry()
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("hello", ctx) is None

    def test_non_slash_returns_none(self):
        reg = self._make_registry()
        reg.register(self._make_cmd("help"))
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("help", ctx) is None

    def test_dispatch_known_command(self):
        reg = self._make_registry()
        reg.register(self._make_cmd("help"))
        ctx = ChatCommandContext(history=[], session_id="s")
        result = reg.dispatch("/help", ctx)
        assert result == "ok"

    def test_dispatch_unknown_command(self):
        reg = self._make_registry()
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("/unknown", ctx) is None

    def test_dispatch_bare_slash_returns_none(self):
        reg = self._make_registry()
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("/", ctx) is None

    def test_dispatch_sets_args(self):
        received_args = []

        def handler(ctx):
            received_args.append(ctx.args)
            return "done"

        cmd = SlashCommand(name="ask", description="ask", handler=handler)
        reg = self._make_registry()
        reg.register(cmd)
        ctx = ChatCommandContext(history=[], session_id="s")
        reg.dispatch("/ask what is 2+2", ctx)
        assert received_args == ["what is 2+2"]

    def test_dispatch_no_args_sets_empty_string(self):
        received_args = []

        def handler(ctx):
            received_args.append(ctx.args)
            return "done"

        cmd = SlashCommand(name="quit", description="quit", handler=handler)
        reg = self._make_registry()
        reg.register(cmd)
        ctx = ChatCommandContext(history=[], session_id="s")
        reg.dispatch("/quit", ctx)
        assert received_args == [""]

    def test_dispatch_via_alias(self):
        reg = self._make_registry()
        reg.register(self._make_cmd("quit", result="bye", aliases=("q",)))
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("/q", ctx) == "bye"

    def test_dispatch_resets_command_ok(self):
        reg = self._make_registry()
        reg.register(self._make_cmd("help"))
        ctx = ChatCommandContext(history=[], session_id="s")
        ctx.command_ok = False
        reg.dispatch("/help", ctx)
        assert ctx.command_ok is True

    def test_list_commands_empty(self):
        reg = self._make_registry()
        assert reg.list_commands() == []

    def test_list_commands_returns_primary_only(self):
        reg = self._make_registry()
        cmd_a = self._make_cmd("help", aliases=("h",))
        cmd_b = self._make_cmd("quit")
        reg.register(cmd_a)
        reg.register(cmd_b)
        cmds = reg.list_commands()
        assert len(cmds) == 2
        assert cmds[0].name == "help"
        assert cmds[1].name == "quit"

    def test_register_multiple_aliases(self):
        reg = self._make_registry()
        cmd = self._make_cmd("version", aliases=("v", "ver"))
        reg.register(cmd)
        ctx = ChatCommandContext(history=[], session_id="s")
        assert reg.dispatch("/v", ctx) == "ok"
        assert reg.dispatch("/ver", ctx) == "ok"
        assert reg.dispatch("/version", ctx) == "ok"
