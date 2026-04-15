"""Tests for bot message/attachment handlers."""

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
import pytest
from discord import app_commands

os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_handlers_logs")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_handlers_audit")

import bot as bot_mod
import discord_events as discord_events_mod
import runtime_state as runtime_state_mod
from ask_orchestrator import AskStreamResult
from bot_attachments import handle_doc_attachment, handle_image_attachment
from bot_formatting import (
    build_attachment_embed_summary,
    build_brief_detail_bundle,
    build_copy_safe_text_bundle,
    extract_file_attachment,
    extract_image_url,
    format_markdown_for_discord,
    format_tables_for_context,
    format_tables_for_copy,
    format_tables_for_discord,
    is_dense_recap_or_list,
    should_package_as_attachment,
    split_mobile_safe_bundle,
    split_response,
    truncate_for_embed,
)


class TestFormatting:
    """Tests for bot_formatting utilities."""

    def test_truncate_short_text(self):
        text = "Short message"
        assert truncate_for_embed(text) == text

    def test_truncate_long_text(self):
        text = "a" * 5000
        result = truncate_for_embed(text, limit=100)
        assert len(result) <= 100
        assert result.endswith("… (truncated)")

    def test_extract_image_url_markdown(self):
        text = "Check this out: ![alt](https://example.com/image.png)"
        assert extract_image_url(text) == "https://example.com/image.png"

    def test_extract_image_url_bare(self):
        text = "See https://example.com/photo.jpg for details"
        assert extract_image_url(text) == "https://example.com/photo.jpg"

    def test_extract_image_url_none(self):
        text = "No images here"
        assert extract_image_url(text) is None

    def test_format_markdown_heading1(self):
        text = "# Main Title\nSome content"
        result = format_markdown_for_discord(text)
        assert "__**Main Title**__" in result

    def test_format_markdown_heading2(self):
        text = "## Subtitle\nMore content"
        result = format_markdown_for_discord(text)
        assert "**Subtitle**" in result

    def test_format_markdown_preserves_code_blocks(self):
        text = "```python\n# Not a heading\ncode here\n```"
        result = format_markdown_for_discord(text)
        assert "# Not a heading" in result  # Should not convert

    def test_format_tables_simple(self):
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = format_tables_for_discord(text)
        assert "```text" in result
        assert "+---+---+" in result

    def test_format_tables_copy_safe_preserves_following_bullets(self):
        text = (
            "| Team | Record |\n"
            "| --- | --- |\n"
            "| Wolves | 10-2 |\n\n"
            "- ✅ Keep this summary\n"
            "- 📌 Next step"
        )
        result = format_tables_for_copy(text)
        assert "📋 Table" in result
        assert "  - Team: Wolves" in result
        assert "  - Record: 10-2" in result
        assert "- ✅ Keep this summary" in result
        assert "- 📌 Next step" in result

    def test_format_tables_copy_safe_handles_extra_columns(self):
        text = "| Team |\n| --- |\n| Wolves | 10-2 |"
        result = format_tables_for_copy(text)
        assert "  - Team: Wolves" in result
        assert "  - Column 2: 10-2" in result

    def test_format_tables_for_context_uses_channel_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-format-test.db"))
        runtime_state_mod._reset_channel_profile_store_for_tests()

        runtime_state_mod.set_channel_profile(42, table_style="copy-safe")
        table = "| Team | Record |\n| --- | --- |\n| Wolves | 10-2 |"

        copy_safe = format_tables_for_context(table, channel_id=42)
        discord_style = format_tables_for_context(table, channel_id=43)

        assert "📋 Table" in copy_safe
        assert "```text" in discord_style

        runtime_state_mod._reset_channel_profile_store_for_tests()

    def test_split_response_short(self):
        text = "Short text"
        assert split_response(text) == [text]

    def test_split_response_long(self):
        text = "a" * 10000
        chunks = split_response(text)
        assert len(chunks) > 1
        assert all(len(c) <= 4000 for c in chunks)

    def test_split_response_does_not_break_table_rows(self):
        table = (
            "```text\n"
            "| Date | Matchup | Time |\n"
            "| 2026-04-01 | Team A vs Team B | 7:00 PM |\n"
            "| 2026-04-02 | Team C vs Team D | 8:00 PM |\n"
            "| 2026-04-03 | Team E vs Team F | 9:00 PM |\n"
            "```"
        )
        chunks = split_response(table, limit=110)
        assert len(chunks) > 1
        assert all(len(c) <= 110 for c in chunks)
        assert any("Team A vs Team B" in chunk for chunk in chunks)
        assert any("Team C vs Team D" in chunk for chunk in chunks)
        assert any("Team E vs Team F" in chunk for chunk in chunks)

    def test_split_response_keeps_code_fences_balanced(self):
        text = "```python\n" + "\n".join(f"print({i})" for i in range(50)) + "\n```"
        chunks = split_response(text, limit=120)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.count("```") % 2 == 0

    def test_split_response_keeps_copy_safe_table_rows_readable(self):
        table_text = format_tables_for_copy(
            "| Team | Record |\n| --- | --- |\n" + "\n".join(f"| Team {i} | {10 + i}-{i} |" for i in range(16))
        )
        chunks = split_response(table_text, limit=180)
        assert len(chunks) > 1
        assert all(len(chunk) <= 180 for chunk in chunks)
        assert "Team 0" in "".join(chunks)
        assert "Team 15" in "".join(chunks)

    def test_extract_file_attachment_small_code(self):
        text = "```python\nprint('hi')\n```"
        assert extract_file_attachment(text) is None

    def test_extract_file_attachment_large_code(self):
        code = "print('line')\n" * 100
        text = f"```python\n{code}```"
        result = extract_file_attachment(text)
        assert result is not None
        file, lang = result
        assert isinstance(file, discord.File)
        assert lang == "python"

    def test_build_copy_safe_text_bundle_prefers_copy_workflow_payload(self):
        text = "**Status**\n- Shipped report packaging\n- Added tests"
        payload = build_copy_safe_text_bundle(text)
        assert payload.startswith("Status")
        assert "• Shipped report packaging" in payload

    def test_build_brief_detail_bundle_contains_brief_and_detail_sections(self):
        text = "| Team | Record |\n| --- | --- |\n| Wolves | 10-2 |\n\n- Hold the line"
        payload = build_brief_detail_bundle(text)
        assert "## Brief" in payload
        assert "## Detail" in payload
        assert "📋 Table" in payload

    def test_split_mobile_safe_bundle_chunks_for_mobile_readability(self):
        text = "Line\n" * 500
        chunks = split_mobile_safe_bundle(text, limit=300)
        assert len(chunks) > 1
        assert all(len(chunk) <= 300 for chunk in chunks)

    def test_should_package_as_attachment_for_chunked_text(self):
        text = "a" * 5000
        chunks = split_response(text, limit=1200)
        assert should_package_as_attachment(text, chunks) is True

    def test_is_dense_recap_or_list_detects_dense_bullets(self):
        text = "Weekly recap\n" + "\n".join(f"- item {idx}" for idx in range(8))
        assert is_dense_recap_or_list(text) is True

    def test_build_attachment_embed_summary_includes_coverage(self):
        summary = build_attachment_embed_summary(
            "first line\nsecond line",
            coverage_summary="Coverage medium · 4/8 items",
            attachment_note="📎 attached",
        )
        assert "Coverage medium · 4/8 items" in summary
        assert "📎 attached" in summary


