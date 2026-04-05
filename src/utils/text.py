"""Text utility functions for OpenClaw."""

import re


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to max length with suffix.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: String to append when truncated (default: "...")

    Returns:
        Truncated text with suffix if needed

    Examples:
        >>> truncate("Hello world", 8)
        'Hello...'
        >>> truncate("Short", 10)
        'Short'
    """
    if len(text) <= max_length:
        return text

    if max_length <= len(suffix):
        return suffix[:max_length]

    return text[: max_length - len(suffix)] + suffix


def split_by_length(text: str, max_length: int, separator: str = "\n") -> list[str]:
    """
    Split text into chunks of max length, preserving word boundaries.

    Args:
        text: Text to split
        max_length: Maximum length per chunk
        separator: Separator to use between natural split points (default: newline)

    Returns:
        List of text chunks, each <= max_length

    Examples:
        >>> split_by_length("abc\\ndef\\nghi", 5)
        ['abc', 'def', 'ghi']
        >>> split_by_length("Hello world this is a test", 12)
        ['Hello world', 'this is a', 'test']
    """
    if not text:
        return []

    if len(text) <= max_length:
        return [text]

    chunks = []

    # Try splitting by separator first
    parts = text.split(separator)
    current_chunk = []
    current_length = 0

    for part in parts:
        part_length = len(part)

        # If this single part is too long, split it by words
        if part_length > max_length:
            if current_chunk:
                chunks.append(separator.join(current_chunk))
                current_chunk = []
                current_length = 0

            # Split long part by words
            words = part.split()
            for word in words:
                if current_length + len(word) + 1 <= max_length:
                    current_chunk.append(word)
                    current_length += len(word) + 1
                else:
                    if current_chunk:
                        chunks.append(" ".join(current_chunk))
                    current_chunk = [word]
                    current_length = len(word)

            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_length = 0
        else:
            # Check if adding this part would exceed limit
            potential_length = current_length + part_length + (len(separator) if current_chunk else 0)

            if potential_length <= max_length:
                current_chunk.append(part)
                current_length = potential_length
            else:
                if current_chunk:
                    chunks.append(separator.join(current_chunk))
                current_chunk = [part]
                current_length = part_length

    if current_chunk:
        chunks.append(separator.join(current_chunk))

    return chunks


def extract_code_blocks(text: str, language: str = "") -> list[str]:
    """
    Extract code blocks from markdown text.

    Args:
        text: Markdown text containing code blocks
        language: Optional language filter (e.g., "python", "javascript")

    Returns:
        List of code block contents

    Examples:
        >>> text = "```python\\nprint('hi')\\n```"
        >>> extract_code_blocks(text, "python")
        ["print('hi')"]
    """
    if language:
        pattern = rf"```{re.escape(language)}\n(.*?)```"
    else:
        pattern = r"```(?:\w+)?\n(.*?)```"

    matches = re.findall(pattern, text, re.DOTALL)
    return [match.strip() for match in matches]


def remove_markdown(text: str) -> str:
    """
    Remove common markdown formatting from text.

    Args:
        text: Markdown formatted text

    Returns:
        Plain text with markdown removed

    Examples:
        >>> remove_markdown("**bold** and *italic*")
        'bold and italic'
    """
    # Remove code blocks first
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", "", text)

    # Remove bold/italic
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)

    # Remove headers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)

    # Remove links [text](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    return text.strip()


def sanitize_filename(text: str, max_length: int = 255) -> str:
    """
    Sanitize text to be safe for use as a filename.

    Args:
        text: Text to sanitize
        max_length: Maximum filename length (default: 255)

    Returns:
        Sanitized filename

    Examples:
        >>> sanitize_filename("Hello/World?.txt")
        'Hello_World_.txt'
    """
    # Replace invalid characters with underscores
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", text)

    # Replace whitespace with underscores
    sanitized = re.sub(r"\s+", "_", sanitized)

    # Remove leading/trailing underscores and dots
    sanitized = sanitized.strip("_.")

    # Truncate if too long
    if len(sanitized) > max_length:
        name, ext = sanitized.rsplit(".", 1) if "." in sanitized else (sanitized, "")
        if ext:
            max_name_length = max_length - len(ext) - 1
            sanitized = name[:max_name_length] + "." + ext
        else:
            sanitized = sanitized[:max_length]

    return sanitized or "untitled"
