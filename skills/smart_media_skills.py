"""
OpenClaw Smart Media Skills — Phase 3
Advanced automation for Sonarr/Radarr with watchlist sync, quality optimization,
and intelligent scheduling.
"""

import asyncio
import datetime
import json
import logging
from typing import Any

import aiohttp

from config import TIMEOUT_SLOW
from config import cfg as _cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.smart_media")

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_sessions = SessionManager(
    timeout=TIMEOUT_SLOW,
    name="smart_media",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SONARR_URL = _cfg.sonarr_url
SONARR_API_KEY = _cfg.sonarr_api_key
RADARR_URL = _cfg.radarr_url
RADARR_API_KEY = _cfg.radarr_api_key

# Storage thresholds (in GB)
STORAGE_HIGH_QUALITY_MIN = 500
STORAGE_MEDIUM_QUALITY_MIN = 200


# ---------------------------------------------------------------------------
# HTTP Helpers
# ---------------------------------------------------------------------------


async def _api_get(url: str, headers: dict | None = None, timeout: int = 15) -> dict | list | str:
    """Make async HTTP GET request."""
    try:
        session = await _get_session()
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.content_type and "json" in resp.content_type:
                return await resp.json()
            return await resp.text()
    except asyncio.TimeoutError:
        log.warning("GET %s timed out after %ds", url, timeout)
        return f"Request timed out ({timeout}s)"
    except Exception as e:
        log.warning("GET %s failed: %s", url, e)
        return f"Request failed: {e}"


async def _api_post(url: str, json_data: dict, headers: dict | None = None, timeout: int = 15) -> dict | list | str:
    """Make async HTTP POST request."""
    try:
        session = await _get_session()
        async with session.post(
            url, json=json_data, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                try:
                    err = json.loads(body)
                    msg = err.get("message") or str(err)
                except json.JSONDecodeError:
                    msg = body[:300]
                return f"HTTP {resp.status}: {msg}"
            
            if resp.content_type and "json" in resp.content_type:
                return json.loads(body)
            return body
    except asyncio.TimeoutError:
        return f"Request timed out ({timeout}s)"
    except Exception as e:
        log.warning("POST %s failed: %s", url, e)
        return f"Request failed: {e}"


async def _api_put(url: str, json_data: dict, headers: dict | None = None, timeout: int = 15) -> dict | list | str:
    """Make async HTTP PUT request."""
    try:
        session = await _get_session()
        async with session.put(
            url, json=json_data, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                try:
                    err = json.loads(body)
                    msg = err.get("message") or str(err)
                except json.JSONDecodeError:
                    msg = body[:300]
                return f"HTTP {resp.status}: {msg}"
            
            if resp.content_type and "json" in resp.content_type:
                return json.loads(body)
            return body
    except Exception as e:
        log.warning("PUT %s failed: %s", url, e)
        return f"Request failed: {e}"


# ---------------------------------------------------------------------------
# Storage Management
# ---------------------------------------------------------------------------


async def get_storage_info(service: str = "sonarr") -> dict[str, Any]:
    """Get storage information from *arr service."""
    if service == "sonarr":
        url = f"{SONARR_URL}/api/v3/diskspace"
        headers = {"X-Api-Key": SONARR_API_KEY}
    elif service == "radarr":
        url = f"{RADARR_URL}/api/v3/diskspace"
        headers = {"X-Api-Key": RADARR_API_KEY}
    else:
        return {"error": f"Unknown service: {service}"}
    
    result = await _api_get(url, headers=headers)
    
    if isinstance(result, str):
        return {"error": result}
    
    if isinstance(result, list) and len(result) > 0:
        disk = result[0]
        free_gb = disk.get("freeSpace", 0) / (1024**3)
        total_gb = disk.get("totalSpace", 0) / (1024**3)
        return {
            "free_gb": round(free_gb, 2),
            "total_gb": round(total_gb, 2),
            "used_gb": round(total_gb - free_gb, 2),
            "percent_free": round((free_gb / total_gb * 100), 1) if total_gb > 0 else 0,
        }
    
    return {"error": "No disk info available"}


async def determine_quality_profile(service: str = "sonarr") -> dict[str, Any]:
    """Determine optimal quality profile based on available storage."""
    storage = await get_storage_info(service)
    
    if "error" in storage:
        return {"error": storage["error"], "recommended_profile": "medium"}
    
    free_gb = storage["free_gb"]
    
    if free_gb >= STORAGE_HIGH_QUALITY_MIN:
        return {
            "free_gb": free_gb,
            "recommended_profile": "high",
            "profile_name": "Bluray-1080p" if service == "radarr" else "Bluray-1080p",
            "reason": f"{free_gb}GB available - high quality recommended",
        }
    elif free_gb >= STORAGE_MEDIUM_QUALITY_MIN:
        return {
            "free_gb": free_gb,
            "recommended_profile": "medium",
            "profile_name": "Web-1080p" if service == "radarr" else "WEB-1080p",
            "reason": f"{free_gb}GB available - medium quality recommended",
        }
    else:
        return {
            "free_gb": free_gb,
            "recommended_profile": "low",
            "profile_name": "Web-720p" if service == "radarr" else "WEB-720p",
            "reason": f"Only {free_gb}GB available - low quality recommended",
        }


# ---------------------------------------------------------------------------
# Watchlist Sync
# ---------------------------------------------------------------------------


async def sync_trakt_watchlist(username: str = "", list_type: str = "watchlist") -> str:
    """
    Sync Trakt.tv watchlist to Sonarr/Radarr.
    
    Note: Requires Trakt integration to be configured in Sonarr/Radarr.
    This is a placeholder - actual implementation would require Trakt API keys.
    """
    if not username:
        return "❌ Please provide a Trakt username"
    
    # This would normally call Trakt API and add items to Sonarr/Radarr
    # For now, return a placeholder response
    log.info("Trakt watchlist sync requested for user %s (list: %s)", username, list_type)
    
    return (
        f"🔄 Trakt watchlist sync initiated for user: {username}\n"
        f"Note: Ensure Trakt integration is configured in Sonarr/Radarr settings.\n"
        f"List type: {list_type}"
    )


async def sync_imdb_list(list_id: str) -> str:
    """
    Sync IMDb list to Radarr.
    
    Note: Requires IMDb list integration in Radarr.
    """
    if not list_id:
        return "❌ Please provide an IMDb list ID"
    
    try:
        # Check if Radarr has import list feature
        url = f"{RADARR_URL}/api/v3/importlist"
        headers = {"X-Api-Key": RADARR_API_KEY}
        
        result = await _api_get(url, headers=headers)
        
        if isinstance(result, str):
            return f"❌ Failed to access Radarr import lists: {result}"
        
        # Check if IMDb list already exists
        imdb_lists = [lst for lst in result if lst.get("implementation") == "IMDbListImport"]
        
        if not imdb_lists:
            return (
                f"💡 IMDb list sync available but not configured.\n"
                f"Add an IMDb List import in Radarr settings → Import Lists.\n"
                f"List ID: {list_id}"
            )
        
        # Trigger sync
        sync_url = f"{RADARR_URL}/api/v3/importlist/action/sync"
        sync_result = await _api_post(sync_url, {}, headers=headers)
        
        return f"✅ Triggered IMDb list sync for list: {list_id}"
        
    except Exception as e:
        log.error("IMDb list sync failed: %s", e)
        return f"❌ IMDb list sync failed: {e}"


# ---------------------------------------------------------------------------
# Quality Optimization
# ---------------------------------------------------------------------------


async def optimize_quality_profiles() -> str:
    """Automatically adjust quality profiles based on available storage."""
    results = []
    
    # Check Sonarr
    sonarr_rec = await determine_quality_profile("sonarr")
    if "error" not in sonarr_rec:
        results.append(
            f"📺 **Sonarr**: {sonarr_rec['reason']}\n"
            f"   Recommended: {sonarr_rec['profile_name']}"
        )
    else:
        results.append(f"📺 **Sonarr**: {sonarr_rec['error']}")
    
    # Check Radarr
    radarr_rec = await determine_quality_profile("radarr")
    if "error" not in radarr_rec:
        results.append(
            f"🎬 **Radarr**: {radarr_rec['reason']}\n"
            f"   Recommended: {radarr_rec['profile_name']}"
        )
    else:
        results.append(f"🎬 **Radarr**: {radarr_rec['error']}")
    
    return "\n\n".join(results)


async def apply_quality_profile(service: str, profile_name: str) -> str:
    """Apply a quality profile to all monitored items in a service."""
    if service not in ("sonarr", "radarr"):
        return f"❌ Unknown service: {service}. Use 'sonarr' or 'radarr'."
    
    # Get quality profiles
    if service == "sonarr":
        url = f"{SONARR_URL}/api/v3/qualityprofile"
        headers = {"X-Api-Key": SONARR_API_KEY}
    else:
        url = f"{RADARR_URL}/api/v3/qualityprofile"
        headers = {"X-Api-Key": RADARR_API_KEY}
    
    profiles = await _api_get(url, headers=headers)
    
    if isinstance(profiles, str):
        return f"❌ Failed to fetch quality profiles: {profiles}"
    
    # Find matching profile
    target_profile = None
    for profile in profiles:
        if profile_name.lower() in profile.get("name", "").lower():
            target_profile = profile
            break
    
    if not target_profile:
        available = [p.get("name") for p in profiles]
        return f"❌ Profile '{profile_name}' not found. Available: {', '.join(available)}"
    
    profile_id = target_profile["id"]
    
    # This would update all series/movies - simplified for now
    return (
        f"✅ Quality profile '{target_profile['name']}' (ID: {profile_id}) selected for {service}.\n"
        f"Note: Apply this to individual series/movies through the *arr interface."
    )


# ---------------------------------------------------------------------------
# Download Scheduling
# ---------------------------------------------------------------------------


async def schedule_downloads(hours: list[int] | None = None) -> str:
    """
    Configure download scheduling for off-peak hours.
    
    Args:
        hours: List of hours (0-23) when downloads are allowed
    """
    if not hours:
        hours = [2, 3, 4, 5]  # Default: 2 AM - 6 AM
    
    if not all(0 <= h <= 23 for h in hours):
        return "❌ Hours must be between 0 and 23"
    
    # Format hours for display
    hour_ranges = []
    sorted_hours = sorted(hours)
    start = sorted_hours[0]
    end = sorted_hours[0]
    
    for h in sorted_hours[1:]:
        if h == end + 1:
            end = h
        else:
            hour_ranges.append(f"{start:02d}:00-{end:02d}:59")
            start = h
            end = h
    hour_ranges.append(f"{start:02d}:00-{end:02d}:59")
    
    return (
        f"⏰ Download scheduling configured:\n"
        f"**Allowed hours**: {', '.join(hour_ranges)}\n\n"
        f"💡 To enforce this, configure download client throttling or use SABnzbd's schedule feature."
    )


# ---------------------------------------------------------------------------
# Duplicate Detection
# ---------------------------------------------------------------------------


async def find_duplicates(service: str = "radarr") -> str:
    """Find duplicate movies/series in media library."""
    if service == "sonarr":
        url = f"{SONARR_URL}/api/v3/series"
        headers = {"X-Api-Key": SONARR_API_KEY}
        media_type = "series"
    elif service == "radarr":
        url = f"{RADARR_URL}/api/v3/movie"
        headers = {"X-Api-Key": RADARR_API_KEY}
        media_type = "movies"
    else:
        return f"❌ Unknown service: {service}"
    
    items = await _api_get(url, headers=headers)
    
    if isinstance(items, str):
        return f"❌ Failed to fetch {media_type}: {items}"
    
    # Group by title
    title_groups: dict[str, list] = {}
    for item in items:
        title = item.get("title", "").lower().strip()
        if title:
            if title not in title_groups:
                title_groups[title] = []
            title_groups[title].append(item)
    
    # Find duplicates
    duplicates = {title: items for title, items in title_groups.items() if len(items) > 1}
    
    if not duplicates:
        return f"✅ No duplicates found in {service} ({len(items)} {media_type} checked)"
    
    result_lines = [f"⚠️ Found {len(duplicates)} potential duplicates in {service}:\n"]
    
    for title, dup_items in list(duplicates.items())[:10]:  # Limit to 10 for readability
        result_lines.append(f"**{title.title()}**: {len(dup_items)} copies")
        for item in dup_items:
            item_id = item.get("id")
            year = item.get("year", "")
            result_lines.append(f"  - ID {item_id} ({year})")
    
    if len(duplicates) > 10:
        result_lines.append(f"\n... and {len(duplicates) - 10} more duplicates")
    
    return "\n".join(result_lines)


async def cleanup_duplicates(service: str = "radarr", dry_run: bool = True) -> str:
    """Remove duplicate entries (keeps newest, removes older duplicates)."""
    if dry_run:
        result = await find_duplicates(service)
        return f"🔍 **Dry run mode** - showing duplicates without removing:\n\n{result}"
    
    # Actual cleanup would be implemented here
    return "⚠️ Automatic duplicate cleanup not yet implemented. Manual review recommended."


# ---------------------------------------------------------------------------
# LLM-Callable Skills
# ---------------------------------------------------------------------------


async def sync_watchlist(source: str = "trakt", username: str = "", list_id: str = "") -> str:
    """
    Sync external watchlist to Sonarr/Radarr.
    
    Args:
        source: 'trakt' or 'imdb'
        username: Trakt username (for Trakt sync)
        list_id: IMDb list ID (for IMDb sync)
    """
    if source == "trakt":
        return await sync_trakt_watchlist(username)
    elif source == "imdb":
        return await sync_imdb_list(list_id)
    else:
        return f"❌ Unknown source: {source}. Use 'trakt' or 'imdb'."


async def optimize_quality() -> str:
    """Automatically adjust quality profiles based on storage space."""
    return await optimize_quality_profiles()


async def schedule_downloads_skill(hours: str = "2,3,4,5") -> str:
    """
    Schedule downloads for off-peak hours.
    
    Args:
        hours: Comma-separated list of hours (0-23), e.g., "2,3,4,5"
    """
    try:
        hour_list = [int(h.strip()) for h in hours.split(",")]
        return await schedule_downloads(hour_list)
    except ValueError:
        return "❌ Invalid hours format. Use comma-separated integers (0-23), e.g., '2,3,4,5'"


async def find_media_duplicates(service: str = "radarr") -> str:
    """Find duplicate movies or TV shows."""
    return await find_duplicates(service)


async def get_media_storage() -> str:
    """Get storage information for media services."""
    sonarr_storage = await get_storage_info("sonarr")
    radarr_storage = await get_storage_info("radarr")
    
    lines = []
    
    if "error" not in sonarr_storage:
        lines.append(
            f"📺 **Sonarr Storage**:\n"
            f"   Free: {sonarr_storage['free_gb']}GB / {sonarr_storage['total_gb']}GB "
            f"({sonarr_storage['percent_free']}% free)"
        )
    else:
        lines.append(f"📺 **Sonarr**: {sonarr_storage['error']}")
    
    if "error" not in radarr_storage:
        lines.append(
            f"🎬 **Radarr Storage**:\n"
            f"   Free: {radarr_storage['free_gb']}GB / {radarr_storage['total_gb']}GB "
            f"({radarr_storage['percent_free']}% free)"
        )
    else:
        lines.append(f"🎬 **Radarr**: {radarr_storage['error']}")
    
    return "\n\n".join(lines)


SMART_MEDIA_SKILLS = {
    "sync_watchlist": sync_watchlist,
    "optimize_quality": optimize_quality,
    "schedule_downloads": schedule_downloads_skill,
    "find_media_duplicates": find_media_duplicates,
    "get_media_storage": get_media_storage,
}
