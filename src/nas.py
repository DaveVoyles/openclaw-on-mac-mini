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
import json as _json
import logging
import os
import ssl
import time

import aiohttp

from config import cfg

log = logging.getLogger("openclaw.nas")

NAS_URL = cfg.nas_url
NAS_USER = cfg.nas_user
NAS_PASSWORD = cfg.nas_password

# DSM typically uses a self-signed cert; NAS_VERIFY_SSL=true if you have a valid cert
_VERIFY_SSL = cfg.nas_verify_ssl
_SSL_CTX: ssl.SSLContext | bool = ssl.create_default_context() if _VERIFY_SSL else False


def _truncate(text: str, limit: int = 1900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# DSM session helpers
# ---------------------------------------------------------------------------

_nas_session: aiohttp.ClientSession | None = None

# Cached SID with TTL — avoids login/logout on every API call.
# DSM sessions last ~20 min by default; we refresh at 10 min to be safe.
_SID_TTL = 600  # seconds
_cached_sid: str | None = None
_sid_obtained_at: float = 0.0
_sid_lock: asyncio.Lock | None = None  # created lazily inside event loop


def _get_sid_lock() -> asyncio.Lock:
    global _sid_lock
    if _sid_lock is None:
        _sid_lock = asyncio.Lock()
    return _sid_lock


async def _get_nas_session() -> aiohttp.ClientSession:
    global _nas_session
    if _nas_session is None or _nas_session.closed:
        connector = aiohttp.TCPConnector(
            limit=10,
            limit_per_host=5,
            ssl=_SSL_CTX,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
        )
        _nas_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            connector=connector,
        )
    return _nas_session


async def close_session() -> None:
    """Close the shared NAS session. Call on bot shutdown."""
    global _nas_session, _cached_sid, _sid_obtained_at
    # Logout the cached SID before closing
    if _cached_sid and _nas_session and not _nas_session.closed:
        await _raw_logout(_nas_session, _cached_sid)
    _cached_sid = None
    _sid_obtained_at = 0.0
    if _nas_session and not _nas_session.closed:
        await _nas_session.close()
        _nas_session = None


async def _raw_login(session: aiohttp.ClientSession) -> str | None:
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
        async with session.post(
            f"{NAS_URL}/webapi/auth.cgi", data=params, ssl=_SSL_CTX
        ) as resp:
            data = await resp.json(content_type=None)
            if data.get("success"):
                sid = data.get("data", {}).get("sid")
                if not sid:
                    log.warning("DSM login succeeded but no SID returned")
                    return None
                return sid
            code = data.get("error", {}).get("code", "unknown")
            log.warning("DSM login failed, error code: %s", code)
            return None
    except Exception as e:
        log.error("DSM login error: %s", e)
        return None


async def _raw_logout(session: aiohttp.ClientSession, sid: str) -> None:
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


async def _get_sid(session: aiohttp.ClientSession) -> str | None:
    """Return a cached SID, refreshing only when expired or invalid."""
    global _cached_sid, _sid_obtained_at
    lock = _get_sid_lock()
    async with lock:
        now = time.monotonic()
        if _cached_sid and (now - _sid_obtained_at) < _SID_TTL:
            return _cached_sid
        # Existing SID expired — get a fresh one (no need to logout, DSM auto-expires)
        sid = await _raw_login(session)
        if sid:
            _cached_sid = sid
            _sid_obtained_at = now
        else:
            _cached_sid = None
            _sid_obtained_at = 0.0
        return sid


async def _invalidate_sid() -> None:
    """Force re-login on next call (e.g. after an auth error)."""
    global _cached_sid, _sid_obtained_at
    lock = _get_sid_lock()
    async with lock:
        _cached_sid = None
        _sid_obtained_at = 0.0


_DSM_MAX_RETRIES = 3
_DSM_BACKOFF_BASE = 1.5  # seconds


