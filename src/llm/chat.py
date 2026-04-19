"""
Core LLM chat logic — chat(), chat_stream(), chat_deep(), summarize_conversation().
"""

import asyncio
import logging
import os
import time as _time
from typing import Any, AsyncGenerator

from google import genai

from llm.trace import RequestTrace
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
    _MEMORY_STORE_RE,
    _VAGUE_RESPONSE_RE,
    _gemma_response_seems_valid,
    _needs_tools,
    _provider_response_seems_valid,
    _reflect_on_response,
)
from llm_ratelimit import rate_limiter as _rate_limiter
from tool_health import circuit_breaker as _gemini_circuit
from tool_orchestration import build_tool_provider_context
from tool_router import route_tool_declarations
from trace_context import get_trace_id

from .context import (
    _auto_recall_context,
    _build_context_explainability,
    _extract_context_controls,
    _extract_cross_channel_opt_in,
    _format_context_explainability_note,
    _merge_structured_context_controls,
    _strip_recalled_prefix,
    _trim_history,
)
from .tool_execution import _ollama_available, _try_local_model

_GEMINI_CIRCUIT_KEY = "gemini"
_RECOVERY_LOCAL_TIMEOUT_SECONDS = 25.0
_RECOVERY_COPILOT_TIMEOUT_SECONDS = 20.0
_RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS = 30.0
# W12-3: configurable chunk interval — replace hard-coded 200 with env var
STREAM_INTERVAL_CHARS = int(os.getenv("PROVIDER_STREAM_INTERVAL_CHARS", "200"))
# Minimum accumulated chars between partial-chunk yields when PROVIDER_STREAM=1.
_PROVIDER_STREAM_PARTIAL_INTERVAL = STREAM_INTERVAL_CHARS

log = logging.getLogger("openclaw.llm")


def _build_trace_footer(trace: "RequestTrace | None", debug_level: int) -> str:
    """Build a routing debug footer from a RequestTrace."""
    if trace is None or debug_level == 0:
        return ""
    parts = []
    if trace.model_used:
        label = trace.model_used
        if trace.provider and trace.provider not in label:
            label = f"{label} ({trace.provider})"
        if trace.mini_model_used:
            label = f"{label} ⚡"
        parts.append(f"via {label}")
    if debug_level >= 2 and trace.skills_invoked:
        parts.append(f"skills: {', '.join(trace.skills_invoked)}")
    if debug_level >= 2 and trace.latency_ms > 0:
        parts.append(f"{trace.latency_ms:.0f}ms")
    if not parts:
        return ""
    return "\n\n_" + " · ".join(parts) + "_"


def _apply_trace_footer(text: str, trace: "RequestTrace | None") -> str:
    """Append routing debug footer to a reply when SHOW_ROUTING_DEBUG is enabled."""
    _debug_level = int(os.getenv("SHOW_ROUTING_DEBUG", "0"))
    if _debug_level > 0:
        footer = _build_trace_footer(trace, _debug_level)
        if footer:
            return text + footer
    return text


