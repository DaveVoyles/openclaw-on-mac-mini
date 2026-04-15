"""
OpenClaw LLM Client — Gemini client setup, model config, and tool declarations.
"""

import asyncio
import dataclasses
import datetime
import logging
import os
import threading
from pathlib import Path
from typing import Any

from google import genai

from config import cfg
from spending import tracker as spending_tracker

log = logging.getLogger("openclaw.llm.client")

# ---------------------------------------------------------------------------
# Configuration (sourced from centralized config)
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = cfg.google_api_key
_client: genai.Client | None = None
if GOOGLE_API_KEY:
    _client = genai.Client(api_key=GOOGLE_API_KEY)
MODEL_NAME = cfg.llm_model
MAX_TOKENS = cfg.llm_max_tokens
TEMPERATURE = cfg.llm_temperature
CONFIG_DIR = cfg.config_dir

# Local LLM (Ollama) settings
OLLAMA_URL = cfg.ollama_url
OLLAMA_MODEL = cfg.ollama_model
LOCAL_LLM_ENABLED = cfg.local_llm_enabled

# Deep / thinking mode
THINKING_MODEL = cfg.thinking_model
THINKING_BUDGET = cfg.thinking_budget

# Function-call loop limit
MAX_TOOL_ROUNDS = cfg.llm_max_tool_rounds

# ---------------------------------------------------------------------------
# System prompt (cached with mtime-based invalidation)
# ---------------------------------------------------------------------------

_system_prompt_cache: str | None = None
_system_prompt_mtime: float = 0.0
_system_prompt_lock = threading.Lock()


def _load_system_prompt() -> str:
    """Load the system prompt from config/prompts/system.txt with mtime cache.

    The file content is cached and invalidated on mtime change. The current
    date/time is injected at call time (not cached) so the LLM always has an
    accurate timestamp regardless of how long the process has been running.
    """
    global _system_prompt_cache, _system_prompt_mtime
    prompt_file = CONFIG_DIR / "prompts" / "system.txt"
    try:
        current_mtime = prompt_file.stat().st_mtime if prompt_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    with _system_prompt_lock:
        if _system_prompt_cache is None or current_mtime != _system_prompt_mtime:
            if prompt_file.exists():
                _system_prompt_cache = prompt_file.read_text().strip()
            else:
                _system_prompt_cache = (
                    "You are OpenClaw, a helpful AI assistant managing a home media server. "
                    "Be concise, professional, and use emojis sparingly."
                )
            _system_prompt_mtime = current_mtime

        now = datetime.datetime.now()
        date_header = (
            f"## Current Date & Time\n"
            f"Today is {now.strftime('%A, %B %-d, %Y')}. "
            f"The current time is {now.strftime('%-I:%M %p')}. "
            f"Use this as the authoritative date/time — do NOT use your training cutoff.\n\n"
        )
        return date_header + _system_prompt_cache


# ---------------------------------------------------------------------------
# Tool / function declarations for Gemini
# ---------------------------------------------------------------------------


def _load_tool_declarations() -> list[dict[str, Any]]:
    """Load tool declarations from config/tools.yaml."""
    # Primary: honour explicit env override, then use CONFIG_DIR (same as system prompt)
    tools_file = Path(os.getenv("TOOLS_CONFIG", str(CONFIG_DIR / "tools.yaml")))
    if not tools_file.exists():
        # Fallback: try relative to CWD (local dev layout)
        tools_file = Path("config/tools.yaml")
    if not tools_file.exists():
        # Fallback: relative to this file's parent
        tools_file = Path(__file__).resolve().parent.parent / "config" / "tools.yaml"
    if not tools_file.exists():
        log.error("tools.yaml not found — no tools will be available")
        return []
    import yaml
    with open(tools_file) as f:
        declarations = yaml.safe_load(f)
    log.info("Loaded %d tool declarations from %s", len(declarations), tools_file)
    return declarations


_TOOL_DECLARATIONS: list[dict[str, Any]] = _load_tool_declarations()


def _convert_schema(schema: dict) -> dict:
    """Convert a JSON-Schema-style dict to Gemini Schema keyword args."""
    type_map = {
        "object": genai.types.Type.OBJECT,
        "string": genai.types.Type.STRING,
        "integer": genai.types.Type.INTEGER,
        "number": genai.types.Type.NUMBER,
        "boolean": genai.types.Type.BOOLEAN,
        "array": genai.types.Type.ARRAY,
    }
    result: dict[str, Any] = {"type": type_map.get(schema.get("type", "object"), genai.types.Type.OBJECT)}

    if "properties" in schema:
        result["properties"] = {
            k: genai.types.Schema(
                type=type_map.get(v.get("type", "string"), genai.types.Type.STRING),
                description=v.get("description", ""),
            )
            for k, v in schema["properties"].items()
        }

    if "required" in schema:
        result["required"] = schema["required"]

    return result


def _build_tools(tool_declarations: list[dict[str, Any]] | None = None) -> list:
    """Build the Gemini tools list from declarations."""
    declarations = tool_declarations if tool_declarations is not None else _TOOL_DECLARATIONS
    return [genai.types.Tool(function_declarations=[
        genai.types.FunctionDeclaration(
            name=d["name"],
            description=d["description"],
            parameters=genai.types.Schema(**_convert_schema(d["parameters"])),
        )
        for d in declarations
    ])]


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _ModelConfig:
    """Holds model name + generation config (replaces old GenerativeModel)."""
    model_name: str
    config: genai.types.GenerateContentConfig


