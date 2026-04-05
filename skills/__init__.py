"""
OpenClaw Skills — Phase 5: Unified Skill Registry
Core Docker & System Monitoring + Advanced Media/Network/Analysis skills.
"""

import asyncio
import logging
import os
import shlex

from memory_manager import recall as memory_recall  # noqa: F401
from memory_manager import stats as memory_stats  # noqa: F401

# Unified memory manager (Phase 16)
from memory_manager import store as memory_store  # noqa: F401
from subprocess_utils import COMMAND_TIMEOUT  # noqa: F401
from subprocess_utils import run as _run

log = logging.getLogger("openclaw.skills")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from utils import truncate as _truncate

# ---------------------------------------------------------------------------
# 6.1  Docker & Container Management
# ---------------------------------------------------------------------------


async def list_containers() -> str:
    """List all running Docker containers with name, status, and ports."""
    rc, out, err = await _run([
        "docker", "ps",
        "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
    ])
    if rc != 0:
        return f"❌ Failed to list containers: {err}"
    return out.strip() or "No containers running."


async def get_container_status(service: str) -> str:
    """Get detailed status for a specific container."""
    safe_name = shlex.quote(service).strip("'")

    # Get basic info
    rc, out, err = await _run([
        "docker", "inspect",
        "--format",
        "Name: {{.Name}}\n"
        "Image: {{.Config.Image}}\n"
        "Status: {{.State.Status}}\n"
        "Health: {{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}\n"
        "Started: {{.State.StartedAt}}\n"
        "Restarts: {{.RestartCount}}\n"
        "Ports: {{range $p, $conf := .NetworkSettings.Ports}}{{$p}}→{{range $conf}}{{.HostPort}}{{end}} {{end}}",
        safe_name,
    ])
    if rc != 0:
        return f"❌ Container '{service}' not found or not running.\n{err.strip()}"

    # Get resource usage (single snapshot)
    rc2, stats_out, _ = await _run([
        "docker", "stats", "--no-stream", "--no-trunc",
        "--format", "CPU: {{.CPUPerc}}  |  Memory: {{.MemUsage}}  |  Net I/O: {{.NetIO}}",
        safe_name,
    ])
    if rc2 == 0 and stats_out.strip():
        out += f"\n{stats_out.strip()}"

    return out.strip()


async def get_container_logs(service: str, lines: int = 100) -> str:
    """Retrieve the last N lines of logs from a container, with smart noise filtering.

    Always returns the last 100 lines.  Lines containing 'error', 'warn',
    'exception', 'critical', or 'fatal' are surfaced first so signal doesn't
    get buried in verbose health-check output.
    """
    import re as _re
    safe_name = shlex.quote(service).strip("'")
    # Fetch up to 500 lines so we have enough to extract signal from
    fetch_lines = max(min(lines, 500), 100)

    rc, out, err = await _run([
        "docker", "logs", safe_name,
        "--tail", str(fetch_lines),
        "--timestamps",
    ], timeout=20)
    if rc != 0:
        return f"❌ Could not fetch logs for '{service}': {err.strip()}"
    if not out.strip():
        return "(empty log output)"

    all_lines = out.strip().splitlines()

    _signal_re = _re.compile(r"error|warn|exception|critical|fatal|traceback", _re.IGNORECASE)
    signal_lines = [ln for ln in all_lines if _signal_re.search(ln)]
    tail_lines = all_lines[-100:]

    # Merge: signal lines first (deduplicated), then the tail
    seen: set[str] = set()
    merged: list[str] = []
    for line in signal_lines:
        if line not in seen:
            seen.add(line)
            merged.append(line)

    if signal_lines:
        merged.append("--- last 100 lines ---")

    for line in tail_lines:
        if line not in seen:
            seen.add(line)
            merged.append(line)

    return _truncate("\n".join(merged)) or "(empty log output)"


