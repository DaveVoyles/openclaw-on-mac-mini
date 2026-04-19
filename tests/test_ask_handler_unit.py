"""
test_ask_handler_unit.py — Characterization tests for handle_ask() in ask_handler.py.

Tests pin current behavior as a safety net for the planned decomposition refactor.
Uses AsyncMock for Discord interactions; patches LLM + external calls to avoid
network access or API keys.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_result(
    response_text: str = "Hello, world!",
    model_used: str = "gemini-test",
) -> MagicMock:
    """Return a minimal object that satisfies result = await run_ask_stream(...)."""
    result = MagicMock()
    result.response_text = response_text
    result.model_used = model_used
    result.final_meta = {}
    result.routing_notes = []
    result.context_badges = []
    return result


def _make_interaction(
    user_id: int = 12345,
    channel_id: int = 67890,
    interaction_id: int = 99999,
) -> MagicMock:
    """Build a Discord Interaction mock with all attributes handle_ask() touches."""
    interaction = MagicMock()
    interaction.id = interaction_id

    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()

    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()

    interaction.edit_original_response = AsyncMock()

    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.name = "TestUser"
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.png"

    # Plain TextChannel — not discord.Thread / discord.DMChannel
    interaction.channel = MagicMock()
    interaction.channel.id = channel_id
    interaction.channel_id = channel_id
    # create_thread must be awaitable so auto-thread logic doesn't crash
    interaction.channel.create_thread = AsyncMock(
        return_value=MagicMock(
            name="test-thread",
            mention="#test-thread",
            send=AsyncMock(),
        )
    )

    return interaction


@contextlib.contextmanager
def _standard_patches(stream_result=None, conv_store=None, extra: dict | None = None):
    """Apply all patches needed to isolate handle_ask() from external deps."""
    if stream_result is None:
        stream_result = _make_stream_result()
    if conv_store is None:
        store = MagicMock()
        conv = MagicMock()
        conv.history = []
        conv.message_count = 1
        conv.update_from_llm = MagicMock()
        store.get = MagicMock(return_value=conv)
        store.cleanup_expired = MagicMock()
        conv_store = store

    loop_mock = MagicMock()

    # Consume the coroutine passed to create_task to suppress "never awaited" warning
    def _consume_coro(coro):
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    loop_mock.create_task = MagicMock(side_effect=_consume_coro)

    patches = {
        "ask_handler.is_emergency_stopped": MagicMock(return_value=False),
        "ask_handler.llm_is_configured": MagicMock(return_value=True),
        "ask_handler.conversation_store": conv_store,
        "ask_handler._resolve_channel_thread_scope": MagicMock(return_value=(67890, None)),
        "ask_handler.run_ask_stream": AsyncMock(return_value=stream_result),
        "ask_handler.normalize_model_preference": MagicMock(return_value=("auto", False)),
        "ask_handler.get_model_preference": MagicMock(return_value="auto"),
        "ask_handler.get_routing_profile": MagicMock(return_value={}),
        "ask_handler._generate_follow_ups": AsyncMock(return_value=[]),
        "ask_handler.audit_log": MagicMock(),
        "ask_handler.ResponseActions": MagicMock(return_value=MagicMock()),
        # Patch the runtime_state inline imports to avoid bot.py loading /logs
        "runtime_state.get_channel_roles": MagicMock(return_value={}),
        "runtime_state.get_channel_prompts": MagicMock(return_value={}),
        "runtime_state.request_context": MagicMock(return_value=contextlib.nullcontext()),
        # Stop asyncio.get_running_loop from spawning real tasks in post-response hook
        "asyncio.get_running_loop": MagicMock(return_value=loop_mock),
        # Formatting helpers that touch /memory at channel-profile lookup
        "ask_handler._format_tables_for_context": MagicMock(side_effect=lambda text, **kw: text),
        "ask_handler._format_markdown_for_discord": MagicMock(side_effect=lambda text: text),
        # rules_engine raises TypeError (not in narrow except) when chromadb is mocked
        "rules_engine.get_relevant_rules": AsyncMock(return_value=[]),
    }
    if extra:
        patches.update(extra)

    with contextlib.ExitStack() as stack:
        mocks = {k: stack.enter_context(patch(k, v)) for k, v in patches.items()}
        yield mocks


async def _run(interaction, question: str = "Test question", **kwargs):
    from ask_handler import handle_ask

    await handle_ask(interaction, question, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def interaction():
    return _make_interaction()


@pytest.fixture()
def stream_result():
    return _make_stream_result()


@pytest.fixture()
def conv_store():
    store = MagicMock()
    conv = MagicMock()
    conv.history = []
    conv.message_count = 1
    conv.update_from_llm = MagicMock()
    store.get = MagicMock(return_value=conv)
    store.cleanup_expired = MagicMock()
    return store


# ---------------------------------------------------------------------------
# Class 1: Basic behavior
# ---------------------------------------------------------------------------


class TestHandleAskBasic:
    """handle_ask() with a normal successful LLM response."""

    @pytest.mark.asyncio
    async def test_defers_response_first(self, interaction, stream_result, conv_store):
        """handle_ask() always defers the interaction response before any LLM work."""
        with _standard_patches(stream_result=stream_result, conv_store=conv_store):
            await _run(interaction)

        interaction.response.defer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_edits_original_response_on_success(self, interaction, stream_result, conv_store):
        """handle_ask() edits the original deferred response after LLM returns."""
        with _standard_patches(stream_result=stream_result, conv_store=conv_store):
            await _run(interaction)

        interaction.edit_original_response.assert_awaited()

    @pytest.mark.asyncio
    async def test_emergency_stop_sends_ephemeral_and_returns(self, interaction):
        """When emergency stop is active, sends ephemeral message and returns early."""
        with patch("ask_handler.is_emergency_stopped", return_value=True):
            await _run(interaction)

        interaction.response.send_message.assert_awaited_once()
        sent_kwargs = interaction.response.send_message.call_args
        assert sent_kwargs.kwargs.get("ephemeral") is True
        interaction.response.defer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_not_configured_sends_ephemeral(self, interaction):
        """When LLM is not configured, sends config-guidance ephemeral message."""
        with (
            patch("ask_handler.is_emergency_stopped", return_value=False),
            patch("ask_handler.llm_is_configured", return_value=False),
        ):
            await _run(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
        interaction.response.defer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_attachment_completes_normally(self, interaction, stream_result, conv_store):
        """handle_ask() succeeds when no attachment is supplied."""
        with _standard_patches(stream_result=stream_result, conv_store=conv_store):
            await _run(interaction, attachment=None)

        interaction.response.defer.assert_awaited_once()


# ---------------------------------------------------------------------------
# Class 2: Attachment handling
# ---------------------------------------------------------------------------


class TestHandleAskAttachment:
    """Attachment routing — images vs. documents vs. oversized."""

    @pytest.mark.asyncio
    async def test_image_attachment_calls_handle_image(self, interaction, stream_result, conv_store):
        """When attachment is an image within size limit, _handle_image_attachment is called."""
        attachment = MagicMock()
        attachment.content_type = "image/png"
        attachment.size = 1024

        mock_img = AsyncMock(return_value="Describe this image")
        with _standard_patches(
            stream_result=stream_result,
            conv_store=conv_store,
            extra={
                "ask_handler.SUPPORTED_IMAGE_MIMES": {"image/png", "image/jpeg"},
                "ask_handler.MAX_FILE_SIZE": 10 * 1024 * 1024,
                "ask_handler._handle_image_attachment": mock_img,
            },
        ):
            await _run(interaction, attachment=attachment)

        mock_img.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_doc_attachment_calls_handle_doc(self, interaction, stream_result, conv_store):
        """Non-image attachment within size limit routes to _handle_doc_attachment."""
        attachment = MagicMock()
        attachment.content_type = "text/plain"
        attachment.size = 512

        mock_doc = AsyncMock(return_value="Doc contents")
        with _standard_patches(
            stream_result=stream_result,
            conv_store=conv_store,
            extra={
                "ask_handler.SUPPORTED_IMAGE_MIMES": {"image/png", "image/jpeg"},
                "ask_handler.MAX_FILE_SIZE": 10 * 1024 * 1024,
                "ask_handler._handle_doc_attachment": mock_doc,
            },
        ):
            await _run(interaction, attachment=attachment)

        mock_doc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oversized_attachment_skips_both_handlers(self, interaction, stream_result, conv_store):
        """Attachments exceeding MAX_FILE_SIZE skip both image and doc handlers."""
        attachment = MagicMock()
        attachment.content_type = "image/png"
        attachment.size = 999_999_999

        mock_img = AsyncMock()
        mock_doc = AsyncMock()
        with _standard_patches(
            stream_result=stream_result,
            conv_store=conv_store,
            extra={
                "ask_handler.SUPPORTED_IMAGE_MIMES": {"image/png"},
                "ask_handler.MAX_FILE_SIZE": 10 * 1024 * 1024,
                "ask_handler._handle_image_attachment": mock_img,
                "ask_handler._handle_doc_attachment": mock_doc,
            },
        ):
            await _run(interaction, attachment=attachment)

        mock_img.assert_not_awaited()
        mock_doc.assert_not_awaited()


# ---------------------------------------------------------------------------
# Class 3: Error paths
# ---------------------------------------------------------------------------


class TestHandleAskErrorPaths:
    """handle_ask() resilience: LLM errors, timeouts."""

    @pytest.mark.asyncio
    async def test_llm_exception_does_not_propagate(self, interaction, conv_store):
        """LLM exception is caught; handle_ask() completes without raising."""
        with _standard_patches(
            conv_store=conv_store,
            extra={"ask_handler.run_ask_stream": AsyncMock(side_effect=RuntimeError("LLM exploded"))},
        ):
            await _run(interaction)  # must not raise

        assert interaction.edit_original_response.await_count > 0 or interaction.followup.send.await_count > 0, (
            "Expected at least one response after LLM error"
        )

    @pytest.mark.asyncio
    async def test_llm_exception_sends_non_empty_response(self, interaction, conv_store):
        """On LLM error the user still receives a non-empty response."""
        with _standard_patches(
            conv_store=conv_store,
            extra={"ask_handler.run_ask_stream": AsyncMock(side_effect=ValueError("bad model"))},
        ):
            await _run(interaction)

        sent_texts = []
        for call in interaction.edit_original_response.await_args_list:
            embed = call.kwargs.get("embed")
            if embed and getattr(embed, "description", None):
                sent_texts.append(embed.description)
        for call in interaction.followup.send.await_args_list:
            embed = call.kwargs.get("embed")
            if embed and getattr(embed, "description", None):
                sent_texts.append(embed.description)

        assert " ".join(sent_texts), "Expected non-empty response text on LLM error"

    @pytest.mark.asyncio
    async def test_timeout_does_not_propagate(self, interaction, conv_store):
        """asyncio.TimeoutError from LLM is converted to a timeout message, not re-raised."""
        with _standard_patches(
            conv_store=conv_store,
            extra={"ask_handler.run_ask_stream": AsyncMock(side_effect=asyncio.TimeoutError())},
        ):
            await _run(interaction)  # must not raise

        assert interaction.edit_original_response.await_count > 0 or interaction.followup.send.await_count > 0

    @pytest.mark.asyncio
    async def test_audit_log_always_called(self, interaction, stream_result, conv_store):
        """audit_log() is called exactly once on a successful response."""
        mock_audit = MagicMock()
        with _standard_patches(
            stream_result=stream_result,
            conv_store=conv_store,
            extra={"ask_handler.audit_log": mock_audit},
        ):
            await _run(interaction, question="What is 2+2?")

        mock_audit.assert_called_once()
        assert mock_audit.call_args.args[1] == "ask"
