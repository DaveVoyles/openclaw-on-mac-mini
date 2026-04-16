"""
OpenClaw Model Router — Phase 8: Multi-Model Smart Routing
Classifies query types and routes to the optimal model backend.

Routing strategy:
  - Code-heavy non-tool queries → Copilot/Claude first when proxy is enabled
  - Multimodal (images) → Gemini (best multimodal support)
  - Research / deep analysis → Copilot first when proxy is enabled
  - Simple chat → Copilot first when proxy is enabled, else Ollama/Gemma
  - Tool-requiring → Gemini (native function calling)
  - Everything else → Gemini (reliable default)
"""

import asyncio
import logging
import os
import re
import time
from typing import Optional

import aiohttp

from http_session import SessionManager as _SessionManager
from model_routing_policy import select_auto_route, select_tool_route

_router_sessions = _SessionManager(timeout=60, name="model-router")

from config import cfg as _router_cfg

log = logging.getLogger("openclaw.model_router")

# Copilot proxy configuration — single source of truth is llm.providers
# Re-exported here for backward compat with any remaining callers.
from llm.providers import COPILOT_PROXY_ENABLED, COPILOT_PROXY_URL  # re-export  # noqa: E402

# Ollama health-check state (cached for 30 s)
_OLLAMA_URL = _router_cfg.ollama_url
_ollama_last_check: dict = {"alive": True, "ts": 0.0}


async def is_ollama_alive(url: str = "") -> bool:
    """Fast pre-flight check — cached for 30 s to avoid spamming Ollama."""
    url = url or _OLLAMA_URL
    now = time.monotonic()
    if now - _ollama_last_check["ts"] < 30:
        return _ollama_last_check["alive"]
    try:
        s = await _router_sessions.get()
        async with s.get(
            f"{url}/api/tags",
            timeout=aiohttp.ClientTimeout(total=2),
        ) as r:
            alive = r.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError):
        alive = False
    _ollama_last_check.update(alive=alive, ts=now)
    if not alive:
        log.info("Ollama health check failed — routing will prefer Gemini")
    return alive

# Query type classifications
_CODE_PATTERN = re.compile(
    r"\b(write|create|generate|fix|debug|refactor|review|explain)\s+(a\s+)?"
    r"(code|script|function|class|program|snippet|regex|query|sql)\b"
    r"|\b(python|javascript|typescript|rust|go|java|c\+\+|bash|shell|html|css)\b.{0,40}\b(code|script|function|error|bug)\b"
    r"|\bcode\s+review\b"
    r"|\b(stack\s*trace|traceback|syntax\s+error|compile\s+error|runtime\s+error)\b",
    re.IGNORECASE,
)

_CREATIVE_PATTERN = re.compile(
    r"\b(write|compose|draft|create)\s+(a\s+)?"
    r"(story|poem|essay|article|blog\s+post|letter|email\s+draft|speech|song|haiku)\b"
    r"|\b(creative\s+writing|brainstorm|ideate)\b",
    re.IGNORECASE,
)

_ANALYSIS_PATTERN = re.compile(
    r"\b(analyze|compare|evaluate|assess|critique|summarize|synthesize)\b.{0,60}"
    r"\b(data|report|document|paper|article|research|findings|results|trends)\b"
    r"|\b(pros?\s+and\s+cons?|trade[\s-]?offs?|swot|cost[\s-]?benefit)\b",
    re.IGNORECASE,
)

_REASONING_PATTERN = re.compile(
    r"\b(prove|proof|deduce|infer|logical|reasoning|step[\s-]by[\s-]step|"
    r"mathematically|theorem|equation|formula|algorithm|optimize|complexity|"
    r"O\(n\)|solve|calculate|compute|derive)\b"
    r"|\b(math|calculus|statistics|probability|linear\s+algebra|"
    r"differential\s+equation)\b",
    re.IGNORECASE,
)


class ModelRoute:
    """Represents a routing decision."""

    __slots__ = ("model_type", "reason")

    def __init__(self, model_type: str, reason: str):
        self.model_type = model_type  # "gemini", "ollama", "openai", "anthropic", "copilot"
        self.reason = reason

    def __repr__(self):
        return f"ModelRoute({self.model_type!r}, {self.reason!r})"


def copilot_model_for_message(message: str) -> str:
    """Choose the proxy model to use for a Copilot-routed message.

    Selection priority:
    1. Code queries → Claude Sonnet (best code quality via proxy)
    2. Reasoning/math → o1-mini (step-by-step reasoning model)
    3. Default → GPT-4o
    """
    msg = message or ""
    if _CODE_PATTERN.search(msg):
        return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")
    if _REASONING_PATTERN.search(msg):
        return os.getenv("COPILOT_REASONING_MODEL", "o1-mini")
    return os.getenv("OPENAI_MODEL", "gpt-4o")


