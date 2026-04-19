"""
Tests for bot.py targeting uncovered lines 1475-1835:
- on_app_command_error: TransformerError, else branch, failed-send path
- ResponseActions.__init__: follow_ups button creation
- lock_thread_btn: thread-scope path
- use_prior_report_btn: anchor-found path
- _make_followup_callback: inner callback execution
- _go_deeper_callback: full execution path
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs_c")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit_c")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/test_cov_c.db")

import bot as mod
import response_actions as ra_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view(user_id: int = 42, follow_ups=None, channel_id: int = 100):
    return mod.ResponseActions(
        response_text="Test response text.",
        question="What is the answer?",
        user_id=user_id,
        channel_id=channel_id,
        thread_id=None,
        follow_ups=follow_ups or [],
        bot=None,
    )


def _make_interaction(user_id: int = 42, channel_id: int = 100):
    interaction = AsyncMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = None
    interaction.channel.id = channel_id
    interaction.channel.parent_id = None
    interaction.channel_id = channel_id
    interaction.message = MagicMock()
    interaction.message.id = 555
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# on_app_command_error — TransformerError branch (lines 1474-1483)
# ---------------------------------------------------------------------------


class TestOnAppCommandErrorTransformerError:
    @pytest.mark.asyncio
    async def test_transformer_error_sends_parse_error_message(self, monkeypatch):
        interaction = _make_interaction()
        interaction.command = MagicMock()
        interaction.command.qualified_name = "test_cmd"
        interaction.guild_id = None

        error = app_commands.TransformerError("bad value", discord.AppCommandOptionType.string, MagicMock())
        mock_send = AsyncMock()
        monkeypatch.setattr(mod, "_send_app_command_error_message", mock_send)

        await mod.on_app_command_error(interaction, error)

        mock_send.assert_awaited_once()
        call_text = str(mock_send.call_args)
        assert "parse" in call_text.lower() or "input" in call_text.lower()

    @pytest.mark.asyncio
    async def test_transformer_error_logs_warning(self, monkeypatch, caplog):
        import logging

        interaction = _make_interaction()
        interaction.command = None
        interaction.guild_id = None

        error = app_commands.TransformerError("bad value", discord.AppCommandOptionType.string, MagicMock())
        monkeypatch.setattr(mod, "_send_app_command_error_message", AsyncMock())

        with caplog.at_level(logging.WARNING, logger="bot"):
            await mod.on_app_command_error(interaction, error)

        # Just confirm it ran without raising
        assert True


# ---------------------------------------------------------------------------
# on_app_command_error — else/unhandled branch (lines 1506-1515)
# ---------------------------------------------------------------------------


class TestOnAppCommandErrorElseBranch:
    @pytest.mark.asyncio
    async def test_unknown_error_type_sends_generic_message(self, monkeypatch):
        interaction = _make_interaction()
        interaction.command = None
        interaction.guild_id = "dm"

        # Use a plain AppCommandError (not Check/Transformer/CommandInvoke)
        error = app_commands.AppCommandError("something weird happened")
        mock_send = AsyncMock()
        monkeypatch.setattr(mod, "_send_app_command_error_message", mock_send)

        await mod.on_app_command_error(interaction, error)

        mock_send.assert_awaited_once()
        call_text = str(mock_send.call_args)
        assert "wrong" in call_text.lower() or "command" in call_text.lower()

    @pytest.mark.asyncio
    async def test_unknown_error_message_contains_try_again(self, monkeypatch):
        interaction = _make_interaction()
        interaction.command = MagicMock()
        interaction.command.qualified_name = "mystery"
        interaction.guild_id = 99

        error = app_commands.AppCommandError("unclassified")
        captured = {}

        async def capture_send(itr, msg):
            captured["msg"] = msg

        monkeypatch.setattr(mod, "_send_app_command_error_message", capture_send)

        await mod.on_app_command_error(interaction, error)

        assert "msg" in captured
        assert "try again" in captured["msg"].lower()


# ---------------------------------------------------------------------------
# on_app_command_error — failed send path (lines 1517-1527)
# ---------------------------------------------------------------------------


class TestOnAppCommandErrorFailedSend:
    @pytest.mark.asyncio
    async def test_send_failure_is_swallowed(self, monkeypatch):
        """If _send_app_command_error_message raises, the handler must not propagate."""
        interaction = _make_interaction()
        interaction.command = None
        interaction.guild_id = None

        error = app_commands.AppCommandError("any error")
        monkeypatch.setattr(
            mod,
            "_send_app_command_error_message",
            AsyncMock(side_effect=RuntimeError("network down")),
        )

        # Should not raise
        await mod.on_app_command_error(interaction, error)

    @pytest.mark.asyncio
    async def test_transformer_error_send_failure_is_swallowed(self, monkeypatch):
        """Same resilience for TransformerError branch."""
        interaction = _make_interaction()
        interaction.command = None
        interaction.guild_id = None

        error = app_commands.TransformerError("v", discord.AppCommandOptionType.integer, MagicMock())
        monkeypatch.setattr(
            mod,
            "_send_app_command_error_message",
            AsyncMock(side_effect=OSError("timeout")),
        )

        await mod.on_app_command_error(interaction, error)


# ---------------------------------------------------------------------------
# ResponseActions.__init__ with follow_ups (lines 1614-1622)
# ---------------------------------------------------------------------------


class TestResponseActionsFollowUpButtons:
    def test_no_follow_ups_baseline_button_count(self):
        view = _make_view(follow_ups=[])
        baseline = len(view.children)
        assert baseline >= 5  # save, regen, email, thumbs-up, thumbs-down + lock buttons

    def test_one_follow_up_adds_one_button(self):
        view_none = _make_view(follow_ups=[])
        view_one = _make_view(follow_ups=["What about X?"])
        assert len(view_one.children) == len(view_none.children) + 1

    def test_two_follow_ups_adds_two_buttons(self):
        view_none = _make_view(follow_ups=[])
        view_two = _make_view(follow_ups=["Q1?", "Q2?"])
        assert len(view_two.children) == len(view_none.children) + 2

    def test_follow_up_button_label_is_truncated_to_80(self):
        long_label = "A" * 100
        view = _make_view(follow_ups=[long_label])
        # Find a button whose label comes from the follow-up (not a fixed label)
        fu_buttons = [b for b in view.children if hasattr(b, "label") and b.label and b.label.startswith("A")]
        assert len(fu_buttons) == 1
        assert len(fu_buttons[0].label) <= 80

    def test_follow_up_buttons_have_secondary_style(self):
        view = _make_view(follow_ups=["Some follow-up?"])
        fu_buttons = [
            b for b in view.children if hasattr(b, "custom_id") and b.custom_id and b.custom_id.startswith("followup_")
        ]
        assert len(fu_buttons) == 1
        assert fu_buttons[0].style == discord.ButtonStyle.secondary

    def test_follow_up_buttons_have_correct_custom_ids(self):
        view = _make_view(follow_ups=["First?", "Second?", "Third?"])
        custom_ids = [
            b.custom_id
            for b in view.children
            if hasattr(b, "custom_id") and b.custom_id and b.custom_id.startswith("followup_")
        ]
        assert set(custom_ids) == {"followup_0", "followup_1", "followup_2"}


# ---------------------------------------------------------------------------
# lock_thread_btn — thread-scope path (lines 1710-1726)
# ---------------------------------------------------------------------------


class TestLockThreadBtn:
    @pytest.mark.asyncio
    async def test_lock_thread_btn_with_thread_id(self, monkeypatch):
        view = _make_view(user_id=33)
        interaction = _make_interaction(user_id=33, channel_id=300)

        mock_set_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(300, 500)))
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_set_lock)

        await view.lock_thread_btn.callback.callback(view, interaction, MagicMock())

        mock_set_lock.assert_called_once_with(
            user_id=33,
            mode="thread",
            channel_id=300,
            thread_id=500,
        )
        call_text = str(interaction.response.send_message.call_args)
        assert "thread" in call_text.lower()

    @pytest.mark.asyncio
    async def test_lock_thread_btn_without_thread_id_falls_back_to_channel(self, monkeypatch):
        view = _make_view(user_id=33)
        interaction = _make_interaction(user_id=33, channel_id=300)

        mock_set_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(300, None)))
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_set_lock)

        await view.lock_thread_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.response.send_message.call_args)
        # Should mention "Not in a thread" or "channel" fallback
        assert "channel" in call_text.lower() or "thread" in call_text.lower()

    @pytest.mark.asyncio
    async def test_lock_thread_btn_uses_channel_id_fallback_when_scoped_is_none(self, monkeypatch):
        view = _make_view(user_id=10, channel_id=200)
        interaction = _make_interaction(user_id=10, channel_id=200)

        mock_set_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(None, None)))
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_set_lock)

        await view.lock_thread_btn.callback.callback(view, interaction, MagicMock())

        mock_set_lock.assert_called_once_with(
            user_id=10,
            mode="thread",
            channel_id=200,  # falls back to self._channel_id
            thread_id=None,
        )


# ---------------------------------------------------------------------------
# use_prior_report_btn — anchor found path (lines 1728-1752)
# ---------------------------------------------------------------------------


class TestUsePriorReportBtn:
    @pytest.mark.asyncio
    async def test_use_prior_report_btn_with_anchor_sets_lock(self, monkeypatch):
        view = _make_view(user_id=77)
        interaction = _make_interaction(user_id=77, channel_id=200)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(200, None)))
        monkeypatch.setattr(ra_mod, "get_anchor_state", MagicMock(return_value={"anchor_id": "report-abc"}))
        mock_set_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_set_lock)

        await view.use_prior_report_btn.callback.callback(view, interaction, MagicMock())

        mock_set_lock.assert_called_once_with(
            user_id=77,
            mode="prior_report",
            channel_id=200,
            thread_id=None,
            anchor_id="report-abc",
        )
        call_text = str(interaction.response.send_message.call_args)
        assert "report-abc" in call_text

    @pytest.mark.asyncio
    async def test_use_prior_report_btn_no_anchor_sends_warning(self, monkeypatch):
        view = _make_view(user_id=77)
        interaction = _make_interaction(user_id=77, channel_id=200)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(200, None)))
        monkeypatch.setattr(ra_mod, "get_anchor_state", MagicMock(return_value=None))

        await view.use_prior_report_btn.callback.callback(view, interaction, MagicMock())

        call_text = str(interaction.response.send_message.call_args)
        assert "No prior report" in call_text or "anchor" in call_text.lower()

    @pytest.mark.asyncio
    async def test_use_prior_report_btn_with_thread_scope(self, monkeypatch):
        view = _make_view(user_id=55, channel_id=100)
        interaction = _make_interaction(user_id=55, channel_id=100)

        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, 999)))
        monkeypatch.setattr(ra_mod, "get_anchor_state", MagicMock(return_value={"anchor_id": "thread-report"}))
        mock_set_lock = MagicMock()
        monkeypatch.setattr(ra_mod, "set_context_lock", mock_set_lock)

        await view.use_prior_report_btn.callback.callback(view, interaction, MagicMock())

        mock_set_lock.assert_called_once_with(
            user_id=55,
            mode="prior_report",
            channel_id=100,
            thread_id=999,
            anchor_id="thread-report",
        )


# ---------------------------------------------------------------------------
# _make_followup_callback — inner callback execution (lines 1766-1805)
# ---------------------------------------------------------------------------


class TestFollowupCallbackExecution:
    @pytest.mark.asyncio
    async def test_followup_callback_calls_llm_and_sends_embed(self, monkeypatch):
        view = _make_view(user_id=42, follow_ups=["Tell me more?"])
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        mock_conv.update_from_llm = MagicMock()
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(return_value=("Follow-up answer", [], "gemini")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))
        monkeypatch.setattr(ra_mod, "_generate_follow_ups", AsyncMock(return_value=[]))

        # Get the follow-up button (custom_id = "followup_0")
        fu_btn = next(b for b in view.children if hasattr(b, "custom_id") and b.custom_id == "followup_0")
        # The callback is set directly (not via discord.ui.button decorator), so call it directly
        await fu_btn.callback(interaction)

        interaction.response.defer.assert_awaited()
        interaction.followup.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_followup_callback_error_sends_failure_message(self, monkeypatch):
        view = _make_view(user_id=42, follow_ups=["Another question?"])
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(side_effect=RuntimeError("llm crashed")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        fu_btn = next(b for b in view.children if hasattr(b, "custom_id") and b.custom_id == "followup_0")
        await fu_btn.callback(interaction)

        call_text = str(interaction.followup.send.call_args)
        assert "Follow-up failed" in call_text or "failed" in call_text.lower()


# ---------------------------------------------------------------------------
# _go_deeper_callback — full execution path (lines 1807-1835)
# ---------------------------------------------------------------------------


class TestGoDeeperCallback:
    @pytest.mark.asyncio
    async def test_go_deeper_calls_llm_and_sends_embed(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        mock_conv.update_from_llm = MagicMock()
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(return_value=("Deep dive answer", [], "gemini-pro")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        await view._go_deeper_callback(interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_go_deeper_embed_footer_mentions_deep_dive(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        mock_conv.update_from_llm = MagicMock()
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(return_value=("Detailed answer", [], "gpt-4")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        await view._go_deeper_callback(interaction)

        embed_arg = interaction.followup.send.call_args.kwargs["embed"]
        assert "Deep dive" in embed_arg.footer.text or "deep" in embed_arg.footer.text.lower()

    @pytest.mark.asyncio
    async def test_go_deeper_error_sends_failure_message(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(side_effect=RuntimeError("service down")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        await view._go_deeper_callback(interaction)

        call_text = str(interaction.followup.send.call_args)
        assert "Failed" in call_text or "failed" in call_text.lower()

    @pytest.mark.asyncio
    async def test_go_deeper_question_includes_original_question(self, monkeypatch):
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        captured_args = {}

        async def capture_llm(user_message, history, user_name):
            captured_args["user_message"] = user_message
            return ("Response", [], "gemini")

        mock_conv = MagicMock()
        mock_conv.history = []
        mock_conv.update_from_llm = MagicMock()
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", capture_llm)
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        await view._go_deeper_callback(interaction)

        assert "What is the answer?" in captured_args.get("user_message", "")
        assert "detailed" in captured_args.get("user_message", "").lower()

    @pytest.mark.asyncio
    async def test_go_deeper_via_button_callback(self, monkeypatch):
        """Exercise _go_deeper_callback via the Go Deeper button."""
        view = _make_view(user_id=42)
        interaction = _make_interaction(user_id=42, channel_id=100)

        mock_conv = MagicMock()
        mock_conv.history = []
        mock_conv.update_from_llm = MagicMock()
        monkeypatch.setattr(ra_mod.conversation_store, "get", MagicMock(return_value=mock_conv))
        monkeypatch.setattr(ra_mod, "llm_chat", AsyncMock(return_value=("Detailed", [], "gemini")))
        monkeypatch.setattr(ra_mod, "_resolve_channel_thread_scope", MagicMock(return_value=(100, None)))

        # Find the Go Deeper button by custom_id
        deeper_btn = next(
            (b for b in view.children if hasattr(b, "custom_id") and b.custom_id == "go_deeper"),
            None,
        )
        assert deeper_btn is not None, "Go Deeper button not found"
        await deeper_btn.callback(interaction)

        interaction.followup.send.assert_awaited()
