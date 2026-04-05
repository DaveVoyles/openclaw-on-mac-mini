"""Small runtime registry for objects that need cross-module access."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from discord.ext import commands

_BOT: commands.Bot | None = None
_CURRENT_CHANNEL_ID: ContextVar[int | None] = ContextVar(
    "openclaw_current_channel_id",
    default=None,
)


def set_bot(bot: commands.Bot) -> None:
    """Register the live Discord bot instance for runtime helpers."""
    global _BOT
    _BOT = bot


def get_bot() -> commands.Bot | None:
    """Return the active Discord bot instance if one has been registered."""
    return _BOT


@contextmanager
def request_context(*, channel_id: int | None = None):
    """Bind the active Discord channel for the current request/tool call."""
    token = None
    if channel_id is not None:
        token = _CURRENT_CHANNEL_ID.set(channel_id)
    try:
        yield
    finally:
        if token is not None:
            _CURRENT_CHANNEL_ID.reset(token)


def get_current_channel_id() -> int | None:
    """Return the active Discord channel bound to the current request, if any."""
    return _CURRENT_CHANNEL_ID.get()
