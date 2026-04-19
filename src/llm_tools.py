"""
OpenClaw LLM Tools — tool execution loop, caching, and response extraction.
"""

import hashlib
import logging
import time
from typing import Any

from llm_client import MAX_TOOL_ROUNDS, _record_usage
from llm_ratelimit import rate_limiter as _rate_limiter
from skills import SKILLS
from tool_orchestration import GeminiToolAdapter, ToolOrchestrator

log = logging.getLogger(__name__)

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
    except Exception as e:  # broad: intentional
        circuit_breaker.record_failure(name)
        tool_health.record(name, success=False)
        return f"Error executing {name}: {e}"


def _should_return_tool_result_directly(name: str, result: str) -> bool:
    """Return True when *result* from *name* should bypass LLM synthesis.

    Delegates to the centralized answer policy.
    """
    from answer_policy import should_return_directly
    return should_return_directly(name, result)


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
    orchestrator = ToolOrchestrator(
        adapter=GeminiToolAdapter(),
        execute_tool_call=_execute_function_call,
        rate_limiter=_rate_limiter,
        record_usage=_record_usage,
        should_return_tool_result_directly=_should_return_tool_result_directly,
    )
    return await orchestrator.run(
        chat_session,
        response,
        max_rounds=max_rounds,
        on_tool_call=on_tool_call,
        parallel=parallel,
        label=label,
    )


# ---------------------------------------------------------------------------
# Response text extraction
# ---------------------------------------------------------------------------


def _extract_final_text(response, rounds: int, chat_session) -> str:
    """Compatibility wrapper around the Gemini adapter's final-text extraction."""
    return GeminiToolAdapter().extract_final_text(
        response,
        rounds,
        chat_session,
        max_rounds=MAX_TOOL_ROUNDS,
    )


# ---------------------------------------------------------------------------
# Chat history extraction
# ---------------------------------------------------------------------------


def _extract_history(chat_session) -> list[dict]:
    """Compatibility wrapper around the Gemini adapter's history extraction."""
    return GeminiToolAdapter().extract_history(chat_session)


def _merge_direct_final_history(history: list[dict], text: str) -> list[dict]:
    """Compatibility wrapper around the Gemini adapter's direct-final history shaping."""
    return GeminiToolAdapter().merge_direct_final_history(history, text)