async def _dsm(
    api: str, version: int, method: str, extra: dict | None = None
) -> dict:
    """Make a single DSM API call with automatic auth and retry. Returns response dict."""
    if not NAS_USER or not NAS_PASSWORD:
        return {"success": False, "_err": "NAS_USER / NAS_PASSWORD not configured."}

    session = await _get_nas_session()

    last_err = "unknown"
    for attempt in range(_DSM_MAX_RETRIES):
        sid = await _get_sid(session)
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

            # Check for auth errors (DSM error code 105 = invalid SID, 119 = no perm)
            if not result.get("success"):
                err_code = result.get("error", {}).get("code")
                if err_code == 105:  # SID expired/invalid
                    log.info("DSM SID expired for %s, re-authenticating (attempt %d)", api, attempt + 1)
                    await _invalidate_sid()
                    continue
            return result

        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            last_err = str(e)
            log.warning(
                "DSM request %s failed (attempt %d/%d): %s",
                api, attempt + 1, _DSM_MAX_RETRIES, last_err,
            )
            # Invalidate SID in case the connection dropped mid-session
            await _invalidate_sid()
            if attempt < _DSM_MAX_RETRIES - 1:
                await asyncio.sleep(_DSM_BACKOFF_BASE * (attempt + 1))

    return {"success": False, "_err": f"Failed after {_DSM_MAX_RETRIES} retries: {last_err}"}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def get_nas_storage_health() -> str:
    """Get Synology NAS system utilization: volumes, disks, CPU, memory."""
    util_data, sys_data = await asyncio.gather(
        _dsm("SYNO.Core.System.Utilization", 1, "get"),
        _dsm("SYNO.Core.System", 1, "info"),
    )

    if not util_data.get("success"):
        err = util_data.get("_err") or util_data.get("error", {}).get("code", "unknown")
        return f"❌ NAS utilization query failed: {err}"

    d = util_data.get("data", {})
    lines = ["**NAS System Overview**"]

    # System info
    if sys_data.get("success"):
        si = sys_data.get("data", {})
        model = si.get("model", "")
        version = si.get("firmware_ver", "")
        temp = si.get("sys_temp")
        uptime = si.get("up_time", "")
        temp_icon = "✅" if (temp or 0) < 70 else "⚠️"
        lines.append(f"📦 **{model}** — {version} | uptime {uptime}")
        if temp is not None:
            lines.append(f"{temp_icon} **System Temp**: {temp}°C")

    # Volume I/O utilization
    space = d.get("space", {})
    volumes = space.get("volume", [])
    if volumes:
        lines.append("")
        lines.append("**Volumes (I/O utilization)**")
        for vol in volumes:
            name = vol.get("display_name") or vol.get("device", "?")
            util_pct = vol.get("utilization", 0)
            util_icon = "✅" if util_pct < 80 else "⚠️"
            lines.append(f"{util_icon} **{name}**: {util_pct}% I/O utilization")

    # Disk I/O utilization (internal only, skip USB)
    disk_info = d.get("disk", {})
    disks = [dk for dk in disk_info.get("disk", []) if dk.get("type") == "internal"]
    if disks:
        lines.append("")
        lines.append("**Internal Drives (I/O utilization)**")
        for disk in disks:
            name = disk.get("display_name") or disk.get("device", "?")
            util_pct = disk.get("utilization", 0)
            util_icon = "✅" if util_pct < 80 else "⚠️"
            lines.append(f"{util_icon} **{name}**: {util_pct}% busy")

    # Memory
    mem = d.get("memory", {})
    if mem:
        total_mb = round(mem.get("total_real", 0) / 1024, 0)
        used_pct = mem.get("real_usage", 0)
        mem_icon = "✅" if used_pct < 85 else "⚠️"
        lines.append("")
        lines.append(
            f"{mem_icon} **Memory**: {used_pct}% used ({total_mb:.0f} MB total)"
        )

    # CPU
    cpu = d.get("cpu", {})
    if cpu:
        load = cpu.get("1min_load", 0)
        cpu_icon = "✅" if load < 80 else "⚠️"
        lines.append(f"{cpu_icon} **CPU 1-min load**: {load}%")

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
    """Get physical disk activity and utilization for all internal drives in the NAS."""
    data = await _dsm("SYNO.Core.System.Utilization", 1, "get")

    if not data.get("success"):
        err = data.get("_err") or data.get("error", {}).get("code", "unknown")
        return f"❌ Disk query failed: {err}"

    disk_info = data.get("data", {}).get("disk", {})
    all_disks = disk_info.get("disk", [])
    if not all_disks:
        return "⚠️ No disk data returned."

    internal = [dk for dk in all_disks if dk.get("type") == "internal"]
    usb = [dk for dk in all_disks if dk.get("type") == "usb"]

    lines = ["**Disk Activity Status**"]
    lines.append("*(SMART health data requires DSM admin access — showing I/O utilization)*")
    lines.append("")

    if internal:
        lines.append("**Internal Drives**")
        for disk in internal:
            name = disk.get("display_name") or disk.get("device", "?")
            util_pct = disk.get("utilization", 0)
            read_mbps = round(disk.get("read_byte", 0) / (1024 * 1024), 2)
            write_mbps = round(disk.get("write_byte", 0) / (1024 * 1024), 2)
            util_icon = "✅" if util_pct < 80 else "⚠️"
            lines.append(
                f"{util_icon} **{name}**: {util_pct}% busy | "
                f"↓{read_mbps} MB/s ↑{write_mbps} MB/s"
            )

    active_usb = [dk for dk in usb if dk.get("utilization", 0) > 0 or dk.get("read_byte", 0) > 0]
    if active_usb:
        lines.append("")
        lines.append("**USB Devices (active)**")
        for disk in active_usb:
            name = disk.get("display_name") or disk.get("device", "?")
            util_pct = disk.get("utilization", 0)
            lines.append(f"📀 **{name}**: {util_pct}% busy")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FileStation — read operations
