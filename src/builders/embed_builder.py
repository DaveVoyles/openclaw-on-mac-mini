"""Builder pattern for Discord embeds."""

import discord
from datetime import datetime
from typing import Optional


class EmbedColors:
    """Standard colors for Discord embeds."""
    SUCCESS = 0x00FF00  # Green
    ERROR = 0xFF0000    # Red
    WARNING = 0xFFA500  # Orange
    INFO = 0x0099FF     # Blue
    NEUTRAL = 0x808080  # Gray


class EmbedBuilder:
    """
    Fluent builder for Discord embeds.
    
    Provides a clean, chainable API for creating embeds without
    dealing with discord.Embed directly.
    
    Examples:
        >>> embed = (EmbedBuilder()
        ...     .title("Weather Report")
        ...     .description("Current conditions")
        ...     .color(EmbedColors.INFO)
        ...     .field("Temperature", "72°F")
        ...     .field("Conditions", "Sunny")
        ...     .timestamp()
        ...     .build())
        
        >>> error_embed = (EmbedBuilder()
        ...     .error("API Failed", "Connection timeout")
        ...     .build())
    """
    
    def __init__(self):
        self._embed = discord.Embed()
    
    def title(self, text: str) -> 'EmbedBuilder':
        """Set the embed title."""
        self._embed.title = text
        return self
    
    def description(self, text: str) -> 'EmbedBuilder':
        """Set the embed description."""
        self._embed.description = text
        return self
    
    def color(self, color: int) -> 'EmbedBuilder':
        """Set the embed color."""
        self._embed.color = color
        return self
    
    def url(self, url: str) -> 'EmbedBuilder':
        """Set the embed URL (makes title clickable)."""
        self._embed.url = url
        return self
    
    def timestamp(self, dt: Optional[datetime] = None) -> 'EmbedBuilder':
        """Set the embed timestamp (defaults to now)."""
        self._embed.timestamp = dt or datetime.now()
        return self
    
    def field(
        self,
        name: str,
        value: str,
        inline: bool = False,
    ) -> 'EmbedBuilder':
        """Add a field to the embed."""
        self._embed.add_field(name=name, value=value, inline=inline)
        return self
    
    def author(
        self,
        name: str,
        url: Optional[str] = None,
        icon_url: Optional[str] = None,
    ) -> 'EmbedBuilder':
        """Set the embed author."""
        self._embed.set_author(name=name, url=url, icon_url=icon_url)
        return self
    
    def footer(
        self,
        text: str,
        icon_url: Optional[str] = None,
    ) -> 'EmbedBuilder':
        """Set the embed footer."""
        self._embed.set_footer(text=text, icon_url=icon_url)
        return self
    
    def thumbnail(self, url: str) -> 'EmbedBuilder':
        """Set the embed thumbnail (small image in top-right)."""
        self._embed.set_thumbnail(url=url)
        return self
    
    def image(self, url: str) -> 'EmbedBuilder':
        """Set the embed image (large image at bottom)."""
        self._embed.set_image(url=url)
        return self
    
    # Convenience methods for common patterns
    
    def success(self, title: str, message: str) -> 'EmbedBuilder':
        """Configure as a success embed."""
        self._embed.title = f"✅ {title}"
        self._embed.description = message
        self._embed.color = EmbedColors.SUCCESS
        return self
    
    def error(self, title: str, message: str) -> 'EmbedBuilder':
        """Configure as an error embed."""
        self._embed.title = f"❌ {title}"
        self._embed.description = message
        self._embed.color = EmbedColors.ERROR
        return self
    
    def warning(self, title: str, message: str) -> 'EmbedBuilder':
        """Configure as a warning embed."""
        self._embed.title = f"⚠️ {title}"
        self._embed.description = message
        self._embed.color = EmbedColors.WARNING
        return self
    
    def info(self, title: str, message: str) -> 'EmbedBuilder':
        """Configure as an info embed."""
        self._embed.title = f"ℹ️ {title}"
        self._embed.description = message
        self._embed.color = EmbedColors.INFO
        return self
    
    def build(self) -> discord.Embed:
        """Build and return the Discord embed."""
        return self._embed


# Convenience factory functions

def success_embed(title: str, message: str) -> discord.Embed:
    """
    Create a success embed (green with checkmark).
    
    Args:
        title: Embed title
        message: Embed description
    
    Returns:
        Configured success embed
    """
    return EmbedBuilder().success(title, message).build()


def error_embed(title: str, message: str) -> discord.Embed:
    """
    Create an error embed (red with X).
    
    Args:
        title: Error title
        message: Error description
    
    Returns:
        Configured error embed
    """
    return EmbedBuilder().error(title, message).build()


def warning_embed(title: str, message: str) -> discord.Embed:
    """
    Create a warning embed (orange with warning sign).
    
    Args:
        title: Warning title
        message: Warning description
    
    Returns:
        Configured warning embed
    """
    return EmbedBuilder().warning(title, message).build()


def info_embed(title: str, message: str) -> discord.Embed:
    """
    Create an info embed (blue with info icon).
    
    Args:
        title: Info title
        message: Info description
    
    Returns:
        Configured info embed
    """
    return EmbedBuilder().info(title, message).build()
