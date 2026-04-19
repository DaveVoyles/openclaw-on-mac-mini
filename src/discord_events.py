"""Discord message event handler — extracted from bot.py."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from typing import Any

import discord

from approvals import is_emergency_stopped
from ask_orchestrator import normalize_model_preference, run_ask_stream
from audit import audit_log
from bot_formatting import (
    format_markdown_for_discord as _format_markdown_for_discord,
    format_tables_for_context as _format_tables_for_context,
    split_response as _split_response,
)
from config import cfg
from llm import chat_stream as llm_chat_stream
from llm import is_configured as llm_is_configured
from memory import get_model_preference, get_routing_profile, store as conversation_store
from permissions import ALLOWED_USER_IDS
from llm_patterns import _MEMORY_STORE_RE
from quality_helpers import (
    _append_explainability_footer,
    _build_ask_recovery_block,
    _build_coverage_summary_for_embed,
    _explainability_note_from_meta,
    _run_quality_auto_repair,
    _safe_score_answer_quality,
    _should_prefer_file_for_multichunk_response,
    _with_requested_item_target,
)
from response_actions import ResponseActions, _generate_follow_ups, _resolve_channel_thread_scope
from runtime_state import (
    get_anchor_state,
    get_bot,
    set_anchor_state,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_DEFAULT_ASK_THREAD_CACHE: dict[tuple[int, int, int], tuple[int, float]] = {}
_DEFAULT_ASK_THREAD_CACHE_TTL_SECONDS = 60 * 60 * 24

_MESSAGE_CONTENT_HINT_CACHE: dict[int, float] = {}
_MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS = 60 * 30

# W5-2: Track threads where archive warning has already been sent (TTL-bounded cache)
_ARCHIVE_WARNING_SENT: dict[int, float] = {}  # thread_id → timestamp
_ARCHIVE_WARNING_TTL = 86400  # 24 hours


def _has_archive_warning_been_sent(thread_id: int) -> bool:
    """Check if archive warning was sent for this thread recently."""
    cutoff = time.monotonic() - _ARCHIVE_WARNING_TTL
    stale = [tid for tid, ts in _ARCHIVE_WARNING_SENT.items() if ts < cutoff]
    for tid in stale:
        del _ARCHIVE_WARNING_SENT[tid]
    return thread_id in _ARCHIVE_WARNING_SENT


def _mark_archive_warning_sent(thread_id: int) -> None:
    _ARCHIVE_WARNING_SENT[thread_id] = time.monotonic()


# ---------------------------------------------------------------------------
# Copied helper functions from bot.py
# ---------------------------------------------------------------------------


def _is_user_allowed(user_id: int) -> bool:
    """Return True when *user_id* is in the configured allow-list."""
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _bot_can_read_channel(channel: Any) -> bool:
    """Best-effort check that the bot has read access to *channel*."""
    _bot = get_bot()
    guild = getattr(channel, "guild", None)
    if guild is None:
        return True
    permissions_for = getattr(channel, "permissions_for", None)
    if not callable(permissions_for):
        return False
    bot_member = getattr(guild, "me", None)
    if bot_member is None and _bot is not None and _bot.user is not None and hasattr(guild, "get_member"):
        bot_member = guild.get_member(_bot.user.id)
    if bot_member is None:
        return True
    perms = permissions_for(bot_member)
    return bool(getattr(perms, "read_messages", getattr(perms, "view_channel", False)))


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


def _is_reusable_bot_thread(candidate: Any, *, parent_channel_id: int) -> bool:
    _bot = get_bot()
    if not isinstance(candidate, discord.Thread):
        return False
    if _bot is None or _bot.user is None:
        return False
    if getattr(candidate, "owner_id", None) != _bot.user.id:
        return False
    if getattr(candidate, "parent_id", None) != parent_channel_id:
        return False
    if bool(getattr(candidate, "archived", False)):
        return False
    if bool(getattr(candidate, "locked", False)):
        return False
    return True


def _remember_default_ask_thread(channel: Any, user_id: int, thread_id: int) -> None:
    _DEFAULT_ASK_THREAD_CACHE[_default_ask_thread_cache_key(channel, user_id)] = (thread_id, time.time())


def _pick_most_recent_thread(candidates: list[discord.Thread]) -> discord.Thread:
    def _thread_sort_key(thread: discord.Thread) -> int:
        last_msg = getattr(thread, "last_message_id", None)
        try:
            return int(last_msg or thread.id)
        except (ValueError, TypeError):
            return int(thread.id)

    return sorted(candidates, key=_thread_sort_key, reverse=True)[0]


async def _get_or_create_default_ask_thread(
    channel: Any,
    *,
    user_id: int,
    user_question: str,
) -> tuple[discord.Thread | None, bool]:
    """Return (thread, created_new) for top-level default ask routing."""
    _bot = get_bot()
    if (
        not cfg.thread_auto_create
        or isinstance(channel, discord.DMChannel)
        or not hasattr(channel, "create_thread")
        or _bot is None
        or _bot.user is None
    ):
        return None, False

    key = _default_ask_thread_cache_key(channel, user_id)
    cached = _DEFAULT_ASK_THREAD_CACHE.get(key)
    if cached:
        thread_id, last_seen = cached
        if time.time() - last_seen <= _DEFAULT_ASK_THREAD_CACHE_TTL_SECONDS:
            candidate = _bot.get_channel(thread_id)
            if candidate is None:
                guild = getattr(channel, "guild", None)
                get_thread = getattr(guild, "get_thread", None)
                if callable(get_thread):
                    candidate = get_thread(thread_id)
            if _is_reusable_bot_thread(candidate, parent_channel_id=int(channel.id)):
                _remember_default_ask_thread(channel, user_id, int(candidate.id))
                return candidate, False
        else:
            _DEFAULT_ASK_THREAD_CACHE.pop(key, None)

    user_tag = _default_ask_thread_user_tag(user_id)
    channel_threads = getattr(channel, "threads", None)
    if channel_threads is not None:
        matching_threads = [
            thread
            for thread in list(channel_threads)
            if _is_reusable_bot_thread(thread, parent_channel_id=int(channel.id))
            and user_tag in str(getattr(thread, "name", ""))
        ]
        if matching_threads:
            chosen = _pick_most_recent_thread(matching_threads)
            _remember_default_ask_thread(channel, user_id, int(chosen.id))
            return chosen, False

    # W5-1: Also check archived threads — unarchive and reuse if possible
    if hasattr(channel, "archived_threads"):
        try:
            async for archived_thread in channel.archived_threads(limit=15):
                if (
                    _bot is not None
                    and _bot.user is not None
                    and getattr(archived_thread, "owner_id", None) == _bot.user.id
                    and getattr(archived_thread, "parent_id", None) == int(channel.id)
                    and not getattr(archived_thread, "locked", False)
                    and user_tag in str(getattr(archived_thread, "name", ""))
                ):
                    try:
                        await archived_thread.edit(archived=False)
                        log.debug("Unarchived thread %d for user %d", archived_thread.id, user_id)
                        _remember_default_ask_thread(channel, user_id, int(archived_thread.id))
                        return archived_thread, False
                    except discord.HTTPException as unarchive_exc:
                        log.debug("Failed to unarchive thread %d: %s", archived_thread.id, unarchive_exc)
        except (discord.HTTPException, asyncio.TimeoutError) as archived_exc:
            log.debug("Archived thread search failed: %s", archived_exc)

    try:
        archive_duration = 60 if cfg.thread_archive_minutes <= 60 else 1440
        created = await channel.create_thread(
            name=_build_default_ask_thread_name(user_question, user_id),
            auto_archive_duration=archive_duration,
            reason=f"Auto-threaded default ask for user {user_id}",
        )
        _remember_default_ask_thread(channel, user_id, int(created.id))
        return created, True
    except discord.HTTPException as exc:
        log.debug("Default ask auto-thread creation failed: %s", exc)
        return None, False


async def _maybe_send_archive_warning(thread: discord.Thread) -> None:
    """W5-2: Send a one-time warning when a thread is within 10 minutes of auto-archiving."""
    thread_id = int(thread.id)
    if _has_archive_warning_been_sent(thread_id):
        return
    archive_duration_minutes = getattr(thread, "auto_archive_duration", None)
    if not archive_duration_minutes:
        return
    last_msg_id = getattr(thread, "last_message_id", None)
    if not last_msg_id:
        return
    # Derive timestamp from Discord snowflake
    last_msg_ts = ((int(last_msg_id) >> 22) + 1420070400000) / 1000
    elapsed_since_last = time.time() - last_msg_ts
    archive_in_seconds = archive_duration_minutes * 60 - elapsed_since_last
    if 0 < archive_in_seconds <= 600:
        try:
            await thread.send(
                "⚠️ This thread will auto-archive soon. Keep chatting to extend it, or start a new `/ask`."
            )
            _mark_archive_warning_sent(thread_id)
        except discord.HTTPException as exc:
            log.debug("Failed to send archive warning for thread %d: %s", thread_id, exc)


# ---------------------------------------------------------------------------
# Main handler — decomposition helpers
# ---------------------------------------------------------------------------


async def _preflight_message_checks(
    message: discord.Message,
    _bot: Any,
) -> tuple[bool, bool, bool]:
    """Run early skip/auth/runtime gates.

    Returns (should_exit, in_thread, original_bot_owned_thread).
    May call ``_bot.process_commands`` or send notice messages as side effects.
    """
    if message.author.bot:
        return True, False, False

    user_question = (message.content or "").strip()
    if user_question.startswith("/"):
        await _bot.process_commands(message)
        return True, False, False

    in_thread = isinstance(message.channel, discord.Thread)
    bot_owns_thread = in_thread and _bot.user is not None and message.channel.owner_id == _bot.user.id

    if in_thread and not _bot_can_read_channel(message.channel):
        await _bot.process_commands(message)
        return True, in_thread, bot_owns_thread

    if not in_thread and not _bot_can_read_channel(message.channel):
        await _bot.process_commands(message)
        return True, in_thread, bot_owns_thread

    if not _is_user_allowed(message.author.id):
        return True, in_thread, bot_owns_thread

    if is_emergency_stopped():
        await message.channel.send(
            "🛑 **Emergency stop is active.** Conversation is disabled. Use `/estop resume` to resume."
        )
        return True, in_thread, bot_owns_thread

    if not llm_is_configured():
        await message.channel.send("⚠️ LLM not configured.")
        return True, in_thread, bot_owns_thread

    return False, in_thread, bot_owns_thread


async def _resolve_flow_channel(
    message: discord.Message,
    in_thread: bool,
    bot_owns_thread: bool,
    original_bot_owned_thread: bool,
    user_question: str,
) -> tuple[Any, bool, bool]:
    """Resolve the channel where ask-flow output should be posted.

    Returns (flow_channel, in_thread, bot_owns_thread).
    """
    flow_channel = message.channel
    if original_bot_owned_thread:
        parent_channel = getattr(message.channel, "parent", None)
        if parent_channel is not None and getattr(parent_channel, "id", None):
            _remember_default_ask_thread(parent_channel, message.author.id, int(message.channel.id))

    if not in_thread:
        routed_thread, _created_new = await _get_or_create_default_ask_thread(
            message.channel,
            user_id=message.author.id,
            user_question=user_question,
        )
        if routed_thread is not None:
            flow_channel = routed_thread
            in_thread = True
            bot_owns_thread = True
            _remember_default_ask_thread(message.channel, message.author.id, int(routed_thread.id))
            try:
                await message.channel.send(f"💬 Continuing in {routed_thread.mention}")
            except Exception as exc:  # broad: intentional — redirect send can raise any Discord error
                log.debug("Failed to send default-ask thread redirect: %s", exc)

    return flow_channel, in_thread, bot_owns_thread


async def _enforce_thread_message_cap(
    message: discord.Message,
    flow_channel: Any,
    bot_owns_thread: bool,
) -> bool:
    """Send a warning and return True if the bot-owned thread is at capacity."""
    if not (bot_owns_thread and cfg.thread_max_messages > 0):
        return False
    conv = conversation_store.get(
        user_id=message.author.id,
        channel_id=flow_channel.id,
        user_name=str(message.author.display_name),
    )
    if conv.message_count >= cfg.thread_max_messages * 2:
        await flow_channel.send(
            f"⚠️ This thread has reached {cfg.thread_max_messages} exchanges. "
            "Please start a new `/ask` for a fresh conversation."
        )
        return True
    return False


async def _maybe_send_empty_content_hint(message: discord.Message) -> None:
    """Notify users that the bot received an empty message, with a one-time hint."""
    if not (
        getattr(message, "guild", None) is not None
        and _is_user_allowed(message.author.id)
        and _should_send_message_content_hint(message.channel)
    ):
        return
    try:
        await message.channel.send(
            "ℹ️ I received a message with no readable content. "
            "If plain-message chat isn't working, enable **Message Content Intent** "
            "for this bot in the Discord Developer Portal, then restart OpenClaw. "
            "You can still use `/ask` immediately."
        )
    except discord.HTTPException as exc:
        log.debug("Failed to send message-content hint: %s", exc)


async def _run_ask_pipeline(
    message: discord.Message,
    flow_channel: Any,
    user_question: str,
) -> tuple[str, str, Any, int | None, int | None]:
    """Run the LLM ask + quality-repair pipeline.

    Returns (response_text, model_used, result_or_none, scoped_channel_id, scoped_thread_id).
    ``result_or_none`` is the raw ``run_ask_stream`` result (or None on error) so
    callers can pull metadata such as ``final_meta`` / ``model_used`` for feedback.
    """
    conv = conversation_store.get(
        user_id=message.author.id,
        channel_id=flow_channel.id,
        user_name=str(message.author.display_name),
    )

    model_pref = get_model_preference(message.author.id)
    user_routing_profile = get_routing_profile(message.author.id)
    from llm import _needs_tools as llm_needs_tools

    model_pref, _ = normalize_model_preference(user_question, model_pref, llm_needs_tools)

    response_text = ""
    model_used = "unknown"
    result: Any = None
    scoped_channel_id, scoped_thread_id = _resolve_channel_thread_scope(
        flow_channel,
        flow_channel.id,
        user_id=message.author.id,
    )

    try:

        def _update_history(updated_history: list[dict[str, Any]]) -> None:
            conv.update_from_llm(updated_history)
            conversation_store.auto_save_thread(
                message.author.id,
                flow_channel.id,
                str(message.author.display_name),
            )

        from bot import PROVIDER_STREAM, make_discord_stream_handler

        _on_partial, _get_placeholder = (
            make_discord_stream_handler(flow_channel) if PROVIDER_STREAM else (None, lambda: None)
        )

        context_controls: dict[str, Any] = {}
        if hasattr(message.channel, "name"):
            context_controls["channel_name"] = message.channel.name
        elif hasattr(message.channel, "parent") and message.channel.parent:
            context_controls["channel_name"] = message.channel.parent.name

        result = await run_ask_stream(
            llm_stream=llm_chat_stream,
            user_message=user_question,
            history=conv.history,
            user_name=str(message.author.display_name),
            model_preference=model_pref,
            channel_id=scoped_channel_id,
            thread_id=scoped_thread_id,
            user_id=str(message.author.id),
            update_history=_update_history,
            routing_profile=user_routing_profile,
            on_partial_chunk=_on_partial,
            context_controls=context_controls,
        )

        _placeholder_msg = _get_placeholder()
        if _placeholder_msg is not None:
            try:
                await _placeholder_msg.delete()
            except discord.HTTPException:
                pass
        response_text = result.response_text
        model_used = result.model_used

        final_meta: dict[str, Any] = _with_requested_item_target(result.final_meta, question=user_question)
        _is_memory_store = bool(_MEMORY_STORE_RE.search(user_question))
        quality_meta = _safe_score_answer_quality(
            response_text,
            final_meta=final_meta,
            context="ask_message_flow",
        )

        async def _run_retry_stream(retry_question: str) -> Any:
            return await run_ask_stream(
                llm_stream=llm_chat_stream,
                user_message=retry_question,
                history=conv.history,
                user_name=str(message.author.display_name),
                model_preference=model_pref,
                channel_id=scoped_channel_id,
                thread_id=scoped_thread_id,
                user_id=str(message.author.id),
                update_history=_update_history,
                routing_profile=user_routing_profile,
            )

        repair_result = await _run_quality_auto_repair(
            question=user_question,
            response_text=response_text,
            model_used=model_used,
            final_meta=final_meta,
            quality_meta=quality_meta,
            context="ask_message_flow",
            run_retry_stream=_run_retry_stream,
        )
        response_text = str(repair_result["response_text"])
        final_meta = dict(repair_result["final_meta"])
        recovery_block = _build_ask_recovery_block(final_meta)
        if model_used == "perplexity-direct":
            recovery_block = None
        if recovery_block and "Recovery note" not in response_text and not _is_memory_store:
            # W4-1: Strip duplicate sources from recovery block
            if re.search(r"(?:\*\*Sources\*\*|Sources)\s*:", response_text, re.IGNORECASE):
                recovery_block = re.sub(
                    r"\n{0,2}(?:\*\*Sources\*\*|Sources):?\s*\n(?:(?:[-*]|\d+\.)\s+.+\n?)+",
                    "",
                    recovery_block,
                    flags=re.IGNORECASE,
                ).rstrip()
            if recovery_block:
                response_text = f"{response_text.rstrip()}{recovery_block}"
        log.info(
            "message ask quality status=%s path=%s",
            final_meta.get("answer_quality", {}).get("status", "unknown"),
            final_meta.get("answer_quality_retry", {}).get("status_path"),
        )
    except Exception as e:  # broad: intentional — outer catch for entire LLM ask-flow pipeline
        log.error("Message ask-flow LLM error: %s", e)
        response_text = f"❌ **Error:** {e}"
        model_used = "error"

    if not response_text or len(response_text.strip()) < 5:
        response_text = "⚠️ I wasn't able to generate a useful response. Try rephrasing your question."

    return response_text, model_used, result, scoped_channel_id, scoped_thread_id


async def _render_and_send_ask_response(
    message: discord.Message,
    flow_channel: Any,
    response_text: str,
    model_used: str,
    scoped_channel_id: int | None,
    scoped_thread_id: int | None,
    ask_start: float,
    result: Any,
    user_question: str,
) -> None:
    """Format the response, send it (with fallback), and schedule feedback."""
    table_image_file = None
    try:
        from table_renderer import (
            extract_table_text,
            render_table_image,
            should_render_table_image,
        )

        table_text = extract_table_text(response_text)
        if table_text and should_render_table_image(table_text):
            img_bytes = render_table_image(table_text)
            if img_bytes:
                table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
    except (ImportError, ValueError, RuntimeError, OSError) as e:
        log.debug("Thread table image rendering failed: %s", e)

    response_text = _format_markdown_for_discord(response_text)
    response_text = _format_tables_for_context(
        response_text,
        channel_id=scoped_channel_id,
        thread_id=scoped_thread_id,
    )
    chunks = _split_response(response_text)

    # W5-2: Warn if the thread is close to auto-archiving
    if isinstance(flow_channel, discord.Thread):
        await _maybe_send_archive_warning(flow_channel)

    _elapsed = time.monotonic() - ask_start
    _display_model = (model_used or "unknown").replace("models/", "")

    _last_sent_msg: discord.Message | None = None
    try:
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(description=chunk, color=discord.Color.purple())
            chunk_str = f"[{i + 1}/{len(chunks)}] • " if len(chunks) > 1 else ""
            embed.set_footer(text=f"{chunk_str}{_display_model} • ⏱ {_elapsed:.1f}s")
            _last_sent_msg = await flow_channel.send(embed=embed)
        if table_image_file:
            _last_sent_msg = await flow_channel.send(file=table_image_file)
    except Exception as exc:  # broad: intentional — send can raise many Discord or runtime errors
        log.warning("Failed to send default ask response in flow channel: %s", exc)
        if flow_channel is not message.channel:
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                chunk_str = f"[{i + 1}/{len(chunks)}] • " if len(chunks) > 1 else ""
                embed.set_footer(text=f"{chunk_str}{_display_model} • ⏱ {_elapsed:.1f}s")
                _last_sent_msg = await message.channel.send(embed=embed)
            if table_image_file:
                _last_sent_msg = await message.channel.send(file=table_image_file)

    if _last_sent_msg is not None:
        try:
            from bot import _collect_feedback
            import hashlib as _hashlib

            _qhash = _hashlib.sha256(user_question.encode()).hexdigest()[:16]
            _result_meta = getattr(result, "final_meta", {}) or {}
            _provider = str(_result_meta.get("provider", ""))
            _skills: list[str] = list(_result_meta.get("skills_invoked", []))
            _model = getattr(result, "model_used", "")
            asyncio.ensure_future(
                _collect_feedback(
                    bot_message=_last_sent_msg,
                    query_hash=_qhash,
                    model=_model,
                    provider=_provider,
                    skills=_skills,
                )
            )
        except (AttributeError, KeyError, RuntimeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_message(
    message: discord.Message,
    *,
    channel_roles: dict[int, str] | None = None,
) -> None:
    """Handle plain-text messages — extracted from bot.py for modularity."""
    _bot = get_bot()
    if _bot is None:
        return

    should_exit, in_thread, bot_owns_thread = await _preflight_message_checks(message, _bot)
    if should_exit:
        return
    original_bot_owned_thread = bot_owns_thread

    user_question = (message.content or "").strip()

    flow_channel, in_thread, bot_owns_thread = await _resolve_flow_channel(
        message,
        in_thread,
        bot_owns_thread,
        original_bot_owned_thread,
        user_question,
    )

    if await _enforce_thread_message_cap(message, flow_channel, bot_owns_thread):
        return

    if not user_question:
        await _maybe_send_empty_content_hint(message)
        return

    _ask_start = time.monotonic()

    async with flow_channel.typing():
        response_text, model_used, result, scoped_channel_id, scoped_thread_id = await _run_ask_pipeline(
            message, flow_channel, user_question
        )

        await _render_and_send_ask_response(
            message,
            flow_channel,
            response_text,
            model_used,
            scoped_channel_id,
            scoped_thread_id,
            _ask_start,
            result,
            user_question,
        )

    audit_action = "thread_followup" if original_bot_owned_thread else "ask_default"
    audit_log(message.author, audit_action, detail=user_question[:200])
    conversation_store.cleanup_expired()
