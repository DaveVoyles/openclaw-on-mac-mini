"""
Patreon Skills - LLM-callable functions for Patreon/MonsterVision monitoring.

Allows the LLM to proactively check Patreon health and provide guidance
when users ask about video downloads.
"""

import logging
from typing import Any, Dict

from patreon_monitor import PatreonHealthStatus, get_patreon_checker
from patreon_recovery import get_recovery_manager

log = logging.getLogger("openclaw.patreon_skills")


async def check_patreon_health() -> Dict[str, Any]:
    """
    Check Patreon/MonsterVision health status.

    LLM-callable skill to diagnose Patreon download issues.

    Returns:
        Dictionary with health status, issues, and recommendations
    """
    try:
        checker = get_patreon_checker()
        health = await checker.check_health()

        # Format for LLM consumption
        return {
            "status": health.status.value,
            "overall_health": _get_status_description(health.status),
            "message": health.message,
            "issues": health.issues,
            "action_items": health.action_items,
            "details": {
                "container_status": health.metadata.get("container_status", "unknown"),
                "api_available": health.metadata.get("api_available", False),
                "cookie_age_hours": health.metadata.get("cookie_age_hours"),
                "failed_downloads": health.metadata.get("failed_downloads", 0),
            },
            "timestamp": health.timestamp.isoformat(),
        }
    except Exception as e:
        log.error(f"Error in check_patreon_health skill: {e}")
        return {
            "status": "error",
            "overall_health": "Unable to check Patreon health",
            "message": str(e),
            "issues": [f"Health check failed: {str(e)}"],
            "action_items": ["Check if MonsterVision container is running"],
            "details": {},
        }


async def get_patreon_status() -> str:
    """
    Get human-readable Patreon status summary.

    Returns:
        Formatted status string suitable for chat response
    """
    health_data = await check_patreon_health()

    if health_data["status"] == "ok":
        return (
            "✅ Patreon downloads are working normally. "
            "Container is running, cookies are fresh, and no download failures detected."
        )

    # Build status message
    status = health_data["overall_health"]
    details = health_data["details"]

    parts = [f"**Patreon Status:** {status}"]

    # Add key details
    if details.get("container_status"):
        parts.append(f"• Container: {details['container_status']}")

    if details.get("cookie_age_hours") is not None:
        age = details["cookie_age_hours"]
        if age > 72:
            parts.append(f"• Cookies: ❌ Expired ({age:.0f}h old)")
        elif age > 48:
            parts.append(f"• Cookies: ⚠️ Expiring soon ({age:.0f}h old)")
        else:
            parts.append(f"• Cookies: ✅ Fresh ({age:.0f}h old)")

    if details.get("failed_downloads", 0) > 0:
        parts.append(f"• Failed downloads: {details['failed_downloads']}")

    # Add issues
    if health_data["issues"]:
        parts.append("\n**Issues:**")
        for issue in health_data["issues"][:3]:
            parts.append(f"• {issue}")

    # Add quick fix
    if health_data["action_items"]:
        parts.append("\n**Quick Fix:**")
        parts.append(health_data["action_items"][0])

    return "\n".join(parts)


async def refresh_patreon_cookies_guide() -> str:
    """
    Get step-by-step guide for refreshing Patreon cookies.

    Returns:
        Formatted instructions for cookie refresh
    """
    return """
🍪 **Patreon Cookie Refresh Guide**

**1. Export Cookies from Browser:**
   • Open Chrome/Firefox and go to patreon.com (logged in)
   • Install EditThisCookie (Chrome) or Cookie-Editor (Firefox)
   • Click extension → Export → Netscape format
   • Save as `cookies.txt`

**2. Copy to MonsterVision Container:**
   ```bash
   docker cp cookies.txt monstervision:/app/cookies.txt
   ```

**3. Restart Container:**
   ```bash
   docker restart monstervision
   ```

**4. Verify:**
   Wait 1-2 minutes, then check `/patreon status`

**Note:** Cookies typically last 60-90 days. You'll get a warning before they expire.
    """.strip()


