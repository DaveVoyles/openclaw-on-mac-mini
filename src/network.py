"""
OpenClaw Network Skills — Phase 6: Remote Access & Monitoring
Provides Tailscale status, external connectivity checks, and DNS resolution.
"""

import asyncio
import logging
import os
import socket
from typing import Optional

import aiohttp

log = logging.getLogger("openclaw.network")

from http_session import SessionManager

_sessions = SessionManager(timeout=5, name="network")
close_session = _sessions.close


def _get_session() -> aiohttp.ClientSession:
    """Sync wrapper — session created lazily on first await in caller."""
    import asyncio
    loop = asyncio.get_event_loop()
    if not loop.is_running():
        return loop.run_until_complete(_sessions.get())
    # Called from async context — return a coro result via __await__ won't work,
    # so just do the lazy init inline (matches original behaviour).
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        )
    return _http_session


_http_session: aiohttp.ClientSession | None = None

from config import cfg as _cfg
HOST = os.getenv("DOCKER_HOST_IP", _cfg.docker_host_ip)
DNS_TEST_HOST = os.getenv("DNS_TEST_HOST", "8.8.8.8")
PING_TEST_HOST = os.getenv("PING_TEST_HOST", "1.1.1.1")

# Common Tailscale binary locations on macOS
_TAILSCALE_PATHS = [
    "/opt/homebrew/bin/tailscale",
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
        except Exception as exc:
            log.debug("Tailscale path check failed for %s: %s", path, exc)
    return None


# Cache the result so we don't scan the filesystem on every command
_tailscale_path_cache: Optional[str] = None
_tailscale_path_searched: bool = False


def _get_tailscale() -> Optional[str]:
    """Return the cached Tailscale binary path (found once at first call)."""
    global _tailscale_path_cache, _tailscale_path_searched
    if not _tailscale_path_searched:
        _tailscale_path_cache = _find_tailscale()
        _tailscale_path_searched = True
    return _tailscale_path_cache


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_tailscale_status() -> str:
    """Get Tailscale VPN status including connected peers and this device's IP."""
    ts = _get_tailscale()
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
    """Summarize external internet + LAN connectivity and Tailscale status (parallel checks)."""

    nas_ip = os.getenv("NAS_IP", _cfg.nas_ip)
    ts = _get_tailscale()

    async def _check_dns() -> str:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, "google.com", 80),
                timeout=3,
            )
            return "✅ DNS Resolution"
        except Exception as exc:
            log.debug("DNS resolution check failed: %s", exc)
            return "❌ DNS Resolution failed"

    async def _check_health() -> str:
        try:
            session = _get_session()
            async with session.get(f"http://{HOST}:8765/health") as resp:
                return f"{'✅' if resp.status == 200 else '❌'} OpenClaw health endpoint (:{8765})"
        except Exception as exc:
            log.debug("Health endpoint check failed: %s", exc)
            return f"❌ OpenClaw health endpoint (:{8765})"

    async def _check_tailscale() -> str:
        if not ts:
            return "⚠️ Tailscale not installed"
        rc3, ip_out, _ = await _run([ts, "ip", "-4"])
        if rc3 == 0 and ip_out.strip():
            return f"✅ Tailscale IP: `{ip_out.strip()}`"
        return "⚠️ Tailscale not connected"

    # Run all 5 checks in parallel
    (
        (rc_nas, _, _),
        (rc_inet, _, _),
        dns_result,
        ts_result,
        health_result,
    ) = await asyncio.gather(
        _run(["ping", "-c", "1", "-W", "1", nas_ip]),
        _run(["ping", "-c", "1", "-W", "2", PING_TEST_HOST]),
        _check_dns(),
        _check_tailscale(),
        _check_health(),
    )

    results = [
        f"{'✅' if rc_nas == 0 else '❌'} LAN (NAS {nas_ip})",
        f"{'✅' if rc_inet == 0 else '❌'} Internet ({PING_TEST_HOST})",
        dns_result,
        ts_result,
        health_result,
        f"🖥️ Host: `{HOST}`",
    ]
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
        await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, "google.com", 80),
            timeout=3,
        )
        dns_ms = (time.monotonic() - start) * 1000
        results.append(f"**DNS Latency**: {dns_ms:.0f} ms")
    except Exception as exc:
        log.debug("DNS lookup failed: %s", exc)
        results.append("❌ DNS lookup failed")

    return "\n".join(results) if results else "❌ Speed test unavailable"
