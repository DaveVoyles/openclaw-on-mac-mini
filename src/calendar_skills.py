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

log = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = _cfg.google_oauth_client_id
GOOGLE_CLIENT_SECRET = _cfg.google_oauth_client_secret
GOOGLE_REFRESH_TOKEN = _cfg.google_oauth_refresh_token

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_PEOPLE_BASE = "https://people.googleapis.com/v1"

# ---------------------------------------------------------------------------
# Required Google OAuth2 scopes (add when re-authorizing via
# scripts/google_oauth_setup.py):
#
#   https://www.googleapis.com/auth/calendar             (existing — calendar R/W)
#   https://www.googleapis.com/auth/drive.readonly       (list + read Drive files)
#   https://www.googleapis.com/auth/drive.file           (upload Drive files)
#   https://www.googleapis.com/auth/contacts.readonly    (search/get Contacts)
#
# If /drive or /contacts commands return HTTP 403, the current refresh token
# was issued without these scopes.  Re-run the setup script and replace
# GOOGLE_OAUTH_REFRESH_TOKEN in .env with the new token.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Google Drive skills
# ---------------------------------------------------------------------------


async def list_drive_files(query: str = "", max_results: int = 10) -> str:
    """List files in Google Drive, optionally filtered by query (Drive query syntax)."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    max_results = min(max(max_results, 1), 50)
    params: dict = {
        "pageSize": str(max_results),
        "fields": "files(id,name,mimeType,modifiedTime,size)",
        "orderBy": "modifiedTime desc",
    }
    if query:
        params["q"] = query

    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.get(
            f"{_DRIVE_BASE}/files",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status == 403:
                body = await resp.text()
                return (
                    "❌ Drive API returned 403 Forbidden.\n"
                    "The current refresh token may lack Drive scopes.\n"
                    "Re-run `python scripts/google_oauth_setup.py` and add "
                    "`drive.readonly` scope, then update GOOGLE_OAUTH_REFRESH_TOKEN in .env.\n"
                    f"Details: {body[:200]}"
                )
            if resp.status != 200:
                body = await resp.text()
                return f"❌ Drive API error {resp.status}: {body[:200]}"
            data = await resp.json()
    except Exception as e:  # broad: intentional
        return f"❌ Drive request failed: {e}"

    files = data.get("files", [])
    if not files:
        label = f" matching `{query}`" if query else ""
        return f"✅ No files found{label}."

    lines = [f"**Google Drive Files** ({len(files)} result(s){f' — query: `{query}`' if query else ''})"]
    for f in files:
        name = f.get("name", "(unnamed)")
        fid = f.get("id", "?")
        mime = f.get("mimeType", "")
        modified = (f.get("modifiedTime") or "")[:10]
        size_bytes = f.get("size")
        size_str = f" · {int(size_bytes):,} B" if size_bytes else ""
        lines.append(f"• **{name}**{size_str} · `{fid}` · {modified} · {mime}")

    return _truncate("\n".join(lines))


async def read_drive_file(file_id: str) -> str:
    """Read/export a Google Drive file as plain text.

    Supports Google Docs (exported as text/plain), Google Sheets (exported as CSV),
    and regular files downloaded as-is.
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    headers = {"Authorization": f"Bearer {token}"}

    # Fetch file metadata first to determine MIME type
    try:
        session = await _get_session()
        async with session.get(
            f"{_DRIVE_BASE}/files/{file_id}",
            headers=headers,
            params={"fields": "id,name,mimeType"},
        ) as meta_resp:
            if meta_resp.status == 403:
                return (
                    "❌ Drive API returned 403 Forbidden. "
                    "Ensure the refresh token has `drive.readonly` scope."
                )
            if meta_resp.status != 200:
                body = await meta_resp.text()
                return f"❌ Drive metadata error {meta_resp.status}: {body[:200]}"
            meta = await meta_resp.json()
    except Exception as e:
        return f"❌ Drive metadata request failed: {e}"

    name = meta.get("name", file_id)
    mime = meta.get("mimeType", "")

    # Choose export vs download URL
    _GOOGLE_DOC_TYPES = {
        "application/vnd.google-apps.document": ("export", "text/plain"),
        "application/vnd.google-apps.spreadsheet": ("export", "text/csv"),
        "application/vnd.google-apps.presentation": ("export", "text/plain"),
    }

    try:
        session = await _get_session()
        if mime in _GOOGLE_DOC_TYPES:
            mode, export_mime = _GOOGLE_DOC_TYPES[mime]
            async with session.get(
                f"{_DRIVE_BASE}/files/{file_id}/export",
                headers=headers,
                params={"mimeType": export_mime},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return f"❌ Drive export error {resp.status}: {body[:200]}"
                content = await resp.text()
        else:
            async with session.get(
                f"{_DRIVE_BASE}/files/{file_id}",
                headers=headers,
                params={"alt": "media"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return f"❌ Drive download error {resp.status}: {body[:200]}"
                content = await resp.text()
    except Exception as e:
        return f"❌ Drive read failed: {e}"

    return _truncate(f"**{name}**\n\n{content}")


async def upload_drive_file(name: str, content: str, folder_id: str = "") -> str:
    """Create a new plain-text file in Google Drive with the given name and content."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    import json as _json

    metadata: dict = {"name": name, "mimeType": "text/plain"}
    if folder_id:
        metadata["parents"] = [folder_id]

    boundary = "openclaw_drive_boundary"
    meta_bytes = _json.dumps(metadata).encode()
    content_bytes = content.encode("utf-8")

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        + _json.dumps(metadata)
        + f"\r\n--{boundary}\r\n"
        f"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
        + content
        + f"\r\n--{boundary}--"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{_DRIVE_UPLOAD_BASE}/files",
            headers=headers,
            params={"uploadType": "multipart"},
            data=body.encode("utf-8"),
        ) as resp:
            if resp.status == 403:
                body_text = await resp.text()
                return (
                    "❌ Drive API returned 403 Forbidden. "
                    "Ensure the refresh token has `drive.file` scope.\n"
                    f"Details: {body_text[:200]}"
                )
            if resp.status not in (200, 201):
                body_text = await resp.text()
                return f"❌ Drive upload error {resp.status}: {body_text[:200]}"
            data = await resp.json()
    except Exception as e:
        return f"❌ Drive upload failed: {e}"

    file_id = data.get("id", "?")
    return (
        f"✅ File uploaded to Google Drive.\n"
        f"Name: **{name}**\n"
        f"ID: `{file_id}`"
    )


# ---------------------------------------------------------------------------
# Google Contacts / People skills
# ---------------------------------------------------------------------------


async def search_contacts(query: str, max_results: int = 10) -> str:
    """Search Google Contacts by name or email."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    max_results = min(max(max_results, 1), 30)
    params = {
        "query": query,
        "pageSize": str(max_results),
        "readMask": "names,emailAddresses,phoneNumbers",
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.get(
            f"{_PEOPLE_BASE}/people:searchContacts",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status == 403:
                body = await resp.text()
                return (
                    "❌ Contacts API returned 403 Forbidden.\n"
                    "The current refresh token may lack Contacts scopes.\n"
                    "Re-run `python scripts/google_oauth_setup.py` and add "
                    "`contacts.readonly` scope, then update GOOGLE_OAUTH_REFRESH_TOKEN in .env.\n"
                    f"Details: {body[:200]}"
                )
            if resp.status != 200:
                body = await resp.text()
                return f"❌ Contacts API error {resp.status}: {body[:200]}"
            data = await resp.json()
    except Exception as e:
        return f"❌ Contacts search failed: {e}"

    results = data.get("results", [])
    if not results:
        return f"✅ No contacts found matching `{query}`."

    lines = [f"**Contacts matching `{query}`** ({len(results)} result(s))"]
    for r in results:
        person = r.get("person", {})
        names = person.get("names", [])
        display = names[0].get("displayName", "(no name)") if names else "(no name)"
        emails = [e.get("value", "") for e in person.get("emailAddresses", [])]
        phones = [p.get("value", "") for p in person.get("phoneNumbers", [])]
        parts = [f"**{display}**"]
        if emails:
            parts.append(", ".join(emails))
        if phones:
            parts.append(", ".join(phones))
        lines.append("• " + " · ".join(parts))

    return _truncate("\n".join(lines))


async def get_contact(resource_name: str) -> str:
    """Get detailed info for a specific Google Contact by resource name (e.g. people/c1234)."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return _not_configured()

    token = await _get_access_token()
    if not token:
        return "❌ Failed to obtain Google access token. Check your OAuth credentials."

    params = {
        "personFields": "names,emailAddresses,phoneNumbers,organizations,addresses",
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        session = await _get_session()
        async with session.get(
            f"{_PEOPLE_BASE}/{resource_name}",
            headers=headers,
            params=params,
        ) as resp:
            if resp.status == 403:
                body = await resp.text()
                return (
                    "❌ Contacts API returned 403 Forbidden. "
                    "Ensure the refresh token has `contacts.readonly` scope.\n"
                    f"Details: {body[:200]}"
                )
            if resp.status != 200:
                body = await resp.text()
                return f"❌ Contacts API error {resp.status}: {body[:200]}"
            person = await resp.json()
    except Exception as e:
        return f"❌ Contact fetch failed: {e}"

    names = person.get("names", [])
    display = names[0].get("displayName", "(no name)") if names else "(no name)"
    emails = [e.get("value", "") for e in person.get("emailAddresses", [])]
    phones = [p.get("value", "") for p in person.get("phoneNumbers", [])]
    orgs = [o.get("name", "") for o in person.get("organizations", [])]
    addrs = [a.get("formattedValue", "") for a in person.get("addresses", [])]

    lines = [f"**{display}** (`{resource_name}`)"]
    if emails:
        lines.append(f"📧 Email: {', '.join(emails)}")
    if phones:
        lines.append(f"📞 Phone: {', '.join(phones)}")
    if orgs:
        lines.append(f"🏢 Org: {', '.join(orgs)}")
    if addrs:
        lines.append(f"📍 Address: {'; '.join(addrs)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

CALENDAR_SKILLS = {
    "get_upcoming_events": get_upcoming_events,
    "create_calendar_event": create_calendar_event,
    "get_todays_events": get_todays_events,
    "delete_calendar_event": delete_calendar_event,
    "list_drive_files": list_drive_files,
    "read_drive_file": read_drive_file,
    "upload_drive_file": upload_drive_file,
    "search_contacts": search_contacts,
    "get_contact": get_contact,
}