# ---------------------------------------------------------------------------

async def nas_list_folder(path: str = "/Misc/audiobooks", pattern: str = "") -> str:
    """
    List contents of a folder on the Synology NAS via FileStation.

    Args:
        path: Folder path to list. Use share-relative paths like '/Misc/audiobooks',
              '/PlexMediaServer/Movies', etc. Do NOT prefix with /volume1.
        pattern: Optional search filter — only return items whose name contains
                 this string (case-insensitive). Leave empty to list all.
    """
    if not NAS_USER or not NAS_PASSWORD:
        return "❌ NAS credentials not configured (NAS_USER / NAS_PASSWORD)."

    import posixpath
    normed = posixpath.normpath(path)
    if normed.startswith("..") or "/../" in path or path.endswith("/.."):
        return "❌ Invalid path: directory traversal is not allowed."

    extra: dict = {
        "folder_path": path,
        "sort_by": "name",
        "sort_direction": "asc",
        "additional": '["size","type"]',
    }
    if pattern:
        extra["pattern"] = pattern

    result = await _dsm("SYNO.FileStation.List", 2, "list", extra)
    if not result.get("success"):
        err = result.get("_err") or result.get("error", {}).get("code", "unknown")
        return f"❌ Could not list `{path}`: {err}"

    files = result.get("data", {}).get("files", [])
    if not files:
        return f"📂 `{path}` is empty" + (f" (filter: '{pattern}')" if pattern else "")

    # Filter client-side if pattern provided (FileStation pattern support is limited)
    if pattern:
        pat_lower = pattern.lower()
        files = [f for f in files if pat_lower in f.get("name", "").lower()]
        if not files:
            return f"📂 No items matching '{pattern}' in `{path}`"

    total = len(files)
    lines = [f"📂 **{path}** — {total} item{'s' if total != 1 else ''}"]
    if pattern:
        lines[0] += f" matching '{pattern}'"

    # Show up to 50 items to avoid Discord message limits
    for f in files[:50]:
        name = f.get("name", "?")
        is_dir = f.get("isdir", False)
        icon = "📁" if is_dir else "📄"
        size = f.get("additional", {}).get("size", 0)
        if is_dir:
            lines.append(f"  {icon} {name}/")
        else:
            size_mb = size / (1024 * 1024)
            lines.append(f"  {icon} {name} ({size_mb:.1f} MB)")

    if total > 50:
        lines.append(f"  … and {total - 50} more items")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FileStation — write operations
