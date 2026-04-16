"""
OpenClaw Approval UI — Discord button view and embed builder for approval workflows.

Separated from approval_store to isolate the discord.py UI dependency.
Import this module only in cogs and Discord event handlers.
"""

import datetime
import logging

import discord

from approval_models import APPROVAL_TTL, ApprovalRequest, RiskLevel
from approval_store import approval_store, is_authorized_approver

log = logging.getLogger("openclaw.approvals")

RISK_COLORS = {
    RiskLevel.LOW: discord.Color.green(),
    RiskLevel.MEDIUM: discord.Color.gold(),
    RiskLevel.HIGH: discord.Color.orange(),
    RiskLevel.CRITICAL: discord.Color.red(),
}

RISK_EMOJI = {
    RiskLevel.LOW: "🟢",
    RiskLevel.MEDIUM: "🟡",
    RiskLevel.HIGH: "🟠",
    RiskLevel.CRITICAL: "🔴",
}


class ApprovalView(discord.ui.View):
    """Discord button view for approving/denying a pending request."""

    def __init__(self, request_id: str, action_callback):
        super().__init__(timeout=APPROVAL_TTL)
        self.request_id = request_id
        self.action_callback = action_callback

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green, custom_id="approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, approved=True)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red, custom_id="deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, approved=False)

    async def _handle(self, interaction: discord.Interaction, approved: bool):
        if not is_authorized_approver(interaction.user.id):
            await interaction.response.send_message(
                "🚫 You are not authorized to approve this action.",
                ephemeral=True,
            )
            return

        req = approval_store.resolve(
            self.request_id,
            approved=approved,
            resolver_id=interaction.user.id,
            resolver_name=str(interaction.user),
        )

        if req is None:
            await interaction.response.send_message(
                "⚠️ This request has expired or was already resolved.", ephemeral=True
            )
            self.stop()
            return

        # Disable buttons after resolution
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True

        if approved:
            await interaction.response.edit_message(
                content=f"✅ **Approved** by {interaction.user.display_name}",
                view=self,
            )
            try:
                result = await self.action_callback(req)
                await interaction.followup.send(result)
            except Exception as e:  # broad: intentional
                await interaction.followup.send(f"❌ Execution failed: {e}")
        else:
            await interaction.response.edit_message(
                content=f"❌ **Denied** by {interaction.user.display_name}",
                view=self,
            )

        self.stop()

    async def on_timeout(self):
        """Called when the view times out (no button pressed)."""
        req = approval_store.get(self.request_id)
        if req and not req.resolved:
            req.resolved = True
            log.info("Approval request %s timed out", self.request_id)
        if hasattr(self, "message") and self.message:
            try:
                for child in self.children:
                    if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                        child.disabled = True
                await self.message.edit(
                    content="⏱️ **Approval request expired** — no action was taken.",
                    view=self,
                )
            except Exception as e:  # broad: intentional
                log.debug("Could not update expired approval message: %s", e)


def build_approval_embed(req: ApprovalRequest) -> discord.Embed:
    """Build the embed shown when an approval is requested."""
    emoji = RISK_EMOJI.get(req.risk_level, "⚪")
    embed = discord.Embed(
        title=f"{emoji} Approval Required — {req.action}",
        description=(
            f"**Action**: `{req.action}`\n"
            f"**Target**: `{req.target}`\n"
            f"**Risk Level**: {req.risk_level.value}\n"
            f"**Requested by**: {req.requester_name}\n"
            f"**Request ID**: `{req.request_id}`"
        ),
        color=RISK_COLORS.get(req.risk_level, discord.Color.greyple()),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if req.detail:
        embed.add_field(name="Details", value=f"```\n{req.detail[:1000]}\n```", inline=False)
    embed.set_footer(text=f"Expires in {APPROVAL_TTL // 60} minutes • Click a button to respond")
    return embed
