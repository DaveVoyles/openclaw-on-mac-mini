"""Shared utilities for cog modules — avoids duplicating helpers in every cog."""


def audit_log(user, action, detail="", result="success"):
    """Forward to bot.py's audit_log — imported lazily to avoid circular imports."""
    from bot import audit_log as _audit_log
    _audit_log(user, action, detail=detail, result=result)


def is_service_allowed(skill: str, service: str) -> bool:
    """Forward to bot.py's is_service_allowed — imported lazily to avoid circular imports."""
    from bot import is_service_allowed as _is_service_allowed
    return _is_service_allowed(skill, service)
