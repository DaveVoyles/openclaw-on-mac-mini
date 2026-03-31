"""
OpenClaw AgentMail Skill — Phase 5
Integrates with AgentMail.to for sending e-mails via AI.
"""

import asyncio
import logging
import os
from urllib.parse import quote

import aiohttp

from config import TIMEOUT_DEFAULT, cfg as _cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.agentmail")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENTMAIL_API_KEY = _cfg.agentmail_api_key
AGENTMAIL_INBOX = _cfg.agentmail_inbox

_sessions = SessionManager(timeout=TIMEOUT_DEFAULT, name="agentmail")
_get_session = _sessions.get


async def close_session() -> None:
    await _sessions.close()


async def send_agent_mail(to: str, subject: str, body: str) -> str:
    """Send an automated e-mail message via AgentMail.to."""
    if not AGENTMAIL_API_KEY:
        return "❌ AgentMail API key not configured. Set `AGENTMAIL_API_KEY` in your `.env` file."
    if not AGENTMAIL_INBOX:
        return "❌ AgentMail inbox not configured. Set `AGENTMAIL_INBOX` in your `.env` file (e.g. `openclaw`)."

    inbox_id = AGENTMAIL_INBOX if "@" in AGENTMAIL_INBOX else f"{AGENTMAIL_INBOX}@agentmail.to"
    url = f"https://api.agentmail.to/v0/inboxes/{quote(inbox_id, safe='')}/messages/send"
    headers = {
        "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to,
        "subject": subject,
        "text": body,
    }

    try:
        session = await _get_session()
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
