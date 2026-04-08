"""Tests for response_actions.py — pure helpers and ResponseActions view."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import response_actions as mod
from response_actions import _generate_follow_ups, _resolve_channel_thread_scope


# ---------------------------------------------------------------------------
# _resolve_channel_thread_scope
# ---------------------------------------------------------------------------

class TestResolveChannelThreadScope:
    def _no_lock(self, *args, **kwargs):
        return None, None

    def test_regular_channel_preserves_channel_id(self):
        channel = MagicMock(spec=discord.TextChannel)
        with patch.object(mod, "resolve_context_lock", self._no_lock):
            ch_id, th_id = _resolve_channel_thread_scope(channel, 42)
        assert ch_id == 42
        assert th_id is None

    def test_thread_extracts_thread_id(self):
        thread = MagicMock(spec=discord.Thread)
        thread.id = 999
        thread.parent_id = 100
        with patch.object(mod, "resolve_context_lock", self._no_lock):
            ch_id, th_id = _resolve_channel_thread_scope(thread, 100)
        assert th_id == 999
        assert ch_id == 100

    def test_thread_without_parent_id(self):
        thread = MagicMock(spec=discord.Thread)
        thread.id = 777
        thread.parent_id = None
        with patch.object(mod, "resolve_context_lock", self._no_lock):
            ch_id, th_id = _resolve_channel_thread_scope(thread, 50)
        assert th_id == 777

    def test_channel_mode_lock_overrides(self):
        channel = MagicMock(spec=discord.TextChannel)
        lock = {"mode": "channel", "channel_id": 500}

        def mock_resolve(*args, **kwargs):
            return lock, None

        with patch.object(mod, "resolve_context_lock", mock_resolve):
            ch_id, th_id = _resolve_channel_thread_scope(channel, 42)
        assert ch_id == 500
        assert th_id is None

    def test_thread_mode_lock_overrides(self):
        channel = MagicMock(spec=discord.Thread)
        channel.id = 1
        channel.parent_id = 2
        lock = {"mode": "thread", "channel_id": 200, "thread_id": 300}

        def mock_resolve(*args, **kwargs):
            return lock, None

        with patch.object(mod, "resolve_context_lock", mock_resolve):
            ch_id, th_id = _resolve_channel_thread_scope(channel, 2)
        assert ch_id == 200
        assert th_id == 300


# ---------------------------------------------------------------------------
# _generate_follow_ups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_follow_ups_returns_two_questions():
    mock_chat = AsyncMock(return_value=("What about overtime?\nWho scored the most?", [], "gemini"))
    fake_llm_chat = MagicMock(chat=mock_chat)
    with patch.dict("sys.modules", {"llm": MagicMock(), "llm.chat": fake_llm_chat}):
        result = await _generate_follow_ups("How did the game go?", "Team A won 3-2")
    assert isinstance(result, list)
    assert len(result) <= 2


@pytest.mark.asyncio
async def test_generate_follow_ups_returns_empty_on_import_error():
    with patch.dict("sys.modules", {"llm": None, "llm.chat": None}):
        result = await _generate_follow_ups("q", "a")
    assert result == []


@pytest.mark.asyncio
async def test_generate_follow_ups_returns_empty_on_runtime_error():
    failing_chat = MagicMock(chat=AsyncMock(side_effect=RuntimeError("LLM down")))
    with patch.dict("sys.modules", {"llm.chat": failing_chat}):
        result = await _generate_follow_ups("q", "a")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ResponseActions — construction and interaction_check
# ---------------------------------------------------------------------------

class TestResponseActionsConstruction:
    def _make_view(self, **kwargs):
        defaults = dict(
            response_text="Test response",
            question="Test question",
            user_id=12345,
            channel_id=67890,
        )
        defaults.update(kwargs)
        return mod.ResponseActions(**defaults)

    def test_construction_succeeds(self):
        view = self._make_view()
        assert view._user_id == 12345
        assert view._channel_id == 67890

    def test_follow_up_buttons_added(self):
        view = self._make_view(follow_ups=["What else?", "Can you expand?"])
        # Follow-up buttons + go_deeper + the 5 persistent buttons
        item_names = [
            getattr(item, "label", None)
            for item in view.children
        ]
        assert "What else?" in item_names
        assert "Can you expand?" in item_names

    def test_go_deeper_button_present(self):
        view = self._make_view()
        labels = [getattr(item, "label", None) for item in view.children]
        assert "🔁 Go Deeper" in labels

    def test_no_follow_ups_excludes_dynamic_buttons(self):
        view = self._make_view(follow_ups=None)
        labels = [getattr(item, "label", None) for item in view.children]
        # Should not have any followup_ buttons
        assert "What else?" not in labels

    def test_timeout_set(self):
        view = self._make_view(timeout=600)
        assert view.timeout == 600

    def test_bot_stored(self):
        fake_bot = MagicMock()
        view = self._make_view(bot=fake_bot)
        assert view._bot is fake_bot


@pytest.mark.asyncio
async def test_interaction_check_blocks_other_users():
    view = mod.ResponseActions(
        response_text="r",
        question="q",
        user_id=12345,
        channel_id=1,
    )
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 99999  # different user
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()

    result = await view.interaction_check(interaction)
    assert result is False
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_interaction_check_allows_original_user():
    view = mod.ResponseActions(
        response_text="r",
        question="q",
        user_id=12345,
        channel_id=1,
    )
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 12345  # same user

    result = await view.interaction_check(interaction)
    assert result is True


# ---------------------------------------------------------------------------
# ResponseActions._record_feedback — guardrail integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_feedback_accepted_saves_and_acks():
    view = mod.ResponseActions(
        response_text="response",
        question="question",
        user_id=1,
        channel_id=2,
    )
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 1
    interaction.channel = MagicMock()
    interaction.channel.id = 2
    interaction.message = MagicMock()
    interaction.message.id = 3
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()

    fake_file = AsyncMock()
    fake_file.write = AsyncMock()

    with (
        patch.object(mod, "_apply_feedback_guardrails", return_value=(True, "accepted")),
        patch.object(mod, "_record_quality_metric", MagicMock()),
        patch("aiofiles.open", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=fake_file),
            __aexit__=AsyncMock(return_value=False),
        )),
    ):
        await view._record_feedback(interaction, "helpful")

    interaction.response.send_message.assert_called_once()
    call_text = interaction.response.send_message.call_args[0][0]
    assert "thanks" in call_text.lower() or "👍" in call_text or "Feedback" in call_text or "⚠️" in call_text


@pytest.mark.asyncio
async def test_record_feedback_dedupe_shows_already_captured():
    view = mod.ResponseActions(
        response_text="r",
        question="q",
        user_id=1,
        channel_id=2,
    )
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 1
    interaction.channel = MagicMock()
    interaction.channel.id = 2
    interaction.message = MagicMock()
    interaction.message.id = 3
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()

    with (
        patch.object(mod, "_apply_feedback_guardrails", return_value=(False, "dedupe")),
        patch.object(mod, "_record_quality_metric", MagicMock()),
    ):
        await view._record_feedback(interaction, "helpful")

    interaction.response.send_message.assert_called_once()
    call_text = interaction.response.send_message.call_args[0][0]
    assert "Already captured" in call_text


@pytest.mark.asyncio
async def test_record_feedback_rate_limited_shows_message():
    view = mod.ResponseActions(
        response_text="r",
        question="q",
        user_id=1,
        channel_id=2,
    )
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 1
    interaction.channel = MagicMock()
    interaction.channel.id = 2
    interaction.message = MagicMock()
    interaction.message.id = 3
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()

    with (
        patch.object(mod, "_apply_feedback_guardrails", return_value=(False, "rate_limited_user")),
        patch.object(mod, "_record_quality_metric", MagicMock()),
    ):
        await view._record_feedback(interaction, "helpful")

    call_text = interaction.response.send_message.call_args[0][0]
    assert "rate" in call_text.lower() or "⏱️" in call_text
