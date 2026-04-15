"""Shared dataclasses and registry types for OpenClaw CLI.

This is a leaf module with zero dependencies on other openclaw_cli_* modules.
All other modules may safely import from here without circular-import risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AskResponse:
    """Structured response from the OpenClaw ask API."""

    response: str
    model: str
    tokens: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class LocalLinkValidation:
    """Result of checking a plan/task identifier against local on-disk sources."""

    kind: str
    item_id: str
    available: bool
    exists: bool = False
    source: str = ""
    summary: str = ""


@dataclass
class CliConfig:
    """Resolved runtime configuration for a CLI invocation."""

    base_url: str
    token: str
    model: str
    timeout_seconds: int
    user_name: str
    client_name: str
    output_json: bool = False
    session_id: str = ""
    no_stream: bool = False


@dataclass
class ChatCommandContext:
    """Mutable context passed to every slash-command handler."""

    history: list[dict[str, str]]
    session_id: str
    args: str = ""  # text after the command name, stripped
    config: Any = None  # CliConfig instance when running inside run_chat
    route_metadata: dict[str, Any] | None = None
    command_ok: bool = True
    command_summary: str = ""


@dataclass
class SlashCommand:
    """A single registered slash command with optional aliases."""

    name: str
    description: str
    handler: Callable[[ChatCommandContext], str]
    aliases: tuple[str, ...] = ()


class ChatCommandRegistry:
    """Maps slash-command names (without the leading /) to handlers."""

    def __init__(self) -> None:
        self._commands: list[SlashCommand] = []
        self._lookup: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands.append(cmd)
        self._lookup[cmd.name] = cmd
        for alias in cmd.aliases:
            self._lookup[alias] = cmd

    def dispatch(self, text: str, ctx: ChatCommandContext) -> str | None:
        """Route *text* to a handler if it starts with '/'.

        Returns a sentinel string (_CMD_CONTINUE or _CMD_QUIT) when handled,
        or None when the text is not a recognised slash command.

        Text after the command name is placed in ``ctx.args`` so handlers can
        accept optional arguments without needing separate registry entries.
        """
        if not text.startswith("/"):
            return None
        parts = text[1:].split(maxsplit=1)
        cmd_name = parts[0] if parts else ""
        if not cmd_name:
            return None
        cmd = self._lookup.get(cmd_name)
        if cmd is None:
            return None
        ctx.args = parts[1] if len(parts) > 1 else ""
        ctx.command_ok = True
        ctx.command_summary = ""
        return cmd.handler(ctx)

    def list_commands(self) -> list[SlashCommand]:
        """Return the primary commands in registration order."""
        return list(self._commands)
