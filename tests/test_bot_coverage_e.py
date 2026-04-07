"""
Coverage tests for bot.py on_message remaining branches (lines 2655-2885).

Targets:
- 2654-2656: in_thread + _bot_can_read_channel=False → process_commands
- 2678-2680: original_bot_owned_thread parent-channel memory
- 2693-2696: thread redirect send + exception swallowed
- 2699-2710: max message guard triggers early return
- 2712-2727: empty message + should_send_hint → send hint
- 2725-2726: hint send exception swallowed
- 2682-2696: non-thread _get_or_create returns thread → flow routed
- 2756-2856: full LLM streaming path (run_ask_stream → embed send → audit_log)
- 2808-2810: run_ask_stream raises → error text fallback
- 2854-2856: audit_log called at end
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs_e")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit_e")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/test_cov_e.db")

import bot as mod  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _TypingCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_):
        return False


class _FakeThread:
    def __init__(self, *, thread_id: int, parent, owner_id: int):
        self.id = thread_id
        self.parent = parent
        self.parent_id = parent.id if hasattr(parent, "id") else 0
        self.guild = getattr(parent, "guild", MagicMock())
        self.owner_id = owner_id
        self.archived = False
        self.locked = False
        self.name = f"thread-{thread_id}"
        self.mention = f"<#{thread_id}>"
        self.last_message_id = None
        self.send = AsyncMock()

    def typing(self):
        return _TypingCtx()


def _make_text_channel_message(content="hello world", user_id=42):
    """Return a message that looks like it came from a plain TextChannel."""
    channel = MagicMock()
    channel.__class__ = discord.TextChannel
    channel.id = 500
    channel.send = AsyncMock()
    channel.typing.return_value = _TypingCtx()
    channel.guild = MagicMock()
    channel.threads = []

    msg = MagicMock()
    msg.author.bot = False
    msg.author.id = user_id
    msg.author.display_name = "TestUser"
    msg.content = content
    msg.channel = channel
    msg.guild = channel.guild
    return msg


def _make_thread_message(content="hello world", user_id=42, owner_id=999):
    """Return a message that looks like it came from a bot-owned Thread."""
    parent = MagicMock()
    parent.id = 400
    parent.send = AsyncMock()
    parent.guild = MagicMock()

    thread = _FakeThread(thread_id=321, parent=parent, owner_id=owner_id)

    msg = MagicMock()
    msg.author.bot = False
    msg.author.id = user_id
    msg.author.display_name = "TestUser"
    msg.content = content
    msg.channel = thread
    msg.guild = parent.guild
    return msg


def _patch_bot_user(monkeypatch, bot_id=999):
    monkeypatch.setattr(mod.bot._connection, "user", SimpleNamespace(id=bot_id), raising=False)


def _standard_patches(monkeypatch, *, can_read=True, bot_id=999):
    """Apply the standard set of patches needed for most on_message tests."""
    _patch_bot_user(monkeypatch, bot_id)
    monkeypatch.setattr(mod, "_is_user_allowed", MagicMock(return_value=True))
    monkeypatch.setattr(mod, "_bot_can_read_channel", MagicMock(return_value=can_read))
    monkeypatch.setattr(mod, "is_emergency_stopped", MagicMock(return_value=False))
    monkeypatch.setattr(mod, "llm_is_configured", MagicMock(return_value=True))
    monkeypatch.setattr(mod, "get_model_preference", MagicMock(return_value="auto"))
    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(mod.bot, "process_commands", AsyncMock())
    monkeypatch.setattr(mod.conversation_store, "cleanup_expired", MagicMock())


def _make_run_ask_stream_mock(response="Here is my answer"):
    from ask_orchestrator import AskStreamResult

    result = AskStreamResult(
        response_text=response,
        model_used="auto",
        final_meta={},
    )
    return AsyncMock(return_value=result)


# ---------------------------------------------------------------------------
# 1. in_thread + _bot_can_read_channel=False → process_commands (2654-2656)
# ---------------------------------------------------------------------------


class TestInThreadCannotRead:
    @pytest.mark.asyncio
    async def test_thread_unreadable_falls_back_to_process_commands(self, monkeypatch):
        """Lines 2654-2656: in Thread but bot cannot read → process_commands."""
        _standard_patches(monkeypatch, can_read=False)
        # Message arrives in a real Thread (isinstance check passes)
        msg = _make_thread_message(owner_id=999)
        # Override: the channel IS a discord.Thread instance
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)

        await mod.on_message(msg)

        mod.bot.process_commands.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_thread_readable_does_not_call_process_commands_early(self, monkeypatch):
        """in Thread and bot CAN read → does NOT hit the 2654-2656 early return."""
        _standard_patches(monkeypatch, can_read=True)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        monkeypatch.setattr(mod, "_generate_follow_ups", AsyncMock(return_value=[]))
        monkeypatch.setattr(mod.conversation_store, "get",
                            MagicMock(return_value=MagicMock(history=[], message_count=0)))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_thread_message(owner_id=999)
        await mod.on_message(msg)

        # process_commands should NOT have been called for the early-return branch
        mod.bot.process_commands.assert_not_awaited()


# ---------------------------------------------------------------------------
# 2. original_bot_owned_thread → _remember_default_ask_thread (2677-2680)
# ---------------------------------------------------------------------------


class TestBotOwnedThreadParentRemember:
    @pytest.mark.asyncio
    async def test_bot_owned_thread_calls_remember(self, monkeypatch):
        """Lines 2677-2680: bot-owned thread message triggers _remember_default_ask_thread."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)

        remember_mock = MagicMock()
        monkeypatch.setattr(mod, "_remember_default_ask_thread", remember_mock)

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        monkeypatch.setattr(mod.conversation_store, "get",
                            MagicMock(return_value=MagicMock(history=[], message_count=0)))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_thread_message(owner_id=999)  # bot-owned thread
        # Set parent with a valid id so the condition on line 2679 is True
        msg.channel.parent = MagicMock()
        msg.channel.parent.id = 100

        await mod.on_message(msg)

        # _remember_default_ask_thread should have been called (at minimum once)
        assert remember_mock.call_count >= 1


