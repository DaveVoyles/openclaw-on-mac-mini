"""
OpenClaw LLM Integration \u2014 Phase 5: Gemini + Function Calling

Public API facade \u2014 delegates to llm_client, llm_tools, llm_patterns,
and llm_ratelimit for implementation details.

Hybrid routing (auto mode):
  - Copilot proxy (GPT-4o via local proxy)  \u2192 FREE, tried first
  - Gemini 2.0 Flash                        \u2192 cheap backup, full tool support
  - Ollama                                   \u2192 only when explicitly requested via /ask model:local
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory import Conversation

import aiohttp
from google import genai

from config import cfg

# ---------------------------------------------------------------------------
# Re-exports from sub-modules (preserves backward-compatible imports)
# ---------------------------------------------------------------------------
from llm_client import (  # noqa: F401
    _TOOL_DECLARATIONS,
    GOOGLE_API_KEY,
    LOCAL_LLM_ENABLED,
    MAX_TOKENS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
    OLLAMA_MODEL,
    OLLAMA_URL,
    TEMPERATURE,
    THINKING_BUDGET,
    THINKING_MODEL,
    _client,
    _get_model,
    _get_thinking_model,
    _init_gemini_model,
    _load_system_prompt,
    _ModelConfig,
    _record_usage,
    _reset_models,
)
from llm_patterns import (  # noqa: F401
    _FACTUAL_QUESTION_RE,
    _GEMMA_HALLUCINATION_RE,
    _GEMMA_WEAK_DOMAINS,
    _LIVE_ACTION_PATTERN,
    _VAGUE_RESPONSE_RE,
    _gemma_response_seems_valid,
    _needs_tools,
    _reflect_on_response,
)
from llm_ratelimit import RateLimiter  # noqa: F401
from llm_ratelimit import rate_limiter as _rate_limiter
from llm_tools import (  # noqa: F401
    _execute_function_call,
    _extract_final_text,
    _extract_history,
    _run_tool_loop,
)
from skills import SKILLS

log = logging.getLogger("openclaw.llm")


def _to_content(msg: dict) -> dict:
    """Convert internal history message to genai-compatible ContentDict.

    Internal history stores parts as plain strings, but the google-genai SDK
    requires Part objects (dicts with 'text' key).
    """
    parts = []
    for p in msg.get("parts", []):
        if isinstance(p, str):
            parts.append({"text": p})
        elif isinstance(p, dict):
            parts.append(p)
        else:
            parts.append({"text": str(p)})
    return {"role": msg["role"], "parts": parts}


# ---------------------------------------------------------------------------
# Ollama \u2014 local LLM for simple / conversational queries
# ---------------------------------------------------------------------------

_ollama_session: aiohttp.ClientSession | None = None
_ollama_session_lock: asyncio.Lock | None = None


async def _get_ollama_session() -> aiohttp.ClientSession:
    """Return the shared Ollama aiohttp session, (re)creating if closed."""
    global _ollama_session, _ollama_session_lock
    if _ollama_session_lock is None:
        _ollama_session_lock = asyncio.Lock()
    async with _ollama_session_lock:
        if _ollama_session is None or _ollama_session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            _ollama_session = aiohttp.ClientSession(connector=connector)
        return _ollama_session


async def _ollama_available() -> bool:
    """Return True if Ollama is reachable and the model is loaded."""
    try:
        session = await _get_ollama_session()
        async with session.get(
            f"{OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=3)
        ) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return any(OLLAMA_MODEL.split(":")[0] in m for m in models)
    except Exception as exc:
        log.debug("Ollama availability check failed: %s", exc)
        return False


async def _chat_ollama(
    user_message: str,
    history: list[dict],
    system_prompt: str,
) -> str | None:
    """Send a message to Ollama's /api/chat endpoint.
    Returns the response text, or None on failure.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        role = msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
    }

    try:
        session = await _get_ollama_session()
        async with session.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                log.warning("Ollama returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            return data.get("message", {}).get("content") or None
    except asyncio.TimeoutError:
        log.warning("Ollama request timed out")
        return None
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return None


# ---------------------------------------------------------------------------
# chat() helper decomposition
# ---------------------------------------------------------------------------

# Per-model context limits
_CONTEXT_LIMITS = {
    "gemini": {"max_turns": 50, "max_chars": 500_000},
    "ollama": {"max_turns": 40, "max_chars": 400_000},
    "default": {"max_turns": 20, "max_chars": 80_000},
}


def _get_context_limits(model_hint: str = "default") -> tuple[int, int]:
    """Return (max_turns, max_chars) for the given model."""
    limits = _CONTEXT_LIMITS.get(model_hint, _CONTEXT_LIMITS["default"])
    return limits["max_turns"], limits["max_chars"]


def _estimate_chars(history: list[dict]) -> int:
    """Rough character count of conversation history."""
    total = 0
    for msg in history:
        for p in msg.get("parts", []):
            if isinstance(p, str):
                total += len(p)
    return total


async def _trim_history(
    history: list[dict],
    model_hint: str = "default",
    *,
    conversation: "Conversation | None" = None,
) -> list[dict]:
    """Keep first 2 turns (persona context) + last N to avoid context overflow."""
    max_turns, max_chars = _get_context_limits(model_hint)

    should_summarize = (
        len(history) >= 40
        and conversation is not None
        and not conversation.summarized
        and GOOGLE_API_KEY
    )

    if should_summarize:
        original_len = len(history)
        summarize_end = min(22, len(history))
        turns_to_summarize = history[2:summarize_end]

        if turns_to_summarize:
            try:
                summary_text = await _generate_context_summary(turns_to_summarize)
                if summary_text:
                    summary_turn = {
                        "role": "model",
                        "parts": [f"[Session Summary] {summary_text}"],
                    }
                    history = history[:2] + [summary_turn] + history[summarize_end:]
                    conversation.summarized = True
                    log.info(
                        "Context auto-summarized: %d turns \u2192 %d turns",
                        original_len,
                        len(history),
                    )
            except Exception as exc:
                log.warning("Auto-summarization failed, falling back to drop: %s", exc)

    if len(history) > max_turns:
        history = history[:2] + history[-(max_turns - 2):]

    while len(history) > 4 and _estimate_chars(history) > max_chars:
        history = history[:2] + history[3:]
        log.debug("Trimmed history to %d turns (%d chars)", len(history), _estimate_chars(history))

    return list(history)


async def _generate_context_summary(turns: list[dict]) -> str:
    """Summarize a block of conversation turns into a compact bullet-point summary."""
    lines: list[str] = []
    for msg in turns:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = " ".join(str(p) for p in msg["parts"] if isinstance(p, str))[:300]
        if content:
            lines.append(f"{role}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    prompt = (
        "Summarize this conversation so far in 3-5 bullet points, "
        "preserving key facts, decisions, and findings.\n\n"
        f"Conversation:\n{transcript}"
    )

    summary_config = genai.types.GenerateContentConfig(
        max_output_tokens=500,
        temperature=0.1,
    )
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=summary_config,
        ),
    )
    return response.text.strip()


