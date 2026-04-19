"""
OpenClaw Maintenance Skills — Automated 4:00 AM Maintenance Tasks

Handles:
  - OpenClaw skills update (git pull)
  - Gateway / LLM session restart (clears memory leaks)
  - Full backup to NAS: config, .env, memory, vault, audit, tasks
"""

import datetime
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
NAS_BACKUP_PATH = os.getenv("NAS_BACKUP_PATH", "/volume1/docker/openclaw/backups")
from config import cfg as _cfg

NAS_SSH_USER = _cfg.nas_ssh_user
NAS_HOST = _cfg.nas_host
NAS_SSH_PORT = _cfg.nas_ssh_port


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
        _llm._reset_models()
        log.info("Maintenance: LLM sessions cleared")
        results.append("LLM sessions cleared")
    except Exception as e:  # broad: intentional
        results.append(f"LLM clear failed: {e}")

    try:
        import http_session as _hs

        await _hs.close()
        log.info("Maintenance: HTTP sessions closed")
        results.append("HTTP sessions closed")
    except Exception as e:  # broad: intentional
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
            "rsync",
            "-az",
            "--delete",
            "-e",
            rsync_ssh,
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
            [
                "scp",
                "-O",
                f"-P{NAS_SSH_PORT}",
                "-o",
                "BatchMode=yes",
                str(tasks_file),
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/tasks.json",
            ],
            timeout=30,
        )
        results.append("tasks ✅" if rc3 == 0 else f"tasks ❌ {err3[:60]}")

    # -- 3. .env (API keys, secrets — CRITICAL) -------------------------------
    env_file = Path("/app/.env")
    if env_file.exists():
        rc4, _, err4 = await _run(
            [
                "scp",
                "-O",
                f"-P{NAS_SSH_PORT}",
                "-o",
                "BatchMode=yes",
                str(env_file),
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/dot-env",
            ],
            timeout=30,
        )
        results.append("env ✅" if rc4 == 0 else f"env ❌ {err4[:60]}")
        # Restrict permissions on the remote copy
        if rc4 == 0:
            await _run(
                ["ssh"] + ssh_opts + [f"{NAS_SSH_USER}@{NAS_HOST}", f"chmod 600 {remote_dest}/dot-env"],
                timeout=10,
            )

    # -- 4. memory/ (QMD knowledge, threads, summaries, spending) -------------
    memory_dir = Path("/memory")
    if memory_dir.is_dir():
        rc5, _, err5 = await _run(
            [
                "rsync",
                "-az",
                "-e",
                rsync_ssh,
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
                "rsync",
                "-az",
                "-e",
                rsync_ssh,
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
                "rsync",
                "-az",
                "-e",
                rsync_ssh,
                str(audit_dir) + "/",
                f"{NAS_SSH_USER}@{NAS_HOST}:{remote_dest}/audit/",
            ],
            timeout=90,
        )
        results.append("audit ✅" if rc7 == 0 else f"audit ❌ {err7[:60]}")

    summary = ", ".join(results)
    log.info("NAS backup (%s): %s", date_str, summary)
    return f"✅ NAS backup ({date_str}): {summary}"


