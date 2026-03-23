"""
OpenClaw Skills — Phase 5: Unified Skill Registry
Core Docker & System Monitoring + Advanced Media/Network/Analysis skills.
"""

import asyncio
import logging
import shlex
from typing import Optional

log = logging.getLogger("openclaw.skills")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COMMAND_TIMEOUT = 15  # seconds


async def _run(cmd: list[str], timeout: int = COMMAND_TIMEOUT) -> tuple[int, str, str]:
    """Run a subprocess asynchronously and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        proc.kill()  # type: ignore[union-attr]
        return 1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"


def _truncate(text: str, limit: int = 1900) -> str:
    """Truncate text to fit Discord's 2000-char embed field limit."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


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


async def get_container_logs(service: str, lines: int = 30) -> str:
    """Retrieve the last N lines of logs from a container."""
    safe_name = shlex.quote(service).strip("'")
    lines = min(max(lines, 5), 100)  # clamp 5–100

    rc, out, err = await _run([
        "docker", "logs", safe_name,
        "--tail", str(lines),
        "--timestamps",
    ], timeout=20)
    if rc != 0:
        return f"❌ Could not fetch logs for '{service}': {err.strip()}"
    return _truncate(out.strip()) or "(empty log output)"


async def restart_container(service: str) -> str:
    """Restart a Docker container by name. Returns result message."""
    safe_name = shlex.quote(service).strip("'")

    # Verify container exists first
    rc, _, err = await _run(["docker", "inspect", "--format", "{{.State.Status}}", safe_name])
    if rc != 0:
        return f"❌ Container '{service}' not found."

    rc, out, err = await _run(["docker", "restart", safe_name], timeout=60)
    if rc != 0:
        return f"❌ Failed to restart '{service}': {err.strip()}"
    return f"✅ Container '{service}' restarted successfully."


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

GLANCES_URL = "http://192.168.1.93:61208"


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
            else:
                lines.append("**CPU**: unavailable")

            # Memory
            if not isinstance(mem_resp, BaseException) and mem_resp.status == 200:
                mem = await mem_resp.json()
                used_gb = mem.get("used", 0) / (1024 ** 3)
                total_gb = mem.get("total", 0) / (1024 ** 3)
                pct = mem.get("percent", 0)
                lines.append(f"**Memory**: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")
            else:
                lines.append("**Memory**: unavailable")

            # Disk
            if not isinstance(disk_resp, BaseException) and disk_resp.status == 200:
                disks = await disk_resp.json()
                for d in disks[:5]:  # limit to 5 mounts
                    mp = d.get("mnt_point", "?")
                    used_gb = d.get("used", 0) / (1024 ** 3)
                    total_gb = d.get("size", 0) / (1024 ** 3)
                    pct = d.get("percent", 0)
                    lines.append(f"**Disk** `{mp}`: {used_gb:.1f} / {total_gb:.1f} GB ({pct}%)")
            else:
                lines.append("**Disk**: unavailable")

            return "\n".join(lines)
    except Exception:
        pass

    # Fallback: use local commands
    return await _get_system_stats_fallback()


async def _get_system_stats_fallback() -> str:
    """Fallback system stats using shell commands (no Glances)."""
    lines = []

    # CPU load average
    rc, out, _ = await _run(["sysctl", "-n", "vm.loadavg"])
    if rc == 0:
        lines.append(f"**Load Average**: {out.strip()}")

    # Memory (macOS)
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
# Registry — skill name → callable (used by bot.py)
# ---------------------------------------------------------------------------

SKILLS = {
    "list_containers": list_containers,
    "get_container_status": get_container_status,
    "get_container_logs": get_container_logs,
    "restart_container": restart_container,
    "get_docker_stats": get_docker_stats,
    "get_system_stats": get_system_stats,
    "get_uptime": get_uptime,
}

# Merge advanced skills (media, network, Plex, reports)
from advanced_skills import ADVANCED_SKILLS  # noqa: E402
SKILLS.update(ADVANCED_SKILLS)

# Merge analyzer skills (AI log analysis)
from analyzer import ANALYZER_SKILLS  # noqa: E402
SKILLS.update(ANALYZER_SKILLS)

# Add Phase 5 supplemental skills (QMD, AgentMail)
from qmd import remember_fact, recall_fact, list_memories
from agentmail import send_agent_mail

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
from spending import get_spending, get_daily_spending

SKILLS.update({
    "get_spending": get_spending,
    "get_daily_spending": get_daily_spending,
})
