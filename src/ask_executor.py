"""Self-contained executor for streaming agent `ask` calls.

Extracted from the now-deleted `dashboard.api_handlers` module so that
`slack_bot.py` no longer has a Slack→dashboard import dependency.

The single entry point, :func:`execute_agent_ask`, runs the LLM stream,
applies quality scoring + auto-repair via :mod:`quality_helpers`, and
returns a payload suitable for posting back to a Slack thread.
"""

from __future__ import annotations

import time
from typing import Callable


async def execute_agent_ask(
    *,
    prompt: str,
    model_pref: str,
    history: list[dict],
    user_name: str,
    routing_profile: str = "",
    on_partial_chunk: Callable | None = None,
) -> dict[str, object]:
    """Run a single agent ask end-to-end.

    Args:
        prompt: User-visible question.
        model_pref: Model preference key (`gemini`, `copilot`, …).
        history: Conversation history list of `{role, content}` dicts.
        user_name: Display name for the requesting user.
        routing_profile: Optional routing profile override.
        on_partial_chunk: Optional async callback receiving streamed deltas.

    Returns:
        Dict with `response`, `model`, and `tokens` keys.
    """
    from ask_orchestrator import run_ask_stream
    from error_tracker import journal_ask_outcome
    from llm import chat_stream as llm_chat_stream
    from quality_helpers import (
        _build_ask_recovery_block,
        _run_quality_auto_repair,
        _safe_score_answer_quality,
        _with_requested_item_target,
    )

    _ask_t0 = time.monotonic()
    latest_history = list(history)
    last_partial = ""

    def _update_history(updated_history: list[dict]) -> None:
        nonlocal latest_history
        latest_history = updated_history

    async def _handle_partial(chunk_text: str) -> None:
        nonlocal last_partial
        if on_partial_chunk is None:
            return
        text = str(chunk_text or "")
        delta = text[len(last_partial) :] if last_partial and text.startswith(last_partial) else text
        last_partial = text
        if delta:
            await on_partial_chunk(delta)

    result = await run_ask_stream(
        llm_stream=llm_chat_stream,
        user_message=prompt,
        history=history,
        user_name=user_name,
        model_preference=model_pref,
        channel_id=None,
        thread_id=None,
        user_id=user_name,
        update_history=_update_history,
        context_controls=None,
        routing_profile=routing_profile,
        on_partial_chunk=_handle_partial if on_partial_chunk is not None else None,
    )
    response_text = str(result.response_text or "").strip()
    model_used = str(result.model_used or model_pref)
    final_meta = _with_requested_item_target(result.final_meta, question=prompt)
    quality_meta = _safe_score_answer_quality(
        response_text,
        final_meta=final_meta,
        context="ask",
    )

    async def _run_retry_stream(retry_prompt: str):
        _retry_pref = "copilot" if (model_used or "").startswith("gemini") else model_pref
        return await run_ask_stream(
            llm_stream=llm_chat_stream,
            user_message=retry_prompt,
            history=latest_history,
            user_name=user_name,
            model_preference=_retry_pref,
            channel_id=None,
            thread_id=None,
            user_id=user_name,
            update_history=_update_history,
            context_controls=None,
        )

    repair_result = await _run_quality_auto_repair(
        question=prompt,
        response_text=response_text,
        model_used=model_used,
        final_meta=final_meta,
        quality_meta=quality_meta,
        context="ask",
        run_retry_stream=_run_retry_stream,
        think_hook=None,
    )
    response_text = str(repair_result["response_text"])
    model_used = str(repair_result["model_used"])
    final_meta = dict(repair_result["final_meta"])

    recovery_block = _build_ask_recovery_block(final_meta)
    if recovery_block and "Recovery note" not in response_text:
        response_text = f"{response_text.rstrip()}{recovery_block}"

    tokens_raw = final_meta.get("total_tokens", 0) if isinstance(final_meta, dict) else 0
    try:
        tokens = int(tokens_raw or 0)
    except (TypeError, ValueError):
        tokens = 0

    journal_ask_outcome(
        question=prompt,
        response_text=response_text,
        model_used=model_used,
        final_meta=final_meta,
        success=bool(response_text),
        latency_ms=int((time.monotonic() - _ask_t0) * 1000),
    )

    return {
        "response": response_text,
        "model": model_used,
        "tokens": tokens,
    }


# Backwards-compat alias for the previous private name.
_execute_agent_ask = execute_agent_ask
