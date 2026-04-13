"""Terminal client for OpenClaw's authenticated ask API."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from openclaw_cli_actions import (
    format_shell_result,
    infer_command_risk,
    infer_file_edit_risk,
    preview_file_result,
    replace_text_in_file,
    request_cli_approval,
    risk_level_from_name,
    run_shell_command,
    write_text_file,
)
from openclaw_cli_sessions import (
    SessionSummary,
    append_event,
    build_workspace_signature,
    collect_workspace_context,
    create_routed_action_checkpoint,
    create_session,
    export_session,
    extract_prompt_targets,
    list_saved_outputs,
    list_sessions,
    load_conversation_history,
    load_events,
    load_saved_output_preview,
    load_session,
    load_watch_state,
    recent_output_context,
    require_session,
    restore_last_routed_action_checkpoint,
    save_output,
    save_watch_state,
    update_session,
)

try:
    import readline
except ImportError:  # pragma: no cover - platform-dependent
    readline = None

# ---------------------------------------------------------------------------
# Color / rich support — graceful fallback when not in a TTY or rich absent
# ---------------------------------------------------------------------------
try:
    from rich.console import Console as _RichConsole
    from rich.markdown import Markdown as _RichMarkdown
    from rich.panel import Panel as _RichPanel
    from rich.table import Table as _RichTable
    from rich.text import Text as _RichText

    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_ERR = _RichConsole(stderr=True, highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

_IS_TTY = sys.stdout.isatty()

# Cached latest PyPI version set by the background update-check thread.
_latest_version: str | None = None
# Set to True by the background update-check thread when standalone file hashes differ from server.
_standalone_needs_update: bool = False


def _c(code: str) -> str:
    """Return an ANSI escape code only when stdout is a real terminal."""
    return code if _IS_TTY else ""


# ANSI palette
_R   = _c("\033[0m")     # reset
_B   = _c("\033[1m")     # bold
_DM  = _c("\033[2m")     # dim
_CY  = _c("\033[36m")    # cyan
_GR  = _c("\033[32m")    # green
_YE  = _c("\033[33m")    # yellow
_RE  = _c("\033[31m")    # red
_MA  = _c("\033[35m")    # magenta
_BCY = _c("\033[1;36m")  # bold cyan
_BGR = _c("\033[1;32m")  # bold green
_BYE = _c("\033[1;33m")  # bold yellow
_BRE = _c("\033[1;31m")  # bold red

DEFAULT_BASE_URL = "http://localhost:8765"
DEFAULT_MODEL = "auto"
DEFAULT_TIMEOUT_SECONDS = 120
KEYCHAIN_SERVICE = "OpenClaw CLI"
DEFAULT_VERSION = "0.6.0"
HISTORY_FILE = Path.home() / ".openclaw_history"
HISTORY_LIMIT = 500
TOKEN_ENV_VARS = "OPENCLAW_TOKEN or DASHBOARD_API_TOKEN"
AUTH_FILE_NAME = "token"
WATCH_PROGRESS_LOG_LIMIT = 25
WATCH_RETRY_LIMIT = 3
WATCH_RETRY_MAX_DELAY_SECONDS = 8
CONTEXT_PREVIEW_MAX_CHARS = 5_000
OUTPUT_LIST_LIMIT = 10
OUTPUT_PREVIEW_MAX_CHARS = 4_000
REPL_ROUTE_AUTO_THRESHOLD = 0.74
REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT = 80
REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT = 72


# ---------------------------------------------------------------------------
# Shared display helpers
# ---------------------------------------------------------------------------

def _status_emoji(status: str) -> str:
    """Map a status string to a representative emoji."""
    s = str(status or "").lower().strip()
    if s in {"ok", "healthy", "done", "completed", "success", "active"}:
        return "🟢"
    if s in {"running", "in_progress"}:
        return "🔵"
    if s in {"warn", "warning", "degraded"}:
        return "🟡"
    if s in {"error", "failed", "unhealthy"}:
        return "🔴"
    if s in {"paused", "stopped", "cancelled"}:
        return "⏸"
    if s in {"pending", "queued"}:
        return "⏳"
    return "●"


def _print_meta_footer(*pairs: tuple[str, str]) -> None:
    """Print dim label + value metadata lines after a command (e.g. session id, saved path)."""
    if not pairs:
        return
    print()
    if _RICH_AVAILABLE and _IS_TTY:
        for label, value in pairs:
            _RICH_CONSOLE.print(f"  [dim]{label}:[/]  [dim]{value}[/]")
    else:
        for label, value in pairs:
            print(f"  {_DM}{label}:{_R}  {value}")


def _print_error(msg: str, *, file: object = None) -> None:
    """Print a standardized error message with color when available."""
    import sys as _sys
    dest = file if file is not None else _sys.stdout
    if _RICH_AVAILABLE and _IS_TTY and dest is _sys.stderr:
        _RICH_CONSOLE.print(f"[bold red]error:[/] {msg}", stderr=True)
    elif _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[bold red]error:[/] {msg}")
    else:
        print(f"{_BRE}error:{_R} {msg}", file=dest)


def _print_shell_result(result: Any) -> None:
    """Print a shell execution result with colored exit code and dim output blocks."""
    if _RICH_AVAILABLE and _IS_TTY:
        exit_ok = result.returncode == 0
        exit_style = "green" if exit_ok else "bold red"
        exit_icon = "\u2713" if exit_ok else "\u2717"
        _RICH_CONSOLE.print(f"[dim]$[/] {result.command}  [dim]\u00b7  cwd:[/] {result.cwd}  [{exit_style}]{exit_icon} exit {result.returncode}[/]")
        if result.stdout.strip():
            _RICH_CONSOLE.print(f"[dim]\u2500\u2500\u2500 stdout \u2500\u2500\u2500[/]\n[dim]{result.stdout.rstrip()}[/]")
        if result.stderr.strip():
            _RICH_CONSOLE.print(f"[dim]\u2500\u2500\u2500 stderr \u2500\u2500\u2500[/]\n[dim red]{result.stderr.rstrip()}[/]")
    else:
        from openclaw_cli_actions import format_shell_result
        print(format_shell_result(result))


def _print_file_edit_result(result: Any) -> None:
    """Print a file edit result with colored summary and diff when available."""
    if _RICH_AVAILABLE and _IS_TTY:
        icon = "✓" if result.changed else "—"
        style = "green" if result.changed else "dim"
        _RICH_CONSOLE.print(f"[{style}]{icon}[/] {result.summary}  [dim]{result.path}[/]")
        if result.diff:
            _RICH_CONSOLE.print("[dim]─── diff ───[/]")
            for line in result.diff.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    _RICH_CONSOLE.print(f"[green]{line}[/]")
                elif line.startswith("-") and not line.startswith("---"):
                    _RICH_CONSOLE.print(f"[red]{line}[/]")
                else:
                    _RICH_CONSOLE.print(f"[dim]{line}[/]")
    else:
        from openclaw_cli_actions import preview_file_result
        print(preview_file_result(result))


def _with_spinner(label: str, fn: Any, *args: Any, output_json: bool = False, **kwargs: Any) -> Any:
    """Run *fn* in a background thread while showing an animated braille spinner.

    Falls back to a direct call when output is not a TTY or when --json output
    is requested so that machine-readable output is never corrupted.
    """
    if not (_IS_TTY and not output_json):
        return fn(*args, **kwargs)

    result_holder: list[Any] = []
    exc_holder: list[BaseException] = []

    def _run() -> None:
        try:
            result_holder.append(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            exc_holder.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    frame_idx = 0
    start = time.monotonic()
    while thread.is_alive():
        elapsed = time.monotonic() - start
        frame = spinner_frames[frame_idx % len(spinner_frames)]
        sys.stdout.write(f"\r{frame} {label}  {elapsed:.0f}s")
        sys.stdout.flush()
        frame_idx += 1
        time.sleep(0.1)

    thread.join()
    # Clear the spinner line.
    sys.stdout.write("\r" + " " * (len(label) + 20) + "\r")
    sys.stdout.flush()

    if exc_holder:
        raise exc_holder[0]
    return result_holder[0]


TRANSIENT_WATCH_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "unable to reach",
    "connection refused",
    "refused the connection",
    "temporarily unavailable",
    "temporary failure",
    "network is unreachable",
    "connection reset",
    "connection aborted",
    "remote end closed connection",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


class OpenClawCliError(RuntimeError):
    """Raised when the CLI cannot talk to the OpenClaw API."""


@dataclass
class AskResponse:
    """Structured response from the OpenClaw ask API."""

    response: str
    model: str
    tokens: int
    raw: dict[str, Any]


@dataclass
class HealthResponse:
    """Structured response from the OpenClaw health endpoint."""

    payload: Any
    raw_text: str
    status: str = ""
    healthy: bool | None = None


@dataclass(frozen=True)
class LocalLinkValidation:
    """Result of checking a plan/task identifier against local on-disk sources."""

    kind: str
    item_id: str
    available: bool
    exists: bool = False
    source: str = ""
    summary: str = ""


@dataclass
class TokenResolution:
    """Resolved token plus the source it came from."""

    token: str
    source: str


@dataclass(frozen=True)
class ReplRouteStepContext:
    """Resolved plan-step grounding used to route ambiguous REPL prompts."""

    num: int
    description: str
    status: str = ""


@dataclass(frozen=True)
class ReplRouteGrounding:
    """Active session context that can sharpen freeform REPL routing."""

    session_id: str = ""
    cwd: str = ""
    plan_id: str = ""
    plan_goal: str = ""
    task_id: str = ""
    task_title: str = ""
    task_status: str = ""
    task_description: str = ""
    current_step: ReplRouteStepContext | None = None
    plan: Any | None = None


@dataclass
class CliConfig:
    """Resolved runtime configuration for a CLI invocation."""

    base_url: str
    token: str
    model: str
    timeout_seconds: int
    user_name: str
    client_name: str
    output_json: bool = False
    session_id: str = ""


def normalize_base_url(raw_url: str | None) -> str:
    """Normalize a user-provided base URL and remove trailing slashes."""
    value = str(raw_url or "").strip() or DEFAULT_BASE_URL
    return value.rstrip("/")


def default_client_name() -> str:
    """Return a human-readable client name for telemetry and headers."""
    return (
        os.getenv("OPENCLAW_CLIENT_NAME")
        or socket.gethostname()
        or platform.node()
        or "openclaw-cli"
    ).strip()


def default_user_name() -> str:
    """Return the logical user label sent to the ask API."""
    configured = (os.getenv("OPENCLAW_USER_NAME") or "").strip()
    if configured:
        return configured
    user = getpass.getuser().strip() or "cli"
    client_name = default_client_name()
    return f"{user}@{client_name}"


def read_keychain_token(*, account: str | None = None) -> str:
    """Look up the CLI token from macOS Keychain when available."""
    if sys.platform != "darwin":
        return ""
    keychain_account = (account or os.getenv("USER") or getpass.getuser() or "").strip()
    if not keychain_account:
        return ""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                keychain_account,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def write_keychain_token(token: str, *, account: str | None = None) -> None:
    """Store a CLI token in macOS Keychain."""
    value = str(token).strip()
    if not value:
        raise OpenClawCliError("OpenClaw token cannot be empty.")
    keychain_account = (account or os.getenv("USER") or getpass.getuser() or "").strip()
    if not keychain_account:
        raise OpenClawCliError("Unable to determine the current macOS account for Keychain storage.")
    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                keychain_account,
                "-w",
                value,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenClawCliError("Unable to store token in macOS Keychain.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "security add-generic-password failed"
        raise OpenClawCliError(f"Unable to store token in macOS Keychain: {detail}")


def delete_keychain_token(*, account: str | None = None) -> bool:
    """Delete the CLI token from macOS Keychain when present."""
    if sys.platform != "darwin":
        return False
    keychain_account = (account or os.getenv("USER") or getpass.getuser() or "").strip()
    if not keychain_account:
        return False
    try:
        result = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                keychain_account,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenClawCliError("Unable to remove token from macOS Keychain.") from exc
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout or "").strip().lower()
    if "could not be found" in detail or "item not found" in detail:
        return False
    raise OpenClawCliError(f"Unable to remove token from macOS Keychain: {detail or 'unknown error'}")


def auth_storage_path(*, platform_name: str | None = None) -> Path:
    """Return the per-user fallback credential file path for the CLI."""
    current_platform = platform_name or sys.platform
    if current_platform.startswith("win"):
        base_dir = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "OpenClaw"
    elif current_platform == "darwin":
        base_dir = Path.home() / "Library" / "Application Support" / "OpenClaw"
    else:
        base_dir = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "openclaw"
    return base_dir / AUTH_FILE_NAME


def read_saved_token(*, path: Path | None = None) -> str:
    """Read a token from the fallback credential file when present."""
    target = path or auth_storage_path()
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OpenClawCliError(f"Unable to read stored OpenClaw token from {target}: {exc}") from exc


def write_saved_token(token: str, *, path: Path | None = None) -> Path:
    """Persist a token to the fallback credential file."""
    value = str(token).strip()
    if not value:
        raise OpenClawCliError("OpenClaw token cannot be empty.")
    target = path or auth_storage_path()
    tmp_target = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target.write_text(f"{value}\n", encoding="utf-8")
        if os.name != "nt":
            tmp_target.chmod(0o600)
        os.replace(tmp_target, target)
        if os.name != "nt":
            target.chmod(0o600)
    except OSError as exc:
        raise OpenClawCliError(f"Unable to store OpenClaw token in {target}: {exc}") from exc
    return target


def delete_saved_token(*, path: Path | None = None) -> bool:
    """Delete the fallback credential file when present."""
    target = path or auth_storage_path()
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError as exc:
        raise OpenClawCliError(f"Unable to remove stored OpenClaw token from {target}: {exc}") from exc
    return True


def resolve_token_details(explicit_token: str | None = None) -> TokenResolution:
    """Resolve a token and describe where it came from."""
    value = str(explicit_token or "").strip()
    if value:
        return TokenResolution(token=value, source="command line flag --token")

    for env_name in ("OPENCLAW_TOKEN", "DASHBOARD_API_TOKEN"):
        env_value = str(os.getenv(env_name) or "").strip()
        if env_value:
            return TokenResolution(token=env_value, source=f"environment variable {env_name}")

    keychain_token = read_keychain_token()
    if keychain_token:
        return TokenResolution(token=keychain_token, source=f"macOS Keychain '{KEYCHAIN_SERVICE}'")

    saved_token = read_saved_token()
    if saved_token:
        return TokenResolution(token=saved_token, source=f"credential file {auth_storage_path()}")

    return TokenResolution(token="", source="")


def resolve_token(explicit_token: str | None = None) -> str:
    """Resolve a token from CLI arg, env vars, keychain, or fallback file store."""
    return resolve_token_details(explicit_token).token


def auth_setup_hint(*, platform_name: str | None = None) -> str:
    """Return platform-aware guidance for configuring CLI authentication."""
    if (platform_name or sys.platform) == "darwin":
        return (
            f"Set {TOKEN_ENV_VARS}, store a token in macOS Keychain under "
            f"'{KEYCHAIN_SERVICE}', run `openclaw auth login`, or pass --token."
        )
    return f"Set {TOKEN_ENV_VARS}, run `openclaw auth login`, or pass --token."


def cli_version() -> str:
    """Return the installed CLI version when available."""
    try:
        return metadata.version("openclaw")
    except metadata.PackageNotFoundError:
        return DEFAULT_VERSION


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert a version string like '2026.3.20' or '0.6.0' to a comparable tuple."""
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _fetch_latest_pypi_version(timeout: float = 3.0) -> str | None:
    """Return the latest openclaw version from PyPI, or None on any error."""
    try:
        req = request.Request(
            "https://pypi.org/pypi/openclaw/json",
            headers={"Accept": "application/json"},
        )
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return str(data["info"]["version"])
    except Exception:
        return None


def _find_pip() -> list[str] | None:
    """Return the first usable pip invocation, or None if pip is unavailable.

    Tries ``sys.executable -m pip`` first so that the same virtual-environment
    that is running openclaw is used for the upgrade, then falls back to the
    common ``pip``/``pip3`` shims on PATH.
    """
    candidates: list[list[str]] = [
        [sys.executable, "-m", "pip"],   # same venv/interpreter as running process
        ["pip3"],
        ["pip"],
        ["python3", "-m", "pip"],
        ["python", "-m", "pip"],
    ]
    for cmd in candidates:
        try:
            result = subprocess.run(
                cmd + ["--version"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _print_update_notice(current: str, latest: str | None) -> None:
    """Print a styled update-available notice.

    When ``latest`` is None (standalone hash-based check), only the "Run: /update"
    line is shown without a version arrow.
    """
    if _RICH_AVAILABLE and _IS_TTY:
        from rich.panel import Panel as _P
        from rich.text import Text as _T
        t = _T()
        t.append("⬆  Update available", style="bold yellow")
        if latest is not None:
            t.append("   ", style="")
            t.append(current, style="dim")
            t.append("  →  ", style="dim")
            t.append(latest, style="bold green")
        t.append("\n   Run: ", style="dim")
        t.append("/update", style="bold cyan")
        _RICH_CONSOLE.print(_P(t, border_style="yellow", padding=(0, 1)))
    else:
        action = f"   {_DM}Run:{_R} {_BCY}/update{_R}"
        if latest is not None:
            version_line = f"   {_DM}{current}{_R}  →  {_BGR}{latest}{_R}\n"
        else:
            version_line = ""
        print(
            f"\n{_BYE}⬆  Update available!{_R}\n"
            f"{version_line}"
            f"{action}\n",
            file=sys.stderr,
        )


def _standalone_install_dir() -> str | None:
    """Return the standalone install dir if openclaw is running from one, else None.

    A standalone install places openclaw_cli.py directly in a directory like
    ~/.local/share/openclaw-cli/ and runs it via a bash shim — not from a
    pip-managed site-packages location.
    """
    try:
        script = Path(__file__).resolve()
        marker = script.parent / "openclaw_cli_sessions.py"
        if marker.exists() and "site-packages" not in str(script):
            return str(script.parent)
    except Exception:
        pass
    return None


def _update_standalone_install(install_dir: str, *, current: str, base_url: str) -> int:
    """Download CLI files from the openclaw server and replace in-place."""
    import urllib.request

    files = [
        "openclaw_cli.py",
        "openclaw_cli_actions.py",
        "openclaw_cli_sessions.py",
        "subprocess_utils.py",
    ]
    server = base_url.rstrip("/")

    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(
            f"[bold cyan]🦞 Updating openclaw[/]  [dim]{current}[/]  "
            f"[dim]from[/] [cyan]{server}[/]"
        )
    else:
        print(f"Updating openclaw {current} from {server}…")

    updated: list[str] = []
    failed: list[tuple[str, str]] = []

    for fname in files:
        url = f"{server}/cli-update/{fname}"
        dest = Path(install_dir) / fname
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"  [dim]↓ {fname}[/]", end="")
        else:
            print(f"  ↓ {fname}", end="", flush=True)
        try:
            tmp = dest.with_suffix(".tmp")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(dest)
            updated.append(fname)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("  [green]✓[/]")
            else:
                print("  ✓")
        except Exception as exc:
            failed.append((fname, str(exc)))
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"  [red]✗[/]")
            else:
                print("  ✗")

    if failed:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_ERR.print("\n[bold red]✗ Update incomplete[/] — some files could not be downloaded:")
            for fname, err in failed:
                url = f"{server}/cli-update/{fname}"
                _RICH_ERR.print(f"  [red]{fname}[/]  [dim]{url}[/]\n  [dim red]{err}[/]")
        else:
            print("\n✗ Update incomplete — some files could not be downloaded:", file=sys.stderr)
            for fname, err in failed:
                url = f"{server}/cli-update/{fname}"
                print(f"  {fname}  {url}\n  {err}", file=sys.stderr)
        return 1
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("\n[bold green]✓ Updated.[/] Restart openclaw to use the new version.")
        else:
            print("\n✓ Updated. Restart openclaw to use the new version.")
        global _standalone_needs_update
        _standalone_needs_update = False
        return 0