async def backup_vault_to_nas() -> str:
    """Backup the Obsidian vault to NAS via rsync over SSH."""
    from subprocess_utils import run as _run

    vault_dir = Path(os.getenv("VAULT_DIR", "/vault"))
    remote_path = "/volume1/backups/vault/"
    rsync_ssh = f"ssh -p {NAS_SSH_PORT} -o BatchMode=yes -o ConnectTimeout=10"

    if not vault_dir.is_dir():
        return f"⚠️ Vault dir {vault_dir} not found — skipping backup"

    # Ensure remote directory exists
    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    rc_mkdir, _, err_mkdir = await _run(
        ["ssh"] + ssh_opts + [f"{NAS_SSH_USER}@{NAS_HOST}", f"mkdir -p {remote_path}"],
        timeout=20,
    )
    if rc_mkdir != 0:
        return f"❌ Cannot reach NAS for vault backup: {err_mkdir[:150]}"

    rc, _, err = await _run(
        [
            "rsync",
            "-avz",
            "--delete",
            "-e",
            rsync_ssh,
            f"{vault_dir}/",
            f"{NAS_SSH_USER}@{NAS_HOST}:{remote_path}",
        ],
        timeout=120,
    )

    if rc == 0:
        return f"✅ Vault backed up to NAS: {remote_path}"
    else:
        return f"❌ Vault backup failed (exit {rc}): {err[:200]}"


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
      4. backup_vault_to_nas — rsync vault to NAS
      5. memory_decay    — flag stale unused memories (Phase 14B)

    Registered by bot.py at startup for 4:00 AM daily execution.
    """
    log.info("4:00 AM maintenance cycle starting")
    steps = [
        ("skills-update", update_skills),
        ("gateway-restart", restart_gateway),
        ("nas-backup", backup_config_to_nas),
        ("vault-backup", backup_vault_to_nas),
        ("memory-decay", run_memory_decay),
        ("dream-cycle", _run_dream_cycle),
    ]
    lines: list[str] = []
    for label, fn in steps:
        try:
            result = await fn()
            lines.append(f"• **{label}**: {result[:100]}")
        except Exception as e:  # broad: intentional
            lines.append(f"• **{label}**: ❌ {e}")

    summary = "\n".join(lines)
    log.info("Maintenance complete:\n%s", summary)
    return f"🔧 **4:00 AM Maintenance Complete**\n{summary}"


# ---------------------------------------------------------------------------
# Memory decay & consolidation  (Phase 14B)
# ---------------------------------------------------------------------------


async def run_memory_decay() -> str:
    """Mark old, infrequently-accessed memories as decayed.

    Memories that haven't been accessed in 30 days AND have access_count < 2
    get flagged as decayed. They still appear in search results but with a
    10% similarity penalty (handled by vector_store.search).
    """
    try:
        import vector_store

        total_decayed = 0
        for collection in [
            vector_store.MEMORIES_COLLECTION,
            vector_store.CONVERSATIONS_COLLECTION,
            vector_store.RESEARCH_COLLECTION,
        ]:
            candidates = await vector_store.get_decayed_documents(collection, max_age_days=30, min_access_count=2)
            if candidates:
                ids = [c["id"] for c in candidates]
                count = await vector_store.mark_decayed(collection, ids)
                total_decayed += count
                log.info("Decayed %d documents in %s", count, collection)

        if total_decayed > 0:
            return f"🧹 Memory decay: marked {total_decayed} old memories as decayed"
        return "🧹 Memory decay: no candidates found"
    except Exception as e:  # broad: intentional
        log.warning("Memory decay failed: %s", e)
        return f"⚠️ Memory decay failed: {e}"


async def _run_dream_cycle() -> str:
    """Run a dream cycle as part of nightly maintenance."""
    try:
        from dream_cycle import DreamCycle

        cycle = DreamCycle()
        report = await cycle.run()
        # Truncate for log-friendly summary
        summary = report[:200].replace("\n", " ")
        log.info("Dream cycle complete: %s", summary)
        return f"🌙 Dream cycle complete ({len(report)} chars)"
    except Exception as e:  # broad: intentional
        log.warning("Dream cycle failed: %s", e)
        return f"⚠️ Dream cycle failed: {e}"


async def run_memory_consolidation() -> str:
    """Weekly: summarize the week's session summaries into a digest.

    Called manually or by a weekly cron job (Sunday 4 AM).
    Distills multiple session summaries into a single weekly insight memory.
    """
    try:
        # Fetch recent conversation summaries from the last 7 days
        import time

        import vector_store
        from llm import chat

        week_ago = time.time() - (7 * 86400)
        results = await vector_store.search(
            vector_store.CONVERSATIONS_COLLECTION,
            "session summary weekly digest",
            top_k=20,
            where={"type": "summary"},
            threshold=0.3,
            track_access=False,
        )

        # Filter to only recent summaries
        recent = [r for r in results if r.get("metadata", {}).get("added_at", 0) > week_ago]
        if len(recent) < 2:
            return "Not enough recent sessions to consolidate"

        summaries_text = "\n---\n".join(r["text"][:500] for r in recent)
        prompt = (
            "Consolidate these session summaries into a single weekly digest (3-5 bullet points). "
            "Focus on: key topics discussed, decisions made, research done, and recurring themes.\n\n"
            f"Session summaries:\n{summaries_text}"
        )
        digest, _, _ = await chat(prompt, model_preference="auto")
        if digest:
            await vector_store.add_document(
                vector_store.CONVERSATIONS_COLLECTION,
                doc_id=f"weekly_digest_{int(time.time())}",
                text=f"[Weekly digest] {digest}",
                metadata={"type": "weekly_digest", "period": "weekly"},
            )
            return f"Weekly digest created ({len(digest)} chars) from {len(recent)} sessions"
        return "Consolidation produced no output"
    except Exception as e:  # broad: intentional
        log.warning("Memory consolidation failed: %s", e)
        return f"Consolidation skipped: {e}"


# ---------------------------------------------------------------------------
# Self-healing skills — automated config repair
# ---------------------------------------------------------------------------

# qBittorrent download path auto-fix constants
QBIT_CONFIG_PATH = os.getenv("QBIT_CONFIG_PATH", "/volume1/docker/qbittorrent/config/qBittorrent/qBittorrent.conf")
QBIT_EXPECTED_SAVE_PATH = "/downloads"


async def fix_qbit_download_path() -> str:
    """Check qBittorrent's download path and fix if it drifted from /downloads."""
    from subprocess_utils import run as _run

    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    ssh_target = f"{NAS_SSH_USER}@{NAS_HOST}"

    # Read current config
    rc, out, _ = await _run(
        ["ssh"] + ssh_opts + [ssh_target, f"grep -E 'DefaultSavePath|TempPath' {QBIT_CONFIG_PATH}"],
        timeout=15,
    )
    if rc != 0:
        return "❌ Could not read qBittorrent config via SSH"

    current_save = ""
    for line in out.strip().split("\n"):
        if "DefaultSavePath=" in line:
            current_save = line.split("=", 1)[1].strip()

    if current_save == QBIT_EXPECTED_SAVE_PATH:
        return f"✅ qBittorrent download path is correct: `{current_save}`"

    # Fix: stop container, edit config, restart
    log.info("Self-heal: qBittorrent save path drifted to '%s', fixing to '%s'", current_save, QBIT_EXPECTED_SAVE_PATH)

    commands = [
        "/usr/local/bin/docker stop qbittorrent",
        "sleep 2",
        f"sed -i "
        f"-e 's|Session\\\\DefaultSavePath=.*|Session\\\\DefaultSavePath={QBIT_EXPECTED_SAVE_PATH}|' "
        f"-e 's|Session\\\\TempPath=.*|Session\\\\TempPath={QBIT_EXPECTED_SAVE_PATH}/incomplete|' "
        f"{QBIT_CONFIG_PATH}",
        "/usr/local/bin/docker start qbittorrent",
    ]
    rc, out, err = await _run(
        ["ssh"] + ssh_opts + [ssh_target, " && ".join(commands)],
        timeout=30,
    )
    if rc != 0:
        return f"❌ Failed to fix qBittorrent config: {err[:200]}"

    return f"✅ Fixed qBittorrent download path: `{current_save}` → `{QBIT_EXPECTED_SAVE_PATH}` (container restarted)"


