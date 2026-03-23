"""
OpenClaw Synology NAS Skill — Phase 6
Query Synology DSM via the REST API for storage health, backup status,
system alerts, and disk SMART data.

Requires in .env:
  NAS_URL            - DSM base URL, default https://192.168.1.8:5001
  NAS_USER           - DSM admin/user account
  NAS_PASSWORD       - DSM account password
  NAS_VERIFY_SSL     - Set to "true" if DSM has a valid cert (default: false)
"""

import asyncio
import datetime
import logging
import os
import ssl

import aiohttp

log = logging.getLogger("openclaw.nas")

NAS_URL = os.getenv("NAS_URL", "https://192.168.1.8:5001")
NAS_USER = os.getenv("NAS_USER", "")
NAS_PASSWORD = os.getenv("NAS_PASSWORD", "")

# DSM typically uses a self-signed cert; NAS_VERIFY_SSL=true if you have a valid cert
_VERIFY_SSL = os.getenv("NAS_VERIFY_SSL", "false").lower() == "true"
_SSL_CTX: ssl.SSLContext | bool = ssl.create_default_context() if _VERIFY_SSL else False


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# DSM session helpers
# ---------------------------------------------------------------------------


async def _login(session: aiohttp.ClientSession) -> str | None:
    """Authenticate to DSM and return a session ID (SID), or None on failure."""
    params = {
        "api": "SYNO.API.Auth",
        "version": "3",
        "method": "login",
        "account": NAS_USER,
        "passwd": NAS_PASSWORD,
        "session": "openclaw",
        "format": "sid",
    }
    try:
        async with session.get(
            f"{NAS_URL}/webapi/auth.cgi", params=params, ssl=_SSL_CTX
        ) as resp:
            data = await resp.json(content_type=None)
            if data.get("success"):
                return data["data"]["sid"]
            code = data.get("error", {}).get("code", "unknown")
            log.warning("DSM login failed, error code: %s", code)
            return None
    except Exception as e:
        log.error("DSM login error: %s", e)
        return None


async def _logout(session: aiohttp.ClientSession, sid: str) -> None:
    params = {
        "api": "SYNO.API.Auth",
        "version": "1",
        "method": "logout",
        "_sid": sid,
    }
    try:
        async with session.get(
            f"{NAS_URL}/webapi/auth.cgi", params=params, ssl=_SSL_CTX
        ):
            pass
    except Exception:
        pass


async def _dsm(
    api: str, version: int, method: str, extra: dict | None = None
) -> dict:
    """Make a single DSM API call with automatic auth. Returns response dict."""
    if not NAS_USER or not NAS_PASSWORD:
        return {"success": False, "_err": "NAS_USER / NAS_PASSWORD not configured."}

    connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        connector=connector,
    ) as session:
        sid = await _login(session)
        if not sid:
            return {"success": False, "_err": "DSM authentication failed. Check NAS_USER / NAS_PASSWORD."}

        params: dict = {
            "api": api,
            "version": str(version),
            "method": method,
            "_sid": sid,
        }
        if extra:
            params.update(extra)

        try:
            async with session.get(
                f"{NAS_URL}/webapi/entry.cgi", params=params, ssl=_SSL_CTX
            ) as resp:
                result = await resp.json(content_type=None)
        except Exception as e:
            result = {"success": False, "_err": str(e)}
        finally:
            await _logout(session, sid)

        return result


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_nas_storage_health() -> str:
    """Get Synology NAS storage pool and volume health status."""
    vol_data, disk_data = await asyncio.gather(
        _dsm("SYNO.Core.Storage.Volume", 1, "list"),
        _dsm("SYNO.Core.Storage.Disk", 1, "list"),
    )

    if not vol_data.get("success"):
        err = vol_data.get("_err") or vol_data.get("error", {}).get("code", "unknown")
        return f"❌ Storage query failed: {err}"

    lines = ["**NAS Storage Health**"]

    volumes = vol_data.get("data", {}).get("volumes", [])
    for vol in volumes:
        status = vol.get("status", "unknown")
        icon = "✅" if status == "normal" else "⚠️" if status == "degraded" else "❌"
        fs_type = vol.get("fs_type", "")
        size_bytes = vol.get("size", {})
        total_gb = round(size_bytes.get("total", 0) / (1024 ** 3), 1)
        used_gb = round(size_bytes.get("used", 0) / (1024 ** 3), 1)
        pct = round(used_gb / total_gb * 100, 1) if total_gb > 0 else 0
        name = vol.get("volume_path") or vol.get("id", "?")
        lines.append(
            f"{icon} **{name}** ({fs_type}): {used_gb}/{total_gb} GB ({pct}%) — {status}"
        )

    if disk_data.get("success"):
        disks = disk_data.get("data", {}).get("disks", [])
        if disks:
            lines.append("")
            lines.append("**Disks**")
            for disk in disks:
                d_status = disk.get("status", "unknown")
                d_icon = (
                    "✅" if d_status == "normal"
                    else "⚠️" if d_status == "warning"
                    else "❌"
                )
                name = disk.get("name", "?")
                model = disk.get("model", "")
                temp = disk.get("temp", "?")
                lines.append(f"{d_icon} **{name}** {model} — {d_status} ({temp}°C)")

    if len(lines) == 1:
        lines.append("⚠️ No volume data returned. Check DSM API access.")

    return _truncate("\n".join(lines))


