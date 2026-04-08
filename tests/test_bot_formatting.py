"""Tests for bot_formatting.py — Discord message formatting utilities."""

import sys
from unittest.mock import MagicMock, patch

# Stub modules before importing bot_formatting
_cwf_mock = MagicMock()
_cwf_mock.build_copy_workflow_payload = MagicMock(return_value="")
sys.modules.setdefault("copy_workflow_formatter", _cwf_mock)

_rs_mock = MagicMock()
_rs_mock.get_effective_channel_profile = MagicMock(return_value={"table_style": "discord"})
_rs_mock.record_channel_profile_signal = MagicMock()
sys.modules.setdefault("runtime_state", _rs_mock)

import bot_formatting as bf  # noqa: E402 — must come after sys.modules patching

import discord
import pytest

from constants import EMBED_DESC_LIMIT


# ---------------------------------------------------------------------------
# truncate_for_embed
# ---------------------------------------------------------------------------


def test_truncate_for_embed_short_text_unchanged():
    text = "Hello, world!"
    assert bf.truncate_for_embed(text) == text


def test_truncate_for_embed_at_limit_unchanged():
    text = "x" * EMBED_DESC_LIMIT
    assert bf.truncate_for_embed(text) == text


def test_truncate_for_embed_over_limit_truncated():
    text = "x" * (EMBED_DESC_LIMIT + 100)
    result = bf.truncate_for_embed(text)
    assert len(result) <= EMBED_DESC_LIMIT
    assert result.endswith("… (truncated)")


def test_truncate_for_embed_custom_limit():
    text = "a" * 200
    result = bf.truncate_for_embed(text, limit=50)
    assert len(result) <= 50
    assert "truncated" in result


# ---------------------------------------------------------------------------
# extract_image_url
# ---------------------------------------------------------------------------


def test_extract_image_url_markdown_image():
    text = "Here is an image: ![alt text](https://example.com/image.png)"
    assert bf.extract_image_url(text) == "https://example.com/image.png"


def test_extract_image_url_bare_png():
    text = "Check this out: https://cdn.example.com/photo.PNG"
    result = bf.extract_image_url(text)
    assert result == "https://cdn.example.com/photo.PNG"


def test_extract_image_url_bare_jpg():
    text = "Photo: https://example.com/shot.jpg"
    result = bf.extract_image_url(text)
    assert result == "https://example.com/shot.jpg"


def test_extract_image_url_prefers_markdown_over_bare():
    text = "![pic](https://example.com/a.gif) and https://example.com/b.png"
    result = bf.extract_image_url(text)
    assert result == "https://example.com/a.gif"


def test_extract_image_url_none_when_no_image():
    text = "No images here, just plain text."
    assert bf.extract_image_url(text) is None


# ---------------------------------------------------------------------------
# format_markdown_for_discord
# ---------------------------------------------------------------------------


def test_format_markdown_h1_becomes_underline_bold():
    result = bf.format_markdown_for_discord("# My Heading")
    assert result == "__**My Heading**__"


def test_format_markdown_h2_becomes_bold():
    result = bf.format_markdown_for_discord("## Section")
    assert result == "**Section**"


def test_format_markdown_h3_becomes_bold():
    result = bf.format_markdown_for_discord("### Subsection")
    assert result == "**Subsection**"


def test_format_markdown_code_block_passes_through():
    code = "```python\nprint('hello')\n```"
    result = bf.format_markdown_for_discord(code)
    assert "```python" in result
    assert "print('hello')" in result


def test_format_markdown_heading_inside_code_block_unchanged():
    text = "```\n# not a heading\n```"
    result = bf.format_markdown_for_discord(text)
    assert "# not a heading" in result
    assert "__**" not in result


def test_format_markdown_plain_text_unchanged():
    text = "Just some regular text."
    assert bf.format_markdown_for_discord(text) == text


# ---------------------------------------------------------------------------
# format_tables
# ---------------------------------------------------------------------------


_SIMPLE_TABLE = (
    "| Name | Age |\n"
    "| ---- | --- |\n"
    "| Alice | 30 |\n"
    "| Bob | 25 |"
)


def test_format_tables_discord_mode_renders_code_block():
    result = bf.format_tables(_SIMPLE_TABLE, mode="discord")
    assert "```" in result
    assert "Alice" in result


def test_format_tables_copy_safe_mode_renders_bullets():
    result = bf.format_tables(_SIMPLE_TABLE, mode="copy-safe")
    assert "📋 Table" in result
    assert "Alice" in result
    assert "```" not in result


def test_format_tables_for_discord_alias():
    result = bf.format_tables_for_discord(_SIMPLE_TABLE)
    assert "```" in result


