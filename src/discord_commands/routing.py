"""Routing info slash command: /routing."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from ._helpers import require_auth

log = logging.getLogger("openclaw")


def _register_routing_commands(bot: commands.Bot) -> None:
    """Register /routing."""

    @bot.tree.command(
        name="routing",
        description="Show current LLM routing configuration and mini-model fast-path info",
    )
    @require_auth
    async def routing_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Lazy imports to avoid circular deps at module load time
        try:
            from llm.providers import PROVIDER_FALLBACK_CHAIN
        except (ImportError, AttributeError):
            PROVIDER_FALLBACK_CHAIN = ["(unavailable)"]

        try:
            import model_routing_policy as _mrp

            mini_model: str = getattr(_mrp, "_MINI_MODEL", os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini"))
            mini_threshold: int = getattr(
                _mrp, "_MINI_TOKEN_THRESHOLD", int(os.getenv("MINI_TOKEN_THRESHOLD", "25"))
            )
            mini_max: int = getattr(
                _mrp, "MINI_MODEL_MAX_TOKENS", int(os.getenv("MINI_MODEL_MAX_TOKENS", "50"))
            )
        except (ImportError, AttributeError):
            mini_model = os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini")
            mini_threshold = int(os.getenv("MINI_TOKEN_THRESHOLD", "25"))
            mini_max = int(os.getenv("MINI_MODEL_MAX_TOKENS", "50"))

        try:
            from config import cfg

            routing_profile: str = cfg.routing_profile or os.getenv("ROUTING_PROFILE", "copilot-first")
        except (ImportError, AttributeError, OSError):
            routing_profile = os.getenv("ROUTING_PROFILE", "copilot-first")

        embed = discord.Embed(
            title="🔀 LLM Routing Configuration",
            color=0x5865F2,
        )
        embed.add_field(
            name="Routing Profile",
            value=f"`{routing_profile}`",
            inline=True,
        )
        embed.add_field(
            name="Fallback Chain",
            value=" → ".join(f"`{p}`" for p in PROVIDER_FALLBACK_CHAIN),
            inline=True,
        )
        embed.add_field(
            name="Mini-Model Fast-Path",
            value=(
                f"**Model:** `{mini_model}`\n"
                f"**Threshold:** ≤ {mini_threshold} tokens (select_auto_route gate)\n"
                f"**Max tokens:** {mini_max} words (copilot_model_for_message gate)\n"
                f"Short queries with no tools/context hit this path automatically."
            ),
            inline=False,
        )
        embed.set_footer(text="Set OPENAI_MINI_MODEL / MINI_TOKEN_THRESHOLD / ROUTING_PROFILE env vars to override.")

        await interaction.followup.send(embed=embed, ephemeral=True)
