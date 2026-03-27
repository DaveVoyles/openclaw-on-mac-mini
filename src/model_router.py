"""
OpenClaw Model Router — Phase 8: Multi-Model Smart Routing
Classifies query types and routes to the optimal model backend.

Routing strategy:
  - Code-heavy queries → OpenAI GPT-4 or Anthropic Claude (if keys available)
  - Multimodal (images) → Gemini (best multimodal support)
  - Research / deep analysis → Gemini thinking model
  - Simple chat → Ollama/Gemma (free, fast, private)
  - Tool-requiring → Gemini (native function calling)
  - Everything else → Gemini (reliable default)
"""

import logging
import os
import re
from typing import Optional

log = logging.getLogger("openclaw.model_router")

# Copilot proxy configuration
# When COPILOT_PROXY_URL is set, OpenAI and Anthropic calls route through it
COPILOT_PROXY_URL = os.getenv("COPILOT_PROXY_URL", "")
COPILOT_PROXY_ENABLED = COPILOT_PROXY_URL != ""

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


class ModelRoute:
    """Represents a routing decision."""

    __slots__ = ("model_type", "reason")

    def __init__(self, model_type: str, reason: str):
        self.model_type = model_type  # "gemini", "ollama", "openai", "anthropic"
        self.reason = reason

    def __repr__(self):
        return f"ModelRoute({self.model_type!r}, {self.reason!r})"


def classify_query(
    message: str,
    *,
    has_openai_key: bool = False,
    has_anthropic_key: bool = False,
    has_image: bool = False,
    needs_tools: bool = False,
    model_preference: str = "auto",
) -> ModelRoute:
    """Classify a query and return the optimal model route.

    Priority order:
    1. Explicit user preference (not "auto") → honor it
    2. Image attached → Gemini (best multimodal)
    3. Needs tools → Gemini (native function calling)
    4. Code query + Claude available → Anthropic (best code quality)
    5. Creative writing + GPT available → OpenAI (strong creative)
    6. Analysis/research → Gemini (good reasoning + tools)
    7. Simple chat → Ollama (free, fast)
    8. Default → Gemini
    """
    # Honor explicit preference
    if model_preference == "local":
        return ModelRoute("ollama", "user preference: local")
    if model_preference == "gemini":
        return ModelRoute("gemini", "user preference: gemini")
    if model_preference == "openai" and has_openai_key:
        return ModelRoute("openai", "user preference: openai")
    if model_preference == "anthropic" and has_anthropic_key:
        return ModelRoute("anthropic", "user preference: anthropic")

    # Image → Gemini (best multimodal)
    if has_image:
        return ModelRoute("gemini", "multimodal query (image attached)")

    # Tool-requiring → Gemini
    if needs_tools:
        return ModelRoute("gemini", "requires tool/function calling")

    # Code queries → prefer Claude if available
    if _CODE_PATTERN.search(message):
        if has_anthropic_key:
            return ModelRoute("anthropic", "code query (Claude excels at code)")
        if has_openai_key:
            return ModelRoute("openai", "code query (GPT-4 strong at code)")
        return ModelRoute("gemini", "code query (no alternative keys)")

    # Creative writing → prefer GPT if available
    if _CREATIVE_PATTERN.search(message):
        if has_openai_key:
            return ModelRoute("openai", "creative writing (GPT-4 strong at creative)")
        return ModelRoute("gemini", "creative writing (no OpenAI key)")

    # Deep analysis → Gemini (can use tools if needed)
    if _ANALYSIS_PATTERN.search(message):
        return ModelRoute("gemini", "analysis/research query")

    # Simple chat → Ollama (free, fast, private)
    return ModelRoute("ollama", "simple conversational query")


# ---------------------------------------------------------------------------
# Alternative model backends (OpenAI, Anthropic)
# ---------------------------------------------------------------------------


async def chat_openai(
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

        async with aiohttp.ClientSession() as session:
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
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("OpenAI call failed: %s", e)
        return None


async def chat_anthropic(
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
        import aiohttp

        messages = []
        for msg in history[-10:]:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            content = " ".join(p for p in msg["parts"] if isinstance(p, str))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        async with aiohttp.ClientSession() as session:
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
    except Exception as e:
        log.warning("Anthropic call failed: %s", e)
        return None