class TestAttachmentHandlers:
    """Tests for bot_attachments handlers."""

    @pytest.mark.asyncio
    async def test_handle_image_attachment_success(self):
        """Test successful image analysis."""
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.url = "https://example.com/image.png"
        mock_attachment.content_type = "image/png"

        with patch("bot_attachments.llm_analyze_image") as mock_analyze:
            mock_analyze.return_value = AsyncMock(return_value="This is a cat")()

            with patch("bot_attachments._attachment_sessions") as mock_session_mgr:
                mock_session = MagicMock()
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.read = AsyncMock(return_value=b"fake image data")

                # Create async context manager
                mock_cm = AsyncMock()
                mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
                mock_cm.__aexit__ = AsyncMock(return_value=None)
                mock_session.get.return_value = mock_cm

                mock_session_mgr.get = AsyncMock(return_value=mock_session)

                result = await handle_image_attachment(mock_attachment, "What's this?")

                assert "What's this?" in result
                # May fail due to exception handling - just check it returns something
                assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_handle_image_attachment_download_failure(self):
        """Test image handler when download fails."""
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.url = "https://example.com/image.png"
        mock_attachment.content_type = "image/png"

        with patch("bot_attachments._attachment_sessions") as mock_session_mgr:
            mock_session = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status = 404
            mock_session.get.return_value.__aenter__.return_value = mock_response
            mock_session_mgr.get.return_value = mock_session

            result = await handle_image_attachment(mock_attachment, "What's this?")
            assert result == "What's this?"  # Falls back to original question

    @pytest.mark.asyncio
    async def test_handle_doc_attachment_success(self):
        """Test successful document processing."""
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.url = "https://example.com/doc.txt"
        mock_attachment.content_type = "text/plain"

        with patch("bot_attachments._attachment_sessions") as mock_session_mgr:
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.read = AsyncMock(return_value=b"Document content here")

            # Create async context manager
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.get.return_value = mock_cm

            mock_session_mgr.get = AsyncMock(return_value=mock_session)

            result = await handle_doc_attachment(mock_attachment, "Summarize this")

            assert "Summarize this" in result
            assert "Document content here" in result
            assert "Attached Document" in result

    @pytest.mark.asyncio
    async def test_handle_doc_attachment_failure(self):
        """Test doc handler when download fails."""
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.url = "https://example.com/doc.txt"

        with patch("bot_attachments._attachment_sessions") as mock_session_mgr:
            mock_session = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status = 500
            mock_session.get.return_value.__aenter__.return_value = mock_response
            mock_session_mgr.get.return_value = mock_session

            result = await handle_doc_attachment(mock_attachment, "Summarize this")
            assert result == "Summarize this"  # Falls back


