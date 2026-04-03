"""
Core LLM chat logic — chat(), chat_stream(), chat_deep(), summarize_conversation().
"""

import asyncio
import logging
from typing import Any

from google import genai

from llm_client import (
    GOOGLE_API_KEY,
    LOCAL_LLM_ENABLED,
    MAX_TOKENS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
    OLLAMA_MODEL,
    TEMPERATURE,
    _client,
    _get_model,
    _get_thinking_model,
    _load_system_prompt,
    _ModelConfig,
    _record_usage,
)
from llm_patterns import (
    _FACTUAL_QUESTION_RE,
    _VAGUE_RESPONSE_RE,
    _gemma_response_seems_valid,
    _needs_tools,
    _reflect_on_response,
)
from llm_ratelimit import rate_limiter as _rate_limiter
from llm_tools import _extract_final_text, _extract_history, _run_tool_loop
from skills import SKILLS
from trace_context import get_trace_id

from .context import (
    _auto_recall_context,
    _strip_recalled_prefix,
    _to_content,
    _trim_history,
)
from .tool_execution import _ollama_available, _try_local_model

log = logging.getLogger("openclaw.llm")


async def _gemini_chat(
    user_message: str,
    history: list[dict],
    model: _ModelConfig,
    *,
    on_tool_call: Any | None = None,
    parallel_tools: bool = True,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    label: str = "LLM",
) -> tuple[str, list[dict], str]:
    """Common Gemini chat path: rate-limit, send, tool-loop, extract text.

    Returns (response_text, updated_history, model_name).
    """
    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history,
            model.model_name if hasattr(model, "model_name") else "unknown",
        )

    gemini_history = [_to_content(msg) for msg in history]

    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    loop = asyncio.get_running_loop()
    _rate_limiter.record()
    response = await loop.run_in_executor(
        None, lambda: chat_session.send_message(user_message)
    )
    await _record_usage(response)

    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=max_tool_rounds,
        on_tool_call=on_tool_call,
        parallel=parallel_tools,
        label=label,
    )

    text = _extract_final_text(response, rounds, chat_session)
    text = await _reflect_on_response(text, user_message, rounds)

    updated_history = _extract_history(chat_session)
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    return text, updated_history, model_name


