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
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import discord

from alert_manager import QUALITY_DRIFT_ALERT_COOLDOWN, should_route_bounded_alert
from approvals import approval_store
from audit import _audit_buffer, audit_log
from http_session import SessionManager as _SessionManager
from metrics_collector import get_collector
from trace_context import trace_context

_bg_sessions = _SessionManager(timeout=10, name="discord-background")
from constants import (
    AUDIT_FLUSH_INTERVAL,
    BRIEFING_CHECK_INTERVAL,
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    CLEANUP_INTERVAL,
    EMBED_DESC_LIMIT,
    EMBED_PROMPT_LIMIT,
    EMBED_SPLIT_LIMIT,
    EVENING_DIGEST_HOUR,
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
_BACKGROUND_RESTART_DELAY_SECONDS = 5
_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}
_BACKGROUND_FACTORIES: dict[str, Callable[[], Awaitable[None]]] = {}
_BACKGROUND_STOPPING = False
_QUALITY_DRIFT_ALERT_ROUTE = "quality_calibration_drift"

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
            except OSError as ex:
                log.warning("Audit flush failed: %s", ex)


# ---------------------------------------------------------------------------
# Background cleanup
# ---------------------------------------------------------------------------


async def background_cleanup_loop():
    """Periodically clean up expired conversations and approval requests."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        with trace_context(command="background_cleanup", user_id=0, channel_id=0):
            start = time.time()
            success = True
            error_type = None
            try:
                conversation_store.cleanup_expired()
                approval_store.cleanup_expired()
            except Exception as e:
                log.warning("Background cleanup error: %s", e)
                success = False
                error_type = type(e).__name__
            finally:
                duration = time.time() - start
                get_collector().record_command(
                    command="background_cleanup",
                    user="background",
                    workspace="background",
                    duration=duration,
                    success=success,
                    error_type=error_type,
                )


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
                    # Observability: trace context and metrics
                    with trace_context(command="morning_briefing", user_id=0, channel_id=ALERT_CHANNEL_ID):
                        start = time.time()
                        success = True
                        error_type = None
                        try:
                            asyncio.create_task(send_morning_briefing(bot))
                        except Exception as e:
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

        overseerr_section = ""
        try:
            from overseerr import get_request_stats
            overseerr_section = await asyncio.wait_for(get_request_stats(), timeout=10)
        except Exception as exc:
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
        except Exception as exc:
            log.debug("Briefing disk prediction failed: %s", exc)
        if overseerr_section:
            embed.add_field(name="🎬 Media Requests", value=overseerr_section[:200], inline=False)
        await channel.send(embed=embed)
        audit_log(None, "morning_briefing", detail=f"channel={ALERT_CHANNEL_ID}")
    except Exception as e:
        log.error("Morning briefing failed: %s", e)


# ---------------------------------------------------------------------------
# Evening digest
# ---------------------------------------------------------------------------

async def evening_digest_loop(bot):
    """Post an end-of-day digest to ALERT_CHANNEL_ID each day at ~9:00 PM."""
    last_digest_date: str = ""
    while True:
        try:
            now = datetime.datetime.now()
            if now.hour == EVENING_DIGEST_HOUR and now.minute < BRIEFING_MINUTE_WINDOW:
                today_str = now.strftime("%Y-%m-%d")
                if today_str != last_digest_date:
                    last_digest_date = today_str
                    asyncio.create_task(send_evening_digest(bot))
        except Exception as e:
            log.warning("Evening digest scheduler error: %s", e)
        await asyncio.sleep(BRIEFING_CHECK_INTERVAL)


async def send_evening_digest(bot, channel_override=None):
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
            entries = [
                json.loads(line)
                for line in audit_file.read_text().splitlines()
                if line.strip()
            ]
            cmd_count = len(entries)
            action_counts: dict[str, int] = {}
            for entry in entries:
                action = entry.get("action", "unknown")
                action_counts[action] = action_counts.get(action, 0) + 1
            top_actions = sorted(
                action_counts.items(), key=lambda x: x[1], reverse=True
            )[:5]
            actions_text = "\n".join(f"• `{a}`: {c}" for a, c in top_actions)
            embed.add_field(
                name=f"📋 Activity ({cmd_count} actions)",
                value=actions_text or "No activity",
                inline=False,
            )
    except Exception as e:
        log.debug("Digest: audit summary failed: %s", e)

    # 2. Reminders fired today
    try:
        from reminder_manager import reminder_manager

        fired_today = [
            r
            for r in reminder_manager._reminders
            if r.fired
            and datetime.date.fromtimestamp(r.fire_at) == datetime.date.today()
        ]
        if fired_today:
            reminder_text = "\n".join(
                f"• ✅ {r.message}" for r in fired_today[:5]
            )
            embed.add_field(
                name=f"⏰ Reminders ({len(fired_today)})",
                value=reminder_text,
                inline=False,
            )
    except Exception as e:
        log.debug("Digest: reminders failed: %s", e)

    # 3. System health summary
    try:
        stats = await asyncio.wait_for(get_system_stats(), timeout=10)
        embed.add_field(
            name="💻 System",
            value=stats[:300] if stats else "N/A",
            inline=False,
        )
    except Exception as e:
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
    except Exception as e:
        log.debug("Digest: downloads failed: %s", e)

    embed.set_footer(text="Evening digest • daily at 9 PM")
    await channel.send(embed=embed)
    audit_log(None, "evening_digest", detail=f"channel={ALERT_CHANNEL_ID}")


# ---------------------------------------------------------------------------
# Proactive insight scanner
# ---------------------------------------------------------------------------

async def proactive_insight_loop(bot):
    """Scan for anomalies every 2 hours and post a Discord alert if noteworthy."""
    await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)
    while True:
        try:
            with trace_context(command="proactive_scan"):
                log.info("Proactive scan starting")
                await _check_quality_drift_alert(bot)
                await _run_proactive_scan(bot)
                log.info("Proactive scan complete")
        except Exception as e:
            log.warning("Proactive scan error: %s", e)
        await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)


async def _check_quality_drift_alert(bot) -> bool:
    """Post severe calibration drift alerts with cooldown + de-dup bounds."""
    if not ALERT_CHANNEL_ID:
        return False
    try:
        from dashboard.api_handlers import _build_offline_quality_calibration_payload
    except Exception as exc:
        log.debug("Quality drift calibration import failed: %s", exc)
        return False

    calibration = _build_offline_quality_calibration_payload()
    if not isinstance(calibration, dict):
        return False
    drift = calibration.get("drift")
    if not isinstance(drift, dict):
        return False
    severity = drift.get("severity")
    if not isinstance(severity, dict):
        severity = {}
    if not bool(severity.get("severe")):
        return False

    regressed_metrics = sorted(str(item) for item in drift.get("regressed_metrics", []) if str(item).strip())
    fingerprint = json.dumps(
        {
            "status": str(drift.get("status") or ""),
            "severity": str(severity.get("level") or ""),
            "regressed_metrics": regressed_metrics,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    allowed, reason = should_route_bounded_alert(
        _QUALITY_DRIFT_ALERT_ROUTE,
        fingerprint=fingerprint,
        cooldown_seconds=QUALITY_DRIFT_ALERT_COOLDOWN,
    )
    if not allowed:
        log.debug("Quality drift alert skipped (%s)", reason)
        return False

    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if not channel:
        return False

    score_value = int(severity.get("score", 0) or 0)
    reason_lines = [f"• {item}" for item in severity.get("reasons", []) if isinstance(item, str) and item.strip()]
    metrics_line = ", ".join(regressed_metrics[:6]) if regressed_metrics else "none"
    embed = discord.Embed(
        title="🚨 Severe Quality Calibration Drift",
        description=(
            f"Offline calibration detected **severe** drift.\n"
            f"Regressed metrics: {metrics_line}"
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Severity", value=f"{severity.get('level', 'unknown')} (score: {score_value})", inline=True)
    embed.add_field(name="Policy", value="Advisory only (no auto threshold mutation)", inline=True)
    if reason_lines:
        embed.add_field(name="Reasons", value="\n".join(reason_lines)[:1024], inline=False)
    embed.set_footer(text="Quality drift monitor • bounded alert routing")
    await channel.send(embed=embed)
    audit_log(None, "quality_drift_alert", detail=f"severe drift score={score_value} metrics={metrics_line}")
    log.warning("Severe quality drift alert sent (score=%d)", score_value)
    return True


async def _gather_system_signals():
    """Collect health checks, disk space, and log snippets. Returns None if all clean."""
    health, dl_clients, plex, sys_stats = await asyncio.gather(
        check_arr_health(),
        check_download_clients(),
        check_plex_status(),
        get_system_stats(),
        return_exceptions=True,
    )

    # NAS disk space and RAID via SSH
    nas_disk = ""
    try:
        from maintenance_skills import check_nas_health
        nas_disk = await asyncio.wait_for(check_nas_health(), timeout=20)
    except Exception as exc:
        log.debug("NAS health check failed: %s", exc)

    # gluetun VPN container (qBittorrent + SABnzbd depend on it)
    vpn_status = ""
    try:
        from maintenance_skills import check_gluetun_vpn
        vpn_status = await asyncio.wait_for(check_gluetun_vpn(), timeout=15)
    except Exception as exc:
        log.debug("gluetun VPN check failed: %s", exc)

    # Record service-level health for trend tracking
    try:
        from health_history import record as _hh_record
        for svc_name, result in [("arr", health), ("download-clients", dl_clients), ("plex", plex)]:
            if isinstance(result, Exception):
                _hh_record(svc_name, "down", str(result))
            elif isinstance(result, str) and _error_re.search(result):
                _hh_record(svc_name, "degraded", result[:200])
            elif isinstance(result, str):
                _hh_record(svc_name, "ok", result[:200])
    except Exception as e:
        log.debug("Failed to record health check to history: %s", e)

    # Record disk usage for trend prediction
    try:
        import shutil

        from health_history import record_disk as _hh_record_disk
        usage = shutil.disk_usage("/")
        _hh_record_disk("/", usage.total / 1e9, usage.used / 1e9, usage.free / 1e9, usage.used / usage.total * 100)
    except Exception as e:
        log.debug("Failed to record disk usage to history: %s", e)

    key_containers = ["sonarr", "radarr", "sabnzbd", "plex"]
    log_snippets: dict[str, str] = {}
    for svc in key_containers:
        try:
            logs = await asyncio.wait_for(get_container_logs(svc, lines=PROACTIVE_LOG_LINES), timeout=6)
            if logs and _error_re.search(logs):
                log_snippets[svc] = logs[:LOG_SNIPPET_MAX_CHARS]
        except Exception as exc:
            log.debug("Container log fetch for %s failed: %s", svc, exc)

    # Check for disk space alerts (>90% used)
    disk_alert = False
    if isinstance(sys_stats, str) and "Disk" in sys_stats:
        for line in sys_stats.split("\n"):
            if "Disk" in line:
                try:
                    pct = int(line.split("(")[1].split("%")[0])
                    if pct >= 90:
                        disk_alert = True
                except (IndexError, ValueError):
                    pass
    if nas_disk and "🔴" in nas_disk:
        disk_alert = True

    all_clean = all(
        isinstance(r, str) and not _error_re.search(r)
        for r in [health, dl_clients, plex]
        if isinstance(r, str)
    )
    if all_clean and not log_snippets and not disk_alert:
        return None

    summary_parts = [
        f"Health checks:\n  *arr: {health}\n  Download clients: {dl_clients}\n  VPN (gluetun): {vpn_status or 'not checked'}\n  Plex: {plex}"
    ]
    if isinstance(sys_stats, str):
        summary_parts.append(f"System stats:\n{sys_stats}")
    if nas_disk:
        summary_parts.append(f"NAS health:\n{nas_disk}")
    if log_snippets:
        summary_parts.append("Log anomalies:")
        for svc, snippet in log_snippets.items():
            summary_parts.append(f"  {svc}:\n{snippet}")

    return "\n\n".join(summary_parts), log_snippets


def _parse_heal_actions(analysis: str) -> list[tuple[str, str]]:
    """Extract SELF_HEAL directives from LLM analysis text."""
    actions: list[tuple[str, str]] = []
    for line in analysis.split("\n"):
        if line.strip().startswith("SELF_HEAL:"):
            parts = line.strip().split()
            if len(parts) >= 3 and parts[1] == "restart_container":
                target = parts[2].lower().strip()
                if target in _SAFE_RESTART_TARGETS:
                    actions.append(("restart_container", target))
            elif len(parts) >= 2 and parts[1] == "fix_qbit_download_path":
                actions.append(("fix_qbit_download_path", ""))
            elif len(parts) >= 2 and parts[1] == "fix_arr_remote_path":
                actions.append(("fix_arr_remote_path", ""))
            elif len(parts) >= 2 and parts[1] == "auto_cleanup_disk":
                actions.append(("auto_cleanup_disk", ""))
            elif parts[1] == "copilot_fix":
                copilot_prompt = " ".join(parts[2:]) if len(parts) > 2 else ""
                if copilot_prompt:
                    actions.append(("copilot_fix_pending", copilot_prompt))
    return actions


async def _execute_self_healing(analysis: str) -> tuple[str, list[str]]:
    """Parse SELF_HEAL directives and execute safe fixes.

    Returns (cleaned_analysis, heal_results).
    """
    heal_actions = _parse_heal_actions(analysis)
    display_analysis = analysis
    for line in analysis.split("\n"):
        if line.strip().startswith("SELF_HEAL:"):
            display_analysis = display_analysis.replace(line, "").strip()

    heal_results: list[str] = []
    for action_type, target in heal_actions:
        try:
            if action_type == "restart_container":
                result = await asyncio.wait_for(restart_container(target), timeout=60)
                heal_results.append(f"🔧 `{target}`: {result}")
                audit_log(None, "self_heal", detail=f"restart {target}: {result}")
                log.info("Self-heal: restarted %s → %s", target, result[:80])
            elif action_type == "fix_qbit_download_path":
                from maintenance_skills import fix_qbit_download_path
                result = await asyncio.wait_for(fix_qbit_download_path(), timeout=60)
                heal_results.append(f"🔧 qBittorrent path fix: {result}")
                audit_log(None, "self_heal", detail=f"fix_qbit_download_path: {result[:200]}")
                log.info("Self-heal: fix_qbit_download_path → %s", result[:80])
            elif action_type == "fix_arr_remote_path":
                from maintenance_skills import fix_arr_remote_path
                result = await asyncio.wait_for(fix_arr_remote_path(), timeout=120)
                heal_results.append(f"🔧 *arr path fix: {result}")
                audit_log(None, "self_heal", detail=f"fix_arr_remote_path: {result[:200]}")
                log.info("Self-heal: fix_arr_remote_path → %s", result[:80])
            elif action_type == "auto_cleanup_disk":
                from maintenance_skills import auto_cleanup_disk
                result = await asyncio.wait_for(auto_cleanup_disk(), timeout=120)
                heal_results.append(f"🧹 Disk cleanup: {result}")
                audit_log(None, "self_heal", detail=f"auto_cleanup_disk: {result[:200]}")
                log.info("Self-heal: auto_cleanup_disk → %s", result[:80])
            elif action_type == "copilot_fix_pending":
                # Don't execute — return a pending approval message
                heal_results.append(
                    f"🤖 **Copilot CLI fix suggested** (requires approval):\n"
                    f"> {target}\n"
                    f"Click **Approve Fix** below to run (uses API tokens)."
                )
                audit_log(None, "self_heal", detail=f"copilot_fix proposed: {target[:200]}")
        except Exception as exc:
            heal_results.append(f"❌ `{action_type} {target}`: {exc}")
            log.warning("Self-heal %s failed for %s: %s", action_type, target, exc)

    return display_analysis, heal_results


class _CopilotFixView(discord.ui.View):
    """Discord button view for approving/denying Copilot CLI fix suggestions."""

    def __init__(self, prompts: list[str]):
        super().__init__(timeout=3600)
        self.prompts = prompts

    async def on_timeout(self):
        """Disable buttons when view times out (after 1 hour)."""
        try:
            for child in self.children:
                child.disabled = True
            # Try to update the message so buttons appear disabled in Discord
            if hasattr(self, "message") and self.message:
                await self.message.edit(content="⏱️ Copilot fix approval expired.", view=self)
        except discord.HTTPException as e:
            log.debug("Failed to edit expired approval message: %s", e)
        except Exception as e:
            log.warning("Unexpected error disabling expired approval buttons: %s", e)
        log.debug("_CopilotFixView timed out after 1 hour")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Called before any button handler. Ensures the interaction is always acknowledged."""
        if self.is_finished():
            # View timed out or was stopped — button is stale. Always ack to avoid
            # "This interaction failed" in Discord, then bail out.
            await interaction.response.send_message(
                "⏱️ This approval has already expired or was already resolved.", ephemeral=True
            )
            return False
        return True

    async def _ack(self, interaction: discord.Interaction) -> None:
        """Immediately acknowledge the interaction (must happen within 3 seconds).

        Uses defer_update() so Discord doesn't show a loading spinner on the message.
        Disables the buttons optimistically so the user sees instant feedback.
        """
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.InteractionResponded:
            pass  # Already acknowledged (shouldn't happen, but safe)
        except Exception as exc:
            log.warning("_CopilotFixView ack via edit_message failed: %s", exc)
            # Last-resort acknowledgment — sends an ephemeral so Discord doesn't mark as failed
            try:
                await interaction.response.defer_update()
            except discord.HTTPException as e:
                log.debug("Failed to defer interaction update: %s", e)
            except Exception as e:
                log.warning("Unexpected error in last-resort interaction acknowledgment: %s", e)

    @discord.ui.button(label="✅ Approve Fix", style=discord.ButtonStyle.green, custom_id="copilot_approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Acknowledge immediately — must happen within 3 s or Discord shows "This interaction failed"
        await self._ack(interaction)

        # Send a visible "running" status right away so the user knows the fix is in progress.
        # We'll edit this message in-place with the result when done.
        n = len(self.prompts)
        status_msg = await interaction.channel.send(
            f"⏳ **Running Copilot fix{'es' if n > 1 else ''}** ({n} task{'s' if n > 1 else ''}) "
            f"— approved by **{interaction.user.display_name}**. This may take up to 3 minutes…"
        )

        results: list[str] = []
        try:
            from maintenance_skills import copilot_fix
            for cp in self.prompts:
                try:
                    result = await asyncio.wait_for(copilot_fix(cp), timeout=180)
                    results.append(result[:1800])
                    audit_log(interaction.user, "copilot_fix_approved", detail=cp[:200])
                except asyncio.TimeoutError:
                    results.append("⏱️ Timed out after 3 minutes.")
                except Exception as exc:
                    results.append(f"❌ Failed: {exc}")
        except Exception as exc:
            log.exception("approve_button fix execution failed")
            results.append(f"❌ Execution error: {exc}")

        # Edit the status message in-place with the final result
        summary = "\n\n".join(results) if results else "No output."
        final = (
            f"🤖 **Copilot CLI result** (approved by **{interaction.user.display_name}**):\n"
            f"{summary[:1900]}"
        )
        try:
            await status_msg.edit(content=final)
        except discord.HTTPException as e:
            log.warning("Failed to edit status message, sending new message: %s", e)
            await interaction.channel.send(final)
        except Exception:
            log.exception("Unexpected error sending approval result")
            await interaction.channel.send(final)

        self.stop()

    @discord.ui.button(label="❌ Skip", style=discord.ButtonStyle.grey, custom_id="copilot_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Acknowledge immediately
        await self._ack(interaction)

        try:
            await interaction.channel.send("❌ Copilot CLI fix skipped.")
            audit_log(interaction.user, "copilot_fix_rejected", detail=self.prompts[0][:200])
        except Exception as exc:
            log.warning("deny_button cleanup failed: %s", exc)
        self.stop()


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
        "SELF_HEAL: fix_qbit_download_path\n"
        "SELF_HEAL: fix_arr_remote_path\n"
        "SELF_HEAL: auto_cleanup_disk\n"
        "SELF_HEAL: copilot_fix <description of what to fix>\n"
        "Use fix_qbit_download_path when qBittorrent's download path has drifted from /downloads "
        "(e.g. health check shows 'rom-downloads' or bad remote path mapping).\n"
        "Use fix_arr_remote_path when Sonarr/Radarr report remote path mapping errors — this will "
        "fix qBittorrent's config and restart the affected *arr services.\n"
        "Use copilot_fix for novel/complex issues that don't have a dedicated fix skill — "
        "this spawns the Copilot CLI and requires user approval before running.\n"
        "Use auto_cleanup_disk when disk space is critically low (>90% used) — "
        "this prunes Docker images, rotates logs, and cleans temp files.\n"
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
        msg = await channel.send(embed=embed)

        # If a copilot_fix is pending approval, use Discord buttons (not reactions)
        copilot_prompts = [
            target for action_type, target in
            [(a, t) for a, t in _parse_heal_actions(analysis)]
            if action_type == "copilot_fix_pending"
        ]
        if copilot_prompts:
            view = _CopilotFixView(copilot_prompts)
            await msg.edit(view=view)

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
    except discord.HTTPException as e:
        log.warning("Failed to post error alert: %s", e)
# ---------------------------------------------------------------------------
# Container health auto-alerts
# ---------------------------------------------------------------------------

# Tracks last-seen status per container to avoid repeat alerts
_container_prev_state: dict[str, str] = {}
_container_unhealthy_count: dict[str, int] = {}  # consecutive unhealthy checks
_AUTO_RESTART_THRESHOLD = 2  # restart after N consecutive unhealthy checks

CONTAINER_HEALTH_INTERVAL = 300  # 5 minutes


async def container_health_loop(bot):
    """Check Docker container health every 5 minutes and alert on unhealthy/exited."""
    await asyncio.sleep(60)  # initial delay to let containers settle on startup
    while True:
        try:
            await _check_container_health(bot)
            await _check_monstervision_cookies(bot)
        except Exception as e:
            log.warning("Container health check error: %s", e)
        await asyncio.sleep(CONTAINER_HEALTH_INTERVAL)


_cookie_alert_sent = False  # only alert once per expiry cycle


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
        except Exception as e:
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
                except Exception as exc:
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
        except Exception as e:
            log.debug("Resource monitor loop error: %s", e)
        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def reminder_loop(bot):
    """Check for due reminders every 15 seconds and DM users."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            from reminder_manager import reminder_manager

            due = reminder_manager.get_due()
            for r in due:
                try:
                    user = await bot.fetch_user(r.user_id)
                    embed = discord.Embed(
                        title="⏰ Reminder",
                        description=r.message,
                        color=discord.Color.gold(),
                    )
                    recur = f" (🔁 {r.recurring})" if r.recurring else ""
                    embed.set_footer(text=f"ID: {r.id}{recur}")
                    await user.send(embed=embed)
                except Exception as e:
                    log.debug("Failed to send reminder %s: %s", r.id, e)
                reminder_manager.mark_fired(r.id)
        except Exception as e:
            log.debug("Reminder loop error: %s", e)
        await asyncio.sleep(15)


def _build_background_task_factories(bot) -> dict[str, Callable[[], Awaitable[None]]]:
    factories: dict[str, Callable[[], Awaitable[None]]] = {
        "background_cleanup": background_cleanup_loop,
        "audit_writer": audit_writer_loop,
        "reminder": lambda: reminder_loop(bot),
    }
    if ALERT_CHANNEL_ID:
        factories.update({
            "morning_briefing": lambda: morning_briefing_loop(bot),
            "evening_digest": lambda: evening_digest_loop(bot),
            "proactive_insight": lambda: proactive_insight_loop(bot),
            "error_monitor": lambda: error_monitor_loop(bot),
            "container_health": lambda: container_health_loop(bot),
            "resource_monitor": lambda: resource_monitor_loop(bot),
        })
    return factories


def _handle_background_task_done(task_name: str, task: asyncio.Task) -> None:
    if _BACKGROUND_STOPPING:
        return
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return

    if error:
        log.warning(
            "Background task %s crashed: %s; restarting in %ss",
            task_name,
            error,
            _BACKGROUND_RESTART_DELAY_SECONDS,
        )
    else:
        log.warning(
            "Background task %s exited unexpectedly; restarting in %ss",
            task_name,
            _BACKGROUND_RESTART_DELAY_SECONDS,
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_later(_BACKGROUND_RESTART_DELAY_SECONDS, _restart_background_task, task_name)


def _launch_background_task(task_name: str, task_factory: Callable[[], Awaitable[None]]) -> None:
    _BACKGROUND_FACTORIES[task_name] = task_factory
    task = asyncio.create_task(
        _run_supervised_background_task(task_name, task_factory),
        name=f"openclaw.background.{task_name}",
    )
    _BACKGROUND_TASKS[task_name] = task
    task.add_done_callback(lambda done, name=task_name: _handle_background_task_done(name, done))


async def _run_supervised_background_task(
    task_name: str,
    task_factory: Callable[[], Awaitable[None]],
) -> None:
    start = time.monotonic()
    success = True
    error_type: str | None = None
    cancelled = False

    try:
        with trace_context(command=f"background:{task_name}", user_id=0, channel_id=ALERT_CHANNEL_ID, component="background"):
            await task_factory()
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        success = False
        error_type = type(exc).__name__
        raise
    finally:
        if not (cancelled and _BACKGROUND_STOPPING):
            get_collector().record_command(
                command=f"background:{task_name}",
                user="system",
                workspace="background",
                duration=max(0.0, time.monotonic() - start),
                success=success,
                error_type=error_type,
            )


def _restart_background_task(task_name: str) -> None:
    if _BACKGROUND_STOPPING:
        return
    current = _BACKGROUND_TASKS.get(task_name)
    if current and not current.done():
        return
    task_factory = _BACKGROUND_FACTORIES.get(task_name)
    if task_factory is None:
        return
    _launch_background_task(task_name, task_factory)


def start_background_tasks(bot) -> int:
    """Create all background asyncio tasks. Called from OpenClawBot.on_ready."""
    global _BACKGROUND_STOPPING

    if any(not task.done() for task in _BACKGROUND_TASKS.values()):
        log.info("Background tasks already running (%d active)", len(_BACKGROUND_TASKS))
        return len(_BACKGROUND_TASKS)

    _BACKGROUND_STOPPING = False
    _BACKGROUND_TASKS.clear()
    _BACKGROUND_FACTORIES.clear()

    for task_name, task_factory in _build_background_task_factories(bot).items():
        _launch_background_task(task_name, task_factory)

    if ALERT_CHANNEL_ID:
        log.info("Proactive tasks started (alert channel: %d)", ALERT_CHANNEL_ID)
    else:
        log.info("ALERT_CHANNEL_ID not set — proactive push notifications disabled")
    log.info("Background task supervisor started (%d loops)", len(_BACKGROUND_TASKS))
    return len(_BACKGROUND_TASKS)


async def stop_background_tasks() -> None:
    """Cancel and await all supervised background tasks."""
    global _BACKGROUND_STOPPING

    if not _BACKGROUND_TASKS:
        return

    _BACKGROUND_STOPPING = True
    tasks = list(_BACKGROUND_TASKS.items())
    for _, task in tasks:
        task.cancel()

    results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
    for (task_name, _), result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            log.debug("Background task %s stopped with error: %s", task_name, result)

    _BACKGROUND_TASKS.clear()
    _BACKGROUND_FACTORIES.clear()
    log.info("Background task supervisor stopped")