def test_format_tables_for_copy_alias():
    result = bf.format_tables_for_copy(_SIMPLE_TABLE)
    assert "📋 Table" in result


def test_format_tables_no_table_unchanged():
    text = "Just a regular paragraph.\nNo tables here."
    result = bf.format_tables(text)
    assert result == text


# ---------------------------------------------------------------------------
# split_response
# ---------------------------------------------------------------------------


def test_split_response_short_text_returns_single_chunk():
    text = "Short text."
    chunks = bf.split_response(text)
    assert chunks == [text]


def test_split_response_long_text_splits_into_multiple_chunks():
    text = ("word " * 1000).strip()
    chunks = bf.split_response(text, limit=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 200


def test_split_response_code_block_preserved():
    code = "```python\n" + "x = 1\n" * 5 + "```"
    short_text = "Before.\n" + code + "\nAfter."
    chunks = bf.split_response(short_text, limit=10000)
    combined = "".join(chunks)
    assert "```python" in combined
    assert "x = 1" in combined


def test_split_response_empty_string():
    chunks = bf.split_response("")
    assert chunks == [""]


# ---------------------------------------------------------------------------
# is_dense_recap_or_list
# ---------------------------------------------------------------------------


def test_is_dense_recap_12_list_items():
    lines = "\n".join(f"- item {i}" for i in range(12))
    assert bf.is_dense_recap_or_list(lines) is True


def test_is_dense_recap_keyword_with_6_bullets():
    text = "Summary of today's recap:\n" + "\n".join(f"- point {i}" for i in range(6))
    assert bf.is_dense_recap_or_list(text) is True


def test_is_dense_recap_short_text_not_dense():
    text = "Just three items:\n- one\n- two\n- three"
    assert bf.is_dense_recap_or_list(text) is False


def test_is_dense_recap_empty_text():
    assert bf.is_dense_recap_or_list("") is False


def test_is_dense_recap_numbered_list():
    lines = "\n".join(f"{i}. item" for i in range(1, 13))
    assert bf.is_dense_recap_or_list(lines) is True


# ---------------------------------------------------------------------------
# should_package_as_attachment
# ---------------------------------------------------------------------------


def test_should_package_as_attachment_threshold_met():
    chunks = ["chunk1", "chunk2"]  # >= PACKAGE_CHUNK_THRESHOLD (2)
    assert bf.should_package_as_attachment("text", chunks) is True


def test_should_package_as_attachment_below_threshold_not_dense():
    chunks = ["only one chunk"]
    text = "Short and sparse."
    assert bf.should_package_as_attachment(text, chunks) is False


def test_should_package_as_attachment_dense_recap():
    text = "Headlines from today:\n" + "\n".join(f"- story {i}" for i in range(8))
    assert bf.should_package_as_attachment(text, ["single_chunk"]) is True


# ---------------------------------------------------------------------------
# build_attachment_embed_summary
# ---------------------------------------------------------------------------


def test_build_attachment_embed_summary_has_attachment_note():
    result = bf.build_attachment_embed_summary("Some content")
    assert "📎" in result


def test_build_attachment_embed_summary_includes_coverage_summary():
    result = bf.build_attachment_embed_summary("Content", coverage_summary="50 items")
    assert "📊 50 items" in result
    assert "Content" in result


def test_build_attachment_embed_summary_truncates_long_text():
    long_text = "x" * 2000
    result = bf.build_attachment_embed_summary(long_text)
    assert len(result) < 2000 + 200  # summary_limit=500 + overhead


def test_build_attachment_embed_summary_custom_note():
    result = bf.build_attachment_embed_summary("hi", attachment_note="📎 Custom note")
    assert "📎 Custom note" in result


# ---------------------------------------------------------------------------
# extract_file_attachment
# ---------------------------------------------------------------------------


def test_extract_file_attachment_no_code_block():
    assert bf.extract_file_attachment("No code here.") is None


def test_extract_file_attachment_short_code_returns_none():
    text = "```python\nprint('hi')\n```"
    assert bf.extract_file_attachment(text) is None


def test_extract_file_attachment_long_code_returns_file():
    code_body = "x = 1\n" * 100  # 600+ chars
    text = f"```python\n{code_body}```"
    result = bf.extract_file_attachment(text)
    assert result is not None
    file_obj, lang = result
    assert isinstance(file_obj, discord.File)
    assert lang == "python"
    assert file_obj.filename.endswith(".py")


def test_extract_file_attachment_unknown_lang_defaults_to_txt():
    code_body = "data\n" * 200
    text = f"```unknownlang\n{code_body}```"
    result = bf.extract_file_attachment(text)
    assert result is not None
    file_obj, lang = result
    assert file_obj.filename.endswith(".txt")