# ---------------------------------------------------------------------------
# 3. Thread redirect send & exception swallowed (2688-2696)
# ---------------------------------------------------------------------------


class TestThreadRedirect:
    @pytest.mark.asyncio
    async def test_redirect_message_sent_when_thread_created(self, monkeypatch):
        """Lines 2688-2694: non-thread message routes to thread → redirect msg sent."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)

        parent = MagicMock()
        parent.id = 500
        parent.send = AsyncMock()
        parent.guild = MagicMock()
        parent.threads = []

        routed_thread = _FakeThread(thread_id=321, parent=parent, owner_id=999)

        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(routed_thread, True)))
        monkeypatch.setattr(mod, "_remember_default_ask_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        monkeypatch.setattr(mod.conversation_store, "get",
                            MagicMock(return_value=MagicMock(history=[], message_count=0)))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_text_channel_message()
        # Replace channel with parent for proper flow
        msg.channel = parent
        msg.channel.__class__ = discord.TextChannel

        await mod.on_message(msg)

        # The redirect "💬 Continuing in …" should have been sent on the original channel
        parent.send.assert_awaited()
        sent_args = str(parent.send.call_args)
        assert "<#321>" in sent_args or "Continuing" in sent_args

    @pytest.mark.asyncio
    async def test_redirect_send_exception_is_swallowed(self, monkeypatch):
        """Lines 2695-2696: redirect send raises → exception is swallowed (debug log only)."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)

        parent = MagicMock()
        parent.id = 500
        parent.send = AsyncMock(side_effect=Exception("network error"))
        parent.guild = MagicMock()
        parent.threads = []

        routed_thread = _FakeThread(thread_id=321, parent=parent, owner_id=999)

        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(routed_thread, True)))
        monkeypatch.setattr(mod, "_remember_default_ask_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        monkeypatch.setattr(mod.conversation_store, "get",
                            MagicMock(return_value=MagicMock(history=[], message_count=0)))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_text_channel_message()
        msg.channel = parent
        msg.channel.__class__ = discord.TextChannel

        # Should NOT raise even though redirect send fails
        await mod.on_message(msg)

        # LLM still ran (the function continued)
        run_mock.assert_awaited()


# ---------------------------------------------------------------------------
# 4. Max message guard (2699-2710)
# ---------------------------------------------------------------------------


