"""Session summaries and handover for conversation memory."""

import json
import logging
import time
from pathlib import Path

from memory_helpers import (
    MEMORY_DIR,
    MIN_MESSAGES_TO_SUMMARIZE,
    SUMMARIES_DIR,
    _atomic_write,
)

log = logging.getLogger("openclaw.memory")

__all__ = [
    "HANDOVER_DIR",
    "_summary_path",
    "_load_last_summary",
    "_summarize_and_store",
    "create_session_handover",
    "load_last_handover",
]

HANDOVER_DIR = MEMORY_DIR / "handovers"


def _summary_path(user_id: int) -> Path:
    """Return the path to the rolling session summary file for a user."""
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    return SUMMARIES_DIR / f"{user_id}_last_session.json"


def _load_last_summary(user_id: int) -> str:
    """Load the most recent session summary for a user, or '' if none."""
    path = _summary_path(user_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
        return data.get("summary", "")
    except Exception as exc:
        log.debug("Failed to load summary for user %d: %s", user_id, exc)
        return ""


async def _summarize_and_store(user_id: int, user_name: str, history: list[dict]) -> None:
    """Generate a concise summary of the conversation and persist it so it
    can be recalled at the start of the user's next session.
    Also stores the summary as a QMD memory fact for long-term recall.
    """
    try:
        from llm import summarize_conversation
        summary = await summarize_conversation(history)
        if not summary:
            return

        path = _summary_path(user_id)
        payload = {
            "user_id": user_id,
            "user_name": user_name,
            "saved_at": time.time(),
            "summary": summary,
        }
        _atomic_write(path, json.dumps(payload, indent=2))
        log.info("Saved session summary for user %d (%d chars)", user_id, len(summary))

        try:
            from qmd import remember_fact
            await remember_fact(
                content=f"[Session summary for {user_name}] {summary}",
                tags=f"session,{user_name.split('#')[0].lower().replace(' ', '_')}",
            )
        except Exception as e:
            log.debug("QMD session save failed (non-critical): %s", e)

        try:
            import vector_store
            await vector_store.add_conversation_summary(
                user_id, f"session_{user_name}", summary
            )
        except Exception as e:
            log.debug("Vector embed for summary failed (non-critical): %s", e)
    except Exception as e:
        log.warning("Session summarization failed: %s", e)


async def create_session_handover(
    user_id: int, user_name: str, history: list[dict]
) -> str | None:
    """Generate a proactive handover when a conversation goes idle.

    Unlike the summary (a brief recap), the handover captures:
      - Key decisions made during the session
      - Pending items / unanswered questions
      - Suggested next steps

    Stored on disk and embedded in ChromaDB so that when the user returns
    to a similar topic, the handover context is injected automatically.
    """
    if len(history) < MIN_MESSAGES_TO_SUMMARIZE:
        return None

    try:
        from llm import chat
        transcript_lines = []
        for msg in history[-20:]:
            role = "User" if msg.get("role") == "user" else "Bot"
            parts = msg.get("parts", [])
            text = " ".join(p for p in parts if isinstance(p, str))[:300]
            if text:
                transcript_lines.append(f"{role}: {text}")
        transcript = "\n".join(transcript_lines)

        prompt = (
            "Analyze this conversation and create a structured handover note. "
            "Include:\n"
            "1. **Decisions Made** — what was decided or agreed on\n"
            "2. **Pending Items** — unanswered questions or unresolved topics\n"
            "3. **Next Steps** — what the user should do next or what the bot should follow up on\n\n"
            "Be concise (3-5 bullet points total). If nothing significant, return 'No handover needed.'\n\n"
            f"Conversation:\n{transcript}"
        )

        response, _, _ = await chat(prompt, model_preference="gemini")
        if not response or "no handover needed" in response.lower():
            return None

        HANDOVER_DIR.mkdir(parents=True, exist_ok=True)
        handover_path = HANDOVER_DIR / f"{user_id}_last_handover.json"
        payload = {
            "user_id": user_id,
            "user_name": user_name,
            "created_at": time.time(),
            "handover": response,
        }
        _atomic_write(handover_path, json.dumps(payload, indent=2))
        log.info("Session handover saved for user %d (%d chars)", user_id, len(response))

        try:
            import vector_store
            await vector_store.add_document(
                vector_store.CONVERSATIONS_COLLECTION,
                doc_id=f"handover_{user_id}_{int(time.time())}",
                text=f"[Session handover for {user_name}] {response}",
                metadata={
                    "type": "handover",
                    "user_id": str(user_id),
                    "user_name": user_name,
                },
            )
        except Exception as e:
            log.debug("Vector embed for handover failed (non-critical): %s", e)

        return response
    except Exception as e:
        log.warning("Session handover generation failed: %s", e)
        return None


def load_last_handover(user_id: int) -> str:
    """Load the most recent session handover for a user, or '' if none."""
    path = HANDOVER_DIR / f"{user_id}_last_handover.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
        return data.get("handover", "")
    except Exception as exc:
        log.debug("Failed to load handover for user %d: %s", user_id, exc)
        return ""