async def _try_local_model(
    user_message: str, history: list[dict], *, force: bool = False,
) -> str | None:
    """Attempt to serve via Gemma/Ollama. Returns reply text or None to fall through."""
    if not LOCAL_LLM_ENABLED:
        return None

    if not force and _needs_tools(user_message) and cfg.ollama_tools_enabled:
        if await _ollama_available():
            try:
                from ollama_tools import chat_ollama_with_tools
                system_prompt = _load_system_prompt()
                reply, tools_used = await chat_ollama_with_tools(
                    user_message, history, system_prompt, _TOOL_DECLARATIONS,
                    _execute_function_call,
                    ollama_url=OLLAMA_URL, ollama_model=OLLAMA_MODEL,
                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                )
                if reply and tools_used:
                    log.info("Served by Ollama with tools (%d calls): %.60s\u2026",
                             len(tools_used), user_message)
                    return reply
            except Exception as e:
                log.info("Ollama tool calling failed, falling back: %s", e)

    if not force and _needs_tools(user_message):
        return None
    if not await _ollama_available():
        log.debug("Gemma/Ollama not reachable, using Gemini")
        return None

    system_prompt = _load_system_prompt()
    gemma_reply = await _chat_ollama(user_message, history, system_prompt)

    if gemma_reply and _gemma_response_seems_valid(gemma_reply):
        log.info("Served by Gemma (%s): %.60s\u2026", OLLAMA_MODEL, user_message)
        return gemma_reply

    if gemma_reply:
        log.info("Gemma response failed validation (hallucination signals detected), falling back to Gemini")
    else:
        log.info("Gemma returned empty response, falling back to Gemini")
    return None


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
            "\u26a0\ufe0f Rate limit reached. Please wait a moment before asking again. "
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