def handle_update_command(_args: argparse.Namespace) -> int:
    """Self-update openclaw via pip, showing a spinner while the install runs."""
    pip_cmd = _find_pip()
    if pip_cmd is None:
        msg = (
            "Could not find pip, pip3, or 'python -m pip'.\n"
            "Install pip first:  https://pip.pypa.io/en/stable/installation/\n"
            "Then run:  pip install --upgrade openclaw"
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_ERR.print(f"[bold red]error:[/] {msg}")
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 1

    current = cli_version()
    latest = _fetch_latest_pypi_version() or "latest"

    # Standalone installs (bash-shim on laptop) can't use pip install because
    # they're not in a venv and macOS blocks system-Python pip installs (PEP 668).
    # Instead, download the files directly from the openclaw server.
    install_dir = _standalone_install_dir()
    if install_dir:
        _cfg = build_config(argparse.Namespace(
            url=os.getenv("OPENCLAW_URL"),
            token=None,
            model=None,
            timeout=30,
            user_name=None,
            client_name=None,
            json=False,
            session="",
        ))
        return _update_standalone_install(install_dir, current=current, base_url=_cfg.base_url)

    # Standard pip install path (venv or user site-packages).
    in_venv = sys.prefix != sys.base_prefix
    user_flag = [] if in_venv else ["--user"]
    install_cmd = pip_cmd + ["install", "--upgrade"] + user_flag

    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(
            f"[bold cyan]🦞 Updating openclaw[/]  "
            f"[dim]{current}[/] [dim]→[/] [bold green]{latest}[/]"
        )

        # Run pip quietly and show a live spinner with elapsed time.
        result_holder: list[subprocess.CompletedProcess[bytes]] = []

        def _run_pip() -> None:
            result_holder.append(
                subprocess.run(
                    install_cmd + ["--quiet", "openclaw"],
                    capture_output=True,
                )
            )

        pip_thread = threading.Thread(target=_run_pip, daemon=True)
        pip_thread.start()

        _RICH_CONSOLE.print()
        start = time.monotonic()
        spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        frame_idx = 0
        while pip_thread.is_alive():
            elapsed = time.monotonic() - start
            frame = spinner_frames[frame_idx % len(spinner_frames)]
            _RICH_CONSOLE.print(
                f"\r  [cyan]{frame}[/] Installing…  [dim]{elapsed:.0f}s elapsed[/]",
                end="",
            )
            frame_idx += 1
            time.sleep(0.1)

        pip_thread.join()
        elapsed = time.monotonic() - start
        _RICH_CONSOLE.print()  # end spinner line

        result = result_holder[0]
        if result.returncode == 0:
            _RICH_CONSOLE.print(
                f"[bold green]✓ Done[/] in [cyan]{elapsed:.1f}s[/]  —  "
                f"openclaw [dim]{current}[/] → [bold green]{latest}[/]"
            )
        else:
            _RICH_ERR.print("\n[bold red]✗ Update failed[/]")
            if result.stderr:
                _RICH_ERR.print(result.stderr.decode(errors="replace"))
    else:
        # Plain fallback: just run pip with its normal output.
        print(f"Updating openclaw {current} → {latest}…")
        result = subprocess.run(install_cmd + ["openclaw"])
        if result.returncode == 0:
            print(f"✓ Done  —  openclaw {current} → {latest}")
        else:
            print("✗ Update failed. Check the output above.", file=sys.stderr)

    return result.returncode


def check_for_update(*, timeout: float = 3.0) -> None:
    """Check PyPI for a newer openclaw release and cache the result in _latest_version.

    Does NOT print — callers should print the notice from the main thread to avoid
    interleaving with readline prompts.  All errors are silently swallowed.
    """
    global _latest_version
    latest = _fetch_latest_pypi_version(timeout=timeout)
    if latest:
        _latest_version = latest


def build_headers(*, token: str, client_name: str) -> dict[str, str]:
    """Build HTTP headers for OpenClaw API requests."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"OpenClawCLI/1.0 ({client_name})",
        "X-OpenClaw-Client": client_name,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def attach_session_header(headers: dict[str, str], *, session_id: str) -> dict[str, str]:
    """Attach the CLI session header when a session is active."""
    updated = dict(headers)
    if session_id:
        updated["X-OpenClaw-Session-ID"] = session_id
    return updated


def parse_prompt(prompt_parts: list[str]) -> str:
    """Resolve a prompt from args or stdin for pipeline-friendly use."""
    joined = " ".join(prompt_parts).strip()
    if joined:
        return joined
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def ensure_cli_session(
    session_id: str,
    *,
    title: str,
    cwd: str | None = None,
    files: list[str] | None = None,
    plan_id: str = "",
    task_id: str = "",
) -> SessionSummary:
    """Load an existing session or create a new one when needed."""
    existing_id = str(session_id or "").strip()
    if existing_id:
        session = load_session(existing_id)
        if session is None:
            raise OpenClawCliError(f"Session '{existing_id}' was not found.")
        return session
    return create_session(title=title, cwd=cwd, files=files or [], plan_id=plan_id, task_id=task_id)


def bind_config_to_session(config: CliConfig, session_id: str) -> CliConfig:
    """Return a copy of the CLI config scoped to the given session."""
    return CliConfig(
        base_url=config.base_url,
        token=config.token,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
        user_name=config.user_name,
        client_name=config.client_name,
        output_json=config.output_json,
        session_id=session_id,
    )


def summarize_session(session: SessionSummary) -> str:
    """Render a compact single-session summary for terminal output."""
    parts = [
        f"session: {session.session_id}",
        f"title: {session.title}",
        f"cwd: {session.cwd}",
        f"updated: {session.updated_at}",
        f"commands: {session.command_count}",
        f"outputs: {session.output_count}",
    ]
    if session.plan_id:
        parts.append(f"plan: {session.plan_id}")
    if session.task_id:
        parts.append(f"task: {session.task_id}")
    if session.files:
        parts.append("files: " + ", ".join(session.files[:6]))
    if session.last_summary:
        parts.append(f"last: {session.last_summary}")
    if session.automation_mode:
        status = session.automation_status or "active"
        parts.append(f"automation: {session.automation_mode} ({status})")
    if session.checkpoint_count:
        parts.append(f"checkpoints: {session.checkpoint_count}")
    if session.last_checkpoint_at:
        parts.append(f"last checkpoint: {session.last_checkpoint_at}")
    return "\n".join(parts)


def _print_session_summary(session: SessionSummary) -> None:
    """Print a compact session summary, with rich formatting when available."""
    if _RICH_AVAILABLE and _IS_TTY:
        grid = _RichTable.grid(padding=(0, 2))
        grid.add_column(style="dim", min_width=12)
        grid.add_column()
        grid.add_row("🆔 id", f"[dim]{session.session_id}[/]")
        grid.add_row("📋 title", f"[bold]{session.title}[/]")
        if session.cwd:
            grid.add_row("📁 cwd", f"[dim]{session.cwd}[/]")
        grid.add_row("🕐 updated", f"[yellow]{session.updated_at}[/]")
        grid.add_row("📊 stats", f"[cyan]{session.command_count}[/] commands  [cyan]{session.output_count}[/] outputs")
        if session.plan_id:
            grid.add_row("📋 plan", f"[magenta]{session.plan_id}[/]")
        if session.task_id:
            grid.add_row("✅ task", f"[magenta]{session.task_id}[/]")
        if session.files:
            grid.add_row("📄 files", f"[dim]{', '.join(session.files[:4])}{'…' if len(session.files) > 4 else ''}[/]")
        if session.last_summary:
            grid.add_row("💬 last", f"[dim]{session.last_summary[:80]}[/]")
        if session.automation_mode:
            a_status = session.automation_status or "active"
            grid.add_row("🤖 automation", f"[cyan]{session.automation_mode}[/] [dim]({a_status})[/]")
        _RICH_CONSOLE.print(_RichPanel(grid, border_style="cyan", padding=(0, 1)))
    else:
        print(summarize_session(session))


def _print_session_list(items: list[SessionSummary]) -> None:
    """Print a session list table, with rich formatting when available."""
    if not items:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No OpenClaw CLI sessions have been recorded yet.[/]")
        else:
            print("No OpenClaw CLI sessions have been recorded yet.")
        return
    if _RICH_AVAILABLE and _IS_TTY:
        table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
        table.add_column("Session ID", style="dim", no_wrap=True)
        table.add_column("Title", style="bold")
        table.add_column("Updated", style="yellow", no_wrap=True)
        table.add_column("Cmds", justify="right", style="cyan")
        table.add_column("Outputs", justify="right", style="cyan")
        table.add_column("Mode", style="dim")
        for s in items:
            table.add_row(
                s.session_id,
                s.title or "—",
                s.updated_at or "—",
                str(s.command_count),
                str(s.output_count),
                s.automation_mode or "—",
            )
        _RICH_CONSOLE.print(table)
    else:
        print(format_session_list(items))


def inspect_session(session_id: str) -> str:
    """Render a human-readable inspection view of a persisted session."""
    from openclaw_cli_sessions import export_session

    export = export_session(session_id)
    session_data: dict[str, Any] = export.get("session") or {}
    events: list[dict[str, Any]] = export.get("events") or []
    outputs: list[dict[str, Any]] = export.get("outputs") or []
    watch: dict[str, Any] = export.get("watch_state") or {}
    routed_checkpoints: list[dict[str, Any]] = export.get("routed_action_checkpoints") or []

    if _RICH_AVAILABLE and _IS_TTY:
        _inspect_session_rich(session_id, session_data, events, outputs, watch, routed_checkpoints)
        return ""

    sep = "-" * 60
    lines: list[str] = []

    # ── Metadata ─────────────────────────────────────────────────
    lines += [
        sep,
        "SESSION INSPECTION",
        sep,
        f"  id       : {session_data.get('session_id', session_id)}",
        f"  title    : {session_data.get('title', '')}",
        f"  status   : {session_data.get('status', 'active')}",
        f"  cwd      : {session_data.get('cwd', '')}",
        f"  created  : {session_data.get('created_at', '')}",
        f"  updated  : {session_data.get('updated_at', '')}",
        f"  commands : {session_data.get('command_count', 0)}  "
        f"outputs: {session_data.get('output_count', 0)}  "
        f"edits: {session_data.get('file_edit_count', 0)}",
    ]

    # ── Plan / task linkage ───────────────────────────────────────
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id or task_id:
        lines.append("")
        lines.append("PLAN / TASK LINKAGE")
        if plan_id:
            lines.append(f"  plan  : {plan_id}")
        if task_id:
            lines.append(f"  task  : {task_id}")

    # ── Tracked files ─────────────────────────────────────────────
    files: list[str] = list(session_data.get("files") or [])
    if files:
        lines.append("")
        lines.append("TRACKED FILES")
        for f in files[:10]:
            lines.append(f"  {f}")
        if len(files) > 10:
            lines.append(f"  … and {len(files) - 10} more")

    # ── Automation / watch status ─────────────────────────────────
    automation_mode = str(session_data.get("automation_mode") or "").strip()
    if automation_mode or watch:
        lines.append("")
        lines.append("AUTOMATION / WATCH")
        if automation_mode:
            a_status = str(session_data.get("automation_status") or "active").strip()
            interval = int(session_data.get("watch_interval_seconds") or 0)
            lines.append(f"  mode     : {automation_mode}  status: {a_status}")
            if interval:
                lines.append(f"  interval : {interval}s")
        if watch:
            w_status = str(watch.get("status") or "").strip()
            poll_count = int(watch.get("poll_count") or 0)
            max_polls = int(watch.get("max_polls") or 0)
            goal = str(watch.get("goal") or "").strip()
            if goal:
                lines.append(f"  goal     : {goal[:120]}")
            if w_status:
                lines.append(f"  w.status : {w_status}  polls: {poll_count}/{max_polls or '∞'}")
            last_error = str(watch.get("last_error") or "").strip()
            if last_error:
                lines.append(f"  last err : {last_error[:200]}")

    # ── Checkpoints ───────────────────────────────────────────────
    checkpoint_count = int(session_data.get("checkpoint_count") or 0)
    last_checkpoint_at = str(session_data.get("last_checkpoint_at") or "").strip()
    watch_checkpoints: list[dict[str, Any]] = list(watch.get("checkpoints") or [])
    if checkpoint_count or watch_checkpoints or routed_checkpoints:
        lines.append("")
        lines.append("CHECKPOINTS")
        lines.append(f"  total : {checkpoint_count}  last: {last_checkpoint_at or 'n/a'}")
        for ckpt in routed_checkpoints[:3]:
            step_index = int(ckpt.get("step_index") or 0)
            step_total = int(ckpt.get("step_total") or 0)
            step_label = (
                f"step {step_index}/{step_total}"
                if step_index > 0 and step_total > 0
                else "routed action"
            )
            lines.append(
                f"  [{ckpt.get('created_at', '')}] {ckpt.get('action_kind', 'action')}"
                f" {step_label} ({ckpt.get('rollback_status', 'available')})"
            )
        for ckpt in watch_checkpoints[-3:]:
            ts = str(ckpt.get("timestamp") or ckpt.get("at") or "").strip()
            note = str(ckpt.get("note") or ckpt.get("summary") or "").strip()
            if ts or note:
                lines.append(f"  [{ts}] {note[:100]}")

    # ── Recent progress log (watch) ───────────────────────────────
    progress_log: list[dict[str, Any]] = list(watch.get("progress_log") or [])
    if progress_log:
        lines.append("")
        lines.append("RECENT PROGRESS (last 5 watch entries)")
        for entry in progress_log[-5:]:
            ts = str(entry.get("timestamp") or entry.get("at") or "").strip()
            phase = str(entry.get("phase") or "").strip()
            note = str(entry.get("note") or entry.get("summary") or entry.get("content") or "").strip()
            lines.append(f"  [{ts}] ({phase}) {note[:120]}")

    # ── Recent events ─────────────────────────────────────────────
    if events:
        lines.append("")
        lines.append("RECENT EVENTS (last 5)")
        for event in events[-5:]:
            ts = str(event.get("timestamp") or event.get("at") or event.get("created_at") or "").strip()
            kind = str(event.get("kind") or "").strip()
            content = str(event.get("content") or "").strip()
            meta = event.get("metadata") or {}
            summary_note = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            label = summary_note or content[:80]
            lines.append(f"  [{ts}] {kind}: {label}")

    # ── Saved outputs ─────────────────────────────────────────────
    if outputs:
        lines.append("")
        lines.append(f"SAVED OUTPUTS ({len(outputs)})")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = int(out.get("size_bytes") or 0)
            lines.append(f"  {name}  ({size} bytes)")

    # ── Last summary ──────────────────────────────────────────────
    last_summary = str(session_data.get("last_summary") or "").strip()
    if last_summary:
        lines.append("")
        lines.append("LAST SUMMARY")
        lines.append(f"  {last_summary}")

    lines.append(sep)
    lines.append(f"Resume: openclaw --session {session_data.get('session_id', session_id)}")
    return "\n".join(lines)


def _inspect_session_rich(
    session_id: str,
    session_data: dict[str, Any],
    events: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    watch: dict[str, Any],
    routed_checkpoints: list[dict[str, Any]],
) -> None:
    """Print a rich-formatted session inspection view."""
    sid = session_data.get("session_id", session_id)
    title = session_data.get("title") or "Session"
    status = str(session_data.get("status") or "active")
    emoji = _status_emoji(status)

    # Metadata panel
    meta = _RichTable.grid(padding=(0, 2))
    meta.add_column(style="dim", min_width=12)
    meta.add_column()
    meta.add_row("🆔 id", f"[dim]{sid}[/]")
    meta.add_row(f"{emoji} status", f"[bold]{status}[/]")
    meta.add_row("📁 cwd", f"[dim]{session_data.get('cwd', '')}[/]")
    meta.add_row("🕐 created", f"[dim]{session_data.get('created_at', '')}[/]")
    meta.add_row("🕐 updated", f"[yellow]{session_data.get('updated_at', '')}[/]")
    meta.add_row(
        "📊 stats",
        f"[cyan]{session_data.get('command_count', 0)}[/] commands  "
        f"[cyan]{session_data.get('output_count', 0)}[/] outputs  "
        f"[cyan]{session_data.get('file_edit_count', 0)}[/] edits",
    )
    plan_id = str(session_data.get("plan_id") or "").strip()
    task_id = str(session_data.get("task_id") or "").strip()
    if plan_id:
        meta.add_row("📋 plan", f"[magenta]{plan_id}[/]")
    if task_id:
        meta.add_row("✅ task", f"[magenta]{task_id}[/]")
    files: list[str] = list(session_data.get("files") or [])
    if files:
        file_str = ", ".join(files[:5]) + (f" … +{len(files)-5}" if len(files) > 5 else "")
        meta.add_row("📄 files", f"[dim]{file_str}[/]")
    _RICH_CONSOLE.print(_RichPanel(meta, title=f"[bold cyan]{title}[/]", border_style="cyan", padding=(0, 1)))

    # Events panel
    if events:
        kind_styles = {"prompt": "cyan", "assistant": "green", "exec": "yellow", "edit": "magenta", "error": "red"}
        ev_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        ev_table.add_column("Time", style="dim", no_wrap=True)
        ev_table.add_column("Kind", no_wrap=True)
        ev_table.add_column("Summary")
        for event in events[-8:]:
            ts = str(event.get("timestamp") or event.get("created_at") or "").strip()[-8:]
            kind = str(event.get("kind") or "").strip()
            meta_d = event.get("metadata") or {}
            summary = str(meta_d.get("summary") if isinstance(meta_d, dict) else "") or str(event.get("content") or "")
            style = kind_styles.get(kind, "dim")
            ev_table.add_row(ts, f"[{style}]{kind}[/]", summary[:80])
        _RICH_CONSOLE.print(_RichPanel(ev_table, title="[bold dim]Recent Events[/]", border_style="dim", padding=(0, 1)))

    # Outputs panel
    if outputs:
        out_table = _RichTable(border_style="dim", show_edge=False, pad_edge=True, header_style="bold dim")
        out_table.add_column("Name", style="cyan")
        out_table.add_column("Size", justify="right", style="dim")
        for out in outputs[-5:]:
            name = str(out.get("name") or "").strip()
            size = _format_byte_count(int(out.get("size_bytes") or 0))
            out_table.add_row(name, size)
        _RICH_CONSOLE.print(_RichPanel(out_table, title=f"[bold dim]Saved Outputs ({len(outputs)})[/]", border_style="dim", padding=(0, 1)))

    _RICH_CONSOLE.print(f"  [dim]Resume:[/] [cyan]openclaw --session {sid}[/]")


def format_session_list(items: list[SessionSummary]) -> str:
    """Render a recent-session table as plain text."""
    if not items:
        return "No OpenClaw CLI sessions have been recorded yet."
    rows = ["SESSION ID | UPDATED | MODE | COMMANDS | OUTPUTS | TITLE", "-" * 104]
    for session in items:
        rows.append(
            f"{session.session_id} | {session.updated_at} | {session.automation_mode or '-'} | {session.command_count} | "
            f"{session.output_count} | {session.title}"
        )
    return "\n".join(rows)


def utc_timestamp() -> str:
    """Return a UTC timestamp for watch-mode state updates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_plan_goal(plan_id: str) -> str:
    """Resolve a plan goal when watch mode is attached to an existing plan."""
    normalized = str(plan_id or "").strip()
    if not normalized:
        return ""
    from agent_loop import load_plan as load_agent_plan

    plan = load_agent_plan(normalized)
    return str(plan.goal or "").strip() if plan else ""


def _truncate_preview(text: str, *, max_chars: int) -> str:
    clipped = str(text or "").strip()
    if len(clipped) <= max_chars:
        return clipped
    return clipped[: max_chars - 15].rstrip() + "\n...[truncated]..."


def _format_byte_count(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes or 0)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(size)} B"


