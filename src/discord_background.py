"""
OpenClaw background tasks — extracted from bot.py.

Provides long-running asyncio loops for maintenance, monitoring, briefings,
and audit flushing. All functions accept the bot instance so they can post
Discord messages without importing bot.py at module level.
"""

import asyncio
import datetime
import json
import logging
import os
import re
from pathlib import Path

import discord

from approvals import approval_store
from audit import _audit_buffer, audit_log
from constants import (
    AUDIT_FLUSH_INTERVAL,
    BRIEFING_CHECK_INTERVAL,
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    CLEANUP_INTERVAL,
    EMBED_DESC_LIMIT,
    EMBED_FIELD_LIMIT,
    EMBED_PROMPT_LIMIT,
    EMBED_SPLIT_LIMIT,
    LOG_SNIPPET_MAX_CHARS,
    PROACTIVE_LOG_LINES,
    PROACTIVE_SCAN_INTERVAL,
)
from llm import chat as llm_chat
from memory import store as conversation_store
from skills import get_container_logs, get_system_stats, restart_container
from skills.advanced_skills import (
    check_arr_health,
    check_download_clients,
    check_plex_status,
    get_download_queue,
    get_weather,
)

log = logging.getLogger("openclaw")

AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

# ---------------------------------------------------------------------------
# Self-healing constants
# ---------------------------------------------------------------------------

