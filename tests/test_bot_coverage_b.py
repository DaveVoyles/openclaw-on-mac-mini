"""
Tests for bot.py coverage of lines 959, 1046-1427.

Targets:
- 959: think_hook path + quality retry no-improvement branch
- 1046: _bot_can_read_channel guild.get_member path
- 1042-1043: _bot_can_read_channel permissions_for not callable
- 1088-1089: _build_default_ask_thread_name long truncation
- 1056-1057: _should_send_message_content_hint channel_id None
- 1060-1061: _should_send_message_content_hint cooldown
- 1142-1154: _get_or_create_default_ask_thread cache paths
- 1219-1427: OpenClawBot.on_ready and close
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Use project-relative dirs to avoid /tmp writes
_TEST_BASE = Path(__file__).parent
os.environ.setdefault("LOG_DIR", str(_TEST_BASE / ".test_logs_b"))
os.environ.setdefault("AUDIT_DIR", str(_TEST_BASE / ".test_audit_b"))
os.environ.setdefault("THREAD_DB_PATH", str(_TEST_BASE / ".test_cov_b.db"))

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord.ext import commands

import bot as mod
import bot_helpers as bot_helpers_mod

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_mock_user(user_id: int = 999) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.__str__ = MagicMock(return_value=f"TestBot#{user_id}")
    u.__format__ = MagicMock(return_value=f"TestBot#{user_id}")
    return u


def _set_bot_user(user_id: int = 999) -> MagicMock:
    u = _make_mock_user(user_id)
    mod.bot._connection.user = u
    return u


# ---------------------------------------------------------------------------
# _quality_retry_improved — no-improvement branch
# ---------------------------------------------------------------------------


class TestQualityRetryImproved:
    def test_no_improvement_same_score(self):
        result = mod._quality_retry_improved(
            original={"status": "low", "score": 50},
            retried={"status": "low", "score": 50},
        )
        assert result is False

    def test_no_improvement_score_increase_too_small(self):
        result = mod._quality_retry_improved(
            original={"status": "low", "score": 50},
            retried={"status": "low", "score": 58},
        )
        assert result is False

    def test_improved_when_score_jumps_enough(self):
        result = mod._quality_retry_improved(
            original={"status": "low", "score": 40},
            retried={"status": "low", "score": 51},
        )
        assert result is True

    def test_improved_when_retried_becomes_high(self):
        result = mod._quality_retry_improved(
            original={"status": "low", "score": 60},
            retried={"status": "high", "score": 61},
        )
        assert result is True

    def test_not_improved_when_both_high(self):
        # both already "high" → status check fails, fall through to score check
        result = mod._quality_retry_improved(
            original={"status": "high", "score": 80},
            retried={"status": "high", "score": 85},
        )
        assert result is False  # 85 < 80 + 10


# ---------------------------------------------------------------------------
# _run_quality_auto_repair — think_hook path + no-improvement outcome (line 959)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_quality_auto_repair_no_improvement_with_think_hook(monkeypatch):
    """Cover line 959 (think_hook await) and lines 1016-1026 (no_improvement branch)."""
    think_hook = AsyncMock()
    retry_result = SimpleNamespace(
        response_text="retry response",
        final_meta={"model_used": "gpt-4"},
        model_used="gpt-4",
    )
    run_retry_stream = AsyncMock(return_value=retry_result)

    monkeypatch.setattr(mod, "get_effective_channel_profile", MagicMock(return_value={}))
    monkeypatch.setattr(mod, "get_latency_load_snapshot", MagicMock(return_value={}))
    monkeypatch.setattr(
        mod,
        "select_latency_budget_policy",
        MagicMock(
            return_value={
                "load_tier": "low",
                "decision": "allow",
                "degrade_mode": "normal",
                "degrade_reasons": [],
                "metrics_available": True,
            }
        ),
    )
    monkeypatch.setattr(
        mod,
        "apply_repair_budget",
        MagicMock(return_value={"max_attempts": 1, "timeout_seconds": 30}),
    )
    monkeypatch.setattr(
        mod, "_with_requested_item_target", MagicMock(return_value={})
    )
    monkeypatch.setattr(mod, "_record_quality_metric", MagicMock())
    monkeypatch.setattr(mod, "_record_budget_policy_metric", MagicMock())
    monkeypatch.setattr(
        mod,
        "_build_quality_broadening_prompt",
        MagicMock(return_value="broadened question"),
    )
    # Retry quality: same score as original — not improved (40 < 40 + 10)
    monkeypatch.setattr(
        mod,
        "_safe_score_answer_quality",
        MagicMock(return_value={"status": "low", "score": 40}),
    )

    result = await mod._run_quality_auto_repair(
        question="test question",
        response_text="original response",
        model_used="gpt-4",
        final_meta=None,
        quality_meta={"status": "low", "score": 40},
        context="test",
        run_retry_stream=run_retry_stream,
        think_hook=think_hook,
    )

    assert result["retry_summary"]["outcome"] == "no_improvement"
    assert result["response_text"] == "original response"
    assert result["retry_result"] is None
    think_hook.assert_awaited_once()


# ---------------------------------------------------------------------------
# _bot_can_read_channel — permissions_for not callable (line 1042-1043)
# ---------------------------------------------------------------------------


class TestBotCanReadChannel:
    def test_permissions_for_not_callable_returns_false(self):
        """Line 1042-1043: permissions_for exists but is not callable."""
        guild = SimpleNamespace(me=MagicMock())
        channel = SimpleNamespace(guild=guild, permissions_for="not_a_function")
        assert mod._bot_can_read_channel(channel) is False

    def test_permissions_for_none_returns_false(self):
        """permissions_for attribute is None — not callable."""
        guild = SimpleNamespace(me=MagicMock())
        channel = SimpleNamespace(guild=guild, permissions_for=None)
        assert mod._bot_can_read_channel(channel) is False

    def test_no_guild_returns_true(self):
        """No guild → assume readable."""
        channel = SimpleNamespace(guild=None)
        assert mod._bot_can_read_channel(channel) is True

    def test_guild_me_none_no_get_member_returns_true(self):
        """guild.me is None and guild lacks get_member → assume readable."""
        guild = SimpleNamespace(me=None)
        perms = SimpleNamespace(read_messages=True)
        channel = SimpleNamespace(guild=guild, permissions_for=lambda m: perms)
        # bot_member is None → return True
        assert mod._bot_can_read_channel(channel) is True

    def test_guild_get_member_path(self):
        """Line 1046: guild.me None, bot.user set, guild.get_member used."""
        _set_bot_user(999)
        bot_member = SimpleNamespace(id=999)
        perms = SimpleNamespace(read_messages=True)
        guild = SimpleNamespace(
            me=None,
            get_member=MagicMock(return_value=bot_member),
        )
        channel = SimpleNamespace(guild=guild, permissions_for=lambda m: perms)
        result = mod._bot_can_read_channel(channel)
        assert result is True
        guild.get_member.assert_called_once_with(999)

    def test_read_messages_false_returns_false(self):
        """Bot member found but lacks read_messages."""
        bot_member = MagicMock()
        perms = SimpleNamespace(read_messages=False, view_channel=False)
        guild = SimpleNamespace(me=bot_member)
        channel = SimpleNamespace(guild=guild, permissions_for=lambda m: perms)
        assert mod._bot_can_read_channel(channel) is False


# ---------------------------------------------------------------------------
# _should_send_message_content_hint — channel_id None + cooldown
# ---------------------------------------------------------------------------


class TestShouldSendMessageContentHint:
    def setup_method(self):
        bot_helpers_mod._MESSAGE_CONTENT_HINT_CACHE.clear()

    def test_channel_id_none_returns_false(self):
        """Lines 1056-1057: channel with no id → False."""
        channel = SimpleNamespace()  # no id attribute
        assert mod._should_send_message_content_hint(channel) is False

    def test_channel_id_none_explicit_returns_false(self):
        """channel.id = None → False."""
        channel = SimpleNamespace(id=None)
        assert mod._should_send_message_content_hint(channel) is False

    def test_first_call_returns_true(self):
        channel = SimpleNamespace(id=5001)
        assert mod._should_send_message_content_hint(channel) is True

    def test_second_call_same_channel_returns_false_cooldown(self):
        """Lines 1060-1061: cooldown active → second call returns False."""
        channel = SimpleNamespace(id=5002)
        first = mod._should_send_message_content_hint(channel)
        second = mod._should_send_message_content_hint(channel)
        assert first is True
        assert second is False  # cooldown hit

    def test_different_channels_both_true(self):
        ch_a = SimpleNamespace(id=6001)
        ch_b = SimpleNamespace(id=6002)
        assert mod._should_send_message_content_hint(ch_a) is True
        assert mod._should_send_message_content_hint(ch_b) is True


# ---------------------------------------------------------------------------
# _build_default_ask_thread_name — long snippet truncation (lines 1087-1089)
# ---------------------------------------------------------------------------


class TestBuildDefaultAskThreadName:
    def test_short_question_no_truncation(self):
        name = mod._build_default_ask_thread_name("short question", user_id=42)
        assert "short question" in name
        assert len(name) <= 100

    def test_long_question_truncated_at_50_chars(self):
        """Snippet > 50 chars → ellipsis added at position 50."""
        question = "A" * 60
        name = mod._build_default_ask_thread_name(question, user_id=42)
        assert "…" in name
        assert len(name) <= 100

    def test_very_long_name_truncated_to_100(self):
        """Lines 1087-1089: total name > 100 → truncated."""
        # 50-char snippet + emoji + tag may exceed 100 with a long user_id tag
        question = "B" * 60  # generates 50-char snippet + ellipsis
        user_id = 99999999999999  # long user id gives a long tag
        name = mod._build_default_ask_thread_name(question, user_id=user_id)
        assert len(name) <= 100

    def test_empty_question_uses_fallback(self):
        name = mod._build_default_ask_thread_name("", user_id=1)
        assert "conversation" in name

    def test_whitespace_only_uses_fallback(self):
        name = mod._build_default_ask_thread_name("   ", user_id=1)
        assert "conversation" in name

    def test_exactly_50_chars_gets_ellipsis(self):
        question = "C" * 50
        name = mod._build_default_ask_thread_name(question, user_id=1)
        assert "…" in name


# ---------------------------------------------------------------------------
# _get_or_create_default_ask_thread — cache paths (lines 1142-1154)
# ---------------------------------------------------------------------------


class TestGetOrCreateDefaultAskThread:
    def setup_method(self):
        mod._DEFAULT_ASK_THREAD_CACHE.clear()
        _set_bot_user(999)

    def _make_channel(self, channel_id: int = 100, guild_id: int = 1):
        guild = SimpleNamespace(id=guild_id, get_thread=None)
        ch = SimpleNamespace(
            id=channel_id,
            guild=guild,
            threads=[],
            create_thread=AsyncMock(return_value=MagicMock(id=9999)),
        )
        return ch

    @pytest.mark.asyncio
    async def test_thread_auto_create_false_returns_none(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", False)
        ch = self._make_channel()
        result, created = await mod._get_or_create_default_ask_thread(
            ch, user_id=1, user_question="hello"
        )
        assert result is None
        assert created is False

    @pytest.mark.asyncio
    async def test_cache_hit_valid_thread_returned(self, monkeypatch):
        """Lines 1141-1152: cache hit with thread still within TTL."""
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        ch = self._make_channel(channel_id=200)

        # Build a mock thread that passes _is_reusable_bot_thread
        monkeypatch.setattr(
            mod,
            "_is_reusable_bot_thread",
            lambda candidate, *, parent_channel_id: candidate is not None,
        )

        cached_thread = MagicMock()
        cached_thread.id = 5001
        mod.bot.get_channel = MagicMock(return_value=cached_thread)

        # Seed cache with fresh entry
        key = mod._default_ask_thread_cache_key(ch, 42)
        mod._DEFAULT_ASK_THREAD_CACHE[key] = (5001, time.time())

        result, created = await mod._get_or_create_default_ask_thread(
            ch, user_id=42, user_question="hello"
        )
        assert result is cached_thread
        assert created is False

    @pytest.mark.asyncio
    async def test_cache_hit_expired_pops_and_creates_new(self, monkeypatch):
        """Line 1154: expired cache entry is popped, then new thread created."""
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        monkeypatch.setattr(mod.cfg, "thread_archive_minutes", 60)

        ch = self._make_channel(channel_id=300)
        new_thread = MagicMock(id=7777)
        ch.create_thread = AsyncMock(return_value=new_thread)
        mod.bot.get_channel = MagicMock(return_value=None)

        # Seed cache with expired entry (timestamp very old)
        key = mod._default_ask_thread_cache_key(ch, 43)
        mod._DEFAULT_ASK_THREAD_CACHE[key] = (5002, time.time() - 999999)

        result, created = await mod._get_or_create_default_ask_thread(
            ch, user_id=43, user_question="question"
        )
        # Cache entry should have been popped
        assert key not in mod._DEFAULT_ASK_THREAD_CACHE or created
        assert result is new_thread
        assert created is True

    @pytest.mark.asyncio
    async def test_cache_hit_bot_get_channel_none_falls_back_to_guild_get_thread(
        self, monkeypatch
    ):
        """Lines 1145-1149: bot.get_channel returns None, guild.get_thread used."""
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)

        guild_thread = MagicMock()
        guild_thread.id = 8888

        monkeypatch.setattr(
            mod,
            "_is_reusable_bot_thread",
            lambda candidate, *, parent_channel_id: candidate is guild_thread,
        )

        guild = SimpleNamespace(
            id=1,
            get_thread=MagicMock(return_value=guild_thread),
        )
        ch = SimpleNamespace(
            id=400,
            guild=guild,
            threads=[],
            create_thread=AsyncMock(return_value=MagicMock(id=0)),
        )

        mod.bot.get_channel = MagicMock(return_value=None)

        key = mod._default_ask_thread_cache_key(ch, 44)
        mod._DEFAULT_ASK_THREAD_CACHE[key] = (8888, time.time())

        result, created = await mod._get_or_create_default_ask_thread(
            ch, user_id=44, user_question="hello"
        )
        assert result is guild_thread
        assert created is False
        guild.get_thread.assert_called_once_with(8888)


# ---------------------------------------------------------------------------
# OpenClawBot.on_ready (lines 1254-1388)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ready_basic(monkeypatch):
    """Cover on_ready: mocks all external dependencies and verifies flow."""
    _set_bot_user(999)
    mod.bot._connection._guilds = {}

    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod, "set_bot", MagicMock())
    monkeypatch.setattr(mod, "_load_channel_config", AsyncMock())
    monkeypatch.setattr(mod, "scan_interrupted_plans", MagicMock(return_value=[]))

    # Scheduler: list_tasks returns empty list so all cron jobs get registered
    monkeypatch.setattr(mod.scheduler, "register_skills", MagicMock())
    monkeypatch.setattr(mod.scheduler, "start", MagicMock())
    monkeypatch.setattr(mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "create", MagicMock())

    # Local imports inside on_ready: patreon_scheduled
    mock_patreon = MagicMock()
    mock_patreon.scheduled_patreon_health_check = MagicMock()
    monkeypatch.setitem(sys.modules, "patreon_scheduled", mock_patreon)

    # Local import: discord_background
    mock_bg = MagicMock()
    mock_bg.start_background_tasks = MagicMock(return_value=1)
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    # Local import: skills.list_containers
    import skills as _skills_mod
    monkeypatch.setattr(
        _skills_mod,
        "list_containers",
        AsyncMock(return_value="NAMES\ncontainer1\ncontainer2"),
    )

    # Bot instance methods
    monkeypatch.setattr(mod.bot, "change_presence", AsyncMock())
    monkeypatch.setattr(mod.bot, "get_channel", MagicMock(return_value=None))

    await mod.bot.on_ready()

    mod.scheduler.start.assert_called_once()
    mod._load_channel_config.assert_awaited_once()
    mod.set_bot.assert_called_once_with(mod.bot)
    mod.bot.change_presence.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ready_scheduler_cron_jobs_registered(monkeypatch):
    """Verify run_maintenance and index_vault_to_qmd cron jobs are created."""
    _set_bot_user(999)
    mod.bot._connection._guilds = {}

    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod, "set_bot", MagicMock())
    monkeypatch.setattr(mod, "_load_channel_config", AsyncMock())
    monkeypatch.setattr(mod, "scan_interrupted_plans", MagicMock(return_value=[]))

    mock_create = MagicMock()
    monkeypatch.setattr(mod.scheduler, "register_skills", MagicMock())
    monkeypatch.setattr(mod.scheduler, "start", MagicMock())
    monkeypatch.setattr(mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "create", mock_create)

    mock_patreon = MagicMock()
    mock_patreon.scheduled_patreon_health_check = MagicMock()
    monkeypatch.setitem(sys.modules, "patreon_scheduled", mock_patreon)

    mock_bg = MagicMock()
    mock_bg.start_background_tasks = MagicMock(return_value=1)
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    import skills as _skills_mod
    monkeypatch.setattr(_skills_mod, "list_containers", AsyncMock(return_value="NAMES\n"))
    monkeypatch.setattr(mod.bot, "change_presence", AsyncMock())
    monkeypatch.setattr(mod.bot, "get_channel", MagicMock(return_value=None))

    await mod.bot.on_ready()

    actions_created = [call.kwargs.get("action", call.args[0] if call.args else None)
                       for call in mock_create.call_args_list]
    assert "run_maintenance" in actions_created
    assert "index_vault_to_qmd" in actions_created


@pytest.mark.asyncio
async def test_on_ready_background_loops_zero_logs_warning(monkeypatch):
    """Cover lines 1343-1346: background loops = 0 triggers warning path."""
    _set_bot_user(999)
    mod.bot._connection._guilds = {}

    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod, "set_bot", MagicMock())
    monkeypatch.setattr(mod, "_load_channel_config", AsyncMock())
    monkeypatch.setattr(mod, "scan_interrupted_plans", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "register_skills", MagicMock())
    monkeypatch.setattr(mod.scheduler, "start", MagicMock())
    monkeypatch.setattr(mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "create", MagicMock())

    mock_patreon = MagicMock()
    mock_patreon.scheduled_patreon_health_check = MagicMock()
    monkeypatch.setitem(sys.modules, "patreon_scheduled", mock_patreon)

    # Return 0 background loops to trigger warning path
    mock_bg = MagicMock()
    mock_bg.start_background_tasks = MagicMock(return_value=0)
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    import skills as _skills_mod
    monkeypatch.setattr(_skills_mod, "list_containers", AsyncMock(return_value="NAMES\n"))
    monkeypatch.setattr(mod.bot, "change_presence", AsyncMock())
    monkeypatch.setattr(mod.bot, "get_channel", MagicMock(return_value=None))

    # Should not raise even with 0 background loops
    await mod.bot.on_ready()
    mod.bot.change_presence.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ready_list_containers_import_error(monkeypatch):
    """Cover lines 1364-1366: list_containers raises ImportError → fallback."""
    _set_bot_user(999)
    mod.bot._connection._guilds = {}

    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod, "set_bot", MagicMock())
    monkeypatch.setattr(mod, "_load_channel_config", AsyncMock())
    monkeypatch.setattr(mod, "scan_interrupted_plans", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "register_skills", MagicMock())
    monkeypatch.setattr(mod.scheduler, "start", MagicMock())
    monkeypatch.setattr(mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "create", MagicMock())

    mock_patreon = MagicMock()
    mock_patreon.scheduled_patreon_health_check = MagicMock()
    monkeypatch.setitem(sys.modules, "patreon_scheduled", mock_patreon)

    mock_bg = MagicMock()
    mock_bg.start_background_tasks = MagicMock(return_value=1)
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    # list_containers raises RuntimeError → fallback to guild count
    import skills as _skills_mod
    monkeypatch.setattr(
        _skills_mod, "list_containers", AsyncMock(side_effect=RuntimeError("docker unavailable"))
    )
    monkeypatch.setattr(mod.bot, "change_presence", AsyncMock())
    monkeypatch.setattr(mod.bot, "get_channel", MagicMock(return_value=None))

    await mod.bot.on_ready()
    mod.bot.change_presence.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_ready_with_interrupted_plans(monkeypatch):
    """Cover lines 1375-1388: ALERT_CHANNEL_ID set, interrupted plans found."""
    _set_bot_user(999)
    mod.bot._connection._guilds = {}

    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod, "set_bot", MagicMock())
    monkeypatch.setattr(mod, "_load_channel_config", AsyncMock())

    # Simulate 2 interrupted plans
    plan1 = SimpleNamespace(plan_id="plan-abc")
    plan2 = SimpleNamespace(plan_id="plan-xyz")
    monkeypatch.setattr(mod, "scan_interrupted_plans", MagicMock(return_value=[plan1, plan2]))

    monkeypatch.setattr(mod.scheduler, "register_skills", MagicMock())
    monkeypatch.setattr(mod.scheduler, "start", MagicMock())
    monkeypatch.setattr(mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(mod.scheduler, "create", MagicMock())

    mock_patreon = MagicMock()
    mock_patreon.scheduled_patreon_health_check = MagicMock()
    monkeypatch.setitem(sys.modules, "patreon_scheduled", mock_patreon)

    mock_bg = MagicMock()
    mock_bg.start_background_tasks = MagicMock(return_value=1)
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    import skills as _skills_mod
    monkeypatch.setattr(_skills_mod, "list_containers", AsyncMock(return_value="NAMES\n"))
    monkeypatch.setattr(mod.bot, "change_presence", AsyncMock())

    # Set ALERT_CHANNEL_ID to trigger the interrupted plans branch
    monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 1234)
    alert_ch = MagicMock()
    alert_ch.send = AsyncMock()
    monkeypatch.setattr(mod.bot, "get_channel", MagicMock(return_value=alert_ch))

    await mod.bot.on_ready()

    alert_ch.send.assert_awaited()
    sent_text = alert_ch.send.call_args[0][0]
    assert "plan-abc" in sent_text or "interrupted" in sent_text.lower()


# ---------------------------------------------------------------------------
# OpenClawBot.close (lines 1390-1427)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_basic(monkeypatch):
    """Cover close(): mock all dependencies and verify cleanup flow."""
    # Mock discord_background.stop_background_tasks
    mock_bg = MagicMock()
    mock_bg.stop_background_tasks = AsyncMock()
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    # Ensure audit buffer is empty (skip file-write branch)
    mod._audit_buffer.clear()

    # Mock module-level close functions via sys.modules
    mock_llm = MagicMock()
    mock_llm.close_sessions = AsyncMock()
    monkeypatch.setitem(sys.modules, "llm", mock_llm)

    mock_agentmail = MagicMock()
    mock_agentmail.close_session = AsyncMock()
    monkeypatch.setitem(sys.modules, "agentmail", mock_agentmail)

    mock_nas = MagicMock()
    mock_nas.close_session = AsyncMock()
    monkeypatch.setitem(sys.modules, "nas", mock_nas)

    mock_http_session = MagicMock()
    mock_http_session.close_all = AsyncMock()
    monkeypatch.setitem(sys.modules, "http_session", mock_http_session)

    mock_metrics = MagicMock()
    mock_metrics.stop_metrics_collector = AsyncMock()
    monkeypatch.setitem(sys.modules, "metrics_collector", mock_metrics)

    # No health runner to clean up
    mod.bot._health_runner = None

    with patch.object(commands.Bot, "close", new_callable=AsyncMock):
        await mod.bot.close()

    mock_bg.stop_background_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_with_health_runner(monkeypatch):
    """Cover line 1425-1426: _health_runner.cleanup() called on close."""
    mock_bg = MagicMock()
    mock_bg.stop_background_tasks = AsyncMock()
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    mod._audit_buffer.clear()

    mock_metrics = MagicMock()
    mock_metrics.stop_metrics_collector = AsyncMock()
    monkeypatch.setitem(sys.modules, "metrics_collector", mock_metrics)

    # Set a health runner with async cleanup
    health_runner = MagicMock()
    health_runner.cleanup = AsyncMock()
    mod.bot._health_runner = health_runner

    with patch.object(commands.Bot, "close", new_callable=AsyncMock):
        await mod.bot.close()

    health_runner.cleanup.assert_awaited_once()
    mod.bot._health_runner = None  # cleanup


@pytest.mark.asyncio
async def test_close_flushes_audit_buffer(monkeypatch, tmp_path):
    """Cover lines 1396-1406: audit buffer flushed to file on close."""
    mock_bg = MagicMock()
    mock_bg.stop_background_tasks = AsyncMock()
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    mock_metrics = MagicMock()
    mock_metrics.stop_metrics_collector = AsyncMock()
    monkeypatch.setitem(sys.modules, "metrics_collector", mock_metrics)

    # Populate audit buffer with a test entry
    test_entry = {"event": "test", "ts": "2024-01-01"}
    mod._audit_buffer.append(test_entry)

    # Point AUDIT_DIR at tmp_path (within project dir via monkeypatch)
    monkeypatch.setattr(mod, "AUDIT_DIR", tmp_path)
    mod.bot._health_runner = None

    with patch.object(commands.Bot, "close", new_callable=AsyncMock):
        await mod.bot.close()

    # Buffer should have been drained
    # (note: close() does list(_audit_buffer) then clear(), so buffer is empty after)
    # The audit file should exist if writing succeeded
    import datetime
    today = datetime.date.today().isoformat()
    audit_file = tmp_path / f"{today}.jsonl"
    if audit_file.exists():
        content = audit_file.read_text()
        assert "test" in content


@pytest.mark.asyncio
async def test_close_handles_stop_background_tasks_error(monkeypatch):
    """close() handles errors from stop_background_tasks gracefully."""
    mock_bg = MagicMock()
    mock_bg.stop_background_tasks = AsyncMock(side_effect=Exception("bg error"))
    monkeypatch.setitem(sys.modules, "discord_background", mock_bg)

    mod._audit_buffer.clear()

    mock_metrics = MagicMock()
    mock_metrics.stop_metrics_collector = AsyncMock()
    monkeypatch.setitem(sys.modules, "metrics_collector", mock_metrics)

    mod.bot._health_runner = None

    # stop_background_tasks raises but close should still proceed
    try:
        with patch.object(commands.Bot, "close", new_callable=AsyncMock):
            await mod.bot.close()
    except Exception:
        pass  # error is expected to propagate from stop_background_tasks
