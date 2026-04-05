"""Small runtime registry for objects that need cross-module access."""

from __future__ import annotations

from discord.ext import commands

_BOT: commands.Bot | None = None


def set_bot(bot: commands.Bot) -> None:
    """Register the live Discord bot instance for runtime helpers."""
    global _BOT
    _BOT = bot


def get_bot() -> commands.Bot | None:
    """Return the active Discord bot instance if one has been registered."""
    return _BOT
