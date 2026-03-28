"""Shared utilities for cog modules — avoids duplicating helpers in every cog."""

from discord import app_commands

from audit import audit_log  # noqa: F401 — re-exported for cog convenience


def is_service_allowed(skill: str, service: str) -> bool:
    """Forward to bot.py's is_service_allowed — imported lazily to avoid circular imports."""
    from bot import is_service_allowed as _is_service_allowed
    return _is_service_allowed(skill, service)


def require_auth():
    """``app_commands.check`` that gates a cog command behind the allow-list.

    Raises ``app_commands.CheckFailure`` so the cog's error handler can
    send an ephemeral "not authorized" message.
    """

    async def predicate(interaction) -> bool:
        from bot import is_allowed
        if not is_allowed(interaction):
            raise app_commands.CheckFailure(
                "🔒 You are not authorized to use this command."
            )
        return True

    return app_commands.check(predicate)


def truncate_for_embed(text: str, limit: int = 4000) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


def split_response(text: str, limit: int = 3800) -> list[str]:
    """Split a long response into chunks that fit within Discord's embed limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
            chunks.append(text[:split_at] + "…")
            text = "…" + text[split_at:].lstrip("\n")
        else:
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
    return chunks
