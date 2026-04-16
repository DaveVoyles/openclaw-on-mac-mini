"""OpenClaw permission checks — extracted from bot.py for modularity."""

import functools
import logging
import os
from enum import Enum
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
        except (yaml.YAMLError, OSError, TypeError) as exc:
            _permissions_cache = _permissions_cache or {}
    else:
        _permissions_cache = {}
    _permissions_mtime = current_mtime
    return _permissions_cache


# ---------------------------------------------------------------------------
# Plugin permission levels
# ---------------------------------------------------------------------------


class PermissionLevel(Enum):
    PUBLIC = 0   # Anyone can use
    MEMBER = 1   # Server members only (no DMs)
    TRUSTED = 2  # Users with a specific role
    ADMIN = 3    # Server administrators
    OWNER = 4    # Bot owner only


_DEFAULT_PLUGIN_LEVEL = PermissionLevel.MEMBER

# Per-plugin overrides: {"plugin_name": PermissionLevel}
_PLUGIN_PERMISSIONS: dict[str, PermissionLevel] = {}


def set_plugin_permission(plugin_name: str, level: PermissionLevel) -> None:
    """Set the required permission level for a plugin."""
    _PLUGIN_PERMISSIONS[plugin_name] = level


def get_plugin_permission(plugin_name: str) -> PermissionLevel:
    """Return the required permission level for a plugin (defaults to MEMBER)."""
    return _PLUGIN_PERMISSIONS.get(plugin_name, _DEFAULT_PLUGIN_LEVEL)


def check_permission(
    level: PermissionLevel,
    interaction: discord.Interaction,
    trusted_role_id: int | None = None,
    owner_id: int | None = None,
) -> bool:
    """Return True if the interaction's user meets *level*.

    Args:
        level: The minimum required PermissionLevel.
        interaction: The Discord interaction to evaluate.
        trusted_role_id: Role ID that grants TRUSTED access (optional).
        owner_id: Bot owner user ID for OWNER checks (optional).
    """
    user = interaction.user

    if level == PermissionLevel.PUBLIC:
        return True

    if level == PermissionLevel.MEMBER:
        return interaction.guild is not None

    if level == PermissionLevel.TRUSTED:
        if not interaction.guild or not trusted_role_id:
            return False
        member = interaction.guild.get_member(user.id)
        return member is not None and any(r.id == trusted_role_id for r in member.roles)

    if level == PermissionLevel.ADMIN:
        if not interaction.guild:
            return False
        member = interaction.guild.get_member(user.id)
        return member is not None and member.guild_permissions.administrator

    if level == PermissionLevel.OWNER:
        if owner_id is None:
            return False
        return user.id == owner_id

    return False


def check_plugin_permission(
    plugin_name: str,
    interaction: discord.Interaction,
    trusted_role_id: int | None = None,
    owner_id: int | None = None,
) -> bool:
    """Return True if the interaction user may invoke *plugin_name*."""
    level = get_plugin_permission(plugin_name)
    return check_permission(level, interaction, trusted_role_id=trusted_role_id, owner_id=owner_id)


# ---------------------------------------------------------------------------
# Service / skill permission checks (reads config/permissions.yaml)
# ---------------------------------------------------------------------------


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