def _candidate_workspace_roots(cwd: str | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    seeds = [str(cwd or "").strip(), str(Path.cwd())]
    for seed in seeds:
        if not seed:
            continue
        resolved = Path(seed).expanduser().resolve()
        for candidate in (resolved, *resolved.parents):
            marker = str(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            roots.append(candidate)
    return roots


def _resolve_local_source(path_text: str, *, cwd: str | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate
    base = Path(cwd).expanduser().resolve() if cwd else Path.cwd().resolve()
    return (base / candidate).resolve()


def _find_local_plan_dir(*, cwd: str | None = None) -> Path | None:
    candidates: list[Path] = []
    env_path = str(os.getenv("PLANS_DIR") or "").strip()
    if env_path:
        candidates.append(_resolve_local_source(env_path, cwd=cwd))
    candidates.extend(root / "data" / "plans" for root in _candidate_workspace_roots(cwd))
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_dir():
            return candidate
    return None


def _find_local_tasks_file(*, cwd: str | None = None) -> Path | None:
    candidates: list[Path] = []
    env_path = str(os.getenv("MC_TASKS_FILE") or "").strip()
    if env_path:
        candidates.append(_resolve_local_source(env_path, cwd=cwd))
    candidates.extend(root / "data" / "tasks.json" for root in _candidate_workspace_roots(cwd))
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate
    return None


def _read_plan_goal_from_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Plan:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _validate_plan_id_local(plan_id: str, *, cwd: str | None = None) -> LocalLinkValidation:
    normalized = str(plan_id or "").strip()
    if not normalized:
        return LocalLinkValidation(kind="plan", item_id="", available=False)
    plan_dir = _find_local_plan_dir(cwd=cwd)
    if plan_dir is None:
        return LocalLinkValidation(kind="plan", item_id=normalized, available=False)
    plan_path = plan_dir / f"{normalized}.md"
    if not plan_path.is_file():
        return LocalLinkValidation(
            kind="plan",
            item_id=normalized,
            available=True,
            exists=False,
            source=str(plan_dir),
        )
    return LocalLinkValidation(
        kind="plan",
        item_id=normalized,
        available=True,
        exists=True,
        source=str(plan_path),
        summary=_read_plan_goal_from_file(plan_path),
    )


def _validate_task_id_local(task_id: str, *, cwd: str | None = None) -> LocalLinkValidation:
    normalized = str(task_id or "").strip()
    if not normalized:
        return LocalLinkValidation(kind="task", item_id="", available=False)
    tasks_file = _find_local_tasks_file(cwd=cwd)
    if tasks_file is None:
        return LocalLinkValidation(kind="task", item_id=normalized, available=False)
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalLinkValidation(kind="task", item_id=normalized, available=False, source=str(tasks_file))
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return LocalLinkValidation(kind="task", item_id=normalized, available=False, source=str(tasks_file))
    task = next((item for item in tasks if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized), None)
    if task is None:
        return LocalLinkValidation(
            kind="task",
            item_id=normalized,
            available=True,
            exists=False,
            source=str(tasks_file),
        )
    title = str(task.get("title") or "").strip()
    status = str(task.get("status") or "").strip()
    summary_parts = [part for part in (title, f"status={status}" if status else "") if part]
    return LocalLinkValidation(
        kind="task",
        item_id=normalized,
        available=True,
        exists=True,
        source=str(tasks_file),
        summary="; ".join(summary_parts),
    )


def _load_task_record(task_id: str, *, cwd: str | None = None) -> dict[str, Any] | None:
    normalized = str(task_id or "").strip()
    if not normalized:
        return None
    tasks_file = _find_local_tasks_file(cwd=cwd)
    if tasks_file is None:
        return None
    try:
        payload = json.loads(tasks_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        return None
    return next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and str(item.get("id") or "").strip() == normalized
        ),
        None,
    )


def _load_route_plan(plan_id: str) -> Any | None:
    normalized = str(plan_id or "").strip()
    if not normalized:
        return None
    try:
        from agent_loop import load_plan as load_agent_plan
    except ImportError:
        return None
    try:
        return load_agent_plan(normalized)
    except Exception:
        return None


def _normalize_route_step_context(step: Any) -> ReplRouteStepContext | None:
    if step is None:
        return None
    try:
        num = int(getattr(step, "num", 0) or 0)
    except (TypeError, ValueError):
        num = 0
    description = str(getattr(step, "description", "") or "").strip()
    status = str(getattr(step, "status", "") or "").strip()
    if num <= 0 or not description:
        return None
    return ReplRouteStepContext(num=num, description=description, status=status)


def _active_plan_step(plan: Any | None) -> ReplRouteStepContext | None:
    steps = list(getattr(plan, "steps", []) or [])
    if not steps:
        return None
    for step in steps:
        if str(getattr(step, "status", "") or "").strip().lower() == "in-progress":
            return _normalize_route_step_context(step)
    for step in steps:
        if str(getattr(step, "status", "") or "").strip().lower() not in {"done", "failed", "skipped"}:
            return _normalize_route_step_context(step)
    return None


def _find_plan_step_context(plan: Any | None, step_num: int) -> ReplRouteStepContext | None:
    if plan is None or step_num <= 0:
        return None
    for step in list(getattr(plan, "steps", []) or []):
        try:
            current_num = int(getattr(step, "num", 0) or 0)
        except (TypeError, ValueError):
            current_num = 0
        if current_num == step_num:
            return _normalize_route_step_context(step)
    return None


def _load_repl_route_grounding(
    *,
    session_id: str = "",
    session: SessionSummary | None = None,
) -> ReplRouteGrounding | None:
    resolved_session = session
    if resolved_session is None and session_id:
        resolved_session = load_session(session_id)
    if resolved_session is None:
        return None

    plan_id = str(resolved_session.plan_id or "").strip()
    task_id = str(resolved_session.task_id or "").strip()
    if not plan_id and not task_id:
        return None

    plan = _load_route_plan(plan_id) if plan_id else None
    plan_goal = str(getattr(plan, "goal", "") or "").strip()
    if not plan_goal and plan_id:
        plan_goal = str(_validate_plan_id_local(plan_id, cwd=resolved_session.cwd).summary or "").strip()

    task_record = _load_task_record(task_id, cwd=resolved_session.cwd) if task_id else None
    task_title = str((task_record or {}).get("title") or "").strip()
    task_status = str((task_record or {}).get("status") or "").strip()
    task_description = _normalize_prompt_text(
        " ".join(
            str((task_record or {}).get(field) or "").strip()
            for field in ("summary", "description", "notes")
            if str((task_record or {}).get(field) or "").strip()
        )
    )

    return ReplRouteGrounding(
        session_id=resolved_session.session_id,
        cwd=resolved_session.cwd,
        plan_id=plan_id,
        plan_goal=plan_goal,
        task_id=task_id,
        task_title=task_title,
        task_status=task_status,
        task_description=task_description,
        current_step=_active_plan_step(plan),
        plan=plan,
    )


def _link_validation_suffix(result: LocalLinkValidation) -> str:
    if not result.item_id:
        return ""
    if not result.available:
        return " (validation unavailable)"
    if result.exists:
        return " (confirmed locally)"
    return " (warning: not found locally)"


def _render_effective_grounding_preview(
    session: SessionSummary,
    *,
    max_chars: int = CONTEXT_PREVIEW_MAX_CHARS,
) -> str:
    _, workspace_context = collect_workspace_context(
        cwd=session.cwd or None,
        targets=list(session.files),
        max_chars=max_chars,
    )
    sections: list[str] = []
    plan_context = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_context:
        sections.extend(["Plan/task framing:", plan_context])
    if workspace_context:
        sections.extend(["Workspace context:", workspace_context])
    prior_outputs = recent_output_context(session.session_id, max_chars=max(750, min(1_500, max_chars // 3)))
    if prior_outputs:
        sections.extend(["Recent session outputs:", prior_outputs])
    return _truncate_preview("\n\n".join(section for section in sections if section), max_chars=max_chars)


def build_watch_state(
    *,
    session: SessionSummary,
    mode: str,
    goal: str,
    interval_seconds: int,
    max_polls: int,
    on_change: bool,
) -> dict[str, Any]:
    """Create the persisted watch-mode state payload."""
    now = utc_timestamp()
    return {
        "session_id": session.session_id,
        "mode": mode,
        "goal": goal,
        "cwd": session.cwd,
        "files": list(session.files or []),
        "plan_id": session.plan_id,
        "task_id": session.task_id,
        "interval_seconds": interval_seconds,
        "max_polls": max_polls,
        "poll_count": 0,
        "on_change": on_change,
        "status": "idle",
        "created_at": now,
        "updated_at": now,
        "last_run_at": "",
        "last_output_path": "",
        "last_summary": "",
        "last_error": "",
        "workspace_signature": "",
        "failure_count": 0,
        "consecutive_failures": 0,
        "retry_limit": WATCH_RETRY_LIMIT,
        "retry_history": [],
        "progress_log": [],
        "active_checkpoint": {},
        "checkpoints": [],
    }


def normalize_watch_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Backfill watch-state fields introduced after the first CLI releases."""
    normalized = dict(state or {})
    normalized.setdefault("last_error", "")
    normalized.setdefault("failure_count", 0)
    normalized.setdefault("consecutive_failures", 0)
    normalized.setdefault("retry_limit", WATCH_RETRY_LIMIT)
    normalized["retry_limit"] = max(1, int(normalized.get("retry_limit") or WATCH_RETRY_LIMIT))
    normalized["retry_history"] = [
        item for item in list(normalized.get("retry_history") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["progress_log"] = [
        item for item in list(normalized.get("progress_log") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["interventions"] = [
        item for item in list(normalized.get("interventions") or []) if isinstance(item, dict)
    ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["force_run_once"] = bool(normalized.get("force_run_once"))
    normalized["stop_requested"] = bool(normalized.get("stop_requested"))
    normalized["stop_requested_at"] = str(normalized.get("stop_requested_at", "") or "")
    normalized["last_intervention_at"] = str(normalized.get("last_intervention_at", "") or "")
    active_checkpoint = normalized.get("active_checkpoint")
    if not isinstance(active_checkpoint, dict):
        active_checkpoint = {}
    if active_checkpoint:
        active_checkpoint.setdefault("progress", [])
        active_checkpoint["progress"] = [
            item for item in list(active_checkpoint.get("progress") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint.setdefault("attempts", [])
        active_checkpoint["attempts"] = [
            item for item in list(active_checkpoint.get("attempts") or []) if isinstance(item, dict)
        ][-WATCH_PROGRESS_LOG_LIMIT:]
    normalized["active_checkpoint"] = active_checkpoint
    normalized["checkpoints"] = [item for item in list(normalized.get("checkpoints") or []) if isinstance(item, dict)]
    return normalized


def watch_retry_delay_seconds(attempt: int) -> int:
    """Return a capped exponential backoff delay for transient watch retries."""
    return min(WATCH_RETRY_MAX_DELAY_SECONDS, max(1, 2 ** max(0, attempt - 1)))


def is_transient_watch_error(exc: Exception | str) -> bool:
    """Classify whether a watch failure is worth retrying automatically."""
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return any(marker in message for marker in TRANSIENT_WATCH_ERROR_MARKERS)


def start_watch_checkpoint(*, iteration: int, mode: str) -> dict[str, Any]:
    """Create the mutable state object for an in-flight watch checkpoint."""
    now = utc_timestamp()
    return {
        "poll": iteration,
        "mode": mode,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "progress": [],
        "attempts": [],
    }


def record_watch_progress(
    *,
    session_id: str,
    state: dict[str, Any],
    iteration: int,
    mode: str,
    phase: str,
    message: str,
    output_json: bool,
) -> None:
    """Persist and optionally render watch progress updates."""
    entry = {
        "poll": iteration,
        "mode": mode,
        "phase": phase,
        "message": message,
        "created_at": utc_timestamp(),
    }
    progress_log = list(state.get("progress_log") or [])
    progress_log.append(entry)
    state["progress_log"] = progress_log[-WATCH_PROGRESS_LOG_LIMIT:]
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        active_progress = list(active_checkpoint.get("progress") or [])
        active_progress.append(entry)
        active_checkpoint["progress"] = active_progress[-WATCH_PROGRESS_LOG_LIMIT:]
        active_checkpoint["phase"] = phase
        active_checkpoint["last_message"] = message
        active_checkpoint["updated_at"] = entry["created_at"]
    state["updated_at"] = entry["created_at"]
    save_watch_state(session_id, normalize_watch_state(state))
    if not output_json:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim][[/][cyan]{iteration}[/][dim]][/] [dim]{mode}/{phase}:[/] {message}")
        else:
            print(f"[watch {iteration}] {mode}/{phase}: {message}")


def print_watch_resume_snapshot(session_id: str, state: dict[str, Any], *, output_json: bool) -> None:
    """Print the most useful persisted state when resuming a watch session."""
    if output_json:
        return
    status = str(state.get("status") or "unknown").strip() or "unknown"
    poll_count = int(state.get("poll_count") or 0)
    last_summary = str(state.get("last_summary") or "").strip()
    last_error = str(state.get("last_error") or "").strip()
    active_checkpoint = state.get("active_checkpoint")
    recent_progress = list(state.get("progress_log") or [])[-3:]

    if _RICH_AVAILABLE and _IS_TTY:
        emoji = _status_emoji(status)
        border = "green" if status in ("active", "running") else ("yellow" if status in ("paused", "idle") else ("red" if status in ("failed", "error") else "dim"))
        body = _RichText()
        body.append(f"{emoji} status    ", style="dim")
        body.append(f"{status}", style=f"bold {border}")
        body.append(f"\n🔢 polls    ", style="dim")
        body.append(f"{poll_count}", style="cyan")
        if last_summary:
            body.append(f"\n📝 last     ", style="dim")
            body.append(last_summary, style="white")
        if last_error:
            body.append(f"\n⚠️  error    ", style="dim")
            body.append(last_error, style="red")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                body.append(f"\n⏳ partial  ", style="dim")
                body.append(partial, style="yellow")
        if recent_progress:
            body.append(f"\n📋 recent   ", style="dim")
            for entry in recent_progress:
                body.append(f"\n   • {entry.get('message', '')}", style="dim")
        _RICH_CONSOLE.print(_RichPanel(body, title=f"[bold]resuming watch[/] [dim]{session_id}[/]", border_style=border, padding=(0, 1)))
    else:
        print(f"Resuming watch {session_id} (status={status}, completed polls={poll_count}).")
        if last_summary:
            print(f"Last checkpoint: {last_summary}")
        if last_error:
            print(f"Last error: {last_error}")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            partial = str(active_checkpoint.get("last_message") or "").strip()
            if partial:
                print(f"Partial progress: {partial}")
        if recent_progress:
            print("Recent progress:")
            for entry in recent_progress:
                print(f"  - {entry.get('message', '')}")


def refresh_watch_controls(session_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted intervention flags into the in-memory watch state."""
    latest = load_watch_state(session_id)
    if latest is None:
        return state
    latest = normalize_watch_state(latest)
    state["interventions"] = list(latest.get("interventions") or [])
    state["force_run_once"] = bool(latest.get("force_run_once"))
    state["stop_requested"] = bool(latest.get("stop_requested"))
    state["stop_requested_at"] = str(latest.get("stop_requested_at", "") or "")
    state["last_intervention_at"] = str(latest.get("last_intervention_at", "") or "")
    return state


def resolve_watch_intervention(
    state: dict[str, Any],
    *,
    action: str,
    status: str,
    note: str = "",
) -> bool:
    """Resolve the newest pending intervention of the requested action."""
    for item in reversed(list(state.get("interventions") or [])):
        if str(item.get("action") or "") != action or str(item.get("status") or "") != "pending":
            continue
        item["status"] = status
        item["applied_at"] = utc_timestamp()
        if note:
            item["note"] = note[:240]
        return True
    return False


def stop_watch_from_intervention(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    mode: str,
    output_json: bool,
) -> int:
    """Persist a graceful watch stop requested through the dashboard."""
    interrupted_at = utc_timestamp()
    summary = "Watch stopped by dashboard intervention."
    active_checkpoint = state.get("active_checkpoint")
    if isinstance(active_checkpoint, dict) and active_checkpoint:
        partial = str(active_checkpoint.get("last_message") or "").strip()
        active_checkpoint.update(
            {
                "status": "interrupted",
                "completed_at": interrupted_at,
                "summary": partial[:160] if partial else "checkpoint interrupted by dashboard intervention",
            }
        )
        state.setdefault("checkpoints", []).append(dict(active_checkpoint))
        state["active_checkpoint"] = {}
    resolve_watch_intervention(
        state,
        action="graceful-stop",
        status="applied",
        note="Watch loop exited cleanly after dashboard stop request.",
    )
    state["status"] = "interrupted"
    state["updated_at"] = interrupted_at
    state["last_run_at"] = interrupted_at
    state["last_summary"] = summary
    state["stop_requested"] = False
    save_watch_state(session.session_id, state)
    append_event(
        session.session_id,
        kind="intervention",
        content=summary,
        metadata={
            "summary": summary,
            "mode": mode,
            "action": "graceful-stop",
            "plan_id": session.plan_id,
            "task_id": session.task_id,
        },
    )
    update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
    if not output_json:
        _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
    return 0


def render_watch_iteration(
    *,
    iteration: int,
    mode: str,
    summary: str,
    output_path: str,
    output_json: bool,
) -> None:
    """Print a compact watch checkpoint result."""
    payload = {
        "iteration": iteration,
        "mode": mode,
        "summary": summary,
        "saved": output_path,
    }
    if output_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if _RICH_AVAILABLE and _IS_TTY:
        _MODE_COLORS = {"analyze": "cyan", "research": "blue", "write": "yellow"}
        mode_color = _MODE_COLORS.get(str(mode).lower(), "white")
        _RICH_CONSOLE.print(f"\U0001f504 [bold]watch [{iteration}][/]  [{mode_color}]{mode}[/]  [dim]·[/]  {summary}")
        _print_meta_footer(("saved", output_path))
    else:
        print(f"[watch {iteration}] {mode}: {summary}")
        print(f"saved: {output_path}")


def _plan_task_context_snippet(plan_id: str, task_id: str, *, cwd: str | None = None) -> str:
    """Return a concise plan/task framing block for LLM prompts, or '' if neither is set."""
    plan_id = str(plan_id or "").strip()
    task_id = str(task_id or "").strip()
    if not plan_id and not task_id:
        return ""
    lines = ["Active work context:"]
    if plan_id:
        lines.append(f"  Plan: {plan_id}")
        plan_validation = _validate_plan_id_local(plan_id, cwd=cwd)
        if plan_validation.exists and plan_validation.summary:
            lines.append(f"  Plan goal: {plan_validation.summary}")
    if task_id:
        lines.append(f"  Task: {task_id}")
        task_validation = _validate_task_id_local(task_id, cwd=cwd)
        if task_validation.exists and task_validation.summary:
            lines.append(f"  Task detail: {task_validation.summary}")
    lines.append("Keep your response aligned with this plan/task framing.")
    return "\n".join(lines)


def build_analysis_prompt(
    *,
    goal: str,
    context_text: str,
    session: SessionSummary,
) -> str:
    """Build an ask payload for file/directory analysis."""
    prompt_sections = [
        "You are OpenClaw operating in terminal analysis mode.",
        f"Session ID: {session.session_id}",
        f"Goal: {goal}",
        _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd),
        f"Working directory: {session.cwd}",
        "Use the workspace context below to ground your answer. "
        "Focus on findings, risks, and concrete next steps.",
        "Workspace context:",
        context_text,
    ]
    prior_outputs = recent_output_context(session.session_id)
    if prior_outputs:
        prompt_sections.extend(["Recent session outputs:", prior_outputs])
    return "\n\n".join(section for section in prompt_sections if section)


def build_write_prompt(
    *,
    task: str,
    context_text: str,
    session: SessionSummary,
    title: str,
) -> str:
    """Build a writing-oriented ask payload."""
    prompt_sections = [
        "You are OpenClaw operating in document-writing mode.",
        f"Session ID: {session.session_id}",
        f"Document title: {title}",
        f"Writing task: {task}",
        _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd),
        "Return clean markdown suitable for saving to a file. "
        "Prefer clear headings and direct prose.",
        "Workspace context:",
        context_text,
    ]
    prior_outputs = recent_output_context(session.session_id)
    if prior_outputs:
        prompt_sections.extend(["Recent session outputs:", prior_outputs])
    return "\n\n".join(section for section in prompt_sections if section)


def persist_response(session_id: str, prompt: str, response: str) -> None:
    """Persist a prompt/response turn into the local CLI session store."""
    append_event(session_id, kind="user", content=prompt, metadata={"summary": prompt})
    append_event(session_id, kind="assistant", content=response, metadata={"summary": response})


def invoke_openclaw(
    prompt: str,
    *,
    config: CliConfig,
    history: list[dict[str, str]] | None = None,
    opener: Any = request.urlopen,
) -> AskResponse:
    """Submit a prompt to the OpenClaw ask API."""
    payload = {
        "prompt": prompt,
        "model": config.model,
        "history": history or [],
        "user_name": config.user_name,
    }
    req = request.Request(
        f"{config.base_url}/api/agent/ask",
        data=json.dumps(payload).encode("utf-8"),
        headers=attach_session_header(
            build_headers(token=config.token, client_name=config.client_name),
            session_id=config.session_id,
        ),
        method="POST",
    )
    try:
        with opener(req, timeout=config.timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenClawCliError(format_http_error(config.base_url, exc.code, detail)) from exc
    except error.URLError as exc:
        raise OpenClawCliError(format_url_error(config.base_url, exc)) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OpenClawCliError("OpenClaw returned invalid JSON.") from exc

    if not isinstance(data, dict):
        raise OpenClawCliError("OpenClaw returned an unexpected response payload.")

    return AskResponse(
        response=str(data.get("response") or "").strip(),
        model=str(data.get("model") or config.model),
        tokens=int(data.get("tokens") or 0),
        raw=data,
    )


def fetch_health(
    *,
    config: CliConfig,
    opener: Any = request.urlopen,
) -> HealthResponse:
    """Fetch the OpenClaw health endpoint."""
    req = request.Request(
        f"{config.base_url}/health",
        headers=attach_session_header(
            build_headers(token=config.token, client_name=config.client_name),
            session_id=config.session_id,
        ),
        method="GET",
    )
    try:
        with opener(req, timeout=config.timeout_seconds) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenClawCliError(format_http_error(config.base_url, exc.code, detail)) from exc
    except error.URLError as exc:
        raise OpenClawCliError(format_url_error(config.base_url, exc)) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = body.strip()
    status, healthy = analyze_health_payload(payload)
    return HealthResponse(payload=payload, raw_text=body, status=status, healthy=healthy)


def format_http_error(base_url: str, status_code: int, detail: str) -> str:
    """Format actionable HTTP failures from the OpenClaw API."""
    message = detail.strip()
    if message:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            message = str(payload["error"]).strip()
    if status_code == 401:
        return (
            f"OpenClaw rejected the request with 401 Unauthorized. "
            f"{auth_setup_hint()} for {base_url}."
        )
    if not message:
        message = f"HTTP {status_code}"
    return f"OpenClaw request failed ({status_code}): {message}"


def format_url_error(base_url: str, exc: error.URLError) -> str:
    """Format actionable network failures for OpenClaw endpoints."""
    reason = str(getattr(exc, "reason", exc) or "").lower()
    if "timed out" in reason:
        return (
            f"Timed out while contacting OpenClaw at {base_url}. "
            "Check that the service is running and reachable from this machine."
        )
    if "connection refused" in reason or "actively refused" in reason:
        return (
            f"OpenClaw at {base_url} refused the connection. "
            "Check that the service is running and that the URL/port are correct."
        )
    if (
        "nodename nor servname provided" in reason
        or "name or service not known" in reason
        or "getaddrinfo failed" in reason
    ):
        return f"Unable to resolve the OpenClaw host in {base_url}. Check OPENCLAW_URL or pass --url."
    return f"Unable to reach OpenClaw at {base_url}. Set OPENCLAW_URL or pass --url."


def analyze_health_payload(payload: Any) -> tuple[str, bool | None]:
    """Best-effort classification of the /health payload."""
    if isinstance(payload, dict):
        status = str(payload.get("status") or "").strip().lower()
        if status in {"healthy", "ok", "pass"}:
            return status, True
        if status in {"degraded", "down", "fail", "failing", "unhealthy", "error"}:
            return status, False
        checks = payload.get("checks")
        if isinstance(checks, dict):
            normalized = " ".join(str(value).lower() for value in checks.values())
            if any(bad in normalized for bad in ("down", "fail", "unhealthy", "missing")):
                return status or "degraded", False
        return status, None
    if isinstance(payload, str):
        text = payload.strip().lower()
        if not text:
            return "", None
        if text in {"healthy", "ok", "pass"} or "healthy" in text:
            return text, True
        if any(bad in text for bad in ("down", "fail", "unhealthy", "degraded", "error")):
            return text, False
        return text, None
    return "", None


def print_response(response: AskResponse, *, output_json: bool) -> None:
    """Render a response to stdout."""
    if output_json:
        print(json.dumps(response.raw, indent=2, sort_keys=True))
        return
    if response.response:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(_RichMarkdown(response.response))
        else:
            print(response.response)
    if response.model or response.tokens:
        if _RICH_AVAILABLE and _IS_TTY:
            from rich.rule import Rule as _RichRule
            _RICH_CONSOLE.print(_RichRule(style="dim"))
            _RICH_CONSOLE.print(f"[dim]model:[/] [cyan]{response.model}[/]  [dim]tokens:[/] [cyan]{response.tokens}[/]")
        else:
            print()
            print(f"{_DM}[model: {response.model} | tokens: {response.tokens}]{_R}")


def print_health(response: HealthResponse, *, output_json: bool) -> None:
    """Render a health response to stdout."""
    if output_json:
        if isinstance(response.payload, str):
            print(json.dumps({"health": response.payload}, indent=2, sort_keys=True))
            return
        print(json.dumps(response.payload, indent=2, sort_keys=True))
        return
    status = (response.status or "unknown").upper()
    emoji = _status_emoji(response.status or "")
    if _RICH_AVAILABLE and _IS_TTY:
        border = "green" if response.healthy is True else ("yellow" if response.healthy is False else "dim")
        status_style = "bold green" if response.healthy is True else ("bold yellow" if response.healthy is False else "dim")
        t = _RichText()
        t.append(f"{emoji}  OpenClaw  ", style="bold")
        t.append(status, style=status_style)
        if isinstance(response.payload, dict):
            grid = _RichTable.grid(padding=(0, 2))
            grid.add_column(style="dim", min_width=14)
            grid.add_column()
            labels = {
                "uptime_seconds": "uptime",
                "bot_user": "bot",
                "guilds": "guilds",
                "python": "python",
                "discord_py": "discord.py",
            }
            for key, label in labels.items():
                if key in response.payload:
                    val = str(response.payload[key])
                    if key == "uptime_seconds":
                        val = f"{val}s"
                    grid.add_row(label, val)
            checks = response.payload.get("checks")
            if isinstance(checks, dict) and checks:
                for name, value in sorted(checks.items()):
                    chk_emoji = "✅" if str(value).lower() in {"ok", "true", "healthy"} else "⚠️"
                    grid.add_row(f"  {name}", f"{chk_emoji} {value}")
            from rich.console import Group as _RichGroup
            _RICH_CONSOLE.print(_RichPanel(_RichGroup(t, grid), border_style=border, padding=(0, 1)))
        elif isinstance(response.payload, str) and response.payload.strip():
            from rich.console import Group as _RichGroup
            _RICH_CONSOLE.print(_RichPanel(_RichGroup(t, _RichText(response.payload.strip(), style="dim")), border_style=border, padding=(0, 1)))
        else:
            _RICH_CONSOLE.print(_RichPanel(t, border_style=border, padding=(0, 1)))
    else:
        if response.healthy is True:
            prefix = f"{_BGR}OK{_R}"
        elif response.healthy is False:
            prefix = f"{_BYE}WARN{_R}"
        else:
            prefix = f"{_DM}INFO{_R}"
        print(f"{emoji}  {prefix} OpenClaw health: {_B}{status}{_R}")
        if isinstance(response.payload, dict):
            for key in ("uptime_seconds", "bot_user", "guilds", "python", "discord_py"):
                if key in response.payload:
                    val = str(response.payload[key])
                    if key == "uptime_seconds":
                        val = f"{val}s"
                    print(f"  {_DM}{key}:{_R}  {val}")
            checks = response.payload.get("checks")
            if isinstance(checks, dict) and checks:
                for name, value in sorted(checks.items()):
                    print(f"  {_DM}{name}:{_R}  {value}")
            return
        if isinstance(response.payload, str) and response.payload.strip():
            print(response.payload.strip())


def maybe_warn_missing_token(config: CliConfig) -> None:
    """Warn before interactive or one-shot usage when auth is not configured."""
    if config.token:
        return
    print(
        "warning: no OpenClaw API token is configured; requests will likely fail with 401 Unauthorized. "
        + auth_setup_hint(),
        file=sys.stderr,
    )


class ReplRouteKind(str, Enum):
    """Supported route kinds for freeform REPL prompts."""

    CHAT = "chat"
    PLAN = "plan"
    ANALYZE = "analyze"
    RESEARCH = "research"
    WRITE = "write"
    EXEC = "exec"
    EDIT = "edit"


@dataclass(frozen=True)
class ReplPlanStep:
    """A single ordered step inside a multi-step plan candidate."""

    index: int
    kind: ReplRouteKind
    target_text: str
    args_text: str
    rationale: str


@dataclass(frozen=True)
class ReplRouteDecision:
    """Structured routing outcome for a freeform REPL prompt."""

    kind: ReplRouteKind
    confidence: float
    target_text: str
    args_text: str
    rationale: str
    steps: tuple[ReplPlanStep, ...] = ()

    def should_auto_route(self, *, threshold: float = REPL_ROUTE_AUTO_THRESHOLD) -> bool:
        """Return whether this decision is confident enough to auto-route."""
        return (
            self.kind not in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}
            and self.confidence >= threshold
            and bool(self.args_text.strip())
        )

    def should_auto_execute_plan(self, *, threshold: float = REPL_ROUTE_AUTO_THRESHOLD) -> bool:
        """Return whether this decision is a high-confidence multi-step plan."""
        return (
            self.kind == ReplRouteKind.PLAN
            and self.confidence >= threshold
            and len(self.steps) >= 2
        )

    def to_slash_command(self) -> str:
        """Render this decision as an equivalent slash command."""
        if self.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
            return ""
        args = self.args_text.strip()
        return f"/{self.kind.value} {args}".strip()


_ROUTE_DOC_HINTS = (
    "doc",
    "docs",
    "documentation",
    "readme",
    "guide",
    "summary",
    "recap",
    "report",
    "memo",
    "notes",
    "markdown",
    "release notes",
)
_ROUTE_ANALYZE_HINTS = (
    "repo",
    "repository",
    "codebase",
    "file",
    "files",
    "module",
    "modules",
    "directory",
    "workspace",
    "project",
    "architecture",
    "flow",
    "implementation",
)
_ROUTE_SHELL_HINTS = (
    "git",
    "pytest",
    "python",
    "pip",
    "npm",
    "node",
    "go",
    "cargo",
    "make",
    "ls",
    "cat",
    "grep",
    "rg",
    "docker",
    "kubectl",
    "uv",
)
_ROUTE_ACTION_HINTS = (
    "analyze",
    "inspect",
    "review",
    "audit",
    "research",
    "investigate",
    "look up",
    "search",
    "look into",
    "take a look",
    "dig into",
    "write",
    "draft",
    "compose",
    "summarize",
    "summarise",
    "run",
    "execute",
    "command",
    "shell",
    "edit",
    "modify",
    "update",
    "change",
    "append",
    "replace",
    "tweak",
)
_PLAN_ROUTE_SPLIT_RE = re.compile(
    r"\s*(?:;|\band then\b|\bafter that\b|\bafterward(?:s)?\b|\bthen\b|\bfinally\b)\s*",
    re.IGNORECASE,
)
_PLAN_ROUTE_LEAD_RE = re.compile(
    r"^(?:and then|after that|afterward(?:s)?|then|finally|first|second|third|lastly)\b[\s,:-]*",
    re.IGNORECASE,
)
_EDIT_ROUTE_RE = re.compile(
    r"^(?P<verb>edit|modify|update|change|append(?:\s+to)?|tweak)\s+(?:the\s+)?(?:file\s+)?(?P<path>\S+)(?P<rest>.*)$",
    re.IGNORECASE,
)
_PLAN_CREATE_RESULT_RE = re.compile(r"Created plan `([^`]+)`")
_ROUTE_STEP_REF_RE = re.compile(
    r"\bstep\s+(?P<step>(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|eleventh|twelfth))\b",
    re.IGNORECASE,
)
_ROUTE_CURRENT_STEP_RE = re.compile(r"\b(?:the\s+)?current\s+step\b", re.IGNORECASE)
_ROUTE_CURRENT_TASK_RE = re.compile(r"\b(?:the\s+)?current\s+task\b", re.IGNORECASE)
_ROUTE_PROGRESS_PREFIXES = (
    "finish ",
    "complete ",
    "continue ",
    "resume ",
    "work on ",
    "keep working on ",
)
_ROUTE_STEP_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
}


def _normalize_prompt_text(prompt: str) -> str:
    return " ".join(str(prompt or "").strip().split())


def _clean_route_token(token: str) -> str:
    return str(token or "").strip().strip("`'\"()[]{}.,:;")


def _unwrap_route_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"`", "'", '"'}:
        return cleaned[1:-1].strip()
    return cleaned


def _normalize_route_field(text: str) -> str:
    cleaned = _normalize_prompt_text(text)
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {"`", "'", '"'}
        and cleaned.count(cleaned[0]) == 2
    ):
        return cleaned[1:-1].strip()
    return cleaned


def _extract_fenced_route_block(text: str) -> str:
    match = re.search(r"```(?:[a-z0-9_.+-]+)?\s*(.*?)```", str(text or ""), re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _normalize_prompt_text(match.group(1))


def _iter_route_quoted_segments(text: str) -> list[str]:
    raw = str(text or "")
    segments: list[str] = []
    patterns = (
        r"```(?:[a-z0-9_.+-]+)?\s*(.*?)```",
        r"`([^`]+)`",
        r'"([^"]+)"',
        r"'([^']+)'",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw, re.IGNORECASE | re.DOTALL):
            candidate = _normalize_prompt_text(match.group(1))
            if candidate:
                segments.append(candidate)
    return segments


def _shell_split_route_tokens(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _first_shell_token(text: str) -> str:
    parts = _shell_split_route_tokens(text)
    return parts[0] if parts else _normalize_prompt_text(text)


def _shell_quote_route_arg(text: str) -> str:
    return shlex.quote(str(text or ""))


def _looks_like_path(token: str) -> bool:
    candidate = _clean_route_token(token)
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered in {"readme", "readme.md", "makefile", "dockerfile", "pyproject.toml", "package.json"}:
        return True
    return (
        "/" in candidate
        or "\\" in candidate
        or candidate.startswith(".")
        or candidate.startswith("~")
        or bool(re.search(r"\.[a-z0-9]{1,12}$", lowered))
    )


def _extract_first_path(prompt: str) -> str:
    for candidate in _iter_route_quoted_segments(prompt):
        if _looks_like_path(candidate):
            return candidate
    for token in _normalize_prompt_text(prompt).split():
        candidate = _clean_route_token(token)
        if _looks_like_path(candidate):
            return candidate
    return ""


def _strip_request_lead(text: str) -> str:
    stripped = _normalize_prompt_text(text)
    lowered = stripped.lower()
    for prefix in (
        "please ",
        "can you please ",
        "could you please ",
        "would you please ",
        "can you ",
        "could you ",
        "would you ",
    ):
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip()
    return stripped


def _extract_after_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    stripped = _strip_request_lead(text)
    lowered = stripped.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def _strip_route_prefixes(text: str, prefixes: tuple[str, ...]) -> str:
    candidate = _normalize_prompt_text(text).strip(" ,;:")
    lowered = candidate.lower()
    changed = True
    while candidate and changed:
        changed = False
        for prefix in prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix):].strip(" ,;:")
                lowered = candidate.lower()
                changed = True
                break
    return candidate


def _clean_route_fragment(text: str) -> str:
    candidate = _extract_fenced_route_block(text) or _normalize_prompt_text(text)
    candidate = candidate.strip(" ,;:")
    candidate = _normalize_route_field(candidate)
    return candidate.strip(" ,;:")


def _extract_route_quoted_content(text: str, *, exclude: tuple[str, ...] = ()) -> str:
    excluded = {_normalize_prompt_text(item) for item in exclude if item}
    for candidate in _iter_route_quoted_segments(text):
        if candidate not in excluded:
            return candidate
    return ""


