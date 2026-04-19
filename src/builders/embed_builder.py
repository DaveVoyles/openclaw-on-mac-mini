"""
Fluent embed builder for consistent Discord message styling across OpenClaw.

Usage:
    embed = (
        EmbedBuilder("Sports Recap")
        .description("Today's scores")
        .color(EmbedColor.SPORTS)
        .add_field("NBA", "Lakers 110, Celtics 98")
        .footer("OpenClaw • powered by Gemini")
        .timestamp()
        .build()
    )
"""

from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional

import discord


class EmbedColor(IntEnum):
    """Brand-consistent color palette for OpenClaw embeds."""

    DEFAULT = 0x5865F2  # Discord blurple
    SUCCESS = 0x57F287  # Green
    ERROR = 0xED4245  # Red
    WARNING = 0xFEE75C  # Yellow
    INFO = 0x5865F2  # Blurple
    SPORTS = 0xFF6B35  # Orange
    FINANCE = 0x2ECC71  # Money green
    NEWS = 0x3498DB  # Blue
    AI = 0x9B59B6  # Purple


# Backward-compatible alias
class EmbedColors:
    """Deprecated: use EmbedColor instead."""

    SUCCESS = int(EmbedColor.SUCCESS)
    ERROR = int(EmbedColor.ERROR)
    WARNING = int(EmbedColor.WARNING)
    INFO = int(EmbedColor.INFO)
    NEUTRAL = 0x808080


class EmbedBuilder:
    """Fluent builder for discord.Embed with consistent OpenClaw styling."""

    _DEFAULT_FOOTER = "OpenClaw"

    def __init__(self, title: str = "", color: int = EmbedColor.DEFAULT):
        self._embed = discord.Embed(title=title, color=int(color))

    # --- Core setters ---

    def title(self, text: str) -> "EmbedBuilder":
        self._embed.title = text
        return self

    def description(self, text: str) -> "EmbedBuilder":
        self._embed.description = text
        return self

    def color(self, color: int | EmbedColor) -> "EmbedBuilder":
        self._embed.color = int(color)
        return self

    def url(self, url: str) -> "EmbedBuilder":
        self._embed.url = url
        return self

    def add_field(self, name: str, value: str, inline: bool = False) -> "EmbedBuilder":
        self._embed.add_field(name=name, value=value or "\u200b", inline=inline)
        return self

    # Backward-compatible alias
    def field(self, name: str, value: str, inline: bool = False) -> "EmbedBuilder":
        return self.add_field(name, value, inline)

    def thumbnail(self, url: str) -> "EmbedBuilder":
        self._embed.set_thumbnail(url=url)
        return self

    def image(self, url: str) -> "EmbedBuilder":
        self._embed.set_image(url=url)
        return self

    def author(self, name: str, url: Optional[str] = None, icon_url: Optional[str] = None) -> "EmbedBuilder":
        self._embed.set_author(name=name, url=url, icon_url=icon_url)
        return self

    def footer(self, text: str = "", icon_url: Optional[str] = None) -> "EmbedBuilder":
        self._embed.set_footer(text=text or self._DEFAULT_FOOTER, icon_url=icon_url)
        return self

    def timestamp(self, dt: Optional[datetime] = None) -> "EmbedBuilder":
        self._embed.timestamp = dt or datetime.now(tz=timezone.utc)
        return self

    def build(self) -> discord.Embed:
        """Return the constructed embed. Call this last."""
        return self._embed

    # --- Convenience class methods for common embed types ---

    @classmethod
    def error(cls, title: str, description: str) -> discord.Embed:
        return cls(f"❌ {title}", EmbedColor.ERROR).description(description).footer().timestamp().build()

    @classmethod
    def success(cls, title: str, description: str) -> discord.Embed:
        return cls(f"✅ {title}", EmbedColor.SUCCESS).description(description).footer().timestamp().build()

    @classmethod
    def info(cls, title: str, description: str) -> discord.Embed:
        return cls(f"ℹ️ {title}", EmbedColor.INFO).description(description).footer().timestamp().build()

    @classmethod
    def warning(cls, title: str, description: str) -> discord.Embed:
        return cls(f"⚠️ {title}", EmbedColor.WARNING).description(description).footer().timestamp().build()


# Convenience factory functions (backward-compatible)


def success_embed(title: str, message: str) -> discord.Embed:
    return EmbedBuilder.success(title, message)


def error_embed(title: str, message: str) -> discord.Embed:
    return EmbedBuilder.error(title, message)


def warning_embed(title: str, message: str) -> discord.Embed:
    return EmbedBuilder.warning(title, message)


def info_embed(title: str, message: str) -> discord.Embed:
    return EmbedBuilder.info(title, message)
