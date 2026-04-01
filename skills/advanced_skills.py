"""
OpenClaw Advanced Skills — facade that re-exports from focused sub-modules.

Sub-modules:
  - search_skills: web search providers and cascade logic
  - media_skills:  *arr services, Plex, download clients
  - web_skills:    URL browsing, content extraction, multi-source comparison

Local skills (network/weather/reports) remain here.
"""

import asyncio
import datetime
import logging
import shlex

import aiohttp

from config import TIMEOUT_DEFAULT, TIMEOUT_SLOW
from config import cfg as _cfg
from http_session import SessionManager

log = logging.getLogger("openclaw.advanced_skills")

# ---------------------------------------------------------------------------
# Re-export everything from sub-modules (backward-compatible)
# ---------------------------------------------------------------------------

from skills.media_skills import (  # noqa: F401
    MEDIA_SKILLS,
    add_to_radarr,
    add_to_sonarr,
    check_arr_health,
    check_download_clients,
    check_plex_status,
    get_download_queue,
    get_plex_activity,
    get_recent_additions,
    search_media,
)
from skills.search_skills import (  # noqa: F401
    SEARCH_SKILLS,
    _firecrawl_search,
    _format_ddg_results,
    _format_tavily_results,
    _perplexity_search,
    firecrawl_scrape,
    search_web,
    serper_search,
)
from skills.web_skills import (  # noqa: F401
    WEB_SKILLS,
    browse_url,
    compare_sources,
)

# ---------------------------------------------------------------------------
# Shared HTTP session (used by local skills only — weather)
# ---------------------------------------------------------------------------

_sessions = SessionManager(
    timeout=TIMEOUT_SLOW,
    name="advanced_skills",
    connector_limit=50,
    connector_limit_per_host=15,
    ttl_dns_cache=600,
)
_get_session = _sessions.get
close_session = _sessions.close

# ---------------------------------------------------------------------------
# Configuration for local skills
# ---------------------------------------------------------------------------

HOST = _cfg.docker_host_ip
SABNZBD_API_KEY = _cfg.sabnzbd_api_key
QBIT_URL = _cfg.qbit_url
WEATHER_DEFAULT_LOCATION = _cfg.weather_default_location

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Weather — via wttr.in (no API key required)
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
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_DEFAULT)) as resp:
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



# ---------------------------------------------------------------------------
# Network & Connectivity
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
    if QBIT_URL:
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
# Status Report Generation
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
# Merged registry — all skills from sub-modules + local
# ---------------------------------------------------------------------------

ADVANCED_SKILLS = {
    **SEARCH_SKILLS,
    **MEDIA_SKILLS,
    **WEB_SKILLS,
    "get_weather": get_weather,
    "ping_host": ping_host,
    "check_service_ports": check_service_ports,
    "create_status_report": create_status_report,
}