def _find_route_path_span(text: str, path: str) -> tuple[int, int] | None:
    if not text or not path:
        return None
    for pattern in (
        rf"([`\"']){re.escape(path)}\1",
        re.escape(path),
    ):
        match = re.search(pattern, text)
        if match:
            return match.span()
    return None


def _extract_append_content(prompt: str, path: str) -> str:
    quoted = _extract_route_quoted_content(prompt, exclude=(path,))
    if quoted:
        return quoted
    span = _find_route_path_span(prompt, path)
    before = prompt[: span[0]] if span else prompt
    after = prompt[span[1]:] if span else ""

    after_candidate = _strip_route_prefixes(
        after,
        ("with ", "containing ", "content ", "contents ", "saying ", "that says ", "to say ", ":", "- ", "to "),
    )
    after_candidate = _clean_route_fragment(after_candidate)
    if after_candidate:
        return after_candidate

    before_candidate = _normalize_prompt_text(before)
    before_candidate = re.sub(r"^(?:append(?:\s+to)?|add)\b", "", before_candidate, flags=re.IGNORECASE).strip(" ,;:")
    before_candidate = re.sub(r"\bto\b$", "", before_candidate, flags=re.IGNORECASE).strip(" ,;:")
    return _clean_route_fragment(before_candidate)


def _extract_replace_values(prompt: str, path: str) -> tuple[str, str] | None:
    if not path:
        return None
    path_pattern = rf"(?:[`\"'])?{re.escape(path)}(?:[`\"'])?(?:[ ,;:.!?]+)?$"
    patterns = (
        rf"\breplace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)\s+(?:in|inside|within)\s+(?:the\s+)?(?:file\s+)?{path_pattern}",
        rf"\bchange\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)\s+(?:in|inside|within)\s+(?:the\s+)?(?:file\s+)?{path_pattern}",
        rf"\b(?:edit|modify|update)\s+(?:the\s+)?(?:file\s+)?(?:[`\"'])?{re.escape(path)}(?:[`\"'])?\s+to\s+replace\s+(?P<old>.+?)\s+with\s+(?P<new>.+?)(?:[ ,;:.!?]+)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if not match:
            continue
        old = _clean_route_fragment(match.group("old"))
        new = _clean_route_fragment(match.group("new"))
        if old and new and old != new:
            return old, new
    return None


def _extract_structured_edit_route(prompt: str) -> tuple[str, str, float, str] | None:
    stripped = _strip_request_lead(prompt)
    lowered = stripped.lower()
    if not re.match(r"^(?:edit|modify|update|change|append(?:\s+to)?|tweak|replace)\b", lowered):
        return None
    path = _extract_first_path(stripped)
    if not path:
        return None

    replace_values = _extract_replace_values(stripped, path)
    if replace_values:
        old, new = replace_values
        args = f"{_shell_quote_route_arg(path)} --replace {_shell_quote_route_arg(old)} {_shell_quote_route_arg(new)}"
        return (
            args,
            path,
            0.95,
            "deterministic match for an explicit file replacement request",
        )

    if lowered.startswith("append"):
        content = _extract_append_content(stripped, path)
        if content:
            args = f"{_shell_quote_route_arg(path)} --append {_shell_quote_route_arg(content)}"
            return (
                args,
                path,
                0.95,
                "deterministic match for an explicit file append request",
            )
        return (
            _shell_quote_route_arg(path),
            path,
            0.68,
            "explicit file-append request matched a file target but inline content was ambiguous",
        )

    edit_match = _EDIT_ROUTE_RE.match(stripped)
    if edit_match:
        rest = edit_match.group("rest").strip()
        if not rest:
            return (
                _shell_quote_route_arg(path),
                path,
                0.96,
                "deterministic match for an explicit file-edit request",
            )
        return (
            _shell_quote_route_arg(path),
            path,
            0.68,
            "explicit file-edit request matched a file target but inline change details were ambiguous",
        )
    return None


def _extract_write_payload(prompt: str) -> tuple[str, str]:
    args = _extract_after_prefix(prompt, ("write ", "draft ", "compose ", "summarize ", "summarise ")) or _normalize_prompt_text(prompt)
    args = _clean_route_fragment(args) or _normalize_prompt_text(prompt)
    target = ""
    patterns = (
        r"\b(?:into|as|for)\s+(?P<target>.+)$",
        r"^(?P<target>.+?)\s+(?:about|from|using|based on|covering)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, args, re.IGNORECASE)
        if not match:
            continue
        candidate = _clean_route_fragment(match.group("target"))
        if candidate and any(hint in candidate.lower() for hint in _ROUTE_DOC_HINTS):
            target = candidate
            break
    if not target:
        match = re.match(
            r"^(?P<target>(?:(?:a|an|the)\s+)?(?:(?:short|brief|concise|detailed|weekly|daily|release|incident|status)\s+)*(?:release notes|summary|recap|report|memo|notes|readme|guide|documentation))\b",
            args,
            re.IGNORECASE,
        )
    if match:
        target = _clean_route_fragment(match.group("target"))
    return args, target


def _parse_route_step_number(raw_step: str) -> int | None:
    token = str(raw_step or "").strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _ROUTE_STEP_WORDS.get(token)


def _remove_route_span(text: str, span: tuple[int, int] | None) -> str:
    if not span:
        return _normalize_prompt_text(text)
    return _normalize_prompt_text(f"{text[: span[0]]} {text[span[1] :]}")


def _grounded_subject_route(subject_text: str) -> ReplRouteDecision | None:
    normalized = _clean_route_fragment(subject_text)
    if not normalized:
        return None
    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None and deterministic.kind != ReplRouteKind.PLAN:
        return deterministic
    classified = lightweight_classify_repl_prompt(normalized)
    if classified is not None and classified.kind not in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return classified
    if any(hint in normalized.lower() for hint in _ROUTE_DOC_HINTS):
        args, target = _extract_write_payload(f"draft {normalized}")
        return _build_route_decision(
            ReplRouteKind.WRITE,
            args_text=target or args or normalized,
            target_text=target or normalized,
            confidence=0.74,
            rationale="grounded subject matched an active document target",
        )
    if any(re.search(rf"\b{re.escape(token)}\b", normalized.lower()) for token in _ROUTE_SHELL_HINTS):
        args = _extract_exec_args(normalized)
        return _build_route_decision(
            ReplRouteKind.EXEC,
            args_text=args,
            target_text=_first_shell_token(args),
            confidence=0.72,
            rationale="grounded subject matched an active shell target",
        )
    path = _extract_first_path(normalized)
    if path:
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=normalized,
            target_text=path,
            confidence=0.68,
            rationale="grounded subject matched an active workspace target",
        )
    return None


def _grounding_intent(prompt: str) -> tuple[ReplRouteKind | None, bool]:
    normalized = _clean_route_fragment(prompt)
    lowered = normalized.lower()
    for pattern, kind in (
        (r"^(?:analyze|inspect|review|audit|take a look at|look into|dig into)\b", ReplRouteKind.ANALYZE),
        (r"^(?:research|investigate|look up|search for|find information on|gather sources on)\b", ReplRouteKind.RESEARCH),
        (r"^(?:write|draft|compose|summarize|summarise)\b", ReplRouteKind.WRITE),
        (r"^(?:run|execute|exec)\b", ReplRouteKind.EXEC),
        (r"^(?:edit|modify|update|change|append|replace|tweak)\b", ReplRouteKind.EDIT),
    ):
        if re.match(pattern, lowered):
            return kind, False
    return None, any(re.match(rf"^{re.escape(prefix.strip())}\b", lowered) for prefix in _ROUTE_PROGRESS_PREFIXES)


def _grounded_prompt_route(prompt: str, subject_text: str) -> ReplRouteDecision | None:
    subject_decision = _grounded_subject_route(subject_text)
    intent_kind, is_progress = _grounding_intent(prompt)
    if intent_kind in {ReplRouteKind.ANALYZE, ReplRouteKind.RESEARCH, ReplRouteKind.WRITE, ReplRouteKind.EXEC}:
        grounded_prompt = f"{intent_kind.value} {subject_text}".strip()
        decision = _grounded_subject_route(grounded_prompt)
        return decision or subject_decision
    if intent_kind == ReplRouteKind.EDIT:
        grounded_prompt = f"update {subject_text}".strip()
        decision = _grounded_subject_route(grounded_prompt)
        if subject_decision is not None and subject_decision.kind in {ReplRouteKind.WRITE, ReplRouteKind.EDIT}:
            return subject_decision if subject_decision.confidence >= getattr(decision, "confidence", 0.0) else decision
        return decision or subject_decision
    if is_progress:
        return subject_decision
    return subject_decision


def _apply_grounding_to_route(
    decision: ReplRouteDecision | None,
    *,
    label: str,
    detail: str,
    boost: float,
) -> ReplRouteDecision | None:
    if decision is None:
        return None
    detail_text = _truncate_repl_route_text(detail, limit=84)
    rationale = f"{decision.rationale}; grounded by {label}: {detail_text}".strip()
    target_text = decision.target_text
    if decision.kind in {ReplRouteKind.ANALYZE, ReplRouteKind.EDIT}:
        grounded_path = _extract_first_path(f"{decision.target_text} {decision.args_text}")
        if grounded_path:
            target_text = grounded_path
    return ReplRouteDecision(
        kind=decision.kind,
        confidence=round(min(0.93, decision.confidence + boost), 2),
        target_text=target_text,
        args_text=decision.args_text,
        rationale=rationale,
        steps=decision.steps,
    )


def _maybe_route_with_grounding(
    prompt: str,
    *,
    grounding: ReplRouteGrounding | None,
) -> ReplRouteDecision | None:
    if grounding is None:
        return None
    normalized = _normalize_prompt_text(prompt)

    step_match = _ROUTE_STEP_REF_RE.search(normalized)
    if step_match and grounding.plan is not None:
        step_num = _parse_route_step_number(step_match.group("step"))
        step = _find_plan_step_context(grounding.plan, step_num or 0)
        if step is not None:
            remainder = _remove_route_span(normalized, step_match.span())
            decision = _grounded_prompt_route(remainder, step.description)
            return _apply_grounding_to_route(
                decision,
                label=f"active plan {grounding.plan_id} step {step.num}",
                detail=step.description,
                boost=0.15,
            )

    current_step_match = _ROUTE_CURRENT_STEP_RE.search(normalized)
    if current_step_match and grounding.current_step is not None:
        remainder = _remove_route_span(normalized, current_step_match.span())
        decision = _grounded_prompt_route(remainder, grounding.current_step.description)
        return _apply_grounding_to_route(
            decision,
            label=f"current step in plan {grounding.plan_id or 'session'}",
            detail=grounding.current_step.description,
            boost=0.12,
        )

    current_task_match = _ROUTE_CURRENT_TASK_RE.search(normalized)
    subject_text = grounding.task_title or grounding.task_description
    if current_task_match and subject_text:
        remainder = _remove_route_span(normalized, current_task_match.span())
        decision = _grounded_prompt_route(remainder, subject_text)
        detail = subject_text
        if grounding.task_status:
            detail = f"{detail} ({grounding.task_status})"
        return _apply_grounding_to_route(
            decision,
            label=f"active task {grounding.task_id or 'session task'}",
            detail=detail,
            boost=0.08,
        )

    return None


def _clean_plan_clause(text: str) -> str:
    clause = _normalize_prompt_text(text).strip(" ,;:.?!")
    if not clause:
        return ""
    clause = _PLAN_ROUTE_LEAD_RE.sub("", clause).strip(" ,;:.?!")
    clause = _strip_request_lead(clause)
    return clause.strip(" ,;:.?!")


def _classify_repl_clause(clause: str) -> ReplRouteDecision | None:
    normalized = _clean_plan_clause(clause)
    if not normalized:
        return None
    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None and deterministic.kind != ReplRouteKind.PLAN:
        return deterministic
    if not _looks_action_like(normalized):
        return None
    classified = lightweight_classify_repl_prompt(normalized)
    if classified is None or classified.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return None
    return classified


def _build_chat_route(prompt: str, rationale: str, *, confidence: float = 0.0) -> ReplRouteDecision:
    return ReplRouteDecision(
        kind=ReplRouteKind.CHAT,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        target_text="",
        args_text=_normalize_prompt_text(prompt),
        rationale=rationale,
    )


def _build_route_decision(
    kind: ReplRouteKind,
    *,
    args_text: str,
    target_text: str = "",
    confidence: float,
    rationale: str,
    steps: tuple[ReplPlanStep, ...] = (),
) -> ReplRouteDecision:
    normalized_args = (
        _normalize_prompt_text(args_text)
        if kind in {ReplRouteKind.EXEC, ReplRouteKind.EDIT}
        else _normalize_route_field(args_text)
    )
    return ReplRouteDecision(
        kind=kind,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        target_text=_normalize_route_field(target_text) or normalized_args,
        args_text=normalized_args,
        rationale=rationale,
        steps=steps,
    )


def _maybe_build_plan_route(prompt: str, *, min_confidence: float) -> ReplRouteDecision | None:
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return None
    if not _PLAN_ROUTE_SPLIT_RE.search(normalized):
        return None

    clauses = [_clean_plan_clause(part) for part in _PLAN_ROUTE_SPLIT_RE.split(normalized)]
    clauses = [clause for clause in clauses if clause]
    if len(clauses) < 2:
        return None

    step_decisions: list[ReplRouteDecision] = []
    for clause in clauses:
        step_decision = _classify_repl_clause(clause)
        if step_decision is None:
            return None
        step_decisions.append(step_decision)

    step_confidences = [decision.confidence for decision in step_decisions]
    if min(step_confidences) < 0.58:
        return None

    unique_kinds = {decision.kind for decision in step_decisions}
    confidence = sum(step_confidences) / len(step_confidences)
    confidence += 0.08
    confidence += min(0.08, 0.04 * (len(step_decisions) - 1))
    if len(unique_kinds) > 1:
        confidence += 0.03
    confidence = round(min(0.97, confidence), 2)
    if confidence < min_confidence:
        return None

    steps = tuple(
        ReplPlanStep(
            index=index + 1,
            kind=decision.kind,
            target_text=decision.target_text,
            args_text=decision.args_text,
            rationale=decision.rationale,
        )
        for index, decision in enumerate(step_decisions)
    )
    rationale = "decomposition matched ordered action clauses via sequencing markers"
    return _build_route_decision(
        ReplRouteKind.PLAN,
        args_text=normalized,
        target_text=steps[0].target_text or steps[0].args_text,
        confidence=confidence,
        rationale=rationale,
        steps=steps,
    )


def _deterministic_repl_route(prompt: str) -> ReplRouteDecision | None:
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return None

    exec_args = _extract_after_prefix(normalized, ("run ", "execute ", "exec "))
    if exec_args:
        exec_args = _extract_exec_args(normalized)
        return _build_route_decision(
            ReplRouteKind.EXEC,
            args_text=exec_args,
            target_text=_first_shell_token(exec_args),
            confidence=0.98,
            rationale="deterministic match for an explicit run/execute request",
        )

    structured_edit = _extract_structured_edit_route(normalized)
    if structured_edit is not None:
        edit_args, path, confidence, rationale = structured_edit
        return _build_route_decision(
            ReplRouteKind.EDIT,
            args_text=edit_args,
            target_text=path,
            confidence=confidence,
            rationale=rationale,
        )

    research_args = _extract_after_prefix(
        normalized,
        (
            "research ",
            "investigate ",
            "look up ",
            "search for ",
            "find information on ",
            "gather sources on ",
        ),
    )
    if research_args:
        return _build_route_decision(
            ReplRouteKind.RESEARCH,
            args_text=research_args,
            confidence=0.95,
            rationale="deterministic match for an explicit research request",
        )

    analyze_args = _extract_after_prefix(normalized, ("analyze ",))
    if analyze_args:
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=analyze_args,
            confidence=0.95,
            rationale="deterministic match for an explicit analyze request",
        )

    soft_analyze_args = _extract_after_prefix(normalized, ("inspect ", "review ", "audit "))
    if soft_analyze_args and (_extract_first_path(soft_analyze_args) or any(hint in soft_analyze_args.lower() for hint in _ROUTE_ANALYZE_HINTS)):
        return _build_route_decision(
            ReplRouteKind.ANALYZE,
            args_text=soft_analyze_args,
            confidence=0.88,
            rationale="deterministic match for a workspace inspection request",
        )

    write_args, write_target = _extract_write_payload(normalized)
    if write_args and any(hint in write_args.lower() for hint in _ROUTE_DOC_HINTS):
        return _build_route_decision(
            ReplRouteKind.WRITE,
            args_text=write_args,
            target_text=write_target,
            confidence=0.9,
            rationale="deterministic match for a document-writing request",
        )

    return None


def _looks_action_like(prompt: str) -> bool:
    lowered = _normalize_prompt_text(prompt).lower()
    return bool(_extract_first_path(lowered)) or any(hint in lowered for hint in _ROUTE_ACTION_HINTS)


def _extract_exec_args(prompt: str) -> str:
    raw = str(prompt or "").strip()
    fenced = _extract_fenced_route_block(raw)
    if fenced:
        return fenced
    normalized = _normalize_prompt_text(raw)
    inline = re.search(r"`([^`]+)`", normalized)
    if inline:
        return _normalize_prompt_text(inline.group(1))
    explicit = _extract_after_prefix(raw, ("run ", "execute ", "exec "))
    if explicit:
        fenced = _extract_fenced_route_block(explicit)
        if fenced:
            return fenced
        inline = re.search(r"`([^`]+)`", explicit)
        if inline:
            return _normalize_prompt_text(inline.group(1))
        return _normalize_prompt_text(explicit)
    words = normalized.split()
    for index, token in enumerate(words):
        cleaned = _clean_route_token(token).lower()
        if cleaned in _ROUTE_SHELL_HINTS:
            return " ".join(words[index:]).strip()
    return normalized


def _extract_route_payload(kind: ReplRouteKind, prompt: str) -> tuple[str, str]:
    normalized = _normalize_prompt_text(prompt)
    if kind == ReplRouteKind.EXEC:
        args = _extract_exec_args(normalized)
        return args, _first_shell_token(args)
    if kind == ReplRouteKind.EDIT:
        structured = _extract_structured_edit_route(normalized)
        if structured is not None:
            args, path, _confidence, _rationale = structured
            return args, path
        path = _extract_first_path(normalized)
        return (_shell_quote_route_arg(path), path) if path else (normalized, "")
    if kind == ReplRouteKind.ANALYZE:
        args = _extract_after_prefix(normalized, ("analyze ", "inspect ", "review ", "audit ", "take a look at ", "look into ", "dig into "))
        return (args or normalized, _extract_first_path(args or normalized))
    if kind == ReplRouteKind.RESEARCH:
        args = _extract_after_prefix(
            normalized,
            ("research ", "investigate ", "look up ", "search for ", "find information on ", "gather sources on "),
        )
        return (args or normalized, "")
    if kind == ReplRouteKind.WRITE:
        return _extract_write_payload(normalized)
    return normalized, ""


def lightweight_classify_repl_prompt(prompt: str) -> ReplRouteDecision | None:
    """Classify action-like prompts with lightweight keyword scoring."""
    normalized = _normalize_prompt_text(prompt)
    lowered = normalized.lower()
    if not normalized:
        return None

    scores: dict[ReplRouteKind, float] = {
        ReplRouteKind.ANALYZE: 0.0,
        ReplRouteKind.RESEARCH: 0.0,
        ReplRouteKind.WRITE: 0.0,
        ReplRouteKind.EXEC: 0.0,
        ReplRouteKind.EDIT: 0.0,
    }
    reasons: dict[ReplRouteKind, list[str]] = {kind: [] for kind in scores}

    def add(kind: ReplRouteKind, weight: float, reason: str) -> None:
        scores[kind] += weight
        reasons[kind].append(reason)

    for phrase, weight in (
        ("take a look", 0.5),
        ("look into", 0.45),
        ("dig into", 0.45),
        ("inspect", 0.4),
        ("review", 0.35),
        ("audit", 0.4),
        ("explain", 0.2),
    ):
        if phrase in lowered:
            add(ReplRouteKind.ANALYZE, weight, phrase)

    for phrase, weight in (
        ("research", 0.5),
        ("investigate", 0.4),
        ("look up", 0.38),
        ("search for", 0.35),
        ("sources", 0.2),
        ("compare", 0.18),
    ):
        if phrase in lowered:
            add(ReplRouteKind.RESEARCH, weight, phrase)

    for phrase, weight in (
        ("draft", 0.45),
        ("compose", 0.4),
        ("write", 0.25),
        ("summarize", 0.22),
        ("summarise", 0.22),
    ):
        if phrase in lowered:
            add(ReplRouteKind.WRITE, weight, phrase)

    for phrase, weight in (
        ("run", 0.25),
        ("execute", 0.4),
        ("shell", 0.25),
        ("command", 0.22),
        ("show me", 0.15),
    ):
        if phrase in lowered:
            add(ReplRouteKind.EXEC, weight, phrase)

    for phrase, weight in (
        ("edit", 0.45),
        ("modify", 0.38),
        ("update", 0.28),
        ("change", 0.22),
        ("append", 0.25),
        ("replace", 0.42),
        ("tweak", 0.3),
    ):
        if phrase in lowered:
            add(ReplRouteKind.EDIT, weight, phrase)

    if any(hint in lowered for hint in _ROUTE_ANALYZE_HINTS):
        add(ReplRouteKind.ANALYZE, 0.18, "workspace hint")
    if any(hint in lowered for hint in _ROUTE_DOC_HINTS):
        add(ReplRouteKind.WRITE, 0.22, "document hint")
    if any(re.search(rf"\b{re.escape(token)}\b", lowered) for token in _ROUTE_SHELL_HINTS):
        add(ReplRouteKind.EXEC, 0.2, "shell token")
    path = _extract_first_path(normalized)
    if path:
        add(ReplRouteKind.EDIT, 0.22, "path target")
        add(ReplRouteKind.ANALYZE, 0.18, "path target")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_kind, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 0.58:
        return None

    confidence = best_score
    if best_score - runner_up < 0.12:
        confidence -= 0.12 - (best_score - runner_up)
    confidence = max(0.0, min(0.92, confidence))
    args_text, target_text = _extract_route_payload(best_kind, normalized)
    rationale_bits = ", ".join(reasons[best_kind][:3]) or "keyword scoring"
    return _build_route_decision(
        best_kind,
        args_text=args_text,
        target_text=target_text,
        confidence=confidence,
        rationale=f"lightweight classifier matched {rationale_bits}",
    )


def route_repl_prompt(
    prompt: str,
    *,
    classifier_func: Callable[[str], ReplRouteDecision | None] = lightweight_classify_repl_prompt,
    min_confidence: float = REPL_ROUTE_AUTO_THRESHOLD,
    session_id: str = "",
    session: SessionSummary | None = None,
) -> ReplRouteDecision:
    """Decide how a freeform REPL prompt should be handled."""
    normalized = _normalize_prompt_text(prompt)
    if not normalized:
        return _build_chat_route(prompt, "empty prompt")

    grounded = _maybe_route_with_grounding(
        normalized,
        grounding=_load_repl_route_grounding(session_id=session_id, session=session),
    )
    if grounded is not None:
        return grounded

    decomposed = _maybe_build_plan_route(normalized, min_confidence=min_confidence)
    if decomposed is not None:
        return decomposed

    deterministic = _deterministic_repl_route(normalized)
    if deterministic is not None:
        return deterministic

    if not _looks_action_like(normalized):
        return _build_chat_route(normalized, "defaulting to chat for a conversational prompt")

    classified = classifier_func(normalized) if classifier_func is not None else None
    if classified is None:
        return _build_chat_route(normalized, "defaulting to chat because no confident action route was found")
    if classified.kind == ReplRouteKind.PLAN:
        if classified.confidence >= min_confidence and len(classified.steps) >= 2:
            return classified
        return _build_chat_route(
            normalized,
            f"{classified.rationale}; confidence {classified.confidence:.2f} below plan threshold {min_confidence:.2f}",
            confidence=classified.confidence,
        )
    if classified.should_auto_route(threshold=min_confidence):
        return classified
    return _build_chat_route(
        normalized,
        f"{classified.rationale}; confidence {classified.confidence:.2f} below auto-route threshold {min_confidence:.2f}",
        confidence=classified.confidence,
    )


