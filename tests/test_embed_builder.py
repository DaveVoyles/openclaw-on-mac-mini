"""Tests for EmbedBuilder."""

import pytest
import discord
from builders.embed_builder import (
    EmbedBuilder,
    EmbedColors,
    error_embed,
    info_embed,
    success_embed,
    warning_embed,
)


class TestEmbedBuilder:
    def test_basic_embed(self):
        embed = EmbedBuilder().title("Test").description("Description").build()
        assert isinstance(embed, discord.Embed)
        assert embed.title == "Test"
        assert embed.description == "Description"
    
    def test_chaining(self):
        embed = (EmbedBuilder()
            .title("Title")
            .description("Desc")
            .color(EmbedColors.INFO)
            .build())
        
        assert embed.title == "Title"
        assert embed.description == "Desc"
        assert embed.color.value == EmbedColors.INFO
    
    def test_fields(self):
        embed = (EmbedBuilder()
            .title("Test")
            .field("Name", "Value", inline=False)
            .field("Name2", "Value2", inline=True)
            .build())
        
        assert len(embed.fields) == 2
        assert embed.fields[0].name == "Name"
        assert embed.fields[0].value == "Value"
        assert not embed.fields[0].inline
        assert embed.fields[1].inline
    
    def test_author(self):
        embed = (EmbedBuilder()
            .title("Test")
            .author("Author Name", url="https://example.com")
            .build())
        
        assert embed.author.name == "Author Name"
        assert embed.author.url == "https://example.com"
    
    def test_footer(self):
        embed = (EmbedBuilder()
            .title("Test")
            .footer("Footer text")
            .build())
        
        assert embed.footer.text == "Footer text"
    
    def test_thumbnail(self):
        embed = (EmbedBuilder()
            .title("Test")
            .thumbnail("https://example.com/thumb.png")
            .build())
        
        assert embed.thumbnail.url == "https://example.com/thumb.png"
    
    def test_image(self):
        embed = (EmbedBuilder()
            .title("Test")
            .image("https://example.com/image.png")
            .build())
        
        assert embed.image.url == "https://example.com/image.png"
    
    def test_url(self):
        embed = (EmbedBuilder()
            .title("Test")
            .url("https://example.com")
            .build())
        
        assert embed.url == "https://example.com"
    
    def test_timestamp(self):
        from datetime import datetime
        dt = datetime(2024, 1, 1, 12, 0, 0)
        embed = EmbedBuilder().title("Test").timestamp(dt).build()
        
        # Just verify timestamp is set (don't compare exact datetime due to timezone conversion)
        assert embed.timestamp is not None
        assert embed.timestamp.year == 2024


class TestConvenienceMethods:
    def test_success(self):
        embed = EmbedBuilder().success("Success", "It worked!").build()
        
        assert "✅" in embed.title
        assert "Success" in embed.title
        assert embed.description == "It worked!"
        assert embed.color.value == EmbedColors.SUCCESS
    
    def test_error(self):
        embed = EmbedBuilder().error("Error", "It failed!").build()
        
        assert "❌" in embed.title
        assert "Error" in embed.title
        assert embed.description == "It failed!"
        assert embed.color.value == EmbedColors.ERROR
    
    def test_warning(self):
        embed = EmbedBuilder().warning("Warning", "Be careful!").build()
        
        assert "⚠️" in embed.title
        assert "Warning" in embed.title
        assert embed.description == "Be careful!"
        assert embed.color.value == EmbedColors.WARNING
    
    def test_info(self):
        embed = EmbedBuilder().info("Info", "FYI").build()
        
        assert "ℹ️" in embed.title
        assert "Info" in embed.title
        assert embed.description == "FYI"
        assert embed.color.value == EmbedColors.INFO


class TestFactoryFunctions:
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


class TestComplexEmbed:
    def test_fully_configured_embed(self):
        embed = (EmbedBuilder()
            .title("Complex Embed")
            .description("With all features")
            .color(EmbedColors.INFO)
            .url("https://example.com")
            .author("Author", url="https://author.com")
            .field("Field 1", "Value 1", inline=True)
            .field("Field 2", "Value 2", inline=True)
            .footer("Footer text")
            .thumbnail("https://example.com/thumb.png")
            .build())
        
        assert embed.title == "Complex Embed"
        assert embed.description == "With all features"
        assert embed.url == "https://example.com"
        assert embed.author.name == "Author"
        assert len(embed.fields) == 2
        assert embed.footer.text == "Footer text"
        assert embed.thumbnail.url == "https://example.com/thumb.png"
