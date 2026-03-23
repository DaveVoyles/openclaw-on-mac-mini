"""
OpenClaw Network Skills — Phase 6: Remote Access & Monitoring
Provides Tailscale status, external connectivity checks, and DNS resolution.
"""

import asyncio
import logging
import os
import socket
from typing import Optional

log = logging.getLogger("openclaw.network")

HOST = os.getenv("DOCKER_HOST_IP", "192.168.1.93")

# Common Tailscale binary locations on macOS
_TAILSCALE_PATHS = [
    "/Applications/Tailscale.app/Contents/MacOS/tailscale",
    "/usr/local/bin/tailscale",
    "/usr/bin/tailscale",
]

COMMAND_TIMEOUT = 15


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
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"


def _find_tailscale() -> Optional[str]:
    """Locate the Tailscale binary on this system."""
    for path in _TAILSCALE_PATHS:
        try:
            import os as _os
            if _os.path.isfile(path) and _os.access(path, _os.X_OK):
                return path
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_tailscale_status() -> str:
    """Get Tailscale VPN status including connected peers and this device's IP."""
    ts = _find_tailscale()
    if not ts:
        return "⚠️ Tailscale binary not found. Is Tailscale installed?"

    rc, peers_out, err = await _run([ts, "status", "--peers=false", "--self=true"])
    if rc != 0:
        return f"❌ Tailscale error: {err.strip() or 'unknown error'}"

    lines = [l for l in peers_out.strip().splitlines() if l.strip()]
    if not lines:
        return "⚠️ Tailscale is not connected."

    # Get this device's Tailscale IP
    rc2, ip_out, _ = await _run([ts, "ip", "-4"])
    ts_ip = ip_out.strip() if rc2 == 0 else "unknown"

    return (
        f"**Tailscale Status**\n"
        f"IP: `{ts_ip}` → Accessible as `http://{ts_ip}:8765/health`\n"
        f"State: {lines[0].strip() if lines else 'Unknown'}"
    )


async def get_network_status() -> str:
    """Summarize external internet + LAN connectivity and Tailscale status."""
    results = []

    # 1. LAN reachability (ping NAS)
    nas_ip = os.getenv("NAS_IP", "192.168.1.8")
    rc, _, _ = await _run(["ping", "-c", "1", "-W", "1", nas_ip])
    results.append(f"{'✅' if rc == 0 else '❌'} LAN (NAS {nas_ip})")

    # 2. External internet (ping 1.1.1.1)
    rc2, _, _ = await _run(["ping", "-c", "1", "-W", "2", "1.1.1.1"])
    results.append(f"{'✅' if rc2 == 0 else '❌'} Internet (1.1.1.1)")

    # 3. DNS resolution
    try:
        socket.setdefaulttimeout(3)
        socket.gethostbyname("google.com")
        results.append("✅ DNS Resolution")
    except socket.error:
        results.append("❌ DNS Resolution failed")

    # 4. Tailscale
    ts = _find_tailscale()
    if ts:
        rc3, ip_out, _ = await _run([ts, "ip", "-4"])
        if rc3 == 0 and ip_out.strip():
            results.append(f"✅ Tailscale IP: `{ip_out.strip()}`")
        else:
            results.append("⚠️ Tailscale not connected")
    else:
        results.append("⚠️ Tailscale not installed")

    # 5. Self health check
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            async with session.get(f"http://{HOST}:8765/health") as resp:
                results.append(f"{'✅' if resp.status == 200 else '❌'} OpenClaw health endpoint (:{8765})")
    except Exception:
        results.append(f"❌ OpenClaw health endpoint (:{8765})")

    results.append(f"🖥️ Host: `{HOST}`")
    return "\n".join(results)


async def run_speed_test() -> str:
    """Run a quick network speed test using curl to measure download throughput."""
    results = []

    # Download a 10MB test file from Cloudflare
    rc, out, err = await _run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{speed_download}",
         "https://speed.cloudflare.com/__down?bytes=10000000"],
        timeout=30
    )
    if rc == 0 and out.strip():
        try:
            speed_bps = float(out.strip())
            speed_mbps = speed_bps / (1024 * 1024)
            results.append(f"**Download**: {speed_mbps:.1f} MB/s ({speed_mbps * 8:.0f} Mbps)")
        except ValueError:
            results.append("⚠️ Could not parse speed result")
    else:
        results.append(f"❌ Speed test failed: {err.strip()}")

    # DNS latency
    import time
    start = time.monotonic()
    try:
        socket.setdefaulttimeout(3)
        socket.gethostbyname("google.com")
        dns_ms = (time.monotonic() - start) * 1000
        results.append(f"**DNS Latency**: {dns_ms:.0f} ms")
    except Exception:
        results.append("❌ DNS lookup failed")

    return "\n".join(results) if results else "❌ Speed test unavailable"