async def fix_arr_remote_path() -> str:
    """Detect and fix *arr health issues by restarting services after qBit path is corrected."""
    from skills.media_skills import check_arr_health

    health = await check_arr_health()
    issues: list[str] = []

    if "remote" in health.lower() or "rom-downloads" in health.lower() or "path" in health.lower():
        # The path mapping issue is caused by qBittorrent reporting a wrong path.
        # Fix qBit first, then restart the *arr services so they re-query.
        qbit_result = await fix_qbit_download_path()
        issues.append(qbit_result)

        if "✅ Fixed" in qbit_result or "✅ qBittorrent download path is correct" in qbit_result:
            from skills import restart_container

            for svc in ["sonarr", "radarr"]:
                try:
                    result = await restart_container(svc)
                    issues.append(f"🔄 Restarted `{svc}`: {result}")
                except Exception as e:  # broad: intentional
                    issues.append(f"❌ Failed to restart `{svc}`: {e}")

            # Verify health after restart
            import asyncio

            await asyncio.sleep(15)
            new_health = await check_arr_health()
            issues.append(f"\n**Health after fix:**\n{new_health}")
    else:
        issues.append(f"✅ No remote path issues detected.\n{health}")

    return "\n".join(issues)


async def check_gluetun_vpn() -> str:
    """Check gluetun VPN container status on NAS (qBittorrent + SABnzbd depend on it)."""
    from subprocess_utils import run as _run

    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    ssh_target = f"{NAS_SSH_USER}@{NAS_HOST}"

    # Check container health
    rc, out, err = await _run(
        ["ssh"]
        + ssh_opts
        + [ssh_target, "/usr/local/bin/docker inspect gluetun --format '{{.State.Health.Status}} {{.State.Status}}'"],
        timeout=15,
    )
    if rc != 0:
        return f"❌ gluetun: not found on NAS ({err.strip()[:100]})"

    parts = out.strip().split()
    health_status = parts[0] if len(parts) > 0 else "unknown"
    container_status = parts[1] if len(parts) > 1 else "unknown"

    if health_status == "healthy" and container_status == "running":
        return "✅ gluetun VPN: healthy"
    elif container_status != "running":
        return f"❌ gluetun VPN: container {container_status}"
    else:
        return f"⚠️ gluetun VPN: health={health_status}, status={container_status}"


