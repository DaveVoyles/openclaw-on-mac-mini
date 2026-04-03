"""
OpenClaw slash commands — package version.

All slash commands *except* ``/ask`` (which stays in bot.py) are registered
here via ``register_commands(bot)``.  This function is called once after the
bot object is created.

Backward-compatible: ``from discord_commands import register_commands`` still works.
"""

import logging

from ._helpers import (  # noqa: F401 — re-exported for backward compat
    ALLOWED_USER_IDS,
    _get_http_session,
    _is_allowed,
    require_auth,
    truncate_for_embed,
)
from .agent import _register_agent_commands
from .code import _register_code_commands
from .comms import _register_comms_commands
from .context_menus import _register_context_menus
from .conversation import _register_conversation_commands
from .media import _register_media_commands
from .monitoring import _register_monitoring_commands
from .safety import _register_safety_commands
from .schedule import _register_schedule_commands
from .skills import _register_skills_commands
from .system import _register_system_commands
from .utility import _register_utility_commands

log = logging.getLogger("openclaw")


def register_commands(bot):  # noqa: C901 — large but flat
    """Register all slash commands (except /ask) on *bot*.tree."""

    # Import send_morning_briefing lazily to avoid circular deps
    from discord_background import send_morning_briefing

    _register_utility_commands(bot)
    _register_conversation_commands(bot)
    _register_system_commands(bot)
    _register_schedule_commands(bot)
    _register_skills_commands(bot)
    _register_safety_commands(bot)
    _register_comms_commands(bot)
    _register_agent_commands(bot)
    _register_code_commands(bot)
    _register_media_commands(bot, send_morning_briefing)
    _register_monitoring_commands(bot)
    _register_context_menus(bot)

    log.info("Registered %d standalone slash commands", 32)