async def diagnose_patreon_downloads(issue_description: str = "") -> str:
    """
    Diagnose Patreon download issues and provide solutions.

    Args:
        issue_description: User's description of the problem (optional)

    Returns:
        Diagnostic information and suggested fixes
    """
    health_data = await check_patreon_health()

    diagnosis = ["🔍 **Patreon Downloads Diagnosis**\n"]

    # Current status
    diagnosis.append(f"**Current Status:** {health_data['overall_health']}")

    # If user described an issue, acknowledge it
    if issue_description:
        diagnosis.append(f"\n**Reported Issue:** {issue_description}")

    # Check for common problems
    details = health_data["details"]

    if details.get("container_status") == "stopped":
        diagnosis.append("\n**Problem:** MonsterVision container is stopped")
        diagnosis.append("**Solution:** Start the container with `docker start monstervision`")
        return "\n".join(diagnosis)

    if not details.get("api_available"):
        diagnosis.append("\n**Problem:** MonsterVision API is unreachable")
        diagnosis.append("**Solution:** Restart container with `docker restart monstervision`")
        diagnosis.append("Then check logs: `docker logs monstervision --tail 50`")
        return "\n".join(diagnosis)

    cookie_age = details.get("cookie_age_hours")
    if cookie_age and cookie_age > 72:
        diagnosis.append(f"\n**Problem:** Patreon cookies expired ({cookie_age:.0f}h old)")
        diagnosis.append("**Solution:** Cookies need to be refreshed")
        diagnosis.append("\nUse `/patreon refresh-cookies` for step-by-step guide")
        return "\n".join(diagnosis)

    if details.get("failed_downloads", 0) > 0:
        diagnosis.append(f"\n**Problem:** {details['failed_downloads']} failed downloads")
        if cookie_age and cookie_age > 48:
            diagnosis.append("**Likely Cause:** Cookies are expiring")
            diagnosis.append("**Solution:** Refresh cookies (see `/patreon refresh-cookies`)")
        else:
            diagnosis.append("**Possible Causes:** Network issues, Patreon API changes, or disk space")
            diagnosis.append("**Solution:** Check container logs for specific errors:")
            diagnosis.append("`docker logs monstervision --tail 100`")
        return "\n".join(diagnosis)

    # No obvious issues
    if health_data["status"] == "ok":
        diagnosis.append("\n✅ **No Issues Detected**")
        diagnosis.append("All systems are operating normally.")
        if issue_description:
            diagnosis.append("\nIf you're still experiencing issues:")
            diagnosis.append("• Check recent downloads in the MonsterVision container")
            diagnosis.append("• Verify your Patreon subscription is active")
            diagnosis.append("• Check container logs: `docker logs monstervision --tail 50`")
    else:
        # Warning status with unclear cause
        diagnosis.append("\n⚠️ **Minor Issues Detected**")
        if health_data["issues"]:
            for issue in health_data["issues"]:
                diagnosis.append(f"• {issue}")

    return "\n".join(diagnosis)


async def attempt_patreon_recovery() -> str:
    """
    Attempt automatic recovery for Patreon issues.

    Returns:
        Result of recovery attempt
    """
    try:
        # Check current health
        checker = get_patreon_checker()
        health = await checker.check_health()

        if health.status == PatreonHealthStatus.OK:
            return "✅ Patreon is healthy - no recovery needed"

        # Attempt recovery
        recovery_mgr = get_recovery_manager()
        result = await recovery_mgr.attempt_recovery(health)

        if not result:
            return (
                "⚠️ No automatic recovery available for this issue. "
                "Manual intervention required - check `/patreon status` for details."
            )

        if result.success:
            return (
                f"✅ **Recovery Successful**\n"
                f"Action: {result.action.value}\n"
                f"Result: {result.message}\n\n"
                f"Check `/patreon status` in a few minutes to verify."
            )
        else:
            return (
                f"❌ **Recovery Failed**\n"
                f"Action: {result.action.value}\n"
                f"Error: {result.message}\n\n"
                f"Manual intervention required."
            )

    except Exception as e:
        log.error(f"Error in attempt_patreon_recovery: {e}")
        return f"❌ Recovery attempt failed: {str(e)}"


def _get_status_description(status: PatreonHealthStatus) -> str:
    """Get human-readable status description."""
    descriptions = {
        PatreonHealthStatus.OK: "✅ Healthy - All systems operational",
        PatreonHealthStatus.WARNING: "⚠️ Warning - Attention needed",
        PatreonHealthStatus.CRITICAL: "🚨 Critical - Downloads failing",
        PatreonHealthStatus.UNKNOWN: "❓ Unknown - Unable to determine status",
    }
    return descriptions.get(status, "Unknown status")


# Skill registry for LLM tool calling
PATREON_SKILLS = {
    "check_patreon_health": {
        "function": check_patreon_health,
        "description": "Check Patreon/MonsterVision health status and get diagnostic information",
        "parameters": {},
    },
    "get_patreon_status": {
        "function": get_patreon_status,
        "description": "Get a human-readable summary of Patreon download status",
        "parameters": {},
    },
    "refresh_patreon_cookies_guide": {
        "function": refresh_patreon_cookies_guide,
        "description": "Get step-by-step instructions for refreshing Patreon cookies",
        "parameters": {},
    },
    "diagnose_patreon_downloads": {
        "function": diagnose_patreon_downloads,
        "description": "Diagnose Patreon download issues and suggest solutions",
        "parameters": {
            "issue_description": {
                "type": "string",
                "description": "Optional description of the issue from the user",
                "required": False,
            }
        },
    },
    "attempt_patreon_recovery": {
        "function": attempt_patreon_recovery,
        "description": "Attempt automatic recovery for Patreon issues",
        "parameters": {},
    },
}
