"""Shared helpers for discord_commands sub-modules."""

import functools
import logging
import os

import aiohttp
import discord

from constants import EMBED_DESC_LIMIT

log = logging.getLogger("openclaw")

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]


def _is_allowed(interaction: discord.Interaction) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return interaction.user.id in ALLOWED_USER_IDS


def require_auth(func):
    """Decorator that gates a slash-command handler behind the allow-list."""

    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.", ephemeral=True
            )
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def truncate_for_embed(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Module-level aiohttp session (reused for attachment downloads)
# ---------------------------------------------------------------------------

from http_session import SessionManager as _SessionManager

_sessions = _SessionManager(timeout=30, name="discord-commands-helpers")


async def _get_http_session() -> aiohttp.ClientSession:
    return await _sessions.get()
