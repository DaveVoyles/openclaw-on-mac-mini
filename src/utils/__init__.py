"""Utils package - organized utility functions."""

# Re-export commonly used functions from the old utils.py for backward compatibility
import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("openclaw.utils")


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


async def safe_call(
    coro,
    *,
    timeout: int = 20,
    fallback: str | None = None,
    label: str = "operation",
) -> str:
    """Await *coro* with a timeout; return *fallback* on timeout or error.

    Parameters
    ----------
    coro
        Coroutine to await
    timeout
        Seconds before cancelling
    fallback
        Message to return on timeout/error (default: error description)
    label
        Operation name for logging
    """
    if fallback is None:
        fallback = f"{label} timed out or failed"
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        log.warning(f"{label} timed out after {timeout}s")
        return fallback
    except Exception as e:
        log.warning(f"{label} failed: {e}")
        return fallback


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
    "safe_call",
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
