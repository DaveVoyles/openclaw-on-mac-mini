"""
OpenClaw Overseerr Skill — Phase 6
Manage media requests via the Overseerr REST API.

Requires: OVERSEERR_URL, OVERSEERR_API_KEY in .env
(OVERSEERR_API_KEY is already present in advanced_skills.py config)
"""

import asyncio
import logging

log = logging.getLogger(__name__)

import aiohttp

from config import cfg as _cfg

OVERSEERR_URL = _cfg.overseerr_url
OVERSEERR_API_KEY = _cfg.overseerr_api_key

from http_session import SessionManager

_sessions = SessionManager(timeout=10, name="overseerr")
_get_session = _sessions.get
close_session = _sessions.close


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


async def _get(path: str) -> dict | list | str:
    if not OVERSEERR_API_KEY:
        return "❌ `OVERSEERR_API_KEY` not configured."
    headers = {"X-Api-Key": OVERSEERR_API_KEY}
    try:
        session = await _get_session()
        async with session.get(f"{OVERSEERR_URL}/api/v1{path}", headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            try:
                text = await resp.text()
            except aiohttp.ClientError as exc:
                log.debug("Overseerr GET response body read failed: %s", exc)
                text = "[could not read response body]"
            return f"HTTP {resp.status}: {text[:200]}"
    except asyncio.TimeoutError:
        return "Request timed out (10s)"
    except aiohttp.ClientError as e:
        log.debug("Overseerr GET request failed: %s", e)
        return f"Request failed: {e}"


async def _post(path: str) -> dict | str:
    if not OVERSEERR_API_KEY:
        return "❌ `OVERSEERR_API_KEY` not configured."
    headers = {"X-Api-Key": OVERSEERR_API_KEY}
    try:
        session = await _get_session()
        async with session.post(f"{OVERSEERR_URL}/api/v1{path}", headers=headers) as resp:
            if resp.status in (200, 201, 204):
                return {} if resp.status == 204 else await resp.json()
            try:
                text = await resp.text()
            except aiohttp.ClientError as exc:
                log.debug("Overseerr POST response body read failed: %s", exc)
                text = "[could not read response body]"
            return f"HTTP {resp.status}: {text[:200]}"
    except asyncio.TimeoutError:
        return "Request timed out (10s)"
    except aiohttp.ClientError as e:
        log.debug("Overseerr POST request failed: %s", e)
        return f"Request failed: {e}"


def _media_title(media: dict) -> str:
    return media.get("title") or media.get("originalTitle") or media.get("name") or f"TMDB #{media.get('tmdbId', '?')}"


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_pending_requests() -> str:
    """List all pending media requests awaiting approval in Overseerr."""
    data = await _get("/request?filter=pending&take=25&sort=added")
    if isinstance(data, str):
        return f"❌ Overseerr error: {data}"

    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return "✅ No pending media requests."

    total = data.get("pageInfo", {}).get("results", len(results))
    lines = [f"**Pending Requests** ({len(results)}/{total} shown)"]
    for r in results:
        req_id = r.get("id", "?")
        media = r.get("media", {})
        title = _media_title(media)
        media_type = "🎬" if r.get("type") == "movie" else "📺"
        requester = r.get("requestedBy", {}).get("displayName") or r.get("requestedBy", {}).get("username", "Unknown")
        lines.append(f"• **#{req_id}** {media_type} {title} — by {requester}")

    return _truncate("\n".join(lines))


async def approve_request(request_id: int) -> str:
    """Approve a pending Overseerr media request by its numeric ID."""
    try:
        rid = int(request_id)
    except (ValueError, TypeError):
        return f"❌ Invalid request ID: `{request_id}` — must be a number."
    result = await _post(f"/request/{rid}/approve")
    if isinstance(result, str):
        return f"❌ Failed to approve request #{rid}: {result}"
    return f"✅ Request #{rid} approved."


async def deny_request(request_id: int) -> str:
    """Decline a pending Overseerr media request by its numeric ID."""
    try:
        rid = int(request_id)
    except (ValueError, TypeError):
        return f"❌ Invalid request ID: `{request_id}` — must be a number."
    result = await _post(f"/request/{rid}/decline")
    if isinstance(result, str):
        return f"❌ Failed to decline request #{rid}: {result}"
    return f"❌ Request #{rid} declined."


async def get_request_stats() -> str:
    """Get summary statistics of all Overseerr media requests by status."""
    filters = ["all", "pending", "approved", "available", "processing"]
    tasks = [_get(f"/request?take=1&filter={f}") for f in filters]
    results = await asyncio.gather(*tasks)

    def _count(d: dict | list | str) -> int | str:
        if isinstance(d, dict):
            return d.get("pageInfo", {}).get("results", 0)
        return "?"

    all_c, pending_c, approved_c, available_c, processing_c = [_count(r) for r in results]
    lines = [
        "**Overseerr Request Stats**",
        f"• Total:         {all_c}",
        f"• 🟡 Pending:    {pending_c}",
        f"• ✅ Approved:   {approved_c}",
        f"• 🔄 Processing: {processing_c}",
        f"• 📦 Available:  {available_c}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

OVERSEERR_SKILLS = {
    "get_pending_requests": get_pending_requests,
    "approve_request": approve_request,
    "deny_request": deny_request,
    "get_request_stats": get_request_stats,
}