# ---------------------------------------------------------------------------
# Auto-RAG
# ---------------------------------------------------------------------------


async def _auto_recall_context(user_message: str) -> str:
    """Fetch recalled context from the vector store for Auto-RAG injection."""
    if not cfg.auto_recall_enabled:
        return ""

    parts = []

    try:
        import vector_store
        context = await vector_store.recall_for_context(user_message)
        if context:
            parts.append(context)
    except Exception as e:
        log.debug("Auto-RAG vector recall failed (non-fatal): %s", e)

    try:
        from user_profile import get_profile_prompt
        profile = get_profile_prompt()
        if profile and profile.strip():
            parts.append(profile)
    except Exception as e:
        log.debug("Auto-RAG profile injection failed (non-fatal): %s", e)

    try:
        from rules_engine import get_relevant_rules
        rules = await get_relevant_rules(user_message, top_k=3)
        if rules:
            rules_block = "[Active Rules]\n" + "\n".join(f"- {r}" for r in rules)
            parts.append(rules_block)
    except Exception as e:
        log.debug("Auto-RAG rules injection failed (non-fatal): %s", e)

    if parts:
        combined = "\n\n".join(parts)
        count = combined.count("\n- ")
        log.info(
            "Auto-RAG: injected %d context items for: %.60s\u2026",
            count,
            user_message,
        )
        return combined

    return ""


def _strip_recalled_prefix(history: list[dict], original: str, augmented: str) -> list[dict]:
    """Remove the Auto-RAG context prefix from the last user turn in history."""
    if original == augmented:
        return history
    for entry in reversed(history):
        if entry.get("role") == "user":
            entry["parts"] = [
                original if p == augmented else p for p in entry["parts"]
            ]
            break
    return history


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------

async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
):
    """Async generator yielding ``(chunk_text, is_final, metadata)`` tuples."""
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

            from model_router import chat_anthropic, chat_openai, classify_query
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
            )
            log.debug("Model router (stream): %s", route)

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
            yield "\u26a0\ufe0f Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        if not await _ollama_available():
            yield "\u26a0\ufe0f Ollama is not reachable. Check that the service is running.", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
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
            yield "\u26a0\ufe0f Gemini API key not configured (`GOOGLE_API_KEY`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
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
                    _routing_notes.append("Gemini rate-limited \u2192 used Copilot proxy")
                    yield reply, True, {"model_used": f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False, "routing_notes": _routing_notes}
                    return
        except Exception:
            pass
        msg = (
            "\u26a0\ufe0f Rate limit reached. Please wait a moment before asking again. "
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
        yield f"\u274c **LLM Error:** {e}", True, {"model_used": model_name, "updated_history": history, "needs_tools": False, "routing_notes": _routing_notes}
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
            accumulated = f"\u274c Streaming error: {e}"

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
      - ``"auto"``  \u2014 Copilot proxy first (free), then Gemini with tools
      - ``"local"`` \u2014 force Ollama/Gemma; error if unavailable
      - ``"gemini"`` \u2014 skip everything, go straight to Gemini
    """
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

            from model_router import chat_anthropic, chat_openai, classify_query
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
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
            return "\u26a0\ufe0f Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", history, "none"
        if not await _ollama_available():
            return "\u26a0\ufe0f Ollama is not reachable. Check that the service is running.", history, "none"
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
            return "\u26a0\ufe0f Gemini API key not configured (`GOOGLE_API_KEY`).", history, "none"
        if not _rate_limiter.check():
            return (
                "\u26a0\ufe0f Rate limit reached. Please wait a moment before asking again. "
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
            "\u26a0\ufe0f Rate limit reached. Please wait a moment before asking again. "
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


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


async def close_sessions() -> None:
    """Close all persistent aiohttp sessions. Call on bot shutdown."""
    global _ollama_session
    if _ollama_session is not None and not _ollama_session.closed:
        await _ollama_session.close()
        _ollama_session = None
        log.info("Closed Ollama aiohttp session")


def is_configured() -> bool:
    """Return True if a Google API key is set (Gemini) OR local LLM is enabled."""
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED


def get_rate_info() -> str:
    """Return a human-readable rate limit status for Gemini Flash."""
    return f"{_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining"


# ---------------------------------------------------------------------------
# Deep research chat
# ---------------------------------------------------------------------------


async def chat_deep(
    user_message: str,
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """Deep research chat \u2014 always uses Gemini with extended thinking.

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


# ---------------------------------------------------------------------------
# Phase 8: Multimodal helpers (image + document analysis)
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/webp",
    "image/heic", "image/heif", "image/gif",
}


