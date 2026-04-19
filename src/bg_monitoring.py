"""
bg_monitoring — Error monitoring, container health, and resource threshold loops.

Concerns: error pattern detection, Docker container health auto-alerts,
MonsterVision cookie expiry, and per-container CPU/memory threshold monitoring.
"""

import asyncio
import logging
import os
import re

import discord

from audit import audit_log
from http_session import SessionManager as _SessionManager
from skills import restart_container

log = logging.getLogger(__name__)

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

# ---------------------------------------------------------------------------
# W10-4 — User-load awareness
# ---------------------------------------------------------------------------

_ACTIVE_CONVERSATION_COUNT: int = 0


def set_active_conversation_count(n: int) -> None:
    """Set the number of currently active user conversations (call from ask_handler.py)."""
    global _ACTIVE_CONVERSATION_COUNT
    _ACTIVE_CONVERSATION_COUNT = max(0, int(n))


def get_active_conversation_count() -> int:
    """Return the current number of active user conversations."""
    return _ACTIVE_CONVERSATION_COUNT

_bg_sessions = _SessionManager(timeout=10, name="discord-background")

_SAFE_RESTART_TARGETS = frozenset({
    "sonarr", "radarr", "lidarr", "prowlarr",
    "sabnzbd", "qbittorrent", "tautulli", "overseerr",
})
_error_re = re.compile(r"error|warn|exception|critical|failed", re.IGNORECASE)

# Tracks last-seen status per container to avoid repeat alerts
_container_prev_state: dict[str, str] = {}
_container_unhealthy_count: dict[str, int] = {}  # consecutive unhealthy checks
_AUTO_RESTART_THRESHOLD = 2  # restart after N consecutive unhealthy checks

CONTAINER_HEALTH_INTERVAL = 300  # 5 minutes

_cookie_alert_sent = False  # only alert once per expiry cycle


# ---------------------------------------------------------------------------
# Error monitor
# ---------------------------------------------------------------------------

async def error_monitor_loop(bot):
    """Fast error pattern check — runs every 5 minutes (optional scan)."""
    await asyncio.sleep(300)
    while True:
        try:
            # W10-4: skip optional scan when users are actively conversing
            active = get_active_conversation_count()
            if active >= 3:
                log.info("Skipping optional scan: %d active conversations", active)
                await asyncio.sleep(300)
                continue

            from error_tracker import check_error_patterns

            patterns = check_error_patterns(window_minutes=30)
            if patterns:
                critical = [p for p in patterns if p["severity"] == "critical"]
                if critical or len(patterns) >= 2:
                    await _post_error_alert(bot, patterns)

                    # E3+E4+E5: Auto-diagnosis → fix → learn pipeline
                    try:
                        from llm_ratelimit import background_quota_guard

                        if not background_quota_guard.check_background_allowed():
                            log.warning("Background LLM quota exhausted — skipping auto-diagnosis")
                        else:
                            from error_tracker import (
                                diagnose_error_pattern,
                                execute_fix,
                                get_recent_outcomes,
                                record_incident,
                            )
                            recent = get_recent_outcomes(hours=1)
                            recent_errors = [e for e in recent if not e.get("success")]

                            diagnosis = await diagnose_error_pattern(patterns, recent_errors)
                            log.info(
                                "Auto-diagnosis: %s (confidence: %.0f%%)",
                                diagnosis.get("cause", "?"),
                                diagnosis.get("confidence", 0) * 100,
                            )

                            fix_result = await execute_fix(diagnosis)
                            log.info(
                                "Auto-fix result: %s (success: %s)",
                                fix_result.get("action_taken", "none"),
                                fix_result.get("success"),
                            )

                            await record_incident(patterns, diagnosis, fix_result)

                            if fix_result.get("success"):
                                embed = discord.Embed(
                                    title="🔧 Auto-Fix Applied",
                                    color=discord.Color.green(),
                                )
                                embed.add_field(
                                    name="Diagnosis",
                                    value=diagnosis.get("explanation", "")[:200],
                                    inline=False,
                                )
                                embed.add_field(
                                    name="Action",
                                    value=fix_result.get("action_taken", ""),
                                    inline=True,
                                )
                                embed.add_field(
                                    name="Result",
                                    value=fix_result.get("detail", "")[:200],
                                    inline=True,
                                )
                                embed.set_footer(text="Self-Healing System • auto-diagnosed and fixed")
                                channel = bot.get_channel(ALERT_CHANNEL_ID)
                                if channel:
                                    await channel.send(embed=embed)
                    except Exception as e:  # broad: intentional — auto-diagnosis pipeline spans LLM + Discord + monitoring
                        log.warning("Auto-diagnosis/fix pipeline failed: %s", e)
                else:
                    log.info(
                        "Error monitor: %d warning patterns (below critical threshold)",
                        len(patterns),
                    )
        except Exception as e:  # broad: intentional — error monitor loop must not crash
            log.debug("Error monitor check failed: %s", e)

        await asyncio.sleep(300)


