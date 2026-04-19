"""
Patreon Alert System - Discord notifications for MonsterVision issues.

Sends actionable Discord alerts when:
- Cookies are expiring or expired
- Downloads are failing
- Container is down

Includes rate limiting to prevent alert spam.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import discord

from patreon_monitor import PatreonHealthResult, PatreonHealthStatus

log = logging.getLogger(__name__)

# Rate limiting: Max 1 alert per issue type per 6 hours
ALERT_COOLDOWN_SECONDS = 6 * 3600


@dataclass
class AlertState:
    """Track alert state for rate limiting."""

    last_alert_time: float = 0.0
    last_status: PatreonHealthStatus = PatreonHealthStatus.UNKNOWN
    alert_count: int = 0


class PatreonAlertManager:
    """Manages Discord alerts for Patreon health issues."""

    def __init__(self):
        self._alert_states: dict[str, AlertState] = {}

    async def send_alert_if_needed(
        self,
        health_result: PatreonHealthResult,
        discord_client: discord.Client | None = None,
        user_id: int | None = None,
        channel_id: int | None = None,
    ) -> bool:
        """
        Send alert if health status warrants it and cooldown has passed.

        Args:
            health_result: Health check result
            discord_client: Discord client instance
            user_id: User ID to DM (optional)
            channel_id: Channel ID to post to (optional)

        Returns:
            True if alert was sent, False otherwise
        """
        if not discord_client:
            log.debug("No Discord client provided, skipping alert")
            return False

        # Check if we should alert
        should_alert, alert_key = self._should_send_alert(health_result)

        if not should_alert:
            log.debug(f"Alert cooldown active for {alert_key}")
            return False

        # Create embed
        embed = self._create_alert_embed(health_result)

        # Send alert
        sent = await self._send_discord_alert(
            embed=embed,
            discord_client=discord_client,
            user_id=user_id,
            channel_id=channel_id,
        )

        if sent:
            # Update alert state
            state = self._alert_states.get(alert_key, AlertState())
            state.last_alert_time = time.time()
            state.last_status = health_result.status
            state.alert_count += 1
            self._alert_states[alert_key] = state
            log.info(f"Sent Patreon alert: {alert_key}")

        return sent

    def _should_send_alert(self, health_result: PatreonHealthResult) -> tuple[bool, str]:
        """
        Determine if we should send an alert based on status and cooldown.

        Returns:
            (should_send, alert_key)
        """
        # Don't alert for OK status
        if health_result.status == PatreonHealthStatus.OK:
            return False, "ok"

        # Create alert key based on primary issue
        if "container is stopped" in health_result.message.lower():
            alert_key = "container_stopped"
        elif "api unreachable" in health_result.message.lower():
            alert_key = "api_unreachable"
        elif "cookies expired" in health_result.message.lower():
            alert_key = "cookies_expired"
        elif "cookies expiring" in health_result.message.lower():
            alert_key = "cookies_expiring"
        elif "failed downloads" in health_result.message.lower():
            alert_key = "downloads_failing"
        else:
            alert_key = "general_warning"

        # Check cooldown
        state = self._alert_states.get(alert_key, AlertState())

        # If status improved from last alert, allow immediate re-alert when it degrades again
        if state.last_status in (PatreonHealthStatus.OK, PatreonHealthStatus.UNKNOWN):
            return True, alert_key

        # Check cooldown timer
        time_since_last = time.time() - state.last_alert_time
        if time_since_last < ALERT_COOLDOWN_SECONDS:
            return False, alert_key

        # For CRITICAL issues, always alert after cooldown
        if health_result.status == PatreonHealthStatus.CRITICAL:
            return True, alert_key

        # For WARNING, alert after cooldown
        if health_result.status == PatreonHealthStatus.WARNING:
            return True, alert_key

        return False, alert_key

    def _create_alert_embed(self, health_result: PatreonHealthResult) -> discord.Embed:
        """Create Discord embed for health alert."""
        # Determine color and emoji
        if health_result.status == PatreonHealthStatus.CRITICAL:
            color = discord.Color.red()
            emoji = "🚨"
            title_prefix = "CRITICAL"
        elif health_result.status == PatreonHealthStatus.WARNING:
            color = discord.Color.orange()
            emoji = "⚠️"
            title_prefix = "WARNING"
        else:
            color = discord.Color.yellow()
            emoji = "ℹ️"
            title_prefix = "NOTICE"

        embed = discord.Embed(
            title=f"{emoji} {title_prefix}: Patreon Downloads",
            description=health_result.message,
            color=color,
            timestamp=health_result.timestamp,
        )

        # Add issues
        if health_result.issues:
            issues_text = "\n".join(f"• {issue}" for issue in health_result.issues[:5])
            embed.add_field(name="Issues Detected", value=issues_text, inline=False)

        # Add metadata
        metadata = health_result.metadata
        if metadata:
            meta_parts = []

            if "container_status" in metadata:
                meta_parts.append(f"**Container:** {metadata['container_status']}")

            if "cookie_age_hours" in metadata and metadata["cookie_age_hours"] is not None:
                age_h = metadata["cookie_age_hours"]
                if age_h > 72:
                    meta_parts.append(f"**Cookies:** ❌ Expired ({age_h:.0f}h old)")
                elif age_h > 48:
                    meta_parts.append(f"**Cookies:** ⚠️ Expiring ({age_h:.0f}h old)")
                else:
                    meta_parts.append(f"**Cookies:** ✅ Fresh ({age_h:.0f}h old)")

            if "failed_downloads" in metadata:
                failed = metadata["failed_downloads"]
                if failed > 0:
                    meta_parts.append(f"**Failed Downloads:** {failed}")

            if meta_parts:
                embed.add_field(name="Status Details", value="\n".join(meta_parts), inline=False)

        # Add action items
        if health_result.action_items:
            # Split into immediate actions and detailed steps
            immediate = [item for item in health_result.action_items if not item[0].isdigit()]
            steps = [item for item in health_result.action_items if item[0].isdigit()]

            if immediate:
                embed.add_field(
                    name="🔧 Quick Actions",
                    value="\n".join(f"• {action}" for action in immediate[:3]),
                    inline=False,
                )

            if steps:
                embed.add_field(
                    name="📋 Cookie Refresh Steps",
                    value="\n".join(steps[:6]),
                    inline=False,
                )

        # Add helpful footer
        embed.set_footer(text="Use /patreon status for detailed diagnostics")

        return embed

    async def _send_discord_alert(
        self,
        embed: discord.Embed,
        discord_client: discord.Client,
        user_id: int | None = None,
        channel_id: int | None = None,
    ) -> bool:
        """Send alert via Discord DM or channel."""
        try:
            # Try DM first if user_id provided
            if user_id:
                try:
                    user = await discord_client.fetch_user(user_id)
                    await user.send(embed=embed)
                    log.info(f"Sent Patreon alert DM to user {user_id}")
                    return True
                except discord.errors.Forbidden:
                    log.warning(f"Cannot DM user {user_id}, trying channel")
                except discord.errors.NotFound:
                    log.warning(f"User {user_id} not found")

            # Fallback to channel if provided
            if channel_id:
                try:
                    channel = discord_client.get_channel(channel_id)
                    if not channel:
                        channel = await discord_client.fetch_channel(channel_id)
                    if channel:
                        await channel.send(embed=embed)
                        log.info(f"Sent Patreon alert to channel {channel_id}")
                        return True
                except (discord.errors.Forbidden, discord.errors.NotFound) as e:
                    log.error(f"Cannot send to channel {channel_id}: {e}")

            log.warning("No valid destination for Patreon alert")
            return False

        except Exception as e:  # broad: intentional
            log.error("Error sending Patreon alert: %s", e)
            return False

    def reset_alert_state(self, alert_key: str | None = None):
        """Reset alert state (for testing or manual reset)."""
        if alert_key:
            self._alert_states.pop(alert_key, None)
            log.info(f"Reset alert state for {alert_key}")
        else:
            self._alert_states.clear()
            log.info("Reset all alert states")

    def get_alert_status(self) -> dict[str, Dict]:
        """Get current alert state (for debugging)."""
        return {
            key: {
                "last_alert": datetime.fromtimestamp(state.last_alert_time).isoformat()
                if state.last_alert_time > 0
                else "never",
                "last_status": state.last_status.value,
                "alert_count": state.alert_count,
                "cooldown_remaining": max(
                    0, ALERT_COOLDOWN_SECONDS - (time.time() - state.last_alert_time)
                ),
            }
            for key, state in self._alert_states.items()
        }


# Global instance
_alert_manager: PatreonAlertManager | None = None


def get_alert_manager() -> PatreonAlertManager:
    """Get or create the global alert manager."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = PatreonAlertManager()
    return _alert_manager
