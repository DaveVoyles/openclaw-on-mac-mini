"""
OpenClaw Uptime Kuma Skills — query monitor status from Uptime Kuma v2.

Exposes monitor health, uptime stats, and incident history to the LLM
so it can answer questions like "is Sonarr up?", "what's down right now?",
or "show me uptime stats for the last 24 hours".

Skills:
  get_all_monitor_status()       — overview of every monitor (up/down/pending)
  get_monitor_detail(name)       — detailed info for one monitor
  get_monitors_down()            — list only monitors that are currently down
  get_uptime_summary()           — overall uptime percentages across all monitors
"""

import logging
import os

import aiohttp

log = logging.getLogger("openclaw.uptime_kuma")

from config import cfg as _cfg
from http_session import SessionManager

UPTIME_KUMA_URL = os.getenv(
    "UPTIME_KUMA_URL", f"http://{_cfg.docker_host_ip}:3001"
)
UPTIME_KUMA_USER = os.getenv("UPTIME_KUMA_USER", "dave")
UPTIME_KUMA_PASS = os.getenv("UPTIME_KUMA_PASS", "")

_sessions = SessionManager(timeout=15, name="uptime_kuma")
_get_session = _sessions.get
close_session = _sessions.close

# Status page slug configured in Kuma
_STATUS_SLUG = os.getenv("UPTIME_KUMA_STATUS_SLUG", "main")


# ---------------------------------------------------------------------------
# Helpers — use the public status-page JSON API (no auth needed)
# ---------------------------------------------------------------------------

async def _fetch_status_page() -> dict:
    """Fetch the public status page heartbeat data."""
    session = await _get_session()
    url = f"{UPTIME_KUMA_URL}/api/status-page/{_STATUS_SLUG}"
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Uptime Kuma returned HTTP {resp.status}")
        return await resp.json()


async def _fetch_heartbeat() -> dict:
    """Fetch heartbeat data for the status page."""
    session = await _get_session()
    url = f"{UPTIME_KUMA_URL}/api/status-page/heartbeat/{_STATUS_SLUG}"
    async with session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Uptime Kuma heartbeat returned HTTP {resp.status}")
        return await resp.json()


def _status_emoji(status: int) -> str:
    return {0: "🔴", 1: "🟢", 2: "🟡", 3: "🔵"}.get(status, "⚪")


def _status_text(status: int) -> str:
    return {0: "DOWN", 1: "UP", 2: "PENDING", 3: "MAINTENANCE"}.get(status, "UNKNOWN")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

async def get_all_monitor_status() -> str:
    """
    Get current up/down status for ALL monitors in Uptime Kuma.

    Returns a formatted list showing each monitor's name, status,
    and response time.
    """
    try:
        hb_data = await _fetch_heartbeat()
    except Exception as e:
        return f"❌ Could not reach Uptime Kuma: {e}"

    heartbeat_list = hb_data.get("heartbeatList", {})
    if not heartbeat_list:
        return "⚠️ No monitors found on the status page."

    lines = ["**📡 Uptime Kuma — All Monitors**\n"]
    up_count = 0
    down_count = 0

    for monitor_id, beats in sorted(heartbeat_list.items()):
        if not beats:
            continue
        latest = beats[-1]
        name = latest.get("name", f"Monitor {monitor_id}")
        status = latest.get("status", -1)
        ping = latest.get("ping", "N/A")
        emoji = _status_emoji(status)

        if status == 1:
            up_count += 1
            lines.append(f"{emoji} **{name}** — {ping}ms")
        else:
            down_count += 1
            msg = latest.get("msg", "")
            lines.append(f"{emoji} **{name}** — {_status_text(status)}"
                         + (f" ({msg[:80]})" if msg else ""))

    lines.insert(1, f"🟢 {up_count} up · 🔴 {down_count} down\n")
    return "\n".join(lines)[:1900]