async def check_nas_health() -> str:
    """Check NAS RAID status and disk space via SSH."""
    from subprocess_utils import run as _run

    ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    ssh_target = f"{NAS_SSH_USER}@{NAS_HOST}"
    lines: list[str] = []

    # RAID status
    rc, out, _ = await _run(
        ["ssh"] + ssh_opts + [ssh_target, "cat /proc/mdstat 2>/dev/null | grep -E 'md|blocks|\\[' | head -10"],
        timeout=15,
    )
    if rc == 0 and out.strip():
        if "degraded" in out.lower() or "_" in out:
            lines.append(f"🔴 **RAID**: DEGRADED\n```\n{out.strip()}\n```")
        else:
            lines.append("✅ **RAID**: Healthy")
    else:
        lines.append("⚠️ **RAID**: Could not check (SSH failed)")

    # Disk space
    rc, out, _ = await _run(
        ["ssh"] + ssh_opts + [ssh_target, "df -h /volume1 /volume2 2>/dev/null"],
        timeout=15,
    )
    if rc == 0 and out.strip():
        for line in out.strip().split("\n"):
            if line.startswith("Filesystem"):
                continue
            parts = line.split()
            if len(parts) >= 5:
                mount = parts[5] if len(parts) > 5 else parts[0]
                pct = int(parts[4].rstrip("%"))
                icon = "🔴" if pct >= 90 else "🟡" if pct >= 75 else "✅"
                lines.append(f"{icon} **{mount}**: {parts[2]} used / {parts[1]} total ({pct}%)")

    # Uptime
    rc, out, _ = await _run(
        ["ssh"] + ssh_opts + [ssh_target, "uptime -p 2>/dev/null || uptime"],
        timeout=10,
    )
    if rc == 0 and out.strip():
        lines.append(f"⏱️ **Uptime**: {out.strip()}")

    return "\n".join(lines) or "❌ Could not reach NAS via SSH"