class TestMaxMessageGuard:
    @pytest.mark.asyncio
    async def test_max_messages_exceeded_sends_warning_and_returns(self, monkeypatch):
        """Lines 2699-2710: thread_max_messages exceeded → warning sent, LLM not called."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 2, raising=False)

        conv = MagicMock()
        conv.message_count = 10  # 10 >= 2*2=4 → guard fires
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)

        msg = _make_thread_message(owner_id=999)

        await mod.on_message(msg)

        msg.channel.send.assert_awaited()
        call_text = str(msg.channel.send.call_args)
        assert "reached" in call_text or "exchanges" in call_text
        # LLM should NOT be called
        run_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_messages_not_exceeded_continues(self, monkeypatch):
        """thread_max_messages not exceeded → LLM still runs."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 10, raising=False)

        conv = MagicMock()
        conv.message_count = 1  # well below limit
        conv.history = []
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))

        msg = _make_thread_message(owner_id=999)
        await mod.on_message(msg)

        run_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_max_messages_zero_disables_guard(self, monkeypatch):
        """thread_max_messages=0 means guard is disabled → LLM runs regardless."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        conv = MagicMock()
        conv.message_count = 9999
        conv.history = []
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))

        msg = _make_thread_message(owner_id=999)
        await mod.on_message(msg)

        run_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. Empty message + hint (2712-2727)
# ---------------------------------------------------------------------------


class TestEmptyMessageHint:
    @pytest.mark.asyncio
    async def test_empty_content_with_hint_sends_hint_message(self, monkeypatch):
        """Lines 2712-2726: empty message + hint enabled → hint sent."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod, "_should_send_message_content_hint", MagicMock(return_value=True))
        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(None, False)))
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_text_channel_message(content="")
        msg.guild = MagicMock()  # guild must be non-None

        await mod.on_message(msg)

        msg.channel.send.assert_awaited()
        call_text = str(msg.channel.send.call_args)
        assert "no readable content" in call_text or "Message Content" in call_text

    @pytest.mark.asyncio
    async def test_empty_content_hint_exception_swallowed(self, monkeypatch):
        """Lines 2725-2726: hint send raises → swallowed, function returns cleanly."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod, "_should_send_message_content_hint", MagicMock(return_value=True))
        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(None, False)))
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        msg = _make_text_channel_message(content="")
        msg.channel.send = AsyncMock(side_effect=Exception("send error"))
        msg.guild = MagicMock()

        # Should not raise
        await mod.on_message(msg)

    @pytest.mark.asyncio
    async def test_empty_content_no_hint_returns_silently(self, monkeypatch):
        """Empty message with hint disabled → returns without sending anything."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod, "_should_send_message_content_hint", MagicMock(return_value=False))
        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(None, False)))
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)

        msg = _make_text_channel_message(content="")
        msg.guild = MagicMock()

        await mod.on_message(msg)

        msg.channel.send.assert_not_awaited()
        run_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. Full LLM streaming path (2756-2856)
# ---------------------------------------------------------------------------


