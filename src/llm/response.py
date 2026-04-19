"""
Response handling — multimodal analysis (image + document).
"""

import asyncio
import logging
from typing import Any

from google import genai

from llm_client import (
    GOOGLE_API_KEY,
    MAX_TOKENS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
    TEMPERATURE,
    _client,
    _get_model,
    _record_usage,
)
from llm_patterns import _needs_tools
from llm_ratelimit import rate_limiter as _rate_limiter
from llm_tools import (
    _extract_final_text,
    _extract_history,
    _merge_direct_final_history,
    _run_tool_loop,
)
from model_routing_policy import select_multimodal_route

from .context import _to_content, _trim_history

log = logging.getLogger(__name__)


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
    """Analyze an image, routing to Copilot vision when available."""
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}"

    if _needs_tools(prompt):
        text, _ = await analyze_image_with_tools(
            image_bytes, mime_type, prompt,
            history=history, on_tool_call=on_tool_call,
        )
        return text

    import os

    from llm.providers import COPILOT_PROXY_ENABLED, chat_openai_vision

    route = select_multimodal_route(
        copilot_available=COPILOT_PROXY_ENABLED,
        has_openai_key=bool(os.getenv("OPENAI_API_KEY", "")),
    )
    log.debug("Image analysis route: %s (%s)", route.provider, route.reason)

    if route.provider in ("copilot", "openai"):
        result = await chat_openai_vision(prompt, image_bytes, mime_type, max_tokens=MAX_TOKENS)
        if result:
            return result

    # Gemini fallback (also the only path when tools are not needed and Copilot/OpenAI unavailable)
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."

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
    except Exception as e:  # broad: intentional
        log.error("Image analysis failed: %s", e)
        return f"❌ Image analysis failed: {e}"


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
        return "❌ GOOGLE_API_KEY not configured.", history or []
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}", history or []

    history = await _trim_history(history or [], model_hint="gemini")

    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "⚠️ Rate limit reached. Please wait a moment.",
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
    except Exception as e:  # broad: intentional
        log.error("Image analysis with tools failed: %s", e)
        return f"❌ Image analysis failed: {e}", history

    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=MAX_TOOL_ROUNDS,
        on_tool_call=on_tool_call,
        parallel=True,
        label="Vision+Tools",
    )

    text = _extract_final_text(response, rounds, chat_session)
    updated_history = _extract_history(chat_session)
    if getattr(response, "direct_final_text", ""):
        updated_history = _merge_direct_final_history(updated_history, text)

    return text, updated_history


async def analyze_document(text: str, prompt: str) -> str:
    """Analyze document text using Gemini (no tool loop)."""
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."

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
    except Exception as e:  # broad: intentional
        log.error("Document analysis failed: %s", e)
        return f"❌ Document analysis failed: {e}"