async def auto_cleanup_disk() -> str:
    """Auto-clean disk space: prune Docker images, rotate logs, clean temp files."""
    from subprocess_utils import run as _run

    results: list[str] = []

    # Docker image prune (dangling)
    rc, out, _ = await _run(["docker", "image", "prune", "-f"], timeout=60)
    if rc == 0:
        results.append(f"🐳 Docker prune: {out.strip().split(chr(10))[-1] if out else 'done'}")

    # Docker builder prune
    rc, out, _ = await _run(["docker", "builder", "prune", "-f", "--keep-storage=2GB"], timeout=60)
    if rc == 0:
        results.append("🔨 Builder prune: done")

    # Clean old logs in /memory/logs (older than 7 days)
    rc, out, _ = await _run(
        ["find", "/memory/logs", "-name", "*.log", "-mtime", "+7", "-delete", "-print"],
        timeout=30,
    )
    if rc == 0 and out.strip():
        count = len(out.strip().split("\n"))
        results.append(f"📝 Cleaned {count} old log file(s)")

    # Clean old audit logs (older than 30 days)
    rc, out, _ = await _run(
        ["find", "/memory/audit", "-name", "*.jsonl", "-mtime", "+30", "-delete", "-print"],
        timeout=30,
    )
    if rc == 0 and out.strip():
        count = len(out.strip().split("\n"))
        results.append(f"📋 Cleaned {count} old audit file(s)")

    # Report current disk usage after cleanup
    rc, out, _ = await _run(["df", "-h", "/"], timeout=10)
    if rc == 0:
        for line in out.strip().split("\n"):
            if not line.startswith("Filesystem"):
                results.append(f"💾 After cleanup: {line.strip()}")

    return "\n".join(results) or "No cleanup actions taken"


async def copilot_fix(prompt: str, cwd: str = "~/openclaw") -> str:
    """
    Run a fix via Copilot CLI on the Mac Mini host in programmatic mode.

    Spawns: copilot -p "<prompt>" --allow-all-tools --no-ask-user
    Returns the CLI output (truncated to 2000 chars for Discord).
    """
    from subprocess_utils import run as _run

    host_ip = _cfg.docker_host_ip
    host_user = os.getenv("HOST_SSH_USER", "davevoyles")
    ssh_opts = ["-o", "ConnectTimeout=15", "-o", "BatchMode=yes"]

    safe_prompt = prompt.replace('"', '\\"').replace("'", "'\\''")

    # Resolve the copilot binary path.
    # SSH with BatchMode=yes uses a non-login shell that skips .zshrc/.zprofile,
    # so /opt/homebrew/bin is not in PATH. We wrap in a login shell to fix that,
    # and also honour an explicit COPILOT_CLI_PATH override for environments where
    # the login shell init is slow or the binary lives elsewhere.
    copilot_bin = os.getenv("COPILOT_CLI_PATH", "copilot")
    inner_cmd = f'cd {cwd} && {copilot_bin} -p "{safe_prompt}" --allow-all-tools --no-ask-user 2>&1'
    # Run through a login shell so Homebrew PATH is loaded on the Mac Mini host.
    cmd = f"/bin/zsh -l -c '{inner_cmd}'"

    log.info("Copilot CLI bridge: running on %s@%s — prompt: %s", host_user, host_ip, prompt[:80])

    rc, out, err = await _run(
        ["ssh"] + ssh_opts + [f"{host_user}@{host_ip}", cmd],
        timeout=180,
    )

    if rc != 0 and not out:
        return f"❌ Copilot CLI failed (exit {rc}): {err[:500]}"

    result = (out or err or "No output").strip()
    if len(result) > 2000:
        result = result[:1950] + "\n\n…(truncated)"

    log.info("Copilot CLI bridge: completed (exit %d, %d chars)", rc, len(result))
    return f"{'✅' if rc == 0 else '⚠️'} Copilot CLI result:\n```\n{result}\n```"


# ---------------------------------------------------------------------------
# Skill exports
# ---------------------------------------------------------------------------

MAINTENANCE_SKILLS = {
    "update_skills": update_skills,
    "restart_gateway": restart_gateway,
    "backup_config_to_nas": backup_config_to_nas,
    "backup_vault_to_nas": backup_vault_to_nas,
    "run_maintenance": run_maintenance,
    "run_memory_decay": run_memory_decay,
    "run_memory_consolidation": run_memory_consolidation,
    "fix_qbit_download_path": fix_qbit_download_path,
    "fix_arr_remote_path": fix_arr_remote_path,
    "copilot_fix": copilot_fix,
    "check_nas_health": check_nas_health,
    "check_gluetun_vpn": check_gluetun_vpn,
    "auto_cleanup_disk": auto_cleanup_disk,
}
