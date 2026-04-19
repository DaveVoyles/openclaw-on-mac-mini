"""Channel profile commands for per-channel/thread response defaults."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from runtime_state import (
    clear_channel_profile,
    get_channel_profile,
    get_channel_profile_defaults,
    get_channel_profile_usage_signals,
    list_channel_profile_recommendations,
    refresh_channel_profile_recommendations,
    set_channel_profile,
    update_channel_profile_recommendation,
)

log = logging.getLogger(__name__)


def _resolve_scope(
    interaction: discord.Interaction,
    scope: str,
) -> tuple[int, int | None, str] | None:
    channel = interaction.channel
    if interaction.channel_id is None or channel is None:
        return None

    if isinstance(channel, discord.Thread):
        base_channel_id = channel.parent_id or interaction.channel_id
        thread_id = channel.id
        if scope == "channel":
            return base_channel_id, None, "channel"
        return base_channel_id, thread_id, "thread"

    if scope == "thread":
        return None
    return interaction.channel_id, None, "channel"


class ChannelProfileCog(commands.GroupCog, group_name="channel-profile"):
    """Manage per-channel/thread formatting/report defaults."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="show", description="Show active profile defaults for this channel/thread")
    @app_commands.describe(scope="auto, channel, or thread scope")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="auto", value="auto"),
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="thread", value="thread"),
        ]
    )
    async def show(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] | None = None,
    ) -> None:
        selected_scope = (scope.value if scope else "auto").lower()
        resolved = _resolve_scope(interaction, selected_scope)
        if resolved is None:
            await interaction.response.send_message(
                "❌ Thread scope is only available inside a Discord thread.",
                ephemeral=True,
            )
            return
        channel_id, thread_id, scope_label = resolved
        profile = get_channel_profile(channel_id, thread_id=thread_id)
        defaults = get_channel_profile_defaults()
        lines = []
        for key, value in profile.items():
            marker = " (default)" if value == defaults.get(key, value) else ""
            lines.append(f"• **{key.replace('_', ' ')}:** `{value}`{marker}")
        await interaction.response.send_message(
            f"🧭 **{scope_label.title()} profile**\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="recommendations", description="Show profile recommendations for this scope")
    @app_commands.describe(scope="auto, channel, or thread scope", include_history="Include rejected/reverted history")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="auto", value="auto"),
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="thread", value="thread"),
        ]
    )
    async def recommendations(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] | None = None,
        include_history: bool = False,
    ) -> None:
        selected_scope = (scope.value if scope else "auto").lower()
        resolved = _resolve_scope(interaction, selected_scope)
        if resolved is None:
            await interaction.response.send_message(
                "❌ Thread scope is only available inside a Discord thread.",
                ephemeral=True,
            )
            return
        channel_id, thread_id, scope_label = resolved
        refresh_channel_profile_recommendations(channel_id, thread_id=thread_id)
        recs = list_channel_profile_recommendations(
            channel_id,
            thread_id=thread_id,
            include_history=include_history,
        )
        signals = get_channel_profile_usage_signals(channel_id, thread_id=thread_id)
        signal_summary = ", ".join(f"{k}={v}" for k, v in signals.items() if v)
        if not signal_summary:
            signal_summary = "none"
        if not recs:
            await interaction.response.send_message(
                f"🧠 **{scope_label.title()} recommendations**\nNo active recommendations yet.\nSignals: `{signal_summary}`",
                ephemeral=True,
            )
            return
        lines = [
            (
                f"• **#{rec['recommendation_id']}** `{rec['profile_field']}` → `{rec['recommended_value']}` "
                f"({rec['status']}, confidence {rec['confidence']:.2f})\n"
                f"  ↳ {rec['reason']}"
            )
            for rec in recs[:10]
        ]
        await interaction.response.send_message(
            f"🧠 **{scope_label.title()} recommendations**\nSignals: `{signal_summary}`\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(
        name="recommendation-action", description="Approve/reject/apply/revert a recommendation by ID"
    )
    @app_commands.describe(recommendation_id="Recommendation ID", action="Action to perform")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="approve", value="approve"),
            app_commands.Choice(name="reject", value="reject"),
            app_commands.Choice(name="apply", value="apply"),
            app_commands.Choice(name="revert", value="revert"),
        ]
    )
    async def recommendation_action(
        self,
        interaction: discord.Interaction,
        recommendation_id: int,
        action: app_commands.Choice[str],
    ) -> None:
        try:
            updated = update_channel_profile_recommendation(
                recommendation_id,
                action=action.value,
                actor=str(interaction.user),
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            (
                f"✅ Recommendation **#{updated['recommendation_id']}** is now `{updated['status']}`.\n"
                f"• `{updated['profile_field']}` → `{updated['recommended_value']}`\n"
                f"• Confidence: `{updated['confidence']:.2f}`"
            ),
            ephemeral=True,
        )

    @app_commands.command(name="set", description="Set profile defaults for this channel/thread")
    @app_commands.describe(
        scope="auto, channel, or thread scope",
        tone="Response tone",
        table_style="Table formatting style",
        emoji_level="Emoji density",
        report_depth="Report detail level",
        source_strictness="How strict source-grounding should be",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="auto", value="auto"),
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="thread", value="thread"),
        ],
        tone=[
            app_commands.Choice(name="neutral", value="neutral"),
            app_commands.Choice(name="concise", value="concise"),
            app_commands.Choice(name="analytical", value="analytical"),
            app_commands.Choice(name="friendly", value="friendly"),
        ],
        table_style=[
            app_commands.Choice(name="discord", value="discord"),
            app_commands.Choice(name="copy-safe", value="copy-safe"),
        ],
        emoji_level=[
            app_commands.Choice(name="none", value="none"),
            app_commands.Choice(name="light", value="light"),
            app_commands.Choice(name="rich", value="rich"),
        ],
        report_depth=[
            app_commands.Choice(name="brief", value="brief"),
            app_commands.Choice(name="standard", value="standard"),
            app_commands.Choice(name="detailed", value="detailed"),
        ],
        source_strictness=[
            app_commands.Choice(name="balanced", value="balanced"),
            app_commands.Choice(name="strict", value="strict"),
        ],
    )
    async def set(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] | None = None,
        tone: app_commands.Choice[str] | None = None,
        table_style: app_commands.Choice[str] | None = None,
        emoji_level: app_commands.Choice[str] | None = None,
        report_depth: app_commands.Choice[str] | None = None,
        source_strictness: app_commands.Choice[str] | None = None,
    ) -> None:
        selected_scope = (scope.value if scope else "auto").lower()
        resolved = _resolve_scope(interaction, selected_scope)
        if resolved is None:
            await interaction.response.send_message(
                "❌ Thread scope is only available inside a Discord thread.",
                ephemeral=True,
            )
            return
        if not any([tone, table_style, emoji_level, report_depth, source_strictness]):
            await interaction.response.send_message(
                "❌ Provide at least one setting to update.",
                ephemeral=True,
            )
            return

        channel_id, thread_id, scope_label = resolved
        profile = set_channel_profile(
            channel_id,
            thread_id=thread_id,
            tone=tone.value if tone else None,
            table_style=table_style.value if table_style else None,
            emoji_level=emoji_level.value if emoji_level else None,
            report_depth=report_depth.value if report_depth else None,
            source_strictness=source_strictness.value if source_strictness else None,
        )
        lines = [f"• **{k.replace('_', ' ')}:** `{v}`" for k, v in profile.items()]
        await interaction.response.send_message(
            f"✅ Updated **{scope_label}** profile\n" + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="clear", description="Clear profile override at this scope")
    @app_commands.describe(scope="channel or thread scope to clear")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="thread", value="thread"),
        ]
    )
    async def clear(self, interaction: discord.Interaction, scope: app_commands.Choice[str]) -> None:
        resolved = _resolve_scope(interaction, scope.value)
        if resolved is None:
            await interaction.response.send_message(
                "❌ Thread scope is only available inside a Discord thread.",
                ephemeral=True,
            )
            return
        channel_id, thread_id, scope_label = resolved
        clear_channel_profile(channel_id, thread_id=thread_id)
        await interaction.response.send_message(
            f"🧹 Cleared **{scope_label}** profile override.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChannelProfileCog(bot))