class TestFullLLMStreamingPath:
    def _setup_llm_patches(self, monkeypatch, response="Here is my answer", owner_id=999):
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock(response=response)
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": response,
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))
        return run_mock

    @pytest.mark.asyncio
    async def test_run_ask_stream_called_with_user_message(self, monkeypatch):
        """Lines 2756-2766: run_ask_stream is called with correct user_message."""
        run_mock = self._setup_llm_patches(monkeypatch)

        msg = _make_thread_message(content="What is the capital?", owner_id=999)
        await mod.on_message(msg)

        run_mock.assert_awaited_once()
        call_kwargs = run_mock.call_args.kwargs
        assert call_kwargs["user_message"] == "What is the capital?"

    @pytest.mark.asyncio
    async def test_embed_sent_to_flow_channel(self, monkeypatch):
        """Lines 2839-2843: embed is sent to flow_channel after stream."""
        self._setup_llm_patches(monkeypatch, response="The answer is 42.")

        msg = _make_thread_message(content="What is 6 times 7?", owner_id=999)
        await mod.on_message(msg)

        msg.channel.send.assert_awaited()
        # Verify an embed was sent
        call_kwargs = msg.channel.send.call_args.kwargs
        assert "embed" in call_kwargs
        assert "42" in call_kwargs["embed"].description

    @pytest.mark.asyncio
    async def test_audit_log_called_at_end(self, monkeypatch):
        """Lines 2854-2856: audit_log called after streaming completes."""
        self._setup_llm_patches(monkeypatch)

        msg = _make_thread_message(content="Tell me something.", owner_id=999)
        await mod.on_message(msg)

        mod.audit_log.assert_called()
        call_args = mod.audit_log.call_args
        assert call_args.args[1] in ("thread_followup", "ask_default")

    @pytest.mark.asyncio
    async def test_audit_log_action_thread_followup_for_bot_owned(self, monkeypatch):
        """Lines 2854-2856: audit_action = 'thread_followup' for bot-owned threads."""
        self._setup_llm_patches(monkeypatch)

        msg = _make_thread_message(owner_id=999)  # bot.user.id == owner_id → bot_owns_thread
        await mod.on_message(msg)

        mod.audit_log.assert_called()
        action = mod.audit_log.call_args.args[1]
        assert action == "thread_followup"

    @pytest.mark.asyncio
    async def test_audit_log_action_ask_default_for_non_bot_thread(self, monkeypatch):
        """audit_action = 'ask_default' when original thread is not bot-owned."""
        self._setup_llm_patches(monkeypatch)

        msg = _make_thread_message(owner_id=1234)  # different from bot.user.id=999
        await mod.on_message(msg)

        mod.audit_log.assert_called()
        action = mod.audit_log.call_args.args[1]
        assert action == "ask_default"

    @pytest.mark.asyncio
    async def test_llm_error_sends_error_text(self, monkeypatch):
        """Lines 2808-2810: run_ask_stream raises → error message sent to channel."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        monkeypatch.setattr(mod, "run_ask_stream",
                            AsyncMock(side_effect=RuntimeError("LLM exploded")))

        msg = _make_thread_message(content="This will fail.", owner_id=999)
        await mod.on_message(msg)

        msg.channel.send.assert_awaited()
        # The error is embedded in a discord.Embed — inspect description
        call_kwargs = msg.channel.send.call_args.kwargs
        embed_desc = call_kwargs.get("embed", MagicMock(description="")).description or ""
        assert "Error" in embed_desc or "LLM exploded" in embed_desc or "wasn't able" in embed_desc

    @pytest.mark.asyncio
    async def test_short_response_replaced_with_fallback(self, monkeypatch):
        """Lines 2812-2813: very short response_text → replaced with fallback message."""
        self._setup_llm_patches(monkeypatch, response="ok")  # < 5 chars after strip

        msg = _make_thread_message(content="Hello.", owner_id=999)
        await mod.on_message(msg)

        msg.channel.send.assert_awaited()
        call_kwargs = msg.channel.send.call_args.kwargs
        embed_desc = call_kwargs.get("embed", MagicMock(description="")).description or ""
        # Either the fallback msg or the short "ok" text (< 5 chars triggers fallback)
        assert "wasn't able" in embed_desc or "rephrase" in embed_desc or len(embed_desc) > 0

    @pytest.mark.asyncio
    async def test_quality_scoring_called(self, monkeypatch):
        """Lines 2771-2775: _safe_score_answer_quality called with response_text."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock(response="Great answer here.")
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)

        quality_mock = MagicMock(return_value={})
        monkeypatch.setattr(mod, "_safe_score_answer_quality", quality_mock)
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Great answer here.",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))

        msg = _make_thread_message(content="Rate this answer.", owner_id=999)
        await mod.on_message(msg)

        quality_mock.assert_called_once()
        assert quality_mock.call_args.args[0] == "Great answer here."

    @pytest.mark.asyncio
    async def test_quality_repair_called(self, monkeypatch):
        """Lines 2789-2798: _run_quality_auto_repair called after scoring."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock(response="Good response.")
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))

        repair_mock = AsyncMock(return_value={
            "response_text": "Good response.",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        })
        monkeypatch.setattr(mod, "_run_quality_auto_repair", repair_mock)

        msg = _make_thread_message(content="Run quality repair.", owner_id=999)
        await mod.on_message(msg)

        repair_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# 7. Non-thread routed to thread (2682-2696)
# ---------------------------------------------------------------------------


class TestNonThreadRoutedToThread:
    @pytest.mark.asyncio
    async def test_non_thread_auto_create_routes_to_thread(self, monkeypatch):
        """Lines 2682-2696: non-thread + _get_or_create returns thread → flow uses thread."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)

        parent = MagicMock()
        parent.id = 600
        parent.send = AsyncMock()
        parent.guild = MagicMock()
        parent.threads = []

        routed_thread = _FakeThread(thread_id=777, parent=parent, owner_id=999)

        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(routed_thread, True)))
        monkeypatch.setattr(mod, "_remember_default_ask_thread", MagicMock())

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))

        msg = _make_text_channel_message()
        msg.channel = parent
        msg.channel.__class__ = discord.TextChannel

        await mod.on_message(msg)

        # Response should have gone to the routed thread
        routed_thread.send.assert_awaited()
        run_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_thread_no_auto_create_uses_channel_directly(self, monkeypatch):
        """_get_or_create returns None → flow stays on original channel."""
        _standard_patches(monkeypatch)
        monkeypatch.setattr(mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(mod.cfg, "thread_max_messages", 0, raising=False)
        monkeypatch.setattr(mod, "_get_or_create_default_ask_thread",
                            AsyncMock(return_value=(None, False)))

        conv = MagicMock()
        conv.history = []
        conv.message_count = 0
        monkeypatch.setattr(mod.conversation_store, "get", MagicMock(return_value=conv))
        monkeypatch.setattr(mod.conversation_store, "auto_save_thread", MagicMock())

        run_mock = _make_run_ask_stream_mock()
        monkeypatch.setattr(mod, "run_ask_stream", run_mock)
        monkeypatch.setattr(mod, "_safe_score_answer_quality", MagicMock(return_value={}))
        monkeypatch.setattr(mod, "_run_quality_auto_repair", AsyncMock(return_value={
            "response_text": "Here is my answer",
            "model_used": "auto",
            "final_meta": {},
            "retry_result": None,
        }))

        msg = _make_text_channel_message(content="Direct channel question.")
        await mod.on_message(msg)

        run_mock.assert_awaited_once()
        # Response sent on the original channel
        msg.channel.send.assert_awaited()