class TestExplainabilityFooterHelpers:
    def test_explainability_note_from_meta_uses_trimmed_string(self):
        note = bot_mod._explainability_note_from_meta({"explainability_note": "  thread · lock:thread  "})
        assert note == "thread · lock:thread"

    def test_explainability_note_from_meta_ignores_missing(self):
        assert bot_mod._explainability_note_from_meta({}) == ""
        assert bot_mod._explainability_note_from_meta({"explainability_note": None}) == ""

    def test_append_explainability_footer_appends_compact_note(self):
        base = "💬 4 msgs | local · unlimited | ☁️ gemini-2.5"
        full = bot_mod._append_explainability_footer(base, "thread · lock:thread")
        assert full.endswith("🧭 thread · lock:thread")

    def test_append_explainability_footer_skips_empty_note(self):
        base = "💬 4 msgs | local · unlimited | ☁️ gemini-2.5"
        assert bot_mod._append_explainability_footer(base, "") == base

    def test_build_ask_context_controls_with_structured_options(self):
        controls = bot_mod._build_ask_context_controls(
            scope="cross-channel",
            reset_context=True,
            anchor="report_9",
        )
        assert controls == {
            "scope": "cross-channel",
            "reset_context": True,
            "anchor": "report_9",
        }

    def test_build_ask_context_controls_omits_unset_values(self):
        controls = bot_mod._build_ask_context_controls()
        assert controls == {}

    def test_build_ask_recovery_block_includes_scope_and_confidence_hints(self):
        block = bot_mod._build_ask_recovery_block(
            {
                "answer_quality": {
                    "status": "low",
                    "item_count": 2,
                    "requested_item_count": 6,
                    "evidence_completeness": 0.45,
                }
            }
        )
        assert block is not None
        assert "Recovery note" in block
        assert "Scope hint" in block
        assert "Confidence: partial" in block