async def _post_error_alert(bot, patterns: list[dict]):
    """Post an error pattern alert to the alert channel."""
    if not ALERT_CHANNEL_ID:
        return
    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="⚠️ Error Pattern Detected",
        color=discord.Color.red() if any(p["severity"] == "critical" for p in patterns) else discord.Color.orange(),
    )
    for p in patterns[:5]:
        icon = "🔴" if p["severity"] == "critical" else "🟡"
        embed.add_field(
            name=f"{icon} {p['type'].replace('_', ' ').title()}",
            value=p["detail"],
            inline=False,
        )
    embed.set_footer(text="Error Monitor • checks every 5 min")

    try:
        await channel.send(embed=embed)
        audit_log(None, "error_monitor", detail=f"{len(patterns)} patterns: {', '.join(p['type'] for p in patterns)}")
    except discord.HTTPException as e:
        log.warning("Failed to post error alert: %s", e)


# ---------------------------------------------------------------------------
# Container health auto-alerts
# ---------------------------------------------------------------------------

async def container_health_loop(bot):
    """Check Docker container health every 5 minutes and alert on unhealthy/exited."""
    await asyncio.sleep(60)  # initial delay to let containers settle on startup
    while True:
        try:
            await _check_container_health(bot)
            await _check_monstervision_cookies(bot)
        except Exception as e:  # broad: intentional — container health loop must not crash
            log.warning("Container health check error: %s", e)
        await asyncio.sleep(CONTAINER_HEALTH_INTERVAL)


