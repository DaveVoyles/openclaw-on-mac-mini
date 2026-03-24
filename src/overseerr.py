"""
OpenClaw Overseerr Skill — Phase 6
Manage media requests via the Overseerr REST API.

Requires: OVERSEERR_URL, OVERSEERR_API_KEY in .env
(OVERSEERR_API_KEY is already present in advanced_skills.py config)
"""

import asyncio
import logging
import os

import aiohttp

log = logging.getLogger("openclaw.overseerr")

OVERSEERR_URL = os.getenv("OVERSEERR_URL", "http://192.168.1.93:5055")
OVERSEERR_API_KEY = os.getenv("OVERSEERR_API_KEY", "")

_http_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
    return _http_session


async def close_session() -> None:
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


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
        async with session.get(
            f"{OVERSEERR_URL}/api/v1{path}", headers=headers
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            try:
                text = await resp.text()
            except Exception:
                text = "[could not read response body]"
            return f"HTTP {resp.status}: {text[:200]}"
    except asyncio.TimeoutError:
        return "Request timed out (10s)"
    except Exception as e:
        return f"Request failed: {e}"


async def _post(path: str) -> dict | str:
    if not OVERSEERR_API_KEY:
        return "❌ `OVERSEERR_API_KEY` not configured."
    headers = {"X-Api-Key": OVERSEERR_API_KEY}
    try:
        session = await _get_session()
        async with session.post(
            f"{OVERSEERR_URL}/api/v1{path}", headers=headers
        ) as resp:
            if resp.status in (200, 201, 204):
                return {} if resp.status == 204 else await resp.json()
            try:
                text = await resp.text()
            except Exception:
                text = "[could not read response body]"
            return f"HTTP {resp.status}: {text[:200]}"
    except asyncio.TimeoutError:
        return "Request timed out (10s)"
    except Exception as e:
        return f"Request failed: {e}"


def _media_title(media: dict) -> str:
    return (
        media.get("title")
        or media.get("originalTitle")
        or media.get("name")
        or f"TMDB #{media.get('tmdbId', '?')}"
    )


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
        requester = (
            r.get("requestedBy", {}).get("displayName")
            or r.get("requestedBy", {}).get("username", "Unknown")
        )
        lines.append(f"• **#{req_id}** {media_type} {title} — by {requester}")

    return _truncate("\n".join(lines))


async def approve_request(request_id: int) -> str:
    """Approve a pending Overseerr media request by its numeric ID."""
    result = await _post(f"/request/{int(request_id)}/approve")
    if isinstance(result, str):
        return f"❌ Failed to approve request #{request_id}: {result}"
    return f"✅ Request #{request_id} approved."


async def deny_request(request_id: int) -> str:
    """Decline a pending Overseerr media request by its numeric ID."""
    result = await _post(f"/request/{int(request_id)}/decline")
    if isinstance(result, str):
        return f"❌ Failed to decline request #{request_id}: {result}"
    return f"❌ Request #{request_id} declined."


async def get_request_stats() -> str:
    """Get summary statistics of all Overseerr media requests by status."""
    filters = ["all", "pending", "approved", "available", "processing"]
    tasks = [_get(f"/request?take=1&filter={f}") for f in filters]
    results = await asyncio.gather(*tasks)

    def _count(d: dict | list | str) -> int | str:
        if isinstance(d, dict):
            return d.get("pageInfo", {}).get("results", 0)
        return "?"

    all_c, pending_c, approved_c, available_c, processing_c = [
        _count(r) for r in results
    ]
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
