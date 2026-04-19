"""
bg_healing — Audit writer, background cleanup, and proactive self-healing loops.

Concerns: audit flushing, conversation/approval cleanup, proactive insight scanning,
quality drift alerting, system signal gathering, and self-healing action execution.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path

import discord

from alert_manager import QUALITY_DRIFT_ALERT_COOLDOWN, should_route_bounded_alert
from approvals import approval_store
from audit import _audit_buffer, audit_log
from constants import (
    AUDIT_FLUSH_INTERVAL,
    CLEANUP_INTERVAL,
    EMBED_PROMPT_LIMIT,
    EMBED_SPLIT_LIMIT,
    LOG_SNIPPET_MAX_CHARS,
    PROACTIVE_LOG_LINES,
    PROACTIVE_SCAN_INTERVAL,
)
from llm import chat as llm_chat
from memory import store as conversation_store
from metrics_collector import get_collector
from skills import get_container_logs, get_system_stats, restart_container
from skills.advanced_skills import (
    check_arr_health,
    check_download_clients,
    check_plex_status,
)
from trace_context import trace_context

log = logging.getLogger(__name__)

AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

_QUALITY_DRIFT_ALERT_ROUTE = "quality_calibration_drift"

_SAFE_RESTART_TARGETS = frozenset(
    {
        "sonarr",
        "radarr",
        "lidarr",
        "prowlarr",
        "sabnzbd",
        "qbittorrent",
        "tautulli",
        "overseerr",
    }
)
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
                        if e.get("severity", "INFO") in ("HIGH", "CRITICAL"):
                            f.flush()
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
            except Exception as e:  # broad: intentional
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
        except Exception as e:  # broad: intentional
            log.warning("Proactive scan error: %s", e)
        await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)


async def _check_quality_drift_alert(bot) -> bool:
    """Post severe calibration drift alerts with cooldown + de-dup bounds."""
    if not ALERT_CHANNEL_ID:
        return False
    try:
        from dashboard.api_handlers import _build_offline_quality_calibration_payload
    except (ImportError, AttributeError) as exc:
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
    drift_category = str(drift.get("category") or drift.get("drift_category") or "").lower().strip()
    embed = discord.Embed(
        title="🚨 Severe Quality Calibration Drift",
        description=(f"Offline calibration detected **severe** drift.\nRegressed metrics: {metrics_line}"),
        color=discord.Color.red(),
    )
    embed.add_field(name="Severity", value=f"{severity.get('level', 'unknown')} (score: {score_value})", inline=True)
    embed.add_field(name="Policy", value="Advisory only (no auto threshold mutation)", inline=True)
    if reason_lines:
        embed.add_field(name="Reasons", value="\n".join(reason_lines)[:1024], inline=False)
    # W13-5: remediation hint
    try:
        from alert_manager import get_remediation_hint

        hint = get_remediation_hint(drift_category)
        if hint:
            embed.add_field(name="Remediation", value=hint, inline=False)
    except (ImportError, AttributeError):
        pass
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
    except (ImportError, OSError, asyncio.TimeoutError) as exc:
        log.debug("NAS health check failed: %s", exc)

    # gluetun VPN container (qBittorrent + SABnzbd depend on it)
    vpn_status = ""
    try:
        from maintenance_skills import check_gluetun_vpn

        vpn_status = await asyncio.wait_for(check_gluetun_vpn(), timeout=15)
    except (ImportError, OSError, asyncio.TimeoutError) as exc:
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
    except (ImportError, OSError, AttributeError, ValueError, RuntimeError) as e:
        log.debug("Failed to record health check to history: %s", e)
    try:
        import shutil

        from health_history import record_disk as _hh_record_disk

        usage = shutil.disk_usage("/")
        _hh_record_disk("/", usage.total / 1e9, usage.used / 1e9, usage.free / 1e9, usage.used / usage.total * 100)
    except (ImportError, OSError, AttributeError, ValueError) as e:
        log.debug("Failed to record disk usage to history: %s", e)

    key_containers = ["sonarr", "radarr", "sabnzbd", "plex"]
    log_snippets: dict[str, str] = {}
    for svc in key_containers:
        try:
            logs = await asyncio.wait_for(get_container_logs(svc, lines=PROACTIVE_LOG_LINES), timeout=6)
            if logs and _error_re.search(logs):
                log_snippets[svc] = logs[:LOG_SNIPPET_MAX_CHARS]
        except (OSError, asyncio.TimeoutError, ValueError) as exc:
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
        isinstance(r, str) and not _error_re.search(r) for r in [health, dl_clients, plex] if isinstance(r, str)
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
        except Exception as e:  # broad: intentional
            heal_results.append(f"❌ `{action_type} {target}`: {e}")
            log.warning("Self-heal %s failed for %s: %s", action_type, target, e)

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
        except (discord.HTTPException, AttributeError) as e:
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
        except Exception as exc:  # broad: intentional
            log.warning("_CopilotFixView ack via edit_message failed: %s", exc)
            # Last-resort acknowledgment — sends an ephemeral so Discord doesn't mark as failed
            try:
                await interaction.response.defer_update()
            except discord.HTTPException as e:
                log.debug("Failed to defer interaction update: %s", e)
            except Exception as e:  # broad: intentional
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
                except (OSError, ValueError, RuntimeError) as exc:
                    results.append(f"❌ Failed: {exc}")
        except (ImportError, AttributeError) as exc:
            log.exception("approve_button fix execution failed")
            results.append(f"❌ Execution error: {exc}")

        # Edit the status message in-place with the final result
        summary = "\n\n".join(results) if results else "No output."
        final = f"🤖 **Copilot CLI result** (approved by **{interaction.user.display_name}**):\n{summary[:1900]}"
        try:
            await status_msg.edit(content=final)
        except discord.HTTPException as e:
            log.warning("Failed to edit status message, sending new message: %s", e)
            await interaction.channel.send(final)
        except Exception:  # broad: intentional
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
        except discord.HTTPException as exc:
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
        from llm_ratelimit import background_quota_guard

        if not background_quota_guard.check_background_allowed():
            log.warning("Background LLM quota exhausted — skipping proactive scan LLM call")
            return
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
            target
            for action_type, target in [(a, t) for a, t in _parse_heal_actions(analysis)]
            if action_type == "copilot_fix_pending"
        ]
        if copilot_prompts:
            view = _CopilotFixView(copilot_prompts)
            await msg.edit(view=view)

        audit_log(None, "proactive_scan", detail="insight posted")
        log.info("Proactive scan posted an insight (healed: %d)", len(heal_results))
    except asyncio.TimeoutError:
        log.warning("Proactive scan LLM call timed out")
    except Exception as e:  # broad: intentional
        log.warning("Proactive scan failed: %s", e)
