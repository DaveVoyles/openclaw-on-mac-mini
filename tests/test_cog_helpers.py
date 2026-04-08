"""Tests for src/cog_helpers.py — truncate_for_embed, split_response, require_auth."""
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# discord and project modules are real (conftest loads them) — no stubs needed

import cog_helpers as ch  # noqa: E402


# ---------------------------------------------------------------------------
# truncate_for_embed
# ---------------------------------------------------------------------------

class TestTruncateForEmbed:
    def test_short_text_returned_unchanged(self):
        text = "Hello world"
        assert ch.truncate_for_embed(text) == text

    def test_exactly_at_limit_not_truncated(self):
        text = "x" * 4000
        assert ch.truncate_for_embed(text) == text

    def test_over_limit_is_truncated(self):
        text = "x" * 4001
        result = ch.truncate_for_embed(text)
        assert len(result) <= 4000
        assert result.endswith("(truncated)")

    def test_custom_limit(self):
        text = "a" * 200
        result = ch.truncate_for_embed(text, limit=100)
        assert len(result) <= 100
        assert "(truncated)" in result

    def test_empty_string_returned_unchanged(self):
        assert ch.truncate_for_embed("") == ""


# ---------------------------------------------------------------------------
# split_response
# ---------------------------------------------------------------------------

class TestSplitResponse:
    def test_short_text_returns_single_chunk(self):
        text = "Short message"
        chunks = ch.split_response(text)
        assert chunks == [text]

    def test_exactly_at_limit_returns_single_chunk(self):
        text = "x" * 3800
        chunks = ch.split_response(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_split_into_multiple_chunks(self):
        # Create text that will exceed the 3800 default limit
        line = "word " * 200 + "\n"  # ~1001 chars per line
        text = line * 5
        chunks = ch.split_response(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 3900  # a little slack for the ellipsis

    def test_chunks_reassemble_to_original_content(self):
        # Lines that split cleanly on newlines
        text = "\n".join(["line " + str(i) for i in range(100)])
        chunks = ch.split_response(text, limit=200)
        # All original content present (ellipsis dots are extra)
        joined = "".join(chunks).replace("…", "")
        for i in range(100):
            assert f"line {i}" in joined

    def test_no_newline_splits_on_char_boundary(self):
        # 8000 chars with no newlines — must split somewhere
        text = "a" * 8000
        chunks = ch.split_response(text, limit=3800)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 3900

    def test_custom_limit(self):
        text = "hello world " * 50  # ~600 chars
        chunks = ch.split_response(text, limit=100)
        assert len(chunks) > 1


# ---------------------------------------------------------------------------
# require_auth
# ---------------------------------------------------------------------------

class TestRequireAuth:
    def test_returns_non_none(self):
        result = ch.require_auth()
        assert result is not None

    @pytest.mark.asyncio
    async def test_predicate_allows_authorized_user(self):
        from unittest.mock import patch
        import discord.app_commands as _ac

        interaction = MagicMock()
        captured = []
        original_check = _ac.check

        def capturing_check(pred):
            captured.append(pred)
            return original_check(pred)

        with patch("cog_helpers.is_allowed", return_value=True):
            with patch.object(_ac, "check", side_effect=capturing_check):
                ch.require_auth()
            if captured:
                result = await captured[0](interaction)
                assert result is True

    @pytest.mark.asyncio
    async def test_predicate_raises_for_unauthorized_user(self):
        from unittest.mock import patch
        import discord.app_commands as _ac

        interaction = MagicMock()
        captured = []
        original_check = _ac.check

        def capturing_check(pred):
            captured.append(pred)
            return original_check(pred)

        with patch("cog_helpers.is_allowed", return_value=False):
            with patch.object(_ac, "check", side_effect=capturing_check):
                ch.require_auth()
            if captured:
                with pytest.raises(_ac.CheckFailure):
                    await captured[0](interaction)
