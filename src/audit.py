"""
OpenClaw Audit Logger — single source of truth for audit_log().

Provides a buffered audit logger that bot.py flushes to disk.
Extracted from bot.py to break circular imports (cog_helpers → bot).
"""

import collections
import datetime
import sys

_audit_buffer: collections.deque = collections.deque(maxlen=10_000)

_HIGH_SEVERITIES = {"HIGH", "CRITICAL"}


def audit_log(
    user,
    action: str,
    detail: str | None = None,
    result: str = "success",
    severity: str = "INFO",
) -> None:
    """Buffer an audit entry (flushed to disk every 30 seconds by bot._audit_writer).

    Args:
        user: Discord user object or string identifier; None → "system".
        action: Short machine-readable action name (e.g. "docker_restart").
        detail: Optional human-readable context such as command arguments.
        result: Outcome string, defaults to "success".
        severity: One of INFO / WARNING / HIGH / CRITICAL. HIGH and CRITICAL
            entries are also written immediately to stdout so they survive a
            process crash in Docker logging.
    """
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user": str(user) if user else "system",
        "user_id": str(user.id) if user and hasattr(user, "id") else "0",
        "action": action,
        "detail": detail or "",
        "result": result,
        "severity": severity.upper(),
    }
    _audit_buffer.append(entry)

    if severity.upper() in _HIGH_SEVERITIES:
        print(
            f"[AUDIT:{severity.upper()}] {entry['ts']} user={entry['user']} "
            f"action={action} detail={entry['detail']} result={result}",
            file=sys.stdout,
        )
        sys.stdout.flush()
