"""SMS Cog — Discord-first one-tap SMS UX."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth
from sms_ux import (
    SMSVerificationUnavailableError,
    check_sms_verification,
    configure_sms_phone,
    format_sms_error,
    send_configured_sms,
    sms_prefs,
    start_sms_verification,
    status_snapshot,
    validate_sms_body,
)


class SMSSendConfirmView(discord.ui.View):
    """Confirmation UI before an outbound SMS is sent."""

    def __init__(self, requester_id: int, body: str) -> None:
        super().__init__(timeout=90)
        self.requester_id = requester_id
        self.body = body

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "❌ Only the original requester can confirm this SMS.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Send SMS", emoji="📲", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            result = await send_configured_sms(interaction.user.id, self.body)
            embed = discord.Embed(
                title="✅ SMS Sent",
                description=f"Delivered via **{result.provider}** (`{result.sid}`)",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
        except Exception as exc:  # broad: intentional
            await interaction.response.edit_message(content=format_sms_error(exc), embed=None, view=None)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="✖️ SMS cancelled.", embed=None, view=None)


class SMSCog(commands.GroupCog, group_name="sms"):
    """Discord-facing SMS flows: config, status, send, and verification."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        msg = str(error) if isinstance(error, app_commands.CheckFailure) else f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="config", description="Configure the phone number for one-tap Discord→SMS")
    @app_commands.describe(
        phone="Phone number in E.164 format (e.g., +15551234567)",
        send_verification="Send a verification code now",
    )
    @require_auth()
    async def config(
        self,
        interaction: discord.Interaction,
        phone: str,
        send_verification: bool = True,
    ) -> None:
        try:
            prefs = await configure_sms_phone(interaction.user.id, phone)
            if not send_verification:
                await interaction.response.send_message(
                    f"✅ SMS phone saved: `{prefs.phone_number}`. Run `/sms test` to verify.",
                    ephemeral=True,
                )
                return
            try:
                verification = await start_sms_verification(interaction.user.id)
                await interaction.response.send_message(
                    "✅ Phone saved.\n"
                    f"📨 Verification sent to `{prefs.phone_number}` via {verification.provider}.\n"
                    "Enter the code with `/sms test code:<code>`.",
                    ephemeral=True,
                )
            except SMSVerificationUnavailableError:
                prefs.is_verified = True
                await sms_prefs.update(prefs)
                await interaction.response.send_message(
                    "✅ Phone saved and marked verified (Twilio Verify is not configured).",
                    ephemeral=True,
                )
        except Exception as exc:  # broad: intentional
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)

    @app_commands.command(name="status", description="Show your SMS configuration and verification state")
    @require_auth()
    async def status(self, interaction: discord.Interaction) -> None:
        snap = status_snapshot(interaction.user.id)
        verified = "✅ verified" if snap["is_verified"] else "⚠️ not verified"
        description = (
            f"**Phone:** `{snap['masked_phone']}`\n"
            f"**Verification:** {verified}\n"
            f"**Verification status:** `{snap['verification_status']}`\n"
            f"**Remaining sends (10m):** `{snap['remaining_sends']}`"
        )
        embed = discord.Embed(title="📱 SMS Status", description=description, color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="send", description="Send an SMS to your configured phone (with confirmation)")
    @app_commands.describe(message="Message to send")
    @require_auth()
    async def send(self, interaction: discord.Interaction, message: str) -> None:
        try:
            cleaned = validate_sms_body(message)
            preview = cleaned if len(cleaned) <= 220 else f"{cleaned[:220]}…"
            embed = discord.Embed(
                title="📤 Confirm SMS Send",
                description=f"```text\n{preview}\n```",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="This message will be sent to your configured phone.")
            await interaction.response.send_message(
                embed=embed,
                view=SMSSendConfirmView(interaction.user.id, cleaned),
                ephemeral=True,
            )
        except Exception as exc:  # broad: intentional
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)

    @app_commands.command(name="test", description="Start or complete SMS verification, or send a test SMS")
    @app_commands.describe(code="Verification code sent to your phone (optional)")
    @require_auth()
    async def test(self, interaction: discord.Interaction, code: str = "") -> None:
        try:
            if code.strip():
                result, approved = await check_sms_verification(interaction.user.id, code)
                if approved:
                    await interaction.response.send_message(
                        f"✅ Verification approved via {result.provider}. SMS is ready.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ Verification status: `{result.status}`. Try again with a fresh code.",
                        ephemeral=True,
                    )
                return

            try:
                verification = await start_sms_verification(interaction.user.id)
                await interaction.response.send_message(
                    f"📨 Verification code sent via {verification.provider}. Submit it with `/sms test code:<code>`.",
                    ephemeral=True,
                )
            except SMSVerificationUnavailableError:
                prefs = sms_prefs.get(interaction.user.id)
                prefs.is_verified = True
                await sms_prefs.update(prefs)
                result = await send_configured_sms(
                    interaction.user.id,
                    "✅ OpenClaw SMS test successful. One-tap flow is ready.",
                )
                await interaction.response.send_message(
                    f"✅ Test SMS sent via {result.provider} (`{result.sid}`).",
                    ephemeral=True,
                )
        except Exception as exc:  # broad: intentional
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SMSCog(bot))
