"""Utils package - organized utility functions."""

# Re-export commonly used functions from the old utils.py for backward compatibility
import os
from pathlib import Path


def atomic_write(path: Path, data: str) -> None:
    """Write *data* to *path* atomically via temp-file + fsync + rename.

    Guarantees that readers see either the old content or the new content,
    never a half-written file — even after a crash or power loss.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# Re-export functions from submodules for convenience
from .text import (
    extract_code_blocks,
    remove_markdown,
    sanitize_filename,
    split_by_length,
    truncate,
)
from .time import (
    format_duration,
    format_duration_short,
    parse_duration,
    relative_time,
    seconds_until_hour,
)

try:
    from .discord import (
        EmbedColors,
        create_embed,
        create_error_embed,
        create_success_embed,
        create_warning_embed,
        format_channel_mention,
        format_role_mention,
        format_user_mention,
        split_message,
        truncate_field_value,
    )
except ImportError:
    # Discord imports are optional (for testing without discord.py)
    pass

__all__ = [
    # File operations
    "atomic_write",
    # Text utilities
    "truncate",
    "split_by_length",
    "extract_code_blocks",
    "remove_markdown",
    "sanitize_filename",
    # Time utilities
    "parse_duration",
    "format_duration",
    "format_duration_short",
    "relative_time",
    "seconds_until_hour",
    # Discord utilities (optional)
    "EmbedColors",
    "create_embed",
    "create_error_embed",
    "create_success_embed",
    "create_warning_embed",
    "format_user_mention",
    "format_channel_mention",
    "format_role_mention",
    "truncate_field_value",
    "split_message",
]
