"""
OpenClaw AgentMail Skill — Phase 5
Integrates with AgentMail.to for sending e-mails via AI.
"""

import asyncio
import logging
import os
import aiohttp
from typing import Optional

log = logging.getLogger("openclaw.agentmail")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENTMAIL_API_KEY = os.getenv("AGENTMAIL_API_KEY", "")


async def send_agent_mail(to: str, subject: str, body: str) -> str:
    """Send an automated e-mail message via AgentMail.to."""
    if not AGENTMAIL_API_KEY:
        return "❌ AgentMail API key not configured. Set `AGENTMAIL_API_KEY` in your `.env` file."

    url = "https://api.agentmail.to/v1/send"
    headers = {
        "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to,
        "subject": subject,
        "body": body,
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return f"✅ AgentMail sent successfully! (ID: {data.get('id', 'unknown')})"

                error_data = await resp.text()
                log.error("AgentMail API error: %s", error_data)
                return f"❌ Failed to send AgentMail: (Status {resp.status}) - {error_data}"

    except asyncio.TimeoutError:
        return "❌ AgentMail request timed out (15s)."
    except Exception as e:
        log.error("AgentMail error: %s", e)
        return f"❌ AgentMail error: {e}"