def _truncate_repl_route_text(text: str, *, limit: int) -> str:
    compact = _normalize_prompt_text(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _session_auto_route_enabled(session_id: str) -> bool:
    if not session_id:
        return False
    session = load_session(session_id)
    if session is None:
        return False
    return bool(getattr(session, "repl_auto_route", True))


def _format_route_announcement(decision: ReplRouteDecision) -> str:
    if decision.kind == ReplRouteKind.PLAN:
        step_summary = " → ".join(f"{step.index}:{step.kind.value}" for step in decision.steps)
        preview = _truncate_repl_route_text(step_summary, limit=REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT)
        rationale = _truncate_repl_route_text(
            decision.rationale,
            limit=REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT,
        )
        return (
            f"{_BYE}⚡ plan{_R} {_YE}{len(decision.steps)} steps ({preview}){_R}  "
            f"{_DM}confidence {decision.confidence:.2f} · {rationale}{_R}"
        )
    slash_command = _truncate_repl_route_text(
        decision.to_slash_command(),
        limit=REPL_ROUTE_ANNOUNCEMENT_COMMAND_LIMIT,
    )
    rationale = _truncate_repl_route_text(
        decision.rationale,
        limit=REPL_ROUTE_ANNOUNCEMENT_REASON_LIMIT,
    )
    return (
        f"{_BYE}⚡ auto-route{_R} {_CY}→ {slash_command}{_R}  "
        f"{_DM}confidence {decision.confidence:.2f} · {rationale}{_R}"
    )


def _append_repl_route_event(session_id: str, prompt: str, decision: ReplRouteDecision) -> None:
    slash_command = decision.to_slash_command()
    summary_prefix = (
        f"plan candidate with {len(decision.steps)} steps ({decision.confidence:.2f})"
        if decision.kind == ReplRouteKind.PLAN
        else f"auto-routed to {slash_command} ({decision.confidence:.2f})"
    )
    append_event(
        session_id,
        kind="route",
        content=prompt,
        metadata={
            "summary": _truncate_repl_route_text(
                summary_prefix,
                limit=90,
            ),
            "source": "repl.autoroute",
            "route_kind": decision.kind.value,
            "slash_command": slash_command,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "target_text": decision.target_text,
            "args_text": decision.args_text,
            "steps": [
                {
                    "index": step.index,
                    "kind": step.kind.value,
                    "target_text": step.target_text,
                    "args_text": step.args_text,
                    "rationale": step.rationale,
                }
                for step in decision.steps
            ],
        },
    )


def _summarize_terminal_result(text: str, *, fallback: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return fallback
    if len(compact) <= 180:
        return compact
    return compact[:179].rstrip() + "…"


def _plan_step_slash_command(step: ReplPlanStep) -> str:
    if step.kind in {ReplRouteKind.CHAT, ReplRouteKind.PLAN}:
        return ""
    args = step.args_text.strip()
    return f"/{step.kind.value} {args}".strip()


def _extract_created_plan_id(create_result: str) -> str:
    match = _PLAN_CREATE_RESULT_RE.search(str(create_result or ""))
    return match.group(1).strip() if match else ""


def _load_plan_storage_helpers() -> tuple[Any, Any]:
    try:
        from agent_loop import load_plan, save_plan
    except ImportError:
        return None, None
    return load_plan, save_plan


def _create_persisted_plan(
    *,
    goal: str,
    steps: tuple[ReplPlanStep, ...] = (),
    steps_text: str = "",
    session_id: str = "",
) -> tuple[str, str]:
    try:
        from agent_loop import create_plan
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw plan")) from exc

    step_commands = [
        line.strip()
        for line in (
            steps_text.strip().splitlines()
            if steps_text.strip()
            else [_plan_step_slash_command(step) for step in steps]
        )
        if line.strip()
    ]
    create_result = str(run_async(create_plan(goal, steps_text="\n".join(step_commands))))
    plan_id = _extract_created_plan_id(create_result)
    if not plan_id:
        raise OpenClawCliError(create_result or "Unable to determine the created plan id.")

    if session_id:
        session = update_session(session_id, plan_id=plan_id)
        load_plan, save_plan = _load_plan_storage_helpers()
        if load_plan is not None and save_plan is not None:
            plan = load_plan(plan_id)
            if plan is not None:
                plan.context["session_id"] = session.session_id
                plan.context["cwd"] = session.cwd
                if session.files:
                    plan.context["files"] = "\n".join(session.files[:20])
                save_plan(plan)
        append_event(
            session_id,
            kind="plan",
            content=goal,
            metadata={
                "plan_id": plan_id,
                "summary": f"created plan {plan_id}",
                "steps": step_commands,
                "source": "repl.autoroute" if steps else "cli.plan",
            },
        )
    return plan_id, create_result


def _update_persisted_plan_step(
    plan_id: str,
    step_num: int,
    *,
    status: str,
    output: str = "",
    session_id: str = "",
) -> None:
    load_plan, save_plan = _load_plan_storage_helpers()
    if load_plan is None or save_plan is None or not plan_id:
        return
    plan = load_plan(plan_id)
    if plan is None:
        return
    step = next((item for item in plan.steps if getattr(item, "num", 0) == step_num), None)
    if step is None:
        return
    step.status = status
    if output:
        step.output = str(output)[:2_000]
        if status == "done":
            plan.context[f"step_{step_num}_output"] = str(output)[:2_000]
    if session_id:
        plan.context["session_id"] = session_id
    if status == "failed":
        plan.status = "interrupted"
    elif all(getattr(item, "is_complete", False) for item in plan.steps):
        plan.status = "completed"
    else:
        plan.status = "in-progress"
    save_plan(plan)


def _execute_routed_plan(
    *,
    prompt: str,
    decision: ReplRouteDecision,
    registry: "ChatCommandRegistry",
    ctx: "ChatCommandContext",
) -> str:
    session = _require_session_or_warn(ctx)
    if session is None:
        return ""

    plan_id, create_result = _create_persisted_plan(goal=prompt, steps=decision.steps, session_id=session.session_id)
    total = len(decision.steps)
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(_RichPanel(f"📋 Plan [yellow]{plan_id}[/] · [dim]{total} steps[/]", border_style="dim"))
    else:
        print(f"plan {plan_id}: {total} steps")
    ctx.history[:] = load_conversation_history(session.session_id)
    for step in decision.steps:
        slash_command = _plan_step_slash_command(step)
        if not slash_command:
            summary = f"step {step.index} has no executable slash command"
            _update_persisted_plan_step(plan_id, step.index, status="failed", output=summary, session_id=session.session_id)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [red]✗ failed:[/] {summary}")
            else:
                print(f"[{step.index}/{total}] failed: {summary}")
            return _CMD_CONTINUE
        _update_persisted_plan_step(plan_id, step.index, status="in-progress", session_id=session.session_id)
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [cyan]{slash_command}[/]")
        else:
            print(f"[{step.index}/{total}] {slash_command}")
        routed = registry.dispatch(slash_command, ctx)
        ctx.history[:] = load_conversation_history(session.session_id)
        summary = ctx.command_summary or _summarize_terminal_result(
            slash_command,
            fallback=f"step {step.index} complete",
        )
        if routed == _CMD_QUIT:
            _update_persisted_plan_step(plan_id, step.index, status="failed", output="execution aborted", session_id=session.session_id)
            return _CMD_QUIT
        if routed is None or not ctx.command_ok:
            _update_persisted_plan_step(plan_id, step.index, status="failed", output=summary, session_id=session.session_id)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"  [dim][{step.index}/{total}][/] [red]✗ failed:[/] {summary}")
            else:
                print(f"[{step.index}/{total}] failed: {summary}")
            return _CMD_CONTINUE
        _update_persisted_plan_step(plan_id, step.index, status="done", output=summary, session_id=session.session_id)
    update_session(session.session_id, plan_id=plan_id)
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# Slash-command dispatcher
# ---------------------------------------------------------------------------

_CMD_CONTINUE: str = "continue"  # sentinel: command handled, keep the REPL loop
_CMD_QUIT: str = "quit"           # sentinel: command handled, exit the REPL


@dataclass
class ChatCommandContext:
    """Mutable context passed to every slash-command handler."""

    history: list[dict[str, str]]
    session_id: str
    args: str = ""  # text after the command name, stripped
    config: Any = None  # CliConfig instance when running inside run_chat
    route_metadata: dict[str, Any] | None = None
    command_ok: bool = True
    command_summary: str = ""


@dataclass
class SlashCommand:
    """A single registered slash command with optional aliases."""

    name: str
    description: str
    handler: Callable[["ChatCommandContext"], str]
    aliases: tuple[str, ...] = ()


class ChatCommandRegistry:
    """Maps slash-command names (without the leading /) to handlers."""

    def __init__(self) -> None:
        self._commands: list[SlashCommand] = []
        self._lookup: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands.append(cmd)
        self._lookup[cmd.name] = cmd
        for alias in cmd.aliases:
            self._lookup[alias] = cmd

    def dispatch(self, text: str, ctx: ChatCommandContext) -> str | None:
        """Route *text* to a handler if it starts with '/'.

        Returns a sentinel string (_CMD_CONTINUE or _CMD_QUIT) when handled,
        or None when the text is not a recognised slash command.

        Text after the command name is placed in ``ctx.args`` so handlers can
        accept optional arguments without needing separate registry entries.
        """
        if not text.startswith("/"):
            return None
        parts = text[1:].split(maxsplit=1)
        cmd_name = parts[0] if parts else ""
        if not cmd_name:
            return None
        cmd = self._lookup.get(cmd_name)
        if cmd is None:
            return None
        ctx.args = parts[1] if len(parts) > 1 else ""
        ctx.command_ok = True
        ctx.command_summary = ""
        return cmd.handler(ctx)

    def list_commands(self) -> list[SlashCommand]:
        """Return the primary commands in registration order."""
        return list(self._commands)


def _routed_plan_metadata(ctx: ChatCommandContext) -> dict[str, Any]:
    metadata = ctx.route_metadata if isinstance(ctx.route_metadata, dict) else {}
    if str(metadata.get("source") or "").strip().lower() != "repl.plan":
        return {}
    return metadata


def _routed_plan_step_label(metadata: dict[str, Any]) -> str:
    step_index = int(metadata.get("step_index") or 0)
    step_total = int(metadata.get("step_total") or 0)
    if step_index > 0 and step_total > 0:
        return f"routed plan step {step_index}/{step_total}"
    return "routed plan step"


def _capture_routed_action_checkpoint(
    ctx: ChatCommandContext,
    *,
    session: SessionSummary,
    action_kind: str,
    target: str,
    detail: str,
    file_paths: list[str] | None = None,
) -> bool:
    metadata = _routed_plan_metadata(ctx)
    if not metadata:
        return True
    workspace_targets = list(file_paths or session.files)
    try:
        checkpoint = create_routed_action_checkpoint(
            session.session_id,
            action_kind=action_kind,
            target=target,
            detail=detail,
            cwd=session.cwd,
            route_metadata=metadata,
            file_paths=file_paths,
            workspace_signature=build_workspace_signature(
                cwd=session.cwd or None,
                targets=workspace_targets or None,
            ),
        )
    except Exception as exc:
        _print_error(f"unable to capture safety checkpoint for {_routed_plan_step_label(metadata)}: {exc}")
        _set_command_result(ctx, ok=False, summary=f"checkpoint failed: {exc}")
        return False
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"  [green]✓[/] checkpoint [dim]{checkpoint['checkpoint_id']}[/] captured · [dim]use /rollback last to recover[/]")
    else:
        print(
            f"Checkpoint {checkpoint['checkpoint_id']} captured for {_routed_plan_step_label(metadata)}. "
            "Use /rollback last to recover."
        )
    return True


def _cmd_quit(ctx: ChatCommandContext) -> str:
    return _CMD_QUIT


def _cmd_help(ctx: ChatCommandContext) -> str:
    print_chat_help()
    return _CMD_CONTINUE


def _set_command_result(ctx: ChatCommandContext, *, ok: bool, summary: str = "") -> None:
    ctx.command_ok = ok
    ctx.command_summary = str(summary or "").strip()


def _cmd_clear(ctx: ChatCommandContext) -> str:
    n = len(ctx.history)
    ctx.history.clear()
    if ctx.session_id:
        append_event(
            ctx.session_id,
            kind="chat",
            content="/clear",
            metadata={"summary": "cleared chat history"},
        )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[green]✓[/] conversation cleared  [dim]({n} message(s) removed)[/]")
    else:
        print(f"Conversation history cleared. ({n} messages removed).")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# Session / context inspection and mutation commands
# ---------------------------------------------------------------------------

def _require_session_or_warn(ctx: ChatCommandContext) -> "SessionSummary | None":
    """Load the active session, printing a warning when none is set."""
    if not ctx.session_id:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[yellow]⚠[/]  no active session  [dim]·  openclaw --session <id>  or  openclaw session create[/]")
        else:
            print("No active session. Start with: openclaw --session <id> or openclaw session create")
        _set_command_result(ctx, ok=False, summary="no active session")
        return None
    session = load_session(ctx.session_id)
    if session is None:
        _print_error(f"session '{ctx.session_id}' not found")
        _set_command_result(ctx, ok=False, summary=f"session '{ctx.session_id}' not found")
        return None
    return session


def _cmd_session(ctx: ChatCommandContext) -> str:
    """/session — show a compact summary of the current session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _print_session_summary(session)
    return _CMD_CONTINUE


def _cmd_context(ctx: ChatCommandContext) -> str:
    """/context — show the effective local grounding for the active session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    if _RICH_AVAILABLE and _IS_TTY:
        from rich.table import Table as _RichTableLocal
        grid = _RichText()
        grid.append("📁 cwd    ", style="dim")
        grid.append(session.cwd or "(none)", style="bold")
        grid.append("\n")
        if session.files:
            grid.append("📄 files  ", style="dim")
            grid.append("\n")
            for f in session.files:
                grid.append(f"   {f}\n", style="cyan")
        else:
            grid.append("📄 files  ", style="dim")
            grid.append("(none tracked)\n", style="dim italic")
        if session.plan_id:
            plan_validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
            suffix = _link_validation_suffix(plan_validation)
            grid.append("📋 plan   ", style="dim")
            grid.append(f"{session.plan_id}{suffix}\n", style="yellow")
        if session.task_id:
            task_validation = _validate_task_id_local(session.task_id, cwd=session.cwd)
            suffix = _link_validation_suffix(task_validation)
            grid.append("✅ task   ", style="dim")
            grid.append(f"{session.task_id}{suffix}\n", style="yellow")
        grounding_preview = _render_effective_grounding_preview(session)
        if grounding_preview:
            grid.append("\neffective grounding preview:\n", style="dim italic")
            grid.append(grounding_preview, style="dim")
        _RICH_CONSOLE.print(_RichPanel(grid, title="[bold]context[/]", border_style="dim", padding=(0, 1)))
    else:
        lines = [f"cwd  : {session.cwd}"]
        if session.files:
            lines.append("files:")
            for f in session.files:
                lines.append(f"  {f}")
        else:
            lines.append("files: (none tracked)")
        if session.plan_id:
            plan_validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
            lines.append(f"plan : {session.plan_id}{_link_validation_suffix(plan_validation)}")
        if session.task_id:
            task_validation = _validate_task_id_local(session.task_id, cwd=session.cwd)
            lines.append(f"task : {session.task_id}{_link_validation_suffix(task_validation)}")
        grounding_preview = _render_effective_grounding_preview(session)
        if grounding_preview:
            lines.extend(["", "effective grounding preview:", grounding_preview])
        print("\n".join(lines))
    return _CMD_CONTINUE


def _cmd_cwd(ctx: ChatCommandContext) -> str:
    """/cwd [path] — show or switch the session working directory."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    new_path = ctx.args.strip()
    if not new_path:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]cwd[/]  {session.cwd}")
        else:
            print(f"cwd: {session.cwd}")
        return _CMD_CONTINUE
    resolved = str(Path(new_path).expanduser().resolve())
    if not Path(resolved).is_dir():
        _print_error(f"not a directory: {resolved}")
        return _CMD_CONTINUE
    update_session(ctx.session_id, cwd=resolved)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/cwd {new_path}",
        metadata={"summary": f"switched cwd to {resolved}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[dim]cwd[/] [green]→[/] {resolved}")
    else:
        print(f"cwd → {resolved}")
    return _CMD_CONTINUE


def _cmd_files(ctx: ChatCommandContext) -> str:
    """/files [add <path> | rm <path>] — list, add, or remove tracked files."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    raw = ctx.args.strip()
    if not raw:
        # List mode
        if not session.files:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No tracked files.[/]")
            else:
                print("No tracked files.")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                for f in session.files:
                    _RICH_CONSOLE.print(f"  [cyan]📄[/] {f}")
            else:
                for f in session.files:
                    print(f"  {f}")
        return _CMD_CONTINUE

    parts = raw.split(maxsplit=1)
    subcmd = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("add", "+"):
        if not target:
            print("Usage: /files add <path>")
            return _CMD_CONTINUE
        resolved = str(Path(target).expanduser().resolve())
        current = list(session.files)
        if resolved in current:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]already tracked:[/] {resolved}")
            else:
                print(f"Already tracked: {resolved}")
            return _CMD_CONTINUE
        current.append(resolved)
        update_session(ctx.session_id, files=current)
        append_event(
            ctx.session_id,
            kind="chat",
            content=f"/files add {target}",
            metadata={"summary": f"added file {resolved}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] tracked: {resolved}")
        else:
            print(f"tracked: {resolved}")

    elif subcmd in ("rm", "remove", "-"):
        if not target:
            print("Usage: /files rm <path>")
            return _CMD_CONTINUE
        resolved = str(Path(target).expanduser().resolve())
        current = list(session.files)
        # Match on resolved path or basename
        matched = [f for f in current if f == resolved or f == target or Path(f).name == target]
        if not matched:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"[yellow]not tracked:[/] {target}")
            else:
                print(f"Not tracked: {target}")
            return _CMD_CONTINUE
        for m in matched:
            current.remove(m)
        update_session(ctx.session_id, files=current)
        append_event(
            ctx.session_id,
            kind="chat",
            content=f"/files rm {target}",
            metadata={"summary": f"removed file {target}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] untracked: {', '.join(matched)}")
        else:
            print(f"untracked: {', '.join(matched)}")

    else:
        print("Usage: /files  |  /files add <path>  |  /files rm <path>")

    return _CMD_CONTINUE


def _cmd_plan(ctx: ChatCommandContext) -> str:
    """/plan [<id> | unlink] — show, link, or unlink a plan for this session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.plan_id:
            validation = _validate_plan_id_local(session.plan_id, cwd=session.cwd)
            suffix = _link_validation_suffix(validation)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"📋 plan: [yellow]{session.plan_id}[/]{suffix}")
            else:
                print(f"plan: {session.plan_id}{suffix}")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan linked. Use:[/] /plan <id>")
            else:
                print("No plan linked. Use: /plan <id>")
        return _CMD_CONTINUE

    if arg.lower() == "unlink":
        if not session.plan_id:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plan is currently linked.[/]")
            else:
                print("No plan is currently linked.")
            return _CMD_CONTINUE
        old = session.plan_id
        update_session(ctx.session_id, plan_id="")
        append_event(
            ctx.session_id,
            kind="chat",
            content="/plan unlink",
            metadata={"summary": f"unlinked plan {old}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]unlinked plan:[/] {old}")
        else:
            print(f"unlinked plan: {old}")
        return _CMD_CONTINUE

    validation = _validate_plan_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]local plan validation unavailable; linking anyway.[/]")
        else:
            print("local plan validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] confirmed plan [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local plan '{arg}'{detail}")
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]⚠[/] plan [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local plan '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, plan_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/plan {arg}",
        metadata={"summary": f"linked plan {arg}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"📋 plan → [yellow]{arg}[/]")
    else:
        print(f"plan → {arg}")
    return _CMD_CONTINUE


def _cmd_task(ctx: ChatCommandContext) -> str:
    """/task [<id> | unlink] — show, link, or unlink a task for this session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    arg = ctx.args.strip()
    if not arg:
        if session.task_id:
            validation = _validate_task_id_local(session.task_id, cwd=session.cwd)
            suffix = _link_validation_suffix(validation)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"✅ task: [yellow]{session.task_id}[/]{suffix}")
            else:
                print(f"task: {session.task_id}{suffix}")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No task linked. Use:[/] /task <id>")
            else:
                print("No task linked. Use: /task <id>")
        return _CMD_CONTINUE

    if arg.lower() == "unlink":
        if not session.task_id:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No task is currently linked.[/]")
            else:
                print("No task is currently linked.")
            return _CMD_CONTINUE
        old = session.task_id
        update_session(ctx.session_id, task_id="")
        append_event(
            ctx.session_id,
            kind="chat",
            content="/task unlink",
            metadata={"summary": f"unlinked task {old}"},
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]unlinked task:[/] {old}")
        else:
            print(f"unlinked task: {old}")
        return _CMD_CONTINUE

    validation = _validate_task_id_local(arg, cwd=session.cwd)
    if not validation.available:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]local task validation unavailable; linking anyway.[/]")
        else:
            print("local task validation unavailable in this install; linking anyway.")
    elif validation.exists:
        detail = f": {validation.summary}" if validation.summary else ""
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] confirmed task [yellow]{arg}[/]{detail}")
        else:
            print(f"confirmed local task '{arg}'{detail}")
    else:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]⚠[/] task [dim]{arg}[/] not found locally; linking anyway.")
        else:
            print(f"warning: local task '{arg}' was not found; linking anyway.")
    update_session(ctx.session_id, task_id=arg)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/task {arg}",
        metadata={"summary": f"linked task {arg}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"✅ task → [yellow]{arg}[/]")
    else:
        print(f"task → {arg}")
    return _CMD_CONTINUE


def _cmd_events(ctx: ChatCommandContext) -> str:
    """/events [n] — show the last n events for this session (default 5)."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    try:
        n = int(ctx.args.strip()) if ctx.args.strip() else 5
    except ValueError:
        _print_error("Usage: /events [n]")
        return _CMD_CONTINUE
    events = load_events(ctx.session_id, limit=n)
    if not events:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No events recorded yet.[/]")
        else:
            print("No events recorded yet.")
        return _CMD_CONTINUE
    _KIND_COLORS = {
        "chat": "dim", "prompt": "white", "analyze": "cyan", "research": "blue",
        "write": "yellow", "exec": "bold yellow", "assistant": "green",
        "edit": "magenta", "error": "red", "watch": "cyan",
    }
    if _RICH_AVAILABLE and _IS_TTY:
        table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
        table.add_column("Time", style="dim", no_wrap=True)
        table.add_column("Kind", no_wrap=True)
        table.add_column("Summary")
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            ts_short = ts[11:19] if len(ts) > 10 else ts  # HH:MM:SS portion
            kind = str(ev.get("kind") or "").strip()
            meta = ev.get("metadata") or {}
            summary = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            content = str(ev.get("content") or "").strip()
            label = (summary or content[:80]).replace("\n", " ")
            color = _KIND_COLORS.get(kind, "dim")
            table.add_row(ts_short, f"[{color}]{kind}[/]", label)
        _RICH_CONSOLE.print(table)
    else:
        for ev in events:
            ts = str(ev.get("timestamp") or ev.get("at") or ev.get("created_at") or "").strip()
            kind = str(ev.get("kind") or "").strip()
            meta = ev.get("metadata") or {}
            summary = str(meta.get("summary") if isinstance(meta, dict) else "").strip()
            content = str(ev.get("content") or "").strip()
            label = summary or content[:100]
            print(f"[{ts}] {kind}: {label}")
    return _CMD_CONTINUE


def _cmd_autoroute(ctx: ChatCommandContext) -> str:
    """/autoroute [on|off] — show or set session-level REPL auto-routing."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    raw = ctx.args.strip().lower()
    current = bool(getattr(session, "repl_auto_route", True))
    if not raw:
        if _RICH_AVAILABLE and _IS_TTY:
            state = "[green]✓ ON[/]" if current else "[dim]✗ OFF[/]"
            _RICH_CONSOLE.print(f"🔀 auto-route: {state}  [dim](high-confidence prompts only)[/]")
        else:
            print(f"Auto-route: {'ON' if current else 'OFF'} (high-confidence prompts only)")
        return _CMD_CONTINUE
    if raw not in {"on", "off"}:
        _print_error("Usage: /autoroute [on|off]")
        return _CMD_CONTINUE
    enabled = raw == "on"
    update_session(ctx.session_id, repl_auto_route=enabled)
    append_event(
        ctx.session_id,
        kind="chat",
        content=f"/autoroute {raw}",
        metadata={"summary": f"auto-route {'enabled' if enabled else 'disabled'}"},
    )
    if _RICH_AVAILABLE and _IS_TTY:
        state = "[green]✓ ON[/]" if enabled else "[dim]✗ OFF[/]"
        note = "" if enabled else "  [dim]prompts will stay in chat[/]"
        _RICH_CONSOLE.print(f"🔀 auto-route → {state}{note}")
    else:
        if enabled:
            print("Auto-route enabled for this session.")
        else:
            print("Auto-route disabled for this session; prompts will stay in chat.")
    return _CMD_CONTINUE


def _cmd_outputs(ctx: ChatCommandContext) -> str:
    """/outputs [<index>|<filename>] — list or preview saved outputs for the active session."""
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    outputs = list_saved_outputs(session.session_id, limit=OUTPUT_LIST_LIMIT)
    if not outputs:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]No saved outputs yet.[/]")
        else:
            print("No saved outputs yet.")
        return _CMD_CONTINUE

    token = ctx.args.strip()
    if not token:
        if _RICH_AVAILABLE and _IS_TTY:
            table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan",
                               caption=f"[dim]{len(outputs)} output(s)[/]")
            table.add_column("#", style="dim", justify="right", no_wrap=True)
            table.add_column("Filename", style="bold")
            table.add_column("Size", style="cyan", justify="right", no_wrap=True)
            table.add_column("Modified", style="dim", no_wrap=True)
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _format_byte_count(int(item.get("size_bytes") or 0))
                modified_at = str(item.get("modified_at") or "").strip()
                table.add_row(str(index), name, size, modified_at)
            _RICH_CONSOLE.print(table)
        else:
            print(f"saved outputs ({len(outputs)} shown):")
            for index, item in enumerate(outputs, start=1):
                name = str(item.get("name") or "").strip()
                size = _format_byte_count(int(item.get("size_bytes") or 0))
                modified_at = str(item.get("modified_at") or "").strip()
                suffix = f"; {modified_at}" if modified_at else ""
                print(f"  {index}. {name} ({size}{suffix})")
        return _CMD_CONTINUE

    preview = load_saved_output_preview(session.session_id, token, max_chars=OUTPUT_PREVIEW_MAX_CHARS)
    if preview is None:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]not found:[/] {token}")
        else:
            print(f"Saved output not found: {token}")
        return _CMD_CONTINUE
    name = str(preview.get("name") or "").strip()
    size = _format_byte_count(int(preview.get("size_bytes") or 0))
    modified_at = str(preview.get("modified_at") or "").strip()
    trunc_note = f"  [dim]preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars[/]" if preview.get("truncated") else ""
    if _RICH_AVAILABLE and _IS_TTY:
        subtitle = f"[dim]{size}"
        if modified_at:
            subtitle += f"  ·  {modified_at}"
        subtitle += f"[/]{trunc_note}"
        _RICH_CONSOLE.print(_RichPanel(str(preview.get("preview") or ""), title=f"[bold]{name}[/]  {subtitle}", border_style="dim", padding=(0, 1)))
    else:
        preview_label = f"saved output preview: {name} ({size}"
        if modified_at:
            preview_label += f"; {modified_at}"
        if preview.get("truncated"):
            preview_label += f"; preview limited to {OUTPUT_PREVIEW_MAX_CHARS} chars"
        preview_label += ")"
        print(preview_label)
        print(str(preview.get("preview") or ""))
    return _CMD_CONTINUE