def classify_query(
    message: str,
    *,
    has_openai_key: bool = False,
    has_anthropic_key: bool = False,
    copilot_available: bool = False,
    has_image: bool = False,
    needs_tools: bool = False,
    model_preference: str = "auto",
    ollama_alive: bool = True,
    routing_profile: str = "",
    recalled_context: bool = False,
) -> ModelRoute:
    """Classify a query and return the optimal model route.

    Priority order:
    1. Explicit user preference (not "auto") → honor it
    2. Image attached → Gemini (best multimodal)
    3. Needs tools → Gemini (native function calling)
    4. Non-tool ask + Copilot proxy available → Copilot-first path
    5. Code query + Claude available → Anthropic (best code quality)
    6. Creative writing + GPT available → OpenAI (strong creative)
    7. Analysis/research → Gemini
    8. Simple chat → Ollama (free, fast) — falls back to Gemini if Ollama is down
    9. Default → Gemini
    """
    # Honor explicit preference
    if model_preference == "local":
        if not ollama_alive:
            return ModelRoute("gemini", "user preference: local but Ollama down — fallback to Gemini")
        return ModelRoute("ollama", "user preference: local")
    if model_preference == "gemini":
        return ModelRoute("gemini", "user preference: gemini")
    if model_preference == "copilot":
        if copilot_available:
            return ModelRoute("copilot", "user preference: copilot")
        return ModelRoute("gemini", "user preference: copilot but proxy unavailable — fallback to Gemini")
    if model_preference == "openai" and (has_openai_key or copilot_available):
        return ModelRoute("openai", "user preference: openai")
    if model_preference == "anthropic" and (has_anthropic_key or copilot_available):
        return ModelRoute("anthropic", "user preference: anthropic")

    # Image → Gemini (best multimodal)
    if has_image:
        return ModelRoute("gemini", "multimodal query (image attached)")

    # Tool-requiring → Gemini
    if needs_tools:
        tool_decision = select_tool_route(
            has_openai_key=has_openai_key,
            has_anthropic_key=has_anthropic_key,
            copilot_available=copilot_available,
            ollama_alive=ollama_alive,
        )
        return ModelRoute(tool_decision.provider, tool_decision.reason)

    decision = select_auto_route(
        has_openai_key=has_openai_key,
        has_anthropic_key=has_anthropic_key,
        copilot_available=copilot_available,
        ollama_alive=ollama_alive,
        is_code=bool(_CODE_PATTERN.search(message or "")),
        is_creative=bool(_CREATIVE_PATTERN.search(message or "")),
        is_analysis=bool(_ANALYSIS_PATTERN.search(message or "")),
        routing_profile=routing_profile,
        text=message or "",
        has_tools=needs_tools,
        recalled_context=recalled_context,
    )
    return ModelRoute(decision.provider, decision.reason)


# ---------------------------------------------------------------------------
# Alternative model backends (OpenAI, Anthropic)
# ---------------------------------------------------------------------------
# Alternative model backends (OpenAI, Anthropic)
# NOTE: chat_openai / chat_openai_vision / chat_anthropic are also defined in
# llm/providers.py.  Callers that still import from model_router are preserved
# here as backward-compat shims until those sites are migrated. # compat
# ---------------------------------------------------------------------------


async def chat_openai(  # compat — also in llm.providers
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message via OpenAI's API. Returns response text or None."""
    import os

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key and not COPILOT_PROXY_ENABLED:
        return None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")

    try:
        import aiohttp

        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            content = " ".join(p for p in msg["parts"] if isinstance(p, str))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        # Use Copilot proxy if available, otherwise direct OpenAI
        if COPILOT_PROXY_ENABLED:
            base_url = COPILOT_PROXY_URL.rstrip("/")
            proxy_token = os.getenv("COPILOT_PROXY_TOKEN", api_key or "")
            headers = {"Content-Type": "application/json"}
            if proxy_token:
                headers["Authorization"] = f"Bearer {proxy_token}"
        else:
            base_url = "https://api.openai.com/v1"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

        session = await _router_sessions.get()
        async with session.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                log.warning("OpenAI returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            content = data["choices"][0]["message"]["content"]
            if COPILOT_PROXY_ENABLED:
                from spending import tracker as spending_tracker
                await spending_tracker.record_copilot(model=model)
            return content
    except Exception as e:  # broad: intentional
        log.warning("OpenAI call failed: %s", e)
        return None


async def chat_openai_vision(  # compat — also in llm.providers
    message: str,
    image_bytes: bytes,
    mime_type: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message with an inline image via OpenAI's vision API.

    Uses the Copilot proxy when available (GPT-4o-vision), otherwise falls
    back to the direct OpenAI API.  Returns the response text or ``None`` on
    failure.
    """
    import base64
    import os

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key and not COPILOT_PROXY_ENABLED:
        return None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")

    image_b64 = base64.b64encode(image_bytes).decode()
    user_content = [
        {"type": "text", "text": message},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
        },
    ]

    messages = [{"role": "user", "content": user_content}]

    if COPILOT_PROXY_ENABLED:
        base_url = COPILOT_PROXY_URL.rstrip("/")
        proxy_token = os.getenv("COPILOT_PROXY_TOKEN", api_key or "")
        headers = {"Content-Type": "application/json"}
        if proxy_token:
            headers["Authorization"] = f"Bearer {proxy_token}"
    else:
        base_url = "https://api.openai.com/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    try:
        session = await _router_sessions.get()
        async with session.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                log.warning("OpenAI vision returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:  # broad: intentional
        log.warning("OpenAI vision call failed: %s", e)
        return None


async def chat_anthropic(  # compat — also in llm.providers
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message via Anthropic's API. Returns response text or None."""
    import os

    # When Copilot proxy is available, route Claude calls through it
    # (the proxy serves Claude models in OpenAI-compatible format)
    if COPILOT_PROXY_ENABLED:
        return await chat_openai(
            message, history, system_prompt,
            model=model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5"),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")

    try:
        messages = []
        for msg in history[-10:]:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            content = " ".join(p for p in msg["parts"] if isinstance(p, str))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        session = await _router_sessions.get()
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "system": system_prompt,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                log.warning("Anthropic returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            content_blocks = data.get("content", [])
            return " ".join(
                b["text"] for b in content_blocks if b.get("type") == "text"
            )
    except Exception as e:  # broad: intentional
        log.warning("Anthropic call failed: %s", e)
        return None
