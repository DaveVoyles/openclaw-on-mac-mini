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
_CURRENT_USER_ID: ContextVar[str | None] = ContextVar(
    "openclaw_current_user_id",
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
def request_context(*, channel_id: int | None = None, user_id: str | None = None):
    """Bind the active Discord channel and user for the current request/tool call."""
    channel_token = None
    user_token = None
    if channel_id is not None:
        channel_token = _CURRENT_CHANNEL_ID.set(channel_id)
    if user_id is not None:
        user_token = _CURRENT_USER_ID.set(user_id)
    try:
        yield
    finally:
        if channel_token is not None:
            _CURRENT_CHANNEL_ID.reset(channel_token)
        if user_token is not None:
            _CURRENT_USER_ID.reset(user_token)


def get_current_channel_id() -> int | None:
    """Return the active Discord channel bound to the current request, if any."""
    return _CURRENT_CHANNEL_ID.get()


def set_current_user_id(user_id: str) -> None:
    """Set the current user ID for the request context."""
    _CURRENT_USER_ID.set(user_id)


def get_current_user_id() -> str | None:
    """Return the active Discord user ID bound to the current request, if any."""
    return _CURRENT_USER_ID.get()
