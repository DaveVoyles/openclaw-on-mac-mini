"""OpenClaw permission checks — extracted from bot.py for modularity."""

import functools
import logging
import os
from pathlib import Path

import discord
import yaml

from config import cfg

log = logging.getLogger("openclaw.permissions")

# ---------------------------------------------------------------------------
# User allow-list
# ---------------------------------------------------------------------------

ALLOWED_USER_IDS: list[int] = cfg.allowed_user_ids

# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


def is_allowed(interaction: discord.Interaction) -> bool:
    """Return True if the invoking user is on the allow-list."""
    if not ALLOWED_USER_IDS:
        return True
    return interaction.user.id in ALLOWED_USER_IDS


def require_auth(func):
    """Decorator that gates a slash-command handler behind the allow-list."""

    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.", ephemeral=True
            )
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Service / skill permission checks (reads config/permissions.yaml)
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))

_permissions_cache: dict | None = None
_permissions_mtime: float = 0.0


def _load_permissions() -> dict:
    global _permissions_cache, _permissions_mtime
    perms_file = CONFIG_DIR / "permissions.yaml"
    try:
        current_mtime = perms_file.stat().st_mtime if perms_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _permissions_cache is not None and current_mtime == _permissions_mtime:
        return _permissions_cache
    if perms_file.exists():
        try:
            with open(perms_file) as f:
                _permissions_cache = yaml.safe_load(f) or {}
        except Exception as exc:
            log.warning("Failed to parse permissions YAML: %s", exc)
            _permissions_cache = _permissions_cache or {}
    else:
        _permissions_cache = {}
    _permissions_mtime = current_mtime
    return _permissions_cache


def is_service_allowed(skill: str, service: str) -> bool:
    """Check permissions.yaml to see if a service is allowed for a skill."""
    perms = _load_permissions()
    cmd_perms = perms.get("commands", {}).get(skill, {})
    denied = cmd_perms.get("denied_services", [])
    allowed = cmd_perms.get("allowed_services", [])
    if service in denied:
        return False
    if allowed and service not in allowed:
        return False
    return True
