"""Unit tests for discord_error module."""
import pytest
from unittest.mock import MagicMock, patch
import types


def _make_discord_stub():
    """Build a minimal discord stub for discord_error tests."""
    stub = types.SimpleNamespace()

    # Embed mock that tracks fields and footer
    class FakeEmbed:
        def __init__(self, title="", description="", color=None):
            self.title = title
            self.description = description
            self.color = color
            self.fields = []
            self.footer = None

        def add_field(self, *, name, value, inline=True):
            field = types.SimpleNamespace(name=name, value=value, inline=inline)
            self.fields.append(field)

        def set_footer(self, *, text):
            self.footer = types.SimpleNamespace(text=text)

    stub.Embed = FakeEmbed
    stub.Color = MagicMock()
    stub.Color.red = MagicMock(return_value=0xFF0000)
    stub.utils = MagicMock()
    stub.utils.escape_markdown = lambda s: s
    return stub


@pytest.fixture(autouse=True)
def patch_discord(monkeypatch):
    import discord_error
    monkeypatch.setattr(discord_error, "discord", _make_discord_stub())


from discord_error import build_error_embed, classify_error, ERROR_CATEGORIES


def test_classify_error_timeout():
    assert classify_error(TimeoutError("request timed out")) == "timeout"


def test_classify_error_rate_limit():
    assert classify_error(Exception("rate limit exceeded")) == "rate_limit"


def test_classify_error_auth():
    assert classify_error(Exception("forbidden")) == "auth"


def test_classify_error_general():
    assert classify_error(Exception("something random")) == "general"


def test_build_error_embed_has_title():
    embed = build_error_embed(Exception("test error"))
    assert embed.title is not None
    assert "Error" in embed.title


def test_build_error_embed_has_trace_id():
    embed = build_error_embed(Exception("test"))
    footer_text = embed.footer.text if embed.footer else ""
    assert "Trace ID" in footer_text


def test_build_error_embed_with_context():
    embed = build_error_embed(Exception("test"), context="/mycommand")
    field_names = [f.name for f in embed.fields]
    assert "Command" in field_names


def test_build_error_embed_truncates_long_detail():
    long_error = Exception("x" * 500)
    embed = build_error_embed(long_error)
    detail_field = next((f for f in embed.fields if f.name == "Detail"), None)
    assert detail_field is not None
    assert len(detail_field.value) <= 210  # 200 chars + backticks


def test_all_categories_have_emoji_and_desc():
    for key, (emoji, desc) in ERROR_CATEGORIES.items():
        assert emoji
        assert desc

