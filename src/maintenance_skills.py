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
      4. memory_decay    — flag stale unused memories (Phase 14B)

    Registered by bot.py at startup for 4:00 AM daily execution.
    """
    log.info("4:00 AM maintenance cycle starting")
    steps = [
        ("skills-update", update_skills),
        ("gateway-restart", restart_gateway),
        ("nas-backup", backup_config_to_nas),
        ("memory-decay", run_memory_decay),
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
        for collection in [vector_store.MEMORIES_COLLECTION,
                           vector_store.CONVERSATIONS_COLLECTION,
                           vector_store.RESEARCH_COLLECTION]:
            candidates = await vector_store.get_decayed_documents(
                collection, max_age_days=30, min_access_count=2
            )
            if candidates:
                ids = [c["id"] for c in candidates]
                count = await vector_store.mark_decayed(collection, ids)
                total_decayed += count
                log.info("Decayed %d documents in %s", count, collection)

        if total_decayed > 0:
            return f"🧹 Memory decay: marked {total_decayed} old memories as decayed"
        return "🧹 Memory decay: no candidates found"
    except Exception as e:
        log.warning("Memory decay failed: %s", e)
        return f"⚠️ Memory decay failed: {e}"


async def run_memory_consolidation() -> str:
    """Weekly: summarize the week's session summaries into a digest.

    Called manually or by a weekly cron job (Sunday 4 AM).
    Distills multiple session summaries into a single weekly insight memory.
    """
    try:
        import vector_store
        from llm import chat

        # Fetch recent conversation summaries from the last 7 days
        import time
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
        digest, _, _ = await chat(prompt, model_preference="gemini")
        if digest:
            await vector_store.add_document(
                vector_store.CONVERSATIONS_COLLECTION,
                doc_id=f"weekly_digest_{int(time.time())}",
                text=f"[Weekly digest] {digest}",
                metadata={"type": "weekly_digest", "period": "weekly"},
            )
            return f"Weekly digest created ({len(digest)} chars) from {len(recent)} sessions"
        return "Consolidation produced no output"
    except Exception as e:
        log.warning("Memory consolidation failed: %s", e)
        return f"Consolidation skipped: {e}"


# ---------------------------------------------------------------------------
# Skill exports
# ---------------------------------------------------------------------------

MAINTENANCE_SKILLS = {
    "update_skills": update_skills,
    "restart_gateway": restart_gateway,
    "backup_config_to_nas": backup_config_to_nas,
    "run_maintenance": run_maintenance,
    "run_memory_decay": run_memory_decay,
    "run_memory_consolidation": run_memory_consolidation,
}
