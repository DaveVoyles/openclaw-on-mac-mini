"""Context menu (right-click) commands."""

import io

import discord
from discord import app_commands
from discord.ext import commands

from bot_formatting import (
    build_brief_detail_bundle,
    format_markdown_for_discord,
    format_tables_for_copy,
    split_mobile_safe_bundle,
)
from cogs.sms_cog import SMSSendConfirmView
from copy_workflow_formatter import build_copy_workflow_payload as _build_copy_workflow_payload
from sms_ux import format_sms_error, validate_sms_body

from ._helpers import _is_allowed


def _register_context_menus(bot: commands.Bot) -> None:
    """Register standalone context-menu commands."""

    async def _send_to_sms(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        raw = (message.content or "").strip()
        if not raw:
            await interaction.response.send_message(
                "❌ Selected message has no text content to send via SMS.",
                ephemeral=True,
            )
            return

        try:
            cleaned = validate_sms_body(raw)
            preview = cleaned if len(cleaned) <= 220 else f"{cleaned[:220]}…"
            embed = discord.Embed(
                title="📲 Send Selected Message to SMS?",
                description=f"```text\n{preview}\n```",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Confirm to send to your configured phone.")
            await interaction.response.send_message(
                embed=embed,
                view=SMSSendConfirmView(interaction.user.id, cleaned),
                ephemeral=True,
            )
        except Exception as exc:  # broad: intentional
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)

    async def _copy_workflow_context(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        payload = _build_copy_workflow_payload((message.content or "").strip())
        if not payload:
            await interaction.response.send_message(
                "❌ Selected message has no text content to export.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"📋 Copy-ready export (mobile-friendly):\n```text\n{payload}\n```",
            ephemeral=True,
        )

    async def _package_copy_safe_context(
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        payload = _build_copy_workflow_payload((message.content or "").strip())
        if not payload:
            await interaction.response.send_message(
                "❌ Selected message has no text content to package.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"📋 Copy-safe text bundle:\n```text\n{payload}\n```",
            ephemeral=True,
        )

    async def _package_artifact_context(
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        text = (message.content or "").strip()
        artifact_text = format_tables_for_copy(format_markdown_for_discord(text)).strip()
        if not artifact_text:
            await interaction.response.send_message(
                "❌ Selected message has no text content to package.",
                ephemeral=True,
            )
            return

        files: list[discord.File] = [
            discord.File(io.BytesIO(artifact_text.encode("utf-8")), filename="report-package.txt")
        ]
        try:
            from table_renderer import extract_table_text, render_table_image, should_render_table_image

            table_text = extract_table_text(text)
            if table_text and should_render_table_image(table_text):
                img_bytes = render_table_image(table_text)
                if img_bytes:
                    files.append(discord.File(io.BytesIO(img_bytes), filename="report-table.png"))
        except Exception:  # broad: intentional
            pass

        await interaction.response.send_message(
            "📦 Attached artifact package for selected message.",
            files=files,
            ephemeral=True,
        )

    async def _package_brief_detail_context(
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        payload = build_brief_detail_bundle((message.content or "").strip())
        if not payload:
            await interaction.response.send_message(
                "❌ Selected message has no text content to package.",
                ephemeral=True,
            )
            return

        chunks = split_mobile_safe_bundle(payload)
        for idx, chunk in enumerate(chunks, start=1):
            suffix = f" (part {idx}/{len(chunks)})" if len(chunks) > 1 else ""
            if idx == 1:
                await interaction.response.send_message(
                    f"🧾 Brief+Detail package{suffix}\n{chunk}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"🧾 Brief+Detail package{suffix}\n{chunk}",
                    ephemeral=True,
                )

    bot.tree.add_command(app_commands.ContextMenu(name="Send to SMS", callback=_send_to_sms))
    bot.tree.add_command(app_commands.ContextMenu(name="Copy Workflow Context", callback=_copy_workflow_context))
    bot.tree.add_command(
        app_commands.ContextMenu(name="Package Report: Copy-safe", callback=_package_copy_safe_context)
    )
    bot.tree.add_command(app_commands.ContextMenu(name="Package Report: Artifact", callback=_package_artifact_context))
    bot.tree.add_command(
        app_commands.ContextMenu(name="Package Report: Brief+Detail", callback=_package_brief_detail_context)
    )
