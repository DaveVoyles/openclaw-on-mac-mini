"""
bg_briefing — Morning briefing and evening digest background loops.

Concerns: composing and posting daily briefings and end-of-day digests to Discord.
"""

import asyncio
import datetime
import json
import logging
import os
import time
import zoneinfo
from pathlib import Path

import discord

from audit import audit_log
from constants import (
    BRIEFING_CHECK_INTERVAL,
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    EMBED_DESC_LIMIT,
    EVENING_DIGEST_HOUR,
)
from llm import chat as llm_chat
from metrics_collector import get_collector
from skills import get_system_stats
from skills.advanced_skills import (
    check_arr_health,
    get_download_queue,
    get_weather,
)
from trace_context import trace_context

log = logging.getLogger(__name__)

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
_OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", os.getenv("BOT_OWNER_ID", "0")))


def _owner_local_now() -> datetime.datetime:
    """Return the current datetime in the bot owner's configured timezone (default UTC)."""
    from notification_prefs import get_user_timezone

    tz_str = get_user_timezone(_OWNER_USER_ID) if _OWNER_USER_ID else "UTC"
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except (zoneinfo.ZoneInfoNotFoundError, KeyError):
        tz = zoneinfo.ZoneInfo("UTC")
    return datetime.datetime.now(tz)


# ---------------------------------------------------------------------------
# Morning briefing
# ---------------------------------------------------------------------------


async def morning_briefing_loop(bot) -> None:
    """Post a morning briefing to ALERT_CHANNEL_ID each day at ~8:00 AM."""
    last_briefing_date: str = ""
    while True:
        try:
            now = _owner_local_now()
            if now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != last_briefing_date:
                    last_briefing_date = today_str
                    # Observability: trace context and metrics
                    with trace_context(command="morning_briefing", user_id=0, channel_id=ALERT_CHANNEL_ID):
                        start = time.time()
                        success = True
                        error_type = None
                        try:
                            asyncio.create_task(send_morning_briefing(bot))
                        except RuntimeError as e:
                            log.warning("Morning briefing task error: %s", e)
                            success = False
                            error_type = type(e).__name__
                        finally:
                            duration = time.time() - start
                            get_collector().record_command(
                                command="morning_briefing",
                                user="background",
                                workspace="background",
                                duration=duration,
                                success=success,
                                error_type=error_type,
                            )
        except Exception as e:  # broad: intentional — outer loop guard for scheduler health
            log.warning("Morning briefing scheduler error: %s", e)
        await asyncio.sleep(BRIEFING_CHECK_INTERVAL)


async def send_morning_briefing(bot, channel_override=None) -> None:
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
        except (ImportError, asyncio.TimeoutError, OSError, ValueError) as exc:
            log.debug("Calendar fetch failed for briefing: %s", exc)
            calendar = "Calendar not available."

        goals_section = ""
        try:
            from goal_tracker import format_goals_for_briefing

            goals_section = format_goals_for_briefing()
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
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
                    error_stats_section += " | Recent errors: " + "; ".join(
                        e["error"][:50] for e in stats["recent_errors"][:3]
                    )
        except (ImportError, KeyError, AttributeError, TypeError, ValueError) as exc:
            log.debug("Error stats unavailable for briefing: %s", exc)

        overseerr_section = ""
        try:
            from overseerr import get_request_stats

            overseerr_section = await asyncio.wait_for(get_request_stats(), timeout=10)
        except (ImportError, asyncio.TimeoutError, OSError, ConnectionError, ValueError) as exc:
            log.debug("Briefing: overseerr stats failed: %s", exc)

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
        if overseerr_section:
            prompt += f"**Media Requests**: {overseerr_section}\n"
        prompt += "Format with clear sections, use emojis, be friendly but brief."

        response_text, _, _ = await llm_chat(prompt)

        embed = discord.Embed(
            title=f"🌅 Morning Briefing — {today}",
            description=response_text[:EMBED_DESC_LIMIT],
            color=discord.Color.from_rgb(255, 165, 0),
        )
        embed.set_footer(text="🤖 OpenClaw Autonomous Briefing")
        try:
            from health_history import predict_full as _hh_predict

            prediction = _hh_predict("/")
            if prediction.get("days_until_full") and prediction["days_until_full"] < 30:
                embed.add_field(
                    name="💾 Disk Space Warning",
                    value=f"Root: {prediction['percent_used']}% used — estimated full in **{prediction['days_until_full']} days**",
                    inline=False,
                )
        except (ImportError, KeyError, AttributeError, TypeError, ValueError, OSError) as exc:
            log.debug("Briefing disk prediction failed: %s", exc)
        if overseerr_section:
            embed.add_field(name="🎬 Media Requests", value=overseerr_section[:200], inline=False)
        await channel.send(embed=embed)
        audit_log(None, "morning_briefing", detail=f"channel={ALERT_CHANNEL_ID}")
    except Exception as e:  # broad: intentional — outer guard for entire briefing pipeline
        log.error("Morning briefing failed: %s", e)


