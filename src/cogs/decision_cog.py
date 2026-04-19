"""Decision workflows: role-weighted polls, logs, and role-aware summaries."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from decision_workflows import (
    DecisionStore,
    DecisionVote,
    compute_weighted_outcome,
    parse_role_weights,
    role_aware_summary,
)

from .poll_cog import NUMBER_EMOJIS

log = logging.getLogger("openclaw.decision_cog")
decision_store = DecisionStore()


def _parse_options(options: str) -> list[str]:
    return [o.strip() for o in options.split(",") if o.strip()]


class DecisionCog(commands.GroupCog, group_name="decision", group_description="Decision polls and logs"):
    """Decision-making workflow commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="poll", description="Create a decision poll with optional role weighting")
    @app_commands.describe(
        question="Decision question",
        options="Comma-separated options (2-10)",
        duration="Auto-close duration in minutes (default: 30)",
        role_weights="Optional role weights, e.g. PM:2,Eng:1.5,QA:1.2",
    )
    async def decision_poll(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration: int = 30,
        role_weights: str = "",
    ) -> None:
        choices = _parse_options(options)
        if len(choices) < 2 or len(choices) > 10:
            await interaction.response.send_message("❌ Provide 2-10 options.", ephemeral=True)
            return

        if duration < 1 or duration > 1440:
            await interaction.response.send_message("❌ Duration must be between 1 and 1440 minutes.", ephemeral=True)
            return

        weights = parse_role_weights(role_weights)
        embed = discord.Embed(title=f"🗳️ Decision Poll: {question}", color=discord.Color.blurple())
        embed.description = "\n".join(f"{NUMBER_EMOJIS[i]} {choice}" for i, choice in enumerate(choices))
        if weights:
            human = ", ".join(f"{k}:{v:g}" for k, v in weights.items())
            embed.add_field(name="Role weights", value=human, inline=False)
        embed.set_footer(text=f"Closes in {duration} minutes • one vote per participant")

        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(choices)):
            await msg.add_reaction(NUMBER_EMOJIS[i])

        asyncio.create_task(
            self._close_poll(
                interaction=interaction,
                poll_message_id=msg.id,
                question=question,
                choices=choices,
                duration=duration,
                role_weights=weights,
            )
        )

    async def _close_poll(
        self,
        *,
        interaction: discord.Interaction,
        poll_message_id: int,
        question: str,
        choices: list[str],
        duration: int,
        role_weights: dict[str, float],
    ) -> None:
        await asyncio.sleep(duration * 60)
        channel = interaction.channel
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(poll_message_id)
        except Exception as exc:  # broad: intentional
            log.debug("Decision poll fetch failed: %s", exc)
            return

        votes: list[DecisionVote] = []
        guild = interaction.guild
        for i, choice in enumerate(choices):
            reaction = discord.utils.get(msg.reactions, emoji=NUMBER_EMOJIS[i])
            if reaction is None:
                continue
            async for user in reaction.users():
                if user.bot:
                    continue
                role_names: list[str] = []
                if guild:
                    member = guild.get_member(user.id)
                    if member is None:
                        try:
                            member = await guild.fetch_member(user.id)
                        except Exception:  # broad: intentional
                            member = None
                    if member:
                        role_names = [r.name for r in member.roles if r.name != "@everyone"]
                votes.append(
                    DecisionVote(user_id=user.id, user_name=user.display_name, option_index=i, roles=role_names)
                )

        outcome = compute_weighted_outcome(question=question, options=choices, votes=votes, role_weights=role_weights)

        context_channel_id = getattr(channel, "id", None)
        context_channel_name = getattr(channel, "name", None)
        context_thread_id = None
        context_thread_name = None
        if isinstance(channel, discord.Thread):
            context_thread_id = channel.id
            context_thread_name = channel.name
            context_channel_id = channel.parent_id
            context_channel_name = channel.parent.name if channel.parent else context_channel_name

        decision_id = decision_store.log_decision(
            outcome,
            channel_id=context_channel_id,
            channel_name=context_channel_name,
            thread_id=context_thread_id,
            thread_name=context_thread_name,
            poll_message_id=poll_message_id,
            created_by=interaction.user.id if interaction.user else None,
        )

        result = discord.Embed(
            title=f"✅ Decision Recorded #{decision_id}",
            description=f"**{question}**",
            color=discord.Color.green(),
        )
        lines = []
        for option, weighted, raw in zip(outcome["options"], outcome["weighted_totals"], outcome["raw_totals"]):
            lines.append(f"• {option}: **{weighted}** weighted ({raw} votes)")
        result.add_field(name="Outcome", value="\n".join(lines), inline=False)
        result.add_field(
            name="Winner",
            value=f"{outcome['winner_option']} (weighted {outcome['winner_weighted_score']})",
            inline=False,
        )
        result.set_footer(text=f"Participants: {outcome['participant_count']}")
        await channel.send(embed=result)

    @app_commands.command(name="recent", description="Show recent logged decisions")
    @app_commands.describe(limit="How many decisions to show (1-20)", current_channel_only="Filter to this channel")
    async def decision_recent(
        self,
        interaction: discord.Interaction,
        limit: int = 5,
        current_channel_only: bool = True,
    ) -> None:
        if limit < 1 or limit > 20:
            await interaction.response.send_message("❌ Limit must be between 1 and 20.", ephemeral=True)
            return

        channel_id = interaction.channel_id if current_channel_only else None
        rows = decision_store.list_recent(limit=limit, channel_id=channel_id)
        if not rows:
            await interaction.response.send_message("No decisions logged yet.", ephemeral=True)
            return

        embed = discord.Embed(title="🧾 Recent Decisions", color=discord.Color.teal())
        lines = []
        for row in rows:
            rel = f"<t:{int(row['created_at'])}:R>"
            lines.append(
                f"**#{row['id']}** {row['question']}\n"
                f"Winner: **{row['winner_option']}** ({row['winner_weighted_score']}) • {rel}"
            )
        embed.description = "\n\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="summary", description="Get a role-aware summary for a logged decision")
    @app_commands.describe(
        decision_id="Decision ID from /decision recent",
        audience="Summary style focus",
    )
    @app_commands.choices(
        audience=[
            app_commands.Choice(name="General", value="general"),
            app_commands.Choice(name="PM", value="pm"),
            app_commands.Choice(name="Engineering", value="eng"),
            app_commands.Choice(name="QA", value="qa"),
        ]
    )
    async def decision_summary(
        self,
        interaction: discord.Interaction,
        decision_id: int,
        audience: app_commands.Choice[str],
    ) -> None:
        decision = decision_store.get_decision(decision_id)
        if decision is None:
            await interaction.response.send_message(f"❌ Decision #{decision_id} not found.", ephemeral=True)
            return

        summary = role_aware_summary(decision, audience=audience.value)
        embed = discord.Embed(
            title=f"🧠 Decision Summary #{decision_id} ({audience.name})",
            description=summary,
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DecisionCog(bot))
