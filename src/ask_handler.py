"""Handler for /ask slash command — extracted from bot.py."""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
import functools
import re
import time
from typing import Any

import discord
from discord import app_commands

from approvals import is_emergency_stopped
from ask_orchestrator import (
    normalize_model_preference,
    run_ask_stream,
)
from audit import audit_log
from bot_attachments import (
    handle_doc_attachment as _handle_doc_attachment,
)
from bot_attachments import (
    handle_image_attachment as _handle_image_attachment,
)
from bot_formatting import (
    build_attachment_embed_summary as _build_attachment_embed_summary,
)
from bot_formatting import (
    extract_file_attachment as _extract_file_attachment,
)
from bot_formatting import (
    extract_image_url as _extract_image_url,
)
from bot_formatting import (
    format_markdown_for_discord as _format_markdown_for_discord,
)
from bot_formatting import (
    format_tables_for_context as _format_tables_for_context,
)
from bot_formatting import (
    split_response as _split_response,
)
from bot_formatting import (
    strip_placeholder_table_rows as _strip_placeholder_table_rows,
)
from config import cfg
from constants import EMBED_SPLIT_LIMIT, MAX_FILE_SIZE
from llm import SUPPORTED_IMAGE_MIMES, get_rate_info
from llm import chat as llm_chat  # noqa: F401 — available if needed
from llm import chat_stream as llm_chat_stream
from llm import is_configured as llm_is_configured
from llm_patterns import _MEMORY_STORE_RE
from memory import get_model_preference, get_routing_profile
from memory import store as conversation_store
from quality_helpers import (
    _append_explainability_footer,
    _build_ask_context_controls,
    _build_ask_failure_message,
    _build_ask_recovery_block,
    _build_ask_timeout_message,
    _build_coverage_summary_for_embed,
    _classify_ask_failure,
    _explainability_note_from_meta,
    _run_quality_auto_repair,
    _safe_score_answer_quality,
    _should_prefer_file_for_multichunk_response,
    _with_requested_item_target,
)
from response_actions import ResponseActions, _generate_follow_ups, _resolve_channel_thread_scope
from runtime_state import (
    set_anchor_state,
    set_context_lock,  # noqa: F401 — available if needed
)
from trace_context import get_trace_id

log = logging.getLogger(__name__)

_EMBED_LIMIT = EMBED_SPLIT_LIMIT
_FILE_THRESHOLD = 8000
_STREAM_EDIT_INTERVAL = 3.0


# ---------------------------------------------------------------------------
# Module-level pipeline helpers (extracted from handle_ask nested functions)
# ---------------------------------------------------------------------------