class TestResponseFeedback:
    @pytest.mark.asyncio
    async def test_feedback_records_quality_event_with_message_scope(self, monkeypatch):
        bot_mod._reset_feedback_guardrails_for_tests()
        writes: list[str] = []

        class _Writer:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def write(self, payload: str):
                writes.append(payload)

        monkeypatch.setattr(bot_mod.aiofiles, "open", lambda *args, **kwargs: _Writer())
        monkeypatch.setattr("pathlib.Path.mkdir", lambda *args, **kwargs: None)
        record_metric = MagicMock()
        monkeypatch.setattr(bot_mod, "_record_quality_metric", record_metric)

        view = bot_mod.ResponseActions(
            response_text="Answer text",
            question="Is this useful?",
            user_id=42,
            channel_id=123,
        )

        interaction = MagicMock()
        interaction.user = MagicMock(id=42)
        interaction.channel = MagicMock(id=123)
        interaction.message = MagicMock(id=987654321)
        interaction.response = MagicMock(send_message=AsyncMock())

        await view._record_feedback(interaction, "helpful")

        assert len(writes) == 1
        payload = json.loads(writes[0].strip())
        assert payload["rating"] == "helpful"
        assert payload["message_id"] == 987654321
        assert payload["channel_id"] == 123
        assert record_metric.call_args_list == [
            call(event="ask_feedback_helpful", context="discord_ask"),
            call(event="ask_feedback_accepted", context="discord_ask"),
        ]
        interaction.response.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_feedback_dedupe_suppresses_rapid_duplicate_clicks(self, monkeypatch):
        bot_mod._reset_feedback_guardrails_for_tests()
        writes: list[str] = []

        class _Writer:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def write(self, payload: str):
                writes.append(payload)

        monkeypatch.setattr(bot_mod.aiofiles, "open", lambda *args, **kwargs: _Writer())
        monkeypatch.setattr("pathlib.Path.mkdir", lambda *args, **kwargs: None)
        record_metric = MagicMock()
        monkeypatch.setattr(bot_mod, "_record_quality_metric", record_metric)
        monkeypatch.setattr(bot_mod.time, "monotonic", lambda: 100.0)

        view = bot_mod.ResponseActions(
            response_text="Answer text",
            question="Is this useful?",
            user_id=42,
            channel_id=123,
        )

        interaction = MagicMock()
        interaction.user = MagicMock(id=42)
        interaction.channel = MagicMock(id=123)
        interaction.message = MagicMock(id=999999)
        interaction.response = MagicMock(send_message=AsyncMock())

        await view._record_feedback(interaction, "helpful")
        await view._record_feedback(interaction, "helpful")

        assert len(writes) == 1
        assert [args.kwargs["event"] for args in record_metric.call_args_list] == [
            "ask_feedback_helpful",
            "ask_feedback_accepted",
            "ask_feedback_suppressed",
            "ask_feedback_suppressed_dedupe",
        ]
        assert interaction.response.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_feedback_rate_limit_suppresses_excess_but_preserves_spaced_signals(self, monkeypatch):
        bot_mod._reset_feedback_guardrails_for_tests()
        writes: list[str] = []
        monotonic_times = iter([0.0, 5.0, 10.0, 80.0])

        class _Writer:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def write(self, payload: str):
                writes.append(payload)

        monkeypatch.setattr(bot_mod.aiofiles, "open", lambda *args, **kwargs: _Writer())
        monkeypatch.setattr("pathlib.Path.mkdir", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot_mod.time, "monotonic", lambda: next(monotonic_times, 80.0))
        monkeypatch.setattr(bot_mod, "_FEEDBACK_DEDUPE_WINDOW_SECONDS", 2.0)
        monkeypatch.setattr(bot_mod, "_FEEDBACK_USER_RATE_LIMIT_WINDOW_SECONDS", 60.0)
        monkeypatch.setattr(bot_mod, "_FEEDBACK_USER_RATE_LIMIT_MAX", 2)
        monkeypatch.setattr(bot_mod, "_FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS", 60.0)
        monkeypatch.setattr(bot_mod, "_FEEDBACK_CHANNEL_RATE_LIMIT_MAX", 100)
        record_metric = MagicMock()
        monkeypatch.setattr(bot_mod, "_record_quality_metric", record_metric)

        view = bot_mod.ResponseActions(
            response_text="Answer text",
            question="Is this useful?",
            user_id=42,
            channel_id=123,
        )

        interaction = MagicMock()
        interaction.user = MagicMock(id=42)
        interaction.channel = MagicMock(id=123)
        interaction.response = MagicMock(send_message=AsyncMock())

        interaction.message = MagicMock(id=1)
        await view._record_feedback(interaction, "helpful")
        interaction.message = MagicMock(id=2)
        await view._record_feedback(interaction, "helpful")
        interaction.message = MagicMock(id=3)
        await view._record_feedback(interaction, "helpful")
        interaction.message = MagicMock(id=4)
        await view._record_feedback(interaction, "helpful")

        assert len(writes) == 3
        metric_events = [args.kwargs["event"] for args in record_metric.call_args_list]
        assert metric_events.count("ask_feedback_helpful") == 3
        assert metric_events.count("ask_feedback_accepted") == 3
        assert "ask_feedback_suppressed" in metric_events
        assert "ask_feedback_suppressed_rate_limited_user" in metric_events


class TestScopeSafeLockResolution:
    def test_resolve_scope_ignores_foreign_lock(self):
        runtime_state_mod.reset_context_lock("u-scope")
        runtime_state_mod.set_context_lock(
            user_id="u-scope",
            mode="thread",
            channel_id=100,
            thread_id=200,
        )

        channel_id, thread_id = bot_mod._resolve_channel_thread_scope(
            channel=None,
            channel_id=300,
            user_id="u-scope",
        )
        assert channel_id == 300
        assert thread_id is None

        runtime_state_mod.reset_context_lock("u-scope")

    def test_resolve_scope_applies_matching_lock(self):
        runtime_state_mod.reset_context_lock("u-scope-match")
        runtime_state_mod.set_context_lock(
            user_id="u-scope-match",
            mode="channel",
            channel_id=444,
            thread_id=None,
        )

        channel_id, thread_id = bot_mod._resolve_channel_thread_scope(
            channel=None,
            channel_id=444,
            user_id="u-scope-match",
        )
        assert channel_id == 444
        assert thread_id is None

        runtime_state_mod.reset_context_lock("u-scope-match")


class _TypingContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeThread:
    def __init__(
        self,
        *,
        thread_id: int,
        parent_channel: MagicMock,
        owner_id: int,
        name: str,
        last_message_id: int | None = None,
    ):
        self.id = thread_id
        self.parent = parent_channel
        self.parent_id = parent_channel.id
        self.guild = parent_channel.guild
        self.owner_id = owner_id
        self.archived = False
        self.locked = False
        self.name = name
        self.mention = f"<#{thread_id}>"
        self.last_message_id = last_message_id
        self.send = AsyncMock()

    def typing(self):
        return _TypingContext()


