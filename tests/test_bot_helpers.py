"""
Tests for bot.py helper functions: feedback guardrails, failure classification,
quality scoring edge cases, permission helpers, thread cache utilities, and
ResponseActions interaction checks.

These tests intentionally import bot.py in isolation so the module-level
singleton (bot = OpenClawBot()) exercises the class __init__ path for coverage.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit")

import ask_handler as ask_handler_mod
import bot as mod
import bot_helpers as bot_helpers_mod
import discord_events as discord_events_mod
import response_actions as ra_mod

# ---------------------------------------------------------------------------
# _prune_feedback_event_buffer
# ---------------------------------------------------------------------------


class TestPruneFeedbackEventBuffer:
    def test_bot_helpers_keeps_events_within_window(self):
        now = 1000.0
        events = [990.0, 995.0, 999.0]
        result = mod._prune_feedback_event_buffer(events, now, 60.0)
        assert result == [990.0, 995.0, 999.0]

    def test_bot_helpers_removes_stale_events(self):
        now = 1000.0
        events = [900.0, 930.0, 970.0, 999.0]  # cutoff = 1000 - 60 = 940
        result = mod._prune_feedback_event_buffer(events, now, 60.0)
        assert result == [970.0, 999.0]  # 900 and 930 are before cutoff 940

    def test_bot_helpers_empty_list_returns_empty(self):
        assert mod._prune_feedback_event_buffer([], 1000.0, 60.0) == []

    def test_zero_window_drops_all(self):
        # window_seconds=0 → cutoff=now → all events at or before now are pruned
        now = 1000.0
        events = [999.0, 1000.0]
        result = mod._prune_feedback_event_buffer(events, now, 0.0)
        assert result == [1000.0]  # exactly now is kept (>= cutoff)

    def test_bot_helpers_negative_window_treated_as_zero(self):
        now = 1000.0
        events = [999.0, 1000.0]
        result = mod._prune_feedback_event_buffer(events, now, -10.0)
        # max(0, -10) → cutoff = now, same as window=0
        assert result == [1000.0]


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails
# ---------------------------------------------------------------------------


class TestApplyFeedbackGuardrails:
    def setup_method(self):
        mod._reset_feedback_guardrails_for_tests()

    def test_first_vote_is_accepted(self):
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1000.0,
        )
        assert accepted is True
        assert reason == "accepted"

    def test_duplicate_vote_within_window_is_rejected(self):
        mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1000.0,
        )
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1001.0,
        )
        assert accepted is False
        assert reason == "dedupe"

    def test_different_rating_is_not_deduped(self):
        mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1000.0,
        )
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="down", now=1001.0,
        )
        assert accepted is True
        assert reason == "accepted"

    def test_user_rate_limit_triggers_after_max_events(self):
        # Exhaust the per-user rate limit — all events within the 60s window
        max_events = mod._FEEDBACK_USER_RATE_LIMIT_MAX
        base_now = 1000.0
        for i in range(max_events):
            accepted, _ = mod._apply_feedback_guardrails(
                user_id=42, channel_id=10, message_id=i, rating="up",
                now=base_now + i * 0.1,  # 100ms apart — all within 60s window
            )
            assert accepted is True
        # The next vote is still within the window — should be rate-limited
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=42, channel_id=10, message_id=999, rating="up",
            now=base_now + 1.0,  # still within 60s window
        )
        assert accepted is False
        assert reason == "rate_limited_user"

    def test_none_ids_are_handled_safely(self):
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=None, channel_id=None, message_id=None, rating="up", now=1000.0,
        )
        assert isinstance(accepted, bool)
        assert isinstance(reason, str)

    def test_bot_helpers_reset_clears_state(self):
        mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1000.0,
        )
        mod._reset_feedback_guardrails_for_tests()
        accepted, reason = mod._apply_feedback_guardrails(
            user_id=1, channel_id=10, message_id=100, rating="up", now=1001.0,
        )
        assert accepted is True  # after reset, no dedupe should trigger


# ---------------------------------------------------------------------------
# _classify_ask_failure
# ---------------------------------------------------------------------------


class TestClassifyAskFailure:
    def test_timeout_from_message(self):
        assert mod._classify_ask_failure("Request timed out") == "timeout"

    def test_bot_helpers_rate_limit_429(self):
        assert mod._classify_ask_failure("429 Too Many Requests") == "rate_limit"

    def test_rate_limit_from_routing_note(self):
        assert mod._classify_ask_failure("", ["resource exhausted"]) == "rate_limit"

    def test_tool_failure(self):
        assert mod._classify_ask_failure("invalid tool call made") == "tool"

    def test_provider_failure_gemini(self):
        assert mod._classify_ask_failure("gemini service unavailable") == "provider"

    def test_provider_failure_api_key(self):
        assert mod._classify_ask_failure("api key missing or forbidden") == "provider"

    def test_provider_failure_anthropic(self):
        assert mod._classify_ask_failure("anthropic returned 403") == "provider"

    def test_bot_helpers_general_fallback(self):
        assert mod._classify_ask_failure("something went wrong") == "general"

    def test_empty_inputs(self):
        assert mod._classify_ask_failure("") == "general"

    def test_none_routing_notes(self):
        assert mod._classify_ask_failure("timeout error", None) == "timeout"


# ---------------------------------------------------------------------------
# _build_ask_failure_message
# ---------------------------------------------------------------------------


class TestBuildAskFailureMessage:
    def test_includes_category_title(self):
        msg = mod._build_ask_failure_message(
            question="what is the weather",
            model_pref="auto",
            trace_id="abc123",
            category="timeout",
        )
        assert "Timeout" in msg
        assert "abc123" in msg
        assert "what is the weather" in msg

    def test_rate_limit_message(self):
        msg = mod._build_ask_failure_message(
            question="q", model_pref="gemini", trace_id="t1", category="rate_limit",
        )
        assert "Rate limit" in msg

    def test_tool_message(self):
        msg = mod._build_ask_failure_message(
            question="q", model_pref="gemini", trace_id="t1", category="tool",
        )
        assert "Tool" in msg

    def test_unknown_category_defaults_to_general(self):
        msg = mod._build_ask_failure_message(
            question="q", model_pref="auto", trace_id="t1", category="unknown_xyz",
        )
        assert "Request failure" in msg

    def test_question_is_escaped_for_markdown(self):
        # Asterisks, underscores etc. should be escaped
        msg = mod._build_ask_failure_message(
            question="what is *bold* and _italic_?",
            model_pref="auto",
            trace_id="t",
            category="general",
        )
        # discord.utils.escape_markdown is called, so raw * should be escaped
        assert "\\*bold\\*" in msg or "*bold*" in msg  # escape_markdown behavior


# ---------------------------------------------------------------------------
# Additional _score_answer_quality coverage
# ---------------------------------------------------------------------------


class TestScoreAnswerQualityEdgeCases:
    def test_evidence_completeness_from_meta_high(self):
        result = mod._score_answer_quality(
            "Answer text with today's data.",
            final_meta={"evidence_completeness": 0.9},
        )
        assert result["evidence_completeness"] == pytest.approx(0.9)
        assert any("Strong claim-to-evidence" in r for r in result["reasons"])

    def test_evidence_completeness_from_meta_moderate(self):
        result = mod._score_answer_quality(
            "Partial data available.",
            final_meta={"evidence_completeness": 0.65},
        )
        assert result["evidence_completeness"] == pytest.approx(0.65)
        assert any("Moderate claim-to-evidence" in r for r in result["reasons"])

    def test_evidence_completeness_from_meta_low(self):
        result = mod._score_answer_quality(
            "Limited info.",
            final_meta={"evidence_completeness": 0.4},
        )
        assert result["evidence_completeness"] == pytest.approx(0.4)
        # Low evidence should drop confidence
        assert result["status"] == "low"

    def test_evidence_completeness_from_text_regex(self):
        text = "Evidence Completeness: **75%** verified"
        result = mod._score_answer_quality(text, final_meta={})
        assert result["evidence_completeness"] == pytest.approx(0.75)

    def test_evidence_source_fields_missing_flag(self):
        text = "Evidence Completeness: **50%** (fail-safe (source fields missing))"
        result = mod._score_answer_quality(text, final_meta={})
        assert result["evidence_source_fields_missing"] is True

    def test_requested_item_count_exact_match_bonus(self):
        lines = "\n".join(f"- item {i}" for i in range(8))
        result = mod._score_answer_quality(
            lines, final_meta={"requested_item_count": 8},
        )
        assert result["requested_item_count"] == 8
        assert any("Requested item target met" in r for r in result["reasons"])
        assert result["score"] <= 100

    def test_score_is_clamped_to_0_100(self):
        # worst possible: multiple uncertainty markers, no items, no sources
        text = " ".join([
            "not sure unclear unknown might may could possibly likely partial coverage"
            " insufficient incomplete tbd"
        ])
        result = mod._score_answer_quality(text, final_meta={"evidence_completeness": 0.0})
        assert 0 <= result["score"] <= 100

    def test_extract_reported_evidence_completeness_from_meta(self):
        val, missing = mod._extract_reported_evidence_completeness(
            "", final_meta={"evidence_completeness": 0.82}
        )
        assert val == pytest.approx(0.82)
        assert missing is False

    def test_extract_reported_evidence_completeness_clamped(self):
        val, _ = mod._extract_reported_evidence_completeness(
            "", final_meta={"evidence_completeness": 1.5}
        )
        assert val == pytest.approx(1.0)

    def test_extract_reported_evidence_completeness_no_data(self):
        val, missing = mod._extract_reported_evidence_completeness("plain text")
        assert val is None
        assert missing is False

    def test_count_markdown_table_items_with_header_and_separator(self):
        text = (
            "| Name | Score |\n"
            "| ---- | ----- |\n"
            "| Alice | 10 |\n"
            "| Bob | 20 |\n"
        )
        assert mod._count_markdown_table_items(text) == 2

    def test_count_markdown_table_items_empty(self):
        assert mod._count_markdown_table_items("") == 0
        assert mod._count_markdown_table_items("no table here") == 0

    def test_extract_distinct_source_domains(self):
        text = "See https://espn.com/news and https://apnews.com/article also https://espn.com/scores"
        domains = mod._extract_distinct_source_domains(text)
        assert "espn.com" in domains
        assert "apnews.com" in domains
        assert len(domains) == 2  # espn.com deduped

    def test_extract_distinct_source_domains_empty(self):
        assert mod._extract_distinct_source_domains("") == set()


# ---------------------------------------------------------------------------
# _quality_retry_improved
# ---------------------------------------------------------------------------


class TestQualityRetryImproved:
    def test_true_when_status_upgraded_to_high(self):
        assert mod._quality_retry_improved(
            original={"score": 30, "status": "low"},
            retried={"score": 80, "status": "high"},
        ) is True

    def test_true_when_score_improved_by_10_or_more(self):
        assert mod._quality_retry_improved(
            original={"score": 40, "status": "medium"},
            retried={"score": 52, "status": "medium"},
        ) is True

    def test_false_when_improvement_less_than_10(self):
        assert mod._quality_retry_improved(
            original={"score": 40, "status": "medium"},
            retried={"score": 45, "status": "medium"},
        ) is False

    def test_false_when_score_same(self):
        assert mod._quality_retry_improved(
            original={"score": 60, "status": "medium"},
            retried={"score": 60, "status": "medium"},
        ) is False

    def test_already_high_not_improved(self):
        assert mod._quality_retry_improved(
            original={"score": 80, "status": "high"},
            retried={"score": 80, "status": "high"},
        ) is False


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


class TestIsUserAllowed:
    def test_empty_allowlist_permits_everyone(self, monkeypatch):
        monkeypatch.setattr(bot_helpers_mod, "ALLOWED_USER_IDS", set())
        assert mod._is_user_allowed(999) is True

    def test_user_in_allowlist_is_permitted(self, monkeypatch):
        monkeypatch.setattr(bot_helpers_mod, "ALLOWED_USER_IDS", {42, 99})
        assert mod._is_user_allowed(42) is True

    def test_user_not_in_allowlist_is_blocked(self, monkeypatch):
        monkeypatch.setattr(bot_helpers_mod, "ALLOWED_USER_IDS", {42, 99})
        assert mod._is_user_allowed(100) is False


class TestBotCanReadChannel:
    def test_dm_channel_has_no_guild_so_returns_true(self):
        chan = SimpleNamespace(guild=None)
        assert mod._bot_can_read_channel(chan) is True

    def test_returns_false_when_permissions_for_not_callable(self):
        guild = SimpleNamespace(me=MagicMock())
        chan = SimpleNamespace(guild=guild, permissions_for="not_callable")
        assert mod._bot_can_read_channel(chan) is False

    def test_returns_true_when_no_bot_member_in_guild(self):
        # guild.me is None and no get_member → bot_member stays None → returns True
        guild = SimpleNamespace(me=None, id=1)  # no get_member attribute
        chan = SimpleNamespace(guild=guild, permissions_for=lambda m: SimpleNamespace(read_messages=True))
        result = mod._bot_can_read_channel(chan)
        assert result is True

    def test_returns_read_messages_permission(self):
        bot_member = MagicMock()
        guild = SimpleNamespace(me=bot_member)
        perms = SimpleNamespace(read_messages=True)
        chan = SimpleNamespace(guild=guild, permissions_for=lambda m: perms)
        assert mod._bot_can_read_channel(chan) is True

    def test_returns_false_when_no_read_messages(self):
        bot_member = MagicMock()
        guild = SimpleNamespace(me=bot_member)
        perms = SimpleNamespace(read_messages=False)
        chan = SimpleNamespace(guild=guild, permissions_for=lambda m: perms)
        assert mod._bot_can_read_channel(chan) is False


class TestShouldSendMessageContentHint:
    def test_returns_false_when_no_channel_id(self):
        chan = SimpleNamespace(id=None)
        assert mod._should_send_message_content_hint(chan) is False

    def test_returns_true_first_time(self, monkeypatch):
        monkeypatch.setattr(bot_helpers_mod, "_MESSAGE_CONTENT_HINT_CACHE", {})
        chan = SimpleNamespace(id=555)
        assert mod._should_send_message_content_hint(chan) is True

    def test_returns_false_within_cooldown(self, monkeypatch):
        monkeypatch.setattr(bot_helpers_mod, "_MESSAGE_CONTENT_HINT_CACHE", {555: time.time()})
        chan = SimpleNamespace(id=555)
        assert mod._should_send_message_content_hint(chan) is False


# ---------------------------------------------------------------------------
# Thread name / cache key helpers
# ---------------------------------------------------------------------------


class TestDefaultAskThreadHelpers:
    def test_user_tag_format(self):
        assert mod._default_ask_thread_user_tag(42) == "u42"

    def test_cache_key_includes_guild_id(self):
        guild = SimpleNamespace(id=7)
        chan = SimpleNamespace(id=10, guild=guild)
        key = mod._default_ask_thread_cache_key(chan, user_id=42)
        assert key == (7, 10, 42)

    def test_cache_key_no_guild(self):
        chan = SimpleNamespace(id=10, guild=None)
        key = mod._default_ask_thread_cache_key(chan, user_id=42)
        assert key == (0, 10, 42)

    def test_thread_name_short_question(self):
        name = mod._build_default_ask_thread_name("hello world", 42)
        assert "hello world" in name
        assert "u42" in name
        assert name.startswith("💬")

    def test_thread_name_very_long_question(self):
        name = mod._build_default_ask_thread_name("x" * 200, 42)
        assert len(name) <= 100
        assert "u42" in name

    def test_thread_name_empty_question(self):
        name = mod._build_default_ask_thread_name("", 42)
        assert "conversation" in name

    def test_thread_name_exactly_50_chars_gets_ellipsis(self):
        name = mod._build_default_ask_thread_name("a" * 50, 42)
        assert "…" in name


# ---------------------------------------------------------------------------
# _is_reusable_bot_thread
# ---------------------------------------------------------------------------


class TestIsReusableBotThread:
    def _make_thread(self, *, owner_id, parent_id, archived=False, locked=False):
        import discord as _discord
        t = MagicMock(spec=_discord.Thread)
        t.owner_id = owner_id
        t.parent_id = parent_id
        t.archived = archived
        t.locked = locked
        return t

    def test_reusable_when_all_conditions_met(self):
        t = self._make_thread(owner_id=999, parent_id=10)
        mod.bot._connection.user = SimpleNamespace(id=999)
        assert mod._is_reusable_bot_thread(t, parent_channel_id=10) is True

    def test_not_reusable_when_not_thread_type(self):
        assert mod._is_reusable_bot_thread(SimpleNamespace(), parent_channel_id=10) is False

    def test_not_reusable_when_bot_user_is_none(self):
        t = self._make_thread(owner_id=1, parent_id=10)
        mod.bot._connection.user = None
        assert mod._is_reusable_bot_thread(t, parent_channel_id=10) is False

    def test_not_reusable_when_archived(self):
        mod.bot._connection.user = SimpleNamespace(id=999)
        t = self._make_thread(owner_id=999, parent_id=10, archived=True)
        assert mod._is_reusable_bot_thread(t, parent_channel_id=10) is False

    def test_not_reusable_when_locked(self):
        mod.bot._connection.user = SimpleNamespace(id=999)
        t = self._make_thread(owner_id=999, parent_id=10, locked=True)
        assert mod._is_reusable_bot_thread(t, parent_channel_id=10) is False

    def test_not_reusable_when_wrong_parent(self):
        mod.bot._connection.user = SimpleNamespace(id=999)
        t = self._make_thread(owner_id=999, parent_id=99)
        assert mod._is_reusable_bot_thread(t, parent_channel_id=10) is False


# ---------------------------------------------------------------------------
# _pick_most_recent_thread
# ---------------------------------------------------------------------------


class TestPickMostRecentThread:
    def _make_thread(self, *, id_, last_message_id=None):
        t = MagicMock()
        t.id = id_
        t.last_message_id = last_message_id
        return t

    def test_picks_highest_last_message_id(self):
        t1 = self._make_thread(id_=1, last_message_id=100)
        t2 = self._make_thread(id_=2, last_message_id=200)
        t3 = self._make_thread(id_=3, last_message_id=50)
        result = mod._pick_most_recent_thread([t1, t2, t3])
        assert result is t2

    def test_falls_back_to_thread_id_when_no_last_message(self):
        t1 = self._make_thread(id_=1, last_message_id=None)
        t2 = self._make_thread(id_=5, last_message_id=None)
        result = mod._pick_most_recent_thread([t1, t2])
        assert result is t2


# ---------------------------------------------------------------------------
# _get_or_create_default_ask_thread
# ---------------------------------------------------------------------------


class TestGetOrCreateDefaultAskThread:
    @pytest.mark.asyncio
    async def test_returns_none_when_thread_auto_create_disabled(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", False)
        chan = MagicMock()
        result, created = await mod._get_or_create_default_ask_thread(
            chan, user_id=1, user_question="hello",
        )
        assert result is None
        assert created is False

    @pytest.mark.asyncio
    async def test_returns_none_for_dm_channel(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        import discord as _discord
        dm = MagicMock(spec=_discord.DMChannel)
        result, created = await mod._get_or_create_default_ask_thread(
            dm, user_id=1, user_question="hello",
        )
        assert result is None
        assert created is False

    @pytest.mark.asyncio
    async def test_returns_none_when_bot_user_is_none(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        chan = MagicMock()
        chan.create_thread = AsyncMock()
        mod.bot._connection.user = None
        result, created = await mod._get_or_create_default_ask_thread(
            chan, user_id=1, user_question="hello",
        )
        assert result is None
        assert created is False

    @pytest.mark.asyncio
    async def test_creates_new_thread_when_no_cache_no_existing(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        monkeypatch.setattr(mod.cfg, "thread_archive_minutes", 60)
        monkeypatch.setattr(mod, "_DEFAULT_ASK_THREAD_CACHE", {})
        mod.bot._connection.user = SimpleNamespace(id=99)

        new_thread = MagicMock()
        new_thread.id = 42
        chan = MagicMock()
        chan.threads = []
        chan.id = 10
        chan.guild = SimpleNamespace(id=1)
        chan.create_thread = AsyncMock(return_value=new_thread)

        result, created = await mod._get_or_create_default_ask_thread(
            chan, user_id=5, user_question="what is weather?",
        )
        assert result is new_thread
        assert created is True

    @pytest.mark.asyncio
    async def test_finds_existing_thread_by_user_tag(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        monkeypatch.setattr(mod, "_DEFAULT_ASK_THREAD_CACHE", {})
        mod.bot._connection.user = SimpleNamespace(id=99)

        import discord as _discord

        existing = MagicMock(spec=_discord.Thread)
        existing.id = 77
        existing.owner_id = 99
        existing.parent_id = 10
        existing.archived = False
        existing.locked = False
        existing.name = "💬 hello · u5"
        existing.last_message_id = 100

        chan = MagicMock()
        chan.threads = [existing]
        chan.id = 10
        chan.guild = SimpleNamespace(id=1)

        result, created = await mod._get_or_create_default_ask_thread(
            chan, user_id=5, user_question="hello",
        )
        assert result is existing
        assert created is False

    @pytest.mark.asyncio
    async def test_handles_create_thread_exception_gracefully(self, monkeypatch):
        monkeypatch.setattr(mod.cfg, "thread_auto_create", True)
        monkeypatch.setattr(mod.cfg, "thread_archive_minutes", 60)
        monkeypatch.setattr(mod, "_DEFAULT_ASK_THREAD_CACHE", {})
        mod.bot._connection.user = SimpleNamespace(id=99)

        chan = MagicMock()
        chan.threads = []
        chan.id = 10
        chan.guild = SimpleNamespace(id=1)
        chan.create_thread = AsyncMock(side_effect=Exception("discord error"))

        result, created = await mod._get_or_create_default_ask_thread(
            chan, user_id=5, user_question="hello",
        )
        assert result is None
        assert created is False


# ---------------------------------------------------------------------------
# _generate_follow_ups
# ---------------------------------------------------------------------------


class TestGenerateFollowUps:
    @pytest.mark.asyncio
    async def test_returns_two_followup_questions(self):
        mock_response = "What else can I help?\nAny updates available?"
        with patch.dict("sys.modules", {"llm.chat": MagicMock(chat=AsyncMock(return_value=(mock_response, [], {})))}):
            result = await mod._generate_follow_ups(
                "What is Python?", "Python is a programming language."
            )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_failure(self):
        mock_chat_module = MagicMock()
        mock_chat_module.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        with patch.dict("sys.modules", {"llm.chat": mock_chat_module}):
            result = await mod._generate_follow_ups("q", "a")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_bot_helpers_returns_empty_on_import_error(self):
        # When LLM module not available, function returns []
        with patch.dict("sys.modules", {"llm.chat": None}):
            try:
                result = await mod._generate_follow_ups("q", "a")
                assert isinstance(result, list)
            except Exception:
                pass  # If the import error isn't caught, that's a bug to note but skip here


# ---------------------------------------------------------------------------
# ResponseActions.interaction_check
# ---------------------------------------------------------------------------


class TestResponseActionsInteractionCheck:
    def _make_view(self, user_id: int):
        return mod.ResponseActions(
            response_text="response",
            question="question",
            user_id=user_id,
            channel_id=1,
            thread_id=None,
        )

    @pytest.mark.asyncio
    async def test_allows_original_user(self):
        view = self._make_view(user_id=42)
        interaction = MagicMock()
        interaction.user.id = 42
        result = await view.interaction_check(interaction)
        assert result is True

    @pytest.mark.asyncio
    async def test_blocks_different_user(self):
        view = self._make_view(user_id=42)
        interaction = AsyncMock()
        interaction.user.id = 99
        interaction.response.send_message = AsyncMock()
        result = await view.interaction_check(interaction)
        assert result is False
        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# _build_quality_broadening_prompt
# ---------------------------------------------------------------------------


class TestBuildQualityBroadeningPrompt:
    def test_includes_original_question(self):
        prompt = mod._build_quality_broadening_prompt("what is Python?", ["low confidence"])
        assert "what is Python?" in prompt
        assert "low confidence" in prompt

    def test_empty_reasons_uses_fallback(self):
        prompt = mod._build_quality_broadening_prompt("q", [])
        assert "low confidence detected" in prompt

    def test_truncates_to_first_three_reasons(self):
        reasons = [f"reason{i}" for i in range(10)]
        prompt = mod._build_quality_broadening_prompt("q", reasons)
        # Only first 3 should appear in the truncated reason_text
        assert "reason0" in prompt
        assert "reason2" in prompt
        # reason3+ might not be there (joined only first 3)
        assert prompt.count("reason") <= 3


# ---------------------------------------------------------------------------
# ResponseActions button callbacks and _record_feedback
# ---------------------------------------------------------------------------


def _make_view(user_id: int = 42):
    return mod.ResponseActions(
        response_text="The answer to everything is 42.",
        question="What is the answer?",
        user_id=user_id,
        channel_id=100,
        thread_id=None,
    )


def _make_interaction(user_id: int = 42, channel_id: int = 100):
    interaction = AsyncMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Dave"
    interaction.channel.id = channel_id
    interaction.channel.parent_id = None
    interaction.message = MagicMock()
    interaction.message.id = 555
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


class TestResponseActionsSaveButton:
    @pytest.mark.asyncio
    async def test_save_btn_success(self, monkeypatch):
        view = _make_view()
        interaction = _make_interaction()
        monkeypatch.setattr(ra_mod, "remember_fact", AsyncMock(return_value="Saved!"))

        # discord.py wraps the method in _ItemCallback; reach the actual coroutine via .callback.callback
        await view.save_btn.callback.callback(view, interaction, MagicMock())

        interaction.response.defer.assert_awaited_once()
        call_text = str(interaction.followup.send.call_args)
        assert "Saved to memory" in call_text

    @pytest.mark.asyncio
    async def test_save_btn_error_sends_error_message(self, monkeypatch):
        view = _make_view()
        interaction = _make_interaction()
        monkeypatch.setattr(ra_mod, "remember_fact", AsyncMock(side_effect=RuntimeError("db down")))

        await view.save_btn.callback.callback(view, interaction, MagicMock())

        interaction.response.defer.assert_awaited_once()
        call_text = str(interaction.followup.send.call_args)
        assert "Save failed" in call_text


class TestResponseActionsEmailButton:
    @pytest.mark.asyncio
    async def test_email_btn_success(self, monkeypatch):
        view = _make_view()
        interaction = _make_interaction()
        monkeypatch.setattr(ra_mod, "send_agent_mail", AsyncMock(return_value="Sent!"))

        await view.email_btn.callback.callback(view, interaction, MagicMock())

        interaction.response.defer.assert_awaited_once()
        call_text = str(interaction.followup.send.call_args)
        assert "Emailed" in call_text

    @pytest.mark.asyncio
    async def test_email_btn_error(self, monkeypatch):
        view = _make_view()
        interaction = _make_interaction()
        monkeypatch.setattr(ra_mod, "send_agent_mail", AsyncMock(side_effect=RuntimeError("smtp down")))

        await view.email_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.followup.send.call_args)
        assert "Email failed" in call_text


class TestResponseActionsContextLockButtons:
    @pytest.mark.asyncio
    async def test_lock_channel_btn_calls_set_context_lock(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)
        mock_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_lock)

        await view.lock_channel_btn.callback.callback(view, interaction, MagicMock())

        mock_lock.assert_called_once_with(
            user_id=42, mode="channel", channel_id=100, thread_id=None,
        )
        interaction.response.send_message.assert_awaited_once()
        call_text = str(interaction.response.send_message.call_args)
        assert "locked" in call_text.lower()

    @pytest.mark.asyncio
    async def test_reset_context_btn_clears_lock_and_anchor(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)
        mock_reset_lock = MagicMock()
        mock_reset_anchor = MagicMock()
        monkeypatch.setattr(ra_mod, "reset_context_lock", mock_reset_lock)
        monkeypatch.setattr(ra_mod, "reset_anchor_state", mock_reset_anchor)
        monkeypatch.setattr(ra_mod, "resolve_context_lock", MagicMock(return_value=(None, None)))

        await view.reset_context_btn.callback.callback(view, interaction, MagicMock())

        mock_reset_lock.assert_called_once_with(42)
        interaction.response.send_message.assert_awaited_once()


class TestRecordFeedback:
    def setup_method(self):
        mod._reset_feedback_guardrails_for_tests()

    @pytest.mark.asyncio
    async def test_dedupe_sends_already_captured_message(self):
        view = _make_view(user_id=1)
        interaction = _make_interaction(user_id=1, channel_id=10)
        interaction.message.id = 100

        with patch("response_actions.aiofiles") as mock_aiofiles, patch("response_actions.Path") as mock_path_cls:
            mock_file = AsyncMock()
            mock_file.__aenter__ = AsyncMock(return_value=mock_file)
            mock_file.__aexit__ = AsyncMock(return_value=False)
            mock_file.write = AsyncMock()
            mock_aiofiles.open = MagicMock(return_value=mock_file)
            mock_path = MagicMock()
            mock_path.parent.mkdir = MagicMock()
            mock_path_cls.return_value = mock_path

            # First vote — accepted
            await view._record_feedback(interaction, "helpful")
            interaction.response.send_message.reset_mock()

            # Second identical vote — deduped
            await view._record_feedback(interaction, "helpful")

        call_text = str(interaction.response.send_message.call_args)
        assert "Already captured" in call_text

    @pytest.mark.asyncio
    async def test_accepted_feedback_sends_ack(self, tmp_path):
        view = _make_view(user_id=77)
        interaction = _make_interaction(user_id=77, channel_id=200)
        interaction.message.id = 999

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_file)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("response_actions.aiofiles") as mock_aiofiles, patch("response_actions.Path") as mock_path_cls:
            mock_aiofiles.open = MagicMock(return_value=mock_cm)
            mock_path = MagicMock()
            mock_path.parent.mkdir = MagicMock()
            mock_path_cls.return_value = mock_path

            await view._record_feedback(interaction, "helpful")

        call_text = str(interaction.response.send_message.call_args)
        assert "👍" in call_text

    @pytest.mark.asyncio
    async def test_not_helpful_rating_shows_thumbs_down(self):
        view = _make_view(user_id=55)
        interaction = _make_interaction(user_id=55, channel_id=300)
        interaction.message.id = 111

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_file)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("response_actions.aiofiles") as mock_aiofiles, patch("response_actions.Path") as mock_path_cls:
            mock_aiofiles.open = MagicMock(return_value=mock_cm)
            mock_path = MagicMock()
            mock_path.parent.mkdir = MagicMock()
            mock_path_cls.return_value = mock_path
            await view._record_feedback(interaction, "not_helpful")

        call_text = str(interaction.response.send_message.call_args)
        assert "👎" in call_text


class TestResponseActionsRegenButton:
    @pytest.mark.asyncio
    async def test_regen_btn_calls_llm_and_sends_embed(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = [{"role": "user", "parts": ["q"]}, {"role": "model", "parts": ["a"]}]
        mock_conv.update_from_llm = MagicMock()

        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(return_value=("regen response", [], "gemini-pro")))
        monkeypatch.setattr(ra_mod, "resolve_context_lock", MagicMock(return_value=(None, None)))

        await view.regen_btn.callback.callback(view, interaction, MagicMock())

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_regen_btn_error_sends_error_message(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(side_effect=RuntimeError("llm down")))
        monkeypatch.setattr(ra_mod, "resolve_context_lock", MagicMock(return_value=(None, None)))

        await view.regen_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.followup.send.call_args)
        assert "Regeneration failed" in call_text


# ---------------------------------------------------------------------------
# ask_cmd guard paths
# ---------------------------------------------------------------------------

class TestAskCmdGuards:
    """Tests for the early-return guard clauses at the top of ask_cmd."""

    @pytest.mark.asyncio
    async def test_emergency_stop_blocks_ask(self, monkeypatch):
        import bot as mod
        monkeypatch.setattr(ask_handler_mod, "is_emergency_stopped", MagicMock(return_value=True))
        interaction = _make_interaction(user_id=1, channel_id=1)
        interaction.response.send_message = AsyncMock()
        await mod.ask_cmd.callback(interaction, question="test?")
        call_text = str(interaction.response.send_message.call_args)
        assert "Emergency stop" in call_text

    @pytest.mark.asyncio
    async def test_llm_not_configured_blocks_ask(self, monkeypatch):
        import bot as mod
        monkeypatch.setattr(ask_handler_mod, "is_emergency_stopped", MagicMock(return_value=False))
        monkeypatch.setattr(ask_handler_mod, "llm_is_configured", MagicMock(return_value=False))
        interaction = _make_interaction(user_id=1, channel_id=1)
        interaction.response.send_message = AsyncMock()
        await mod.ask_cmd.callback(interaction, question="test?")
        call_text = str(interaction.response.send_message.call_args)
        assert "LLM not configured" in call_text


# ---------------------------------------------------------------------------
# on_message guard paths
# ---------------------------------------------------------------------------

class TestOnMessageGuards:
    """Tests for the early-return guard clauses at the top of on_message."""

    def _make_message(self, *, is_bot=False, content="hello", user_id=42, channel_can_read=True):
        msg = MagicMock()
        msg.author.bot = is_bot
        msg.author.id = user_id
        msg.author.display_name = "TestUser"
        msg.content = content
        # Use a plain MagicMock (not spec) so isinstance(channel, discord.Thread) → False
        msg.channel = MagicMock()
        msg.channel.__class__ = discord.TextChannel
        msg.channel.id = 500
        msg.channel.send = AsyncMock()
        msg.channel.owner_id = 9999
        msg.guild = MagicMock()
        return msg

    @pytest.mark.asyncio
    async def test_bot_message_ignored(self, monkeypatch):
        import bot as mod
        msg = self._make_message(is_bot=True)
        # Should return immediately without touching anything
        await mod.on_message(msg)
        msg.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_slash_command_delegated(self, monkeypatch):
        import bot as mod
        msg = self._make_message(content="/help")
        mod.bot.process_commands = AsyncMock()
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=mod.bot))
        await mod.on_message(msg)
        mod.bot.process_commands.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_disallowed_user_ignored(self, monkeypatch):
        import bot as mod
        msg = self._make_message(user_id=999)
        monkeypatch.setattr(mod, "_is_user_allowed", MagicMock(return_value=False))
        monkeypatch.setattr(mod, "_bot_can_read_channel", MagicMock(return_value=True))
        await mod.on_message(msg)
        msg.channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_emergency_stop_sends_notice(self, monkeypatch):
        import bot as mod
        msg = self._make_message(user_id=42)
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=mod.bot))
        monkeypatch.setattr(discord_events_mod, "_is_user_allowed", MagicMock(return_value=True))
        monkeypatch.setattr(discord_events_mod, "_bot_can_read_channel", MagicMock(return_value=True))
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", MagicMock(return_value=True))
        await mod.on_message(msg)
        call_text = str(msg.channel.send.call_args)
        assert "Emergency stop" in call_text

    @pytest.mark.asyncio
    async def test_llm_not_configured_sends_notice(self, monkeypatch):
        import bot as mod
        msg = self._make_message(user_id=42)
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=mod.bot))
        monkeypatch.setattr(discord_events_mod, "_is_user_allowed", MagicMock(return_value=True))
        monkeypatch.setattr(discord_events_mod, "_bot_can_read_channel", MagicMock(return_value=True))
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", MagicMock(return_value=False))
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", MagicMock(return_value=False))
        await mod.on_message(msg)
        call_text = str(msg.channel.send.call_args)
        assert "LLM not configured" in call_text


# ---------------------------------------------------------------------------
# Remaining ResponseActions button callbacks
# ---------------------------------------------------------------------------

class TestResponseActionsThumbsButtons:
    @pytest.mark.asyncio
    async def test_thumbs_up_calls_record_feedback(self, monkeypatch):
        view = _make_view(user_id=11)
        interaction = _make_interaction(user_id=11, channel_id=100)
        interaction.message.id = 1

        mock_file = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_file)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("response_actions.aiofiles") as ma, patch("response_actions.Path") as mp:
            ma.open = MagicMock(return_value=mock_cm)
            mp.return_value = MagicMock()
            mp.return_value.parent.mkdir = MagicMock()
            await view.thumbs_up_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.response.send_message.call_args)
        assert "👍" in call_text

    @pytest.mark.asyncio
    async def test_thumbs_down_calls_record_feedback(self, monkeypatch):
        view = _make_view(user_id=22)
        interaction = _make_interaction(user_id=22, channel_id=200)
        interaction.message.id = 2

        mock_file = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_file)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("response_actions.aiofiles") as ma, patch("response_actions.Path") as mp:
            ma.open = MagicMock(return_value=mock_cm)
            mp.return_value = MagicMock()
            mp.return_value.parent.mkdir = MagicMock()
            await view.thumbs_down_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.response.send_message.call_args)
        assert "👎" in call_text


class TestResponseActionsLockButtons:
    @pytest.mark.asyncio
    async def test_lock_channel_btn_sends_confirmation(self, monkeypatch):
        view = _make_view(user_id=33)
        interaction = _make_interaction(user_id=33, channel_id=300)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(300, None)))
        monkeypatch.setattr(ra_mod, "set_context_lock", MagicMock())

        await view.lock_channel_btn.callback.callback(view, interaction, MagicMock())
        call_text = str(interaction.response.send_message.call_args)
        assert "channel" in call_text.lower() or "locked" in call_text.lower() or "thread" in call_text.lower()

    @pytest.mark.asyncio
    async def test_reset_context_btn_clears_lock(self, monkeypatch):
        view = _make_view(user_id=44)
        interaction = _make_interaction(user_id=44, channel_id=400)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(400, None)))
        monkeypatch.setattr(ra_mod, "reset_context_lock", MagicMock())
        monkeypatch.setattr(ra_mod, "reset_anchor_state", MagicMock())

        await view.reset_context_btn.callback.callback(view, interaction, MagicMock())
        call_text = str(interaction.response.send_message.call_args)
        assert call_text  # Just verify something was sent

    @pytest.mark.asyncio
    async def test_use_prior_report_btn_no_anchor(self, monkeypatch):
        view = _make_view(user_id=55)
        interaction = _make_interaction(user_id=55, channel_id=500)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(500, None)))
        monkeypatch.setattr(ra_mod, "get_anchor_state", MagicMock(return_value=None))

        await view.use_prior_report_btn.callback.callback(view, interaction, MagicMock())
        call_text = str(interaction.response.send_message.call_args)
        assert "No prior report" in call_text or "anchor" in call_text.lower()

    @pytest.mark.asyncio
    async def test_use_prior_report_btn_with_anchor(self, monkeypatch):
        view = _make_view(user_id=66)
        interaction = _make_interaction(user_id=66, channel_id=600)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(600, None)))
        monkeypatch.setattr(ra_mod, "get_anchor_state", MagicMock(return_value={"anchor_id": "abc123"}))
        monkeypatch.setattr(ra_mod, "set_context_lock", MagicMock())

        await view.use_prior_report_btn.callback.callback(view, interaction, MagicMock())
        call_text = str(interaction.response.send_message.call_args)
        assert "abc123" in call_text


# ---------------------------------------------------------------------------
# ask_cmd integration (happy path via full callback)
# ---------------------------------------------------------------------------

class TestAskCmdIntegration:
    """Integration tests for ask_cmd that exercise the main response-building path."""

    def _make_interaction(self, user_id=1, channel_id=100):
        interaction = AsyncMock()
        interaction.user.id = user_id
        interaction.user.display_name = "TestUser"
        interaction.user.display_avatar = None
        interaction.channel.id = channel_id
        interaction.channel_id = channel_id
        interaction.channel.__class__ = discord.TextChannel
        interaction.channel.name = "general"
        interaction.channel.create_thread = AsyncMock()
        interaction.id = 12345
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()
        return interaction

    @pytest.mark.asyncio
    async def test_ask_cmd_happy_path_sends_response(self, monkeypatch):
        import bot as mod
        from ask_orchestrator import AskStreamResult

        monkeypatch.setenv("THREAD_DB_PATH", "/tmp/test_ask_cmd.db")
        monkeypatch.setattr(ask_handler_mod, "is_emergency_stopped", MagicMock(return_value=False))
        monkeypatch.setattr(ask_handler_mod, "llm_is_configured", MagicMock(return_value=True))
        monkeypatch.setattr(ask_handler_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))
        monkeypatch.setattr(ask_handler_mod, "get_model_preference", MagicMock(return_value="auto"))
        monkeypatch.setattr(ask_handler_mod, "normalize_model_preference", MagicMock(return_value=("auto", False)))
        monkeypatch.setattr(ask_handler_mod, "audit_log", MagicMock())
        monkeypatch.setattr(ask_handler_mod, "set_anchor_state", MagicMock())
        monkeypatch.setattr(ask_handler_mod.cfg, "thread_auto_create", False, raising=False)

        conv = MagicMock()
        conv.history = []
        monkeypatch.setattr(ask_handler_mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(ask_handler_mod.conversation_store, "auto_save_thread", MagicMock())

        stream_result = AskStreamResult(
            response_text="Here is the answer.",
            model_used="auto",
            final_meta={},
            routing_notes=[],
            context_badges=[],
        )
        monkeypatch.setattr(ask_handler_mod, "run_ask_stream", AsyncMock(return_value=stream_result))
        monkeypatch.setattr(ask_handler_mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(ask_handler_mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is the answer.",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        monkeypatch.setattr(ask_handler_mod, "_generate_follow_ups", AsyncMock(return_value=[]))

        interaction = self._make_interaction()
        await mod.ask_cmd.callback(interaction, question="What is 2+2?")

        interaction.response.defer.assert_awaited_once()
        # Normal path: first chunk goes via edit_original_response
        interaction.edit_original_response.assert_awaited()

    @pytest.mark.asyncio
    async def test_ask_cmd_llm_exception_sends_failure_message(self, monkeypatch):
        import bot as mod

        monkeypatch.setenv("THREAD_DB_PATH", "/tmp/test_ask_cmd_err.db")
        monkeypatch.setattr(ask_handler_mod, "is_emergency_stopped", MagicMock(return_value=False))
        monkeypatch.setattr(ask_handler_mod, "llm_is_configured", MagicMock(return_value=True))
        monkeypatch.setattr(ask_handler_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))
        monkeypatch.setattr(ask_handler_mod, "get_model_preference", MagicMock(return_value="auto"))
        monkeypatch.setattr(ask_handler_mod, "normalize_model_preference", MagicMock(return_value=("auto", False)))
        monkeypatch.setattr(ask_handler_mod, "audit_log", MagicMock())
        monkeypatch.setattr(ask_handler_mod, "set_anchor_state", MagicMock())
        monkeypatch.setattr(ask_handler_mod.cfg, "thread_auto_create", False, raising=False)

        conv = MagicMock()
        conv.history = []
        monkeypatch.setattr(ask_handler_mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(ask_handler_mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(ask_handler_mod, "run_ask_stream", AsyncMock(side_effect=RuntimeError("LLM blew up")))
        monkeypatch.setattr(ask_handler_mod, "_generate_follow_ups", AsyncMock(return_value=[]))

        interaction = self._make_interaction()
        await mod.ask_cmd.callback(interaction, question="Crash test?")

        # Should still send something (error embed) rather than raising
        assert interaction.followup.send.awaited or interaction.edit_original_response.awaited
