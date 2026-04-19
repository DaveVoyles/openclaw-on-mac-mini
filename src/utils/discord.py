"""Discord utility functions for OpenClaw."""

import discord


class EmbedColors:
    """Standard colors for Discord embeds."""

    SUCCESS = 0x00FF00  # Green
    ERROR = 0xFF0000  # Red
    WARNING = 0xFFA500  # Orange
    INFO = 0x0099FF  # Blue
    NEUTRAL = 0x808080  # Gray


def create_embed(
    title: str,
    description: str | None = None,
    color: int = EmbedColors.INFO,
    fields: list[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    """
    Create a standardized Discord embed.

    Args:
        title: Embed title
        description: Optional embed description
        color: Embed color (default: INFO blue)
        fields: Optional list of (name, value, inline) tuples

    Returns:
        Configured Discord embed

    Examples:
        >>> embed = create_embed("Success", "Operation completed", EmbedColors.SUCCESS)
        >>> embed = create_embed(
        ...     "User Info",
        ...     fields=[("Name", "Alice", True), ("Age", "30", True)]
        ... )
    """
    embed = discord.Embed(title=title, color=color)

    if description:
        embed.description = description

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    return embed


def create_error_embed(title: str, error: str | Exception) -> discord.Embed:
    """
    Create an error embed with standard formatting.

    Args:
        title: Error title
        error: Error message or exception

    Returns:
        Error-formatted Discord embed

    Examples:
        >>> embed = create_error_embed("API Failed", "Connection timeout")
        >>> embed = create_error_embed("Invalid Input", ValueError("Bad value"))
    """
    error_msg = str(error)
    return create_embed(
        title=f"❌ {title}",
        description=error_msg,
        color=EmbedColors.ERROR,
    )


def create_success_embed(title: str, message: str) -> discord.Embed:
    """
    Create a success embed with standard formatting.

    Args:
        title: Success title
        message: Success message

    Returns:
        Success-formatted Discord embed

    Examples:
        >>> embed = create_success_embed("Deployed", "App is now live")
    """
    return create_embed(
        title=f"✅ {title}",
        description=message,
        color=EmbedColors.SUCCESS,
    )


def create_warning_embed(title: str, message: str) -> discord.Embed:
    """
    Create a warning embed with standard formatting.

    Args:
        title: Warning title
        message: Warning message

    Returns:
        Warning-formatted Discord embed

    Examples:
        >>> embed = create_warning_embed("Rate Limited", "Wait 60s before retry")
    """
    return create_embed(
        title=f"⚠️ {title}",
        description=message,
        color=EmbedColors.WARNING,
    )


def truncate_field_value(value: str, max_length: int = 1024) -> str:
    """
    Truncate field value to Discord's limit (1024 chars).

    Args:
        value: Field value to truncate
        max_length: Maximum length (default: 1024, Discord's field limit)

    Returns:
        Truncated value with ellipsis if needed

    Examples:
        >>> truncate_field_value("a" * 1030)
        'aaa...aaa...'
    """
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def split_message(content: str, max_length: int = 2000) -> list[str]:
    """
    Split long message into multiple messages under Discord's 2000 char limit.

    Args:
        content: Message content to split
        max_length: Maximum length per message (default: 2000)

    Returns:
        List of message chunks

    Examples:
        >>> msgs = split_message("a" * 3000)
        >>> len(msgs)
        2
    """
    if len(content) <= max_length:
        return [content]

    chunks = []
    remaining = content

    while remaining:
        # Try to split at newline
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Find last newline before limit
        split_pos = remaining.rfind("\n", 0, max_length)

        if split_pos == -1:
            # No newline found, split at space
            split_pos = remaining.rfind(" ", 0, max_length)

        if split_pos == -1:
            # No space found, hard split
            split_pos = max_length

        chunks.append(remaining[:split_pos].strip())
        remaining = remaining[split_pos:].strip()

    return chunks


def format_user_mention(user_id: int) -> str:
    """
    Format user ID as Discord mention.

    Args:
        user_id: Discord user ID

    Returns:
        Discord mention string

    Examples:
        >>> format_user_mention(123456789)
        '<@123456789>'
    """
    return f"<@{user_id}>"


def format_channel_mention(channel_id: int) -> str:
    """
    Format channel ID as Discord mention.

    Args:
        channel_id: Discord channel ID

    Returns:
        Discord mention string

    Examples:
        >>> format_channel_mention(987654321)
        '<#987654321>'
    """
    return f"<#{channel_id}>"


def format_role_mention(role_id: int) -> str:
    """
    Format role ID as Discord mention.

    Args:
        role_id: Discord role ID

    Returns:
        Discord mention string

    Examples:
        >>> format_role_mention(111222333)
        '<@&111222333>'
    """
    return f"<@&{role_id}>"
