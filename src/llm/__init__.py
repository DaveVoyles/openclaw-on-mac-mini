"""
OpenClaw LLM Integration — Phase 5: Gemini + Function Calling

Public API facade — delegates to llm_client, llm_tools, llm_patterns,
and llm_ratelimit for implementation details.

Hybrid routing (auto mode):
  - Copilot proxy (GPT-4o via local proxy)  → FREE, tried first
  - Gemini 2.0 Flash                        → cheap backup, full tool support
  - Ollama                                   → only when explicitly requested via /ask model:local
"""

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
    _build_tools,
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
from llm_ratelimit import rate_limiter as _rate_limiter  # noqa: F401

# ---------------------------------------------------------------------------
# Lazy re-exports — breaks the circular import chain:
#   llm/__init__.py → llm_tools/llm.chat/llm.response/llm.tool_execution
#                   → skills (ModuleNotFoundError in test/dev)
#
# Modules that are safe to import eagerly (no transitive skills import):
#   llm_client, llm_patterns, llm_ratelimit, llm.context
# Modules that require lazy loading (import llm_tools → skills):
#   llm_tools, llm.chat, llm.response, llm.tool_execution
# ---------------------------------------------------------------------------
_LAZY_EXPORTS: dict[str, str] = {
    # from llm_tools
    "_execute_function_call": "llm_tools",
    "_extract_final_text": "llm_tools",
    "_extract_history": "llm_tools",
    "_run_tool_loop": "llm_tools",
    # from llm.chat
    "_gemini_chat": "llm.chat",
    "chat": "llm.chat",
    "chat_deep": "llm.chat",
    "chat_stream": "llm.chat",
    "get_rate_info": "llm.chat",
    "summarize_conversation": "llm.chat",
    # from llm.response
    "SUPPORTED_IMAGE_MIMES": "llm.response",
    "analyze_document": "llm.response",
    "analyze_image": "llm.response",
    "analyze_image_with_tools": "llm.response",
    # from llm.providers
    "record_skill_tokens": "llm.providers",
    # from llm.tool_execution
    "_chat_ollama": "llm.tool_execution",
    "_get_ollama_session": "llm.tool_execution",
    "_ollama_available": "llm.tool_execution",
    "_try_local_model": "llm.tool_execution",
    "close_sessions": "llm.tool_execution",
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value  # cache so subsequent lookups skip __getattr__
        return value
    raise AttributeError(f"module 'llm' has no attribute {name!r}")


def is_configured() -> bool:
    """Return True if at least one LLM backend is configured.

    Checks Gemini (GOOGLE_API_KEY), local LLM (LOCAL_LLM_ENABLED), and
    the Copilot proxy (COPILOT_PROXY_URL) so that Copilot-only deployments
    are not incorrectly blocked.
    """
    from llm.providers import COPILOT_PROXY_ENABLED  # local import avoids circular deps

    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED or COPILOT_PROXY_ENABLED


from .context import (  # noqa: F401
    _CONTEXT_LIMITS,
    _auto_recall_context,
    _estimate_chars,
    _generate_context_summary,
    _get_context_limits,
    _strip_recalled_prefix,
    _to_content,
    _trim_history,
)
