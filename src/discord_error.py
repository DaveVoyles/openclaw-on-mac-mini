"""Shared Discord error formatting for consistent error UX across all cogs."""

import logging
import uuid

import discord

log = logging.getLogger(__name__)

ERROR_CATEGORIES = {
    "timeout": ("⏱️", "The operation timed out. Please try again."),
    "rate_limit": ("🐢", "Rate limit reached. Please wait a moment before trying again."),
    "auth": ("🔒", "You don't have access to this command. Run /whoami for your permission level."),
    "tool_failure": ("🔧", "A tool failed to respond. Please try again."),
    "provider": ("🤖", "The AI provider returned an error. Please try again."),
    "general": ("❌", "Something went wrong. Please try again."),
}


def classify_error(e):
    """Classify exception into an error category."""
    msg = str(e).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "rate" in msg and "limit" in msg:
        return "rate_limit"
    if "permission" in msg or "forbidden" in msg or "unauthorized" in msg:
        return "auth"
    if "tool" in msg or "function" in msg:
        return "tool_failure"
    if "provider" in msg or "model" in msg or "llm" in msg:
        return "provider"
    return "general"


def build_error_embed(e, *, context="", category=None):
    """Return a standardized ephemeral-ready error embed."""
    category = category or classify_error(e)
    emoji, desc = ERROR_CATEGORIES.get(category, ERROR_CATEGORIES["general"])
    trace_id = uuid.uuid4().hex[:8]
    safe_detail = discord.utils.escape_markdown(str(e))[:200]
    embed = discord.Embed(
        title=f"{emoji} Error",
        description=desc,
        color=discord.Color.red(),
    )
    if context:
        embed.add_field(name="Command", value=context, inline=True)
    embed.add_field(name="Detail", value=f"`{safe_detail}`", inline=False)
    embed.set_footer(text=f"Trace ID: {trace_id}")
    return embed