_model: _ModelConfig | None = None
_model_system_prompt: str | None = None
_model_lock: asyncio.Lock | None = None


def _init_gemini_model(
    model_name: str,
    *,
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_TOKENS,
    thinking_budget: int | None = None,
    with_tools: bool = True,
    tool_declarations: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
) -> _ModelConfig:
    """Create a _ModelConfig holding model name + generation config.

    Args:
        system_prompt: Override the default loaded system prompt. Pass an empty
                       string to suppress the system prompt entirely, or a custom
                       string to use instead (e.g. for worker sub-agents).
    """
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")

    effective_prompt = _load_system_prompt() if system_prompt is None else system_prompt
    config_kwargs: dict[str, Any] = {
        "system_instruction": effective_prompt,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }

    if with_tools and tool_declarations != []:
        config_kwargs["tools"] = _build_tools(tool_declarations)

    if thinking_budget is not None:
        config_kwargs["thinking_config"] = genai.types.ThinkingConfig(
            thinking_budget=thinking_budget,
        )
        log.info("ThinkingConfig enabled (budget=%d tokens)", thinking_budget)

    return _ModelConfig(
        model_name=model_name,
        config=genai.types.GenerateContentConfig(**config_kwargs),
    )


async def _get_model() -> _ModelConfig:
    """Lazy-init the Gemini model config; reloads when system prompt changes."""
    global _model, _model_system_prompt, _model_lock
    if _model_lock is None:
        _model_lock = asyncio.Lock()
    async with _model_lock:
        system_prompt = _load_system_prompt()
        if _model is not None and _model_system_prompt == system_prompt:
            return _model

        _model = _init_gemini_model(MODEL_NAME, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        _model_system_prompt = system_prompt
        log.info("Gemini model initialized: %s (temp=%.1f, max_tokens=%d)", MODEL_NAME, TEMPERATURE, MAX_TOKENS)
        return _model


def _get_tool_declarations() -> list[dict[str, Any]]:
    """Return the raw tool declarations currently exposed to Gemini."""
    return list(_TOOL_DECLARATIONS)


def _build_model_for_tools(tool_declarations: list[dict[str, Any]]) -> _ModelConfig:
    """Build an uncached Gemini model config for a routed tool shortlist."""
    return _init_gemini_model(
        MODEL_NAME,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        with_tools=bool(tool_declarations),
        tool_declarations=tool_declarations,
    )


# ---------------------------------------------------------------------------
# Thinking / deep-research model
# ---------------------------------------------------------------------------

_thinking_model: _ModelConfig | None = None
_thinking_model_prompt: str | None = None
_thinking_model_lock = threading.Lock()


def _get_thinking_model() -> _ModelConfig:
    """Lazy-init the thinking/deep-research variant of the Gemini model."""
    global _thinking_model, _thinking_model_prompt
    system_prompt = _load_system_prompt()
    with _thinking_model_lock:
        if _thinking_model is not None and _thinking_model_prompt == system_prompt:
            return _thinking_model

        _thinking_model = _init_gemini_model(
            THINKING_MODEL,
            temperature=0.3,
            max_tokens=MAX_TOKENS * 2,
            thinking_budget=THINKING_BUDGET,
        )
        _thinking_model_prompt = system_prompt
        log.info("Thinking model initialized: %s", THINKING_MODEL)
        return _thinking_model


def _reset_models() -> None:
    """Reset all cached model state to force re-initialization."""
    global _model, _model_system_prompt, _thinking_model, _thinking_model_prompt
    _model = None
    _model_system_prompt = None
    _thinking_model = None
    _thinking_model_prompt = None


# ---------------------------------------------------------------------------
# Usage / spending tracker
# ---------------------------------------------------------------------------


async def _record_usage(response) -> None:
    """Extract usage_metadata from a Gemini response and record spending."""
    try:
        meta = response.usage_metadata
        if meta:
            inp = getattr(meta, "prompt_token_count", 0) or 0
            out = getattr(meta, "candidates_token_count", 0) or 0
            if inp or out:
                await spending_tracker.record(inp, out)
    except Exception as e:
        log.warning("Failed to record token usage: %s", e)


async def quick_generate(
    prompt: str,
    *,
    max_tokens: int = 300,
    temperature: float = 0.1,
) -> str:
    """Fire a single-turn completion using the best available provider.

    Intended for narrow, non-streaming tasks (fact extraction, goal detection,
    error diagnosis).  Prefers the Copilot proxy (GPT-4o) when available to
    save Gemini quota; falls back to Gemini when Copilot is unavailable.

    Returns the response text, or an empty string when no backend is
    configured or when generation fails.
    """
    # -- Copilot / OpenAI-compatible path --------------------------------
    try:
        from llm.providers import (
            COPILOT_PROXY_ENABLED,
            chat_openai,  # local import
        )
        if COPILOT_PROXY_ENABLED:
            reply = await chat_openai(
                prompt,
                [],
                "You are a concise assistant.  Reply with just the requested information.",
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return (reply or "").strip()
    except Exception as exc:
        log.debug("quick_generate Copilot path failed, falling back to Gemini: %s", exc)

    # -- Gemini fallback --------------------------------------------------
    if not _client:
        return ""
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: _client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            ),
        )
        await _record_usage(response)
        return (response.text or "").strip()
    except Exception as exc:
        log.warning("quick_generate failed: %s", exc)
        return ""