async def get_monitor_detail(name: str) -> str:
    """
    Get detailed status info for a specific monitor by name.

    Args:
        name: The monitor name to look up (case-insensitive partial match).
    """
    try:
        hb_data = await _fetch_heartbeat()
    except Exception as e:
        return f"❌ Could not reach Uptime Kuma: {e}"

    heartbeat_list = hb_data.get("heartbeatList", {})
    name_lower = name.lower()

    # Find matching monitor
    match_id = None
    match_beats = None
    for monitor_id, beats in heartbeat_list.items():
        if not beats:
            continue
        monitor_name = beats[-1].get("name", "")
        if name_lower in monitor_name.lower():
            match_id = monitor_id
            match_beats = beats
            break

    if not match_beats:
        available = []
        for beats in heartbeat_list.values():
            if beats:
                available.append(beats[-1].get("name", "?"))
        return (f"⚠️ No monitor matching '{name}'. "
                f"Available: {', '.join(sorted(available))}")

    latest = match_beats[-1]
    monitor_name = latest.get("name", name)
    status = latest.get("status", -1)
    ping = latest.get("ping", "N/A")
    msg = latest.get("msg", "")

    # Calculate uptime from recent heartbeats
    total = len(match_beats)
    up = sum(1 for b in match_beats if b.get("status") == 1)
    uptime_pct = round(up / total * 100, 1) if total > 0 else 0

    # Recent history sparkline
    sparkline = "".join(_status_emoji(b.get("status", -1)) for b in match_beats[-20:])

    lines = [
        f"**📊 {monitor_name}**",
        f"Status: {_status_emoji(status)} {_status_text(status)}",
        f"Ping: {ping}ms",
        f"Uptime: {uptime_pct}% ({up}/{total} checks)",
        f"Recent: {sparkline}",
    ]
    if msg:
        lines.append(f"Message: {msg[:200]}")

    return "\n".join(lines)


async def get_monitors_down() -> str:
    """
    List only monitors that are currently DOWN or in an error state.

    Returns a summary of failing services, or a success message if all are up.
    """
    try:
        hb_data = await _fetch_heartbeat()
    except Exception as e:
        return f"❌ Could not reach Uptime Kuma: {e}"

    heartbeat_list = hb_data.get("heartbeatList", {})
    down = []

    for monitor_id, beats in heartbeat_list.items():
        if not beats:
            continue
        latest = beats[-1]
        if latest.get("status") != 1:
            name = latest.get("name", f"Monitor {monitor_id}")
            msg = latest.get("msg", "")
            down.append(f"🔴 **{name}** — {_status_text(latest.get('status', -1))}"
                        + (f": {msg[:100]}" if msg else ""))

    if not down:
        total = sum(1 for beats in heartbeat_list.values() if beats)
        return f"✅ All {total} monitors are UP!"

    return f"**🚨 {len(down)} monitor(s) DOWN:**\n" + "\n".join(down)


async def get_uptime_summary() -> str:
    """
    Show overall uptime percentages for all monitors.

    Calculates uptime from recent heartbeat data and ranks
    monitors from worst to best.
    """
    try:
        hb_data = await _fetch_heartbeat()
    except Exception as e:
        return f"❌ Could not reach Uptime Kuma: {e}"

    heartbeat_list = hb_data.get("heartbeatList", {})
    stats = []

    for monitor_id, beats in heartbeat_list.items():
        if not beats:
            continue
        name = beats[-1].get("name", f"Monitor {monitor_id}")
        total = len(beats)
        up = sum(1 for b in beats if b.get("status") == 1)
        pct = round(up / total * 100, 1) if total > 0 else 0
        avg_ping = round(
            sum(b.get("ping", 0) or 0 for b in beats if b.get("status") == 1)
            / max(up, 1)
        )
        stats.append((name, pct, avg_ping, total))

    if not stats:
        return "⚠️ No monitor data available."

    # Sort by uptime ascending (worst first)
    stats.sort(key=lambda x: x[1])

    overall_avg = round(sum(s[1] for s in stats) / len(stats), 1)
    emoji = "🟢" if overall_avg >= 99 else ("🟡" if overall_avg >= 95 else "🔴")

    lines = [
        f"**📈 Uptime Summary** — {emoji} {overall_avg}% overall\n",
    ]
    for name, pct, avg_ping, total in stats:
        bar_emoji = "🟢" if pct >= 99 else ("🟡" if pct >= 95 else "🔴")
        lines.append(f"{bar_emoji} **{name}**: {pct}% ({avg_ping}ms avg)")

    return "\n".join(lines)[:1900]


UPTIME_KUMA_SKILLS = {
    "get_all_monitor_status": get_all_monitor_status,
    "get_monitor_detail": get_monitor_detail,
    "get_monitors_down": get_monitors_down,
    "get_uptime_summary": get_uptime_summary,
}
