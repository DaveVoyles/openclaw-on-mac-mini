"""
OpenClaw Maintenance Skills — Automated 4:00 AM Maintenance Tasks

Handles:
  - OpenClaw skills update (git pull)
  - Gateway / LLM session restart (clears memory leaks)
  - Full backup to NAS: config, .env, memory, vault, audit, tasks
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
from config import cfg as _cfg
NAS_HOST = os.getenv("NAS_HOST", _cfg.nas_host)
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
    Back up config/, .env, memory/, vault/, audit/, and tasks.json to NAS.

    Remote path: {NAS_BACKUP_PATH}/{YYYY-MM-DD}/
    Uses key-based SSH (BatchMode=yes) — will fail gracefully if no key.
    """
    from subprocess_utils import run as _run

    date_str = datetime.date.today().isoformat()
    remote_dest = f"{NAS_BACKUP_PATH}/{date_str}"
    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    rsync_ssh = f"ssh -p {NAS_SSH_PORT} -o BatchMode=yes -o ConnectTimeout=10"

    # Create remote directory
    rc, _, err = await _run(
        ["ssh"] + ssh_opts + [f"{NAS_SSH_USER}@{NAS_HOST}", f"mkdir -p {remote_dest}"],
        timeout=20,
    )
    if rc != 0:
        return f"❌ Cannot reach NAS for backup: {err[:150]}"

    results: list[str] = []

    # -- 1. config/ (YAML, prompts, permissions) ------------------------------
    rc2, _, err2 = await _run(
        [
            "rsync", "-az", "--delete",
            "-e", rsync_ssh,
            str(CONFIG_DIR) + "/",
            f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/config/",
        ],
        timeout=90,
    )
    results.append("config ✅" if rc2 == 0 else f"config ❌ {err2[:60]}")

    # -- 2. tasks.json (scheduler state) --------------------------------------
    tasks_file = Path("/app/data/tasks.json")
    if tasks_file.exists():
        rc3, _, err3 = await _run(
            ["scp", f"-P{NAS_SSH_PORT}", "-o", "BatchMode=yes",
             str(tasks_file), f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/tasks.json"],
            timeout=30,
        )
        results.append("tasks ✅" if rc3 == 0 else f"tasks ❌ {err3[:60]}")

    # -- 3. .env (API keys, secrets — CRITICAL) -------------------------------
    env_file = Path("/app/.env")
    if env_file.exists():
        rc4, _, err4 = await _run(
            ["scp", f"-P{NAS_SSH_PORT}", "-o", "BatchMode=yes",
             str(env_file), f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/dot-env"],
            timeout=30,
        )
        results.append("env ✅" if rc4 == 0 else f"env ❌ {err4[:60]}")
        # Restrict permissions on the remote copy
        if rc4 == 0:
            await _run(
                ["ssh"] + ssh_opts + [f"{NAS_SSH_USER}@{NAS_HOST}",
                 f"chmod 600 {remote_dest}/dot-env"],
                timeout=10,
            )

    # -- 4. memory/ (QMD knowledge, threads, summaries, spending) -------------
    memory_dir = Path("/memory")
    if memory_dir.is_dir():
        rc5, _, err5 = await _run(
            [
                "rsync", "-az",
                "-e", rsync_ssh,
                str(memory_dir) + "/",
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/memory/",
            ],
            timeout=90,
        )
        results.append("memory ✅" if rc5 == 0 else f"memory ❌ {err5[:60]}")

    # -- 5. vault/ (Obsidian notes, research reports, bookmarks) --------------
    vault_dir = Path("/vault")
    if vault_dir.is_dir():
        rc6, _, err6 = await _run(
            [
                "rsync", "-az",
                "-e", rsync_ssh,
                str(vault_dir) + "/",
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/vault/",
            ],
            timeout=90,
        )
        results.append("vault ✅" if rc6 == 0 else f"vault ❌ {err6[:60]}")

    # -- 6. audit/ (command audit trail JSONL) --------------------------------
    audit_dir = Path("/audit")
    if audit_dir.is_dir():
        rc7, _, err7 = await _run(
            [
                "rsync", "-az",
                "-e", rsync_ssh,
                str(audit_dir) + "/",
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/audit/",
            ],
            timeout=90,
        )
        results.append("audit ✅" if rc7 == 0 else f"audit ❌ {err7[:60]}")

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