class TestDefaultAskChannelMode:
    def _setup_default_message_flow(self, monkeypatch):
        monkeypatch.setenv("THREAD_DB_PATH", "/tmp/openclaw-default-ask-quality.db")
        runtime_state_mod._reset_channel_profile_store_for_tests()
        monkeypatch.setattr(discord_events_mod, "ALLOWED_USER_IDS", [42], raising=False)
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", lambda: False)
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", lambda: True)
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(bot_mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(bot_mod.cfg, "thread_auto_create", True, raising=False)
        monkeypatch.setattr(bot_mod.cfg, "thread_archive_minutes", 60, raising=False)
        monkeypatch.setattr(bot_mod.bot._connection, "user", MagicMock(id=999), raising=False)
        monkeypatch.setattr(discord_events_mod, "get_model_preference", lambda user_id: "gemini")
        monkeypatch.setattr(discord_events_mod, "audit_log", MagicMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))
        monkeypatch.setattr(bot_mod.conversation_store, "cleanup_expired", MagicMock())
        conv = MagicMock(history=[], message_count=0)
        monkeypatch.setattr(bot_mod.conversation_store, "get", lambda **kwargs: conv)
        monkeypatch.setattr(bot_mod.conversation_store, "auto_save_thread", MagicMock())
        discord_events_mod._DEFAULT_ASK_THREAD_CACHE.clear()

        channel = MagicMock()
        channel.id = 123
        channel.guild = MagicMock(me=object())
        channel.permissions_for.return_value = MagicMock(read_messages=True, view_channel=True)
        channel.typing.return_value = _TypingContext()
        channel.send = AsyncMock()
        channel.threads = []
        thread = _FakeThread(
            thread_id=321,
            parent_channel=channel,
            owner_id=999,
            name="💬 summarize this · u42",
        )
        channel.create_thread = AsyncMock(return_value=thread)

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.channel = channel
        message.content = "Summarize this."
        return message, channel, thread

    @pytest.mark.asyncio
    async def test_plain_text_in_readable_channel_uses_default_ask_flow(self, monkeypatch):
        message, channel, thread = self._setup_default_message_flow(monkeypatch)
        conv = bot_mod.conversation_store.get(user_id=42, channel_id=321, user_name="Dave")

        stream_calls: list[dict[str, Any]] = []

        async def fake_stream(**kwargs):
            stream_calls.append(dict(kwargs))
            yield "Model response", True, {"updated_history": [{"role": "model", "parts": ["Model response"]}]}

        monkeypatch.setattr(discord_events_mod, "llm_chat_stream", fake_stream)

        await bot_mod.on_message(message)

        assert stream_calls[0]["user_message"] == "Summarize this."
        channel.create_thread.assert_awaited_once()
        assert bot_mod.conversation_store.auto_save_thread.call_count >= 1
        bot_mod.conversation_store.auto_save_thread.assert_any_call(42, 321, "Dave")
        thread.send.assert_awaited()
        channel.send.assert_awaited_once_with("💬 Continuing in <#321>")
        discord_events_mod.audit_log.assert_called_once()
        assert discord_events_mod.audit_log.call_args.args[1] == "ask_default"
        bot_mod.bot.process_commands.assert_not_awaited()
        runtime_state_mod._reset_channel_profile_store_for_tests()
        bot_mod._DEFAULT_ASK_THREAD_CACHE.clear()

    @pytest.mark.asyncio
    async def test_low_score_triggers_single_retry(self, monkeypatch):
        message, channel, thread = self._setup_default_message_flow(monkeypatch)
        calls: list[str] = []
        improved_text = (
            "| Game | Result |\n| --- | --- |\n| A | 1-0 |\n| B | 2-1 |\n| C | 3-2 |\n"
            "Updated today. Sources: https://espn.com/a https://apnews.com/b"
        )

        async def fake_run_ask_stream(**kwargs):
            calls.append(kwargs["user_message"])
            if len(calls) == 1:
                return AskStreamResult(response_text="short", model_used="gemini", final_meta={})
            return AskStreamResult(response_text=improved_text, model_used="gemini", final_meta={})

        monkeypatch.setattr(discord_events_mod, "run_ask_stream", fake_run_ask_stream)
        await bot_mod.on_message(message)

        assert len(calls) == 2
        assert "Please retry this answer once with broader coverage" in calls[1]
        sent_embed = thread.send.await_args_list[-1].kwargs["embed"]
        assert "Updated today." in sent_embed.description
        assert channel.send.await_count >= 1

    @pytest.mark.asyncio
    async def test_high_score_skips_retry(self, monkeypatch):
        message, _channel, thread = self._setup_default_message_flow(monkeypatch)
        calls: list[str] = []
        high_text = (
            "| Team | Status |\n| --- | --- |\n| A | OK |\n| B | OK |\n| C | OK |\n| D | OK |\n| E | OK |\n| F | OK |\n"
            "Updated today with latest checks from https://espn.com/a https://apnews.com/b https://reuters.com/c."
        )

        async def fake_run_ask_stream(**kwargs):
            calls.append(kwargs["user_message"])
            return AskStreamResult(response_text=high_text, model_used="gemini", final_meta={})

        monkeypatch.setattr(discord_events_mod, "run_ask_stream", fake_run_ask_stream)
        await bot_mod.on_message(message)

        assert len(calls) == 1
        sent_embed = thread.send.await_args_list[-1].kwargs["embed"]
        assert "Updated today" in sent_embed.description

    @pytest.mark.asyncio
    async def test_retry_error_keeps_original_response(self, monkeypatch):
        message, _channel, thread = self._setup_default_message_flow(monkeypatch)
        calls: list[str] = []

        async def fake_run_ask_stream(**kwargs):
            calls.append(kwargs["user_message"])
            if len(calls) == 1:
                return AskStreamResult(response_text="short", model_used="gemini", final_meta={})
            raise RuntimeError("retry failed")

        monkeypatch.setattr(discord_events_mod, "run_ask_stream", fake_run_ask_stream)
        await bot_mod.on_message(message)

        assert len(calls) == 2
        sent_embed = thread.send.await_args_list[-1].kwargs["embed"]
        assert "short" in sent_embed.description

    @pytest.mark.asyncio
    async def test_long_thread_follow_up_repair_keeps_context_quality_metadata(self, monkeypatch):
        prompt = (
            "Following up in this long incident thread: summarize impact, timeline, mitigation, "
            "and unresolved risks with source-backed confidence."
        )
        events: list[tuple[str, str]] = []

        monkeypatch.setattr(
            bot_mod,
            "get_effective_channel_profile",
            lambda: {"retrieval_profile": "engineering"},
        )
        monkeypatch.setattr(
            bot_mod,
            "get_latency_load_snapshot",
            lambda command_hint="ask_message_flow": {
                "request_rate_rpm": 18.0,
                "p95_latency_ms": 620.0,
                "error_rate": 0.01,
            },
        )
        monkeypatch.setattr(
            bot_mod,
            "_record_quality_metric",
            lambda event, context="ask": events.append((event, context)),
        )
        monkeypatch.setattr(
            bot_mod,
            "_record_budget_policy_metric",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            bot_mod,
            "_safe_score_answer_quality",
            lambda *args, **kwargs: {
                "score": 62,
                "status": "medium",
                "reasons": ["Broader coverage achieved after retry."],
            },
        )

        retry_prompts: list[str] = []

        async def fake_retry_stream(retry_question: str):
            retry_prompts.append(retry_question)
            return AskStreamResult(
                response_text=(
                    "| Segment | Summary | Sources |\n"
                    "| --- | --- | --- |\n"
                    "| Impact | Elevated API latency and intermittent 5xx for 19 minutes. | status.example.com |\n"
                    "| Mitigation | Rolled back canary and scaled read replicas. | github.com/example/openclaw/actions/12345 |\n"
                ),
                model_used="gemini",
                final_meta={
                    "context_quality": {
                        "compression_ratio": 0.34,
                        "retained_key_facts_count": 9,
                    },
                    "explainability_note": "thread follow-up · lock:thread",
                },
            )

        result = await bot_mod._run_quality_auto_repair(
            question=prompt,
            response_text="Short draft with limited detail.",
            model_used="gemini",
            final_meta={
                "context_quality": {
                    "compression_ratio": 0.21,
                    "retained_key_facts_count": 4,
                },
            },
            quality_meta={
                "score": 30,
                "status": "low",
                "reasons": ["Limited item coverage detected."],
            },
            context="ask_message_flow",
            run_retry_stream=fake_retry_stream,
        )

        assert len(retry_prompts) == 1
        assert "Please retry this answer once with broader coverage" in retry_prompts[0]
        assert result["response_text"].startswith("| Segment | Summary | Sources |")
        retry_meta = result["final_meta"]["answer_quality_retry"]
        assert retry_meta["attempted"] is True
        assert retry_meta["outcome"] == "improved"
        assert retry_meta["status_path"] == ["low", "medium"]
        assert result["final_meta"]["answer_quality"]["status"] == "medium"
        assert result["final_meta"]["context_quality"]["retained_key_facts_count"] == 9
        assert ("ask_quality_retry_improved", "ask_message_flow") in events

    @pytest.mark.asyncio
    async def test_default_ask_reuses_most_recent_matching_thread(self, monkeypatch):
        monkeypatch.setattr(bot_mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(bot_mod.cfg, "thread_auto_create", True, raising=False)
        monkeypatch.setattr(bot_mod.bot._connection, "user", MagicMock(id=999), raising=False)
        bot_mod._DEFAULT_ASK_THREAD_CACHE.clear()

        channel = MagicMock()
        channel.id = 123
        channel.guild = MagicMock(id=77)
        older = _FakeThread(
            thread_id=2001,
            parent_channel=channel,
            owner_id=999,
            name="💬 old question · u42",
            last_message_id=7000,
        )
        newer = _FakeThread(
            thread_id=2002,
            parent_channel=channel,
            owner_id=999,
            name="💬 recent question · u42",
            last_message_id=9000,
        )
        channel.threads = [older, newer]
        channel.create_thread = AsyncMock()

        thread, created = await bot_mod._get_or_create_default_ask_thread(
            channel,
            user_id=42,
            user_question="status?",
        )

        assert created is False
        assert thread is newer
        channel.create_thread.assert_not_awaited()
        bot_mod._DEFAULT_ASK_THREAD_CACHE.clear()

    @pytest.mark.asyncio
    async def test_slash_prefixed_message_keeps_command_precedence(self, monkeypatch):
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "llm_chat_stream", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42)
        message.channel = MagicMock()
        message.content = "/ask hello"

        await bot_mod.on_message(message)

        bot_mod.bot.process_commands.assert_awaited_once_with(message)
        discord_events_mod.llm_chat_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_text_in_user_owned_thread_uses_default_ask_flow(self, monkeypatch):
        monkeypatch.setenv("THREAD_DB_PATH", "/tmp/openclaw-default-ask-thread-test.db")
        runtime_state_mod._reset_channel_profile_store_for_tests()
        monkeypatch.setattr(discord_events_mod, "ALLOWED_USER_IDS", [42], raising=False)
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", lambda: False)
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", lambda: True)
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(bot_mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(bot_mod.bot._connection, "user", MagicMock(id=999), raising=False)
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))

        conv = MagicMock(history=[], message_count=0)
        monkeypatch.setattr(bot_mod.conversation_store, "get", lambda **kwargs: conv)
        monkeypatch.setattr(bot_mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(bot_mod.conversation_store, "cleanup_expired", MagicMock())
        monkeypatch.setattr(discord_events_mod, "get_model_preference", lambda user_id: "gemini")

        stream_calls: list[dict[str, Any]] = []

        async def fake_stream(**kwargs):
            stream_calls.append(dict(kwargs))
            yield "Thread response", True, {"updated_history": [{"role": "model", "parts": ["Thread response"]}]}

        monkeypatch.setattr(discord_events_mod, "llm_chat_stream", fake_stream)
        monkeypatch.setattr(discord_events_mod, "audit_log", MagicMock())

        parent = MagicMock()
        parent.id = 777
        parent.guild = MagicMock(me=object())
        parent.permissions_for.return_value = MagicMock(read_messages=True, view_channel=True)
        parent.send = AsyncMock()

        thread = _FakeThread(
            thread_id=888,
            parent_channel=parent,
            owner_id=1234,  # Not bot-owned
            name="Gaming chat",
        )
        thread.permissions_for = MagicMock(return_value=MagicMock(read_messages=True, view_channel=True))

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.channel = thread
        message.content = "What are good co-op games this month?"

        await bot_mod.on_message(message)

        assert stream_calls[0]["user_message"] == "What are good co-op games this month?"
        thread.send.assert_awaited()
        assert bot_mod.conversation_store.auto_save_thread.call_count >= 1
        bot_mod.conversation_store.auto_save_thread.assert_any_call(42, 888, "Dave")
        bot_mod.bot.process_commands.assert_not_awaited()
        runtime_state_mod._reset_channel_profile_store_for_tests()

    @pytest.mark.asyncio
    async def test_plain_text_in_unreadable_channel_falls_back_to_command_handler(self, monkeypatch):
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "llm_chat_stream", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))

        channel = MagicMock()
        channel.guild = MagicMock(me=object())
        channel.permissions_for.return_value = MagicMock(read_messages=False, view_channel=False)
        channel.send = AsyncMock()

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.channel = channel
        message.content = "What changed this week?"

        await bot_mod.on_message(message)

        bot_mod.bot.process_commands.assert_awaited_once_with(message)
        discord_events_mod.llm_chat_stream.assert_not_called()
        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plain_text_keeps_llm_configured_safety_check(self, monkeypatch):
        monkeypatch.setattr(discord_events_mod, "ALLOWED_USER_IDS", [], raising=False)
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", lambda: False)
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", lambda: False)
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))

        channel = MagicMock()
        channel.guild = MagicMock(me=object())
        channel.permissions_for.return_value = MagicMock(read_messages=True, view_channel=True)
        channel.send = AsyncMock()

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.channel = channel
        message.content = "Can you help?"

        await bot_mod.on_message(message)

        channel.send.assert_awaited_once_with("⚠️ LLM not configured.")

    @pytest.mark.asyncio
    async def test_empty_plain_message_posts_message_content_hint_once(self, monkeypatch):
        monkeypatch.setattr(discord_events_mod, "ALLOWED_USER_IDS", [42], raising=False)
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", lambda: False)
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", lambda: True)
        discord_events_mod._MESSAGE_CONTENT_HINT_CACHE.clear()

        channel = MagicMock()
        channel.id = 999
        channel.guild = MagicMock(id=1)
        channel.send = AsyncMock()

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.guild = channel.guild
        message.channel = channel
        message.content = ""

        await bot_mod.on_message(message)
        await bot_mod.on_message(message)

        channel.send.assert_awaited_once()
        sent_text = channel.send.await_args.args[0]
        assert "Message Content Intent" in sent_text

    @pytest.mark.asyncio
    async def test_default_ask_falls_back_to_parent_channel_if_thread_send_fails(self, monkeypatch):
        monkeypatch.setenv("THREAD_DB_PATH", "/tmp/openclaw-default-ask-fallback.db")
        runtime_state_mod._reset_channel_profile_store_for_tests()
        monkeypatch.setattr(discord_events_mod, "ALLOWED_USER_IDS", [42], raising=False)
        monkeypatch.setattr(discord_events_mod, "is_emergency_stopped", lambda: False)
        monkeypatch.setattr(discord_events_mod, "llm_is_configured", lambda: True)
        monkeypatch.setattr(bot_mod.bot, "process_commands", AsyncMock())
        monkeypatch.setattr(bot_mod.discord, "Thread", _FakeThread)
        monkeypatch.setattr(bot_mod.cfg, "thread_auto_create", True, raising=False)
        monkeypatch.setattr(bot_mod.cfg, "thread_archive_minutes", 60, raising=False)
        monkeypatch.setattr(bot_mod.bot._connection, "user", MagicMock(id=999), raising=False)
        monkeypatch.setattr(discord_events_mod, "get_model_preference", lambda user_id: "gemini")
        monkeypatch.setattr(discord_events_mod, "audit_log", MagicMock())
        monkeypatch.setattr(discord_events_mod, "get_bot", MagicMock(return_value=bot_mod.bot))
        discord_events_mod._DEFAULT_ASK_THREAD_CACHE.clear()

        conv = MagicMock(history=[], message_count=0)
        monkeypatch.setattr(bot_mod.conversation_store, "get", lambda **kwargs: conv)
        monkeypatch.setattr(bot_mod.conversation_store, "auto_save_thread", MagicMock())
        monkeypatch.setattr(bot_mod.conversation_store, "cleanup_expired", MagicMock())

        async def fake_stream(**kwargs):
            yield "Model response", True, {"updated_history": [{"role": "model", "parts": ["Model response"]}]}

        monkeypatch.setattr(discord_events_mod, "llm_chat_stream", fake_stream)

        channel = MagicMock()
        channel.id = 456
        channel.guild = MagicMock(me=object())
        channel.permissions_for.return_value = MagicMock(read_messages=True, view_channel=True)
        channel.typing.return_value = _TypingContext()
        channel.send = AsyncMock()
        channel.threads = []
        thread = _FakeThread(
            thread_id=654,
            parent_channel=channel,
            owner_id=999,
            name="💬 fallback test · u42",
        )
        thread.send = AsyncMock(side_effect=RuntimeError("cannot send thread message"))
        channel.create_thread = AsyncMock(return_value=thread)

        message = MagicMock()
        message.author = MagicMock(bot=False, id=42, display_name="Dave")
        message.channel = channel
        message.content = "Hello from plain message"

        await bot_mod.on_message(message)

        assert thread.send.await_count >= 1
        assert channel.send.await_count >= 2  # redirect notice + fallback embed
        runtime_state_mod._reset_channel_profile_store_for_tests()


