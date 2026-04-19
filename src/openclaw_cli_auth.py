"""
openclaw_cli_auth — Authentication and token management.

Leaf module: no imports from other openclaw_cli_* modules.
Handles keychain, environment variable, and file-based token resolution.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared exception (defined here so auth is a leaf; re-exported by main)
# ---------------------------------------------------------------------------


class OpenClawCliError(RuntimeError):
    """Raised when the CLI cannot talk to the OpenClaw API."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYCHAIN_SERVICE = "OpenClaw CLI"
TOKEN_ENV_VARS = "OPENCLAW_TOKEN or DASHBOARD_API_TOKEN"
AUTH_FILE_NAME = "token"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TokenResolution:
    """Resolved token plus the source it came from."""

    token: str
    source: str


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


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
