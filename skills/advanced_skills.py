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
import sys
import json
import datetime
from pathlib import Path
from typing import Optional

import aiohttp

log = logging.getLogger("openclaw.advanced_skills")

# ---------------------------------------------------------------------------
# Shared HTTP session (created on first use, reused across all API calls)
# ---------------------------------------------------------------------------

from http_session import SessionManager

_sessions = SessionManager(
    timeout=30,
    name="advanced_skills",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Configuration — loaded from environment
# ---------------------------------------------------------------------------

from config import cfg as _cfg

HOST = os.getenv("DOCKER_HOST_IP", _cfg.docker_host_ip)

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

# Perplexity AI search (primary)
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

# Firecrawl (search + extract in one call)
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"

# Tavily web search
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_API_URL = "https://api.tavily.com/search"

# Paths to installed ClawHub skill scripts (relative to this file's directory)
_SKILLS_DIR = Path(__file__).parent
_TAVILY_SCRIPT = _SKILLS_DIR / "openclaw-tavily-search" / "scripts" / "tavily_search.py"
_DDG_SCRIPT = _SKILLS_DIR / "free-web-search" / "scripts" / "web_search.py"

COMMAND_TIMEOUT = 15

# Default location for weather queries (overridable via env var)
WEATHER_DEFAULT_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "Philadelphia, PA")

# ---------------------------------------------------------------------------
# HTTP helper
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


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# 6.3  Service Health Checks
# ---------------------------------------------------------------------------


async def check_arr_health() -> str:
    """Query all *arr services for system health status (parallel requests)."""
    services = [
        ("Sonarr", SONARR_URL, SONARR_API_KEY, "/api/v3/health"),
        ("Radarr", RADARR_URL, RADARR_API_KEY, "/api/v3/health"),
        ("Lidarr", LIDARR_URL, LIDARR_API_KEY, "/api/v1/health"),
        ("Prowlarr", PROWLARR_URL, PROWLARR_API_KEY, "/api/v1/health"),
    ]
    # Kick off all configured requests in parallel
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

    qbit_configured = bool(os.getenv("QBIT_URL", ""))
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
        view_offset = int(s.get("view_offset", 0)) // 1000  # ms → seconds
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


