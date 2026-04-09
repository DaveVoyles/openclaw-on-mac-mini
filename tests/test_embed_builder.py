"""Tests for EmbedBuilder and EmbedColor."""

from datetime import datetime, timezone

import discord
import pytest

from builders.embed_builder import (
    EmbedBuilder,
    EmbedColor,
    EmbedColors,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
)


class TestEmbedColor:
    def test_values_are_valid_ints(self):
        for member in EmbedColor:
            assert isinstance(int(member), int)
            assert 0 <= int(member) <= 0xFFFFFF

    def test_specific_colors(self):
        assert int(EmbedColor.ERROR) == 0xED4245
        assert int(EmbedColor.SUCCESS) == 0x57F287
        assert int(EmbedColor.WARNING) == 0xFEE75C
        assert int(EmbedColor.SPORTS) == 0xFF6B35
        assert int(EmbedColor.AI) == 0x9B59B6


class TestEmbedBuilderCore:
    def test_default_color_applied(self):
        embed = EmbedBuilder().build()
        assert embed.color.value == int(EmbedColor.DEFAULT)

    def test_custom_color_in_constructor(self):
        embed = EmbedBuilder("Title", EmbedColor.SPORTS).build()
        assert embed.color.value == int(EmbedColor.SPORTS)

    def test_description_sets_description(self):
        embed = EmbedBuilder().description("Hello world").build()
        assert embed.description == "Hello world"

    def test_add_field_appends_field(self):
        embed = EmbedBuilder().add_field("Name", "Value").build()
        assert len(embed.fields) == 1
        assert embed.fields[0].name == "Name"
        assert embed.fields[0].value == "Value"

    def test_add_field_empty_value_becomes_zwsp(self):
        embed = EmbedBuilder().add_field("Name", "").build()
        assert embed.fields[0].value == "\u200b"

    def test_add_field_multiple(self):
        embed = (EmbedBuilder()
                 .add_field("F1", "V1", inline=True)
                 .add_field("F2", "V2", inline=False)
                 .build())
        assert len(embed.fields) == 2
        assert embed.fields[0].inline is True
        assert embed.fields[1].inline is False

    def test_footer_no_args_uses_default(self):
        embed = EmbedBuilder().footer().build()
        assert embed.footer.text == "OpenClaw"

    def test_footer_custom_text(self):
        embed = EmbedBuilder().footer("Custom Footer").build()
        assert embed.footer.text == "Custom Footer"

    def test_timestamp_sets_utc_now(self):
        before = datetime.now(tz=timezone.utc)
        embed = EmbedBuilder().timestamp().build()
        after = datetime.now(tz=timezone.utc)
        assert embed.timestamp is not None
        assert before <= embed.timestamp <= after

    def test_timestamp_accepts_explicit_datetime(self):
        dt = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        embed = EmbedBuilder().timestamp(dt).build()
        assert embed.timestamp == dt

    def test_build_returns_discord_embed(self):
        result = EmbedBuilder().build()
        assert isinstance(result, discord.Embed)

    def test_color_method_accepts_int(self):
        embed = EmbedBuilder().color(0xFF0000).build()
        assert embed.color.value == 0xFF0000

    def test_color_method_accepts_embed_color(self):
        embed = EmbedBuilder().color(EmbedColor.AI).build()
        assert embed.color.value == int(EmbedColor.AI)

    def test_url_sets_url(self):
        embed = EmbedBuilder("Title").url("https://example.com").build()
        assert embed.url == "https://example.com"

    def test_thumbnail(self):
        embed = EmbedBuilder().thumbnail("https://example.com/t.png").build()
        assert embed.thumbnail.url == "https://example.com/t.png"

    def test_image(self):
        embed = EmbedBuilder().image("https://example.com/i.png").build()
        assert embed.image.url == "https://example.com/i.png"

    def test_author(self):
        embed = EmbedBuilder().author("Dave", icon_url="https://example.com/a.png").build()
        assert embed.author.name == "Dave"

    def test_title_via_constructor(self):
        embed = EmbedBuilder("My Title").build()
        assert embed.title == "My Title"

    def test_title_via_method(self):
        embed = EmbedBuilder().title("Via Method").build()
        assert embed.title == "Via Method"


