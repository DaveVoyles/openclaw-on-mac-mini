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

_http_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    """Return the module-level shared aiohttp session, creating it if needed."""
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = aiohttp.TCPConnector(limit=50, limit_per_host=15, ttl_dns_cache=600)
        _http_session = aiohttp.ClientSession(connector=connector)
    return _http_session

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

# Tavily web search
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_API_URL = "https://api.tavily.com/search"

# Paths to installed ClawHub skill scripts (relative to this file's directory)
_SKILLS_DIR = Path(__file__).parent
_TAVILY_SCRIPT = _SKILLS_DIR / "openclaw-tavily-search" / "scripts" / "tavily_search.py"
_DDG_SCRIPT = _SKILLS_DIR / "free-web-search" / "scripts" / "web_search.py"

COMMAND_TIMEOUT = 15

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
    tasks: list = [_api_get(f"{QBIT_URL}/api/v2/app/version")]
    if SABNZBD_API_KEY:
        tasks.insert(0, _api_get(f"{SABNZBD_URL}/api?mode=version&output=json&apikey={SABNZBD_API_KEY}"))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    lines = []

    if SABNZBD_API_KEY:
        sab_data, qbit_data = gathered[0], gathered[1]
        if isinstance(sab_data, dict) and "version" in sab_data:
            lines.append(f"✅ **SABnzbd**: v{sab_data['version']}")
        else:
            lines.append(f"❌ **SABnzbd**: {sab_data}")
    else:
        qbit_data = gathered[0]
        lines.append("⚠️ **SABnzbd**: API key not configured")

    if isinstance(qbit_data, str) and qbit_data.startswith("v"):
        lines.append(f"✅ **qBittorrent**: {qbit_data.strip()}")
    else:
        lines.append(f"⚠️ **qBittorrent**: {qbit_data}")

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
    """Get combined download queue from SABnzbd and qBittorrent (parallel requests)."""
    lines = []

    # Fire both requests in parallel
    tasks: list = [_api_get(f"{QBIT_URL}/api/v2/torrents/info?filter=active")]
    if SABNZBD_API_KEY:
        tasks.insert(0, _api_get(f"{SABNZBD_URL}/api?mode=queue&output=json&apikey={SABNZBD_API_KEY}"))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    sab_data = gathered[0] if SABNZBD_API_KEY else None
    qbit_data = gathered[1] if SABNZBD_API_KEY else gathered[0]

    # SABnzbd queue
    if SABNZBD_API_KEY:
        if isinstance(sab_data, dict):
            queue = sab_data.get("queue", {})
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
    data = qbit_data
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
        ("SABnzbd", HOST, 8775),
        ("qBittorrent", HOST, 8080),
        ("Tautulli", HOST, 8181),
        ("Overseerr", HOST, 5055),
        ("Glances", HOST, 61208),
        ("OpenClaw", HOST, 8765),
    ]

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
    """Search the web via Tavily (if API key set) or DuckDuckGo (free fallback).

    Uses installed ClawHub skill scripts:
      - skills/openclaw-tavily-search/scripts/tavily_search.py  (Tavily API)
      - skills/free-web-search/scripts/web_search.py            (DDG, no key)
    """
    num_results = min(max(num_results, 1), 10)

    # ── Tavily path (higher quality, needs API key) ────────────────────────
    if TAVILY_API_KEY and _TAVILY_SCRIPT.exists():
        cmd = [
            sys.executable,
            str(_TAVILY_SCRIPT),
            "--query", query,
            "--max-results", str(num_results),
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
                data = json.loads(stdout.decode())
                return _format_tavily_results(data, num_results)
        except asyncio.TimeoutError:
            log.warning("Tavily script timed out")
        except Exception as e:
            log.warning("Tavily script error: %s", e)

    # ── Free DuckDuckGo fallback (no API key required) ─────────────────────
    if _DDG_SCRIPT.exists():
        cmd = [
            sys.executable,
            str(_DDG_SCRIPT),
            query,
            "--json",
            "--pages", str(min(num_results, 5)),
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
            return _format_ddg_results(data, num_results)
        except asyncio.TimeoutError:
            return "❌ Web search timed out (25s)."
        except Exception as e:
            return f"❌ Web search error: {e}"

    # ── Neither script available ───────────────────────────────────────────
    if not TAVILY_API_KEY:
        return (
            "⚠️ Web search not configured. Either:\n"
            "• Set `TAVILY_API_KEY` in .env for Tavily AI Search, or\n"
            "• Ensure `skills/free-web-search/` is installed (run: "
            "`npx clawhub@latest install free-web-search`)"
        )
    return "❌ Web search scripts not found. Re-install ClawHub skills."


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


async def browse_url(url: str) -> str:
    """Fetch a URL and extract clean readable text from the page."""
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
        return (
            f"⚠️ Could not extract readable content from `{url}`. "
            "The page may be JavaScript-rendered or paywalled."
        )

    # Trim to a reasonable size for Discord + LLM context
    if len(text) > 6000:
        text = text[:6000] + "\n… (truncated)"

    return f"**Source**: {url}\n\n{text}"


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
    # Phase 8: Web skills
    "search_web": search_web,
    "browse_url": browse_url,
}
