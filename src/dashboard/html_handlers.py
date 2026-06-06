"""HTML page handlers for the dashboard."""

import html
from pathlib import Path

from aiohttp import web

from .helpers import (
    DASHBOARD_HTML,
    GUIDE_HTML,
    ONBOARDING_HTML,
    PARENTS_GUIDE_HTML,
    TERMINAL_HTML,
    WEBUI_GUIDE_HTML,
    build_openclaw_cli_installer,
    load_openclaw_cli_source,
    load_openclaw_cli_support_source,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WINDOWS_INSTALLER_SCRIPT = _REPO_ROOT / "scripts" / "install_openclaw_cli_windows.ps1"

_TOKEN_INJECTION_MARKER = "</head>"
_TOKEN_SCRIPT_TEMPLATE = '\n  <script>window.OPENCLAW_API_ACTION_TOKEN = "{token}";</script>\n</head>'


def _inject_api_token(html_text: str, token: str) -> str:
    """Inject the API action token into the page so the JS auth helper can pick it up."""
    if not token:
        return html_text
    escaped = html.escape(token, quote=True)
    snippet = _TOKEN_SCRIPT_TEMPLATE.format(token=escaped)
    return html_text.replace(_TOKEN_INJECTION_MARKER, snippet, 1)


async def dashboard_handler(request: web.Request) -> web.Response:
    """Serve the dashboard HTML page, injecting the API action token when configured."""
    from config import cfg  # local import to avoid circular dep at module load

    body = _inject_api_token(DASHBOARD_HTML, cfg.dashboard_api_token)
    return web.Response(text=body, content_type="text/html")


async def guide_handler(request: web.Request) -> web.Response:
    """Serve the guide / tutorial HTML page."""
    return web.Response(text=GUIDE_HTML, content_type="text/html")


async def terminal_handler(request: web.Request) -> web.Response:
    """Serve the terminal CLI cheat sheet page."""
    return web.Response(text=TERMINAL_HTML, content_type="text/html")


async def onboarding_handler(request: web.Request) -> web.Response:
    """Serve the new-user onboarding page."""
    return web.Response(text=ONBOARDING_HTML, content_type="text/html")


async def webui_guide_handler(request: web.Request) -> web.Response:
    """Serve the Open WebUI vs Gemini comparison guide page."""
    return web.Response(text=WEBUI_GUIDE_HTML, content_type="text/html")


async def parents_guide_handler(request: web.Request) -> web.Response:
    """Serve the family/parents detailed guide page."""
    return web.Response(text=PARENTS_GUIDE_HTML, content_type="text/html")


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
        return web.Response(
            text=f"OpenClaw CLI support source unavailable: {exc}", status=404, content_type="text/plain"
        )
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


async def hermes_installer_handler(request: web.Request) -> web.Response:
    """Serve a shell installer that installs Hermes agent with Copilot provider pre-configured."""
    from .helpers import build_hermes_installer

    base_url = f"{request.scheme}://{request.host}"
    script = build_hermes_installer(base_url)
    return web.Response(
        text=script,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="install-hermes.sh"'},
    )


async def openclaw_cli_windows_installer_handler(request: web.Request) -> web.Response:
    """Serve the PowerShell installer for the standalone OpenClaw CLI on Windows."""
    script = _WINDOWS_INSTALLER_SCRIPT.read_text(encoding="utf-8")
    return web.Response(
        text=script,
        content_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="install_openclaw_cli.ps1"'},
    )


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenClaw Dashboard - Login</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
            width: 100%;
            max-width: 400px;
        }
        .login-container h1 {
            margin-bottom: 30px;
            text-align: center;
            color: #333;
            font-size: 28px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }
        input[type="text"],
        input[type="password"] {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        input[type="text"]:focus,
        input[type="password"]:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        button {
            width: 100%;
            padding: 10px 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 4px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.3s;
        }
        button:hover {
            opacity: 0.9;
        }
        button:active {
            opacity: 0.8;
        }
        .error {
            color: #d32f2f;
            font-size: 14px;
            margin-bottom: 20px;
            padding: 10px;
            background: #ffebee;
            border-radius: 4px;
            display: none;
        }
        .spinner {
            display: none;
            width: 16px;
            height: 16px;
            border: 2px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
            margin-right: 8px;
        }
        button:disabled {
            opacity: 0.7;
            cursor: not-allowed;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .button-content {
            display: flex;
            align-items: center;
            justify-content: center;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🤖 OpenClaw</h1>
        <div class="error" id="errorBox"></div>
        <form id="loginForm">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit" id="loginBtn">
                <div class="button-content">
                    <div class="spinner" id="spinner"></div>
                    <span id="btnText">Login</span>
                </div>
            </button>
        </form>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorBox = document.getElementById('errorBox');
            const loginBtn = document.getElementById('loginBtn');
            const spinner = document.getElementById('spinner');
            const btnText = document.getElementById('btnText');

            errorBox.style.display = 'none';
            loginBtn.disabled = true;
            spinner.style.display = 'inline-block';
            btnText.textContent = 'Logging in...';

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });

                if (response.ok) {
                    const params = new URLSearchParams(window.location.search);
                    const target = params.get('from') || '/dashboard';
                    window.location.href = target.startsWith('/') && !target.startsWith('//') ? target : '/dashboard';
                } else {
                    const data = await response.json();
                    errorBox.textContent = data.message || 'Login failed';
                    errorBox.style.display = 'block';
                }
            } catch (error) {
                errorBox.textContent = 'Network error: ' + error.message;
                errorBox.style.display = 'block';
            } finally {
                loginBtn.disabled = false;
                spinner.style.display = 'none';
                btnText.textContent = 'Login';
            }
        });

        // Clear error on input
        document.getElementById('username').addEventListener('input', () => {
            document.getElementById('errorBox').style.display = 'none';
        });
        document.getElementById('password').addEventListener('input', () => {
            document.getElementById('errorBox').style.display = 'none';
        });
    </script>
</body>
</html>"""


async def login_handler(request: web.Request) -> web.Response:
    """Serve the login page."""
    return web.Response(text=_LOGIN_HTML, content_type="text/html")
