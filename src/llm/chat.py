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
    _build_model_for_tools,
    _client,
    _get_model,
    _get_thinking_model,
    _get_tool_declarations,
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
from tool_router import route_tool_declarations
from trace_context import get_trace_id

from .context import (
    _auto_recall_context,
    _extract_context_controls,
    _extract_cross_channel_opt_in,
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


async def _select_model_for_message(
    user_message: str,
    *,
    tool_declarations: list[dict[str, Any]] | None = None,
    label: str = "LLM",
) -> tuple[_ModelConfig, dict[str, Any]]:
    """Return a model configured with the best-fit tool declarations."""
    if tool_declarations is not None:
        route_info = {
            "strategy": "no-tools" if not tool_declarations else "caller-supplied",
            "selected": [str(d.get("name", "")) for d in tool_declarations],
            "top_score": None,
        }
        return _build_model_for_tools(tool_declarations), route_info

    declarations = _get_tool_declarations()
    routed_declarations, route_info = route_tool_declarations(user_message, declarations)
    if route_info.get("strategy") == "fallback-full":
        log.debug("%s tool routing fell back to the full declaration set", label)
        return await _get_model(), route_info

    selected_names = ", ".join(route_info.get("selected", [])[:8])
    log.info("%s tool shortlist: %s", label, selected_names)
    return _build_model_for_tools(routed_declarations), route_info


def _apply_route_hints(model_message: str, route_info: dict[str, Any]) -> str:
    if route_info.get("strategy") not in {"shortlist", "pack-filter"}:
        return model_message

    bundles = [str(item) for item in (route_info.get("bundles") or []) if item]
    hints = route_info.get("hints") or {}
    if not bundles and not hints:
        return model_message

    lines: list[str] = []
    if bundles:
        lines.append(f"- Likely workflow: {', '.join(bundles)}")

    for key in (
        "services",
        "sport",
        "league",
        "team",
        "days",
        "timeframe",
        "report_topic",
        "output_style",
        "emoji_level",
        "detail_level",
        "pack",
        "persona",
    ):
        value = hints.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")

    if not lines:
        return model_message

    hint_block = (
        "Routing hints inferred from the user's wording:\n"
        + "\n".join(lines)
        + "\nUse these hints when choosing tools and parameters, but do not contradict the user's actual request.\n\n"
    )
    return hint_block + model_message


async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
    tool_declarations: list[dict[str, Any]] | None = None,
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

    cleaned_user_message, cross_channel = _extract_cross_channel_opt_in(user_message)
    cleaned_user_message, context_controls = _extract_context_controls(cleaned_user_message)
    # --- hard-context-boundaries and followup-anchor-mode ---
    followup = False
    followup_phrases = ("follow up", "what about", "and ", "also ", "more on ", "next ", "continue ")
    if len(cleaned_user_message.split()) < 10 or any(cleaned_user_message.lower().startswith(p) for p in followup_phrases):
        followup = True
    followup = followup or bool(context_controls.get("use_prior_report"))
    recalled_context = await _auto_recall_context(
        cleaned_user_message,
        cross_channel=cross_channel,
        followup=followup,
        reset_context=bool(context_controls.get("reset_context")),
        use_prior_report=bool(context_controls.get("use_prior_report")),
        anchor_override=context_controls.get("anchor_override"),  # type: ignore[arg-type]
        disable_anchor=bool(context_controls.get("disable_anchor")),
    )
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {cleaned_user_message}"
    else:
        model_message = cleaned_user_message
    # Add metadata for cross-channel or anchor mode
    metadata = {}
    if cross_channel:
        metadata["context_mode"] = "cross-channel"
        metadata["context_badge"] = "🌐 Cross-channel"
    elif context_controls.get("reset_context"):
        metadata["context_mode"] = "context-reset"
        metadata["context_badge"] = "♻️ Context reset"
    elif followup:
        metadata["context_mode"] = "followup-anchor"
        metadata["context_badge"] = "🧷 Follow-up anchor"

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                cleaned_user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(cleaned_user_message),
                ollama_alive=_ollama_up,
            )

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False, **metadata}
                    return

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}", "updated_history": updated, "needs_tools": False, **metadata}
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
                    {"role": "user", "parts": [cleaned_user_message]},
                    {"role": "model", "parts": [reply]},
                ]
                yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                return
            log.info("%s call failed, falling back to Gemini", model_preference)
        except Exception as e:
            log.info("%s call failed, falling back to Gemini: %s", model_preference, e)

    # Forced local mode
    if model_preference == "local":
        if not LOCAL_LLM_ENABLED:
            yield "⚠️ Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False, **metadata}
            return
        if not await _ollama_available():
            yield "⚠️ Ollama is not reachable. Check that the service is running.", True, {"model_used": "none", "updated_history": history, "needs_tools": False, **metadata}
            return
        gemma_reply = await _try_local_model(model_message, history, force=True)
        if gemma_reply is not None:
            updated = history + [
                {"role": "user", "parts": [cleaned_user_message]},
                {"role": "model", "parts": [gemma_reply]},
            ]
            yield gemma_reply, True, {"model_used": OLLAMA_MODEL, "updated_history": updated, "needs_tools": False, **metadata}
            return
        log.info("Local model returned empty, auto-falling back to Gemini")

    # Forced Gemini mode
    if model_preference in ("gemini", "local"):
        if not GOOGLE_API_KEY:
            yield "⚠️ Gemini API key not configured (`GOOGLE_API_KEY`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False, **metadata}
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
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    _routing_notes.append("Gemini rate-limited → used Copilot proxy")
                    yield reply, True, {"model_used": f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False, "routing_notes": _routing_notes, **metadata}
                    return
        except (ImportError, KeyError) as exc:
            log.debug("Copilot proxy fallback unavailable: %s", exc)
        except Exception as exc:
            log.warning("Copilot proxy fallback failed (rate-limit recovery): %s", exc)
        msg = (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)"
        )
        yield msg, True, {"model_used": MODEL_NAME, "updated_history": history, "needs_tools": False, **metadata}
        return

    model, route_info = await _select_model_for_message(
        cleaned_user_message,
        tool_declarations=tool_declarations,
        label="LLM",
    )
    if route_info.get("strategy") in {"shortlist", "pack-filter"}:
        _routing_notes.append(
            "Tool shortlist: " + ", ".join(route_info.get("selected", [])[:6])
        )
        if route_info.get("bundles"):
            _routing_notes.append("Intent bundle: " + ", ".join(route_info.get("bundles", [])[:3]))
        if route_info.get("pack"):
            _routing_notes.append(f"Domain pack: {route_info.get('pack')}")
        if route_info.get("persona"):
            _routing_notes.append(f"Persona: {route_info.get('persona')}")
    elif route_info.get("strategy") == "no-tools":
        _routing_notes.append("Tool use disabled for this internal request")
    model_message = _apply_route_hints(model_message, route_info)
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    text, updated_history, model_name = await _gemini_chat(
        model_message, history, model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)

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
        updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, retry_msg)

    # Auto-escalate vague responses to web search
    if (
        _VAGUE_RESPONSE_RE.search(text)
        and _FACTUAL_QUESTION_RE.search(cleaned_user_message.strip())
    ):
        log.info("Auto-escalating to web search for: %s", cleaned_user_message)
        search_fn = SKILLS.get("search_web")
        if search_fn is not None:
            try:
                search_results = await search_fn(cleaned_user_message)
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
                        updated_history, cleaned_user_message, enhanced_msg,
                    )
            except Exception as exc:
                log.warning("Auto-escalation web search failed: %s", exc)

    yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": True, "routing_notes": _routing_notes, **metadata}
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
        yield f"❌ **LLM Error:** {e}", True, {"model_used": model_name, "updated_history": history, "needs_tools": False, "routing_notes": _routing_notes, **metadata}
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
                yield accumulated, False, {"model_used": model_name, "needs_tools": False, **metadata}
    except Exception as e:
        if not accumulated:
            accumulated = f"❌ Streaming error: {e}"

    if last_chunk is not None:
        try:
            await _record_usage(last_chunk)
        except Exception as exc:
            log.debug("Stream usage recording failed: %s", exc)

    updated_history = _extract_history(chat_session)
    updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)
    yield accumulated, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": False, "routing_notes": _routing_notes, **metadata}


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
    tool_declarations: list[dict[str, Any]] | None = None,
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

    cleaned_user_message, cross_channel = _extract_cross_channel_opt_in(user_message)
    cleaned_user_message, context_controls = _extract_context_controls(cleaned_user_message)
    recalled_context = await _auto_recall_context(
        cleaned_user_message,
        cross_channel=cross_channel,
        followup=bool(context_controls.get("use_prior_report")),
        reset_context=bool(context_controls.get("reset_context")),
        use_prior_report=bool(context_controls.get("use_prior_report")),
        anchor_override=context_controls.get("anchor_override"),  # type: ignore[arg-type]
        disable_anchor=bool(context_controls.get("disable_anchor")),
    )
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {cleaned_user_message}"
    else:
        model_message = cleaned_user_message

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                cleaned_user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(cleaned_user_message),
                ollama_alive=_ollama_up,
            )
            log.debug("Model router: %s", route)

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
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
                        {"role": "user", "parts": [cleaned_user_message]},
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
                    {"role": "user", "parts": [cleaned_user_message]},
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
                {"role": "user", "parts": [cleaned_user_message]},
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
        model, route_info = await _select_model_for_message(
            cleaned_user_message,
            tool_declarations=tool_declarations,
            label="LLM",
        )
        model_message = _apply_route_hints(model_message, route_info)
        text, updated_history, model_name = await _gemini_chat(
            model_message, history, model,
            on_tool_call=on_tool_call, parallel_tools=True, label="LLM",
        )
        updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)
        return text, updated_history, model_name

    # Auto mode: Copilot for simple queries, Gemini for tool queries
    if not _needs_tools(cleaned_user_message):
        try:
            from model_router import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    import os
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
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

    model, route_info = await _select_model_for_message(
        cleaned_user_message,
        tool_declarations=tool_declarations,
        label="LLM",
    )
    model_message = _apply_route_hints(model_message, route_info)
    text, updated_history, model_name = await _gemini_chat(
        model_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)
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
