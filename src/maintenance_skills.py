"""
OpenClaw Maintenance Skills — Automated 4:00 AM Maintenance Tasks

Handles:
  - OpenClaw skills update (git pull)
  - Gateway / LLM session restart (clears memory leaks)
  - Config + tasks backup to NAS via rsync/scp
"""

import asyncio
import datetime
import logging
import os
from pathlib import Path

log = logging.getLogger("openclaw.maintenance")

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
NAS_BACKUP_PATH = os.getenv("NAS_BACKUP_PATH", "/volume1/docker/openclaw/backups")
NAS_SSH_USER = os.getenv("NAS_SSH_USER", "dave")
NAS_HOST = os.getenv("NAS_HOST", "192.168.1.8")
NAS_SSH_PORT = int(os.getenv("NAS_SSH_PORT", "24"))


# ---------------------------------------------------------------------------
# Individual maintenance tasks
# ---------------------------------------------------------------------------


async def update_skills() -> str:
    """
    Update OpenClaw skills via git pull.

    Pulls the latest skill code in /app (the application directory).
    Idempotent — safe to run if already up-to-date.
    """
    from subprocess_utils import run as _run

    rc, out, err = await _run(
        ["git", "-C", "/app", "pull", "--rebase", "--autostash"],
        timeout=60,
    )
    if rc == 0:
        lines = out.strip().splitlines()
        summary = lines[-1] if lines else "Already up to date."
        log.info("Skills update: %s", summary)
        return f"✅ Skills updated: {summary}"
    log.warning("Skills git pull failed (rc=%d): %s", rc, err)
    return f"⚠️ Skills update failed: {err[:200]}"


async def restart_gateway() -> str:
    """
    Clear in-memory LLM model instances and HTTP sessions.

    Forces fresh model initialization on next request, freeing memory
    held by idle Gemini session objects.
    """
    results: list[str] = []

    try:
        import llm as _llm
        await _llm.close_sessions()
        # Reset cached model instances so they reinitialize next call
        _llm._model = None
        _llm._model_system_prompt = None
        _llm._thinking_model = None
        log.info("Maintenance: LLM sessions cleared")
        results.append("LLM sessions cleared")
    except Exception as e:
        results.append(f"LLM clear failed: {e}")

    try:
        import http_session as _hs
        await _hs.close()
        log.info("Maintenance: HTTP sessions closed")
        results.append("HTTP sessions closed")
    except Exception as e:
        results.append(f"HTTP close failed: {e}")

    return "✅ Gateway restart: " + ", ".join(results)


async def backup_config_to_nas() -> str:
    """
    Back up config/ and data/tasks.json to NAS via rsync/scp.

    Remote path: {NAS_BACKUP_PATH}/{YYYY-MM-DD}/
    Uses key-based SSH (BatchMode=yes) — will fail gracefully if no key.
    """
    from subprocess_utils import run as _run

    date_str = datetime.date.today().isoformat()
    remote_dest = f"{NAS_BACKUP_PATH}/{date_str}"
    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]

    # Create remote directory
    rc, _, err = await _run(
        ["ssh"] + ssh_opts + [f"{NAS_SSH_USER}@{NAS_HOST}", f"mkdir -p {remote_dest}"],
        timeout=20,
    )
    if rc != 0:
        return f"❌ Cannot reach NAS for backup: {err[:150]}"

    results: list[str] = []

    # Backup config/
    rc2, _, err2 = await _run(
        [
            "rsync", "-az", "--delete",
            "-e", f"ssh -p {NAS_SSH_PORT} -o BatchMode=yes -o ConnectTimeout=10",
            str(CONFIG_DIR) + "/",
            f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/config/",
        ],
        timeout=90,
    )
    results.append("config ✅" if rc2 == 0 else f"config ❌ {err2[:60]}")

    # Backup tasks.json
    tasks_file = Path("/app/data/tasks.json")
    if tasks_file.exists():
        rc3, _, err3 = await _run(
            ["scp"] + [f"-P{NAS_SSH_PORT}", "-o", "BatchMode=yes",
                       str(tasks_file), f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/tasks.json"],
            timeout=30,
        )
        results.append("tasks ✅" if rc3 == 0 else f"tasks ❌ {err3[:60]}")

    summary = ", ".join(results)
    log.info("NAS backup (%s): %s", date_str, summary)
    return f"✅ NAS backup ({date_str}): {summary}"


# ---------------------------------------------------------------------------
# Composite: run all maintenance tasks
# ---------------------------------------------------------------------------


async def run_maintenance() -> str:
    """
    Run all 4:00 AM maintenance tasks in sequence.

    Tasks:
      1. update_skills   — git pull latest skill code
      2. restart_gateway — clear LLM/HTTP sessions
      3. backup_config_to_nas — rsync config to NAS

    Registered by bot.py at startup for 4:00 AM daily execution.
    """
    log.info("4:00 AM maintenance cycle starting")
    steps = [
        ("skills-update", update_skills),
        ("gateway-restart", restart_gateway),
        ("nas-backup", backup_config_to_nas),
    ]
    lines: list[str] = []
    for label, fn in steps:
        try:
            result = await fn()
            lines.append(f"• **{label}**: {result[:100]}")
        except Exception as e:
            lines.append(f"• **{label}**: ❌ {e}")

    summary = "\n".join(lines)
    log.info("Maintenance complete:\n%s", summary)
    return f"🔧 **4:00 AM Maintenance Complete**\n{summary}"


# ---------------------------------------------------------------------------
# Skill exports
# ---------------------------------------------------------------------------

MAINTENANCE_SKILLS = {
    "update_skills": update_skills,
    "restart_gateway": restart_gateway,
    "backup_config_to_nas": backup_config_to_nas,
    "run_maintenance": run_maintenance,
}