# ---------------------------------------------------------------------------
# Evening digest
# ---------------------------------------------------------------------------


async def evening_digest_loop(bot) -> None:
    """Post an end-of-day digest to ALERT_CHANNEL_ID each day at ~9:00 PM."""
    last_digest_date: str = ""
    while True:
        try:
            now = _owner_local_now()
            if now.hour == EVENING_DIGEST_HOUR and now.minute < BRIEFING_MINUTE_WINDOW:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != last_digest_date:
                    last_digest_date = today_str
                    asyncio.create_task(send_evening_digest(bot))
        except Exception as e:  # broad: intentional — outer loop guard for scheduler health
            log.warning("Evening digest scheduler error: %s", e)
        await asyncio.sleep(BRIEFING_CHECK_INTERVAL)


async def send_evening_digest(bot, channel_override=None) -> None:
    """Compose and post the daily evening digest.

    If channel_override is provided (e.g. a discord.TextChannel or Interaction
    channel), post there instead of ALERT_CHANNEL_ID.
    """
    channel = channel_override
    if channel is None:
        if not ALERT_CHANNEL_ID:
            return
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if not channel:
            log.warning("Evening digest: channel %d not found", ALERT_CHANNEL_ID)
            return

    log.info("Generating evening digest for channel %d", ALERT_CHANNEL_ID)

    embed = discord.Embed(
        title="🌙 End-of-Day Digest",
        color=discord.Color.dark_purple(),
        timestamp=datetime.datetime.now(),
    )

    # 1. Commands used today (from audit log)
    try:
        today_str = datetime.date.today().isoformat()
        audit_file = Path(f"/app/audit/{today_str}.jsonl")
        if audit_file.exists():
            entries = [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]
            cmd_count = len(entries)
            action_counts: dict[str, int] = {}
            for entry in entries:
                action = entry.get("action", "unknown")
                action_counts[action] = action_counts.get(action, 0) + 1
            top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            actions_text = "\n".join(f"• `{a}`: {c}" for a, c in top_actions)
            embed.add_field(
                name=f"📋 Activity ({cmd_count} actions)",
                value=actions_text or "No activity",
                inline=False,
            )
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        log.debug("Digest: audit summary failed: %s", e)

    # 2. Reminders fired today
    try:
        from reminder_manager import reminder_manager

        fired_today = [
            r
            for r in reminder_manager._reminders
            if r.fired and datetime.date.fromtimestamp(r.fire_at) == datetime.date.today()
        ]
        if fired_today:
            reminder_text = "\n".join(f"• ✅ {r.message}" for r in fired_today[:5])
            embed.add_field(
                name=f"⏰ Reminders ({len(fired_today)})",
                value=reminder_text,
                inline=False,
            )
    except (ImportError, AttributeError, TypeError) as e:
        log.debug("Digest: reminders failed: %s", e)

    # 3. System health summary
    try:
        stats = await asyncio.wait_for(get_system_stats(), timeout=10)
        embed.add_field(
            name="💻 System",
            value=stats[:300] if stats else "N/A",
            inline=False,
        )
    except Exception as e:  # broad: intentional — get_system_stats wraps external command output
        log.debug("Digest: system stats failed: %s", e)

    # 4. Download activity
    try:
        queue = await asyncio.wait_for(get_download_queue(), timeout=10)
        if queue and "no active" not in queue.lower():
            embed.add_field(
                name="📥 Downloads",
                value=queue[:300],
                inline=False,
            )
    except Exception as e:  # broad: intentional — get_download_queue can raise any error
        log.debug("Digest: downloads failed: %s", e)

    embed.set_footer(text="Evening digest • daily at 9 PM")
    await channel.send(embed=embed)
    audit_log(None, "evening_digest", detail=f"channel={ALERT_CHANNEL_ID}")
