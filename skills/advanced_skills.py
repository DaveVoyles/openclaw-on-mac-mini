"""
OpenClaw Advanced Skills — Phase 5: Media, Network, Plex, Reports
Each skill is a standalone async function returning a string result.
API keys are loaded from environment variables set in .env.
"""

import asyncio
import logging
import os
import shlex
import ssl
import socket
import datetime
from typing import Optional

log = logging.getLogger("openclaw.advanced_skills")

# ---------------------------------------------------------------------------
# Configuration — loaded from environment
# ---------------------------------------------------------------------------

HOST = os.getenv("DOCKER_HOST_IP", "192.168.1.93")

# *arr services
SONARR_URL = os.getenv("SONARR_URL", f"http://{HOST}:8989")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")
RADARR_URL = os.getenv("RADARR_URL", f"http://{HOST}:7878")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
LIDARR_URL = os.getenv("LIDARR_URL", f"http://{HOST}:8686")
LIDARR_API_KEY = os.getenv("LIDARR_API_KEY", "")
PROWLARR_URL = os.getenv("PROWLARR_URL", f"http://{HOST}:9696")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY", "")

# Download clients
SABNZBD_URL = os.getenv("SABNZBD_URL", f"http://{HOST}:8775")
SABNZBD_API_KEY = os.getenv("SABNZBD_API_KEY", "")
QBIT_URL = os.getenv("QBIT_URL", f"http://{HOST}:8080")

# Plex / Tautulli
TAUTULLI_URL = os.getenv("TAUTULLI_URL", f"http://{HOST}:8181")
TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY", "")

# Overseerr
OVERSEERR_URL = os.getenv("OVERSEERR_URL", f"http://{HOST}:5055")
OVERSEERR_API_KEY = os.getenv("OVERSEERR_API_KEY", "")

COMMAND_TIMEOUT = 15

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


async def _api_get(url: str, headers: dict | None = None, timeout: int = 10) -> dict | list | str:
    """Make an async HTTP GET request and return JSON or text."""
    import aiohttp
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.content_type and "json" in resp.content_type:
                    return await resp.json()
                return await resp.text()
    except asyncio.TimeoutError:
        return f"Request timed out ({timeout}s)"
    except Exception as e:
        return f"Request failed: {e}"


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# 6.3  Service Health Checks
# ---------------------------------------------------------------------------


async def check_arr_health() -> str:
    """Query all *arr services for system health status."""
    services = [
        ("Sonarr", SONARR_URL, SONARR_API_KEY, "/api/v3/health"),
        ("Radarr", RADARR_URL, RADARR_API_KEY, "/api/v3/health"),
        ("Lidarr", LIDARR_URL, LIDARR_API_KEY, "/api/v1/health"),
        ("Prowlarr", PROWLARR_URL, PROWLARR_API_KEY, "/api/v1/health"),
    ]
    lines = []
    for name, url, key, endpoint in services:
        if not key:
            lines.append(f"⚠️ **{name}**: API key not configured")
            continue
        data = await _api_get(
            f"{url}{endpoint}",
            headers={"X-Api-Key": key},
        )
        if isinstance(data, list):
            if not data:
                lines.append(f"✅ **{name}**: Healthy")
            else:
                issues = ", ".join(d.get("message", "?") for d in data[:3])
                lines.append(f"⚠️ **{name}**: {issues}")
        else:
            lines.append(f"❌ **{name}**: {data}")
    return "\n".join(lines) or "No services configured."


async def check_download_clients() -> str:
    """Check connectivity to SABnzbd and qBittorrent."""
    lines = []

    # SABnzbd
    if SABNZBD_API_KEY:
        data = await _api_get(
            f"{SABNZBD_URL}/api?mode=version&output=json&apikey={SABNZBD_API_KEY}"
        )
        if isinstance(data, dict) and "version" in data:
            lines.append(f"✅ **SABnzbd**: v{data['version']}")
        else:
            lines.append(f"❌ **SABnzbd**: {data}")
    else:
        lines.append("⚠️ **SABnzbd**: API key not configured")

    # qBittorrent (no auth needed for version endpoint sometimes)
    data = await _api_get(f"{QBIT_URL}/api/v2/app/version")
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


# ---------------------------------------------------------------------------
# 6.4  Media Automation
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


async def get_download_queue() -> str:
    """Get combined download queue from SABnzbd and qBittorrent."""
    lines = []

    # SABnzbd queue
    if SABNZBD_API_KEY:
        data = await _api_get(
            f"{SABNZBD_URL}/api?mode=queue&output=json&apikey={SABNZBD_API_KEY}"
        )
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

    # qBittorrent active torrents
    data = await _api_get(f"{QBIT_URL}/api/v2/torrents/info?filter=active")
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
# 6.6  Network & Connectivity
# ---------------------------------------------------------------------------


async def ping_host(host: str) -> str:
    """Ping a host and return latency info."""
    # Validate host - only allow IPs and hostnames
    safe_host = shlex.quote(host).strip("'")
    if not all(c.isalnum() or c in ".-_" for c in safe_host):
        return "❌ Invalid hostname."

    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "3", "-W", "3", safe_host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode()
        # Extract summary line
        for line in output.split("\n"):
            if "round-trip" in line or "rtt" in line:
                return f"✅ **{host}**: {line.strip()}"
            if "avg" in line.lower():
                return f"✅ **{host}**: {line.strip()}"
        if proc.returncode == 0:
            return f"✅ **{host}**: reachable"
        return f"❌ **{host}**: unreachable"
    except asyncio.TimeoutError:
        proc.kill()
        return f"❌ **{host}**: ping timed out"


async def check_service_ports() -> str:
    """Check if key services are listening on expected ports."""
    services = [
        ("Sonarr", HOST, 8989),
        ("Radarr", HOST, 7878),
        ("Lidarr", HOST, 8686),
        ("Prowlarr", HOST, 9696),
        ("SABnzbd", HOST, 8775),
        ("qBittorrent", HOST, 8080),
        ("Tautulli", HOST, 8181),
        ("Overseerr", HOST, 5055),
        ("Glances", HOST, 61208),
        ("OpenClaw", HOST, 8765),
    ]
    lines = []
    for name, host, port in services:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                lines.append(f"✅ **{name}** (:{port})")
            else:
                lines.append(f"❌ **{name}** (:{port}) — not responding")
        except Exception as e:
            lines.append(f"❌ **{name}** (:{port}) — {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6.5  Status Report Generation
# ---------------------------------------------------------------------------


async def create_status_report() -> str:
    """Generate a comprehensive system status report."""
    sections = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections.append(f"**System Status Report** — {timestamp}\n")

    # Service health
    sections.append("### Service Health")
    health = await check_arr_health()
    sections.append(health)

    # Download clients
    sections.append("\n### Download Clients")
    dl = await check_download_clients()
    sections.append(dl)

    # Plex
    sections.append("\n### Plex")
    plex = await check_plex_status()
    sections.append(plex)

    # Active downloads
    sections.append("\n### Active Downloads")
    queue = await get_download_queue()
    sections.append(queue)

    report = "\n".join(sections)
    return _truncate(report)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ADVANCED_SKILLS = {
    "check_arr_health": check_arr_health,
    "check_download_clients": check_download_clients,
    "check_plex_status": check_plex_status,
    "search_media": search_media,
    "get_download_queue": get_download_queue,
    "get_recent_additions": get_recent_additions,
    "ping_host": ping_host,
    "check_service_ports": check_service_ports,
    "create_status_report": create_status_report,
}
