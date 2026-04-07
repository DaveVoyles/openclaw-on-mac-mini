"""
Parametric smoke tests for all Discord cogs.

These tests verify that every cog module:
  1. Imports without errors under mocked discord/aiohttp/LLM environment.
  2. Can be instantiated with a mock bot.

They are intentionally lightweight — the goal is to raise coverage from 0%
(import failure = no coverage counted) and catch import-time regressions like
the memory_manager deletion.  Deeper behavioral tests live in their own files.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs to satisfy cog imports without real Discord / LLM setup
# ---------------------------------------------------------------------------

def _make_discord_stub():
    """Return a minimal discord stub that covers the attrs all cogs reference."""
    discord = MagicMock()
    discord.ext = MagicMock()
    discord.ext.commands = MagicMock()

    # commands.Cog base class — needs to be a real class so cog classes inherit OK
    class _CogBase:
        __cog_app_commands__: list = []
        __cog_commands__: list = []
        __cog_listeners__: list = []
        __cog_is_app_commands_group__ = False

        def __init__(self, bot=None):
            self.bot = bot
            # Each subclass gets its own list (not shared with parent)
            self.__cog_app_commands__ = []

        def __init_subclass__(cls, name=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.__cog_app_commands__ = []
            cls.__cog_commands__ = []
            cls.__cog_listeners__ = []

        @classmethod
        def _get_overridden_method(cls, method):  # discord.py internal
            return None

    class _GroupCog(_CogBase):
        def __init_subclass__(cls, group_name=None, group_description=None, **kwargs):
            super().__init_subclass__(**kwargs)

    discord.ext.commands.Cog = _CogBase
    discord.ext.commands.GroupCog = _GroupCog
    discord.ext.commands.group = lambda *a, **kw: (lambda f: f)
    discord.ext.commands.command = lambda *a, **kw: (lambda f: f)
    discord.ext.commands.hybrid_command = lambda *a, **kw: (lambda f: f)
    discord.ext.commands.has_permissions = lambda **kw: (lambda f: f)
    discord.ext.commands.cooldown = lambda *a, **kw: (lambda f: f)
    discord.ext.commands.BucketType = MagicMock()
    discord.ext.commands.errors = MagicMock()
    discord.ext.commands.Context = MagicMock()
    discord.ext.commands.Bot = MagicMock()
    discord.app_commands = MagicMock()
    discord.app_commands.command = lambda *a, **kw: (lambda f: f)
    discord.app_commands.describe = lambda **kw: (lambda f: f)
    discord.app_commands.choices = lambda **kw: (lambda f: f)
    discord.app_commands.Choice = MagicMock()
    discord.app_commands.guild_only = lambda: (lambda f: f)
    discord.app_commands.default_permissions = lambda **kw: (lambda f: f)
    discord.app_commands.context_menu = lambda *a, **kw: (lambda f: f)
    discord.app_commands.AppCommandError = Exception
    discord.app_commands.errors = MagicMock()
    discord.app_commands.checks = MagicMock()
    discord.app_commands.checks.has_permissions = lambda **kw: (lambda f: f)
    discord.ui = MagicMock()
    discord.ui.View = type("View", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
    discord.ui.button = lambda *a, **kw: (lambda f: f)
    discord.ui.select = lambda *a, **kw: (lambda f: f)
    discord.ui.Modal = type("Modal", (), {"__init_subclass__": classmethod(lambda cls, **kw: None)})
    discord.ui.TextInput = MagicMock()
    discord.Embed = MagicMock
    discord.Color = MagicMock()
    discord.Color.green = MagicMock(return_value=0)
    discord.Color.gold = MagicMock(return_value=0)
    discord.Color.red = MagicMock(return_value=0)
    discord.Color.blue = MagicMock(return_value=0)
    discord.Color.blurple = MagicMock(return_value=0)
    discord.Color.orange = MagicMock(return_value=0)
    discord.Color.purple = MagicMock(return_value=0)
    discord.Color.teal = MagicMock(return_value=0)
    discord.Interaction = MagicMock()
    discord.Member = MagicMock()
    discord.User = MagicMock()
    discord.Message = MagicMock()
    discord.TextChannel = MagicMock()
    discord.ButtonStyle = MagicMock()
    discord.SelectOption = MagicMock()
    discord.PermissionOverwrite = MagicMock()
    discord.utils = MagicMock()
    discord.errors = MagicMock()
    discord.errors.Forbidden = Exception
    discord.errors.NotFound = Exception
    discord.errors.HTTPException = Exception
    discord.Attachment = MagicMock()
    discord.File = MagicMock()
    discord.AllowedMentions = MagicMock
    discord.NotFound = Exception
    discord.Forbidden = Exception
    discord.HTTPException = Exception
    discord.Object = MagicMock
    discord.ChannelType = MagicMock()
    discord.enums = MagicMock()
    return discord


def _common_stubs() -> dict:
    """Build a sys.modules patch dict for all common cog dependencies."""
    discord = _make_discord_stub()

    stubs: dict[str, MagicMock] = {
        "discord": discord,
        "discord.ext": discord.ext,
        "discord.ext.commands": discord.ext.commands,
        "discord.app_commands": discord.app_commands,
        "discord.ui": discord.ui,
        # LLM / config
        "config": MagicMock(ALLOWED_GUILD_IDS=[], OWNER_USER_ID=1, BOT_PREFIX="!"),
        "llm": MagicMock(),
        "llm.chat": MagicMock(chat=AsyncMock()),
        # Memory
        "memory": MagicMock(store_memory=AsyncMock(), recall_memories=AsyncMock(return_value=[]), forget_memory=AsyncMock(), memory_stats=AsyncMock(return_value={}), store=AsyncMock(), recall=AsyncMock(return_value=[]), forget=AsyncMock(), stats=AsyncMock(return_value={})),
        # Skills / utilities
        "skills": MagicMock(SKILLS={}),
        "skills.advanced_skills": MagicMock(),
        "skills.digest_skills": MagicMock(),
        "skills.finance_skills": MagicMock(),
        "skills.health_skills": MagicMock(),
        "skills.media_skills": MagicMock(),
        "skills.news_skills": MagicMock(),
        "cog_helpers": MagicMock(audit_log=MagicMock(), check_allowed=MagicMock(return_value=True)),
        "permissions": MagicMock(),
        "utils": MagicMock(),
        "utils.text": MagicMock(),
        "utils.time": MagicMock(),
        "utils.discord": MagicMock(),
        # Individual skill modules cogs may import
        "todo_manager": MagicMock(),
        "reminder_manager": MagicMock(),
        "digest_manager": MagicMock(),
        "spending": MagicMock(),
        "spending.tracker": MagicMock(),
        "qmd": MagicMock(),
        "vector_store": MagicMock(),
        "rules_engine": MagicMock(),
        "user_profile": MagicMock(load_profile=MagicMock(return_value={})),
        "calendar_skills": MagicMock(),
        "email_skills": MagicMock(),
        "research_skills": MagicMock(),
        "note_skills": MagicMock(),
        "media_skills": MagicMock(),
        "nas_skills": MagicMock(),
        "dns_skills": MagicMock(),
        "network_skills": MagicMock(),
        "docker_skills": MagicMock(),
        "github_skills": MagicMock(),
        "gdoc_skills": MagicMock(),
        "imagine_skills": MagicMock(),
        "imdb_skills": MagicMock(),
        "translate_skills": MagicMock(),
        "sms_skills": MagicMock(),
        "ntfy_skills": MagicMock(),
        "notion_skills": MagicMock(),
        "rss_skills": MagicMock(),
        "sentry_skills": MagicMock(),
        "poll_skills": MagicMock(),
        "perf_skills": MagicMock(),
        "habit_skills": MagicMock(),
        "interview_skills": MagicMock(),
        "expense_skills": MagicMock(),
        "decision_skills": MagicMock(),
        "decision_workflows": MagicMock(DecisionStore=MagicMock(return_value=MagicMock()), DecisionVote=MagicMock(), compute_weighted_outcome=MagicMock(), parse_role_weights=MagicMock(), role_aware_summary=MagicMock()),
        "doc_skills": MagicMock(),
        "journal_skills": MagicMock(),
        "analytics_skills": MagicMock(),
        "review_skills": MagicMock(),
        "dream_skills": MagicMock(),
        "channel_profile_skills": MagicMock(),
        "notify_skills": MagicMock(),
        "incident_copilot": MagicMock(),
        "health_history": MagicMock(),
        "llm_tools": MagicMock(),
        "memory_cog_helpers": MagicMock(),
        "scheduler": MagicMock(),
        "recap_manager": MagicMock(),
        "subprocess_utils": MagicMock(COMMAND_TIMEOUT=30, run=AsyncMock()),
        "aiohttp": MagicMock(),
        "aiofiles": MagicMock(),
        "croniter": MagicMock(),
        "openai": MagicMock(),
        "anthropic": MagicMock(),
        "google": MagicMock(),
        "google.generativeai": MagicMock(),
    }
    return stubs


# ---------------------------------------------------------------------------
# Parametrize: (module_name, class_name)
# ---------------------------------------------------------------------------

COG_PARAMS = [
    ("analytics_cog", "AnalyticsCog"),
    ("calendar_cog", "CalendarCog"),
    ("channel_profile_cog", "ChannelProfileCog"),
    ("context_cog", "ContextMenuCog"),
    ("decision_cog", "DecisionCog"),
    ("digest_cog", "DigestCog"),
    ("dns_cog", "DnsCog"),
    ("doc_cog", "DocCog"),
    ("docker_cog", "DockerCog"),
    ("dream_cog", "DreamCog"),
    ("email_cog", "EmailCog"),
    ("expense_cog", "ExpenseCog"),
    ("gdoc_cog", "GDocCog"),
    ("github_cog", "GitHubCog"),
    ("habit_cog", "HabitCog"),
    ("imagine_cog", "ImagineCog"),
    ("imdb_cog", "ImdbCog"),
    ("incident_cog", "IncidentCog"),
    ("interview_cog", "InterviewCog"),
    ("journal_cog", "JournalCog"),
    ("media_cog", "MediaCog"),
    ("memory_cog", "MemoryCog"),
    ("nas_cog", "NasCog"),
    ("network_cog", "NetworkCog"),
    ("note_cog", "NoteCog"),
    ("notify_cog", "NotifyCog"),
    ("notion_cog", "NotionCog"),
    ("ntfy_cog", "NtfyCog"),
    ("perf_cog", "PerfCog"),
    ("poll_cog", "PollCog"),
    ("reminder_cog", "ReminderCog"),
    ("reports_cog", "ReportsCog"),
    ("research_cog", "ResearchCog"),
    ("review_cog", "ReviewCog"),
    ("rss_cog", "RSSCog"),
    ("sentry_cog", "SentryCog"),
    ("sms_cog", "SMSCog"),
    ("todo_cog", "TodoCog"),
    ("translate_cog", "TranslateCog"),
]


@pytest.mark.parametrize("module_name,class_name", COG_PARAMS, ids=[p[0] for p in COG_PARAMS])
def test_cog_imports_and_instantiates(module_name: str, class_name: str):
    """Each cog must import cleanly and its Cog class must accept a mock bot."""
    stubs = _common_stubs()

    # Force fresh import for each test — remove cached module if present
    full_module = f"cogs.{module_name}"
    stubs.pop(full_module, None)
    stubs.pop("cogs", None)

    mock_bot = MagicMock()
    mock_bot.loop = MagicMock()
    mock_bot.user = MagicMock()

    with patch.dict(sys.modules, stubs):
        import importlib

        # Remove from cache so patch.dict takes effect
        sys.modules.pop(full_module, None)
        sys.modules.pop("cogs", None)

        mod = importlib.import_module(full_module)
        cog_class = getattr(mod, class_name)
        instance = cog_class(mock_bot)

    assert instance is not None
    assert isinstance(instance, cog_class)
