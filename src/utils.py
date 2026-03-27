"""
OpenClaw Shared Utilities

Reusable helpers extracted from repeated patterns across the codebase:
- atomic_write: crash-safe file writes via temp-file + fsync + rename
- safe_call: async timeout wrapper with fallback message
"""

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
    coro : awaitable
        The coroutine to execute.
    timeout : int
        Maximum seconds to wait (default 20).
    fallback : str | None
        Value returned on timeout/error. Defaults to a generic message.
    label : str
        Human-readable name for log messages.
    """
    if fallback is None:
        fallback = f"⏱️ {label} timed out"
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("%s timed out after %ds", label, timeout)
        return fallback
    except Exception as exc:
        log.warning("%s failed: %s", label, exc)
        return fallback
