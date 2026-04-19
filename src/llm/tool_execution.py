"""
Local model (Ollama/Gemma) integration — session management, availability
checks, and local-model chat dispatch.
"""

import asyncio
import logging

import aiohttp

from config import cfg
from http_session import SessionManager as _SessionManager
from llm_client import (
    _TOOL_DECLARATIONS,
    LOCAL_LLM_ENABLED,
    MAX_TOKENS,
    OLLAMA_MODEL,
    OLLAMA_URL,
    TEMPERATURE,
    _load_system_prompt,
)
from llm_patterns import _gemma_response_seems_valid, _needs_tools
from llm_tools import _execute_function_call

log = logging.getLogger(__name__)


_ollama_sessions = _SessionManager(
    timeout=10,
    name="ollama",
    connector_limit=10,
    connector_limit_per_host=5,
)


async def _get_ollama_session() -> aiohttp.ClientSession:
    """Return the shared Ollama aiohttp session, (re)creating if closed."""
    return await _ollama_sessions.get()


async def _ollama_available() -> bool:
    """Return True if Ollama is reachable and the model is loaded."""
    try:
        session = await _get_ollama_session()
        async with session.get(f"{OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=3)) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return any(OLLAMA_MODEL.split(":")[0] in m for m in models)
    except (aiohttp.ClientError, OSError, AttributeError) as exc:
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
    except (aiohttp.ClientError, OSError, ValueError) as e:
        log.warning("Ollama error: %s", e)
        return None


async def _try_local_model(
    user_message: str,
    history: list[dict],
    *,
    force: bool = False,
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
                    user_message,
                    history,
                    system_prompt,
                    _TOOL_DECLARATIONS,
                    _execute_function_call,
                    ollama_url=OLLAMA_URL,
                    ollama_model=OLLAMA_MODEL,
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                if reply and tools_used:
                    log.info("Served by Ollama with tools (%d calls): %.60s…", len(tools_used), user_message)
                    return reply
            except Exception as e:  # broad: intentional
                log.info("Ollama tool calling failed, falling back: %s", e)

    if not force and _needs_tools(user_message):
        return None
    if not await _ollama_available():
        log.debug("Gemma/Ollama not reachable, using Gemini")
        return None

    system_prompt = _load_system_prompt()
    gemma_reply = await _chat_ollama(user_message, history, system_prompt)

    if gemma_reply and _gemma_response_seems_valid(gemma_reply):
        log.info("Served by Gemma (%s): %.60s…", OLLAMA_MODEL, user_message)
        return gemma_reply

    if gemma_reply:
        log.info("Gemma response failed validation (hallucination signals detected), falling back to Gemini")
    else:
        log.info("Gemma returned empty response, falling back to Gemini")
    return None


async def close_sessions() -> None:
    """Close all persistent aiohttp sessions. Call on bot shutdown."""
    await _ollama_sessions.close()
    log.info("Closed Ollama aiohttp session")
