"""
bot_helpers.py — Discord bot utility functions.

Module-level helpers extracted from bot.py. Import from here to avoid
circular imports: bot.py imports bot_helpers, not vice-versa.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

import discord

from permissions import ALLOWED_USER_IDS
from runtime_state import resolve_context_lock

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream placeholder configuration
# ---------------------------------------------------------------------------

_STREAM_DISCORD_EDIT_INTERVAL = 200  # min chars between placeholder edits
_SHOW_THINKING_PLACEHOLDER: bool = os.getenv("THINKING_PLACEHOLDER", "1").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Message-content hint rate-limit cache
# ---------------------------------------------------------------------------

_MESSAGE_CONTENT_HINT_CACHE: dict[int, float] = {}
_MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS = 60 * 30


# ---------------------------------------------------------------------------
# Extracted helpers
# ---------------------------------------------------------------------------


def _resolve_channel_thread_scope(
    channel: Any,
    channel_id: int | None,
    *,
    user_id: int | str | None = None,
) -> tuple[int | None, int | None]:
    """Normalize Discord channel/thread into (channel_id, thread_id) scope."""
    resolved_channel_id = channel_id
    resolved_thread_id = None
    if isinstance(channel, discord.Thread):
        resolved_thread_id = channel.id
        if channel.parent_id:
            resolved_channel_id = channel.parent_id
    lock, _ = resolve_context_lock(
        user_id=user_id,
        channel_id=resolved_channel_id,
        thread_id=resolved_thread_id,
    )
    if lock and lock.get("mode") in {"channel", "thread", "prior_report"}:
        if lock.get("channel_id"):
            resolved_channel_id = int(lock["channel_id"])
        if lock.get("mode") in {"thread", "prior_report"}:
            resolved_thread_id = int(lock["thread_id"]) if lock.get("thread_id") is not None else None
        elif lock.get("mode") == "channel":
            resolved_thread_id = None
    return resolved_channel_id, resolved_thread_id


def make_discord_stream_handler(
    channel: Any,
) -> tuple[
    Any,  # on_partial_chunk coroutine callable
    Any,  # get_placeholder callable → discord.Message | None
]:
    """Return ``(on_partial_chunk, get_placeholder)`` for streaming Discord messages.

    ``on_partial_chunk(chunk_text)`` — call with each partial accumulated text.
      * Sends a "⏳ thinking…" placeholder on the first invocation.
      * Edits the placeholder embed every ``_STREAM_DISCORD_EDIT_INTERVAL`` chars.
      * Silently ignores Discord errors so the final buffered send still works.

    ``get_placeholder()`` — returns the placeholder :class:`discord.Message` (or
    ``None`` if no chunk arrived yet), so the caller can delete it before sending
    the formatted final response.
    """
    _placeholder: list[Any] = []
    _last_edit_len: list[int] = [0]
    # Prevents both the immediate task and _on_partial_chunk from sending a placeholder.
    _placeholder_claimed: list[bool] = [False]

    if _SHOW_THINKING_PLACEHOLDER:

        async def _send_thinking_placeholder() -> None:
            if _placeholder_claimed[0]:
                return
            _placeholder_claimed[0] = True
            try:
                _placeholder_msg = await channel.send("_⏳ Thinking…_")
                _placeholder.append(_placeholder_msg)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                _placeholder_claimed[0] = False

        try:
            asyncio.ensure_future(_send_thinking_placeholder())
        except RuntimeError:
            pass

    async def _on_partial_chunk(chunk_text: str) -> None:
        if not chunk_text:
            return
        try:
            if not _placeholder:
                if _placeholder_claimed[0]:
                    # Immediate placeholder task is in flight; skip until it resolves.
                    return
                _placeholder_claimed[0] = True
                msg = await channel.send("⏳ *thinking…*")
                _placeholder.append(msg)
                _last_edit_len[0] = 0

            if len(chunk_text) - _last_edit_len[0] < _STREAM_DISCORD_EDIT_INTERVAL:
                return

            preview = chunk_text[:1950]
            suffix = "…" if len(chunk_text) > 1950 else "\n\n*⏳ streaming…*"
            embed = discord.Embed(description=preview + suffix, color=discord.Color.purple())
            await _placeholder[0].edit(embed=embed)
            _last_edit_len[0] = len(chunk_text)
        except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
            log.debug("Stream placeholder update failed: %s", exc)

    def _get_placeholder() -> Any | None:
        return _placeholder[0] if _placeholder else None

    return _on_partial_chunk, _get_placeholder


def _is_user_allowed(user_id: int) -> bool:
    """Return True when *user_id* is in the configured allow-list."""
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _should_send_message_content_hint(channel: Any) -> bool:
    """Rate-limit message-content intent hints to avoid channel spam."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return False
    now = time.time()
    last_sent = _MESSAGE_CONTENT_HINT_CACHE.get(int(channel_id), 0.0)
    if now - last_sent < _MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS:
        return False
    _MESSAGE_CONTENT_HINT_CACHE[int(channel_id)] = now
    return True


def _default_ask_thread_cache_key(channel: Any, user_id: int) -> tuple[int, int, int]:
    guild_id = 0
    guild = getattr(channel, "guild", None)
    if guild is not None and getattr(guild, "id", None):
        guild_id = int(guild.id)
    return guild_id, int(channel.id), int(user_id)


def _default_ask_thread_user_tag(user_id: int) -> str:
    return f"u{int(user_id)}"


def _build_default_ask_thread_name(user_question: str, user_id: int) -> str:
    snippet = re.sub(r"\s+", " ", (user_question or "").strip())
    if not snippet:
        snippet = "conversation"
    snippet = snippet[:50].strip()
    if len(snippet) == 50:
        snippet += "…"
    tag = _default_ask_thread_user_tag(user_id)
    name = f"💬 {snippet} · {tag}"
    if len(name) > 100:
        keep = max(1, 100 - len(f" · {tag}") - 1)
        name = f"💬 {snippet[:keep].rstrip()} · {tag}"
    return name


def _pick_most_recent_thread(candidates: list[discord.Thread]) -> discord.Thread:
    def _thread_sort_key(thread: discord.Thread) -> int:
        last_msg = getattr(thread, "last_message_id", None)
        try:
            return int(last_msg or thread.id)
        except (TypeError, ValueError):
            return int(thread.id)

    return sorted(candidates, key=_thread_sort_key, reverse=True)[0]