def _cmd_rollback(ctx: ChatCommandContext) -> str:
    """/rollback last — restore the latest routed-action checkpoint when possible."""
    if ctx.args.strip().lower() != "last":
        _print_error("Usage: /rollback last")
        _set_command_result(ctx, ok=False, summary="invalid rollback selector")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    outcome = restore_last_routed_action_checkpoint(session.session_id)
    if outcome is None:
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]—  no routed action checkpoints available for this session[/]")
        else:
            print("No routed action checkpoints are available for this session.")
        _set_command_result(ctx, ok=False, summary="no routed checkpoints")
        return _CMD_CONTINUE

    checkpoint = outcome.get("checkpoint") or {}
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
    action_kind = str(checkpoint.get("action_kind") or "action").strip()
    reason = str(outcome.get("reason") or "").strip()
    status = str(outcome.get("status") or "").strip()
    if status == "restored":
        restored_files = [str(item) for item in outcome.get("restored_files") or []]
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] rolled back [dim]{action_kind}[/] via checkpoint [dim]{checkpoint_id}[/]  [cyan]({len(restored_files)} file(s) restored)[/]")
            for path in restored_files[:5]:
                _RICH_CONSOLE.print(f"  [dim]↩ {path}[/]")
        else:
            print(f"Rolled back last routed {action_kind} action via checkpoint {checkpoint_id}. Restored {len(restored_files)} file(s).")
            for path in restored_files[:5]:
                print(f"  restored: {path}")
        _set_command_result(ctx, ok=True, summary=f"rolled back checkpoint {checkpoint_id}")
        return _CMD_CONTINUE
    if status == "already_rolled_back":
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[dim]—  checkpoint {checkpoint_id} was already restored[/]")
        else:
            print(f"Checkpoint {checkpoint_id} for the last routed action was already restored.")
        _set_command_result(ctx, ok=True, summary=f"checkpoint {checkpoint_id} already restored")
        return _CMD_CONTINUE
    if status == "unsupported":
        workspace_signature = str(checkpoint.get("workspace_signature") or "").strip()
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[yellow]⚠[/] rollback unavailable for [dim]{checkpoint_id}[/]: {reason or 'manual recovery required'}")
            if workspace_signature:
                _RICH_CONSOLE.print(f"  [dim]workspace before action:[/] {workspace_signature}")
        else:
            print(f"Checkpoint {checkpoint_id} recorded the last routed {action_kind} action, but automatic rollback is unavailable: {reason or 'manual recovery required.'}")
            if workspace_signature:
                print(f"workspace signature before action: {workspace_signature}")
        _set_command_result(ctx, ok=False, summary=f"rollback unavailable for {checkpoint_id}")
        return _CMD_CONTINUE
    if _RICH_AVAILABLE and _IS_TTY:
        _RICH_CONSOLE.print(f"[red]✗[/] rollback failed for [dim]{checkpoint_id}[/]: {reason or 'unable to restore the latest routed action'}")
    else:
        print(f"Rollback failed for checkpoint {checkpoint_id}: {reason or 'unable to restore the latest routed action.'}")
    _set_command_result(ctx, ok=False, summary=f"rollback failed for {checkpoint_id}")
    return _CMD_CONTINUE


# ---------------------------------------------------------------------------
# Action delegation slash commands
# ---------------------------------------------------------------------------

def _require_config_or_warn(ctx: ChatCommandContext) -> "CliConfig | None":
    """Return ctx.config, printing a warning when it is absent."""
    if ctx.config is None:
        _print_error("this command requires an active config. Start with: openclaw --session <id>")
        _set_command_result(ctx, ok=False, summary="missing active config")
        return None
    return ctx.config


def _cmd_analyze(ctx: ChatCommandContext) -> str:
    """/analyze <goal> — run an analysis using the current session context."""
    config = _require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    goal = ctx.args.strip()
    if not goal:
        _print_error("Usage: /analyze <goal>")
        _set_command_result(ctx, ok=False, summary="missing analysis goal")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
    append_event(
        session.session_id,
        kind="analyze",
        content=goal,
        metadata={"summary": goal, "cwd": session.cwd, "files": list(session.files)},
    )
    try:
        response = _with_spinner(
            "🔍 Analyzing…",
            invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    print_response(response, output_json=config.output_json)
    persist_response(session.session_id, goal, response.response)
    ctx.history[:] = load_conversation_history(session.session_id)
    _set_command_result(
        ctx,
        ok=True,
        summary=_summarize_terminal_result(response.response, fallback=f"analysis complete for {goal}"),
    )
    return _CMD_CONTINUE


def _cmd_research(ctx: ChatCommandContext) -> str:
    """/research <query> — run the research agent using the current session context."""
    query = ctx.args.strip()
    if not query:
        _print_error("Usage: /research <query>")
        _set_command_result(ctx, ok=False, summary="missing research query")
        return _CMD_CONTINUE
    try:
        from research_agent import ResearchAgent  # type: ignore[import]
    except ImportError:
        _print_error(missing_feature_hint("openclaw research"))
        _set_command_result(ctx, ok=False, summary="research agent unavailable")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    effective_query = query
    plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_ctx:
        effective_query = f"{plan_ctx}\n\n{effective_query}"
    if context_text and session.files:
        effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

    async def _progress(message: str) -> None:
        if _IS_TTY:
            sys.stdout.write(f"\r🔍 {message:<60}")
            sys.stdout.flush()
        else:
            print(message)

    append_event(session.session_id, kind="research", content=query, metadata={"summary": query})
    try:
        report = run_async(ResearchAgent().run(effective_query, on_progress=_progress))
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 62 + "\r")
        sys.stdout.flush()
    output_target = save_output(
        session.session_id,
        output_name_from_title(query, default_stem="research-report", suffix=".md"),
        report,
    )
    append_event(
        session.session_id,
        kind="assistant",
        content=report,
        metadata={"summary": f"saved research to {output_target}"},
    )
    print(report)
    _print_meta_footer(("saved", output_target))
    _set_command_result(ctx, ok=True, summary=f"saved research to {output_target}")
    return _CMD_CONTINUE


def _cmd_write(ctx: ChatCommandContext) -> str:
    """/write <task> — generate a markdown document using the current session context."""
    config = _require_config_or_warn(ctx)
    if config is None:
        return _CMD_CONTINUE
    task_text = ctx.args.strip()
    if not task_text:
        _print_error("Usage: /write <task>")
        _set_command_result(ctx, ok=False, summary="missing writing task")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    _, context_text = collect_workspace_context(cwd=session.cwd or None, targets=list(session.files))
    title = task_text[:80]
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_write_prompt(task=task_text, context_text=context_text, session=session, title=title)
    append_event(session.session_id, kind="write", content=task_text, metadata={"summary": task_text})
    try:
        response = _with_spinner(
            "✍️  Writing…",
            invoke_openclaw,
            prompt,
            config=scoped_config,
            history=list(ctx.history),
            output_json=False,
        )
    except OpenClawCliError as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    persist_response(session.session_id, task_text, response.response)
    output_target = save_output(
        session.session_id,
        output_name_from_title(title, default_stem="draft", suffix=".md"),
        response.response,
    )
    print(response.response)
    _print_meta_footer(("saved", output_target))
    ctx.history[:] = load_conversation_history(session.session_id)
    _set_command_result(ctx, ok=True, summary=f"saved draft to {output_target}")
    return _CMD_CONTINUE


def _cmd_exec(ctx: ChatCommandContext) -> str:
    """/exec [--] <command> — run a shell command with session tracking and approval."""
    raw = ctx.args.strip()
    if raw.startswith("-- "):
        raw = raw[3:]
    if not raw:
        _print_error("Usage: /exec [--] <command>")
        _set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE
    try:
        command_parts = shlex.split(raw)
    except ValueError as exc:
        _print_error(f"invalid shell command: {exc}")
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not command_parts:
        _print_error("Usage: /exec [--] <command>")
        _set_command_result(ctx, ok=False, summary="missing shell command")
        return _CMD_CONTINUE
    risk_level = infer_command_risk(command_parts)
    if not request_cli_approval(
        action="shell.exec",
        target=raw,
        risk_level=risk_level,
        detail=f"cwd={session.cwd}",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        _print_error("shell command not approved")
        _set_command_result(ctx, ok=False, summary="shell command not approved")
        return _CMD_CONTINUE
    if not _capture_routed_action_checkpoint(
        ctx,
        session=session,
        action_kind="exec",
        target=raw,
        detail=f"cwd={session.cwd}",
    ):
        return _CMD_CONTINUE
    try:
        result = run_async(run_shell_command(command_parts, cwd=session.cwd or None, timeout=60))
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    append_event(
        session.session_id,
        kind="exec",
        content=raw,
        metadata={
            "summary": f"exit {result.returncode}: {raw}",
            "cwd": result.cwd,
            "risk_level": risk_level.value,
            "returncode": result.returncode,
        },
    )
    _print_shell_result(result)
    _set_command_result(
        ctx,
        ok=result.returncode == 0,
        summary=f"exit {result.returncode}: {raw}",
    )
    return _CMD_CONTINUE


def _cmd_edit(ctx: ChatCommandContext) -> str:
    """/edit <path> [--content <text> | --append <text> | --replace OLD NEW] — inspect or write a file."""
    raw = ctx.args.strip()
    if not raw:
        _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        _set_command_result(ctx, ok=False, summary="missing edit target")
        return _CMD_CONTINUE
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        _print_error(f"invalid edit arguments: {exc}")
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    if not parts:
        _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
        _set_command_result(ctx, ok=False, summary="missing edit target")
        return _CMD_CONTINUE
    path = parts[0]
    rest = parts[1:]
    content = ""
    append_mode = False
    replace_values: list[str] = []

    if rest[:1] == ["--content"]:
        content = " ".join(rest[1:])
    elif rest[:1] == ["--append"]:
        content = " ".join(rest[1:])
        append_mode = True
    elif rest[:1] == ["--replace"]:
        if len(rest) < 3:
            _print_error("Usage: /edit <path> [--content <text>] [--append <text>] [--replace OLD NEW]")
            _set_command_result(ctx, ok=False, summary="missing replace arguments")
            return _CMD_CONTINUE
        replace_values = rest[1:3]
    elif rest and not rest[0].startswith("--"):
        content = " ".join(rest)

    session = _require_session_or_warn(ctx)
    if session is None:
        return _CMD_CONTINUE

    if not content and not replace_values:
        # Info mode: show file stats and a preview
        resolved = str(Path(path).expanduser().resolve())
        try:
            p = Path(resolved)
            if p.exists():
                lines = p.read_text(errors="replace").splitlines()
                if _RICH_AVAILABLE and _IS_TTY:
                    _RICH_CONSOLE.print(f"[bold]{resolved}[/]  [dim]({len(lines)} lines)[/]")
                    for ln in lines[:10]:
                        _RICH_CONSOLE.print(f"  [dim]{ln}[/]")
                    if len(lines) > 10:
                        _RICH_CONSOLE.print(f"  [dim]… ({len(lines) - 10} more lines)[/]")
                else:
                    print(f"{resolved}  ({len(lines)} lines)")
                    for ln in lines[:10]:
                        print(f"  {ln}")
                    if len(lines) > 10:
                        print(f"  ... ({len(lines) - 10} more lines)")
                _set_command_result(ctx, ok=True, summary=f"previewed {resolved}")
            else:
                _print_error(f"file not found: {resolved}")
                _set_command_result(ctx, ok=False, summary=f"file not found: {resolved}")
        except Exception as exc:
            _print_error(f"error reading {path}: {exc}")
            _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    risk_level = infer_file_edit_risk(path)
    if not request_cli_approval(
        action="file.edit",
        target=path,
        risk_level=risk_level,
        detail=f"append={append_mode};replace={bool(replace_values)}",
        auto_approve=False,
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        _print_error("file edit not approved")
        _set_command_result(ctx, ok=False, summary="file edit not approved")
        return _CMD_CONTINUE
    resolved_path = str(Path(path).expanduser().resolve())
    if not _capture_routed_action_checkpoint(
        ctx,
        session=session,
        action_kind="edit",
        target=resolved_path,
        detail=f"append={append_mode};replace={bool(replace_values)}",
        file_paths=[resolved_path],
    ):
        return _CMD_CONTINUE
    try:
        if replace_values:
            result = replace_text_in_file(path, old=replace_values[0], new=replace_values[1])
        else:
            result = write_text_file(path, content=content, append=append_mode)
    except Exception as exc:
        _print_error(str(exc))
        _set_command_result(ctx, ok=False, summary=str(exc))
        return _CMD_CONTINUE
    append_event(
        session.session_id,
        kind="edit",
        content=path,
        metadata={
            "summary": result.summary,
            "files": [result.path],
            "changed": result.changed,
            "risk_level": risk_level.value,
        },
    )
    _print_file_edit_result(result)
    _set_command_result(ctx, ok=True, summary=result.summary)
    return _CMD_CONTINUE


def _cmd_update(ctx: ChatCommandContext) -> str:  # noqa: ARG001
    """/update — self-upgrade openclaw via pip without leaving the REPL."""
    import argparse as _argparse
    install_dir = _standalone_install_dir()
    if install_dir and ctx.config is not None:
        _update_standalone_install(install_dir, current=cli_version(), base_url=ctx.config.base_url)
    else:
        handle_update_command(_argparse.Namespace())
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print("[dim]Restart openclaw to use the new version.[/]")
        else:
            print("Restart openclaw to use the new version.")
    return _CMD_CONTINUE


def build_chat_command_registry() -> ChatCommandRegistry:
    """Build and return the default interactive-chat command registry."""
    registry = ChatCommandRegistry()
    registry.register(
        SlashCommand(
            name="help",
            description="Show this help",
            handler=_cmd_help,
        )
    )
    registry.register(
        SlashCommand(
            name="clear",
            description="Reset the current conversation history",
            handler=_cmd_clear,
        )
    )
    registry.register(
        SlashCommand(
            name="quit",
            description="Exit the CLI",
            handler=_cmd_quit,
            aliases=("exit",),
        )
    )
    registry.register(
        SlashCommand(
            name="update",
            description="Self-upgrade openclaw via pip",
            handler=_cmd_update,
        )
    )
    registry.register(
        SlashCommand(
            name="session",
            description="Show current session summary",
            handler=_cmd_session,
        )
    )
    registry.register(
        SlashCommand(
            name="context",
            description="Show the effective session grounding preview",
            handler=_cmd_context,
        )
    )
    registry.register(
        SlashCommand(
            name="cwd",
            description="Show or switch the session working directory (/cwd [path])",
            handler=_cmd_cwd,
        )
    )
    registry.register(
        SlashCommand(
            name="files",
            description="List, add, or remove tracked files (/files [add|rm] [path])",
            handler=_cmd_files,
        )
    )
    registry.register(
        SlashCommand(
            name="plan",
            description="Show, link, or unlink a plan (/plan [<id>|unlink])",
            handler=_cmd_plan,
        )
    )
    registry.register(
        SlashCommand(
            name="task",
            description="Show, link, or unlink a task (/task [<id>|unlink])",
            handler=_cmd_task,
        )
    )
    registry.register(
        SlashCommand(
            name="outputs",
            description="List or preview saved outputs (/outputs [<index>|<filename>])",
            handler=_cmd_outputs,
        )
    )
    registry.register(
        SlashCommand(
            name="rollback",
            description="Restore the last routed checkpoint (/rollback last)",
            handler=_cmd_rollback,
        )
    )
    registry.register(
        SlashCommand(
            name="events",
            description="Show recent session events (/events [n])",
            handler=_cmd_events,
        )
    )
    registry.register(
        SlashCommand(
            name="autoroute",
            description="Show or toggle session auto-routing (/autoroute [on|off])",
            handler=_cmd_autoroute,
        )
    )
    registry.register(
        SlashCommand(
            name="analyze",
            description="Run an analysis on the current session context (/analyze <goal>)",
            handler=_cmd_analyze,
        )
    )
    registry.register(
        SlashCommand(
            name="research",
            description="Run the research agent on a query (/research <query>)",
            handler=_cmd_research,
        )
    )
    registry.register(
        SlashCommand(
            name="write",
            description="Generate a markdown document from a writing task (/write <task>)",
            handler=_cmd_write,
        )
    )
    registry.register(
        SlashCommand(
            name="exec",
            description="Run a shell command with session tracking (/exec [--] <command>)",
            handler=_cmd_exec,
        )
    )
    registry.register(
        SlashCommand(
            name="edit",
            description="Inspect or write a file (/edit <path> [--content <text>] [--append <text>])",
            handler=_cmd_edit,
        )
    )
    return registry


def print_chat_help() -> None:
    """Print built-in interactive chat commands."""
    commands = [
        ("/help",                          "Show this help"),
        ("/clear",                         "Reset the current conversation history"),
        ("/quit",                          "Exit the CLI"),
        ("/update",                        "Self-upgrade openclaw via pip"),
        ("/session",                       "Show current session summary"),
        ("/context",                       "Show effective session grounding preview"),
        ("/cwd [path]",                    "Show or switch the session working directory"),
        ("/files",                         "List tracked files"),
        ("/files add <path>",              "Add a file to tracked files"),
        ("/files rm <path>",               "Remove a file from tracked files"),
        ("/plan [<id>|unlink]",            "Show or link a plan"),
        ("/task [<id>|unlink]",            "Show or link a task"),
        ("/outputs [<index>|<filename>]",  "List or preview saved session outputs"),
        ("/rollback last",                 "Restore latest routed edit checkpoint (text files only)"),
        ("/events [n]",                    "Show last n session events (default 5)"),
        ("/autoroute [on|off]",            "Show or toggle high-confidence REPL auto-routing"),
        ("/analyze <goal>",                "Analyze the session workspace"),
        ("/research <query>",              "Run the research agent on a query"),
        ("/write <task>",                  "Generate a markdown document"),
        ("/exec [--] <command>",           "Run a shell command with approval + session tracking"),
        ("/edit <path> [--content TEXT]",  "Inspect or write a file (--append to append)"),
    ]
    notes = (
        "High-confidence freeform prompts can auto-route to /analyze, /research, /write, /exec, or /edit.\n"
        "Multi-step prompts can decompose into linked plans and auto-run step-by-step with [n/N] progress.\n"
        "Ambiguous prompts stay in normal chat. High/critical /exec and /edit steps still require approval."
    )
    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichTable.grid(padding=(0, 2))
        t.add_column(style="bold cyan", no_wrap=True)
        t.add_column(style="dim")
        for cmd, desc in commands:
            t.add_row(cmd, desc)
        _RICH_CONSOLE.print(_RichPanel(t, title="[bold cyan]OpenClaw Commands[/bold cyan]", border_style="cyan", padding=(0, 1)))
        _RICH_CONSOLE.print(f"[dim]{notes}[/dim]")
        examples = [
            ("Ask a question",       "What does this repo do?"),
            ("Analyze a directory",  "openclaw analyze --cwd ./src"),
            ("Run a command",        "/exec -- git diff HEAD"),
            ("Research a topic",     "/research latest Python async patterns"),
            ("Link a plan",          "/plan my-feature-plan"),
        ]
        ex_grid = _RichTable.grid(padding=(0, 2))
        ex_grid.add_column(style="dim")
        ex_grid.add_column(style="bold cyan")
        for label, cmd in examples:
            ex_grid.add_row(label, cmd)
        _RICH_CONSOLE.print(_RichPanel(ex_grid, title="[bold]Examples[/]", border_style="dim", padding=(0, 1)))
    else:
        print("Interactive commands:")
        for cmd, desc in commands:
            print(f"  {cmd:<38} {desc}")
        print()
        print(notes)
        print("\nExamples:")
        print('  Ask a question         What does this repo do?')
        print('  Analyze a directory    openclaw analyze --cwd ./src')
        print('  Run a command          /exec -- git diff HEAD')
        print('  Research a topic       /research latest Python async patterns')
        print('  Link a plan            /plan my-feature-plan')


def handle_auth_command(args: argparse.Namespace) -> int:
    """Handle CLI token login/status/logout flows."""
    def _auth_print(msg: str, style: str = "") -> None:
        if _RICH_AVAILABLE and _IS_TTY and style:
            _RICH_CONSOLE.print(f"[{style}]{msg}[/]")
        else:
            print(msg)

    if args.auth_command == "login":
        token = str(getattr(args, "token", "") or "").strip()
        if not token:
            token = getpass.getpass("OpenClaw API token: ").strip()
        if not token:
            raise OpenClawCliError("OpenClaw token cannot be empty.")
        if sys.platform == "darwin":
            write_keychain_token(token)
            delete_saved_token()
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token stored in macOS Keychain [dim]({KEYCHAIN_SERVICE})[/]")
            else:
                print(f"Stored OpenClaw token in macOS Keychain under '{KEYCHAIN_SERVICE}'.")
            return 0
        saved_path = write_saved_token(token)
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"🔑 [green]✓[/] token stored  [dim]{saved_path}[/]")
        else:
            print(f"Stored OpenClaw token in {saved_path}.")
        return 0

    if args.auth_command == "status":
        resolution = resolve_token_details(getattr(args, "token", None))
        if _RICH_AVAILABLE and _IS_TTY:
            if resolution.token:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token configured  [dim]via {resolution.source}[/]")
            else:
                _RICH_CONSOLE.print("🔑 [dim]✗  no token configured[/]")
            if sys.platform == "darwin":
                _RICH_CONSOLE.print(f"   [dim]keychain service:[/]  {KEYCHAIN_SERVICE}")
            _RICH_CONSOLE.print(f"   [dim]credential file:[/]   {auth_storage_path()}")
        else:
            if resolution.token:
                print(f"OpenClaw token available via {resolution.source}.")
            else:
                print("No OpenClaw token is currently configured.")
            if sys.platform == "darwin":
                print(f"Keychain service: {KEYCHAIN_SERVICE}")
            print(f"Credential file: {auth_storage_path()}")
        return 0

    if args.auth_command == "logout":
        removed_locations: list[str] = []
        if delete_saved_token():
            removed_locations.append(str(auth_storage_path()))
        if delete_keychain_token():
            removed_locations.append(f"macOS Keychain '{KEYCHAIN_SERVICE}'")
        if removed_locations:
            joined = ", ".join(removed_locations)
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print(f"🔑 [green]✓[/] token removed  [dim]{joined}[/]")
            else:
                print("Removed OpenClaw token from " + joined + ".")
        else:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("🔑 [dim]—  no persisted token was found[/]")
            else:
                print("No persisted OpenClaw token was found.")
        return 0

    raise OpenClawCliError(f"Unknown auth command: {args.auth_command}")


def _print_connection_error_panel(msg: str, base_url: str = "") -> None:
    """Print a rich error panel for connection/HTTP failures, with hints."""
    if not (_RICH_AVAILABLE and _IS_TTY):
        _print_error(msg, file=sys.stderr)
        return
    is_connection = any(k in msg.lower() for k in ("refused", "unreachable", "timed out", "resolve", "unable to reach"))
    body = _RichText()
    body.append(msg + "\n", style="red")
    if base_url:
        body.append("\n  url tried: ", style="dim")
        body.append(base_url + "\n", style="cyan")
    if is_connection:
        body.append("\n  hints:\n", style="dim")
        body.append("  • Is the OpenClaw server running?\n", style="dim")
        body.append("  • Check OPENCLAW_URL or pass --url\n", style="dim")
        body.append("  • Try: openclaw health\n", style="dim")
    _RICH_CONSOLE.print(_RichPanel(body, title="[bold red]❌ Connection failed[/]", border_style="red", padding=(0, 1)), file=sys.stderr)


def handle_status_command(args: argparse.Namespace, *, config: "CliConfig") -> int:
    """Show an at-a-glance status dashboard."""
    output_json = config.output_json

    version = cli_version()
    latest = _latest_version

    try:
        health = fetch_health(config=config)
        health_status = health.status or "unknown"
        health_ok = health.healthy
    except OpenClawCliError as exc:
        health_status = "unreachable"
        health_ok = None
        health_err = str(exc)
    else:
        health_err = ""

    resolution = resolve_token_details(config.token)

    from openclaw_cli_sessions import list_sessions as _list_sessions
    recent = _list_sessions(limit=1)
    recent_session = recent[0] if recent else None

    if output_json:
        import json as _json
        print(_json.dumps({
            "version": version,
            "latest": latest,
            "health": health_status,
            "token_source": resolution.source if resolution.token else None,
            "recent_session": recent_session.session_id if recent_session else None,
        }, indent=2))
        return 0

    if _RICH_AVAILABLE and _IS_TTY:
        grid = _RichText()
        update_badge = ""
        if latest and latest != version:
            update_badge = f"  [yellow]⬆ {latest} available[/]  [dim]/update[/]"
        grid.append("  version    ", style="dim")
        grid.append(version, style="bold")
        grid.append(update_badge + "\n")
        health_emoji = _status_emoji(health_status)
        health_color = "green" if health_ok is True else ("yellow" if health_ok is False else "red")
        grid.append("  server     ", style="dim")
        grid.append(config.base_url, style="cyan")
        grid.append(f"  [{health_color}]{health_emoji} {health_status.upper()}[/]\n")
        if health_err:
            grid.append(f"              [dim red]{health_err[:80]}[/]\n")
        grid.append("  token      ", style="dim")
        if resolution.token:
            grid.append(f"🔑 [green]configured[/]  [dim]via {resolution.source}[/]\n")
        else:
            grid.append("🔑 [dim]not configured[/]\n")
        if recent_session:
            grid.append("  session    ", style="dim")
            grid.append(f"[yellow]{recent_session.session_id[:8]}…[/]")
            if recent_session.title:
                grid.append(f"  [dim]{recent_session.title[:50]}[/]")
            grid.append("\n")
        _RICH_CONSOLE.print(_RichPanel(grid, title="[bold cyan]🦞 OpenClaw Status[/]", border_style="cyan", padding=(0, 1)))
    else:
        print(f"version : {version}" + (f" (update: {latest})" if latest and latest != version else ""))
        print(f"server  : {config.base_url}  [{health_status}]")
        print(f"token   : {'configured via ' + resolution.source if resolution.token else 'not configured'}")
        if recent_session:
            print(f"session : {recent_session.session_id}")
    return 0


def load_shell_history() -> None:
    """Load persisted readline history when available."""
    if readline is None:
        return
    try:
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
    except OSError:
        return
    readline.set_history_length(HISTORY_LIMIT)


def save_shell_history() -> None:
    """Persist readline history between interactive sessions when available."""
    if readline is None:
        return
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        readline.set_history_length(HISTORY_LIMIT)
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        return


def build_config(args: argparse.Namespace) -> CliConfig:
    """Build resolved CLI config from parsed args and environment."""
    timeout_seconds = max(1, int(args.timeout))
    return CliConfig(
        base_url=normalize_base_url(args.url or os.getenv("OPENCLAW_URL")),
        token=resolve_token(args.token),
        model=str(args.model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        timeout_seconds=timeout_seconds,
        user_name=(args.user_name or default_user_name()).strip(),
        client_name=(args.client_name or default_client_name()).strip(),
        output_json=bool(args.json),
        session_id=str(getattr(args, "session", "") or "").strip(),
    )


def _make_prompt(session_id: str = "", autoroute_on: bool = True) -> str:
    """Build the REPL prompt string, optionally with session hint or no-route badge."""
    if _IS_TTY:
        if not autoroute_on:
            return f"\033[2m[no-route]\033[0m openclaw> "
        if session_id:
            short = session_id[:6]
            return f"openclaw \033[2m({short}…)\033[0m> "
    return "openclaw> "


def _print_startup_banner(config: CliConfig, session_id: str) -> None:
    """Print a colored startup banner for the interactive REPL."""
    if _RICH_AVAILABLE and _IS_TTY:
        t = _RichText()
        t.append("🦞 OpenClaw", style="bold cyan")
        t.append("  connected to ", style="dim")
        t.append(config.base_url, style="cyan")
        t.append("\n  👤 ", style="dim")
        t.append(config.user_name, style="bold green")
        if session_id:
            t.append("  ·  🗂  session: ", style="dim")
            t.append(session_id[:8] + "…", style="yellow")
        t.append("\n\n  ", style="")
        t.append("/help", style="bold cyan")
        t.append("  ·  ", style="dim")
        t.append("/autoroute off", style="bold cyan")
        t.append("  ·  ", style="dim")
        t.append("/clear", style="bold cyan")
        t.append("  ·  ", style="dim")
        t.append("/quit", style="bold cyan")
        _RICH_CONSOLE.print(_RichPanel(t, border_style="cyan", padding=(0, 1)))
    else:
        # ANSI fallback — multi-line and colorful
        session_line = (
            f"\n  {_DM}🗂  session:{_R}  {_YE}{session_id[:8]}…{_R}" if session_id else ""
        )
        print(
            f"\n{_BCY}🦞 OpenClaw{_R}"
            f"\n  {_DM}connected to{_R}  {_CY}{config.base_url}{_R}"
            f"\n  {_DM}👤 user:{_R}      {_BGR}{config.user_name}{_R}"
            f"{session_line}"
            f"\n"
            f"\n  {_BCY}/help{_R}  {_DM}·{_R}  {_BCY}/autoroute off{_R}"
            f"  {_DM}·{_R}  {_BCY}/clear{_R}  {_DM}·{_R}  {_BCY}/quit{_R}\n"
        )


def run_chat(
    config: CliConfig,
    *,
    input_func: Any = input,
    ask_func: Any = invoke_openclaw,
    session_id: str = "",
) -> int:
    """Run an interactive chat session against OpenClaw."""
    history: list[dict[str, str]] = load_conversation_history(session_id) if session_id else []
    registry = build_chat_command_registry()
    load_shell_history()
    _print_startup_banner(config, session_id)
    while True:
        try:
            autoroute_on = _session_auto_route_enabled(session_id)
            prompt_str = _make_prompt(session_id=session_id, autoroute_on=autoroute_on)
            prompt = str(input_func(prompt_str)).strip()
        except EOFError:
            print()
            save_shell_history()
            return 0
        except KeyboardInterrupt:
            print()
            save_shell_history()
            return 130

        if not prompt:
            continue

        ctx = ChatCommandContext(history=history, session_id=session_id, config=config)
        result = registry.dispatch(prompt, ctx)
        if result == _CMD_QUIT:
            save_shell_history()
            return 0
        if result == _CMD_CONTINUE:
            continue

        # Unknown slash command — don't send to the AI.
        if prompt.startswith("/"):
            cmd_name = prompt.split()[0]
            _print_error(f"Unknown command {_BCY}{cmd_name}{_R}. Type {_BCY}/help{_R} for a list.")
            continue

        if autoroute_on:
            route_decision = route_repl_prompt(prompt, session_id=session_id)
            if route_decision.should_auto_execute_plan():
                print(_format_route_announcement(route_decision))
                _append_repl_route_event(session_id, prompt, route_decision)
                try:
                    routed = _execute_routed_plan(
                        prompt=prompt,
                        decision=route_decision,
                        registry=registry,
                        ctx=ctx,
                    )
                except OpenClawCliError as exc:
                    print(f"{_BRE}error:{_R} {exc}", file=sys.stderr)
                else:
                    if routed == _CMD_QUIT:
                        save_shell_history()
                        return 0
                    if routed == _CMD_CONTINUE:
                        continue
            if route_decision.should_auto_route():
                print(_format_route_announcement(route_decision))
                _append_repl_route_event(session_id, prompt, route_decision)
                routed = registry.dispatch(route_decision.to_slash_command(), ctx)
                if routed == _CMD_QUIT:
                    save_shell_history()
                    return 0
                if routed == _CMD_CONTINUE:
                    continue

        try:
            response = ask_func(prompt, config=config, history=list(history))
        except OpenClawCliError as exc:
            print(f"{_BRE}error:{_R} {exc}", file=sys.stderr)
            continue

        print_response(response, output_json=config.output_json)
        history.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.response},
            ]
        )
        if session_id:
            append_event(session_id, kind="chat", content=prompt, metadata={"summary": prompt})
            persist_response(session_id, prompt, response.response)