async def add_to_sonarr(title: str, tvdb_id: int = 0) -> str:
    """Add a TV show to Sonarr for monitoring and automatic downloading."""
    if not SONARR_API_KEY:
        return "❌ Sonarr API key not configured."

    headers = {"X-Api-Key": SONARR_API_KEY}

    # Resolve tvdbId via lookup if not provided
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

    # Resolve tmdbId via lookup if not provided
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

    # Fire configured client requests in parallel
    tasks: list = []
    task_labels: list[str] = []
    if SABNZBD_API_KEY:
        tasks.append(_api_get(f"{SABNZBD_URL}/api?mode=queue&output=json&apikey={SABNZBD_API_KEY}"))
        task_labels.append("sabnzbd")
    qbit_configured = bool(os.getenv("QBIT_URL", ""))
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
    """Check if key services are listening on expected ports (all in parallel)."""
    services = [
        ("Sonarr", HOST, 8989),
        ("Radarr", HOST, 7878),
        ("Lidarr", HOST, 8686),
        ("Prowlarr", HOST, 9696),
        ("Tautulli", HOST, 8181),
        ("Overseerr", HOST, 5055),
        ("Glances", HOST, 61208),
        ("OpenClaw", HOST, 8765),
    ]
    # Only check download clients if configured
    if SABNZBD_API_KEY:
        services.append(("SABnzbd", HOST, 8775))
    qbit_url = os.getenv("QBIT_URL", "")
    if qbit_url:
        services.append(("qBittorrent", HOST, 8080))

    async def _check_port(host: str, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    results = await asyncio.gather(
        *[_check_port(host, port) for _, host, port in services]
    )
    lines = []
    for (name, _, port), ok in zip(services, results):
        lines.append(f"{'✅' if ok else '❌'} **{name}** (:{port})" + ("" if ok else " — not responding"))
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


# ---------------------------------------------------------------------------
# Phase 8: Web Search & Browsing
# ---------------------------------------------------------------------------


async def search_web(query: str, num_results: int = 5) -> str:
    """Search the web using the best available provider.

    Search priority: Perplexity (AI-powered) → Firecrawl (search+extract) → Tavily (structured) → DuckDuckGo (free) → Bing Lite (fallback)

    Uses installed ClawHub skill scripts for Tavily and DDG:
      - skills/openclaw-tavily-search/scripts/tavily_search.py  (Tavily API)
      - skills/free-web-search/scripts/web_search.py            (DDG, no key)
    """
    num_results = min(max(num_results, 1), 10)

    # ── Perplexity path (AI-synthesized answers with citations) ────────────
    if PERPLEXITY_API_KEY:
        try:
            log.info("Using Perplexity for search: %s", query[:80])
            result = await _perplexity_search(query, num_results)
            if result:
                return result
        except Exception as e:
            log.debug("Perplexity search failed: %s", e)

    # ── Firecrawl path (search + full page extraction in one call) ─────────
    if FIRECRAWL_API_KEY:
        try:
            log.info("Using Firecrawl for search: %s", query[:80])
            result = await _firecrawl_search(query, num_results)
            if result:
                return result
        except Exception as e:
            log.debug("Firecrawl search failed: %s", e)

    # ── Tavily path (higher quality, needs API key) ────────────────────────
    if TAVILY_API_KEY and _TAVILY_SCRIPT.exists():
        # Ensure num_results is a solid integer for the CLI
        clean_num = int(float(num_results))
        cmd = [
            sys.executable,
            str(_TAVILY_SCRIPT),
            "--query", query,
            "--max-results", str(clean_num),
            "--include-answer",
            "--format", "raw",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TAVILY_API_KEY": TAVILY_API_KEY},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode != 0:
                err = stderr.decode().strip()[:300]
                # Fall through to DDG if Tavily fails at runtime
                log.warning("Tavily script failed: %s", err)
            else:
                try:
                    data = json.loads(stdout.decode())
                    return _format_tavily_results(data, int(float(num_results)))
                except json.JSONDecodeError:
                    log.error("Tavily script returned invalid JSON: %s", stdout.decode()[:200])
        except asyncio.TimeoutError:
            log.warning("Tavily script timed out")
        except Exception as e:
            log.warning("Tavily script error: %s", e)

    # ── Free DuckDuckGo fallback (no API key required) ─────────────────────
    if _DDG_SCRIPT.exists():
        # Ensure num_results is an integer for the CLI
        clean_num = int(float(num_results))
        cmd = [
            sys.executable,
            str(_DDG_SCRIPT),
            query,
            "--json",
            "--pages", str(min(clean_num, 5)),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
            if proc.returncode != 0:
                return f"❌ Web search failed: {stderr.decode().strip()[:200]}"
            data = json.loads(stdout.decode())
            if "error" in data:
                return f"❌ {data['error']}"
            return _format_ddg_results(data, int(float(num_results)))
        except asyncio.TimeoutError:
            return "❌ Web search timed out (25s)."
        except Exception as e:
            return f"❌ Web search error: {e}"

    # ── Bing lite fallback (multi-search-engine skill provides pattern) ──────
    # Parse HTML from Bing lite search (no API key, no script required)
    log.info("Falling back to Bing lite for: %s", query)
    try:
        import urllib.parse
        bing_url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)
        bing_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        session = await _get_session()
        async with session.get(
            bing_url, headers=bing_headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                html = await resp.text()
                # Extract result snippets via basic HTML parsing
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    lines = []
                    for i, result in enumerate(soup.select("li.b_algo")[:int(float(num_results))], 1):
                        title_el = result.select_one("h2 a")
                        snippet_el = result.select_one(".b_caption p")
                        if title_el:
                            title = title_el.get_text(strip=True)
                            url_link = title_el.get("href", "")
                            snippet = snippet_el.get_text(strip=True)[:250] if snippet_el else ""
                            lines.append(f"**{i}. {title}**\n{snippet}\n🔗 <{url_link}>")
                    if lines:
                        return "\n\n".join(lines) + "\n\n*via Bing (fallback)*"
                except ImportError:
                    pass
    except Exception as e:
        log.warning("Bing fallback failed: %s", e)

    # ── Nothing worked ────────────────────────────────────────────────────
    if not TAVILY_API_KEY and not PERPLEXITY_API_KEY:
        return (
            "⚠️ Web search not configured. Either:\n"
            "• Set `PERPLEXITY_API_KEY` in .env for Perplexity AI Search, or\n"
            "• Set `TAVILY_API_KEY` in .env for Tavily AI Search, or\n"
            "• Ensure `skills/free-web-search/` is installed (run: "
            "`npx clawhub@latest install free-web-search`)"
        )
    return "❌ All web search methods exhausted. Check logs for details."


async def _perplexity_search(query: str, num_results: int = 5) -> str:
    """Search via Perplexity API — returns AI-synthesized answer with citations."""
    from spending import tracker as spending_tracker

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": "Be precise and concise. Cite sources."},
            {"role": "user", "content": query},
        ],
        "max_tokens": 1024,
        "return_citations": True,
        "search_recency_filter": "month",
    }

    session = await _get_session()
    async with session.post(
        url, json=payload, headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        if resp.status != 200:
            log.debug("Perplexity returned HTTP %d", resp.status)
            return ""
        data = await resp.json()

    # Track usage
    await spending_tracker.record_perplexity(model="sonar")

    answer = data["choices"][0]["message"]["content"]
    citations = data.get("citations", [])

    lines = [f"**Perplexity AI Answer:**\n{answer}"]
    if citations:
        lines.append("\n**Sources:**")
        for i, cite in enumerate(citations[:num_results], 1):
            lines.append(f"{i}. {cite}")

    return "\n".join(lines)


async def _firecrawl_search(query: str, num_results: int = 5) -> str:
    """Search via Firecrawl API — returns search results with full page content."""
    if not FIRECRAWL_API_KEY:
        return ""

    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "limit": num_results,
        "lang": "en",
        "scrapeOptions": {"formats": ["markdown"]},
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{FIRECRAWL_API_URL}/search",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.debug("Firecrawl search returned HTTP %d", resp.status)
                return ""
            data = await resp.json()

        if not data.get("success") or not data.get("data"):
            return ""

        results = data["data"]
        lines = [f"**Firecrawl Search** ({len(results)} results):\n"]
        for i, r in enumerate(results[:num_results], 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            markdown = r.get("markdown", "")
            # Show first 500 chars of content
            snippet = markdown[:500] + "…" if len(markdown) > 500 else markdown
            lines.append(f"**{i}. [{title}]({url})**")
            if snippet:
                lines.append(f"> {snippet}\n")

        log.info("Firecrawl search: %d results for: %s", len(results), query[:60])
        # Track usage
        from spending import tracker as spending_tracker
        await spending_tracker.record_firecrawl(pages=len(results), action="search")
        return "\n".join(lines)
    except Exception as e:
        log.debug("Firecrawl search failed: %s", e)
        return ""


async def firecrawl_scrape(url: str) -> str:
    """Scrape a URL via Firecrawl and return clean markdown content."""
    if not FIRECRAWL_API_KEY:
        return "⚠️ Firecrawl API key not configured. Set `FIRECRAWL_API_KEY` in .env."

    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["markdown"],
    }

    try:
        session = await _get_session()
        async with session.post(
            f"{FIRECRAWL_API_URL}/scrape",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return f"❌ Firecrawl returned HTTP {resp.status}"
            data = await resp.json()

        if not data.get("success"):
            return f"❌ Firecrawl scrape failed: {data.get('error', 'unknown')}"

        markdown = data.get("data", {}).get("markdown", "")
        title = data.get("data", {}).get("metadata", {}).get("title", "")
        if not markdown:
            return "⚠️ Firecrawl returned no content."

        # Trim to reasonable size
        if len(markdown) > 6000:
            markdown = markdown[:6000] + "\n… (truncated)"

        header = f"**{title}**\n*Source: {url}*\n\n" if title else f"*Source: {url}*\n\n"
        log.info("Firecrawl scrape: %d chars from %s", len(markdown), url)
        # Track usage
        from spending import tracker as spending_tracker
        await spending_tracker.record_firecrawl(pages=1, action="scrape")
        return header + markdown
    except Exception as e:
        log.debug("Firecrawl scrape failed: %s", e)
        return f"❌ Firecrawl error: {e}"


def _format_tavily_results(data: dict, num_results: int) -> str:
    """Format Tavily API JSON response into Discord-friendly markdown."""
    lines = []
    if data.get("answer"):
        lines.append(f"**Answer**: {data['answer']}\n")
    results = data.get("results", [])
    if not results:
        return f"No web results found for: {data.get('query', '?')}"
    for i, r in enumerate(results[:num_results], 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = (r.get("content") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{content}\n🔗 <{url}>")
    return "\n\n".join(lines)


def _format_ddg_results(data: dict, num_results: int) -> str:
    """Format free-web-search JSON response into Discord-friendly markdown."""
    results = data.get("results", [])[:num_results]
    if not results:
        return f"No web results found for: {data.get('query', '?')}"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        text = (r.get("text") or r.get("snippet") or "")[:300].strip()
        lines.append(f"**{i}. {title}**\n{text}\n🔗 <{url}>")
    src = "DuckDuckGo (free)"
    return "\n\n".join(lines) + f"\n\n*via {src}*"


# ---------------------------------------------------------------------------
# Weather — via wttr.in (no API key; uses installed weather skill pattern)
# ---------------------------------------------------------------------------


async def get_weather(location: str = "", units: str = "uscs") -> str:
    """Get current weather and 3-day forecast for a location via wttr.in.

    Args:
        location: City name, airport code, or landmark (default: WEATHER_DEFAULT_LOCATION env var)
        units: 'uscs' (Fahrenheit/mph) or 'metric' (Celsius/kmh)
    """
    loc = (location or WEATHER_DEFAULT_LOCATION).strip()
    # URL encode the location
    import urllib.parse
    encoded = urllib.parse.quote_plus(loc)
    unit_param = "u" if units.lower().startswith("u") else "m"

    # Compact format: summary + today + tomorrow (no color codes)
    url = f"https://wttr.in/{encoded}?format=j1"
    try:
        session = await _get_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return f"❌ Weather not available for `{loc}` (HTTP {resp.status})"
            data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        return f"❌ Weather request timed out for `{loc}`"
    except Exception as e:
        return f"❌ Weather error: {e}"

    try:
        cc = data["current_condition"][0]
        is_fahrenheit = unit_param == "u"
        temp = cc.get("temp_F" if is_fahrenheit else "temp_C", "?")
        feels = cc.get("FeelsLike" + ("F" if is_fahrenheit else "C"), "?")
        weather_desc = cc["weatherDesc"][0]["value"] if cc.get("weatherDesc") else "Unknown"
        humidity = cc.get("humidity", "?")
        wind_speed = cc.get("windspeedMiles" if is_fahrenheit else "windspeedKmph", "?")
        wind_dir = cc.get("winddir16Point", "?")
        unit_sym = "°F" if is_fahrenheit else "°C"
        speed_unit = "mph" if is_fahrenheit else "km/h"

        area = data.get("nearest_area", [{}])[0]
        area_name = area.get("areaName", [{}])[0].get("value", loc)
        country = area.get("country", [{}])[0].get("value", "")

        lines = [f"🌤️ **Weather: {area_name}, {country}**"]
        lines.append(f"**Current**: {weather_desc} · {temp}{unit_sym} (feels {feels}{unit_sym})")
        lines.append(f"**Wind**: {wind_speed} {speed_unit} {wind_dir} · **Humidity**: {humidity}%")

        # 3-day forecast
        for day in data.get("weather", [])[:3]:
            date = day.get("date", "?")
            max_t = day.get("maxtempF" if is_fahrenheit else "maxtempC", "?")
            min_t = day.get("mintempF" if is_fahrenheit else "mintempC", "?")
            desc = day.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "") if day.get("hourly") else ""
            lines.append(f"📅 **{date}**: {desc} · High {max_t}{unit_sym} / Low {min_t}{unit_sym}")

        return "\n".join(lines)
    except (KeyError, IndexError, TypeError) as e:
        return f"⚠️ Could not parse weather data for `{loc}`: {e}"


async def browse_url(url: str) -> str:
    """Fetch a URL and extract clean readable text.

    Uses a 3-tier extraction chain:
      1. trafilatura — fast HTML-based extraction (no JS support).
      2. Jina AI Reader — free service at r.jina.ai; handles JS-rendered
         sites and returns clean markdown without running a browser.
      3. Playwright — headless Chromium as a last resort.
    """
    from urllib.parse import urlparse
    import ipaddress as _ipaddress
    import socket as _socket

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "❌ Only HTTP and HTTPS URLs are supported."

    # SSRF guard: block private / loopback addresses
    hostname = parsed.hostname or ""
    try:
        resolved = _socket.getaddrinfo(hostname, None, _socket.AF_UNSPEC, _socket.SOCK_STREAM)
        for _, _, _, _, addr in resolved:
            ip = _ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return "❌ Cannot browse private or loopback addresses."
    except (OSError, ValueError):
        pass  # DNS failure will be caught by the HTTP request below

    try:
        import trafilatura
    except ImportError:
        return "❌ trafilatura is not installed (run: pip install trafilatura)."

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    try:
        session = await _get_session()
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status == 403:
                return (
                    f"🚫 {url} returned HTTP 403 (bot-blocking). "
                    "This site (e.g. Zillow, Redfin, Realtor.com) actively blocks automated access. "
                    "Do NOT retry this URL. Instead, use your own knowledge about this neighborhood, "
                    "typical prices, and property tax rates to provide a detailed, helpful answer."
                )
            if resp.status == 429:
                return (
                    f"🚫 {url} returned HTTP 429 (rate-limited/bot-blocked). "
                    "This site is blocking automated requests. "
                    "Do NOT retry. Use your own knowledge to answer the user's question."
                )
            if resp.status != 200:
                return f"❌ Could not fetch URL (HTTP {resp.status})."
            # Limit response size to 5MB to avoid memory issues
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > 5 * 1024 * 1024:
                return "❌ Page too large (>5MB)."
            # Stream with hard size cap to avoid memory bloat
            chunks: list[bytes] = []
            total = 0
            _MAX_DOWNLOAD = 5 * 1024 * 1024
            async for chunk in resp.content.iter_chunked(8192):
                total += len(chunk)
                if total > _MAX_DOWNLOAD:
                    break
                chunks.append(chunk)
            html = b"".join(chunks).decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return "❌ URL fetch timed out (20s)."
    except Exception as e:
        return f"❌ Could not fetch URL: {e}"

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )

    if not text:
        # Fallback 1: try Jina AI Reader (free, handles JS sites, returns markdown)
        try:
            log.info("Trying Jina Reader fallback for: %s", url)
            text = await _jina_fetch(url)
        except Exception as e:
            log.debug("Jina Reader fallback failed: %s", e)

    if not text:
        # Fallback 2: try Playwright headless browser (last resort)
        try:
            log.info("Trying Playwright fallback for: %s", url)
            text = await _playwright_fetch(url)
        except Exception as e:
            log.debug("Playwright fallback failed: %s", e)

    if not text:
        return (
            f"⚠️ Could not extract readable content from `{url}`. "
            "The page may be JavaScript-rendered or paywalled."
        )

    # Trim to a reasonable size for Discord + LLM context
    if len(text) > 6000:
        text = text[:6000] + "\n… (truncated)"

    return f"**Source**: {url}\n\n{text}"


async def _jina_fetch(url: str) -> str:
    """Fetch clean markdown content via Jina AI Reader. Free, handles JS sites."""
    jina_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/markdown", "X-No-Cache": "true"}
    try:
        session = await _get_session()
        async with session.get(
            jina_url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                log.debug("Jina Reader returned HTTP %d for %s", resp.status, url)
                return ""
            text = await resp.text()
            return text[:6000] if text else ""
    except Exception as e:
        log.debug("Jina Reader failed for %s: %s", url, e)
        return ""


async def _playwright_fetch(url_or_html: str) -> str:
    """Fetch page content using headless browser for JS-rendered sites.

    Accepts a URL (http/https) or raw HTML string. When given already-fetched
    HTML it renders it in Chromium so JS executes, then extracts text via
    trafilatura.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            if url_or_html.startswith(("http://", "https://")):
                await page.goto(url_or_html, timeout=20000, wait_until="networkidle")
            else:
                await page.set_content(url_or_html, wait_until="networkidle")
            rendered = await page.content()
            await browser.close()

        import trafilatura
        text = trafilatura.extract(
            rendered, include_tables=True, include_comments=False,
        )
        return text or ""
    except Exception as e:
        log.debug("Playwright render failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Multi-source comparison
# ---------------------------------------------------------------------------

async def compare_sources(urls_json: str, question: str) -> str:
    """
    Browse multiple URLs in parallel and synthesize a comparison answer.

    Fetches up to 5 URLs concurrently, then asks the LLM to compare/contrast
    the content and answer the question. Great for competitive analysis,
    comparing documentation pages, or fact-checking across sources.

    Args:
        urls_json: JSON array of URLs, e.g. '["https://site1.com","https://site2.com"]'
        question:  What to compare or answer from these sources.

    Returns a synthesized comparison using all successfully fetched pages.
    """
    import json as _json

    try:
        urls = _json.loads(urls_json)
        if not isinstance(urls, list) or not urls:
            return "❌ urls_json must be a non-empty JSON array of URL strings."
    except _json.JSONDecodeError as e:
        return f"❌ Invalid urls_json: {e}"

    urls = urls[:5]  # cap at 5

    # Fetch all in parallel
    pages = await asyncio.gather(
        *[asyncio.wait_for(browse_url(u), timeout=20) for u in urls],
        return_exceptions=True,
    )

    sections: list[str] = []
    for url, result in zip(urls, pages):
        if isinstance(result, Exception):
            sections.append(f"[{url}: error — {result}]")
        elif isinstance(result, str):
            sections.append(f"=== Source: {url} ===\n{result[:2000]}")
        else:
            sections.append(f"[{url}: no content]")

    if not sections:
        return "❌ Could not fetch any of the provided URLs."

    combined = "\n\n".join(sections)[:7000]
    prompt = (
        f"You have {len(sections)} source(s) to compare. "
        f"Answer this question: **{question}**\n\n"
        "Use only the source content below. Cite which source supports each point. "
        "Note any contradictions or gaps.\n\n"
        f"{combined}"
    )

    try:
        from llm import chat as _llm_chat
        synthesis, _, _ = await asyncio.wait_for(_llm_chat(prompt), timeout=35)
        return synthesis[:1900]
    except Exception as e:
        log.warning("compare_sources LLM synthesis failed: %s", e)
        return f"📄 **Raw sources** (LLM synthesis unavailable):\n\n{combined[:1800]}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ADVANCED_SKILLS = {
    "check_arr_health": check_arr_health,
    "check_download_clients": check_download_clients,
    "check_plex_status": check_plex_status,
    "get_plex_activity": get_plex_activity,
    "search_media": search_media,
    "add_to_sonarr": add_to_sonarr,
    "add_to_radarr": add_to_radarr,
    "get_download_queue": get_download_queue,
    "get_recent_additions": get_recent_additions,
    "ping_host": ping_host,
    "check_service_ports": check_service_ports,
    "create_status_report": create_status_report,
    # Phase 8: Web skills
    "search_web": search_web,
    "browse_url": browse_url,
    "firecrawl_scrape": firecrawl_scrape,
    "compare_sources": compare_sources,
    # Phase E: Weather
    "get_weather": get_weather,
}
