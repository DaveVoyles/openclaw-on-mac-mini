"""
Poll Cog — reaction-based voting with auto-close and results tally.
Commands: /poll
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


class PollCog(commands.Cog):
    """Create polls for voting with automatic result tallying."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Create a poll for voting")
    @app_commands.describe(
        question="The poll question",
        options="Comma-separated options (e.g., 'Pizza, Tacos, Sushi')",
        duration="Duration in minutes before auto-closing (default: 60)",
    )
    async def poll_cmd(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration: int = 60,
    ):
        choices = [o.strip() for o in options.split(",") if o.strip()]
        if len(choices) < 2 or len(choices) > 10:
            await interaction.response.send_message(
                "❌ Provide 2-10 options", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📊 {question}", color=discord.Color.purple()
        )
        desc = "\n".join(
            f"{NUMBER_EMOJIS[i]} {choice}" for i, choice in enumerate(choices)
        )
        embed.description = desc
        embed.set_footer(text=f"Poll closes in {duration} minutes • React to vote!")

        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()

        for i in range(len(choices)):
            await msg.add_reaction(NUMBER_EMOJIS[i])

        async def close_poll():
            await asyncio.sleep(duration * 60)
            try:
                msg_updated = await interaction.channel.fetch_message(msg.id)
                results = []
                for i, choice in enumerate(choices):
                    reaction = discord.utils.get(
                        msg_updated.reactions, emoji=NUMBER_EMOJIS[i]
                    )
                    count = (reaction.count - 1) if reaction else 0
                    results.append((choice, count))

                results.sort(key=lambda x: x[1], reverse=True)
                winner = results[0]
                max_votes = max(r[1] for r in results) if results else 0

                result_embed = discord.Embed(
                    title=f"📊 Poll Results: {question}",
                    color=discord.Color.green(),
                )
                result_lines = []
                for choice, count in results:
                    bar = "█" * count + "░" * (max_votes - count)
                    result_lines.append(f"{choice}: {bar} ({count} votes)")
                result_embed.description = "\n".join(result_lines)
                result_embed.set_footer(
                    text=f"🏆 Winner: {winner[0]} ({winner[1]} votes)"
                )

                await interaction.channel.send(embed=result_embed)
            except Exception as e:  # broad: intentional
                log.debug("Poll close failed: %s", e)

        asyncio.create_task(close_poll())


async def setup(bot: commands.Bot):
    await bot.add_cog(PollCog(bot))
