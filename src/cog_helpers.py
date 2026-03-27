"""Shared utilities for cog modules — avoids duplicating helpers in every cog."""

from audit import audit_log  # noqa: F401 — re-exported for cog convenience


def is_service_allowed(skill: str, service: str) -> bool:
    """Forward to bot.py's is_service_allowed — imported lazily to avoid circular imports."""
    from bot import is_service_allowed as _is_service_allowed
    return _is_service_allowed(skill, service)
