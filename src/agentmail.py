"""
OpenClaw AgentMail Skill — Phase 5
Integrates with AgentMail.to for sending e-mails via AI.
"""

import asyncio
import logging
import os
from urllib.parse import quote

import aiohttp

log = logging.getLogger("openclaw.agentmail")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENTMAIL_API_KEY = os.getenv("AGENTMAIL_API_KEY", "")
AGENTMAIL_INBOX = os.getenv("AGENTMAIL_INBOX", "")  # e.g. "openclaw" → openclaw@agentmail.to

_http_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )
    return _http_session


async def close_session() -> None:
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


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
