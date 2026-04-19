"""Tests for copy_workflow_formatter.py — markdown stripping and payload building."""

from __future__ import annotations

from copy_workflow_formatter import build_copy_workflow_payload, strip_discord_markdown_noise

# ===========================================================================
# strip_discord_markdown_noise
# ===========================================================================


class TestStripDiscordMarkdownNoise:
    def test_copy_workflow_formatter_empty_returns_empty(self):
        assert strip_discord_markdown_noise("") == ""

    def test_copy_workflow_formatter_none_returns_empty(self):
        assert strip_discord_markdown_noise(None) == ""  # type: ignore

    def test_strips_bold(self):
        assert strip_discord_markdown_noise("**hello**") == "hello"

    def test_strips_italic_star(self):
        assert strip_discord_markdown_noise("*italic*") == "italic"

    def test_strips_italic_underscore(self):
        assert strip_discord_markdown_noise("_italic_") == "italic"

    def test_strips_underline(self):
        assert strip_discord_markdown_noise("__underline__") == "underline"

    def test_strips_strikethrough(self):
        assert strip_discord_markdown_noise("~~strike~~") == "strike"

    def test_strips_fenced_code_block(self):
        result = strip_discord_markdown_noise("```python\nprint('hi')\n```")
        assert "```" not in result
        assert "print" in result

    def test_strips_inline_code(self):
        result = strip_discord_markdown_noise("`code`")
        assert "`" not in result
        assert "code" in result

    def test_replaces_masked_link(self):
        result = strip_discord_markdown_noise("[click here](https://example.com)")
        assert "click here" in result
        assert "https://example.com" not in result

    def test_replaces_user_mention(self):
        result = strip_discord_markdown_noise("<@123456>")
        assert "@user" in result
        assert "123456" not in result

    def test_replaces_role_mention(self):
        result = strip_discord_markdown_noise("<@&789>")
        assert "@role" in result

    def test_replaces_channel_mention(self):
        result = strip_discord_markdown_noise("<#456>")
        assert "#channel" in result

    def test_strips_spoiler_markers(self):
        result = strip_discord_markdown_noise("||secret||")
        assert "||" not in result
        assert "secret" in result

    def test_replaces_custom_emoji(self):
        result = strip_discord_markdown_noise("<:thumbsup:12345>")
        assert ":thumbsup:" in result
        assert "12345" not in result

    def test_strips_blockquote_prefix(self):
        result = strip_discord_markdown_noise("> quoted text")
        assert ">" not in result
        assert "quoted text" in result

    def test_collapses_excess_blank_lines(self):
        result = strip_discord_markdown_noise("a\n\n\n\nb")
        assert "\n\n\n" not in result

    def test_copy_workflow_formatter_plain_text_unchanged(self):
        text = "This is plain text."
        assert strip_discord_markdown_noise(text) == text

    def test_crlf_normalized(self):
        result = strip_discord_markdown_noise("line1\r\nline2")
        assert "\r" not in result


# ===========================================================================
# build_copy_workflow_payload
# ===========================================================================


class TestBuildCopyWorkflowPayload:
    def test_copy_workflow_formatter_empty_returns_empty_v2(self):
        assert build_copy_workflow_payload("") == ""

    def test_single_line_returned_as_summary(self):
        result = build_copy_workflow_payload("Hello world")
        assert "Hello world" in result

    def test_bullets_added_for_multiline(self):
        text = "Summary line\n- Point A\n- Point B"
        result = build_copy_workflow_payload(text)
        assert "•" in result

    def test_bullet_limit_respected(self):
        items = "\n".join([f"- Item {i}" for i in range(20)])
        result = build_copy_workflow_payload(f"Header\n{items}", bullet_limit=3)
        assert result.count("•") <= 3

    def test_deduplication_applied(self):
        text = "Summary\n- Same item\n- Same item\n- Same item"
        result = build_copy_workflow_payload(text)
        assert result.count("Same item") == 1

    def test_max_output_length_enforced(self):
        long_text = "A " * 1000
        result = build_copy_workflow_payload(long_text)
        assert len(result) <= 1200

    def test_summary_capped_at_220_chars(self):
        long_first_line = "X" * 300
        result = build_copy_workflow_payload(long_first_line)
        first_line = result.splitlines()[0]
        assert len(first_line) <= 220

    def test_sentence_split_fallback(self):
        text = "First sentence. Second sentence. Third sentence."
        result = build_copy_workflow_payload(text)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_bold_stripped_from_bullets(self):
        text = "Summary\n- **Bold item**"
        result = build_copy_workflow_payload(text)
        assert "**" not in result

    def test_code_blocks_stripped(self):
        text = "Summary\n```python\nprint('hi')\n```"
        result = build_copy_workflow_payload(text)
        assert "```" not in result
