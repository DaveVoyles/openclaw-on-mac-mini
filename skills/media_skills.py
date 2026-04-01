"""
OpenClaw Media Skills — *arr services, Plex, download clients.
Extracted from advanced_skills.py for modularity.
"""

import asyncio
import datetime
import json
import logging

import aiohttp

from config import TIMEOUT_SLOW
from config import cfg as _cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.media_skills")

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_sessions = SessionManager(
    timeout=TIMEOUT_SLOW,
    name="media_skills",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Media service configuration
# ---------------------------------------------------------------------------

HOST = _cfg.docker_host_ip

# *arr services
SONARR_URL = _cfg.sonarr_url
SONARR_API_KEY = _cfg.sonarr_api_key
RADARR_URL = _cfg.radarr_url
RADARR_API_KEY = _cfg.radarr_api_key
LIDARR_URL = _cfg.lidarr_url
LIDARR_API_KEY = _cfg.lidarr_api_key
PROWLARR_URL = _cfg.prowlarr_url
PROWLARR_API_KEY = _cfg.prowlarr_api_key

# Download clients
SABNZBD_URL = _cfg.sabnzbd_url
SABNZBD_API_KEY = _cfg.sabnzbd_api_key
QBIT_URL = _cfg.qbit_url

# Plex / Tautulli
TAUTULLI_URL = _cfg.tautulli_url
TAUTULLI_API_KEY = _cfg.tautulli_api_key

# Overseerr
OVERSEERR_URL = _cfg.overseerr_url
OVERSEERR_API_KEY = _cfg.overseerr_api_key

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _api_get(url: str, headers: dict | None = None, timeout: int = 10) -> dict | list | str:
    """Make an async HTTP GET request and return JSON or text."""
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
        log.warning("GET %s failed (%s): %s", url, type(e).__name__, e)
        return f"Request failed: {e}"


async def _api_post(url: str, json_data: dict, headers: dict | None = None, timeout: int = 15) -> dict | list | str:
    """Make an async HTTP POST request and return JSON or text."""
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
                    msg = err.get("message") or err.get("errorMessage") or str(err)
                except (json.JSONDecodeError, AttributeError):
                    msg = body[:300]
                return f"HTTP {resp.status}: {msg}"
            if resp.content_type and "json" in resp.content_type:
                return json.loads(body)
            return body
    except asyncio.TimeoutError:
        log.warning("POST %s timed out after %ds", url, timeout)
        return f"Request timed out ({timeout}s)"
    except Exception as e:
        log.warning("POST %s failed (%s): %s", url, type(e).__name__, e)
        return f"Request failed: {e}"


# ---------------------------------------------------------------------------
# Service Health Checks
# ---------------------------------------------------------------------------


async def check_arr_health() -> str:
    """Query all *arr services for system health status (parallel requests)."""
    services = [
        ("Sonarr", SONARR_URL, SONARR_API_KEY, "/api/v3/health"),
        ("Radarr", RADARR_URL, RADARR_API_KEY, "/api/v3/health"),
        ("Lidarr", LIDARR_URL, LIDARR_API_KEY, "/api/v1/health"),
        ("Prowlarr", PROWLARR_URL, PROWLARR_API_KEY, "/api/v1/health"),
    ]
    configured = [(n, u, k, e) for n, u, k, e in services if k]
    unconfigured = [n for n, _, k, _ in services if not k]

    tasks = [
        _api_get(f"{url}{endpoint}", headers={"X-Api-Key": key})
        for _, url, key, endpoint in configured
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"⚠️ **{n}**: API key not configured" for n in unconfigured]
    for (name, _, _, _), data in zip(configured, results):
        if isinstance(data, Exception):
            lines.append(f"❌ **{name}**: {data}")
        elif isinstance(data, list):
            if not data:
                lines.append(f"✅ **{name}**: Healthy")
            else:
                issues = ", ".join(d.get("message", "?") for d in data[:3])
                lines.append(f"⚠️ **{name}**: {issues}")
        else:
            lines.append(f"❌ **{name}**: {data}")
    return "\n".join(lines) or "No services configured."


async def check_download_clients() -> str:
    """Check connectivity to SABnzbd and qBittorrent (parallel requests)."""
    tasks: list = []
    task_labels: list[str] = []

    if SABNZBD_API_KEY:
        tasks.append(_api_get(f"{SABNZBD_URL}/api?mode=version&output=json&apikey={SABNZBD_API_KEY}"))
        task_labels.append("sabnzbd")

    qbit_configured = bool(QBIT_URL)
    if qbit_configured:
        tasks.append(_api_get(f"{QBIT_URL}/api/v2/app/version"))
        task_labels.append("qbit")

    if not tasks:
        return "⚠️ No download clients configured (set SABNZBD_API_KEY and/or QBIT_URL in .env)"

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    lines = []

    for label, data in zip(task_labels, gathered):
        if label == "sabnzbd":
            if isinstance(data, dict) and "version" in data:
                lines.append(f"✅ **SABnzbd**: v{data['version']}")
            else:
                lines.append(f"❌ **SABnzbd**: {data}")
        elif label == "qbit":
            if isinstance(data, str) and data.startswith("v"):
                lines.append(f"✅ **qBittorrent**: {data.strip()}")
            else:
                lines.append(f"⚠️ **qBittorrent**: {data}")

    return "\n".join(lines)


async def check_plex_status() -> str:
    """Check Plex server status via Tautulli API."""
    if not TAUTULLI_API_KEY:
        return "⚠️ Tautulli API key not configured. Set TAUTULLI_API_KEY in .env."

    data = await _api_get(
        f"{TAUTULLI_URL}/api/v2?apikey={TAUTULLI_API_KEY}&cmd=server_info"
    )
    if isinstance(data, dict):
        resp = data.get("response", {})
        if resp.get("result") == "success":
            info = resp.get("data", {})
            name = info.get("pms_name", "Plex")
            version = info.get("pms_version", "?")
            platform = info.get("pms_platform", "?")
            return f"✅ **{name}**: v{version} ({platform})"
        return f"❌ Plex: {resp.get('message', 'unknown error')}"
    return f"❌ Plex: {data}"


async def get_plex_activity() -> str:
    """Get real-time Plex activity: who is watching what, progress, quality, and stream type.

    Uses the Tautulli /api/v2?cmd=get_activity endpoint which returns current streams.
    Returns a formatted summary of active sessions or "Nothing playing" if idle.
    """
    if not TAUTULLI_API_KEY:
        return "⚠️ Tautulli API key not configured. Set TAUTULLI_API_KEY in .env."

    data = await _api_get(
        f"{TAUTULLI_URL}/api/v2?apikey={TAUTULLI_API_KEY}&cmd=get_activity"
    )
    if not isinstance(data, dict):
        return f"❌ Plex activity: {data}"

    resp = data.get("response", {})
    if resp.get("result") != "success":
        return f"❌ Plex activity: {resp.get('message', 'unknown error')}"

    activity = resp.get("data", {})
    stream_count = activity.get("stream_count", 0)

    if not stream_count or stream_count == 0:
        return "🎬 Plex — nothing currently playing."

    sessions = activity.get("sessions", [])
    lines: list[str] = [f"🎬 **{stream_count} active stream{'s' if stream_count != 1 else ''}**"]

    for s in sessions:
        user = s.get("friendly_name") or s.get("username", "Unknown")
        title = s.get("full_title") or s.get("title", "Unknown title")
        media_type = s.get("media_type", "")
        type_icon = "📺" if media_type == "episode" else "🎬" if media_type == "movie" else "🎵"

        progress_pct = s.get("progress_percent", 0)
        view_offset = int(s.get("view_offset", 0)) // 1000
        duration = int(s.get("duration", 0)) // 1000
        time_str = f"{view_offset // 60}:{view_offset % 60:02d}/{duration // 60}:{duration % 60:02d}" if duration else ""

        quality = s.get("quality_profile") or s.get("stream_video_resolution") or "?"
        transcode = s.get("transcode_decision", "")
        stream_type = "🔄 Transcode" if transcode == "transcode" else "▶️ Direct"
        player = s.get("player", "")
        platform = s.get("platform", "")

        lines.append(
            f"{type_icon} **{user}** — {title} "
            f"({progress_pct}% · {time_str}) "
            f"[{quality} · {stream_type}]"
            + (f" on {player}/{platform}" if player else "")
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Media Automation
# ---------------------------------------------------------------------------


async def search_media(query: str, media_type: str = "all") -> str:
    """
    Search for media across Sonarr (TV) and Radarr (Movies).
    media_type: 'tv', 'movie', or 'all' (default).
    """
    results = []

    if media_type in ("tv", "all") and SONARR_API_KEY:
        data = await _api_get(
            f"{SONARR_URL}/api/v3/series/lookup?term={query}",
            headers={"X-Api-Key": SONARR_API_KEY},
        )
        if isinstance(data, list):
            for item in data[:5]:
                title = item.get("title", "?")
                year = item.get("year", "?")
                status = item.get("status", "?")
                results.append(f"📺 **{title}** ({year}) — {status}")

    if media_type in ("movie", "all") and RADARR_API_KEY:
        data = await _api_get(
            f"{RADARR_URL}/api/v3/movie/lookup?term={query}",
            headers={"X-Api-Key": RADARR_API_KEY},
        )
        if isinstance(data, list):
            for item in data[:5]:
                title = item.get("title", "?")
                year = item.get("year", "?")
                studio = item.get("studio", "")
                results.append(f"🎬 **{title}** ({year}) {studio}".strip())

    if not results:
        return f"No results found for '{query}'."
    return "\n".join(results[:10])


async def add_to_sonarr(title: str, tvdb_id: int = 0) -> str:
    """Add a TV show to Sonarr for monitoring and automatic downloading."""
    if not SONARR_API_KEY:
        return "❌ Sonarr API key not configured."

    headers = {"X-Api-Key": SONARR_API_KEY}

    if tvdb_id:
        series_data = {"title": title, "tvdbId": tvdb_id}
    else:
        lookup = await _api_get(
            f"{SONARR_URL}/api/v3/series/lookup?term={title}",
            headers=headers,
        )
        if not isinstance(lookup, list) or not lookup:
            return f"❌ No TV show found matching '{title}' in Sonarr lookup."
        series_data = lookup[0]

    payload = {
        "title": series_data.get("title", title),
        "tvdbId": series_data.get("tvdbId", tvdb_id),
        "qualityProfileId": 1,
        "rootFolderPath": "/tv",
        "monitored": True,
        "addOptions": {"searchForMissingEpisodes": True},
    }

    result = await _api_post(
        f"{SONARR_URL}/api/v3/series", payload, headers=headers
    )
    if isinstance(result, str) and result.startswith("HTTP"):
        if "already" in result.lower() or "exist" in result.lower():
            return f"ℹ️ **{payload['title']}** is already in Sonarr."
        return f"❌ Failed to add to Sonarr: {result}"
    added_title = result.get("title", title) if isinstance(result, dict) else title
    return f"✅ Added **{added_title}** to Sonarr — searching for episodes."


async def add_to_radarr(title: str, tmdb_id: int = 0) -> str:
    """Add a movie to Radarr for monitoring and automatic downloading."""
    if not RADARR_API_KEY:
        return "❌ Radarr API key not configured."

    headers = {"X-Api-Key": RADARR_API_KEY}

    if tmdb_id:
        movie_data = {"title": title, "tmdbId": tmdb_id}
    else:
        lookup = await _api_get(
            f"{RADARR_URL}/api/v3/movie/lookup?term={title}",
            headers=headers,
        )
        if not isinstance(lookup, list) or not lookup:
            return f"❌ No movie found matching '{title}' in Radarr lookup."
        movie_data = lookup[0]

    payload = {
        "title": movie_data.get("title", title),
        "tmdbId": movie_data.get("tmdbId", tmdb_id),
        "qualityProfileId": 1,
        "rootFolderPath": "/movies",
        "monitored": True,
        "addOptions": {"searchForMovie": True},
    }

    result = await _api_post(
        f"{RADARR_URL}/api/v3/movie", payload, headers=headers
    )
    if isinstance(result, str) and result.startswith("HTTP"):
        if "already" in result.lower() or "exist" in result.lower():
            return f"ℹ️ **{payload['title']}** is already in Radarr."
        return f"❌ Failed to add to Radarr: {result}"
    added_title = result.get("title", title) if isinstance(result, dict) else title
    return f"✅ Added **{added_title}** to Radarr — searching for download."


async def get_download_queue() -> str:
    """Get combined download queue from SABnzbd and qBittorrent (parallel requests)."""
    lines = []

    tasks: list = []
    task_labels: list[str] = []
    if SABNZBD_API_KEY:
        tasks.append(_api_get(f"{SABNZBD_URL}/api?mode=queue&output=json&apikey={SABNZBD_API_KEY}"))
        task_labels.append("sabnzbd")
    qbit_configured = bool(QBIT_URL)
    if qbit_configured:
        tasks.append(_api_get(f"{QBIT_URL}/api/v2/torrents/info?filter=active"))
        task_labels.append("qbit")

    if not tasks:
        return "No download clients configured."

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    for label, data in zip(task_labels, gathered):
        if label == "sabnzbd":
            if isinstance(data, dict):
                queue = data.get("queue", {})
                slots = queue.get("slots", [])
                speed = queue.get("speed", "0 B/s")
                remaining = queue.get("timeleft", "N/A")
                if slots:
                    lines.append(f"**SABnzbd** ({len(slots)} items, {speed}, ETA: {remaining}):")
                    for s in slots[:5]:
                        name = s.get("filename", "?")[:50]
                        pct = s.get("percentage", "?")
                        size = s.get("sizeleft", "?")
                        lines.append(f"  • `{name}` — {pct}% ({size} left)")
                else:
                    lines.append("**SABnzbd**: Queue empty ✅")
        elif label == "qbit":
            if isinstance(data, list):
                if data:
                    lines.append(f"\n**qBittorrent** ({len(data)} active):")
                    for t in data[:5]:
                        name = t.get("name", "?")[:50]
                        progress = round(t.get("progress", 0) * 100, 1)
                        dlspeed = t.get("dlspeed", 0)
                        speed_str = f"{dlspeed / 1024 / 1024:.1f} MB/s" if dlspeed else "0"
                        lines.append(f"  • `{name}` — {progress}% ({speed_str})")
                else:
                    lines.append("**qBittorrent**: No active torrents ✅")

    return "\n".join(lines) or "No download clients configured."


async def get_recent_additions(limit: int = 10) -> str:
    """Get recently added media from Tautulli (Plex)."""
    if not TAUTULLI_API_KEY:
        return "⚠️ Tautulli API key not configured."

    data = await _api_get(
        f"{TAUTULLI_URL}/api/v2?apikey={TAUTULLI_API_KEY}"
        f"&cmd=get_recently_added&count={min(max(limit, 1), 25)}"
    )
    if not isinstance(data, dict):
        return f"❌ {data}"

    resp = data.get("response", {})
    if resp.get("result") != "success":
        return f"❌ {resp.get('message', 'unknown error')}"

    items = resp.get("data", {}).get("recently_added", [])
    if not items:
        return "No recent additions found."

    lines = []
    for item in items[:limit]:
        title = item.get("title", "?")
        year = item.get("year", "")
        media_type = item.get("media_type", "")
        added = item.get("added_at", "")
        if added:
            try:
                dt = datetime.datetime.fromtimestamp(int(added))
                added = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                pass
        icon = {"movie": "🎬", "show": "📺", "season": "📺", "episode": "📺"}.get(media_type, "🎵")
        year_str = f" ({year})" if year else ""
        lines.append(f"{icon} **{title}**{year_str} — added {added}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

MEDIA_SKILLS = {
    "check_arr_health": check_arr_health,
    "check_download_clients": check_download_clients,
    "check_plex_status": check_plex_status,
    "get_plex_activity": get_plex_activity,
    "search_media": search_media,
    "add_to_sonarr": add_to_sonarr,
    "add_to_radarr": add_to_radarr,
    "get_download_queue": get_download_queue,
    "get_recent_additions": get_recent_additions,
}