def run_async(coro: Any) -> Any:
    """Run an async coroutine from the synchronous CLI entrypoint."""
    return asyncio.run(coro)


def output_name_from_title(title: str, *, default_stem: str, suffix: str) -> str:
    """Build a safe output filename from free-form user input."""
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", str(title or "").strip().lower()).strip("-")
    return f"{(stem or default_stem)[:40]}{suffix}"


def missing_feature_hint(feature: str) -> str:
    """Explain when a standalone CLI install is missing optional dependencies."""
    return (
        f"`{feature}` needs the full OpenClaw runtime dependencies. "
        "Use a repo checkout/package install for advanced commands, or stick to core standalone flows like "
        "ask/chat/health/analyze/write/exec/edit/watch."
    )


def handle_session_command(args: argparse.Namespace) -> int:
    """Handle local CLI session management."""
    subcommand = str(getattr(args, "session_command", "") or "").strip()
    if subcommand == "create":
        session = create_session(
            title=str(getattr(args, "title", "") or "").strip() or "OpenClaw CLI session",
            cwd=getattr(args, "cwd", None),
            files=list(getattr(args, "files", []) or []),
            plan_id=str(getattr(args, "plan_id", "") or "").strip(),
            task_id=str(getattr(args, "task_id", "") or "").strip(),
        )
        _print_session_summary(session)
        return 0
    if subcommand == "list":
        _print_session_list(list_sessions(limit=int(getattr(args, "limit", 20) or 20)))
        return 0
    if subcommand == "show":
        out = inspect_session(args.session_id)
        if out:
            print(out)
        return 0
    if subcommand == "resume":
        session = require_session(args.session_id)
        _print_session_summary(session)
        _print_meta_footer(("resume", f"openclaw --session {session.session_id}"))
        return 0
    if subcommand == "export":
        print(json.dumps(export_session(args.session_id), indent=2, sort_keys=True))
        return 0
    raise OpenClawCliError(f"Unknown session command: {subcommand}")


def handle_plan_command(args: argparse.Namespace, *, session_id: str = "") -> int:
    """Handle agent-plan operations using the existing agent loop."""
    try:
        from agent_loop import cancel_plan, read_plan, resume_plan
        from agent_loop import list_plans as list_plan_objects
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw plan")) from exc

    subcommand = str(getattr(args, "plan_command", "") or "").strip()
    if subcommand == "create":
        goal = parse_prompt(getattr(args, "goal", []) or [])
        if not goal:
            raise OpenClawCliError("A plan goal is required.")
        steps_text = str(getattr(args, "steps_text", "") or "")
        _plan_id, create_result = _create_persisted_plan(
            goal=goal,
            steps_text=steps_text,
            session_id=session_id,
        )
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]✓[/] [bold]plan created:[/] [yellow]{_plan_id}[/]")
        else:
            print(create_result)
        return 0
    if subcommand == "list":
        plans = list_plan_objects(str(getattr(args, "status", "all") or "all"))
        if not plans:
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("[dim]No plans found.[/]")
            else:
                print("No plans found.")
            return 0
        if _RICH_AVAILABLE and _IS_TTY:
            _STATUS_COLORS = {
                "running": "cyan", "done": "green", "completed": "green",
                "failed": "red", "cancelled": "dim", "pending": "yellow",
                "in-progress": "cyan",
            }
            table = _RichTable(border_style="dim", show_edge=True, pad_edge=True, header_style="bold cyan")
            table.add_column("Plan ID", style="dim", no_wrap=True)
            table.add_column("Status", no_wrap=True)
            table.add_column("Progress", no_wrap=True)
            table.add_column("Goal")
            for plan in plans:
                s = (plan.status or "unknown").lower()
                color = _STATUS_COLORS.get(s, "dim")
                emoji = _status_emoji(s)
                goal_text = (plan.goal or "")[:72] + ("…" if len(plan.goal or "") > 72 else "")
                table.add_row(
                    plan.plan_id,
                    f"[{color}]{emoji} {plan.status}[/]",
                    plan.progress_str() if hasattr(plan, "progress_str") else "—",
                    goal_text,
                )
            _RICH_CONSOLE.print(table)
        else:
            for plan in plans:
                print(f"{plan.plan_id} | {plan.status} | {plan.progress_str()} | {plan.goal}")
        return 0
    if subcommand == "show":
        print(run_async(read_plan(args.plan_id)))
        return 0
    if subcommand == "resume":
        result = run_async(resume_plan(args.plan_id))
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[green]▶[/] [bold]plan resumed:[/] [yellow]{args.plan_id}[/]  [dim]{str(result)[:120]}[/]")
        else:
            print(result)
        return 0
    if subcommand == "cancel":
        result = run_async(cancel_plan(args.plan_id))
        if _RICH_AVAILABLE and _IS_TTY:
            _RICH_CONSOLE.print(f"[red]✗[/] [bold]plan cancelled:[/] [dim]{args.plan_id}[/]  [dim]{str(result)[:120]}[/]")
        else:
            print(result)
        return 0
    raise OpenClawCliError(f"Unknown plan command: {subcommand}")


def handle_analyze_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Analyze a working directory or file set through the ask API."""
    prompt_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "prompt", []) or []), cwd=getattr(args, "cwd", None))
    goal = parse_prompt(prompt_parts)
    if not goal:
        raise OpenClawCliError("Analysis goal is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Analyze: {goal[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
    append_event(
        session.session_id,
        kind="analyze",
        content=goal,
        metadata={
            "summary": goal,
            "cwd": session.cwd,
            "files": normalized_targets,
            "plan_id": getattr(args, "plan_id", ""),
            "task_id": getattr(args, "task_id", ""),
        },
    )
    response = _with_spinner(
        "🔍 Analyzing…",
        invoke_openclaw,
        prompt,
        config=scoped_config,
        history=load_conversation_history(session.session_id),
        output_json=config.output_json,
    )
    print_response(response, output_json=config.output_json)
    persist_response(session.session_id, goal, response.response)
    _print_meta_footer(("session", session.session_id))
    return 0


def handle_research_command(args: argparse.Namespace) -> int:
    """Run the built-in research agent from the CLI."""
    from openclaw_cli_actions import write_text_file
    try:
        from research_agent import ResearchAgent
    except ImportError as exc:
        raise OpenClawCliError(missing_feature_hint("openclaw research")) from exc

    prompt_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "query", []) or []), cwd=getattr(args, "cwd", None))
    query = parse_prompt(prompt_parts)
    if not query:
        raise OpenClawCliError("Research query is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Research: {query[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    effective_query = query
    plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
    if plan_ctx:
        effective_query = f"{plan_ctx}\n\n{effective_query}"
    if context_text and normalized_targets:
        effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

    async def _progress(message: str) -> None:
        if _IS_TTY:
            sys.stdout.write(f"\r🔍 {message:<60}")
            sys.stdout.flush()
        else:
            print(message)

    append_event(session.session_id, kind="research", content=query, metadata={"summary": query, "files": normalized_targets})
    report = run_async(ResearchAgent().run(effective_query, on_progress=_progress, deep=bool(getattr(args, "deep", False))))
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 62 + "\r")
        sys.stdout.flush()

    output_path = str(getattr(args, "output", "") or "").strip()
    if output_path:
        write_text_file(output_path, content=report)
        output_display = output_path
    else:
        output_target = save_output(
            session.session_id,
            output_name_from_title(query, default_stem="research-report", suffix=".md"),
            report,
        )
        output_display = str(output_target)
    append_event(session.session_id, kind="assistant", content=report, metadata={"summary": f"saved research to {output_display}"})
    print(report)
    _print_meta_footer(("saved", output_display), ("session", session.session_id))
    return 0


def handle_write_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Generate a markdown document from a writing task."""
    task_parts, prompt_targets = extract_prompt_targets(list(getattr(args, "task", []) or []), cwd=getattr(args, "cwd", None))
    task_text = parse_prompt(task_parts)
    if not task_text:
        raise OpenClawCliError("Writing task is required.")

    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    normalized_targets, context_text = collect_workspace_context(cwd=getattr(args, "cwd", None), targets=explicit_targets)
    title = str(getattr(args, "title", "") or "").strip() or task_text[:80]
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Write: {title[:60]}",
        cwd=getattr(args, "cwd", None),
        files=normalized_targets,
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    scoped_config = bind_config_to_session(config, session.session_id)
    prompt = build_write_prompt(task=task_text, context_text=context_text, session=session, title=title)
    append_event(session.session_id, kind="write", content=task_text, metadata={"summary": task_text, "files": normalized_targets})
    response = _with_spinner(
        "✍️  Writing…",
        invoke_openclaw,
        prompt,
        config=scoped_config,
        history=load_conversation_history(session.session_id),
        output_json=config.output_json,
    )
    persist_response(session.session_id, task_text, response.response)

    output_path = str(getattr(args, "output", "") or "").strip()
    if output_path:
        write_text_file(output_path, content=response.response)
        output_display = output_path
    else:
        output_target = save_output(
            session.session_id,
            output_name_from_title(title, default_stem="draft", suffix=".md"),
            response.response,
        )
        output_display = str(output_target)
    print(response.response)
    _print_meta_footer(("saved", output_display), ("session", session.session_id))
    return 0


def execute_watch_iteration(
    *,
    session: SessionSummary,
    state: dict[str, Any],
    config: CliConfig,
    output_override: str = "",
    deep_research: bool = False,
    title: str = "",
    on_progress: Callable[[str, str], None] | None = None,
) -> tuple[str, str]:
    """Run a single watch-mode checkpoint and persist its output."""
    goal = str(state.get("goal") or "").strip()
    mode = str(state.get("mode") or "analyze").strip().lower()
    cwd = str(state.get("cwd") or session.cwd or "").strip() or None
    targets = list(state.get("files") or session.files or [])
    if on_progress:
        on_progress("context", "Collecting workspace context")
    normalized_targets, context_text = collect_workspace_context(cwd=cwd, targets=targets)
    if normalized_targets != session.files or (cwd and cwd != session.cwd):
        session = update_session(session.session_id, cwd=cwd or session.cwd, files=normalized_targets)
        state["cwd"] = session.cwd
        state["files"] = list(session.files or [])

    output_path = str(output_override or "").strip()
    if mode == "analyze":
        if on_progress:
            on_progress("request", "Submitting analysis checkpoint")
        prompt = build_analysis_prompt(goal=goal, context_text=context_text, session=session)
        append_event(
            session.session_id,
            kind="analyze",
            content=goal,
            metadata={
                "summary": goal,
                "cwd": session.cwd,
                "files": normalized_targets,
                "plan_id": session.plan_id,
                "task_id": session.task_id,
                "automation_mode": "watch",
            },
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving analysis checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved_path = output_path
        else:
            saved_path = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-analysis", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved_path

    if mode == "research":
        try:
            from research_agent import ResearchAgent
        except ImportError as exc:
            raise OpenClawCliError(missing_feature_hint("openclaw watch --mode research")) from exc

        effective_query = goal
        plan_ctx = _plan_task_context_snippet(session.plan_id, session.task_id, cwd=session.cwd)
        if plan_ctx:
            effective_query = f"{plan_ctx}\n\n{effective_query}"
        if context_text and normalized_targets:
            effective_query = f"{effective_query}\n\nLocal workspace context:\n{context_text[:4000]}"

        if on_progress:
            on_progress("request", "Starting research checkpoint")

        async def _progress(message: str) -> None:
            if on_progress:
                on_progress("research", message)

        append_event(
            session.session_id,
            kind="research",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        report = run_async(ResearchAgent().run(effective_query, on_progress=_progress, deep=deep_research))
        if on_progress:
            on_progress("persist", "Saving research checkpoint")
        if output_path:
            write_text_file(output_path, content=report)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{mode}-{state.get('poll_count', 0)}", default_stem="watch-research", suffix=".md"),
                    report,
                )
            )
        append_event(session.session_id, kind="assistant", content=report, metadata={"summary": f"saved research to {saved}"})
        return report, saved

    if mode == "write":
        document_title = title or goal[:80] or "OpenClaw Watch Draft"
        if on_progress:
            on_progress("request", "Submitting writing checkpoint")
        prompt = build_write_prompt(task=goal, context_text=context_text, session=session, title=document_title)
        append_event(
            session.session_id,
            kind="write",
            content=goal,
            metadata={"summary": goal, "files": normalized_targets, "automation_mode": "watch"},
        )
        response = invoke_openclaw(
            prompt,
            config=bind_config_to_session(config, session.session_id),
            history=load_conversation_history(session.session_id),
        )
        persist_response(session.session_id, goal, response.response)
        if on_progress:
            on_progress("persist", "Saving writing checkpoint")
        if output_path:
            write_text_file(output_path, content=response.response)
            saved = output_path
        else:
            saved = str(
                save_output(
                    session.session_id,
                    output_name_from_title(f"watch-{document_title}-{state.get('poll_count', 0)}", default_stem="watch-draft", suffix=".md"),
                    response.response,
                )
            )
        return response.response, saved

    raise OpenClawCliError(f"Unsupported watch mode: {mode}")