async def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = "Describe this image in detail. Note any text, errors, or important information.",
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> str:
    """Analyze an image using Gemini's multimodal vision capabilities."""
    if not GOOGLE_API_KEY:
        return "\u274c GOOGLE_API_KEY not configured."
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"\u274c Unsupported image type: {mime_type}"

    if _needs_tools(prompt):
        text, _ = await analyze_image_with_tools(
            image_bytes, mime_type, prompt,
            history=history, on_tool_call=on_tool_call,
        )
        return text

    try:
        image_part = genai.types.Part(
            inline_data=genai.types.Blob(mime_type=mime_type, data=image_bytes)
        )
        text_part = genai.types.Part(text=prompt)

        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=genai.types.Content(parts=[image_part, text_part]),
            config=genai.types.GenerateContentConfig(
                max_output_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            ),
        )
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Image analysis failed: %s", e)
        return f"\u274c Image analysis failed: {e}"


async def analyze_image_with_tools(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = "Describe this image in detail. Note any text, errors, or important information.",
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """Analyze an image using the main tool-enabled model.

    Returns (response_text, updated_history).
    """
    if not GOOGLE_API_KEY:
        return "\u274c GOOGLE_API_KEY not configured.", history or []
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"\u274c Unsupported image type: {mime_type}", history or []

    history = await _trim_history(history or [], model_hint="gemini")

    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "\u26a0\ufe0f Rate limit reached. Please wait a moment.",
            history,
        )

    model = await _get_model()

    gemini_history = [_to_content(msg) for msg in history]

    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    image_part = genai.types.Part(
        inline_data=genai.types.Blob(mime_type=mime_type, data=image_bytes)
    )
    text_part = genai.types.Part(text=prompt)
    multimodal_parts = [image_part, text_part]

    loop = asyncio.get_running_loop()
    _rate_limiter.record()

    try:
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(multimodal_parts)
        )
        await _record_usage(response)
    except Exception as e:
        log.error("Image analysis with tools failed: %s", e)
        return f"\u274c Image analysis failed: {e}", history

    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=MAX_TOOL_ROUNDS,
        on_tool_call=on_tool_call,
        parallel=True,
        label="Vision+Tools",
    )

    text = _extract_final_text(response, rounds, chat_session)
    updated_history = _extract_history(chat_session)

    return text, updated_history


async def analyze_document(text: str, prompt: str) -> str:
    """Analyze document text using Gemini (no tool loop)."""
    if not GOOGLE_API_KEY:
        return "\u274c GOOGLE_API_KEY not configured."

    doc_config = genai.types.GenerateContentConfig(
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    full_prompt = f"{prompt}\n\n---\n\n{text}"

    try:
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=full_prompt,
            config=doc_config,
        )
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Document analysis failed: %s", e)
        return f"\u274c Document analysis failed: {e}"
