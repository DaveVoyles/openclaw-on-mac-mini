"""
Patreon Scheduled Monitoring Task.

Runs health checks every 30 minutes and sends alerts when issues detected.
Integrates with OpenClaw's advanced scheduler.
"""

import logging
from datetime import datetime

from alert_patreon import get_alert_manager
from config import cfg
from patreon_monitor import get_patreon_checker
from patreon_recovery import get_recovery_manager

log = logging.getLogger("openclaw.patreon_scheduled")


async def scheduled_patreon_health_check(
    discord_client=None, alert_user_id: int = None, alert_channel_id: int = None
) -> dict:
    """
    Scheduled task to check Patreon health and send alerts if needed.

    Args:
        discord_client: Discord client for sending alerts
        alert_user_id: User ID to send DM alerts (optional)
        alert_channel_id: Channel ID for alerts (optional)

    Returns:
        Dictionary with check results
    """
    log.info("Running scheduled Patreon health check")

    try:
        # Run health check
        checker = get_patreon_checker()
        health = await checker.check_health()

        log.info(f"Patreon health status: {health.status.value}")

        # Attempt auto-recovery if needed
        recovery_result = None
        recovery_mgr = get_recovery_manager()
        recovery_result = await recovery_mgr.attempt_recovery(health)

        if recovery_result:
            log.info(
                f"Recovery attempted: {recovery_result.action.value} - "
                f"{'Success' if recovery_result.success else 'Failed'}"
            )

            # If recovery was successful, re-check health
            if recovery_result.success:
                import asyncio

                await asyncio.sleep(5)  # Give it a moment
                health = await checker.check_health()
                log.info(f"Post-recovery health status: {health.status.value}")

        # Send alert if needed
        alert_mgr = get_alert_manager()
        alert_sent = False

        # Determine alert targets (use config defaults if not provided)
        user_id = alert_user_id
        channel_id = alert_channel_id

        # Try to use configured alert channel if available
        if not user_id and not channel_id and cfg.alert_channel_id:
            channel_id = cfg.alert_channel_id

        if discord_client:
            alert_sent = await alert_mgr.send_alert_if_needed(
                health_result=health,
                discord_client=discord_client,
                user_id=user_id,
                channel_id=channel_id,
            )

        return {
            "success": True,
            "status": health.status.value,
            "message": health.message,
            "issues_count": len(health.issues),
            "recovery_attempted": recovery_result is not None,
            "recovery_success": recovery_result.success if recovery_result else None,
            "alert_sent": alert_sent,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        log.error(f"Error in scheduled Patreon health check: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


# Task configuration for scheduler_advanced.py
PATREON_MONITORING_TASK = {
    "name": "patreon_health_check",
    "function": scheduled_patreon_health_check,
    "description": "Monitor Patreon/MonsterVision health and send alerts",
    "schedule": "*/30 * * * *",  # Every 30 minutes (cron format)
    "retry_on_failure": True,
    "max_retries": 3,
    "retry_delay_seconds": 300,  # 5 minutes between retries
    "timeout_seconds": 60,
}