# ---------------------------------------------------------------------------

async def nas_create_folder(path: str) -> str:
    """
    Create a folder on the Synology NAS via FileStation.

    Args:
        path: Full folder path to create, e.g. '/volume1/documents/reports'.
    """
    if not NAS_USER or not NAS_PASSWORD:
        return "❌ NAS credentials not configured (NAS_USER / NAS_PASSWORD)."

    import posixpath
    normed = posixpath.normpath(path)
    if normed.startswith("..") or "/../" in path or path.endswith("/.."):
        return "❌ Invalid path: directory traversal is not allowed."
    if not normed.startswith("/"):
        return "❌ Invalid path: must be an absolute path (e.g. '/volume1/folder')."

    result = await _dsm(
        "SYNO.FileStation.CreateFolder",
        2,
        "create",
        {
            "folder_path": _json.dumps([path.rsplit("/", 1)[0]]),
            "name": _json.dumps([path.rsplit("/", 1)[-1]]),
            "force_parent": "true",
        },
    )
    if not result.get("success"):
        err = result.get("_err") or result.get("error", {}).get("code", "unknown")
        return f"❌ Could not create folder `{path}`: {err}"
    return f"✅ Folder created: `{path}`"


async def nas_write_file(
    content: str,
    remote_folder: str = "/volume1/documents",
    filename: str = "openclaw_output.md",
) -> str:
    """
    Write a text or markdown file to the Synology NAS via FileStation upload.

    Args:
        content: Text content to write.
        remote_folder: Destination folder path on the NAS, e.g. '/volume1/documents'.
        filename: Name for the file, e.g. 'research_report.md'.
    """
    if not NAS_USER or not NAS_PASSWORD:
        return "❌ NAS credentials not configured (NAS_USER / NAS_PASSWORD)."

    import posixpath
    normed = posixpath.normpath(remote_folder)
    if normed.startswith("..") or "/../" in remote_folder or remote_folder.endswith("/.."):
        return "❌ Invalid path: directory traversal is not allowed."
    if not normed.startswith("/"):
        return "❌ Invalid path: must be an absolute path (e.g. '/volume1/documents')."
    if ".." in filename or "/" in filename:
        return "❌ Invalid filename: must not contain '..' or '/'."

    session = await _get_nas_session()
    sid = await _get_sid(session)
    if not sid:
        return "❌ DSM authentication failed. Check NAS_USER / NAS_PASSWORD."

    try:
        data = aiohttp.FormData()
        data.add_field("api", "SYNO.FileStation.Upload")
        data.add_field("version", "2")
        data.add_field("method", "upload")
        data.add_field("_sid", sid)
        data.add_field("path", remote_folder)
        data.add_field("create_parents", "true")
        data.add_field("overwrite", "true")
        data.add_field(
            "file",
            content.encode("utf-8"),
            filename=filename,
            content_type="text/plain",
        )
        async with session.post(
            f"{NAS_URL}/webapi/entry.cgi",
            data=data,
            ssl=_SSL_CTX,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            result = await resp.json(content_type=None)
    except Exception as e:
        result = {"success": False, "_err": str(e)}

    if not result.get("success"):
        err = result.get("_err") or result.get("error", {}).get("code", "unknown")
        return f"❌ File upload failed: {err}"

    return (
        f"✅ Saved `{filename}` to NAS at `{remote_folder}/{filename}` "
        f"({len(content.encode())} bytes)"
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

NAS_SKILLS = {
    "get_nas_storage_health": get_nas_storage_health,
    "get_backup_status": get_backup_status,
    "get_nas_alerts": get_nas_alerts,
    "get_disk_smart_status": get_disk_smart_status,
    "nas_list_folder": nas_list_folder,
    "nas_create_folder": nas_create_folder,
    "nas_write_file": nas_write_file,
}
