"""
Translate Cog — Uses Gemini (via llm/chat.py) for multilingual translation.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("openclaw")


class TranslateCog(commands.Cog, name="Translate"):
    """Translate text to any language using Gemini."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="translate", description="Translate text to another language")
    @app_commands.describe(
        text="The text to translate",
        to="Target language (e.g., Spanish, French, Japanese, German)",
    )
    async def translate_cmd(self, interaction: discord.Interaction, text: str, to: str) -> None:
        await interaction.response.defer()
        try:
            from llm.chat import chat

            prompt = (
                f"Translate the following text to {to}. "
                "Respond with ONLY the translation, no explanation or notes.\n\n"
                f"Text: {text}"
            )
            result, _, _ = await chat(prompt)

            embed = discord.Embed(title=f"🌐 Translation → {to}", color=discord.Color.blue())
            embed.add_field(name="Original", value=text[:1000], inline=False)
            embed.add_field(name=f"Translated ({to})", value=result[:1000], inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception("Translation failed")
            await interaction.followup.send(f"❌ Translation failed: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TranslateCog(bot))
