"""
OpenClaw Audit Logger — single source of truth for audit_log().

Provides a buffered audit logger that bot.py flushes to disk.
Extracted from bot.py to break circular imports (cog_helpers → bot).
"""

import collections
import datetime

_audit_buffer: collections.deque = collections.deque(maxlen=10_000)


def audit_log(
    user,
    action: str,
    detail: str = "",
    result: str = "success",
) -> None:
    """Buffer an audit entry (flushed to disk every 30 seconds by bot._audit_writer)."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user": str(user) if user else "system",
        "user_id": str(user.id) if user and hasattr(user, "id") else "0",
        "action": action,
        "detail": detail,
        "result": result,
    }
    _audit_buffer.append(entry)