async def restart_container(service: str) -> str:
    """Restart a Docker container by name. Falls back to NAS via SSH if not found locally."""
    safe_name = shlex.quote(service).strip("'")

    # Try local Docker first
    rc, _, err = await _run(["docker", "inspect", "--format", "{{.State.Status}}", safe_name])
    if rc == 0:
        rc, out, err = await _run(["docker", "restart", safe_name], timeout=60)
        if rc != 0:
            return f"❌ Failed to restart '{service}': {err.strip()}"
        return f"✅ Container '{service}' restarted successfully."

    # Not found locally — try NAS via SSH
    try:
        from maintenance_skills import NAS_HOST, NAS_SSH_PORT, NAS_SSH_USER

        # Special case: qbittorrent depends on gluetun (VPN) — check gluetun is healthy first
        if service == "qbittorrent":
            from maintenance_skills import check_gluetun_vpn
            vpn_check = await check_gluetun_vpn()
            if "❌" in vpn_check or "unhealthy" in vpn_check.lower():
                return f"❌ Cannot restart qbittorrent: gluetun VPN is down or unhealthy ({vpn_check})"

        ssh_opts = ["-p", str(NAS_SSH_PORT), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
        ssh_target = f"{NAS_SSH_USER}@{NAS_HOST}"
        rc, out, err = await _run(
            ["ssh"] + ssh_opts + [ssh_target, f"/usr/local/bin/docker restart {safe_name}"],
            timeout=60,
        )
        if rc == 0:
            # Double-check qbittorrent: verify it came up healthy
            if service == "qbittorrent":
                await asyncio.sleep(5)  # Give it time to start
                post_check = await check_gluetun_vpn()
                if "❌" in post_check:
                    return f"⚠️ qbittorrent restarted but gluetun VPN is now down: {post_check}"
            return f"✅ Container '{service}' restarted on NAS successfully."
        detail = (err or out or "unknown error").strip()
        return f"❌ Failed to restart '{service}' on NAS: {detail[:200]}"
    except Exception:
        return f"❌ Container '{service}' not found."


async def stop_container(service: str) -> str:
    """Stop a Docker container by name. Returns result message."""
    safe_name = shlex.quote(service).strip("'")

    rc, _, err = await _run(["docker", "inspect", "--format", "{{.State.Status}}", safe_name])
    if rc != 0:
        return f"❌ Container '{service}' not found."

    rc, out, err = await _run(["docker", "stop", safe_name], timeout=60)
    if rc != 0:
        return f"❌ Failed to stop '{service}': {err.strip()}"
    return f"✅ Container '{service}' stopped successfully."


async def pause_container(service: str) -> str:
    """Pause a running Docker container by name. Returns result message."""
    safe_name = shlex.quote(service).strip("'")

    rc, _, err = await _run(["docker", "inspect", "--format", "{{.State.Status}}", safe_name])
    if rc != 0:
        return f"❌ Container '{service}' not found."

    rc, out, err = await _run(["docker", "pause", safe_name], timeout=10)
    if rc != 0:
        return f"❌ Failed to pause '{service}': {err.strip()}"
    return f"✅ Container '{service}' paused successfully."


async def unpause_container(service: str) -> str:
    """Unpause a paused Docker container by name. Returns result message."""
    safe_name = shlex.quote(service).strip("'")

    rc, _, err = await _run(["docker", "inspect", "--format", "{{.State.Status}}", safe_name])
    if rc != 0:
        return f"❌ Container '{service}' not found."

    rc, out, err = await _run(["docker", "unpause", safe_name], timeout=10)
    if rc != 0:
        return f"❌ Failed to unpause '{service}': {err.strip()}"
    return f"✅ Container '{service}' unpaused successfully."


async def get_docker_stats() -> str:
    """Get resource usage for all running containers."""
    rc, out, err = await _run([
        "docker", "stats", "--no-stream",
        "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}",
    ], timeout=30)
    if rc != 0:
        return f"❌ Failed to get Docker stats: {err}"
    return _truncate(out.strip())


# ---------------------------------------------------------------------------
# 6.2  System Monitoring
# ---------------------------------------------------------------------------

from config import cfg as _cfg

GLANCES_URL = os.getenv("GLANCES_URL", f"http://{_cfg.docker_host_ip}:61208")


async def get_system_stats() -> str:
    """
    Get system stats from Glances API.
    Falls back to local commands if Glances is unavailable.
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            # Fetch CPU, memory, disk in parallel
            cpu_task = session.get(f"{GLANCES_URL}/api/4/quicklook")
            mem_task = session.get(f"{GLANCES_URL}/api/4/mem")
            disk_task = session.get(f"{GLANCES_URL}/api/4/fs")

            cpu_resp, mem_resp, disk_resp = await asyncio.gather(
                cpu_task, mem_task, disk_task,
                return_exceptions=True,
            )

            lines = []

            # CPU
            if not isinstance(cpu_resp, BaseException) and cpu_resp.status == 200:
                cpu = await cpu_resp.json()
                lines.append(f"**CPU**: {cpu.get('cpu', 'N/A')}% ({cpu.get('cpu_number', '?')} cores)")

            # Memory
            if not isinstance(mem_resp, BaseException) and mem_resp.status == 200:
                mem = await mem_resp.json()
                used_gb = mem.get("used", 0) / (1024 ** 3)
                total_gb = mem.get("total", 0) / (1024 ** 3)
                pct = mem.get("percent", 0)
                lines.append(f"**Memory**: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")

            # Disk
            if not isinstance(disk_resp, BaseException) and disk_resp.status == 200:
                disks = await disk_resp.json()
                for d in disks[:5]:  # limit to 5 mounts
                    mp = d.get("mnt_point", "?")
                    used_gb = d.get("used", 0) / (1024 ** 3)
                    total_gb = d.get("size", 0) / (1024 ** 3)
                    pct = d.get("percent", 0)
                    lines.append(f"**Disk** `{mp}`: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")

            if lines:
                return "\n".join(lines)
    except Exception:
        pass

    # Fallback: use local commands
    return await _get_system_stats_fallback()


async def _get_system_stats_fallback() -> str:
    """Fallback system stats using shell commands or /proc (Linux container)."""
    lines = []

    # CPU load average (read from /proc/loadavg)
    try:
        with open("/proc/loadavg", "r") as f:
            lines.append(f"**Load Average**: {f.read().split()[0]}")
    except (FileNotFoundError, IndexError):
        rc, out, _ = await _run(["sysctl", "-n", "vm.loadavg"])
        if rc == 0:
            lines.append(f"**Load Average**: {out.strip()}")

    # Memory (Linux/Docker)
    try:
        with open("/proc/meminfo", "r") as f:
            mem_info = {line.split(":")[0]: line.split(":")[1].strip() for line in f}
            if "MemTotal" in mem_info and "MemAvailable" in mem_info:
                total_kb = int(mem_info["MemTotal"].split()[0])
                avail_kb = int(mem_info["MemAvailable"].split()[0])
                used_kb = total_kb - avail_kb
                used_gb = used_kb / (1024 ** 2)
                total_gb = total_kb / (1024 ** 2)
                pct = round((used_kb / total_kb) * 100, 1)
                lines.append(f"**Memory**: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")
    except (FileNotFoundError, KeyError, ValueError):
        # Memory (macOS fallback)
        rc, out, _ = await _run(["vm_stat"])
        if rc == 0:
            # Parse page size and used/free pages
            page_lines = out.strip().split("\n")
            page_size = 16384  # default on Apple Silicon
            free = active = inactive = wired = 0
            for line in page_lines:
                if "page size of" in line:
                    parts = line.split()
                    page_size = int(parts[-2])
                elif "Pages free:" in line:
                    free = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages active:" in line:
                    active = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages inactive:" in line:
                    inactive = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages wired" in line:
                    wired = int(line.split(":")[1].strip().rstrip("."))
            total_pages = free + active + inactive + wired
            used_gb = (active + wired) * page_size / (1024 ** 3)
            total_gb = total_pages * page_size / (1024 ** 3)
            pct = round(used_gb / total_gb * 100, 1) if total_gb > 0 else 0
            lines.append(f"**Memory**: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")

    # Disk
    rc, out, _ = await _run(["df", "-h", "/"])
    if rc == 0:
        df_lines = out.strip().split("\n")
        if len(df_lines) >= 2:
            parts = df_lines[1].split()
            lines.append(f"**Disk** `/`: {parts[2]} used / {parts[1]} total ({parts[4]})")

    return "\n".join(lines) or "❌ Could not fetch system stats."


async def get_uptime() -> str:
    """Get system uptime."""
    rc, out, _ = await _run(["uptime"])
    if rc != 0:
        return "❌ Could not get uptime."
    return out.strip()


# ---------------------------------------------------------------------------
# Docker Compose config awareness (#12)
# ---------------------------------------------------------------------------

import os as _os
from pathlib import Path as _Path

import yaml as _yaml

_COMPOSE_PATHS = [
    _Path(_os.getenv("COMPOSE_FILE", "/app/docker-compose.yml")),
    _Path("/docker-compose.yml"),
    _Path("/app/docker-compose.yaml"),
]


async def get_compose_config(service: str = "") -> str:
    """Read the Docker Compose configuration and return port mappings, volumes,
    and environment variable names for all services (or a specific one).

    This gives the LLM full context for troubleshooting — e.g. which host port
    maps to which container port, which volumes are mounted, and what env vars
    each service expects.
    """
    compose_file: _Path | None = None
    for p in _COMPOSE_PATHS:
        if p.exists():
            compose_file = p
            break

    if compose_file is None:
        return (
            "⚠️ docker-compose.yml not found at any of the expected paths. "
            "Set the COMPOSE_FILE env var to the correct path."
        )

    try:
        with open(compose_file) as fh:
            data = _yaml.safe_load(fh) or {}
    except Exception as e:
        return f"❌ Failed to read {compose_file}: {e}"

    services = data.get("services", {})
    if not services:
        return "⚠️ No services defined in docker-compose.yml."

    if service:
        svc_lower = service.lower()
        # Support fuzzy match (e.g. "sonarr" matches "sonarr" key exactly)
        key = next((k for k in services if k.lower() == svc_lower), None)
        if key is None:
            key = next((k for k in services if svc_lower in k.lower()), None)
        if key is None:
            available = ", ".join(sorted(services.keys()))
            return f"❌ Service '{service}' not found. Available: {available}"
        services = {key: services[key]}

    lines: list[str] = []
    for svc_name, cfg in sorted(services.items()):
        lines.append(f"**{svc_name}**")
        image = cfg.get("image", cfg.get("build", "(local build)"))
        lines.append(f"  image: {image}")

        ports = cfg.get("ports", [])
        if ports:
            lines.append("  ports: " + ", ".join(str(p) for p in ports))

        volumes = cfg.get("volumes", [])
        if volumes:
            lines.append("  volumes: " + ", ".join(str(v) for v in volumes))

        env = cfg.get("environment", [])
        if isinstance(env, dict):
            env_names = list(env.keys())
        else:
            # list of "KEY=value" or "KEY"
            env_names = [e.split("=")[0] if "=" in str(e) else str(e) for e in env]
        if env_names:
            lines.append("  env_vars: " + ", ".join(env_names))

        restart = cfg.get("restart", "")
        if restart:
            lines.append(f"  restart: {restart}")

        networks = cfg.get("networks", [])
        if networks:
            net_names = list(networks.keys()) if isinstance(networks, dict) else networks
            lines.append("  networks: " + ", ".join(str(n) for n in net_names))

        lines.append("")

    return _truncate("\n".join(lines).strip())


# ---------------------------------------------------------------------------
# Registry — skill name → callable (used by bot.py)
# ---------------------------------------------------------------------------

SKILLS = {
    "list_containers": list_containers,
    "get_container_status": get_container_status,
    "get_container_logs": get_container_logs,
    "restart_container": restart_container,
    "stop_container": stop_container,
    "pause_container": pause_container,
    "unpause_container": unpause_container,
    "get_docker_stats": get_docker_stats,
    "get_system_stats": get_system_stats,
    "get_uptime": get_uptime,
    "get_compose_config": get_compose_config,
}

# Merge advanced skills (media, network, Plex, reports)
from .advanced_skills import ADVANCED_SKILLS  # noqa: E402

SKILLS.update(ADVANCED_SKILLS)

# Merge analyzer skills (AI log analysis)
from analyzer import ANALYZER_SKILLS  # noqa: E402

SKILLS.update(ANALYZER_SKILLS)

# Add Phase 5 supplemental skills (QMD, AgentMail)
from agentmail import send_agent_mail
from qmd import list_memories, recall_fact, remember_fact

SKILLS.update({
    "remember_fact": remember_fact,
    "recall_fact": recall_fact,
    "list_memories": list_memories,
    "send_agent_mail": send_agent_mail,
})

# Add Phase 6 network skills (Tailscale, connectivity, speed)
from network import get_network_status, get_tailscale_status, run_speed_test

SKILLS.update({
    "get_network_status": get_network_status,
    "get_tailscale_status": get_tailscale_status,
    "run_speed_test": run_speed_test,
})

# Add spending tracker skills
from agent_loop import AGENT_LOOP_SKILLS
from spending import get_daily_spending, get_spending

SKILLS.update({
    "get_spending": get_spending,
    "get_daily_spending": get_daily_spending,
})

# Add Phase 12 autonomous skills (Planning, Agent Loop)
SKILLS.update(AGENT_LOOP_SKILLS)

# Add Phase 6 extended integrations (Overseerr, NAS, Email, Calendar)
from calendar_skills import CALENDAR_SKILLS
from email_skills import EMAIL_SKILLS
from nas import NAS_SKILLS
from overseerr import OVERSEERR_SKILLS
from skills.reporting_skills import REPORTING_SKILLS
from skills.synthesis_skills import SYNTHESIS_SKILLS

SKILLS.update(OVERSEERR_SKILLS)
SKILLS.update(NAS_SKILLS)
SKILLS.update(EMAIL_SKILLS)
SKILLS.update(CALENDAR_SKILLS)
SKILLS.update(REPORTING_SKILLS)
SKILLS.update(SYNTHESIS_SKILLS)

# Add Maton API Gateway skill (managed OAuth proxy to 100+ APIs)
from gateway import GATEWAY_SKILLS

SKILLS.update(GATEWAY_SKILLS)

# Mission Control — Kanban task management
from mission_control import MISSION_CONTROL_SKILLS

SKILLS.update(MISSION_CONTROL_SKILLS)

# Ontology — structured graph memory
from ontology_skills import ONTOLOGY_SKILLS

SKILLS.update(ONTOLOGY_SKILLS)

# Web & Git skills (webfetch-md, git-essentials)
from git_skills import GIT_SKILLS

SKILLS.update(GIT_SKILLS)

# News & Data APIs (Phase: NLP Enhancement)
from skills.finance_skills import FINANCE_SKILLS
from skills.news_skills import NEWS_SKILLS
from skills.sports_skills import SPORTS_SKILLS

for skill in NEWS_SKILLS:
    SKILLS[skill["name"]] = skill["function"]

for skill in SPORTS_SKILLS:
    SKILLS[skill["name"]] = skill["function"]

for skill in FINANCE_SKILLS:
    SKILLS[skill["name"]] = skill["function"]

# Worker sub-agent (autonomous task delegation)
from worker_agent import WORKER_SKILLS

SKILLS.update(WORKER_SKILLS)

# Scheduler LLM controls (create/cancel/list scheduled tasks)
from scheduler import SCHEDULER_SKILLS

SKILLS.update(SCHEDULER_SKILLS)

# RSS feed monitoring
from rss_skills import RSS_SKILLS

SKILLS.update(RSS_SKILLS)

# URL change monitoring
from monitor_skills import MONITOR_SKILLS

SKILLS.update(MONITOR_SKILLS)

# Uptime Kuma — query monitor status, uptime stats, incidents
from uptime_kuma_skills import UPTIME_KUMA_SKILLS

SKILLS.update(UPTIME_KUMA_SKILLS)

# Weather skill (Phase E — wraps wttr.in via aiohttp, no API key)
from .advanced_skills import get_weather

SKILLS.update({"get_weather": get_weather})

# Obsidian vault — save/list/index markdown notes
from obsidian_writer import OBSIDIAN_SKILLS

SKILLS.update(OBSIDIAN_SKILLS)

# Maintenance — 4AM cron: skill updates, gateway restart, NAS backup
from maintenance_skills import MAINTENANCE_SKILLS

SKILLS.update(MAINTENANCE_SKILLS)

# Dream Cycle — Auto-Dream memory consolidation engine
from dream_cycle import DREAM_SKILLS

SKILLS.update(DREAM_SKILLS)

# Agent Loop — persistent plan management for autonomous goals
from agent_loop import AGENT_LOOP_SKILLS

SKILLS.update(AGENT_LOOP_SKILLS)

# Code sandbox — LLM-driven Python execution (Phase 3: Code Interpreter)
from code_sandbox import run_code as _run_sandbox_code


async def execute_python_code(code: str, stdin_data: str = "") -> str:
    """Execute Python code in a sandboxed Docker container and return results."""
    stdout, stderr, exit_code = await _run_sandbox_code(code, language="python", stdin_data=stdin_data)
    parts = []
    if stdout:
        parts.append(f"Output:\n{stdout}")
    if stderr:
        parts.append(f"Errors:\n{stderr}")
    if exit_code != 0:
        parts.append(f"Exit code: {exit_code}")
    if not parts:
        parts.append("(no output)")
    return "\n".join(parts)


SKILLS["execute_python_code"] = execute_python_code

# ---------------------------------------------------------------------------
# Skill categories for organized display (Phase 16)
# ---------------------------------------------------------------------------

SKILL_CATEGORIES = {
    "🐳 Docker & System": [
        "list_containers", "get_container_status", "get_container_logs",
        "restart_container", "stop_container", "pause_container",
        "unpause_container", "get_docker_stats", "get_system_stats",
        "get_uptime", "get_compose_config",
    ],
    "🎬 Media & Downloads": [
        "search_media", "add_to_sonarr", "add_to_radarr",
        "get_download_queue", "get_plex_activity",
        "check_arr_health", "check_download_clients", "check_plex_status",
        "get_recent_additions", "get_pending_requests", "approve_request",
        "deny_request", "get_request_stats",
    ],
    "🌐 Network & Monitoring": [
        "ping_host", "check_service_ports", "get_network_status",
        "get_tailscale_status", "run_speed_test",
        "get_all_monitor_status", "get_monitor_detail",
        "get_monitors_down", "get_uptime_summary",
    ],
    "🔍 Web & Research": [
        "search_web", "browse_url", "webfetch_md", "get_weather",
        "compare_sources",
    ],
    "🧠 Memory & Knowledge": [
        "remember_fact", "recall_fact", "list_memories",
        "ontology_create_entity", "ontology_get_entity", "ontology_query",
        "ontology_update_entity", "ontology_relate", "ontology_get_related",
        "ontology_validate", "run_memory_consolidation", "run_memory_decay",
    ],
    "📋 Planning & Tasks": [
        "create_plan", "update_plan_step", "update_plan_status", "read_plan",
        "list_plans", "adjust_plan", "execute_plan", "resume_plan",
        "cancel_plan", "init_planning_files", "decompose_goal",
        "get_mission_tasks", "update_task_status", "get_task_detail",
        "add_task_comment", "complete_task",
    ],
    "📡 RSS & URL Monitoring": [
        "fetch_rss_feed", "search_rss", "get_rss_digest", "list_rss_feeds",
        "snapshot_url", "check_url_for_changes", "list_monitored_urls",
        "remove_url_monitor",
    ],
    "📧 Communication": [
        "send_email", "search_emails", "read_inbox",
        "create_calendar_event", "get_todays_events", "get_upcoming_events",
        "send_agent_mail",
    ],
    "🗄️ NAS & Storage": [
        "backup_config_to_nas", "get_backup_status", "get_disk_smart_status",
        "get_nas_alerts", "get_nas_storage_health", "nas_create_folder",
        "nas_write_file",
    ],
    "⚙️ Automation & Scheduling": [
        "create_scheduled_task", "cancel_scheduled_task", "list_scheduled_tasks",
        "gateway_request", "gateway_create_connection", "gateway_list_connections",
        "spawn_worker", "create_google_doc", "create_onedrive_file",
    ],
    "🔧 Development & Git": [
        "execute_python_code", "git_status", "git_diff", "git_log", "git_commit",
    ],
    "📝 Obsidian Vault": [
        "save_to_vault", "list_vault", "index_vault_to_qmd",
    ],
    "🔬 Analysis & Reports": [
        "analyze_logs", "suggest_fixes", "create_status_report",
    ],
    "📊 Weekly Recaps": [
        "get_available_templates", "generate_recap_from_template",
    ],
    "💰 Spending & Budget": [
        "get_spending", "get_daily_spending",
    ],
    "🌙 Auto-Dream": [
        "dream_now", "get_memory_health",
    ],
    "🛠️ Maintenance": [
        "run_maintenance", "update_skills", "restart_gateway",
    ],
    "📊 Trend Tracking": [
        "track_topic", "untrack_topic", "get_trending_topics",
        "detect_breaking_news", "get_topic_trajectory", "list_tracked_topics",
        "update_all_tracked_trends",
    ],
}

# Auto-populate: any skill NOT in a category goes to "📦 Other"
_categorized = set()
for _skills_list in SKILL_CATEGORIES.values():
    _categorized.update(_skills_list)
_uncategorized = [name for name in SKILLS if name not in _categorized]
if _uncategorized:
    SKILL_CATEGORIES["📦 Other"] = sorted(_uncategorized)

# Scheduled research — recurring research queries
from research_agent import run_scheduled_research

SKILLS["run_scheduled_research"] = run_scheduled_research

# Recap templates — topic-specific weekly recap generation
from skills.recap_templates import (
    generate_recap_from_template,
    get_available_templates,
)

SKILLS.update({
    "get_available_templates": get_available_templates,
    "generate_recap_from_template": generate_recap_from_template,
})

# Trend tracking skills
from skills.trend_skills import (
    detect_breaking_news,
    get_topic_trajectory,
    get_trending_topics,
    list_tracked_topics,
    track_topic,
    untrack_topic,
    update_all_tracked_trends,
)

SKILLS.update({
    "track_topic": track_topic,
    "untrack_topic": untrack_topic,
    "get_trending_topics": get_trending_topics,
    "detect_breaking_news": detect_breaking_news,
    "get_topic_trajectory": get_topic_trajectory,
    "list_tracked_topics": list_tracked_topics,
    "update_all_tracked_trends": update_all_tracked_trends,
})