def _format_model_label(model: str | None) -> str:
    """Return a human-readable model attribution label, e.g. 'GPT-4o', 'Gemini 2.5 Flash'."""
    m = (model or "unknown").replace("models/", "").strip()
    if m.startswith("gemini-"):
        return "Gemini " + m[len("gemini-"):].replace("-", " ").title()
    if m.startswith("gpt-"):
        suffix = m[4:]
        # Preserve capitalisation: "4o", "4o-mini" → "4o" / "4o-mini"
        return f"GPT-{suffix}"
    if m.startswith("claude-"):
        parts = m[len("claude-"):].split("-")
        return "Claude " + " ".join(p.title() for p in parts)
    return m.replace("-", " ").title()


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
    Raises RuntimeError if the Gemini circuit breaker is open.
    """
    if _gemini_circuit.is_open(_GEMINI_CIRCUIT_KEY):
        log.warning("Gemini circuit open — skipping API call, falling back to local model")
        raise RuntimeError("Gemini circuit breaker is open")

    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history,
            model.model_name if hasattr(model, "model_name") else "unknown",
        )

    provider_context = build_tool_provider_context(
        "gemini",
        model=model,
        history=history,
    )
    chat_session = provider_context.session

    try:
        loop = asyncio.get_running_loop()
        _t0 = _time.monotonic()
        _rate_limiter.record()
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(user_message)
        )
        await _record_usage(response)

        from llm_tools import _run_tool_loop

        response, rounds = await _run_tool_loop(
            chat_session, response,
            max_rounds=max_tool_rounds,
            on_tool_call=on_tool_call,
            parallel=parallel_tools,
            label=label,
        )

        text = provider_context.adapter.extract_final_text(
            response,
            rounds,
            chat_session,
            max_rounds=max_tool_rounds,
        )
        text = await _reflect_on_response(text, user_message, rounds)

        updated_history = provider_context.adapter.extract_history(chat_session)
        if getattr(response, "direct_final_text", ""):
            updated_history = provider_context.adapter.merge_direct_final_history(
                updated_history,
                text,
            )
        model_name = provider_context.model_name

        _gemini_circuit.record_success(_GEMINI_CIRCUIT_KEY)
        # Phase 15: append provider attribution for direct (non-tool) answers.
        if rounds == 0 and text and "_via " not in text:
            text = text + f"\n\n_via {_format_model_label(model_name)}_"

        # W9-4: structured route logging
        _latency_ms = int((_time.monotonic() - _t0) * 1000)
        log.info("route: query_type=%s provider=%s latency_ms=%d", label, "gemini", _latency_ms)

        return text, updated_history, model_name

    except Exception:  # broad: intentional
        _gemini_circuit.record_failure(_GEMINI_CIRCUIT_KEY)
        raise


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
        "retrieval_profile",
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


def _provider_model_label(provider: str, *, message: str = "") -> str:
    import os

    if provider == "copilot":
        from model_router import copilot_model_for_message

        return f"copilot/{copilot_model_for_message(message)}"
    if provider == "anthropic":
        return f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
    return f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"


def _finalize_provider_reply(
    reply: str | None,
    *,
    provider: str,
    cleaned_user_message: str,
    history: list[dict],
    model_label_override: str | None = None,
) -> tuple[list[dict], str] | None:
    if not reply or not _provider_response_seems_valid(reply, provider=provider):
        return None
    updated = history + [
        {"role": "user", "parts": [cleaned_user_message]},
        {"role": "model", "parts": [reply]},
    ]
    return updated, model_label_override or _provider_model_label(provider, message=cleaned_user_message)


def _copilot_model_candidates(message: str) -> list[str]:
    import os

    from model_router import copilot_model_for_message

    candidates: list[str] = []
    for candidate in (
        copilot_model_for_message(message),
        os.getenv("OPENAI_MODEL", "gpt-4o"),
        os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5"),
    ):
        candidate = str(candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


async def _try_copilot_proxy_reply(
    *,
    model_message: str,
    cleaned_user_message: str,
    history: list[dict],
    context: str,
    timeout: float | None = None,
    model_override: str | None = None,
    trace: RequestTrace | None = None,
) -> tuple[str, list[dict], str] | None:
    from llm.providers import COPILOT_PROXY_ENABLED, call_provider_stream, chat_openai
    if not COPILOT_PROXY_ENABLED:
        return None

    _provider_stream_enabled = os.getenv("PROVIDER_STREAM", "").strip() == "1"
    system_prompt = _load_system_prompt()
    candidates = [model_override] if model_override else _copilot_model_candidates(cleaned_user_message)
    if model_override:
        log.debug("Mini-model fast-path: using %s", model_override)
        if trace is not None:
            trace.mini_model_used = True
    per_attempt_timeout = (
        max(timeout / max(len(candidates), 1), 1.0)
        if timeout
        else None
    )

    for candidate in candidates:
        try:
            if _provider_stream_enabled:
                # Collect stream chunks into a full reply (same return type as non-stream path)
                chunks: list[str] = []
                async for chunk in call_provider_stream(
                    "copilot",
                    model_message,
                    history=history,
                    system_prompt=system_prompt,
                    model=candidate,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                ):
                    chunks.append(chunk)
                reply: str | None = "".join(chunks) or None
            else:
                request = chat_openai(
                    model_message,
                    history,
                    system_prompt,
                    model=candidate,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                reply = (
                    await asyncio.wait_for(request, timeout=per_attempt_timeout)
                    if per_attempt_timeout is not None
                    else await request
                )
        except asyncio.TimeoutError:
            log.warning(
                "Copilot proxy model %s timed out during %s",
                candidate,
                context,
            )
            continue
        except (OSError, ConnectionError, ValueError, RuntimeError) as exc:
            log.warning(
                "Copilot proxy model %s failed during %s (%s): %s",
                candidate,
                context,
                type(exc).__name__,
                exc,
            )
            continue

        finalized = _finalize_provider_reply(
            reply,
            provider="copilot",
            cleaned_user_message=cleaned_user_message,
            history=history,
            model_label_override=f"copilot/{candidate}",
        )
        if finalized is not None:
            updated, model_label = finalized
            # Phase 15: append provider attribution so users can see which model answered.
            if reply and "_via " not in reply:
                reply = reply + f"\n\n_via {_format_model_label(candidate)}_"
            if trace is not None:
                trace.provider = "copilot"
                trace.model_used = candidate
            # W9-4: structured route logging
            log.info("route: query_type=%s provider=%s latency_ms=%d", context, "copilot", 0)
            return reply, updated, model_label
        if reply:
            log.info(
                "Copilot proxy model %s returned placeholder reply during %s; trying next candidate",
                candidate,
                context,
            )
        else:
            log.warning(
                "Copilot proxy model %s returned no reply during %s; trying next candidate",
                candidate,
                context,
            )
    return None


async def _stream_copilot_chunks(
    *,
    model_message: str,
    cleaned_user_message: str,
    history: list[dict],
    context: str,
    model_override: str | None = None,
) -> AsyncGenerator[tuple[str, bool, dict[str, Any]], None]:
    """Async generator for PROVIDER_STREAM=1 copilot proxy path.

    Yields ``(accumulated_partial, False, {})`` every _PROVIDER_STREAM_PARTIAL_INTERVAL chars
    while the provider streams, then ``(final_reply, True, {"model_used": ..., "updated_history": ...})``
    when complete.  Yields nothing if the proxy is disabled or all candidates fail so the
    caller can fall through to the next provider naturally.
    """
    from llm.providers import COPILOT_PROXY_ENABLED, call_provider_stream
    if not COPILOT_PROXY_ENABLED:
        return

    system_prompt = _load_system_prompt()
    candidates = [model_override] if model_override else _copilot_model_candidates(cleaned_user_message)

    for candidate in candidates:
        accumulated = ""
        last_yield_len = 0
        try:
            async for chunk in call_provider_stream(
                "copilot",
                model_message,
                history=history,
                system_prompt=system_prompt,
                model=candidate,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            ):
                accumulated += chunk
                if len(accumulated) - last_yield_len >= _PROVIDER_STREAM_PARTIAL_INTERVAL:
                    yield accumulated, False, {}
                    last_yield_len = len(accumulated)
        except (OSError, ConnectionError, ValueError, RuntimeError) as exc:
            log.warning(
                "Copilot stream candidate %s failed (%s): %s",
                candidate, context, exc,
            )
            # W12-4: emit partial content with warning footer if we accumulated something
            if accumulated:
                partial = accumulated + "\n\n⚠️ *Stream interrupted — showing partial response.*"
                yield partial, True, {
                    "model_used": f"copilot/{candidate}",
                    "updated_history": history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [partial]},
                    ],
                }
                return
            continue

        reply = accumulated or None
        finalized = _finalize_provider_reply(
            reply,
            provider="copilot",
            cleaned_user_message=cleaned_user_message,
            history=history,
            model_label_override=f"copilot/{candidate}",
        )
        if finalized is not None:
            updated, model_label = finalized
            if reply and "_via " not in reply:
                reply = reply + f"\n\n_via {_format_model_label(candidate)}_"
            yield reply, True, {"model_used": model_label, "updated_history": updated}
            return
        if reply:
            log.info(
                "Copilot stream candidate %s returned placeholder reply (%s); trying next",
                candidate, context,
            )
        else:
            log.warning(
                "Copilot stream candidate %s returned no reply (%s); trying next",
                candidate, context,
            )


async def _recover_stream_provider_failure(
    *,
    failed_provider: str,
    model_message: str,
    cleaned_user_message: str,
    history: list[dict],
    routing_notes: list[str],
    reason: str,
) -> tuple[str, list[dict], str, bool]:
    """Best-effort recovery when a primary provider is unavailable."""
    provider_label = (failed_provider or "provider").strip() or "provider"
    provider_display = provider_label.capitalize()
    try:
        fallback = await asyncio.wait_for(
            _try_local_model(model_message, history),
            timeout=_RECOVERY_LOCAL_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning(
            "Local Gemini recovery timed out after %.1fs (%s)",
            _RECOVERY_LOCAL_TIMEOUT_SECONDS,
            reason,
        )
        fallback = None
    if fallback is not None:
        updated = history + [
            {"role": "user", "parts": [cleaned_user_message]},
            {"role": "model", "parts": [fallback]},
        ]
        routing_notes.append(f"{provider_display} unavailable → local fallback ({reason})")
        return fallback, updated, OLLAMA_MODEL, False

    try:
        result = await _try_copilot_proxy_reply(
            model_message=model_message,
            cleaned_user_message=cleaned_user_message,
            history=history,
            context=f"{provider_label} recovery ({reason})",
            timeout=_RECOVERY_COPILOT_TIMEOUT_SECONDS,
        )
        if result is not None:
            reply, updated, model_label = result
            routing_notes.append(f"{provider_display} unavailable → Copilot proxy ({reason})")
            return reply, updated, model_label, False
    except (ImportError, KeyError) as exc:
        log.debug("Copilot proxy recovery unavailable: %s", exc)
    except (OSError, ConnectionError, ValueError, RuntimeError) as exc:
        log.warning("Copilot proxy recovery failed (%s): %s", type(exc).__name__, exc)

    try:
        declarations = _get_tool_declarations()
        routed_declarations, _ = route_tool_declarations(cleaned_user_message, declarations)
        selected_names = {
            str(item.get("name", "")).strip()
            for item in routed_declarations
            if isinstance(item, dict)
        }
        if "generate_sports_watch_report" in selected_names:
            from skills.reporting_skills import generate_sports_watch_report

            direct_reply = await asyncio.wait_for(
                generate_sports_watch_report(query=cleaned_user_message),
                timeout=_RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS,
            )
            if direct_reply and not direct_reply.startswith("❌"):
                updated = history + [
                    {"role": "user", "parts": [cleaned_user_message]},
                    {"role": "model", "parts": [direct_reply]},
                ]
                routing_notes.append(f"{provider_display} unavailable → direct sports skill ({reason})")
                return direct_reply, updated, "direct-sports-skill", False
        elif "generate_news_report" in selected_names:
            from skills.reporting_skills import generate_news_report

            direct_reply = await asyncio.wait_for(
                generate_news_report(query=cleaned_user_message),
                timeout=_RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS,
            )
            if direct_reply and not direct_reply.startswith("❌"):
                updated = history + [
                    {"role": "user", "parts": [cleaned_user_message]},
                    {"role": "model", "parts": [direct_reply]},
                ]
                routing_notes.append(f"{provider_display} unavailable → direct news skill ({reason})")
                return direct_reply, updated, "direct-news-skill", False
        elif "generate_weather_report" in selected_names:
            from skills.reporting_skills import generate_weather_report

            direct_reply = await asyncio.wait_for(
                generate_weather_report(query=cleaned_user_message),
                timeout=_RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS,
            )
            if direct_reply and not direct_reply.startswith("❌"):
                updated = history + [
                    {"role": "user", "parts": [cleaned_user_message]},
                    {"role": "model", "parts": [direct_reply]},
                ]
                routing_notes.append(f"{provider_display} unavailable → direct weather skill ({reason})")
                return direct_reply, updated, "direct-weather-skill", False
        elif "generate_finance_report" in selected_names:
            from skills.reporting_skills import generate_finance_report

            direct_reply = await asyncio.wait_for(
                generate_finance_report(query=cleaned_user_message),
                timeout=_RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS,
            )
            if direct_reply and not direct_reply.startswith("❌"):
                updated = history + [
                    {"role": "user", "parts": [cleaned_user_message]},
                    {"role": "model", "parts": [direct_reply]},
                ]
                routing_notes.append(f"{provider_display} unavailable → direct finance skill ({reason})")
                return direct_reply, updated, "direct-finance-skill", False
    except asyncio.TimeoutError:
        log.warning(
            "Direct sports recovery timed out after %.1fs (%s)",
            _RECOVERY_DIRECT_SKILL_TIMEOUT_SECONDS,
            reason,
        )
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        log.debug("Direct sports recovery unavailable (%s): %s", type(exc).__name__, exc)

    routing_notes.append(f"{provider_display} unavailable ({reason})")
    return (
        f"⚠️ {provider_display} is temporarily unavailable right now. Please try again in a moment.",
        history,
        "unavailable",
        False,
    )


def _channel_context_prefix(channel_name: str | None) -> str:
    """Return a brief channel context hint for the system prompt."""
    if not channel_name:
        return ""
    name = channel_name.lstrip("#").lower().strip()
    _HINTS = {
        "research": "You are helping with research. Favor citations and structured findings.",
        "docker": "You are helping with Docker/container infrastructure. Be precise and technical.",
        "journal": "You are in a personal journaling context. Be thoughtful and reflective.",
        "logs": "You are helping analyze logs and system events. Be concise and diagnostic.",
        "reports": "You are generating a structured report. Use headers and bullet points.",
        "incident": "You are in an incident response context. Be direct, concise, and actionable.",
        "memory": "You are in a memory/recall context. Help the user recall and connect past information.",
        "analytics": "You are in an analytics context. Focus on data patterns and quantitative insights.",
        "real-estate": "You are in a real estate research context. Focus on property data and market analysis.",
        "bookmarks": "You are helping manage bookmarks and saved content. Be organized and concise.",
        "general": "",  # no hint for general channel
    }
    for key, hint in _HINTS.items():
        if key in name:
            return f"\n\n[Channel context: {hint}]\n" if hint else ""
    return ""


async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
    tool_declarations: list[dict[str, Any]] | None = None,
    context_controls: dict[str, Any] | None = None,
    routing_profile: str = "",
    trace: RequestTrace | None = None,
) -> AsyncGenerator[tuple[str, bool, dict[str, Any]], None]:
    """Async generator yielding ``(chunk_text, is_final, metadata)`` tuples."""
    log.info("LLM chat_stream start model_pref=%s trace=%s msg=%.60s",
             model_preference, get_trace_id(), user_message)
    if model_preference == "local":
        _model_hint = "ollama"
    elif model_preference == "gemini":
        _model_hint = "gemini"
    else:
        _model_hint = "gemini"
    context_quality: dict[str, Any] = {}
    history = await _trim_history(
        history or [],
        model_hint=_model_hint,
        context_quality=context_quality,
    )

    _routing_notes: list[str] = []

    cleaned_user_message, cross_channel = _extract_cross_channel_opt_in(user_message)
    cleaned_user_message, legacy_context_controls = _extract_context_controls(cleaned_user_message)
    cross_channel, context_controls = _merge_structured_context_controls(
        cross_channel=cross_channel,
        controls=legacy_context_controls,
        structured_controls=context_controls,
    )
    # --- hard-context-boundaries and followup-anchor-mode ---
    followup = False
    followup_phrases = ("follow up", "what about", "and ", "also ", "more on ", "next ", "continue ")
    if len(cleaned_user_message.split()) < 10 or any(cleaned_user_message.lower().startswith(p) for p in followup_phrases):
        followup = True
    followup = followup or bool(context_controls.get("use_prior_report"))
    recalled_context = await _auto_recall_context(
        cleaned_user_message,
        cross_channel=cross_channel,
        routing_notes=_routing_notes,
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
    channel_name = context_controls.get("channel_name") if context_controls else None
    channel_prefix = _channel_context_prefix(channel_name)
    if channel_prefix:
        model_message = channel_prefix + model_message
    metadata = {}
    metadata["context_quality"] = dict(context_quality)
    explainability = _build_context_explainability(
        cross_channel=cross_channel,
        followup=followup,
        use_prior_report=bool(context_controls.get("use_prior_report")),
        anchor_override=context_controls.get("anchor_override"),  # type: ignore[arg-type]
        disable_anchor=bool(context_controls.get("disable_anchor")),
    )
    metadata["explainability"] = explainability
    metadata["explainability_note"] = _format_context_explainability_note(explainability)
    if context_quality.get("compression_applied"):
        _routing_notes.append(
            "Context compressed "
            f"(ratio {context_quality.get('compression_ratio', 1.0):.2f}, "
            f"facts {context_quality.get('retained_key_facts_count', 0)})"
        )
        drift = str(context_quality.get("drift_risk") or "")
        if drift and drift != "low":
            _routing_notes.append(f"Context drift risk: {drift}")
    if cross_channel:
        metadata["context_mode"] = "cross-channel"
        metadata["context_badge"] = "🌐 Cross-channel"
    elif context_controls.get("reset_context"):
        metadata["context_mode"] = "context-reset"
        metadata["context_badge"] = "♻️ Context reset"
    elif followup:
        metadata["context_mode"] = "followup-anchor"
        metadata["context_badge"] = "🧷 Follow-up anchor"

    # Unified web-search fast-path — uses cleaned_user_message to avoid cross-topic
    # contamination from recalled context (e.g., lacrosse memories polluting gaming queries).
    if model_preference == "auto":
        try:
            from model_routing_policy import select_web_search_route
            web_route = select_web_search_route(model_message)
            if web_route.prefer_search:
                log.info("chat_stream web_search_route reason=%s", web_route.reason)
                from skills.reporting_skills import generate_web_search_report
                web_reply = await generate_web_search_report(cleaned_user_message)
                if web_reply and not web_reply.startswith("❌"):
                    if trace is not None:
                        trace.skills_invoked.append("web_search_report")
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [web_reply]},
                    ]
                    yield web_reply, True, {"model_used": "perplexity-direct", "updated_history": updated, "needs_tools": False, **metadata}
                    return
        except (ImportError, OSError, ValueError, AttributeError) as _web_exc:
            log.warning("Stream web-search fast-path failed, falling through: %s", _web_exc)

    # Copilot fast-path for coding/programming queries — separate from web-search path.
    if model_preference == "auto" and not recalled_context:
        try:
            from llm.providers import COPILOT_PROXY_ENABLED
            from model_routing_policy import select_coding_route
            if COPILOT_PROXY_ENABLED:
                coding_route = select_coding_route(cleaned_user_message)
                if coding_route.matches:
                    if os.getenv("PROVIDER_STREAM", "").strip() == "1":
                        _got_final = False
                        async for _txt, _is_final, _smeta in _stream_copilot_chunks(
                            model_message=model_message,
                            cleaned_user_message=cleaned_user_message,
                            history=history,
                            context="coding-fast-path",
                        ):
                            if _is_final:
                                _routing_notes.append(f"Coding fast-path → Copilot ({coding_route.reason})")
                                yield _txt, True, {"model_used": _smeta["model_used"], "updated_history": _smeta["updated_history"], "needs_tools": False, "routing_notes": list(_routing_notes), **metadata}
                                _got_final = True
                                break
                            yield _txt, False, {}
                        if _got_final:
                            return
                    else:
                        result = await _try_copilot_proxy_reply(
                            model_message=model_message,
                            cleaned_user_message=cleaned_user_message,
                            history=history,
                            context="coding-fast-path",
                            trace=trace,
                        )
                        if result is not None:
                            reply, updated, model_label = result
                            _routing_notes.append(f"Coding fast-path → Copilot ({coding_route.reason})")
                            reply = _apply_trace_footer(reply, trace)
                            yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                            return
        except (ImportError, OSError, ValueError, AttributeError) as _fp_exc:
            log.warning("Stream coding fast-path failed, falling through to standard routing: %s", _fp_exc)

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            from llm.providers import COPILOT_PROXY_ENABLED, chat_anthropic, chat_openai
            from model_router import classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                cleaned_user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                copilot_available=COPILOT_PROXY_ENABLED,
                needs_tools=_needs_tools(cleaned_user_message),
                ollama_alive=_ollama_up,
                routing_profile=routing_profile,
                recalled_context=bool(recalled_context),
            )
            _routing_notes.append(f"Auto route: {route.reason}")

            if route.model_type == "copilot":
                if route.model:
                    _routing_notes.append(f"mini-model: {route.model}")
                if os.getenv("PROVIDER_STREAM", "").strip() == "1":
                    _got_final = False
                    async for _txt, _is_final, _smeta in _stream_copilot_chunks(
                        model_message=model_message,
                        cleaned_user_message=cleaned_user_message,
                        history=history,
                        context="auto-route",
                        model_override=route.model or None,
                    ):
                        if _is_final:
                            yield _txt, True, {"model_used": _smeta["model_used"], "updated_history": _smeta["updated_history"], "needs_tools": False, "routing_notes": list(_routing_notes), **metadata}
                            _got_final = True
                            break
                        yield _txt, False, {}
                    if _got_final:
                        return
                    log.warning("Copilot auto-route exhausted candidates, falling through to Gemini")
                else:
                    result = await _try_copilot_proxy_reply(
                        model_message=model_message,
                        cleaned_user_message=cleaned_user_message,
                        history=history,
                        context="auto-route",
                        model_override=route.model or None,
                        trace=trace,
                    )
                    if result is not None:
                        reply, updated, model_label = result
                        reply = _apply_trace_footer(reply, trace)
                        yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                        return
                    log.warning("Copilot auto-route exhausted candidates, falling through to Gemini")

            elif route.model_type == "ollama":
                reply = await _try_local_model(model_message, history, force=True)
                if reply is not None:
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": OLLAMA_MODEL, "updated_history": updated, "needs_tools": False, **metadata}
                    return
                log.warning("Ollama auto-route returned empty, falling through to Gemini")

            elif route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                finalized = _finalize_provider_reply(
                    reply,
                    provider="openai",
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                )
                if finalized is not None:
                    updated, model_label = finalized
                    yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                    return
                if reply:
                    log.info("OpenAI route returned placeholder reply, falling through to Gemini")

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                finalized = _finalize_provider_reply(
                    reply,
                    provider="anthropic",
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                )
                if finalized is not None:
                    updated, model_label = finalized
                    yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                    return
                if reply:
                    log.info("Anthropic route returned placeholder reply, falling through to Gemini")
        except (ImportError, OSError, ValueError, AttributeError) as e:
            log.debug("Multi-model routing failed (non-fatal, stream): %s", e)

    # Forced OpenAI / Anthropic mode
    if model_preference in ("openai", "anthropic", "copilot"):
        try:
            from llm.providers import chat_anthropic, chat_openai
            provider_name = model_preference
            if model_preference == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
            elif model_preference == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
            else:
                if os.getenv("PROVIDER_STREAM", "").strip() == "1":
                    _got_final = False
                    async for _txt, _is_final, _smeta in _stream_copilot_chunks(
                        model_message=model_message,
                        cleaned_user_message=cleaned_user_message,
                        history=history,
                        context="forced-copilot",
                    ):
                        if _is_final:
                            yield _txt, True, {"model_used": _smeta["model_used"], "updated_history": _smeta["updated_history"], "needs_tools": False, **metadata}
                            _got_final = True
                            break
                        yield _txt, False, {}
                    if _got_final:
                        return
                    reply = None
                else:
                    result = await _try_copilot_proxy_reply(
                        model_message=model_message,
                        cleaned_user_message=cleaned_user_message,
                        history=history,
                        context="forced-copilot",
                        trace=trace,
                    )
                    if result is not None:
                        reply, updated, model_label = result
                        reply = _apply_trace_footer(reply, trace)
                        yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                        return
                    reply = None
            finalized = _finalize_provider_reply(
                reply,
                provider=provider_name,
                cleaned_user_message=cleaned_user_message,
                history=history,
            )
            if finalized is not None:
                updated, model_label = finalized
                yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, **metadata}
                return
            log.info("%s call failed, falling back to Gemini", model_preference)
        except (ImportError, OSError, ConnectionError, ValueError, RuntimeError) as e:
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
            result = await _try_copilot_proxy_reply(
                model_message=model_message,
                cleaned_user_message=cleaned_user_message,
                history=history,
                context="rate-limit-recovery",
                trace=trace,
            )
            if result is not None:
                reply, updated, model_label = result
                _routing_notes.append("Gemini rate-limited → used Copilot proxy")
                yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False, "routing_notes": _routing_notes, **metadata}
                return
        except (ImportError, KeyError) as exc:
            log.debug("Copilot proxy fallback unavailable: %s", exc)
        except (OSError, ConnectionError, ValueError, RuntimeError) as exc:
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
    suppressed_tools = [str(name) for name in (route_info.get("guard_suppressed") or []) if name]
    if suppressed_tools:
        _routing_notes.append("Router guard suppressed: " + ", ".join(suppressed_tools[:6]))
    model_message = _apply_route_hints(model_message, route_info)
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    try:
        text, updated_history, model_name = await _gemini_chat(
            model_message, history, model,
            on_tool_call=on_tool_call,
            parallel_tools=True,
            label="LLM",
        )
    except Exception as exc:  # broad: intentional
        log.warning("Gemini failed in stream mode (%s), trying local fallback: %s", type(exc).__name__, exc)
        text, updated_history, model_name, needs_tools = await _recover_stream_provider_failure(
            failed_provider="gemini",
            model_message=model_message,
            cleaned_user_message=cleaned_user_message,
            history=history,
            routing_notes=_routing_notes,
            reason="primary",
        )
        yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": needs_tools, "routing_notes": _routing_notes, **metadata}
        return
    updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)

    # Memory-store requests (e.g. "remember these facts") produce natural-language
    # acknowledgments that superficially match hallucination patterns.  Skip the
    # hallucination check entirely for these — calling remember_fact or saying
    # "I've saved that" are both valid responses.
    _is_memory_store = bool(_MEMORY_STORE_RE.search(cleaned_user_message))

    if not _is_memory_store and not _gemma_response_seems_valid(text):
        log.warning("Post-response hallucination detected, retrying with explicit tool instruction")
        if _is_memory_store:
            retry_msg = (
                f"{model_message}\n\n"
                "IMPORTANT: Call the remember_fact tool to save this information. "
                "Do not describe what you will do — just call the tool now."
            )
        else:
            retry_msg = (
                f"{model_message}\n\n"
                "IMPORTANT: You have tool access. Do NOT say 'let me search' or 'one moment'. "
                "USE the available tools (e.g. nas_list_folder, search_web, browse_url) to "
                "find the answer, then respond with the actual results."
            )
        try:
            text, updated_history, model_name = await _gemini_chat(
                retry_msg, history, model,
                on_tool_call=on_tool_call,
                parallel_tools=True,
                label="LLM-retry",
            )
            updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, retry_msg)
        except Exception as exc:  # broad: intentional
            log.warning("Gemini retry failed (%s): %s", type(exc).__name__, exc)
            text, updated_history, model_name, needs_tools = await _recover_stream_provider_failure(
                failed_provider="gemini",
                model_message=retry_msg,
                cleaned_user_message=cleaned_user_message,
                history=history,
                routing_notes=_routing_notes,
                reason="retry",
            )
            yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": needs_tools, "routing_notes": _routing_notes, **metadata}
            return
    response_invalid = not _is_memory_store and not _gemma_response_seems_valid(text)
    if response_invalid:
        log.warning("Retry still returned placeholder/hallucination for: %s", cleaned_user_message)
        _routing_notes.append("Retry response remained placeholder")

    # Auto-escalate vague responses to web search (but never for memory-store requests)
    if (
        not _is_memory_store
        and (
            response_invalid
            or (
                _VAGUE_RESPONSE_RE.search(text)
                and _FACTUAL_QUESTION_RE.search(cleaned_user_message.strip())
            )
        )
    ):
        if _FACTUAL_QUESTION_RE.search(cleaned_user_message.strip()):
            log.info("Auto-escalating to web search for: %s", cleaned_user_message)
            from skills import SKILLS
            search_fn = SKILLS.get("search_web")
            if search_fn is not None:
                try:
                    search_results = await search_fn(cleaned_user_message)
                    if search_results and search_results.strip():
                        if trace is not None:
                            trace.skills_invoked.append("search_web")
                        enhanced_msg = (
                            f"{model_message}\n\n"
                            "Here are fresh web search results to help answer the question:\n"
                            f"{search_results}\n\n"
                            "Use these results to give a thorough, factual answer."
                        )
                        try:
                            text, updated_history, model_name = await _gemini_chat(
                                enhanced_msg, history, model,
                                on_tool_call=on_tool_call,
                                parallel_tools=True,
                                label="LLM-escalate",
                            )
                            updated_history = _strip_recalled_prefix(
                                updated_history, cleaned_user_message, enhanced_msg,
                            )
                            response_invalid = not _gemma_response_seems_valid(text)
                        except Exception as exc:  # broad: intentional
                            log.warning("Gemini web escalation failed (%s): %s", type(exc).__name__, exc)
                            text, updated_history, model_name, _ = await _recover_stream_provider_failure(
                                failed_provider="gemini",
                                model_message=enhanced_msg,
                                cleaned_user_message=cleaned_user_message,
                                history=history,
                                routing_notes=_routing_notes,
                                reason="web-escalation",
                            )
                            response_invalid = model_name == "unavailable"
                except (OSError, ConnectionError, ValueError, RuntimeError) as exc:
                    log.warning("Auto-escalation web search failed: %s", exc)

    if response_invalid:
        text = (
            "⚠️ I couldn't complete that live lookup cleanly just now. "
            "Please try again in a moment or ask a narrower question."
        )
        _routing_notes.append("Returned explicit fallback after invalid retry")

    text = _apply_trace_footer(text, trace)
    yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": True, "routing_notes": _routing_notes, **metadata}
    return


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
    tool_declarations: list[dict[str, Any]] | None = None,
    routing_profile: str = "",
    trace: RequestTrace | None = None,
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
    context_quality: dict[str, Any] = {}
    history = await _trim_history(
        history or [],
        model_hint=_model_hint,
        context_quality=context_quality,
    )

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
    channel_name = context_controls.get("channel_name") if context_controls else None
    channel_prefix = _channel_context_prefix(channel_name)
    if channel_prefix:
        model_message = channel_prefix + model_message

    # Unified web-search fast-path — uses cleaned_user_message to avoid cross-topic
    # contamination from recalled context (e.g., lacrosse memories polluting gaming queries).
    if model_preference == "auto":
        try:
            from model_routing_policy import select_web_search_route
            web_route = select_web_search_route(model_message)
            if web_route.prefer_search:
                log.info("chat web_search_route reason=%s", web_route.reason)
                from skills.reporting_skills import generate_web_search_report
                web_reply = await generate_web_search_report(cleaned_user_message)
                if web_reply and not web_reply.startswith("❌"):
                    if trace is not None:
                        trace.skills_invoked.append("web_search_report")
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [web_reply]},
                    ]
                    return web_reply, updated, "perplexity-direct"
        except (ImportError, OSError, ValueError, AttributeError) as _web_exc:
            log.warning("chat web-search fast-path failed, falling through: %s", _web_exc)

    # Copilot fast-path for coding/programming queries — separate from web-search path.
    if model_preference == "auto" and not recalled_context:
        try:
            from llm.providers import COPILOT_PROXY_ENABLED
            from model_routing_policy import select_coding_route
            if COPILOT_PROXY_ENABLED:
                coding_route = select_coding_route(cleaned_user_message)
                if coding_route.matches:
                    result = await _try_copilot_proxy_reply(
                        model_message=model_message,
                        cleaned_user_message=cleaned_user_message,
                        history=history,
                        context="coding-fast-path",
                        trace=trace,
                    )
                    if result is not None:
                        reply, updated, model_label = result
                        log.debug("Coding fast-path → Copilot (%s)", coding_route.reason)
                        return _apply_trace_footer(reply, trace), updated, model_label
        except (ImportError, OSError, ValueError, AttributeError) as _rt_exc:
            log.warning("Realtime fast-path failed, falling through to standard routing: %s", _rt_exc)

    # Multi-model routing (Phase 8)
    if model_preference == "auto":
        try:
            import os

            from llm.providers import COPILOT_PROXY_ENABLED, chat_anthropic, chat_openai
            from model_router import classify_query, is_ollama_alive
            _ollama_up = await is_ollama_alive()
            route = classify_query(
                cleaned_user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                copilot_available=COPILOT_PROXY_ENABLED,
                needs_tools=_needs_tools(cleaned_user_message),
                ollama_alive=_ollama_up,
                routing_profile=routing_profile,
                recalled_context=bool(recalled_context),
            )
            log.debug("Model router: %s", route)

            if route.model_type == "copilot":
                result = await _try_copilot_proxy_reply(
                    model_message=model_message,
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                    context="auto-route",
                    model_override=route.model or None,
                    trace=trace,
                )
                if result is not None:
                    reply, updated, model_label = result
                    return _apply_trace_footer(reply, trace), updated, model_label
                log.warning("Copilot auto-route exhausted candidates, falling through to default routing")

            elif route.model_type == "ollama":
                reply = await _try_local_model(model_message, history, force=True)
                if reply is not None:
                    updated = history + [
                        {"role": "user", "parts": [cleaned_user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, OLLAMA_MODEL
                log.warning("Ollama auto-route returned empty, falling through to default routing")

            elif route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                finalized = _finalize_provider_reply(
                    reply,
                    provider="openai",
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                )
                if finalized is not None:
                    updated, model_label = finalized
                    return reply, updated, model_label
                log.info("OpenAI call failed, falling through to default routing")

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                finalized = _finalize_provider_reply(
                    reply,
                    provider="anthropic",
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                )
                if finalized is not None:
                    updated, model_label = finalized
                    return reply, updated, model_label
                log.info("Anthropic call failed, falling through to default routing")
        except (ImportError, OSError, ValueError, AttributeError) as e:
            log.debug("Multi-model routing failed (non-fatal): %s", e)

    # Forced OpenAI / Anthropic mode
    if model_preference in ("openai", "anthropic", "copilot"):
        try:
            from llm.providers import chat_anthropic, chat_openai
            provider_name = model_preference
            if model_preference == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
            elif model_preference == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
            else:
                result = await _try_copilot_proxy_reply(
                    model_message=model_message,
                    cleaned_user_message=cleaned_user_message,
                    history=history,
                    context="forced-copilot",
                    trace=trace,
                )
                if result is not None:
                    reply, updated, model_label = result
                    return _apply_trace_footer(reply, trace), updated, model_label
                reply = None
            finalized = _finalize_provider_reply(
                reply,
                provider=provider_name,
                cleaned_user_message=cleaned_user_message,
                history=history,
            )
            if finalized is not None:
                updated, model_label = finalized
                return reply, updated, model_label
            log.info("%s call failed, falling back to Gemini", model_preference)
        except (ImportError, OSError, ConnectionError, ValueError, RuntimeError) as e:
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
        try:
            text, updated_history, model_name = await _gemini_chat(
                model_message, history, model,
                on_tool_call=on_tool_call, parallel_tools=True, label="LLM",
            )
        except Exception as exc:  # broad: intentional
            log.warning("Gemini failed in forced mode (%s), trying local fallback: %s", type(exc).__name__, exc)
            text, updated_history, model_name, _ = await _recover_stream_provider_failure(
                failed_provider="gemini",
                model_message=model_message,
                cleaned_user_message=cleaned_user_message,
                history=history,
                routing_notes=[],
                reason="forced",
            )
            return text, updated_history, model_name
        updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)
        return text, updated_history, model_name

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
    try:
        text, updated_history, model_name = await _gemini_chat(
            model_message,
            history,
            model,
            on_tool_call=on_tool_call,
            parallel_tools=True,
            label="LLM",
        )
    except Exception as exc:  # broad: intentional
        log.warning("Gemini failed in auto mode (%s), trying local fallback: %s", type(exc).__name__, exc)
        text, updated_history, model_name, _ = await _recover_stream_provider_failure(
            failed_provider="gemini",
            model_message=model_message,
            cleaned_user_message=cleaned_user_message,
            history=history,
            routing_notes=[],
            reason="auto",
        )
        return text, updated_history, model_name
    updated_history = _strip_recalled_prefix(updated_history, cleaned_user_message, model_message)

    # Phase 28: Quality retry gate — if Gemini returns a low-quality answer and
    # Copilot is available, retry once with Copilot before returning.
    try:
        from answer_policy import is_low_quality, record_quality_retry
        from llm.providers import COPILOT_PROXY_ENABLED
        if is_low_quality(text) and COPILOT_PROXY_ENABLED:
            log.info("Quality retry gate triggered — Gemini reply too short/vague, trying Copilot")
            record_quality_retry()
            print("↺ Auto-retried: quality gate triggered — trying a higher-quality provider", flush=True)
            copilot_result = await _try_copilot_proxy_reply(
                model_message=model_message,
                cleaned_user_message=cleaned_user_message,
                history=history,
                context="quality-retry",
                trace=trace,
            )
            if copilot_result is not None:
                cp_reply, cp_updated, cp_label = copilot_result
                if not is_low_quality(cp_reply):
                    return _apply_trace_footer(cp_reply, trace), cp_updated, cp_label
                log.info("Quality retry Copilot reply also low quality — keeping Gemini answer")
    except (ImportError, AttributeError, ValueError) as _qr_exc:
        log.debug("Quality retry gate skipped: %s", _qr_exc)

    return _apply_trace_footer(text, trace), updated_history, model_name


def is_configured() -> bool:
    """Return True if at least one LLM backend is configured.

    Checks Gemini, local LLM, and Copilot proxy so Copilot-only
    deployments are not incorrectly blocked with "LLM not configured".
    """
    from llm.providers import COPILOT_PROXY_ENABLED  # local import avoids circular deps
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED or COPILOT_PROXY_ENABLED


def apply_repair_transparency_footer(text: str, *, repair_result: dict) -> str:
    """W11-4: Append a subtle footer indicating quality repair outcome.

    Args:
        text: The response text to append to.
        repair_result: The dict returned by ``_run_quality_auto_repair``.

    Returns:
        The original text with an appropriate footer appended, or the
        original text unchanged if no repair was performed.
    """
    if not isinstance(repair_result, dict):
        return text
    if repair_result.get("repair_improved"):
        return text + "\n\n✨ *This response was improved by quality auto-repair.*"
    if repair_result.get("repair_skipped"):
        return text + "\n\nℹ️ *Quality review was skipped due to system load.*"
    return text


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
    except (AttributeError, ValueError, RuntimeError, ImportError) as exc:
        log.warning("Thinking model unavailable, falling back to standard model: %s", exc)
        model = await _get_model()

    try:
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
    except Exception as exc:  # broad: intentional
        log.warning("Gemini failed in deep research (%s): %s", type(exc).__name__, exc)
        text, updated_history, _, _ = await _recover_stream_provider_failure(
            failed_provider="gemini",
            model_message=user_message,
            cleaned_user_message=user_message,
            history=history,
            routing_notes=[],
            reason="deep-research",
        )
        return text, updated_history


async def summarize_conversation(history: list[dict]) -> str:
    """Produce a 3-5 sentence summary of a conversation history for long-term memory."""
    if not history:
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
        from llm.providers import COPILOT_PROXY_ENABLED, chat_openai
        from model_routing_policy import select_summarization_route

        route = select_summarization_route(copilot_available=COPILOT_PROXY_ENABLED)
        log.debug("Conversation summary route: %s (%s)", route.provider, route.reason)

        if route.provider == "copilot":
            result = await chat_openai(prompt, history=[], system_prompt="", temperature=0.2, max_tokens=300)
            if result:
                return result.strip()

        # Gemini fallback
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
    except (OSError, ValueError, RuntimeError, AttributeError) as e:
        log.warning("Failed to summarize conversation: %s", e)
        return ""
