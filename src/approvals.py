"""
OpenClaw Approvals — Phase 4: Security & Approval Workflows
Manages pending action requests with Discord button UI, timeouts, and audit trail.
"""

import datetime
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Coroutine

import discord

log = logging.getLogger("openclaw.approvals")


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


class RiskLevel(Enum):
    LOW = "LOW"          # Auto-execute, no approval
    MEDIUM = "MEDIUM"    # Execute with enhanced logging
    HIGH = "HIGH"        # Requires explicit approval via buttons
    CRITICAL = "CRITICAL"  # Requires approval + shows dry-run preview


# ---------------------------------------------------------------------------
# Pending approval request
# ---------------------------------------------------------------------------

# How long an approval request stays valid (seconds)
APPROVAL_TTL = 300  # 5 minutes


@dataclass
class ApprovalRequest:
    """A single pending action awaiting approval."""
    request_id: str
    action: str                  # e.g. "restart_container"
    target: str                  # e.g. "sonarr"
    risk_level: RiskLevel
    requester_id: int
    requester_name: str
    channel_id: int
    created_at: float = field(default_factory=time.monotonic)
    resolved: bool = False
    approved: bool = False
    resolver_id: int | None = None
    resolver_name: str | None = None
    detail: str = ""             # Extra context (dry-run output, etc.)

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > APPROVAL_TTL

    @property
    def age_seconds(self) -> int:
        return int(time.monotonic() - self.created_at)


# ---------------------------------------------------------------------------
# Approval store
# ---------------------------------------------------------------------------


class ApprovalStore:
    """In-memory store for pending approval requests."""

    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}

    def create(
        self,
        action: str,
        target: str,
        risk_level: RiskLevel,
        requester_id: int,
        requester_name: str,
        channel_id: int,
        detail: str = "",
    ) -> ApprovalRequest:
        """Create a new approval request and return it."""
        request_id = uuid.uuid4().hex[:8]
        req = ApprovalRequest(
            request_id=request_id,
            action=action,
            target=target,
            risk_level=risk_level,
            requester_id=requester_id,
            requester_name=requester_name,
            channel_id=channel_id,
            detail=detail,
        )
        self._pending[request_id] = req
        log.info("Approval request created: %s (%s %s) by %s", request_id, action, target, requester_name)
        return req

    def get(self, request_id: str) -> ApprovalRequest | None:
        req = self._pending.get(request_id)
        if req and req.is_expired and not req.resolved:
            req.resolved = True  # Auto-expire
        return req

    def resolve(self, request_id: str, approved: bool, resolver_id: int, resolver_name: str) -> ApprovalRequest | None:
        """Approve or deny a request. Returns the request if found."""
        req = self._pending.get(request_id)
        if req is None or req.resolved:
            return None
        if req.is_expired:
            req.resolved = True
            return None
        req.resolved = True
        req.approved = approved
        req.resolver_id = resolver_id
        req.resolver_name = resolver_name
        return req

    def cleanup_expired(self):
        """Remove old resolved/expired requests."""
        cutoff = time.monotonic() - APPROVAL_TTL * 2
        expired = [k for k, v in self._pending.items() if v.created_at < cutoff]
        for k in expired:
            del self._pending[k]

    @property
    def pending_count(self) -> int:
        return sum(1 for v in self._pending.values() if not v.resolved and not v.is_expired)

    def list_pending(self) -> list[ApprovalRequest]:
        """Return all active pending requests."""
        return [v for v in self._pending.values() if not v.resolved and not v.is_expired]


# Global instance
approval_store = ApprovalStore()


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------

_emergency_stop = False
_stop_lock = threading.Lock()


def is_emergency_stopped() -> bool:
    """Check if the emergency stop is active (thread-safe)."""
    with _stop_lock:
        return _emergency_stop


def set_emergency_stop(active: bool) -> None:
    """Toggle the emergency stop flag (thread-safe)."""
    global _emergency_stop
    with _stop_lock:
        _emergency_stop = active
    log.warning("Emergency stop %s", "ACTIVATED" if active else "deactivated")


# ---------------------------------------------------------------------------
# Discord UI: Approval buttons
# ---------------------------------------------------------------------------


class ApprovalView(discord.ui.View):
    """Discord button view for approving/denying a pending request."""

    def __init__(self, request_id: str, action_callback: Callable[..., Coroutine]):
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
            child.disabled = True  # type: ignore

        if approved:
            await interaction.response.edit_message(
                content=f"✅ **Approved** by {interaction.user.display_name}",
                view=self,
            )
            # Execute the approved action
            try:
                result = await self.action_callback(req)
                await interaction.followup.send(result)
            except Exception as e:
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
        # Disable buttons and notify users the request has expired
        if hasattr(self, "message") and self.message:
            try:
                for child in self.children:
                    child.disabled = True  # type: ignore
                await self.message.edit(
                    content="⏱️ **Approval request expired** — no action was taken.",
                    view=self,
                )
            except Exception as e:
                log.debug("Could not update expired approval message: %s", e)


# ---------------------------------------------------------------------------
# Helper: build the approval embed
# ---------------------------------------------------------------------------

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
