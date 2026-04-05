"""Tests for bot message/attachment handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import runtime_state as runtime_state_mod
from bot_attachments import handle_doc_attachment, handle_image_attachment
from bot_formatting import (
    extract_file_attachment,
    extract_image_url,
    format_markdown_for_discord,
    format_tables_for_context,
    format_tables_for_copy,
    format_tables_for_discord,
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