_SAFE_RESTART_TARGETS = frozenset({
    "sonarr", "radarr", "lidarr", "prowlarr",
    "sabnzbd", "qbittorrent", "tautulli", "overseerr",
})
_error_re = re.compile(r"error|warn|exception|critical|failed", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Audit writer
# ---------------------------------------------------------------------------

async def audit_writer_loop():
    """Flush buffered audit entries to disk every 30 seconds."""
    while True:
        await asyncio.sleep(AUDIT_FLUSH_INTERVAL)
        if not _audit_buffer:
            continue
        entries = []
        while _audit_buffer:
            try:
                entries.append(_audit_buffer.popleft())
            except IndexError:
                break
        if entries:
            today = datetime.date.today().isoformat()
            audit_file = AUDIT_DIR / f"{today}.jsonl"
            try:
                with open(audit_file, "a") as f:
                    for e in entries:
                        f.write(json.dumps(e) + "\n")
            except Exception as ex:
                log.warning("Audit flush failed: %s", ex)


# ---------------------------------------------------------------------------
# Background cleanup
# ---------------------------------------------------------------------------

async def background_cleanup_loop():
    """Periodically clean up expired conversations and approval requests."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            conversation_store.cleanup_expired()
            approval_store.cleanup_expired()
        except Exception as e:
            log.warning("Background cleanup error: %s", e)


# ---------------------------------------------------------------------------
# Morning briefing
# ---------------------------------------------------------------------------

async def morning_briefing_loop(bot):
    """Post a morning briefing to ALERT_CHANNEL_ID each day at ~8:00 AM."""
    last_briefing_date: str = ""
    while True:
        try:
            now = datetime.datetime.now()
            if now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != last_briefing_date:
                    last_briefing_date = today_str
                    asyncio.create_task(send_morning_briefing(bot))
        except Exception as e:
            log.warning("Morning briefing scheduler error: %s", e)
        await asyncio.sleep(BRIEFING_CHECK_INTERVAL)


async def send_morning_briefing(bot, channel_override=None):
    """Compose and post the daily morning briefing.

    If channel_override is provided (e.g. a discord.TextChannel or Interaction channel),
    post there instead of ALERT_CHANNEL_ID. Used by the /briefing slash command.
    """
    channel = channel_override
    if channel is None:
        if not ALERT_CHANNEL_ID:
            return
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if not channel:
            log.warning("Morning briefing: channel %d not found", ALERT_CHANNEL_ID)
            return

    log.info("Generating morning briefing for channel %d", ALERT_CHANNEL_ID)
    try:
        health, queue, weather, sysstat = await asyncio.gather(
            check_arr_health(),
            get_download_queue(),
            get_weather(),
            get_system_stats(),
            return_exceptions=True,
        )

        try:
            from calendar_skills import get_upcoming_events
            calendar = await asyncio.wait_for(get_upcoming_events(days=1), timeout=8)
        except Exception as exc:
            log.debug("Calendar fetch failed for briefing: %s", exc)
            calendar = "Calendar not available."

        goals_section = ""
        try:
            from goal_tracker import format_goals_for_briefing
            goals_section = format_goals_for_briefing()
        except Exception as exc:
            log.debug("Goal tracker unavailable for briefing: %s", exc)

        error_stats_section = ""
        try:
            from error_tracker import get_error_stats
            stats = get_error_stats(hours=24)
            if stats["total"] > 0:
                error_stats_section = (
                    f"{stats['total']} queries, "
                    f"{stats['successes']} successful ({int(stats['success_rate'] * 100)}%), "
                    f"{stats['failures']} failures, avg latency {stats['avg_latency_ms']}ms"
                )
                if stats["failures"] > 0:
                    error_stats_section += (
                        " | Recent errors: " + "; ".join(
                            e["error"][:50] for e in stats["recent_errors"][:3]
                        )
                    )
        except Exception as exc:
            log.debug("Error stats unavailable for briefing: %s", exc)

        today = datetime.date.today().strftime("%A, %B %d, %Y")
        prompt = (
            f"Good morning! Generate a concise morning briefing for {today}. "
            "Keep it under 600 words. Include:\n"
            f"**Weather**: {weather}\n"
            f"**System health**: {health}\n"
            f"**Downloads**: {queue}\n"
            f"**Today's calendar**: {calendar}\n"
            f"**System**: {sysstat}\n"
        )
        if goals_section:
            prompt += f"**Active Goals**: {goals_section}\n"
        if error_stats_section:
            prompt += f"**Yesterday's /ask Stats**: {error_stats_section}\n"
        prompt += "Format with clear sections, use emojis, be friendly but brief."

        response_text, _, _ = await llm_chat(prompt)

        embed = discord.Embed(
            title=f"🌅 Morning Briefing — {today}",
            description=response_text[:EMBED_DESC_LIMIT],
            color=discord.Color.from_rgb(255, 165, 0),
        )
        embed.set_footer(text="🤖 OpenClaw Autonomous Briefing")
        await channel.send(embed=embed)
        audit_log(None, "morning_briefing", detail=f"channel={ALERT_CHANNEL_ID}")
    except Exception as e:
        log.error("Morning briefing failed: %s", e)


# ---------------------------------------------------------------------------
# Proactive insight scanner
# ---------------------------------------------------------------------------

async def proactive_insight_loop(bot):
    """Scan for anomalies every 2 hours and post a Discord alert if noteworthy."""
    await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)
    while True:
        try:
            await _run_proactive_scan(bot)
        except Exception as e:
            log.warning("Proactive scan error: %s", e)
        await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)


async def _gather_system_signals():
    """Collect health checks and log snippets. Returns None if all clean."""
    health, dl_clients, plex = await asyncio.gather(
        check_arr_health(),
        check_download_clients(),
        check_plex_status(),
        return_exceptions=True,
    )

    key_containers = ["sonarr", "radarr", "sabnzbd", "plex"]
    log_snippets: dict[str, str] = {}
    for svc in key_containers:
        try:
            logs = await asyncio.wait_for(get_container_logs(svc, lines=PROACTIVE_LOG_LINES), timeout=6)
            if logs and _error_re.search(logs):
                log_snippets[svc] = logs[:LOG_SNIPPET_MAX_CHARS]
        except Exception as exc:
            log.debug("Container log fetch for %s failed: %s", svc, exc)

    all_clean = all(
        isinstance(r, str) and not _error_re.search(r)
        for r in [health, dl_clients, plex]
        if isinstance(r, str)
    )
    if all_clean and not log_snippets:
        return None

    summary_parts = [
        f"Health checks:\n  *arr: {health}\n  Download clients: {dl_clients}\n  Plex: {plex}"
    ]
    if log_snippets:
        summary_parts.append("Log anomalies:")
        for svc, snippet in log_snippets.items():
            summary_parts.append(f"  {svc}:\n{snippet}")

    return "\n\n".join(summary_parts), log_snippets


async def _execute_self_healing(analysis: str) -> tuple[str, list[str]]:
    """Parse SELF_HEAL directives and execute safe restarts.

    Returns (cleaned_analysis, heal_results).
    """
    heal_actions: list[str] = []
    display_analysis = analysis
    for line in analysis.split("\n"):
        if line.strip().startswith("SELF_HEAL:"):
            parts = line.strip().split()
            if len(parts) >= 3 and parts[1] == "restart_container":
                target = parts[2].lower().strip()
                if target in _SAFE_RESTART_TARGETS:
                    heal_actions.append(target)
            display_analysis = display_analysis.replace(line, "").strip()

    heal_results: list[str] = []
    for target in heal_actions:
        try:
            result = await asyncio.wait_for(restart_container(target), timeout=60)
            heal_results.append(f"🔧 `{target}`: {result}")
            audit_log(None, "self_heal", detail=f"restart {target}: {result}")
            log.info("Self-heal: restarted %s → %s", target, result[:80])
        except Exception as exc:
            heal_results.append(f"❌ `{target}`: {exc}")
            log.warning("Self-heal restart failed for %s: %s", target, exc)

    return display_analysis, heal_results


async def _run_proactive_scan(bot):
    """Gather system signals + log snippets, ask Gemini for assessment, post if actionable."""
    if not ALERT_CHANNEL_ID:
        return

    result = await _gather_system_signals()
    if result is None:
        log.debug("Proactive scan: all clear")
        return
    summary, _ = result

    prompt = (
        "You are OpenClaw's autonomous monitoring system running a background scan.\n"
        "Based on the signals below, determine if there is anything the operator should be "
        "aware of — errors, service failures, degraded performance, or unusual activity.\n"
        "ONLY respond if there is something genuinely actionable. "
        "If everything is within normal operation, respond with exactly: NO_ALERT\n\n"
        "If you find an issue, also include a SELF_HEAL section at the end with the format:\n"
        "SELF_HEAL: restart_container <container_name>\n"
        "Only suggest restart_container for non-critical services (sonarr, radarr, lidarr, "
        "prowlarr, sabnzbd, tautulli, overseerr). Do NOT suggest restarting plex, postgres, "
        "or openclaw itself. If no safe fix exists, omit the SELF_HEAL line.\n\n"
        f"{summary[:EMBED_PROMPT_LIMIT]}"
    )

    try:
        analysis, _, _ = await asyncio.wait_for(llm_chat(prompt), timeout=35)
        if not analysis or "NO_ALERT" in analysis.upper():
            log.debug("Proactive scan: LLM found nothing notable")
            return

        display_analysis, heal_results = await _execute_self_healing(analysis)

        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if not channel:
            return

        embed = discord.Embed(
            title="🔭 Proactive Insight",
            description=display_analysis[:EMBED_SPLIT_LIMIT],
            color=discord.Color.gold(),
        )
        if heal_results:
            embed.add_field(
                name="🔧 Auto-Repair Actions",
                value="\n".join(heal_results)[:1000],
                inline=False,
            )

        embed.set_footer(text="Autonomous monitoring scan • every 2h")
        await channel.send(embed=embed)
        audit_log(None, "proactive_scan", detail="insight posted")
        log.info("Proactive scan posted an insight (healed: %d)", len(heal_results))
    except asyncio.TimeoutError:
        log.warning("Proactive scan LLM call timed out")
    except Exception as e:
        log.warning("Proactive scan failed: %s", e)


# ---------------------------------------------------------------------------
# Error monitor
# ---------------------------------------------------------------------------

async def error_monitor_loop(bot):
    """Fast error pattern check — runs every 5 minutes."""
    await asyncio.sleep(300)
    while True:
        try:
            from error_tracker import check_error_patterns

            patterns = check_error_patterns(window_minutes=30)
            if patterns:
                critical = [p for p in patterns if p["severity"] == "critical"]
                if critical or len(patterns) >= 2:
                    await _post_error_alert(bot, patterns)

                    # E3+E4+E5: Auto-diagnosis → fix → learn pipeline
                    try:
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
                    except Exception as e:
                        log.warning("Auto-diagnosis/fix pipeline failed: %s", e)
                else:
                    log.info(
                        "Error monitor: %d warning patterns (below critical threshold)",
                        len(patterns),
                    )
        except Exception as e:
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
    except Exception as e:
        log.warning("Failed to post error alert: %s", e)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def start_background_tasks(bot):
    """Create all background asyncio tasks. Called from OpenClawBot.on_ready."""
    asyncio.create_task(background_cleanup_loop())
    asyncio.create_task(audit_writer_loop())
    if ALERT_CHANNEL_ID:
        asyncio.create_task(morning_briefing_loop(bot))
        asyncio.create_task(proactive_insight_loop(bot))
        asyncio.create_task(error_monitor_loop(bot))
        log.info("Proactive tasks started (alert channel: %d)", ALERT_CHANNEL_ID)
    else:
        log.info("ALERT_CHANNEL_ID not set — proactive push notifications disabled")
