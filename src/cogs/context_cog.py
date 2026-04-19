"""
Context Menu Cog — right-click message actions.
Adds: "Analyze with AI", "Save to Memory", "Research This"
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth
from discord_error import build_error_embed

log = logging.getLogger(__name__)


class ContextMenuCog(commands.Cog, name="ContextMenus"):
    """Right-click message context menu actions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Register context menus
        self.analyze_ctx = app_commands.ContextMenu(
            name="Analyze with AI",
            callback=self._analyze_message,
        )
        self.save_ctx = app_commands.ContextMenu(
            name="Save to Memory",
            callback=self._save_to_memory,
        )
        self.research_ctx = app_commands.ContextMenu(
            name="Research This",
            callback=self._research_message,
        )
        self.bot.tree.add_command(self.analyze_ctx)
        self.bot.tree.add_command(self.save_ctx)
        self.bot.tree.add_command(self.research_ctx)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.analyze_ctx.name, type=self.analyze_ctx.type)
        self.bot.tree.remove_command(self.save_ctx.name, type=self.save_ctx.type)
        self.bot.tree.remove_command(self.research_ctx.name, type=self.research_ctx.type)

    async def _analyze_message(self, interaction: discord.Interaction, message: discord.Message):
        """Right-click → Analyze with AI: send message content to LLM for analysis."""
        if not await require_auth(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        content = message.content or "(no text content)"
        if len(content) > 2000:
            content = content[:2000] + "…"

        try:
            from llm import chat

            prompt = (
                f"Analyze the following message. Summarize key points, identify any "
                f"action items, and note anything notable:\n\n{content}"
            )
            response, _, model = await asyncio.wait_for(chat(prompt), timeout=30)
            embed = discord.Embed(
                title="🔍 AI Analysis",
                description=response[:4000] if response else "No analysis produced.",
                color=discord.Color.purple(),
            )
            embed.set_footer(text=f"Analyzed via {model}")
            await interaction.followup.send(embed=embed, ephemeral=True)
            audit_log(interaction.user, "context_analyze", detail=content[:100])
        except Exception as e:  # broad: intentional
            log.exception("context_analyze failed")
            await interaction.followup.send(embed=build_error_embed(e, context="Analyze with AI"), ephemeral=True)

    async def _save_to_memory(self, interaction: discord.Interaction, message: discord.Message):
        """Right-click → Save to Memory: store message content as a memory fact."""
        if not await require_auth(interaction):
            return
        content = message.content or "(no text content)"
        if len(content) > 500:
            content = content[:500]

        try:
            from memory import store_memory

            await store_memory(content, source="context_menu")
            await interaction.response.send_message(
                f"📌 Saved to memory: *{content[:80]}{'…' if len(content) > 80 else ''}*",
                ephemeral=True,
            )
            audit_log(interaction.user, "context_save", detail=content[:100])
        except Exception as e:  # broad: intentional
            log.exception("context_save failed")
            await interaction.response.send_message(
                embed=build_error_embed(e, context="Save to Memory"), ephemeral=True
            )

    async def _research_message(self, interaction: discord.Interaction, message: discord.Message):
        """Right-click → Research This: run a research query on message content."""
        if not await require_auth(interaction):
            return
        await interaction.response.defer()

        content = message.content or "(no text content)"
        if len(content) > 500:
            content = content[:500]

        try:
            from llm import chat

            prompt = (
                f"Research the following topic thoroughly. Provide key facts, context, "
                f"and relevant details:\n\n{content}"
            )
            response, _, model = await asyncio.wait_for(chat(prompt), timeout=45)
            embed = discord.Embed(
                title="📊 Research Results",
                description=response[:4000] if response else "No results.",
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"Researched via {model}")
            await interaction.followup.send(embed=embed)
            audit_log(interaction.user, "context_research", detail=content[:100])
        except Exception as e:  # broad: intentional
            log.exception("context_research failed")
            await interaction.followup.send(embed=build_error_embed(e, context="Research This"), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ContextMenuCog(bot))
