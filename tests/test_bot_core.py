"""Tests for bot.py — utility functions and formatting helpers."""

import os

# Redirect filesystem side-effects before importing the module
os.environ.setdefault("LOG_DIR", "/tmp/_test_bot_logs")
os.environ.setdefault("AUDIT_DIR", "/tmp/_test_bot_audit")


import bot as mod

# ---------------------------------------------------------------------------
# truncate_for_embed
# ---------------------------------------------------------------------------


class TestTruncateForEmbed:
    def test_short_text_unchanged(self):
        assert mod.truncate_for_embed("hello", limit=100) == "hello"

    def test_exact_limit_unchanged(self):
        text = "x" * 100
        assert mod.truncate_for_embed(text, limit=100) == text

    def test_over_limit_truncated(self):
        text = "a" * 200
        result = mod.truncate_for_embed(text, limit=100)
        assert len(result) <= 100
        assert result.endswith("… (truncated)")

    def test_empty_string(self):
        assert mod.truncate_for_embed("", limit=100) == ""


# ---------------------------------------------------------------------------
# _extract_image_url
# ---------------------------------------------------------------------------


class TestExtractImageUrl:
    def test_markdown_image_link(self):
        text = "Check this ![property photo](https://example.com/pic.jpg) out"
        assert mod._extract_image_url(text) == "https://example.com/pic.jpg"

    def test_bare_image_url(self):
        text = "Here is the photo https://cdn.example.com/img.png done"
        assert mod._extract_image_url(text) == "https://cdn.example.com/img.png"

    def test_no_image(self):
        assert mod._extract_image_url("just plain text") is None

    def test_bare_url_with_query_params(self):
        text = "See https://img.host/photo.webp?w=800&h=600 for details"
        url = mod._extract_image_url(text)
        assert url is not None
        assert url.startswith("https://img.host/photo.webp")


# ---------------------------------------------------------------------------
# _format_markdown_for_discord
# ---------------------------------------------------------------------------


class TestFormatMarkdownForDiscord:
    def test_h1_becomes_bold_underline(self):
        result = mod._format_markdown_for_discord("# Title")
        assert "__**Title**__" in result

    def test_h2_becomes_bold(self):
        result = mod._format_markdown_for_discord("## Section")
        assert "**Section**" in result
        assert "__" not in result

    def test_code_block_preserved(self):
        text = "```python\n# heading\nprint('hi')\n```"
        result = mod._format_markdown_for_discord(text)
        assert "# heading" in result  # not converted inside code block

    def test_plain_text_unchanged(self):
        text = "Just regular text"
        assert mod._format_markdown_for_discord(text) == text


# ---------------------------------------------------------------------------
# _split_response
# ---------------------------------------------------------------------------


class TestSplitResponse:
    def test_short_text_single_chunk(self):
        assert mod._split_response("short") == ["short"]

    def test_long_text_split(self):
        text = ("line\n" * 2000)  # well over 3800 chars
        chunks = mod._split_response(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= mod._EMBED_LIMIT + 1  # +1 for trailing ellipsis char

    def test_empty_string(self):
        assert mod._split_response("") == [""]


# ---------------------------------------------------------------------------
# _format_tables_for_discord
# ---------------------------------------------------------------------------


class TestFormatTablesForDiscord:
    def test_simple_table_gets_ansi_block(self):
        table = "| Name | Status |\n|------|--------|\n| Sonarr | OK |"
        result = mod._format_tables_for_discord(table)
        assert "```ansi" in result
        assert "```" in result

    def test_no_table_text_unchanged(self):
        text = "No tables here"
        assert mod._format_tables_for_discord(text) == text
