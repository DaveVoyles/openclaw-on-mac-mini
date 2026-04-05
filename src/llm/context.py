"""
Context management — history trimming, auto-RAG injection, content conversion.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory import Conversation

from google import genai

from config import cfg
from llm_client import GOOGLE_API_KEY, MODEL_NAME, _client

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
                        "Context auto-summarized: %d turns → %d turns",
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


async def _auto_recall_context(user_message: str) -> str:
    """Fetch recalled context from the vector store for Auto-RAG injection."""
    if not cfg.auto_recall_enabled:
        return ""

    parts = []
    from runtime_state import get_current_channel_id, get_current_thread_id

    channel_id = get_current_channel_id()
    thread_id = get_current_thread_id()

    try:
        import vector_store
        context = await vector_store.recall_for_context(
            user_message,
            channel_id=channel_id,
            thread_id=thread_id,
        )
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
            "Auto-RAG: injected %d context items for: %.60s…",
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
