"""
OpenClaw Conversation Memory — Phase 3
Per-user, per-channel conversation context with automatic expiry.
Stored in-memory with optional file persistence.
"""

import logging
import time
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("openclaw.memory")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How long to keep a conversation context alive (seconds)
CONTEXT_TTL = 30 * 60  # 30 minutes of inactivity
# Maximum messages per conversation (to limit token usage)
MAX_HISTORY_LENGTH = 20
# Directory for persistence (optional)
MEMORY_DIR = Path("/memory")


# ---------------------------------------------------------------------------
# In-memory conversation store
# ---------------------------------------------------------------------------


class Conversation:
    """A single conversation thread with history and metadata."""

    __slots__ = ("history", "last_active", "user_name")

    def __init__(self, user_name: str = "User"):
        self.history: list[dict] = []      # [{"role": "user"|"model", "parts": [str]}]
        self.last_active: float = time.monotonic()
        self.user_name: str = user_name

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
        """Keep only the last MAX_HISTORY_LENGTH messages."""
        if len(self.history) > MAX_HISTORY_LENGTH:
            self.history = self.history[-MAX_HISTORY_LENGTH:]

    def clear(self):
        self.history.clear()
        self.last_active = time.monotonic()

    @property
    def message_count(self) -> int:
        return len(self.history)

    @property
    def age_minutes(self) -> float:
        return (time.monotonic() - self.last_active) / 60


class ConversationStore:
    """Manage per-user conversation contexts."""

    def __init__(self):
        # Key: (user_id, channel_id) → Conversation
        self._conversations: dict[tuple[int, int], Conversation] = {}

    def get(self, user_id: int, channel_id: int, user_name: str = "User") -> Conversation:
        """Get or create a conversation for a user+channel pair."""
        key = (user_id, channel_id)
        conv = self._conversations.get(key)
        if conv is None or conv.is_expired:
            conv = Conversation(user_name=user_name)
            self._conversations[key] = conv
        return conv

    def clear_user(self, user_id: int, channel_id: int):
        """Clear a specific user's conversation in a channel."""
        key = (user_id, channel_id)
        if key in self._conversations:
            self._conversations[key].clear()

    def clear_all(self):
        """Clear all conversations."""
        self._conversations.clear()

    def cleanup_expired(self):
        """Remove expired conversations to free memory."""
        expired = [k for k, v in self._conversations.items() if v.is_expired]
        for k in expired:
            del self._conversations[k]
        if expired:
            log.info("Cleaned up %d expired conversations", len(expired))

    @property
    def active_count(self) -> int:
        """Number of active (non-expired) conversations."""
        return sum(1 for v in self._conversations.values() if not v.is_expired)

    def stats(self) -> str:
        """Return a human-readable summary."""
        total = len(self._conversations)
        active = self.active_count
        total_msgs = sum(v.message_count for v in self._conversations.values())
        return f"{active} active / {total} total conversations, {total_msgs} messages"


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

store = ConversationStore()
