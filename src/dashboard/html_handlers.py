"""HTML page handlers for the dashboard."""

from pathlib import Path

from aiohttp import web

from .helpers import (
    DASHBOARD_HTML,
    GUIDE_HTML,
    ONBOARDING_HTML,
    TERMINAL_HTML,
    build_openclaw_cli_installer,
    load_openclaw_cli_source,
    load_openclaw_cli_support_source,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WINDOWS_INSTALLER_SCRIPT = _REPO_ROOT / "scripts" / "install_openclaw_cli_windows.ps1"


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page."""
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def guide_handler(request: web.Request) -> web.Response:
    """Serve the guide / tutorial HTML page."""
    return web.Response(text=GUIDE_HTML, content_type="text/html")


async def terminal_handler(request: web.Request) -> web.Response:
    """Serve the terminal CLI cheat sheet page."""
    return web.Response(text=TERMINAL_HTML, content_type="text/html")


async def onboarding_handler(request: web.Request) -> web.Response:
    """Serve the new-user onboarding page."""
    return web.Response(text=ONBOARDING_HTML, content_type="text/html")


async def openclaw_cli_download_handler(request: web.Request) -> web.Response:
    """Serve the standalone OpenClaw CLI Python source."""
    try:
        source = load_openclaw_cli_source()
    except OSError as exc:
        return web.Response(text=f"OpenClaw CLI source unavailable: {exc}", status=404, content_type="text/plain")
    return web.Response(
        text=source,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="openclaw_cli.py"'},
    )


async def openclaw_cli_support_download_handler(request: web.Request) -> web.Response:
    """Serve one of the support modules required by the standalone OpenClaw CLI."""
    name = str(request.match_info.get("name", "")).strip()
    try:
        source = load_openclaw_cli_support_source(name)
    except OSError as exc:
        return web.Response(text=f"OpenClaw CLI support source unavailable: {exc}", status=404, content_type="text/plain")
    return web.Response(
        text=source,
        content_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


async def openclaw_cli_installer_handler(request: web.Request) -> web.Response:
    """Serve a repo-free shell installer for the standalone OpenClaw CLI."""
    default_base_url = f"{request.scheme}://{request.host}"
    installer = build_openclaw_cli_installer(default_base_url)
    return web.Response(
        text=installer,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="openclaw-cli-installer.sh"'},
    )


async def openclaw_cli_remote_installer_handler(request: web.Request) -> web.Response:
    """Serve a repo-free shell installer with Remote Login enablement preselected."""
    default_base_url = f"{request.scheme}://{request.host}"
    installer = build_openclaw_cli_installer(default_base_url, enable_remote_login_default=True)
    return web.Response(
        text=installer,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="openclaw-cli-remote-installer.sh"'},
    )


async def openclaw_cli_windows_installer_handler(request: web.Request) -> web.Response:
    """Serve the PowerShell installer for the standalone OpenClaw CLI on Windows."""
    script = _WINDOWS_INSTALLER_SCRIPT.read_text(encoding="utf-8")
    return web.Response(
        text=script,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="install_openclaw_cli.ps1"'},
    )