async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
):
    """Async generator yielding ``(chunk_text, is_final, metadata)`` tuples."""
    log.info("LLM chat_stream start model_pref=%s trace=%s msg=%.60s",
             model_preference, get_trace_id(), user_message)
    if model_preference == "local":
        _model_hint = "ollama"
    elif model_preference == "gemini":
        _model_hint = "gemini"
    else:
        _model_hint = "gemini"
    history = await _trim_history(history or [], model_hint=_model_hint)

    _routing_notes: list[str] = []

    recalled_context = await _auto_recall_context(user_message)
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {user_message}"
    else:
        model_message = user_message

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
                ollama_alive=_ollama_up,
            )

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False}
                    return

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}", "updated_history": updated, "needs_tools": False}
                    return
        except Exception as e:
            log.debug("Multi-model routing failed (non-fatal, stream): %s", e)

    # Forced OpenAI / Anthropic mode
    if model_preference in ("openai", "anthropic"):
        try:
            import os

            from model_router import chat_anthropic, chat_openai
            system_prompt = _load_system_prompt()
            if model_preference == "openai":
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
            else:
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
            if reply:
                updated = history + [
                    {"role": "user", "parts": [user_message]},
                    {"role": "model", "parts": [reply]},
                ]
                yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False}
                return
            log.info("%s call failed, falling back to Gemini", model_preference)
        except Exception as e:
            log.info("%s call failed, falling back to Gemini: %s", model_preference, e)

    # Forced local mode
    if model_preference == "local":
        if not LOCAL_LLM_ENABLED:
            yield "⚠️ Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        if not await _ollama_available():
            yield "⚠️ Ollama is not reachable. Check that the service is running.", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        gemma_reply = await _try_local_model(model_message, history, force=True)
        if gemma_reply is not None:
            updated = history + [
                {"role": "user", "parts": [user_message]},
                {"role": "model", "parts": [gemma_reply]},
            ]
            yield gemma_reply, True, {"model_used": OLLAMA_MODEL, "updated_history": updated, "needs_tools": False}
            return
        log.info("Local model returned empty, auto-falling back to Gemini")

    # Forced Gemini mode
    if model_preference in ("gemini", "local"):
        if not GOOGLE_API_KEY:
            yield "⚠️ Gemini API key not configured (`GOOGLE_API_KEY`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
    else:
        pass

    # Rate-limit pre-check
    if not _rate_limiter.check():
        try:
            from model_router import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply and _gemma_response_seems_valid(reply):
                    import os
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    _routing_notes.append("Gemini rate-limited → used Copilot proxy")
                    yield reply, True, {"model_used": f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False, "routing_notes": _routing_notes}
                    return
        except (ImportError, KeyError) as exc:
            log.debug("Copilot proxy fallback unavailable: %s", exc)
        except Exception as exc:
            log.warning("Copilot proxy fallback failed (rate-limit recovery): %s", exc)
        msg = (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)"
        )
        yield msg, True, {"model_used": MODEL_NAME, "updated_history": history, "needs_tools": False}
        return

    model = await _get_model()
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    text, updated_history, model_name = await _gemini_chat(
        model_message, history, model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)

    if not _gemma_response_seems_valid(text):
        log.warning("Post-response hallucination detected, retrying with explicit tool instruction")
        retry_msg = (
            f"{model_message}\n\n"
            "IMPORTANT: You have tool access. Do NOT say 'let me search' or 'one moment'. "
            "USE the available tools (e.g. nas_list_folder, search_web, browse_url) to "
            "find the answer, then respond with the actual results."
        )
        text, updated_history, model_name = await _gemini_chat(
            retry_msg, history, model,
            on_tool_call=on_tool_call,
            parallel_tools=True,
            label="LLM-retry",
        )
        updated_history = _strip_recalled_prefix(updated_history, user_message, retry_msg)

    # Auto-escalate vague responses to web search
    if (
        _VAGUE_RESPONSE_RE.search(text)
        and _FACTUAL_QUESTION_RE.search(user_message.strip())
    ):
        log.info("Auto-escalating to web search for: %s", user_message)
        search_fn = SKILLS.get("search_web")
        if search_fn is not None:
            try:
                search_results = await search_fn(user_message)
                if search_results and search_results.strip():
                    enhanced_msg = (
                        f"{model_message}\n\n"
                        "Here are fresh web search results to help answer the question:\n"
                        f"{search_results}\n\n"
                        "Use these results to give a thorough, factual answer."
                    )
                    text, updated_history, model_name = await _gemini_chat(
                        enhanced_msg, history, model,
                        on_tool_call=on_tool_call,
                        parallel_tools=True,
                        label="LLM-escalate",
                    )
                    updated_history = _strip_recalled_prefix(
                        updated_history, user_message, enhanced_msg,
                    )
            except Exception as exc:
                log.warning("Auto-escalation web search failed: %s", exc)

    yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": True, "routing_notes": _routing_notes}
    return

    # No-tool Gemini streaming path (dead code kept for future use)
    gemini_history = [_to_content(msg) for msg in history]
    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    loop = asyncio.get_running_loop()
    _rate_limiter.record()

    try:
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message_stream(model_message)
        )
    except Exception as e:
        yield f"❌ **LLM Error:** {e}", True, {"model_used": model_name, "updated_history": history, "needs_tools": False, "routing_notes": _routing_notes}
        return

    accumulated = ""
    last_chunk = None
    try:
        for chunk in response:
            last_chunk = chunk
            try:
                text = chunk.text
            except (ValueError, AttributeError):
                continue
            if text:
                accumulated += text
                yield accumulated, False, {"model_used": model_name, "needs_tools": False}
    except Exception as e:
        if not accumulated:
            accumulated = f"❌ Streaming error: {e}"

    if last_chunk is not None:
        try:
            await _record_usage(last_chunk)
        except Exception as exc:
            log.debug("Stream usage recording failed: %s", exc)

    updated_history = _extract_history(chat_session)
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
    yield accumulated, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": False, "routing_notes": _routing_notes}


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
) -> tuple[str, list[dict], str]:
    """
    Send a message and return (response_text, updated_history, model_used).

    ``on_tool_call(tool_name, round_num)`` is an optional async callback invoked
    before each tool execution.

    *model_preference* controls routing:
      - ``"auto"``  — Copilot proxy first (free), then Gemini with tools
      - ``"local"`` — force Ollama/Gemma; error if unavailable
      - ``"gemini"`` — skip everything, go straight to Gemini
    """
    log.info("LLM chat start model_pref=%s trace=%s msg=%.60s",
             model_preference, get_trace_id(), user_message)
    if model_preference == "local":
        _model_hint = "ollama"
    elif model_preference == "gemini":
        _model_hint = "gemini"
    else:
        _model_hint = "gemini"
    history = await _trim_history(history or [], model_hint=_model_hint)

    recalled_context = await _auto_recall_context(user_message)
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {user_message}"
    else:
        model_message = user_message

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
                ollama_alive=_ollama_up,
            )
            log.debug("Model router: %s", route)

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
                log.info("OpenAI call failed, falling through to default routing")

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
                log.info("Anthropic call failed, falling through to default routing")
        except Exception as e:
            log.debug("Multi-model routing failed (non-fatal): %s", e)

    # Forced OpenAI / Anthropic mode
    if model_preference in ("openai", "anthropic"):
        try:
            import os

            from model_router import chat_anthropic, chat_openai
            system_prompt = _load_system_prompt()
            if model_preference == "openai":
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
            else:
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
            if reply:
                updated = history + [
                    {"role": "user", "parts": [user_message]},
                    {"role": "model", "parts": [reply]},
                ]
                return reply, updated, model_label
            log.info("%s call failed, falling back to Gemini", model_preference)
        except Exception as e:
            log.info("%s call failed, falling back to Gemini: %s", model_preference, e)

    # Forced local mode
    if model_preference == "local":
        if not LOCAL_LLM_ENABLED:
            return "⚠️ Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", history, "none"
        if not await _ollama_available():
            return "⚠️ Ollama is not reachable. Check that the service is running.", history, "none"
        gemma_reply = await _try_local_model(model_message, history, force=True)
        if gemma_reply is not None:
            updated = history + [
                {"role": "user", "parts": [user_message]},
                {"role": "model", "parts": [gemma_reply]},
            ]
            return gemma_reply, updated, OLLAMA_MODEL
        log.info("Local model returned empty, auto-falling back to Gemini")

    # Forced Gemini mode
    if model_preference in ("gemini", "local"):
        if not GOOGLE_API_KEY:
            return "⚠️ Gemini API key not configured (`GOOGLE_API_KEY`).", history, "none"
        if not _rate_limiter.check():
            return (
                "⚠️ Rate limit reached. Please wait a moment before asking again. "
                f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
                history,
                MODEL_NAME,
            )
        model = await _get_model()
        text, updated_history, model_name = await _gemini_chat(
            model_message, history, model,
            on_tool_call=on_tool_call, parallel_tools=True, label="LLM",
        )
        updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
        return text, updated_history, model_name

    # Auto mode: Copilot for simple queries, Gemini for tool queries
    if not _needs_tools(user_message):
        try:
            from model_router import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    import os
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
                log.info("Copilot proxy failed, falling through to Gemini")
        except Exception as e:
            log.debug("Copilot proxy failed: %s", e)

    # Gemini path
    if not _rate_limiter.check():
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history,
            MODEL_NAME,
        )

    model = await _get_model()
    text, updated_history, model_name = await _gemini_chat(
        model_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
    return text, updated_history, model_name


def is_configured() -> bool:
    """Return True if a Google API key is set (Gemini) OR local LLM is enabled."""
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED


def get_rate_info() -> str:
    """Return a human-readable rate limit status for Gemini Flash."""
    return f"{_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining"


async def chat_deep(
    user_message: str,
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """Deep research chat — always uses Gemini with extended thinking.

    Returns (response_text, updated_history).
    """
    history = history or []

    try:
        model = _get_thinking_model()
    except Exception as exc:
        log.warning("Thinking model unavailable, falling back to standard model: %s", exc)
        model = await _get_model()

    text, updated_history, _ = await _gemini_chat(
        user_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=False,
        max_tool_rounds=MAX_TOOL_ROUNDS * 2,
        label="Deep research",
    )

    return text, updated_history


async def summarize_conversation(history: list[dict]) -> str:
    """Produce a 3-5 sentence summary of a conversation history for long-term memory."""
    if not GOOGLE_API_KEY or not history:
        return ""

    lines = []
    for msg in history[-20:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = " ".join(str(p) for p in msg["parts"] if isinstance(p, str))[:200]
        if content:
            lines.append(f"{role}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    prompt = (
        "Summarize the following conversation in 3-5 concise sentences. "
        "Capture the main topics, any decisions made, and key facts mentioned. "
        "Write in third person (e.g. 'The user asked about...').\n\n"
        f"Conversation:\n{transcript}"
    )

    try:
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.2,
            ),
        )
        return response.text.strip()
    except Exception as e:
        log.warning("Failed to summarize conversation: %s", e)
        return ""