async def _check_monstervision_cookies(bot):
    """Check MonsterVision API + logs for cookie expiry warnings and alert."""
    global _cookie_alert_sent
    if not ALERT_CHANNEL_ID:
        return

    import aiohttp

    from config import cfg

    # Trust the API's cookie_status first; skip log scraping when cookies are OK
    try:
        session = await _bg_sessions.get()
        async with session.get(
            f"http://{cfg.docker_host_ip}:{cfg.monstervision_port}/api/status",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("cookie_status", {}).get("label") == "ok":
                    _cookie_alert_sent = False  # reset when cookies are fresh
                    return
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.debug("MonsterVision API cookie check failed: %s", exc)

    from subprocess_utils import run as _run

    rc, out, _ = await _run(
        ["docker", "logs", "monstervision", "--tail", "20"],
        timeout=10,
    )
    if rc != 0:
        return

    has_warning = "cookies have expired" in out or "cookies.txt is" in out and "old" in out

    if has_warning and not _cookie_alert_sent:
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if channel:
            import discord
            embed = discord.Embed(
                title="🍪 MonsterVision Cookie Expired",
                description=(
                    "Patreon cookies have expired. New videos **cannot be downloaded** until refreshed.\n\n"
                    "**To fix:**\n"
                    "1. Log into [patreon.com](https://patreon.com) in Chrome\n"
                    "2. Export cookies with a cookie exporter extension\n"
                    "3. Copy to `~/Patreon/cookies/cookies.txt`"
                ),
                color=discord.Color.orange(),
            )
            await channel.send(embed=embed)
            _cookie_alert_sent = True
            log.info("Cookie expiry alert sent to Discord")
    elif not has_warning:
        _cookie_alert_sent = False  # reset when cookies are fresh


async def _check_container_health(bot):
    """Run ``docker ps -a`` and alert on unhealthy or exited containers."""
    if not ALERT_CHANNEL_ID:
        return

    from subprocess_utils import run as _run

    rc, out, err = await _run(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
        timeout=15,
    )
    if rc != 0:
        log.debug("docker ps failed: %s", err)
        return

    global _container_prev_state
    alerts: list[str] = []
    auto_restart_results: list[str] = []

    for line in out.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, status = parts[0].strip(), parts[1].strip()

        # Determine if this container is in a bad state
        status_lower = status.lower()
        is_bad = "unhealthy" in status_lower or status_lower.startswith("exited")

        # Record health check for trend tracking
        try:
            from health_history import record as _hh_record
            if is_bad:
                _hh_record(name, "down" if status_lower.startswith("exited") else "degraded", status)
            else:
                _hh_record(name, "ok", status)
        except (ImportError, AttributeError, TypeError, OSError) as e:
            log.debug("Failed to record container health to history: %s", e)

        prev = _container_prev_state.get(name)
        if is_bad:
            # Derive a short label for the state
            if "unhealthy" in status_lower:
                state_label = "unhealthy"
            else:
                state_label = "Exited"

            # Track consecutive unhealthy count
            _container_unhealthy_count[name] = _container_unhealthy_count.get(name, 0) + 1
            count = _container_unhealthy_count[name]

            # Only alert on state *change* (or first time seeing a bad state)
            if prev != state_label:
                alerts.append(f"🚨 **Container Alert**: `{name}` is **{state_label}**")
                _container_prev_state[name] = state_label

            # Auto-restart after N consecutive unhealthy checks (safe targets only)
            if count >= _AUTO_RESTART_THRESHOLD and name in _SAFE_RESTART_TARGETS:
                try:
                    result = await asyncio.wait_for(restart_container(name), timeout=60)
                    auto_restart_results.append(f"🔧 Auto-restarted `{name}` (unhealthy ×{count}): {result}")
                    audit_log(None, "self_heal", detail=f"auto_restart {name} after {count} unhealthy checks: {result}")
                    log.info("Auto-restart: %s after %d unhealthy checks → %s", name, count, result[:80])
                    _container_unhealthy_count[name] = 0
                except Exception as exc:  # broad: intentional — restart_container can fail in many ways
                    auto_restart_results.append(f"❌ Auto-restart `{name}` failed: {exc}")
                    log.warning("Auto-restart %s failed: %s", name, exc)
        else:
            # Container is healthy/running — clear any previous bad state
            if prev is not None:
                _container_prev_state.pop(name, None)
            _container_unhealthy_count.pop(name, None)

    if not alerts and not auto_restart_results:
        return

    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if not channel:
        return

    desc_parts = alerts + auto_restart_results
    embed = discord.Embed(
        title="🚨 Container Health Alert",
        description="\n".join(desc_parts)[:4000],
        color=discord.Color.red() if alerts else discord.Color.green(),
    )
    embed.set_footer(text="Container Health Monitor • auto-restarts safe targets after 2 failures")
    try:
        await channel.send(embed=embed)
        audit_log(None, "container_health", detail=f"{len(alerts)} alerts")
        log.info("Container health alert: %d containers in bad state", len(alerts))
    except discord.HTTPException as e:
        log.error("Failed to send container health alert: %s", e)


# ---------------------------------------------------------------------------
# Resource-threshold monitor (every 60 s)
# ---------------------------------------------------------------------------

async def resource_monitor_loop(bot):
    """Check per-container CPU/memory thresholds and post alerts."""
    await bot.wait_until_ready()
    from resource_monitor import resource_monitor

    while not bot.is_closed():
        try:
            violations = await resource_monitor.check_all()
            if violations:
                channel = bot.get_channel(ALERT_CHANNEL_ID)
                if channel:
                    for threshold, stats in violations:
                        embed = discord.Embed(
                            title=f"⚠️ Resource Alert: {threshold.container}",
                            color=discord.Color.red(),
                        )
                        embed.add_field(
                            name="CPU",
                            value=f"{stats['cpu']:.1f}% (threshold: {threshold.cpu_percent}%)",
                            inline=True,
                        )
                        embed.add_field(
                            name="Memory",
                            value=f"{stats['memory']:.1f}% (threshold: {threshold.memory_percent}%)",
                            inline=True,
                        )
                        embed.set_footer(text=f"Cooldown: {threshold.cooldown_seconds}s before next alert")
                        await channel.send(embed=embed)
                    audit_log(None, "resource_alert", detail=f"{len(violations)} violation(s)")
        except Exception as e:  # broad: intentional — resource monitor loop must not crash
            log.debug("Resource monitor loop error: %s", e)
        await asyncio.sleep(60)
