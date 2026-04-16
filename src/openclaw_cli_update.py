"""
openclaw_cli_update — Version checking and self-update management.

Leaf module: no imports from other openclaw_cli_* modules except ui_core (for ANSI).
Handles PyPI version polling, standalone binary replacement, and update notices.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from importlib import metadata
from pathlib import Path
from urllib import request

from openclaw_cli_ui_core import (
    _BCY,
    _BGR,
    _BYE,
    _DM,
    _IS_TTY,
    _R,
)

try:
    from rich.console import Console as _RichConsole
    _RICH_CONSOLE = _RichConsole(highlight=False)
    _RICH_ERR = _RichConsole(stderr=True, highlight=False)
    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RICH_AVAILABLE = False

# Mirror constants (kept in sync with openclaw_cli.py)
DEFAULT_BASE_URL = "http://localhost:8765"
DEFAULT_VERSION = "2026.4.16"
_CLI_BUILD = "wave48"  # updated with each UX wave batch

# Module-level state (mirrors what was in openclaw_cli.py)
_latest_version: str | None = None
_standalone_needs_update: bool = False


def cli_version() -> str:
    """Return the installed CLI version when available."""
    try:
        return f"{metadata.version('openclaw')}+{_CLI_BUILD}"
    except metadata.PackageNotFoundError:
        return f"{DEFAULT_VERSION}+{_CLI_BUILD}"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Convert a version string like '2026.3.20' or '0.6.0+wave45' to a comparable tuple."""
    try:
        # Strip any build metadata suffix (e.g. '+wave45') before splitting.
        base = v.split("+")[0]
        return tuple(int(x) for x in base.split("."))
    except (ValueError, TypeError):
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
    except Exception:  # broad: intentional — urlopen can raise many exception types (OSError, timeout, etc.)
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

    Returns None for editable pip installs (``pip install -e .``) even though
    the source files live outside site-packages, so that /update never
    overwrites the developer's working source tree with files from the server.
    """
    try:
        script = Path(__file__).resolve()
        marker = script.parent / "openclaw_cli_sessions.py"
        if marker.exists() and "site-packages" not in str(script):
            # Editable installs: pyproject.toml lives one level up (repo root).
            # Don't treat a dev checkout as a standalone install.
            if (script.parent.parent / "pyproject.toml").exists():
                return None
            return str(script.parent)
    except (OSError, AttributeError):
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
        except (OSError, ValueError) as exc:
            failed.append((fname, str(exc)))
            if _RICH_AVAILABLE and _IS_TTY:
                _RICH_CONSOLE.print("  [red]✗[/]")
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
        base_url = (os.getenv("OPENCLAW_URL") or DEFAULT_BASE_URL).rstrip("/")
        return _update_standalone_install(install_dir, current=current, base_url=base_url)

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