async def get_backup_status() -> str:
    """Get Synology Hyper Backup task status and last run time."""
    data = await _dsm("SYNO.Backup.Task", 1, "list")

    if not data.get("success"):
        err = data.get("_err") or data.get("error", {}).get("code", "unknown")
        if str(err) in ("119", "105"):
            return (
                "⚠️ Backup info unavailable — Hyper Backup may not be installed "
                "or API access is restricted for this account."
            )
        return f"❌ Backup query failed (DSM error {err})"

    tasks = data.get("data", {}).get("task_list", [])
    if not tasks:
        return "⚠️ No Hyper Backup tasks configured."

    STATUS_ICON = {
        "finish": "✅",
        "running": "🔄",
        "error": "❌",
        "waiting": "⏳",
        "suspend": "⏸️",
    }

    lines = ["**Hyper Backup Tasks**"]
    for task in tasks:
        name = task.get("name", "Unknown Task")
        status = task.get("status", "unknown")
        icon = STATUS_ICON.get(status, "❓")
        last_bkp = task.get("last_bkp_time", 0)
        if last_bkp:
            ts = datetime.datetime.fromtimestamp(last_bkp).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{icon} **{name}** — {status} (last: {ts})")
        else:
            lines.append(f"{icon} **{name}** — {status} (never run)")

    return "\n".join(lines)


async def get_nas_alerts() -> str:
    """Get Synology DSM system health status (fans, temperature, power, disks)."""
    data = await _dsm("SYNO.Core.System.Status", 1, "get")

    if not data.get("success"):
        err = data.get("_err") or data.get("error", {}).get("code", "unknown")
        return f"❌ System status query failed: {err}"

    d = data.get("data", {})
    lines = ["**NAS System Status**"]

    # Fan status lists
    for key, label in [
        ("cpu_fan_list", "CPU Fan"),
        ("sys_fan_list", "System Fan"),
    ]:
        items = d.get(key, [])
        for item in items:
            status = item.get("status", "?")
            icon = "✅" if status == "normal" else "⚠️" if status == "warning" else "❌"
            lines.append(f"{icon} **{label}**: {status}")

    # System temperature
    temp = d.get("temperature")
    if temp is not None:
        icon = "✅" if temp < 70 else "⚠️" if temp < 80 else "❌"
        lines.append(f"{icon} **System Temp**: {temp}°C")

    # Disk warnings
    disk_warnings = d.get("disk_warning_list", [])
    for w in disk_warnings:
        lines.append(f"⚠️ **Disk Warning**: {w}")

    # Power status
    power = d.get("pwr_status")
    if power:
        icon = "✅" if power == "normal" else "❌"
        lines.append(f"{icon} **Power**: {power}")

    if len(lines) == 1:
        lines.append("✅ No issues detected (all systems nominal).")

    return "\n".join(lines)


async def get_disk_smart_status() -> str:
    """Get SMART health status for all physical disks in the NAS."""
    data = await _dsm("SYNO.Core.Storage.Disk", 1, "list")

    if not data.get("success"):
        err = data.get("_err") or data.get("error", {}).get("code", "unknown")
        return f"❌ Disk query failed: {err}"

    disks = data.get("data", {}).get("disks", [])
    if not disks:
        return "⚠️ No disks found."

    lines = ["**Disk SMART Status**"]
    for disk in disks:
        name = disk.get("name", "?")
        model = disk.get("model", "unknown model")
        smart_status = (
            disk.get("smart_status") or disk.get("health", "unknown")
        )
        temp = disk.get("temp", "?")
        size_total = disk.get("size_total", 0)
        size_gb = round(size_total / (1024 ** 3), 1) if size_total else "?"
        icon = (
            "✅" if smart_status in ("normal", "good", "pass")
            else "⚠️" if smart_status == "warning"
            else "❌"
        )
        lines.append(
            f"{icon} **{name}** {model} — SMART: {smart_status} | {temp}°C | {size_gb} GB"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

NAS_SKILLS = {
    "get_nas_storage_health": get_nas_storage_health,
    "get_backup_status": get_backup_status,
    "get_nas_alerts": get_nas_alerts,
    "get_disk_smart_status": get_disk_smart_status,
}
