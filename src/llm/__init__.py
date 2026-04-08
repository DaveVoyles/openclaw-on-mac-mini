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
from llm_tools import (  # noqa: F401
    _execute_function_call,
    _extract_final_text,
    _extract_history,
    _run_tool_loop,
)

# Re-exports from package sub-modules
from .chat import (  # noqa: F401
    _gemini_chat,
    chat,
    chat_deep,
    chat_stream,
    get_rate_info,
    summarize_conversation,
)


def is_configured() -> bool:
    """Return True if at least one LLM backend is configured.

    Reads GOOGLE_API_KEY and LOCAL_LLM_ENABLED from this package's namespace
    so that tests can patch them with ``monkeypatch.setattr(llm, ...)``.
    """
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED
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
from .response import (  # noqa: F401
    SUPPORTED_IMAGE_MIMES,
    analyze_document,
    analyze_image,
    analyze_image_with_tools,
)
from .tool_execution import (  # noqa: F401
    _chat_ollama,
    _get_ollama_session,
    _ollama_available,
    _try_local_model,
    close_sessions,
)