async def _ask_think(
    status: str,
    *,
    interaction: discord.Interaction,
    question: str,
    progress_lines: list[str],
    progress_start: float,
) -> None:
    elapsed = time.monotonic() - progress_start
    progress_lines.append(f"💭 {status} ({elapsed:.0f}s)")
    progress = "\n".join(progress_lines) + "\n\n⏳ *thinking…*"
    try:
        embed = discord.Embed(description=progress, color=discord.Color.dark_grey())
        embed.set_author(
            name=f"Replying to: {question[:100]}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as exc:  # broad: intentional — discord may be mocked in tests
        log.debug("Progress edit failed: %s", exc)


async def _ask_on_tool_call(
    tool_name: str,
    round_num: int,
    *,
    args: dict | None = None,
    result_preview: str | None = None,
    interaction: discord.Interaction,
    question: str,
    progress_lines: list[str],
    progress_start: float,
) -> None:
    elapsed = time.monotonic() - progress_start
    if result_preview is not None:
        progress_lines.append(f"✅ `{tool_name}` → {result_preview[:80]}")
    elif args is not None:
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
        progress_lines.append(f"🔄 Using `{tool_name}({args_str})`… ({elapsed:.0f}s)")
    else:
        progress_lines.append(f"🔄 Using `{tool_name}`… ({elapsed:.0f}s)")
    progress = "\n".join(progress_lines) + "\n\n⏳ *working…*"
    try:
        embed = discord.Embed(
            description=progress,
            color=discord.Color.dark_grey(),
        )
        embed.set_author(
            name=f"Replying to: {question[:100]}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as exc:  # broad: intentional — discord may be mocked in tests
        log.debug("Failed to update tool progress: %s", exc)


def _ask_update_history(
    updated_history: list[dict[str, Any]],
    *,
    conv: Any,
    interaction: discord.Interaction,
) -> None:
    conv.update_from_llm(updated_history)
    conversation_store.auto_save_thread(
        interaction.user.id, interaction.channel_id, str(interaction.user.display_name),
    )


async def _ask_handle_partial_chunk(
    chunk_text: str,
    *,
    interaction: discord.Interaction,
    display_question: str,
    last_edit_ref: list[float],
) -> None:
    now = time.monotonic()
    if now - last_edit_ref[0] < _STREAM_EDIT_INTERVAL:
        return
    try:
        preview = chunk_text[:_EMBED_LIMIT - 50] + "\n\n*⏳ streaming…*"
        embed = discord.Embed(description=preview, color=discord.Color.purple())
        embed.set_author(
            name=f"Replying to: {display_question}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        await interaction.edit_original_response(content=None, embed=embed)
        last_edit_ref[0] = now
    except Exception as exc:  # broad: intentional — discord may be mocked in tests
        log.debug("Stream edit failed: %s", exc)


async def _ask_run_retry_stream(
    retry_question: str,
    *,
    model_used: str,
    model_pref: str,
    conv: Any,
    interaction: discord.Interaction,
    context_channel_id: int | None,
    context_thread_id: int | None,
    on_tool_call: Any,
    on_partial_chunk: Any,
    update_history: Any,
    context_controls: dict[str, Any],
    routing_profile: Any,
) -> Any:
    # Phase 21: Cross-provider quality retry — if Gemini produced a low-quality
    # answer, retry with Copilot for a genuinely different response.
    _retry_pref = (
        "copilot" if (model_used or "").startswith("gemini") else model_pref
    )
    return await run_ask_stream(
        llm_stream=llm_chat_stream,
        user_message=retry_question,
        history=conv.history,
        user_name=str(interaction.user.display_name),
        model_preference=_retry_pref,
        channel_id=context_channel_id,
        thread_id=context_thread_id,
        user_id=str(interaction.user.id),
        on_tool_call=on_tool_call,
        on_partial_chunk=on_partial_chunk,
        update_history=update_history,
        context_controls=context_controls,
        routing_profile=routing_profile,
    )


def _ask_build_footer(
    chunk_idx: int = 0,
    total_chunks: int = 1,
    *,
    ask_start: float,
    model_used: str,
    conv: Any,
    context_explainability_note: str,
    routing_notes: list[str],
) -> str:
    elapsed = time.monotonic() - ask_start
    display_model = model_used.replace("models/", "") if model_used else "unknown"
    if "gemini" not in display_model.lower() and "gpt" not in display_model.lower() and "claude" not in display_model.lower():
        rate_str = "local · unlimited"
    else:
        rate_str = get_rate_info()
    if "gemma" in display_model.lower() or "ollama" in display_model.lower():
        actual_icon = "🏠"
    elif "gemini" in display_model.lower():
        actual_icon = "☁️"
    elif "gpt" in display_model.lower() or "openai" in display_model.lower():
        actual_icon = "🟢"
    elif "claude" in display_model.lower() or "anthropic" in display_model.lower():
        actual_icon = "🟣"
    else:
        actual_icon = "🔄"
    chunk_str = f"[{chunk_idx + 1}/{total_chunks}] • " if total_chunks > 1 else ""
    ft = f"{chunk_str}💬 {conv.message_count} msgs | {rate_str} | {actual_icon} {display_model} | ⏱ {elapsed:.1f}s"
    ft = _append_explainability_footer(ft, context_explainability_note)
    if routing_notes:
        ft += " | ⚠️ " + " → ".join(routing_notes)
    return ft


async def _ask_post_response_learning(
    *,
    context_channel_id: int | None,
    context_thread_id: int | None,
    conv: Any,
    question: str,
    interaction: discord.Interaction,
    response_text: str,
) -> None:
    from runtime_state import request_context
    with request_context(channel_id=context_channel_id, thread_id=context_thread_id):
        try:
            from rules_engine import add_rule, detect_correction, extract_rule
            if detect_correction(question):
                prev_bot_msg = ""
                for msg in reversed(conv.history[:-1]):
                    if msg.get("role") == "model":
                        parts = msg.get("parts", [])
                        prev_bot_msg = " ".join(p for p in parts if isinstance(p, str))[:500]
                        break
                if prev_bot_msg:
                    rule = await extract_rule(question, prev_bot_msg)
                    if rule:
                        await add_rule(rule, question[:300])
                        try:
                            await interaction.followup.send(
                                f"📝 Got it — I'll remember: *{rule}*", ephemeral=True
                            )
                        except discord.HTTPException as exc:
                            log.debug("Correction followup send failed: %s", exc)
        except (ImportError, AttributeError, ValueError) as e:
            log.debug("Correction detection failed (non-critical): %s", e)

        try:
            from user_profile import learn_from_message
            await learn_from_message(question, response_text)
        except (ImportError, AttributeError, ValueError) as e:
            log.debug("Profile learning failed (non-critical): %s", e)

        try:
            from fact_extractor import extract_and_store_facts, should_extract
            if should_extract(interaction.user.id, question):
                await extract_and_store_facts(question, response_text, interaction.user.id)
        except (ImportError, AttributeError, ValueError) as e:
            log.debug("Fact extraction failed (non-critical): %s", e)

        try:
            from goal_tracker import detect_goal, extract_and_store_goal
            if detect_goal(question):
                goal = await extract_and_store_goal(question, interaction.user.id)
                if goal:
                    try:
                        await interaction.followup.send(
                            f"🎯 Tracking goal: *{goal}*", ephemeral=True
                        )
                    except discord.HTTPException as exc:
                        log.debug("Goal followup send failed: %s", exc)
        except (ImportError, AttributeError, ValueError) as e:
            log.debug("Goal tracking failed (non-critical): %s", e)


# ---------------------------------------------------------------------------

async def _ask_prepare_response_payload(
    *,
    response_text: str,
    model_used: str,
    question: str,
    trace_id: str,
    routing_notes: list[str],
    guardrail_note: str,
    thread_hint: str,
    ask_start: float,
    context_channel_id: int | None,
    context_thread_id: int | None,
    final_meta: dict[str, Any],
    interaction: discord.Interaction,
    stream_result: Any,
    conv: Any,
) -> dict[str, Any]:
    """Post-LLM response preparation: detection, telemetry, formatting, chunking, actions.

    Returns a dict with keys: response_text, model_used, routing_notes,
    table_image_file, chunks, image_url, file_attachment, force_file_response, action_view.
    """
    # Empty/useless response detection
    if response_text and model_used != "error":
        stripped = response_text.strip()
        is_empty = len(stripped) < 10
        is_echo = stripped.lower().replace("'", "").replace('"', "") == question.lower().replace("'", "").replace('"', "")[:len(stripped)]
        if is_empty or is_echo:
            log.warning("Empty/echo response detected for: %.80s (response: %.80s)", question, stripped)
            response_text = (
                f"⚠️ I wasn't able to generate a useful response for this query.\n\n"
                f"**What happened:** The model returned {'an empty response' if is_empty else 'your question echoed back'}.\n"
                f"**Trace ID:** `{trace_id}`\n"
                f"**Suggestion:** Try rephrasing, or use `/ask model:gemini` to force Gemini with tools.\n\n"
                f"```\n{question[:300]}\n```"
            )
            routing_notes.append("Empty/echo response detected")
            model_used = "error"

    if guardrail_note:
        response_text += guardrail_note
    if thread_hint:
        response_text += thread_hint

    if model_used and model_used not in ("error", "timeout", "unknown"):
        _attr_model = model_used.replace("models/", "")
        response_text += f"\n\n*🤖 Model: {_attr_model}*"

    # Routing telemetry audit record
    try:
        from llm import telemetry as _telemetry
        _telem_provider = model_used.split("/")[0] if model_used not in ("error", "timeout", "unknown") else model_used
        _telem_latency = (time.monotonic() - ask_start) * 1000
        _telemetry.record(
            provider=_telem_provider,
            model=model_used,
            latency_ms=_telem_latency,
            success=model_used not in ("error", "timeout"),
            query_type=routing_notes[0] if routing_notes else "unknown",
            retry_count=getattr(stream_result, "retry_count", 0),
        )
    except Exception as _telem_exc:  # broad: intentional
        log.debug("Telemetry record failed: %s", _telem_exc)

    response_text = _strip_placeholder_table_rows(response_text)

    # Optional image fallback for large/complex tables
    table_image_file = None
    try:
        from table_renderer import extract_table_text, render_table_image, should_render_table_image
        table_text = extract_table_text(response_text)
        if table_text and should_render_table_image(table_text):
            img_bytes = render_table_image(table_text)
            if img_bytes:
                table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
    except (ImportError, OSError, ValueError, AttributeError) as e:
        log.debug("Table image rendering failed: %s", e)

    response_text = _format_markdown_for_discord(response_text)
    response_text = _format_tables_for_context(
        response_text,
        channel_id=context_channel_id,
        thread_id=context_thread_id,
    )
    if context_channel_id is not None and response_text.strip():
        anchor_id = f"ask_{int(time.time())}_{interaction.id}"
        set_anchor_state(
            int(context_channel_id),
            int(context_thread_id) if context_thread_id is not None else None,
            anchor_id,
        )
    chunks = _split_response(response_text)
    image_url = _extract_image_url(response_text)
    file_attachment = _extract_file_attachment(response_text)
    force_file_response = _should_prefer_file_for_multichunk_response(
        question=question,
        chunks=chunks,
        response_text=response_text,
    )

    follow_ups = await _generate_follow_ups(question, response_text)
    action_view = ResponseActions(
        response_text=response_text,
        question=question,
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        thread_id=context_thread_id,
        follow_ups=follow_ups,
        bot=None,
        show_download=len(chunks) > 3,
    )

    return {
        "response_text": response_text,
        "model_used": model_used,
        "routing_notes": routing_notes,
        "table_image_file": table_image_file,
        "chunks": chunks,
        "image_url": image_url,
        "file_attachment": file_attachment,
        "force_file_response": force_file_response,
        "action_view": action_view,
    }


# ---------------------------------------------------------------------------

async def _ask_deliver_response(
    *,
    interaction: discord.Interaction,
    response_text: str,
    question: str,
    chunks: list[str],
    image_url: str | None,
    file_attachment: Any,
    action_view: Any,
    table_image_file: Any,
    build_footer: Any,
    force_file_response: bool,
    final_meta: dict[str, Any],
) -> None:
    """Send the final response to Discord — handles file, auto-thread, and chunked embed paths."""
    _auto_thread = None
    if (
        cfg.thread_auto_create
        and not isinstance(interaction.channel, discord.Thread)
        and not isinstance(interaction.channel, discord.DMChannel)
        and hasattr(interaction.channel, "create_thread")
    ):
        try:
            _thread_name = question[:50].strip() + ("…" if len(question) > 50 else "")
            _archive_dur = 60 if cfg.thread_archive_minutes <= 60 else 1440
            _auto_thread = await interaction.channel.create_thread(
                name=f"💬 {_thread_name}",
                auto_archive_duration=_archive_dur,
                reason="Auto-threaded /ask conversation",
            )
            log.info("Auto-created thread '%s' for %s", _auto_thread.name, interaction.user)
        except discord.HTTPException as e:
            log.debug("Auto-thread creation failed: %s", e)

    display_question = question if len(question) < 200 else question[:197] + "..."

    # Long-response path: send as downloadable .md file
    if len(response_text) > _FILE_THRESHOLD or force_file_response:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        md_file = discord.File(
            io.BytesIO(response_text.encode()),
            filename=f"openclaw-response-{ts}.md",
        )
        summary = _build_attachment_embed_summary(
            response_text,
            coverage_summary=_build_coverage_summary_for_embed(final_meta),
            attachment_note="📎 **Full response attached as file**",
        )
        embed = discord.Embed(description=summary, color=discord.Color.purple())
        embed.set_author(
            name=f"Replying to: {display_question}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text=build_footer())
        attachments = [md_file]
        if file_attachment:
            attachments.append(file_attachment[0])
        try:
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=attachments, view=action_view,
            )
        except discord.NotFound:
            log.warning("Interaction expired, using followup for long response file")
            await interaction.followup.send(
                embed=embed, file=md_file, view=action_view,
            )

    # Normal path: split across embeds
    else:
        if _auto_thread:
            await interaction.edit_original_response(
                content=f"💬 Conversation continued in {_auto_thread.mention}",
                embed=None,
            )
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)
                is_last = i == len(chunks) - 1
                embed.set_footer(text=build_footer(chunk_idx=i, total_chunks=len(chunks)))
                send_kwargs = {"embed": embed}
                if is_last:
                    send_kwargs["view"] = action_view
                if file_attachment and is_last:
                    send_kwargs["file"] = file_attachment[0]
                await _auto_thread.send(**send_kwargs)
        else:
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)
                is_last = i == len(chunks) - 1
                embed.set_footer(text=build_footer(chunk_idx=i, total_chunks=len(chunks)))
                if i == 0:
                    kwargs: dict[str, Any] = {"content": None, "embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["attachments"] = [file_attachment[0]]
                    try:
                        await interaction.edit_original_response(**kwargs)
                    except discord.NotFound:
                        log.warning("Interaction expired, using followup for response")
                        fb_kwargs = {"embed": embed}
                        if is_last:
                            fb_kwargs["view"] = action_view
                        if file_attachment and is_last:
                            fb_kwargs["file"] = file_attachment[0]
                        await interaction.followup.send(**fb_kwargs)
                else:
                    kwargs = {"embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["file"] = file_attachment[0]
                    await interaction.followup.send(**kwargs)

    # Send table image if one was rendered
    if table_image_file:
        try:
            await interaction.followup.send(file=table_image_file)
        except discord.HTTPException as e:
            log.debug("Failed to send table image: %s", e)


# ---------------------------------------------------------------------------

async def _ask_finalize(
    *,
    interaction: discord.Interaction,
    question: str,
    response_text: str,
    model_used: str,
    routing_notes: list[str],
    trace: Any,
    ask_start: float,
    conv: Any,
    context_channel_id: int | None,
    context_thread_id: int | None,
    final_meta: dict[str, Any],
) -> None:
    """Audit, telemetry, cleanup, and fire-and-forget learning after /ask response is sent."""
    audit_log(interaction.user, "ask", detail=question[:200])

    try:
        from error_tracker import record_outcome
        explainability = final_meta.get("explainability") if isinstance(final_meta.get("explainability"), dict) else {}
        scope_mode = final_meta.get("scope_mode") or explainability.get("scope_mode")
        lock_mode = explainability.get("lock_mode")
        anchor_id = explainability.get("anchor_id")
        anchor_age = explainability.get("anchor_age_seconds")
        profile_values = explainability.get("effective_profile") or explainability.get("effective_profile_values")
        record_outcome(
            user_id=interaction.user.id,
            question=question,
            model_used=model_used,
            success=(model_used != "error"),
            error_msg=response_text if model_used == "error" else "",
            trace_id=get_trace_id(),
            response_preview=response_text[:2000],
            latency_ms=int((time.monotonic() - ask_start) * 1000),
            routing_notes=routing_notes,
            scope_mode=scope_mode,
            lock_mode=lock_mode,
            anchor_id=anchor_id,
            anchor_age=anchor_age,
            profile_values=profile_values if isinstance(profile_values, dict) else {},
            explainability=explainability if isinstance(explainability, dict) else {},
        )
    except (ImportError, AttributeError, ValueError) as exc:
        log.debug("Error tracking record failed: %s", exc)

    try:
        from spending import record_response_time
        elapsed_ms = (time.monotonic() - ask_start) * 1000
        record_response_time(elapsed_ms, model=model_used)
    except (ImportError, AttributeError, ValueError) as exc:
        log.debug("Response time tracking failed: %s", exc)

    conversation_store.cleanup_expired()

    asyncio.get_running_loop().create_task(_ask_post_response_learning(
        context_channel_id=context_channel_id,
        context_thread_id=context_thread_id,
        conv=conv,
        question=question,
        interaction=interaction,
        response_text=response_text,
    ))


# ---------------------------------------------------------------------------

async def _ask_inject_recall_and_rules(
    conv: Any,
    *,
    question: str,
    retrieval_question: str,
    context_channel_id: int | None,
    context_thread_id: int | None,
    cross_channel_retrieval: bool,
    think_hook: Any,
) -> None:
    """Inject contextual recall, learned rules, and user profile into conv history."""
    await think_hook("Recalling relevant memories…")
    try:
        import vector_store
        context_hits = await vector_store.recall(
            retrieval_question,
            top_k=3,
            channel_id=context_channel_id,
            thread_id=context_thread_id,
            cross_channel=cross_channel_retrieval,
        )
        if context_hits:
            conv.history.append({
                "role": "model",
                "parts": [f"[Relevant context from memory]\n{context_hits}"],
            })
    except Exception as e:  # broad: intentional
        log.debug("Contextual recall skipped: %s", e)

    await think_hook("Checking learned rules…")
    try:
        from rules_engine import get_relevant_rules
        rules = await get_relevant_rules(question, top_k=3)
        if rules:
            rules_block = "\n".join(f"• {r}" for r in rules)
            conv.history.append({
                "role": "model",
                "parts": [f"[Learned rules — follow these]\n{rules_block}"],
            })
    except (ImportError, AttributeError, ValueError, RuntimeError) as e:
        log.debug("Rules injection skipped: %s", e)

    try:
        from user_profile import get_profile_prompt
        profile_ctx = get_profile_prompt()
        if profile_ctx:
            conv.history.append({
                "role": "model",
                "parts": [profile_ctx],
            })
    except (ImportError, AttributeError, ValueError, RuntimeError) as e:
        log.debug("Profile injection skipped: %s", e)


# ---------------------------------------------------------------------------

async def _ask_inject_conv_context(
    conv: Any,
    *,
    interaction: discord.Interaction,
    retrieval_question: str,
    context_channel_id: int | None,
    context_thread_id: int | None,
    cross_channel_retrieval: bool,
) -> str:
    """Inject research thread context, thread hints, and channel roles into conv.

    Returns thread_hint string (empty if no hint).
    """
    # Research thread context injection
    if isinstance(interaction.channel, discord.Thread) and not conv.history:
        thread_name = interaction.channel.name or ""
        if thread_name.startswith("Research:"):
            try:
                report_text = ""
                async for msg in interaction.channel.history(limit=20, oldest_first=True):
                    if msg.embeds:
                        for embed in msg.embeds:
                            if embed.description:
                                report_text += embed.description + "\n"
                if report_text:
                    conv.history.append({
                        "role": "model",
                        "parts": [f"[Previous Research Report]\n{report_text[:8000]}"],
                    })
                    log.info("Injected research context (%d chars) for thread: %s",
                             len(report_text), thread_name)
            except (discord.HTTPException, AttributeError) as e:
                log.debug("Research context injection failed: %s", e)

    # Thread continuation suggestion
    thread_hint = ""
    if not conv.history:
        try:
            import vector_store
            hits = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION,
                retrieval_question,
                top_k=1,
                threshold=0.75,
                channel_id=context_channel_id,
                thread_id=context_thread_id,
                cross_channel=cross_channel_retrieval,
            )
            if hits:
                meta = hits[0].get("metadata", {})
                thread_name = meta.get("thread_name", "")
                sim = hits[0].get("similarity", 0)
                if thread_name and sim >= 0.75:
                    thread_hint = (
                        f"\n\n> 💡 *This looks related to your thread "
                        f"**{thread_name}**. Use `/resume {thread_name}` to continue it.*"
                    )
        except Exception as exc:  # broad: intentional
            log.debug("Thread hint search failed: %s", exc)

    # Channel role injection
    if not conv.history:
        from runtime_state import get_channel_prompts, get_channel_roles
        channel_role = get_channel_roles().get(interaction.channel_id)
        if channel_role:
            role_prompt = get_channel_prompts().get(channel_role, "")
            if role_prompt:
                conv.history.append({
                    "role": "model",
                    "parts": [f"📌 *{channel_role.capitalize()} mode active.* {role_prompt}"],
                })
                log.debug("Injected %s channel role prompt for channel %d",
                          channel_role, interaction.channel_id)

    return thread_hint


# ---------------------------------------------------------------------------

async def _build_ask_context(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None,
    model: app_commands.Choice[str] | None,
    scope: app_commands.Choice[str] | None,
    reset_context: bool | None,
    anchor: str | None,
    ask_start: float,
) -> dict[str, Any]:
    """Build per-request context: trace, scope, conv, model prefs, guardrail.

    Returns a dict with all derived state needed by the pipeline steps.
    The returned ``question`` key may differ from the input if an attachment
    rewrote the prompt.
    """
    from trace_context import TraceContext, _current_trace
    _trace = TraceContext(command="ask", user_id=interaction.user.id,
                          channel_id=interaction.channel_id)
    _trace_token = _current_trace.set(_trace)
    log.info("ask_cmd start question=%.80s", question)

    context_channel_id, context_thread_id = _resolve_channel_thread_scope(
        interaction.channel,
        interaction.channel_id,
        user_id=interaction.user.id,
    )

    _progress_lines: list[str] = []
    _progress_start = time.monotonic()

    _think = functools.partial(
        _ask_think, interaction=interaction, question=question,
        progress_lines=_progress_lines, progress_start=_progress_start,
    )
    _on_tool_call = functools.partial(
        _ask_on_tool_call, interaction=interaction, question=question,
        progress_lines=_progress_lines, progress_start=_progress_start,
    )

    if attachment:
        mime = (attachment.content_type or "").split(";")[0].strip()
        if mime in SUPPORTED_IMAGE_MIMES and attachment.size <= MAX_FILE_SIZE:
            question = await _handle_image_attachment(attachment, question)
        elif attachment.size > MAX_FILE_SIZE:
            log.info("ask_cmd: attachment too large (%d bytes), skipping", attachment.size)
        else:
            question = await _handle_doc_attachment(attachment, question)

    from llm.context import _extract_cross_channel_opt_in
    retrieval_question, cross_channel_retrieval = _extract_cross_channel_opt_in(question)

    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    thread_hint = await _ask_inject_conv_context(
        conv,
        interaction=interaction,
        retrieval_question=retrieval_question,
        context_channel_id=context_channel_id,
        context_thread_id=context_thread_id,
        cross_channel_retrieval=cross_channel_retrieval,
    )

    model_pref = model.value if model else get_model_preference(interaction.user.id)
    user_routing_profile = get_routing_profile(interaction.user.id)
    context_controls = _build_ask_context_controls(
        scope=scope.value if scope else None,
        reset_context=reset_context,
        anchor=anchor,
    )

    channel = interaction.channel
    if channel and hasattr(channel, "name"):
        context_controls["channel_name"] = channel.name
    elif channel and hasattr(channel, "parent") and channel.parent:
        context_controls["channel_name"] = channel.parent.name

    from llm import _needs_tools as llm_needs_tools
    model_pref, upgraded_to_gemini = normalize_model_preference(
        question, model_pref, llm_needs_tools,
    )
    guardrail_note = (
        "\n\n> ⚡ *Auto-upgraded to Gemini (your query requires tool access)*"
        if upgraded_to_gemini else ""
    )

    return {
        "trace": _trace,
        "trace_token": _trace_token,
        "context_channel_id": context_channel_id,
        "context_thread_id": context_thread_id,
        "progress_lines": _progress_lines,
        "progress_start": _progress_start,
        "think": _think,
        "on_tool_call": _on_tool_call,
        "question": question,
        "retrieval_question": retrieval_question,
        "cross_channel_retrieval": cross_channel_retrieval,
        "conv": conv,
        "thread_hint": thread_hint,
        "model_pref": model_pref,
        "user_routing_profile": user_routing_profile,
        "context_controls": context_controls,
        "guardrail_note": guardrail_note,
    }


async def _route_and_stream(
    *,
    question: str,
    retrieval_question: str,
    cross_channel_retrieval: bool,
    conv: Any,
    interaction: discord.Interaction,
    context_channel_id: int | None,
    context_thread_id: int | None,
    model_pref: str,
    user_routing_profile: Any,
    context_controls: dict[str, Any],
    think_hook: Any,
    on_tool_call: Any,
    progress_lines: list[str],
    progress_start: float,
    trace: Any,
) -> dict[str, Any]:
    """Inject recall/rules, call LLM, apply quality repair.

    Returns a dict with: response_text, model_used, final_meta,
    routing_notes, context_explainability_note, stream_result.
    """
    response_text = ""
    model_used = "unknown"
    result = None
    _routing_notes: list[str] = []
    _context_explainability_note = ""
    _final_meta: dict[str, Any] = {}

    try:
        await _ask_inject_recall_and_rules(
            conv,
            question=question,
            retrieval_question=retrieval_question,
            context_channel_id=context_channel_id,
            context_thread_id=context_thread_id,
            cross_channel_retrieval=cross_channel_retrieval,
            think_hook=think_hook,
        )

        _model_labels = {
            "auto": "smart routing", "local": "Gemma (local)", "gemini": "Gemini",
            "openai": "GPT-4o", "anthropic": "Claude", "copilot": "Copilot",
        }
        await think_hook(f"Routing to {_model_labels.get(model_pref, model_pref)}…")
        _last_edit_ref: list[float] = [0.0]
        display_question = question if len(question) < 200 else question[:197] + "..."

        try:
            _update_history = functools.partial(
                _ask_update_history, conv=conv, interaction=interaction,
            )
            _handle_partial_chunk = functools.partial(
                _ask_handle_partial_chunk, interaction=interaction,
                display_question=display_question, last_edit_ref=_last_edit_ref,
            )

            result = await run_ask_stream(
                llm_stream=llm_chat_stream,
                user_message=question,
                history=conv.history,
                user_name=str(interaction.user.display_name),
                model_preference=model_pref,
                channel_id=context_channel_id,
                thread_id=context_thread_id,
                user_id=str(interaction.user.id),
                on_tool_call=on_tool_call,
                on_partial_chunk=_handle_partial_chunk,
                update_history=_update_history,
                context_controls=context_controls,
                routing_profile=user_routing_profile,
            )
            response_text = result.response_text
            model_used = result.model_used
            _final_meta = result.final_meta
            _final_meta = _with_requested_item_target(_final_meta, question=question)
            _context_explainability_note = _explainability_note_from_meta(_final_meta)
            _routing_notes.extend(result.routing_notes)
            if not _context_explainability_note:
                _routing_notes.extend(result.context_badges)

            _is_memory_store = bool(_MEMORY_STORE_RE.search(question))
            quality_meta = _safe_score_answer_quality(
                response_text, final_meta=_final_meta, context="ask",
            )
            _run_retry_stream = functools.partial(
                _ask_run_retry_stream,
                model_used=model_used, model_pref=model_pref,
                conv=conv, interaction=interaction,
                context_channel_id=context_channel_id, context_thread_id=context_thread_id,
                on_tool_call=on_tool_call, on_partial_chunk=_handle_partial_chunk,
                update_history=_update_history, context_controls=context_controls,
                routing_profile=user_routing_profile,
            )

            repair_result = await _run_quality_auto_repair(
                question=question,
                response_text=response_text,
                model_used=model_used,
                final_meta=_final_meta,
                quality_meta=quality_meta,
                context="ask",
                run_retry_stream=_run_retry_stream,
                think_hook=think_hook,
            )
            response_text = str(repair_result["response_text"])
            model_used = str(repair_result["model_used"])
            _final_meta = dict(repair_result["final_meta"])
            retry_result = repair_result.get("retry_result")
            if retry_result is not None:
                _context_explainability_note = _explainability_note_from_meta(_final_meta)
                _routing_notes = list(retry_result.routing_notes)
                if not _context_explainability_note:
                    _routing_notes.extend(retry_result.context_badges)

            final_quality = _final_meta.get("answer_quality")
            if isinstance(final_quality, dict) and final_quality.get("status") == "low":
                _routing_notes.append("Quality: low confidence")
            recovery_block = _build_ask_recovery_block(_final_meta)
            if model_used == "perplexity-direct":
                recovery_block = None
            if recovery_block and "Recovery note" not in response_text and not _is_memory_store:
                # W4-1: Strip duplicate sources — if response already has a Sources section,
                # remove it from the recovery block before appending.
                if re.search(r'(?:\*\*Sources\*\*|Sources)\s*:', response_text, re.IGNORECASE):
                    recovery_block = re.sub(
                        r'\n{0,2}(?:\*\*Sources\*\*|Sources):?\s*\n(?:(?:[-*]|\d+\.)\s+.+\n?)+',
                        '',
                        recovery_block,
                        flags=re.IGNORECASE,
                    ).rstrip()
                if recovery_block:
                    response_text = f"{response_text.rstrip()}{recovery_block}"
            log.info("ask_cmd LLM done model=%s chars=%d", model_used, len(response_text))

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - progress_start
            log.warning("LLM response timed out after %.0fs for: %.80s", elapsed, question)
            response_text = _build_ask_timeout_message(
                elapsed_seconds=elapsed,
                progress_lines=progress_lines,
                model_pref=model_pref,
                trace_id=trace.trace_id,
            )
            model_used = "timeout"

    except Exception as e:  # broad: intentional
        log.error("LLM error: %s", e)
        response_text = _build_ask_failure_message(
            question=question,
            model_pref=model_pref,
            trace_id=trace.trace_id,
            category=_classify_ask_failure(str(e)),
        )
        model_used = "error"

    return {
        "response_text": response_text,
        "model_used": model_used,
        "final_meta": _final_meta,
        "routing_notes": _routing_notes,
        "context_explainability_note": _context_explainability_note,
        "stream_result": result,
    }


# ---------------------------------------------------------------------------

async def handle_ask(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None = None,
    model: app_commands.Choice[str] | None = None,
    scope: app_commands.Choice[str] | None = None,
    reset_context: bool | None = None,
    anchor: str | None = None,
) -> None:
    """Main /ask handler — extracted from bot.py for modularity."""

    if is_emergency_stopped():
        await interaction.response.send_message(
            "🛑 **Emergency stop is active.** `/ask` is disabled. Use `/estop resume` to resume.",
            ephemeral=True,
        )
        return

    if not llm_is_configured():
        await interaction.response.send_message(
            "⚠️ LLM not configured. Set `GOOGLE_API_KEY` (Gemini), `COPILOT_PROXY_URL` (Copilot), or `LOCAL_LLM_ENABLED=true` in your `.env` file.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    _ask_start = time.monotonic()

    ctx = await _build_ask_context(
        interaction=interaction,
        question=question,
        attachment=attachment,
        model=model,
        scope=scope,
        reset_context=reset_context,
        anchor=anchor,
        ask_start=_ask_start,
    )
    question = ctx["question"]

    stream = await _route_and_stream(
        question=question,
        retrieval_question=ctx["retrieval_question"],
        cross_channel_retrieval=ctx["cross_channel_retrieval"],
        conv=ctx["conv"],
        interaction=interaction,
        context_channel_id=ctx["context_channel_id"],
        context_thread_id=ctx["context_thread_id"],
        model_pref=ctx["model_pref"],
        user_routing_profile=ctx["user_routing_profile"],
        context_controls=ctx["context_controls"],
        think_hook=ctx["think"],
        on_tool_call=ctx["on_tool_call"],
        progress_lines=ctx["progress_lines"],
        progress_start=ctx["progress_start"],
        trace=ctx["trace"],
    )

    _payload = await _ask_prepare_response_payload(
        response_text=stream["response_text"],
        model_used=stream["model_used"],
        question=question,
        trace_id=ctx["trace"].trace_id,
        routing_notes=stream["routing_notes"],
        guardrail_note=ctx["guardrail_note"],
        thread_hint=ctx["thread_hint"],
        ask_start=_ask_start,
        context_channel_id=ctx["context_channel_id"],
        context_thread_id=ctx["context_thread_id"],
        final_meta=stream["final_meta"],
        interaction=interaction,
        stream_result=stream["stream_result"],
        conv=ctx["conv"],
    )

    _build_footer = functools.partial(
        _ask_build_footer,
        ask_start=_ask_start,
        model_used=_payload["model_used"],
        conv=ctx["conv"],
        context_explainability_note=stream["context_explainability_note"],
        routing_notes=_payload["routing_notes"],
    )

    await _ask_deliver_response(
        interaction=interaction,
        response_text=_payload["response_text"],
        question=question,
        chunks=_payload["chunks"],
        image_url=_payload["image_url"],
        file_attachment=_payload["file_attachment"],
        action_view=_payload["action_view"],
        table_image_file=_payload["table_image_file"],
        build_footer=_build_footer,
        force_file_response=_payload["force_file_response"],
        final_meta=stream["final_meta"],
    )

    await _ask_finalize(
        interaction=interaction,
        question=question,
        response_text=_payload["response_text"],
        model_used=_payload["model_used"],
        routing_notes=_payload["routing_notes"],
        trace=ctx["trace"],
        ask_start=_ask_start,
        conv=ctx["conv"],
        context_channel_id=ctx["context_channel_id"],
        context_thread_id=ctx["context_thread_id"],
        final_meta=stream["final_meta"],
    )


async def handle_metrics(interaction: discord.Interaction) -> None:
    """Handler for /metrics — shows last 20 routing telemetry entries."""
    await interaction.response.defer(ephemeral=True)
    from llm import telemetry as _telemetry
    records = _telemetry.tail(20)
    summary = _telemetry.summarise(records)
    await interaction.followup.send(summary, ephemeral=True)
