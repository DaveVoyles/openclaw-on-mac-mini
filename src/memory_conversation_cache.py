"""In-memory management of active per-user conversation sessions."""

import asyncio
import logging

from memory_helpers import CONTEXT_TTL, MIN_MESSAGES_TO_SUMMARIZE  # noqa: F401 – re-exported
from memory_session import _load_last_summary, _summarize_and_store, create_session_handover, load_last_handover

log = logging.getLogger(__name__)

__all__ = ["ConversationCache"]


class ConversationCache:
    """In-memory store of active (user_id, channel_id) → Conversation sessions.

    Responsibilities:
    - Create or retrieve conversations keyed by (user_id, channel_id).
    - Inject recall/handover notes when a session is brand-new or expired.
    - Evict expired sessions and trigger async summarisation.
    - Report runtime stats.
    """

    def __init__(self):
        # Imported here to avoid a circular import at module load time.
        from memory_conversation import Conversation  # noqa: PLC0415
        self._Conversation = Conversation
        self._conversations: dict[tuple[int, int], object] = {}

    def get(self, user_id: int, channel_id: int, user_name: str = "User"):
        """Return the active Conversation for *user_id* + *channel_id*.

        Creates a fresh one (with optional recall/handover context injected)
        when none exists or the previous session has expired.
        """
        key = (user_id, channel_id)
        conv = self._conversations.get(key)
        expired = conv is not None and conv.is_expired
        if conv is None or expired:
            conv = self._Conversation(user_name=user_name)
            self._conversations[key] = conv
            recall = _load_last_summary(user_id)
            if recall:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Recall from last session] {recall}"],
                })
            handover = load_last_handover(user_id)
            if handover:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Session handover — pending items & next steps]\n{handover}"],
                })
        return conv

    def set(self, user_id: int, channel_id: int, conv) -> None:
        """Store *conv* as the active conversation for *user_id* + *channel_id*."""
        self._conversations[(user_id, channel_id)] = conv

    def clear_user(self, user_id: int, channel_id: int) -> None:
        """Reset the conversation for a specific user+channel pair."""
        key = (user_id, channel_id)
        if key in self._conversations:
            self._conversations[key].clear()

    def clear_all(self) -> None:
        """Discard all in-memory conversations."""
        self._conversations.clear()

    def cleanup_expired(self) -> None:
        """Remove expired conversations and fire async summarisation tasks."""
        expired = [k for k, v in self._conversations.items() if v.is_expired]
        for k in expired:
            conv = self._conversations.pop(k)
            if conv.message_count >= MIN_MESSAGES_TO_SUMMARIZE:
                user_id, _channel_id = k
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        _summarize_and_store(user_id, conv.user_name, conv.history)
                    )
                    loop.create_task(
                        create_session_handover(user_id, conv.user_name, conv.history)
                    )
                except RuntimeError:
                    log.debug("No running loop for summarization")
        if expired:
            log.info(
                "Cleaned up %d expired conversations (summarized those with %d+ msgs)",
                len(expired),
                MIN_MESSAGES_TO_SUMMARIZE,
            )

    @property
    def active_count(self) -> int:
        """Number of non-expired conversations currently held."""
        return sum(1 for v in self._conversations.values() if not v.is_expired)

    def stats(self) -> str:
        """Human-readable summary of current in-memory state."""
        total = len(self._conversations)
        active = self.active_count
        total_msgs = sum(v.message_count for v in self._conversations.values())
        return f"{active} active / {total} total conversations, {total_msgs} messages"