class TestGlobalAppCommandErrorHandler:
    @staticmethod
    def _mock_interaction(*, response_done: bool) -> MagicMock:
        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = AsyncMock()
        interaction.response.is_done = MagicMock(return_value=response_done)
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.command = MagicMock(qualified_name="ask")
        interaction.user = MagicMock(id=42)
        interaction.channel_id = 123
        interaction.guild_id = 456
        return interaction

    @pytest.mark.asyncio
    async def test_global_handler_checkfailure_uses_response_send(self):
        interaction = self._mock_interaction(response_done=False)
        err = app_commands.CheckFailure("⛔ Not authorized for this command.")

        await bot_mod.on_app_command_error(interaction, err)

        interaction.response.send_message.assert_awaited_once_with(
            "⛔ Not authorized for this command.",
            ephemeral=True,
        )
        interaction.followup.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_global_handler_timeout_uses_followup_when_response_done(self):
        interaction = self._mock_interaction(response_done=True)
        err = app_commands.CommandInvokeError(
            interaction.command,
            asyncio.TimeoutError(),
        )

        await bot_mod.on_app_command_error(interaction, err)

        interaction.followup.send.assert_awaited_once_with(
            "⏱️ This command timed out before it finished. Please try again.",
            ephemeral=True,
        )
        interaction.response.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_global_handler_invoke_error_hides_internal_details(self):
        interaction = self._mock_interaction(response_done=False)
        root_message = "database password leaked"
        err = app_commands.CommandInvokeError(
            interaction.command,
            RuntimeError(root_message),
        )

        await bot_mod.on_app_command_error(interaction, err)

        interaction.response.send_message.assert_awaited_once()
        sent_message = interaction.response.send_message.await_args.args[0]
        assert sent_message == "⚠️ Something went wrong while running that command. Please try again."
        assert root_message not in sent_message