class TestFluentChaining:
    """All builder methods (except build) must return self."""

    def test_description_returns_self(self):
        b = EmbedBuilder()
        assert b.description("x") is b

    def test_color_returns_self(self):
        b = EmbedBuilder()
        assert b.color(EmbedColor.ERROR) is b

    def test_url_returns_self(self):
        b = EmbedBuilder()
        assert b.url("https://x.com") is b

    def test_add_field_returns_self(self):
        b = EmbedBuilder()
        assert b.add_field("n", "v") is b

    def test_thumbnail_returns_self(self):
        b = EmbedBuilder()
        assert b.thumbnail("https://x.com/t.png") is b

    def test_image_returns_self(self):
        b = EmbedBuilder()
        assert b.image("https://x.com/i.png") is b

    def test_author_returns_self(self):
        b = EmbedBuilder()
        assert b.author("name") is b

    def test_footer_returns_self(self):
        b = EmbedBuilder()
        assert b.footer() is b

    def test_timestamp_returns_self(self):
        b = EmbedBuilder()
        assert b.timestamp() is b

    def test_build_returns_embed_not_self(self):
        b = EmbedBuilder()
        result = b.build()
        assert result is not b
        assert isinstance(result, discord.Embed)


class TestClassMethods:
    def test_error_returns_red_embed(self):
        embed = EmbedBuilder.error("Oops", "Something broke")
        assert isinstance(embed, discord.Embed)
        assert embed.color.value == int(EmbedColor.ERROR)
        assert embed.description == "Something broke"
        assert embed.footer.text == "OpenClaw"
        assert embed.timestamp is not None

    def test_success_returns_green_embed(self):
        embed = EmbedBuilder.success("Done", "All good")
        assert isinstance(embed, discord.Embed)
        assert embed.color.value == int(EmbedColor.SUCCESS)
        assert embed.description == "All good"
        assert embed.footer.text == "OpenClaw"
        assert embed.timestamp is not None

    def test_info_returns_blurple_embed(self):
        embed = EmbedBuilder.info("FYI", "Heads up")
        assert isinstance(embed, discord.Embed)
        assert embed.color.value == int(EmbedColor.INFO)
        assert embed.footer.text == "OpenClaw"

    def test_warning_returns_yellow_embed(self):
        embed = EmbedBuilder.warning("Careful", "Watch out")
        assert isinstance(embed, discord.Embed)
        assert embed.color.value == int(EmbedColor.WARNING)
        assert embed.footer.text == "OpenClaw"


class TestBackwardCompatibility:
    """Ensure old API still works for existing callers."""

    def test_embed_colors_success(self):
        assert EmbedColors.SUCCESS == int(EmbedColor.SUCCESS)

    def test_embed_colors_error(self):
        assert EmbedColors.ERROR == int(EmbedColor.ERROR)

    def test_field_alias_works(self):
        embed = EmbedBuilder().field("Name", "Value").build()
        assert len(embed.fields) == 1

    def test_success_embed_factory(self):
        embed = success_embed("Deploy", "App is live")
        assert isinstance(embed, discord.Embed)
        assert "✅" in embed.title

    def test_error_embed_factory(self):
        embed = error_embed("Failed", "Connection lost")
        assert isinstance(embed, discord.Embed)
        assert "❌" in embed.title

    def test_warning_embed_factory(self):
        embed = warning_embed("Warning", "Low disk space")
        assert isinstance(embed, discord.Embed)
        assert "⚠️" in embed.title

    def test_info_embed_factory(self):
        embed = info_embed("Info", "Version 1.0")
        assert isinstance(embed, discord.Embed)
        assert "ℹ️" in embed.title
