"""
OpenClaw LLM Tools — tool execution loop, caching, and response extraction.
"""

import asyncio
import hashlib
import logging
import time
from typing import Any

from google import genai

from llm_client import MAX_TOOL_ROUNDS, _record_usage
from llm_ratelimit import rate_limiter as _rate_limiter
from skills import SKILLS

log = logging.getLogger("openclaw.llm.tools")

# ---------------------------------------------------------------------------
# Tool result TTL cache — avoid redundant calls for read-only snapshot tools
# ---------------------------------------------------------------------------

_CACHEABLE_TOOLS: frozenset[str] = frozenset({
    "get_system_stats",
    "get_docker_stats",
    "get_nas_storage_health",
    "get_nas_alerts",
    "get_disk_smart_status",
    "get_backup_status",
    "get_uptime",
    "check_arr_health",
    "check_download_clients",
    "check_plex_status",
    "get_plex_activity",
    "get_network_status",
    "get_tailscale_status",
})
_TOOL_CACHE_TTL = 30  # seconds
_TOOL_CACHE_MAX_SIZE = 256

# {"tool_name|arg_hash": (result, timestamp)}
_tool_cache: dict[str, tuple[str, float]] = {}

# Skill invocation counters
_skill_call_counts: dict[str, int] = {}


def get_skill_stats() -> dict[str, int]:
    """Return skill call counts sorted by frequency (descending)."""
    return dict(sorted(_skill_call_counts.items(), key=lambda x: x[1], reverse=True))


def _cache_key(name: str, args: dict) -> str:
    return f"{name}|{hashlib.md5(str(sorted(args.items())).encode()).hexdigest()[:8]}"


def _evict_tool_cache() -> None:
    """Evict expired entries; if still over max, drop oldest."""
    now = time.monotonic()
    expired = [k for k, (_, ts) in _tool_cache.items() if now - ts >= _TOOL_CACHE_TTL]
    for k in expired:
        del _tool_cache[k]
    while len(_tool_cache) > _TOOL_CACHE_MAX_SIZE:
        oldest_key = min(_tool_cache, key=lambda k: _tool_cache[k][1])
        del _tool_cache[oldest_key]


# ---------------------------------------------------------------------------
# Execute a function call from the LLM
# ---------------------------------------------------------------------------


async def _execute_function_call(name: str, args: dict) -> str:
    """Look up and execute a skill by name, returning the string result."""
    from tool_health import circuit_breaker, tool_health

    skill_fn = SKILLS.get(name)
    if skill_fn is None:
        return f"Unknown function: {name}"

    # Circuit breaker: fast-fail on repeatedly broken tools
    if circuit_breaker.is_open(name):
        return f"⚠️ {name} is temporarily unavailable (circuit open — recent failures). Try an alternative approach."

    # Track invocation count
    _skill_call_counts[name] = _skill_call_counts.get(name, 0) + 1

    # Return cached result for read-only snapshot tools if still fresh
    if name in _CACHEABLE_TOOLS:
        key = _cache_key(name, args)
        if key in _tool_cache:
            cached_result, cached_at = _tool_cache[key]
            if time.monotonic() - cached_at < _TOOL_CACHE_TTL:
                log.debug("Returning cached result for %s (age: %.1fs)", name, time.monotonic() - cached_at)
                return cached_result

    log.info("LLM invoking skill: %s(%s)", name, args)
    try:
        result = await skill_fn(**args)
        if not isinstance(result, str):
            result = str(result)
        if name in _CACHEABLE_TOOLS:
            _tool_cache[_cache_key(name, args)] = (result, time.monotonic())
            _evict_tool_cache()
        circuit_breaker.record_success(name)
        tool_health.record(name, success=True)
        return result
    except Exception as e:
        log.error("Skill %s failed: %s", name, e)
        circuit_breaker.record_failure(name)
        tool_health.record(name, success=False)
        return f"Error executing {name}: {e}"


# ---------------------------------------------------------------------------
# Shared tool-calling loop (used by chat and chat_deep)
# ---------------------------------------------------------------------------


