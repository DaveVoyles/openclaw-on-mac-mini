"""
OpenClaw Google Calendar Skill — Phase 6
Read and create Google Calendar events via the Calendar REST API.
Uses OAuth2 with a stored refresh token — no OAuth2 library required.

One-time setup:
  1. Go to console.cloud.google.com → Create or select a project.
  2. APIs & Services → Library → Enable "Google Calendar API".
  3. APIs & Services → OAuth consent screen → External → Add yourself as test user.
  4. APIs & Services → Credentials → Create OAuth 2.0 Client ID → Desktop app.
  5. Note your Client ID and Client Secret.
  6. Run `python scripts/google_oauth_setup.py` to get your refresh token.
  7. Add to .env:
       GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
       GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
       GOOGLE_OAUTH_REFRESH_TOKEN=your-refresh-token

The same OAuth2 credentials (with gmail.* scopes added) can also be used
by a future Gmail OAuth2 skill without re-authorizing.
"""

import datetime
import logging
import time

import aiohttp

from config import TIMEOUT_DEFAULT
from config import cfg as _cfg

log = logging.getLogger("openclaw.calendar")

GOOGLE_CLIENT_ID = _cfg.google_oauth_client_id
GOOGLE_CLIENT_SECRET = _cfg.google_oauth_client_secret
GOOGLE_REFRESH_TOKEN = _cfg.google_oauth_refresh_token

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

_http_session: aiohttp.ClientSession | None = None

# Cached access token with TTL (Google tokens last ~3600s)
_access_token_cache: str | None = None
_access_token_expiry: float = 0.0
from http_session import SessionManager

_sessions = SessionManager(timeout=TIMEOUT_DEFAULT, name="calendar")
_get_session = _sessions.get
close_session = _sessions.close


from utils import truncate as _truncate


def _not_configured() -> str:
    return (
        "❌ Google Calendar not configured.\n"
        "Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and "
        "`GOOGLE_OAUTH_REFRESH_TOKEN` in `.env`.\n"
        "Run `python scripts/google_oauth_setup.py` for one-time setup."
    )


async def _get_access_token() -> str | None:
    """Exchange the stored refresh token for a short-lived access token (cached with TTL)."""
    global _access_token_cache, _access_token_expiry
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return None
    # Return cached token if still valid (refresh 5 min before expiry)
    if _access_token_cache and time.monotonic() < _access_token_expiry - 300:
        return _access_token_cache
    try:
        session = await _get_session()
        async with session.post(
            _TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                _access_token_cache = data.get("access_token")
                _access_token_expiry = time.monotonic() + data.get("expires_in", 3600)
                return _access_token_cache
            body = await resp.text()
            log.error("Token refresh failed %s: %s", resp.status, body[:200])
            return None
    except Exception as e:  # broad: intentional
        log.error("Token refresh error: %s", e)
        return None


def _fmt_event_time(start: dict) -> str:
    """Format an event start dict to a readable string."""
    raw = start.get("dateTime") or start.get("date", "?")
    if "T" in raw:
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%b %d %H:%M UTC")
        except ValueError:
            return raw[:16]
    return raw  # all-day: YYYY-MM-DD


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_upcoming_events(days: int = 7) -> str:
    """Get Google Calendar events for the next N days (default: 7)."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    days = min(max(days, 1), 30)
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "maxResults": "20",
        "orderBy": "startTime",
        "singleEvents": "true",
        "timeMin": time_min,
        "timeMax": time_max,
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.get(
            f"{_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"❌ Calendar API error {resp.status}: {body[:200]}"
            data = await resp.json()
    except Exception as e:  # broad: intentional
        return f"❌ Calendar request failed: {e}"

    items = data.get("items", [])
    if not items:
        return f"✅ No upcoming events in the next {days} day(s)."

    lines = [f"**Upcoming Events** (next {days} day(s) — {len(items)} total)"]
    for item in items:
        summary = item.get("summary", "(no title)")
        start_str = _fmt_event_time(item.get("start", {}))
        lines.append(f"• **{summary}** — {start_str}")

    return _truncate("\n".join(lines))


async def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
) -> str:
    """
    Create a Google Calendar event.

    Args:
        summary:     Event title.
        start_time:  ISO 8601 datetime ('2026-03-25T14:00:00') or date ('2026-03-25') for all-day.
        end_time:    ISO 8601 datetime or date (all-day end is exclusive, e.g. day after).
        description: Optional event notes.
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    # Detect all-day vs timed event
    is_all_day = len(start_time) == 10 and "T" not in start_time
    if is_all_day:
        event_body: dict = {
            "summary": summary,
            "description": description,
            "start": {"date": start_time},
            "end": {"date": end_time},
        }
    else:
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time, "timeZone": "UTC"},
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            json=event_body,
        ) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                event_id = data.get("id", "?")
                html_link = data.get("htmlLink", "")
                result = (
                    f"✅ Event created: **{summary}**\n"
                    f"Start: {start_time} | End: {end_time}\n"
                    f"ID: `{event_id}`"
                )
                if html_link:
                    result += f"\n{html_link}"
                return result
            body = await resp.text()
            return f"❌ Failed to create event ({resp.status}): {body[:200]}"
    except Exception as e:  # broad: intentional
        return f"❌ Calendar create error: {e}"


async def get_todays_events() -> str:
    """Get all Google Calendar events scheduled for today (UTC)."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token."

    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    time_max = now.replace(hour=23, minute=59, second=59, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "maxResults": "15",
        "orderBy": "startTime",
        "singleEvents": "true",
        "timeMin": time_min,
        "timeMax": time_max,
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.get(
            f"{_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"❌ Calendar API error {resp.status}: {body[:200]}"
            data = await resp.json()
    except Exception as e:  # broad: intentional
        return f"❌ Calendar request failed: {e}"

    items = data.get("items", [])
    today_str = now.strftime("%A, %B %d %Y")

    if not items:
        return f"✅ No events today ({today_str})."

    lines = [f"**Today's Events** — {today_str}"]
    for item in items:
        summary = item.get("summary", "(no title)")
        start = item.get("start", {})
        raw = start.get("dateTime") or start.get("date", "")
        if "T" in raw:
            try:
                dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M UTC")
            except ValueError:
                time_str = raw[11:16]
        else:
            time_str = "All day"
        lines.append(f"• {time_str} — **{summary}**")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

async def delete_calendar_event(event_id: str) -> str:
    """Delete a Google Calendar event by its ID."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.delete(
            f"{_CALENDAR_BASE}/calendars/primary/events/{event_id}",
            headers=headers,
        ) as resp:
            if resp.status == 204:
                return f"✅ Event `{event_id}` deleted successfully."
            body = await resp.text()
            return f"❌ Failed to delete event ({resp.status}): {body[:200]}"
    except Exception as e:  # broad: intentional
        return f"❌ Calendar delete error: {e}"


CALENDAR_SKILLS = {
    "get_upcoming_events": get_upcoming_events,
    "create_calendar_event": create_calendar_event,
    "get_todays_events": get_todays_events,
    "delete_calendar_event": delete_calendar_event,
}
