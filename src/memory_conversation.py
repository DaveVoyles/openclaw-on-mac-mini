"""Conversation and ConversationStore classes, plus the module-level store singleton.

``ConversationStore`` is a thin facade that delegates to two focused components:

* :class:`memory_conversation_cache.ConversationCache` — in-memory session management
  (create/retrieve/expire/summarise active conversations).
* :class:`memory_thread_persistence.ThreadPersistence` — named thread I/O
  (save/load/list/delete conversation snapshots on disk).
"""

import logging
import time

from memory_conversation_cache import ConversationCache
from memory_helpers import (
    CONTEXT_TTL,
    MAX_HISTORY_LENGTH,
    THREADS_DIR,  # noqa: F401 - retained for test monkeypatching compatibility
    _build_salience_summary,
)
from memory_thread_persistence import ThreadPersistence

log = logging.getLogger(__name__)

__all__ = [
    "Conversation",
    "ConversationStore",
    "store",
]


class Conversation:
    """A single conversation thread with history and metadata."""

    __slots__ = ("history", "last_active", "user_name", "summarized")

    def __init__(self, user_name: str = "User"):
        self.history: list[dict] = []
        self.last_active: float = time.monotonic()
        self.user_name: str = user_name
        self.summarized: bool = False

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_active) > CONTEXT_TTL

    def add_user_message(self, text: str):
        self.history.append({"role": "user", "parts": [text]})
        self.last_active = time.monotonic()
        self._trim()

    def update_from_llm(self, updated_history: list[dict]):
        """Replace history with the updated history from the LLM module."""
        self.history = updated_history
        self.last_active = time.monotonic()
        self._trim()

    def _trim(self):
        """Keep bounded history while preserving salient context for long threads."""
        if len(self.history) > MAX_HISTORY_LENGTH:
            preserve_head = self.history[:2]
            recent_keep = max(1, MAX_HISTORY_LENGTH - len(preserve_head) - 1)
            recent = self.history[-recent_keep:]
            overflow = self.history[len(preserve_head) : -recent_keep] if recent_keep < len(self.history) else []

            packed_summary, _meta = _build_salience_summary(overflow)
            if packed_summary:
                self.history = preserve_head + [{"role": "model", "parts": [packed_summary]}] + recent
            else:
                self.history = self.history[-MAX_HISTORY_LENGTH:]

    def clear(self):
        self.history.clear()
        self.last_active = time.monotonic()
        self.summarized = False

    @property
    def message_count(self) -> int:
        return len(self.history)

    @property
    def age_minutes(self) -> float:
        return (time.monotonic() - self.last_active) / 60


class ConversationStore:
    """Facade over :class:`ConversationCache` and :class:`ThreadPersistence`.

    All existing call sites continue to work unchanged — this class exposes
    the same public interface as before while delegating each concern to a
    dedicated component.
    """

    def __init__(self):
        self._cache = ConversationCache()
        self._threads = ThreadPersistence()

    @property
    def _conversations(self) -> dict:
        """Expose the cache's internal dict for backward-compatible direct access in tests."""
        return self._cache._conversations

    # ------------------------------------------------------------------
    # In-memory session management (delegates to ConversationCache)
    # ------------------------------------------------------------------

    def get(self, user_id: int, channel_id: int, user_name: str = "User") -> "Conversation":
        """Get or create a conversation for a user+channel pair."""
        return self._cache.get(user_id, channel_id, user_name)

    def clear_user(self, user_id: int, channel_id: int):
        """Clear a specific user's conversation in a channel."""
        self._cache.clear_user(user_id, channel_id)

    def clear_all(self):
        """Clear all conversations."""
        self._cache.clear_all()

    def cleanup_expired(self):
        """Remove expired conversations and trigger async summarisation."""
        self._cache.cleanup_expired()

    @property
    def active_count(self) -> int:
        """Number of active (non-expired) conversations."""
        return self._cache.active_count

    def stats(self) -> str:
        """Return a human-readable summary of in-memory state."""
        return self._cache.stats()

    # ------------------------------------------------------------------
    # Named thread persistence (delegates to ThreadPersistence)
    # ------------------------------------------------------------------

    def save_thread(self, user_id: int, channel_id: int, name: str) -> str:
        """Snapshot the active conversation to a named file on disk."""
        conv = self._cache.get(user_id, channel_id)
        # Pass the raw conv; persistence layer checks for empty history.
        conv_for_save = conv if conv.history else None
        return self._threads.save_thread(conv_for_save, user_id, name)

    def load_thread(self, user_id: int, channel_id: int, name: str) -> str:
        """Load a named thread from disk and make it the active conversation."""
        conv, status = self._threads.load_thread(user_id, name)
        if conv is not None:
            self._cache.set(user_id, channel_id, conv)
        return status

    def auto_save_thread(self, user_id: int, channel_id: int, user_name: str = "User") -> None:
        """Silently overwrite the auto-save slot after every /ask exchange."""
        conv = self._cache.get(user_id, channel_id, user_name)
        self._threads.auto_save_thread(conv, user_id, channel_id, user_name)

    def list_threads(self, user_id: int) -> str:
        """Return a formatted list of saved threads with size and health indicators."""
        return self._threads.list_threads(user_id)

    def delete_thread(self, user_id: int, name: str) -> str:
        """Delete a named thread from disk."""
        return self._threads.delete_thread(user_id, name)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

store: ConversationStore = ConversationStore()