async def _run_tool_loop(
    chat_session,
    response,
    *,
    max_rounds: int = MAX_TOOL_ROUNDS,
    on_tool_call: Any | None = None,
    parallel: bool = True,
    label: str = "LLM",
) -> tuple[Any, int]:
    """Execute the function-call loop on *chat_session*.

    Returns ``(final_response, rounds_executed)``.

    When *parallel* is True (default for normal chat), all function_call
    parts in a single response are gathered concurrently.  When False
    (deep research), only the first function_call part is executed per
    round — matching the sequential research pattern that's easier to
    follow in Discord progress updates.
    """
    loop = asyncio.get_running_loop()
    rounds = 0

    while rounds < max_rounds:
        # Collect function_call parts from this response
        try:
            all_parts = response.candidates[0].content.parts
        except (IndexError, AttributeError):
            break

        function_calls = [
            (part.function_call.name, dict(part.function_call.args) if part.function_call.args else {})
            for part in all_parts
            if hasattr(part, "function_call") and part.function_call and part.function_call.name
        ]

        if not function_calls:
            break

        # In sequential mode, process only the first call per round
        if not parallel:
            function_calls = function_calls[:1]

        log.info("%s function call(s) [round %d]: %s", label, rounds + 1,
                 ", ".join(f"{n}({a})" for n, a in function_calls))

        # Fire progress callbacks (before execution — with args)
        if on_tool_call:
            for fn_name, fn_args in function_calls:
                try:
                    await on_tool_call(fn_name, rounds + 1, args=fn_args)
                except Exception as exc:
                    log.debug("on_tool_call callback failed: %s", exc)

        # Execute tool calls
        results = await asyncio.gather(*[
            _execute_function_call(fn_name, fn_args)
            for fn_name, fn_args in function_calls
        ])

        # Fire progress callbacks (after execution — with result preview)
        if on_tool_call:
            for (fn_name, _), result in zip(function_calls, results):
                try:
                    await on_tool_call(fn_name, rounds + 1, result_preview=result[:200])
                except Exception as exc:
                    log.debug("on_tool_call result callback failed: %s", exc)

        # Rate-limit check before sending results back
        _rate_limiter.record()
        if not _rate_limiter.check():
            # Build a fake text-only response — caller handles this
            return response, rounds + 1

        # Send all function results back to the model
        response_parts = [
            genai.types.Part(
                function_response=genai.types.FunctionResponse(
                    name=fn_name,
                    response={"result": result},
                )
            )
            for (fn_name, _), result in zip(function_calls, results)
        ]

        response = await loop.run_in_executor(
            None,
            lambda parts=response_parts: chat_session.send_message(parts),
        )
        await _record_usage(response)
        rounds += 1

    return response, rounds


# ---------------------------------------------------------------------------
# Response text extraction
# ---------------------------------------------------------------------------


def _extract_final_text(response, rounds: int, chat_session) -> str:
    """Pull the final answer text out of *response*, requesting synthesis if needed."""
    try:
        text = response.text
    except (AttributeError, ValueError):
        try:
            parts = response.candidates[0].content.parts
            text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
        except Exception as exc:
            log.debug("Response text extraction fallback failed: %s", exc)
            text = ""

        if not text and rounds >= MAX_TOOL_ROUNDS:
            log.info("Tool round limit hit with no synthesis — requesting forced summary")
            try:
                _rate_limiter.record()
                synthesis_response = chat_session.send_message(
                    "You have reached the maximum number of tool calls. "
                    "Please synthesize everything you have gathered so far "
                    "into a final, helpful answer for the user. "
                    "Do not call any more tools."
                )
                # Note: usage recording must happen in the caller for async compat
                text = synthesis_response.text
            except Exception as e:
                log.error("Forced synthesis failed: %s", e)

        if not text:
            text = "I processed your request but the model returned no text content."
            if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                text += f" (Safety/Blocked: {response.prompt_feedback})"

    if rounds >= MAX_TOOL_ROUNDS:
        text += f"\n\n⚠️ *Tool call limit reached ({MAX_TOOL_ROUNDS}) — some sources may not have been checked.*"
    return text


# ---------------------------------------------------------------------------
# Chat history extraction
# ---------------------------------------------------------------------------


def _extract_history(chat_session) -> list[dict]:
    """Convert a ChatSession's history to our serializable format."""
    history = []
    for content in chat_session.get_history():
        parts = []
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
            elif hasattr(part, "function_call") and part.function_call and part.function_call.name:
                parts.append(f"[Called {part.function_call.name}]")
            elif hasattr(part, "function_response") and part.function_response and part.function_response.name:
                parts.append(f"[Result from {part.function_response.name}]")
        if parts:
            history.append({"role": content.role, "parts": parts})
    return history