def handle_watch_command(args: argparse.Namespace, *, config: CliConfig) -> int:
    """Run a resumable watch loop over a session workspace."""
    resume_id = str(getattr(args, "resume", "") or "").strip()
    requested_session = str(getattr(args, "session", "") or config.session_id or "").strip()
    if resume_id and requested_session and resume_id != requested_session:
        raise OpenClawCliError("Use either --resume or --session for watch mode, not both.")

    existing_state = load_watch_state(resume_id or requested_session) if (resume_id or requested_session) else None
    session_seed = require_session(resume_id or requested_session) if (resume_id or requested_session) else None
    goal_parts, prompt_targets = extract_prompt_targets(
        list(getattr(args, "goal", []) or []),
        cwd=getattr(args, "cwd", None) or (session_seed.cwd if session_seed else None),
    )
    prompt_goal = parse_prompt(goal_parts) if goal_parts else ""
    plan_id = str(getattr(args, "plan_id", "") or (existing_state or {}).get("plan_id") or (session_seed.plan_id if session_seed else "")).strip()
    task_id = str(getattr(args, "task_id", "") or (existing_state or {}).get("task_id") or (session_seed.task_id if session_seed else "")).strip()
    goal = prompt_goal or str((existing_state or {}).get("goal") or "").strip() or load_plan_goal(plan_id)
    if task_id and not goal:
        goal = f"Continue task {task_id}"
    if not goal:
        raise OpenClawCliError("Watch mode needs a goal, plan, or task to follow.")

    mode = str(getattr(args, "mode", "") or (existing_state or {}).get("mode") or "analyze").strip().lower()
    interval_seconds = max(1, int(getattr(args, "interval", 0) or (existing_state or {}).get("interval_seconds") or 60))
    max_polls = max(0, int(getattr(args, "iterations", 0) or (existing_state or {}).get("max_polls") or 0))
    on_change = bool(getattr(args, "on_change", False) or (existing_state or {}).get("on_change"))
    cwd = str(getattr(args, "cwd", "") or (existing_state or {}).get("cwd") or (session_seed.cwd if session_seed else "")).strip() or None
    explicit_targets = [*list(getattr(args, "files", []) or []), *prompt_targets]
    if not explicit_targets:
        explicit_targets = list((existing_state or {}).get("files") or (session_seed.files if session_seed else []) or [])
    normalized_targets, _ = collect_workspace_context(cwd=cwd, targets=explicit_targets)

    session = ensure_cli_session(
        resume_id or requested_session,
        title=f"Watch: {goal[:60]}",
        cwd=cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
    )
    session = update_session(
        session.session_id,
        cwd=cwd or session.cwd,
        files=normalized_targets,
        plan_id=plan_id,
        task_id=task_id,
        automation_mode=mode,
        automation_status="watching",
        watch_interval_seconds=interval_seconds,
    )

    resume_snapshot = normalize_watch_state(existing_state) if existing_state else None
    state = existing_state or build_watch_state(
        session=session,
        mode=mode,
        goal=goal,
        interval_seconds=interval_seconds,
        max_polls=max_polls,
        on_change=on_change,
    )
    state = normalize_watch_state(state)
    state.update(
        {
            "mode": mode,
            "goal": goal,
            "cwd": session.cwd,
            "files": list(normalized_targets),
            "plan_id": plan_id,
            "task_id": task_id,
            "interval_seconds": interval_seconds,
            "max_polls": max_polls,
            "on_change": on_change,
            "status": "running",
            "updated_at": utc_timestamp(),
        }
    )
    save_watch_state(session.session_id, state)

    if not config.output_json:
        if resume_snapshot:
            print_watch_resume_snapshot(session.session_id, resume_snapshot, output_json=config.output_json)
        if _RICH_AVAILABLE and _IS_TTY:
            _body = _RichText()
            _body.append(f"  session  ", style="dim")
            _body.append(f"{session.session_id}\n")
            _body.append(f"  mode     ", style="dim")
            _body.append(f"{mode}\n")
            _body.append(f"  goal     ", style="dim")
            _body.append(f"{goal[:60]}\n")
            _body.append(f"  interval ", style="dim")
            _body.append(f"{interval_seconds}s")
            _body.append("  ·  max ", style="dim")
            _body.append(f"{'infinite' if max_polls == 0 else max_polls}\n")
            _body.append("  Ctrl-C to pause & resume", style="dim")
            _RICH_CONSOLE.print(_RichPanel(_body, border_style="cyan", title="[bold cyan]👁  watch[/]"))
        else:
            print(
                f"Watching session {session.session_id} in {mode} mode "
                f"(interval={interval_seconds}s, max polls={'infinite' if max_polls == 0 else max_polls})."
            )
            print("Press Ctrl-C to stop and resume later with `openclaw watch --resume <session_id>`.")

    try:
        while max_polls == 0 or int(state.get("poll_count", 0) or 0) < max_polls:
            state = refresh_watch_controls(session.session_id, state)
            if state.get("stop_requested"):
                return stop_watch_from_intervention(
                    session=session,
                    state=state,
                    mode=mode,
                    output_json=config.output_json,
                )
            state["poll_count"] = int(state.get("poll_count", 0) or 0) + 1
            workspace_signature = build_workspace_signature(cwd=state.get("cwd"), targets=list(state.get("files") or []))
            force_run_once = bool(state.get("force_run_once"))
            if on_change and state.get("workspace_signature") and workspace_signature == state.get("workspace_signature") and not force_run_once:
                state["updated_at"] = utc_timestamp()
                state["status"] = "waiting"
                save_watch_state(session.session_id, state)
                update_session(session.session_id, automation_status="waiting", automation_mode=mode)
                if not config.output_json:
                    print(f"[watch {state['poll_count']}] unchanged; waiting for workspace updates.")
            else:
                if force_run_once:
                    state["force_run_once"] = False
                    resolve_watch_intervention(
                        state,
                        action="force-checkpoint",
                        status="applied",
                        note="Forced one checkpoint despite unchanged workspace.",
                    )
                    record_watch_progress(
                        session_id=session.session_id,
                        state=state,
                        iteration=state["poll_count"],
                        mode=mode,
                        phase="control",
                        message="Dashboard requested a forced checkpoint; running anyway.",
                        output_json=config.output_json,
                    )
                state["active_checkpoint"] = start_watch_checkpoint(iteration=state["poll_count"], mode=mode)
                save_watch_state(session.session_id, state)
                retry_limit = max(1, int(state.get("retry_limit") or WATCH_RETRY_LIMIT))
                attempt = 0
                while True:
                    attempt += 1
                    active_checkpoint = state.setdefault("active_checkpoint", start_watch_checkpoint(iteration=state["poll_count"], mode=mode))
                    attempts = list(active_checkpoint.get("attempts") or [])
                    attempts.append({"attempt": attempt, "started_at": utc_timestamp(), "status": "running"})
                    active_checkpoint["attempts"] = attempts[-WATCH_PROGRESS_LOG_LIMIT:]
                    active_checkpoint["updated_at"] = utc_timestamp()
                    save_watch_state(session.session_id, state)

                    try:
                        result_text, output_path = execute_watch_iteration(
                            session=require_session(session.session_id),
                            state=state,
                            config=config,
                            output_override=str(getattr(args, "output", "") or "").strip(),
                            deep_research=bool(getattr(args, "deep", False)),
                            title=str(getattr(args, "title", "") or "").strip(),
                            on_progress=lambda phase, message: record_watch_progress(
                                session_id=session.session_id,
                                state=state,
                                iteration=state["poll_count"],
                                mode=mode,
                                phase=phase,
                                message=message,
                                output_json=config.output_json,
                            ),
                        )
                        active_checkpoint["attempts"][-1].update({"finished_at": utc_timestamp(), "status": "completed"})
                        break
                    except Exception as exc:
                        error_message = str(exc).strip() or exc.__class__.__name__
                        transient = is_transient_watch_error(error_message)
                        active_checkpoint["attempts"][-1].update(
                            {
                                "finished_at": utc_timestamp(),
                                "status": "failed",
                                "error": error_message,
                                "transient": transient,
                            }
                        )
                        state["failure_count"] = int(state.get("failure_count") or 0) + 1
                        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
                        state["last_error"] = error_message
                        retry_entry = {
                            "poll": state["poll_count"],
                            "attempt": attempt,
                            "error": error_message,
                            "transient": transient,
                            "created_at": utc_timestamp(),
                        }
                        retry_history = list(state.get("retry_history") or [])
                        retry_history.append(retry_entry)
                        state["retry_history"] = retry_history[-WATCH_PROGRESS_LOG_LIMIT:]
                        state["status"] = "retrying" if transient and attempt < retry_limit else "failed"
                        state["updated_at"] = utc_timestamp()
                        save_watch_state(session.session_id, state)
                        update_session(
                            session.session_id,
                            automation_mode=mode,
                            automation_status="retrying" if transient and attempt < retry_limit else "failed",
                            watch_interval_seconds=interval_seconds,
                        )
                        if transient and attempt < retry_limit:
                            delay_seconds = watch_retry_delay_seconds(attempt)
                            record_watch_progress(
                                session_id=session.session_id,
                                state=state,
                                iteration=state["poll_count"],
                                mode=mode,
                                phase="retry",
                                message=(
                                    f"Transient failure on attempt {attempt}/{retry_limit}: "
                                    f"{error_message}. Retrying in {delay_seconds}s."
                                ),
                                output_json=config.output_json,
                            )
                            time.sleep(delay_seconds)
                            continue
                        failure_summary = f"{mode} failed: {error_message[:160]}"
                        active_checkpoint.update(
                            {
                                "status": "failed",
                                "completed_at": utc_timestamp(),
                                "summary": failure_summary,
                                "error": error_message,
                                "transient": transient,
                            }
                        )
                        state.setdefault("checkpoints", []).append(dict(active_checkpoint))
                        state["last_run_at"] = active_checkpoint["completed_at"]
                        state["last_summary"] = failure_summary
                        state["active_checkpoint"] = {}
                        save_watch_state(session.session_id, state)
                        append_event(
                            session.session_id,
                            kind="checkpoint",
                            content=failure_summary,
                            metadata={
                                "summary": failure_summary,
                                "mode": mode,
                                "poll": state["poll_count"],
                                "plan_id": plan_id,
                                "task_id": task_id,
                                "status": "failed",
                                "error": error_message,
                                "retry_count": attempt,
                            },
                        )
                        raise OpenClawCliError(
                            f"Watch poll {state['poll_count']} failed after {attempt} attempt(s): {error_message}"
                        ) from exc
                checkpoint_summary = str(result_text or "").strip().splitlines()[0][:160] if str(result_text or "").strip() else f"{mode} checkpoint"
                checkpoint = {
                    "poll": state["poll_count"],
                    "created_at": utc_timestamp(),
                    "completed_at": utc_timestamp(),
                    "summary": checkpoint_summary,
                    "output_path": output_path,
                    "workspace_signature": workspace_signature,
                    "status": "completed",
                    "attempt_count": attempt,
                    "progress": list(state.get("active_checkpoint", {}).get("progress") or []),
                    "attempts": list(state.get("active_checkpoint", {}).get("attempts") or []),
                }
                state.setdefault("checkpoints", []).append(checkpoint)
                state["workspace_signature"] = workspace_signature
                state["last_run_at"] = checkpoint["completed_at"]
                state["last_output_path"] = output_path
                state["last_summary"] = checkpoint_summary
                state["last_error"] = ""
                state["consecutive_failures"] = 0
                state["status"] = "running"
                state["updated_at"] = checkpoint["completed_at"]
                state["active_checkpoint"] = {}
                save_watch_state(session.session_id, state)
                append_event(
                    session.session_id,
                    kind="checkpoint",
                    content=checkpoint_summary,
                    metadata={
                        "summary": checkpoint_summary,
                        "mode": mode,
                        "poll": state["poll_count"],
                        "output_path": output_path,
                        "plan_id": plan_id,
                        "task_id": task_id,
                    },
                )
                update_session(
                    session.session_id,
                    automation_mode=mode,
                    automation_status="running",
                    watch_interval_seconds=interval_seconds,
                )
                render_watch_iteration(
                    iteration=state["poll_count"],
                    mode=mode,
                    summary=checkpoint_summary,
                    output_path=output_path,
                    output_json=config.output_json,
                )

            if max_polls and int(state.get("poll_count", 0) or 0) >= max_polls:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        active_checkpoint = state.get("active_checkpoint")
        if isinstance(active_checkpoint, dict) and active_checkpoint:
            interrupted_at = utc_timestamp()
            interruption_summary = str(active_checkpoint.get("last_message") or f"{mode} interrupted").strip()[:160]
            active_checkpoint.update(
                {
                    "status": "interrupted",
                    "completed_at": interrupted_at,
                    "summary": interruption_summary,
                }
            )
            state.setdefault("checkpoints", []).append(dict(active_checkpoint))
            state["last_run_at"] = interrupted_at
            state["last_summary"] = interruption_summary
            state["active_checkpoint"] = {}
        state["status"] = "interrupted"
        state["updated_at"] = utc_timestamp()
        save_watch_state(session.session_id, state)
        update_session(session.session_id, automation_mode=mode, automation_status="interrupted")
        if not config.output_json:
            _print_meta_footer(("resume", f"openclaw watch --resume {session.session_id}"))
        return 130

    state["status"] = "completed" if max_polls else "idle"
    state["updated_at"] = utc_timestamp()
    save_watch_state(session.session_id, state)
    update_session(
        session.session_id,
        automation_mode=mode,
        automation_status="completed" if max_polls else "idle",
        watch_interval_seconds=interval_seconds,
    )
    if not config.output_json:
        _print_meta_footer(("session", session.session_id))
    return 0


def handle_exec_command(args: argparse.Namespace) -> int:
    """Run a shell command with session tracking and CLI approvals."""
    command_parts = list(getattr(args, "shell_command", []) or [])
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if not command_parts:
        raise OpenClawCliError("A command is required after `openclaw exec --`.")
    risk_level = risk_level_from_name(getattr(args, "risk", None), default=infer_command_risk(command_parts))
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Exec: {' '.join(command_parts)[:60]}",
        cwd=getattr(args, "cwd", None),
        files=[],
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    if not request_cli_approval(
        action="shell.exec",
        target=" ".join(command_parts),
        risk_level=risk_level,
        detail=f"cwd={getattr(args, 'cwd', '') or os.getcwd()}",
        auto_approve=bool(getattr(args, "yes", False)),
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        raise OpenClawCliError("Shell command was not approved.")
    result = run_async(
        run_shell_command(
            command_parts,
            cwd=getattr(args, "cwd", None),
            timeout=int(getattr(args, "command_timeout", 60) or 60),
        )
    )
    append_event(
        session.session_id,
        kind="exec",
        content=" ".join(command_parts),
        metadata={
            "summary": f"exit {result.returncode}: {' '.join(command_parts)}",
            "cwd": result.cwd,
            "risk_level": risk_level.value,
            "returncode": result.returncode,
        },
    )
    _print_shell_result(result)
    _print_meta_footer(("session", session.session_id))
    return 0 if result.returncode == 0 else 1


def handle_edit_command(args: argparse.Namespace) -> int:
    """Edit a text file with diff previews and approval tracking."""
    path = str(getattr(args, "path", "") or "").strip()
    if not path:
        raise OpenClawCliError("A file path is required.")
    content = str(getattr(args, "content", "") or "")
    replace_values = list(getattr(args, "replace", []) or [])
    if not replace_values and not content and sys.stdin.isatty():
        raise OpenClawCliError("Provide --replace OLD NEW, --content TEXT, or pipe content on stdin.")
    if not replace_values and not content and not sys.stdin.isatty():
        content = sys.stdin.read()

    risk_level = risk_level_from_name(getattr(args, "risk", None), default=infer_file_edit_risk(path))
    session = ensure_cli_session(
        getattr(args, "session", ""),
        title=f"Edit: {Path(path).name}",
        cwd=str(Path(path).expanduser().resolve().parent),
        files=[path],
        plan_id=str(getattr(args, "plan_id", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
    )
    if not request_cli_approval(
        action="file.edit",
        target=path,
        risk_level=risk_level,
        detail=f"append={bool(getattr(args, 'append', False))} dry_run={bool(getattr(args, 'dry_run', False))}",
        auto_approve=bool(getattr(args, "yes", False)),
        session_id=session.session_id,
        plan_id=session.plan_id,
        task_id=session.task_id,
    ):
        raise OpenClawCliError("File edit was not approved.")
    if replace_values:
        result = replace_text_in_file(
            path,
            old=replace_values[0],
            new=replace_values[1],
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    else:
        result = write_text_file(
            path,
            content=content,
            append=bool(getattr(args, "append", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    append_event(
        session.session_id,
        kind="edit",
        content=path,
        metadata={
            "summary": result.summary,
            "files": [result.path],
            "changed": result.changed and not bool(getattr(args, "dry_run", False)),
            "risk_level": risk_level.value,
        },
    )
    _print_file_edit_result(result)
    _print_meta_footer(("session", session.session_id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Launch OpenClaw from the terminal.",
        epilog=(
            "Running with no prompt starts interactive chat.\n"
            "Passing a bare prompt auto-wraps to `ask`.\n"
            "`OpenClaw` is a shell shim for `openclaw` in the installer/setup scripts.\n\n"
            "Examples:\n"
            "  OpenClaw\n"
            "  openclaw \"what changed overnight?\"\n"
            "  openclaw analyze --cwd . @README.md \"summarize the repo\"\n"
            "  openclaw watch --cwd . --on-change --iterations 5 \"keep an eye on test regressions\"\n"
            "  openclaw research \"best async Python patterns\"\n"
            "  openclaw write --title \"Weekly recap\" \"Draft the report\"\n"
            "  openclaw exec -- git status\n"
            "  openclaw ask \"summarize the latest alerts\"\n"
            "  openclaw --health\n"
            "  openclaw auth login\n"
            "  openclaw auth status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {cli_version()}")
    parser.add_argument("--health", action="store_true", help="Check the OpenClaw /health endpoint and exit")
    parser.add_argument("--url", help="OpenClaw base URL (default: OPENCLAW_URL or http://localhost:8765)")
    parser.add_argument("--token", help=f"API token (default: {TOKEN_ENV_VARS}, plus macOS Keychain on macOS)")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model preference: auto, gemini, openai, anthropic, or local",
    )
    parser.add_argument(
        "--timeout",
        default=DEFAULT_TIMEOUT_SECONDS,
        type=int,
        help="HTTP timeout in seconds",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON responses")
    parser.add_argument("--user-name", help="Logical user label sent to OpenClaw")
    parser.add_argument("--client-name", help="Client/machine label for headers and telemetry")
    parser.add_argument("--session", help="Resume or tag a local CLI session")

    subparsers = parser.add_subparsers(dest="command")

    ask_parser = subparsers.add_parser("ask", help="Send a single prompt")
    ask_parser.add_argument("prompt", nargs="*", help="Prompt text (or pipe via stdin)")

    subparsers.add_parser("chat", help="Start an interactive chat session")
    subparsers.add_parser("health", help="Check the OpenClaw /health endpoint")
    auth_parser = subparsers.add_parser("auth", help="Manage stored CLI authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    login_parser = auth_subparsers.add_parser("login", help="Persist a token for future CLI use")
    login_parser.add_argument("--token", help="Token to store; if omitted, prompt securely")
    auth_subparsers.add_parser("status", help="Show where the CLI token is currently resolved from")
    auth_subparsers.add_parser("logout", help="Remove persisted CLI token(s)")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a project, directory, or file set")
    analyze_parser.add_argument("--cwd", help="Working directory to inspect")
    analyze_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    analyze_parser.add_argument("--plan-id", help="Optional related plan identifier")
    analyze_parser.add_argument("--task-id", help="Optional related task identifier")
    analyze_parser.add_argument("prompt", nargs="*", help="Analysis goal; @path references are treated as targets")

    session_parser = subparsers.add_parser("session", help="Manage local CLI sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True)
    session_create = session_subparsers.add_parser("create", help="Create a new local CLI session")
    session_create.add_argument("--title", help="Human-readable session title")
    session_create.add_argument("--cwd", help="Working directory associated with the session")
    session_create.add_argument("--file", dest="files", action="append", default=[], help="Initial tracked file or directory")
    session_create.add_argument("--plan-id", help="Optional related plan identifier")
    session_create.add_argument("--task-id", help="Optional related task identifier")
    session_list = session_subparsers.add_parser("list", help="List recent local sessions")
    session_list.add_argument("--limit", type=int, default=20, help="Maximum number of sessions to print")
    session_show = session_subparsers.add_parser("show", help="Show a local session summary")
    session_show.add_argument("session_id", help="Session identifier")
    session_resume = session_subparsers.add_parser("resume", help="Show a session and print its resume command")
    session_resume.add_argument("session_id", help="Session identifier")
    session_export = session_subparsers.add_parser("export", help="Export a local session as JSON")
    session_export.add_argument("session_id", help="Session identifier")

    plan_parser = subparsers.add_parser("plan", help="Manage agent loop plans")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command", required=True)
    plan_create = plan_subparsers.add_parser("create", help="Create a new plan")
    plan_create.add_argument("goal", nargs="*", help="Plan goal")
    plan_create.add_argument("--steps-text", default="", help="Optional newline-delimited steps")
    plan_list = plan_subparsers.add_parser("list", help="List plans")
    plan_list.add_argument("--status", default="all", help="Plan status filter")
    plan_show = plan_subparsers.add_parser("show", help="Show a plan")
    plan_show.add_argument("plan_id", help="Plan identifier")
    plan_resume = plan_subparsers.add_parser("resume", help="Resume an interrupted plan")
    plan_resume.add_argument("plan_id", help="Plan identifier")
    plan_cancel = plan_subparsers.add_parser("cancel", help="Cancel a plan")
    plan_cancel.add_argument("plan_id", help="Plan identifier")

    research_parser = subparsers.add_parser("research", help="Run deep research with saved session outputs")
    research_parser.add_argument("--cwd", help="Working directory to include as context")
    research_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    research_parser.add_argument("--plan-id", help="Optional related plan identifier")
    research_parser.add_argument("--task-id", help="Optional related task identifier")
    research_parser.add_argument("--deep", action="store_true", help="Use iterative gap-filling research mode")
    research_parser.add_argument("--output", help="Optional output file path")
    research_parser.add_argument("query", nargs="*", help="Research query; @path references are treated as targets")

    write_parser = subparsers.add_parser("write", help="Draft a document and save it to the current session")
    write_parser.add_argument("--cwd", help="Working directory to include as context")
    write_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    write_parser.add_argument("--plan-id", help="Optional related plan identifier")
    write_parser.add_argument("--task-id", help="Optional related task identifier")
    write_parser.add_argument("--title", help="Document title")
    write_parser.add_argument("--output", help="Optional output file path")
    write_parser.add_argument("task", nargs="*", help="Writing task; @path references are treated as targets")

    watch_parser = subparsers.add_parser("watch", help="Run a bounded, resumable automation watch loop")
    watch_parser.add_argument("--cwd", help="Working directory to inspect")
    watch_parser.add_argument("--file", dest="files", action="append", default=[], help="Explicit file or directory target")
    watch_parser.add_argument("--plan-id", help="Optional related plan identifier")
    watch_parser.add_argument("--task-id", help="Optional related task identifier")
    watch_parser.add_argument("--mode", choices=["analyze", "research", "write"], default="analyze", help="Watch action to run each poll")
    watch_parser.add_argument("--interval", type=int, default=30, help="Seconds between polls")
    watch_parser.add_argument("--iterations", type=int, default=5, help="Maximum polls before exiting (0 means keep running)")
    watch_parser.add_argument("--on-change", action="store_true", help="Skip iterations until tracked workspace content changes")
    watch_parser.add_argument("--resume", help="Resume a prior watch session by session id")
    watch_parser.add_argument("--deep", action="store_true", help="Use deep research when mode=research")
    watch_parser.add_argument("--title", help="Document title override when mode=write")
    watch_parser.add_argument("--output", help="Optional output file to overwrite each poll")
    watch_parser.add_argument("goal", nargs="*", help="Automation goal; @path references are treated as targets")

    exec_parser = subparsers.add_parser("exec", help="Run a shell command with session and approval tracking")
    exec_parser.add_argument("--cwd", help="Working directory for the command")
    exec_parser.add_argument("--command-timeout", type=int, default=60, help="Shell command timeout in seconds")
    exec_parser.add_argument("--risk", choices=["low", "medium", "high", "critical"], help="Override the inferred command risk")
    exec_parser.add_argument("--yes", action="store_true", help="Auto-approve high-risk commands")
    exec_parser.add_argument("--plan-id", help="Optional related plan identifier")
    exec_parser.add_argument("--task-id", help="Optional related task identifier")
    exec_parser.add_argument("shell_command", nargs=argparse.REMAINDER, help="Command to execute; prefix with -- to stop option parsing")

    edit_parser = subparsers.add_parser("edit", help="Apply a text edit with diff preview support")
    edit_parser.add_argument("path", help="File path to edit")
    edit_parser.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"), help="Replace text in the file")
    edit_parser.add_argument("--content", help="Replace the full file content (or append with --append)")
    edit_parser.add_argument("--append", action="store_true", help="Append content instead of replacing the file")
    edit_parser.add_argument("--dry-run", action="store_true", help="Preview the diff without writing the file")
    edit_parser.add_argument("--risk", choices=["low", "medium", "high", "critical"], help="Override the inferred edit risk")
    edit_parser.add_argument("--yes", action="store_true", help="Auto-approve high-risk edits")
    edit_parser.add_argument("--plan-id", help="Optional related plan identifier")
    edit_parser.add_argument("--task-id", help="Optional related task identifier")
    subparsers.add_parser("status", help="Show version, server health, and token status")
    subparsers.add_parser("update", help="Upgrade openclaw to the latest version from PyPI")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    known_commands = {
        "ask",
        "chat",
        "health",
        "auth",
        "analyze",
        "session",
        "plan",
        "research",
        "write",
        "watch",
        "exec",
        "edit",
        "update",
        "status",
    }

    # Skip background update check when the user is explicitly running `openclaw update`.
    _skip_update_check = bool(raw_argv and raw_argv[0] == "update")
    if not _skip_update_check:
        # Run update check synchronously in a thread so we can join it before
        # drawing the readline prompt. The thread only sets _latest_version;
        # we print the notice in the main thread after joining so it never
        # appears interleaved with the REPL prompt.
        def _update_check_worker() -> None:
            global _latest_version, _standalone_needs_update
            install_dir = _standalone_install_dir()
            if install_dir:
                # Standalone: compare file hashes against the server
                try:
                    import hashlib
                    base_url = os.getenv("OPENCLAW_URL", "http://192.168.1.93:8765").rstrip("/")
                    url = f"{base_url}/cli-update/meta"
                    import urllib.request as _ur
                    with _ur.urlopen(_ur.Request(url), timeout=3.0) as resp:
                        server_hashes: dict[str, str] = json.loads(resp.read())
                    for fname, server_hash in server_hashes.items():
                        local_path = Path(install_dir) / fname
                        if local_path.exists():
                            local_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
                            if local_hash != server_hash:
                                _standalone_needs_update = True
                                break
                        else:
                            _standalone_needs_update = True
                            break
                except Exception:
                    pass
            else:
                # Standard install: check PyPI
                latest = _fetch_latest_pypi_version(timeout=3.0)
                if latest:
                    _latest_version = latest

        _update_thread: threading.Thread | None = threading.Thread(
            target=_update_check_worker, daemon=True
        )
        _update_thread.start()
    else:
        _update_thread = None

    if raw_argv and raw_argv[0] not in known_commands and not raw_argv[0].startswith("-"):
        raw_argv = ["ask", *raw_argv]
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    command = "health" if getattr(args, "health", False) else args.command or "chat"

    # Wait for the update check then print the notice from the main thread,
    # guaranteeing it appears before any output (including the REPL prompt).
    if _update_thread is not None:
        _update_thread.join(timeout=3.5)
    current_ver = cli_version()
    if _standalone_needs_update:
        _print_update_notice(current_ver, None)  # standalone: no version string
    elif _latest_version and _version_tuple(_latest_version) > _version_tuple(current_ver):
        _print_update_notice(current_ver, _latest_version)

    try:
        if command == "auth":
            return handle_auth_command(args)
        if command == "session":
            return handle_session_command(args)
        if command == "update":
            return handle_update_command(args)
        config = build_config(args)
        if command == "status":
            return handle_status_command(args, config=config)
        if command in {"ask", "chat"} and config.session_id:
            require_session(config.session_id)

        if command in {"ask", "chat", "analyze", "write", "watch"}:
            maybe_warn_missing_token(config)
        if command == "chat":
            session = None
            if config.session_id:
                session = require_session(config.session_id)
            elif getattr(args, "session", ""):
                session = require_session(getattr(args, "session", ""))
            session_id = session.session_id if session else ""
            scoped_config = bind_config_to_session(config, session_id) if session_id else config
            if session_id:
                return run_chat(scoped_config, session_id=session_id)
            return run_chat(scoped_config)
        if command == "health":
            health = fetch_health(config=config)
            print_health(health, output_json=config.output_json)
            return 0
        if command == "plan":
            return handle_plan_command(args, session_id=config.session_id)
        if command == "analyze":
            return handle_analyze_command(args, config=config)
        if command == "research":
            return handle_research_command(args)
        if command == "write":
            return handle_write_command(args, config=config)
        if command == "watch":
            return handle_watch_command(args, config=config)
        if command == "exec":
            return handle_exec_command(args)
        if command == "edit":
            return handle_edit_command(args)

        prompt = parse_prompt(args.prompt)
        if not prompt:
            parser.error("prompt is required unless you pipe text on stdin")
        history = load_conversation_history(config.session_id) if config.session_id else None
        if history is None:
            response = _with_spinner("💬 Thinking…", invoke_openclaw, prompt, config=config, output_json=config.output_json)
        else:
            response = _with_spinner("💬 Thinking…", invoke_openclaw, prompt, config=config, history=history, output_json=config.output_json)
        print_response(response, output_json=config.output_json)
        if config.session_id:
            append_event(config.session_id, kind="prompt", content=prompt, metadata={"summary": prompt})
            persist_response(config.session_id, prompt, response.response)
        return 0
    except OpenClawCliError as exc:
        _base = ""
        try:
            _base = config.base_url
        except Exception:
            pass
        _print_connection_error_panel(str(exc), base_url=_base)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
