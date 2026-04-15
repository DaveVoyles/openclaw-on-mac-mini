"""Unit tests for openclaw_cli_render.py — pure rendering helpers."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import openclaw_cli_render as mod  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**kwargs) -> mod.RenderContext:
    """Build a minimal RenderContext for testing."""
    defaults = dict(
        is_tty=False,
        is_rich=False,
        high_contrast=False,
        plain_mode=False,
        cols=80,
        theme_ansi="",
    )
    defaults.update(kwargs)
    return mod.RenderContext(**defaults)


# ---------------------------------------------------------------------------
# _apply_inline_ansi
# ---------------------------------------------------------------------------

def test_apply_inline_ansi_bold():
    result = mod._apply_inline_ansi("**hello**")
    assert "hello" in result


def test_apply_inline_ansi_italic():
    result = mod._apply_inline_ansi("*world*")
    assert "world" in result


def test_apply_inline_ansi_code():
    result = mod._apply_inline_ansi("`code`")
    assert "code" in result


def test_apply_inline_ansi_passthrough():
    plain = "no markdown here"
    assert plain in mod._apply_inline_ansi(plain)


def test_apply_inline_ansi_double_underscore_bold():
    result = mod._apply_inline_ansi("__bold__")
    assert "bold" in result


# ---------------------------------------------------------------------------
# _strip_inline_md
# ---------------------------------------------------------------------------

def test_strip_inline_md_bold():
    assert mod._strip_inline_md("**hello**") == "hello"


def test_strip_inline_md_italic():
    assert mod._strip_inline_md("*world*") == "world"


def test_strip_inline_md_code():
    assert mod._strip_inline_md("`code`") == "code"


def test_strip_inline_md_plain():
    assert mod._strip_inline_md("plain") == "plain"


def test_strip_inline_md_mixed():
    result = mod._strip_inline_md("**Key**: `value`")
    assert "**" not in result
    assert "`" not in result


# ---------------------------------------------------------------------------
# _separator_fill
# ---------------------------------------------------------------------------

def test_separator_fill_default():
    s = mod._separator_fill(10)
    assert len(s) == 10
    assert s == "─" * 10


def test_separator_fill_high_contrast():
    s = mod._separator_fill(10, high_contrast=True)
    assert s == "=" * 10


def test_separator_fill_plain_mode():
    s = mod._separator_fill(10, plain_mode=True)
    assert s == "=" * 10


def test_separator_fill_zero_width():
    s = mod._separator_fill(0)
    assert len(s) >= 1  # max(1, 0) == 1


def test_separator_fill_large():
    s = mod._separator_fill(100)
    assert len(s) == 100


# ---------------------------------------------------------------------------
# _response_footer_lines
# ---------------------------------------------------------------------------

def test_response_footer_with_elapsed():
    headline, detail = mod._response_footer_lines(elapsed=1.5)
    assert "1.5s" in headline
    assert "1.5s" in detail


def test_response_footer_with_tokens():
    headline, detail = mod._response_footer_lines(tokens=500)
    assert "500 tokens" in detail


def test_response_footer_with_model():
    headline, detail = mod._response_footer_lines(model="gpt-4")
    assert "gpt-4" in detail


def test_response_footer_no_elapsed():
    headline, detail = mod._response_footer_lines()
    assert "complete" in headline


def test_response_footer_all_fields():
    headline, detail = mod._response_footer_lines(elapsed=2.0, tokens=100, model="claude")
    assert "2.0s" in headline
    assert "100 tokens" in detail
    assert "claude" in detail


# ---------------------------------------------------------------------------
# _is_kv_bullet_group
# ---------------------------------------------------------------------------

def test_is_kv_bullet_group_valid():
    lines = [
        "- Name: Alice | Age: 30",
        "- Name: Bob | Age: 25",
    ]
    assert mod._is_kv_bullet_group(lines) is True


def test_is_kv_bullet_group_insufficient_colons():
    lines = [
        "- Alice Bob Charlie",
    ]
    assert mod._is_kv_bullet_group(lines) is False


def test_is_kv_bullet_group_with_bold_kv():
    lines = [
        "- **Key:** Value | **Other:** Data",
    ]
    assert mod._is_kv_bullet_group(lines) is True


def test_is_kv_bullet_group_no_pipe():
    lines = [
        "- just text here",
    ]
    assert mod._is_kv_bullet_group(lines) is False


# ---------------------------------------------------------------------------
# _parse_md_table
# ---------------------------------------------------------------------------

def test_parse_md_table_valid():
    block = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = mod._parse_md_table(block)
    assert result is not None
    headers, rows = result
    assert "A" in headers
    assert "B" in headers
    assert len(rows) == 1


def test_parse_md_table_invalid_separator():
    block = "| A | B |\nno sep\n| 1 | 2 |"
    result = mod._parse_md_table(block)
    assert result is None


def test_parse_md_table_too_few_lines():
    block = "| A | B |"
    result = mod._parse_md_table(block)
    assert result is None


def test_parse_md_table_strips_inline_md():
    block = "| **Header** | `Col` |\n|---|---|\n| val | x |"
    result = mod._parse_md_table(block)
    assert result is not None
    headers, rows = result
    assert "Header" in headers
    assert "Col" in headers


def test_parse_md_table_multiple_rows():
    block = "| X |\n|---|\n| a |\n| b |\n| c |"
    result = mod._parse_md_table(block)
    assert result is not None
    _, rows = result
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# _preprocess_response_text
# ---------------------------------------------------------------------------

def test_preprocess_strips_via_trailer():
    text = "Hello world\n_via gpt-4_"
    body, sources = mod._preprocess_response_text(text)
    assert "_via" not in body


def test_preprocess_extracts_sources():
    text = "Answer here\n\nSources:\n- https://example.com\n"
    body, sources = mod._preprocess_response_text(text)
    assert sources is not None
    assert "example.com" in sources


def test_preprocess_strips_citation_markers():
    text = "See reference [1] and also [2]."
    body, _ = mod._preprocess_response_text(text)
    assert "[1]" not in body
    assert "[2]" not in body


def test_preprocess_no_sources():
    text = "Simple response with no sources."
    body, sources = mod._preprocess_response_text(text)
    assert sources is None
    assert "Simple response" in body


def test_preprocess_strips_recovery_note():
    text = "Real content\n\n> ℹ️ **Recovery note:**\n> Some note here\n"
    body, _ = mod._preprocess_response_text(text)
    assert "Recovery note" not in body


# ---------------------------------------------------------------------------
# _clean_sources_for_display
# ---------------------------------------------------------------------------

def test_clean_sources_markdown_link():
    sources = "Sources:\n- [Example](https://example.com)"
    result = mod._clean_sources_for_display(sources)
    assert any("example.com" in url for _, url in result)


def test_clean_sources_bare_url():
    sources = "- https://openai.com"
    result = mod._clean_sources_for_display(sources)
    assert any("openai.com" in url for _, url in result)


def test_clean_sources_deduplicates():
    sources = "- https://example.com\n- https://example.com"
    result = mod._clean_sources_for_display(sources)
    urls = [url for _, url in result]
    assert urls.count("https://example.com") == 1


def test_clean_sources_empty():
    result = mod._clean_sources_for_display("")
    assert result == []


def test_clean_sources_numbered_prefix():
    sources = "1. https://numbered.org"
    result = mod._clean_sources_for_display(sources)
    assert any("numbered.org" in url for _, url in result)


# ---------------------------------------------------------------------------
# _bullet_group_to_table
# ---------------------------------------------------------------------------

def test_bullet_group_to_table_basic():
    lines = [
        "- Name: Alice | Age: 30",
        "- Name: Bob | Age: 25",
    ]
    table = mod._bullet_group_to_table(lines)
    assert any("|" in line for line in table)
    # Header row and separator should be present
    assert len(table) >= 3
