"""
OpenClaw Conversation Memory — Phase 3
Per-user, per-channel conversation context with automatic expiry.
Stored in-memory with optional file persistence.
Named threads can be saved to disk and resumed later (survive restarts).
"""

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("openclaw.memory")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How long to keep a conversation context alive (seconds)
CONTEXT_TTL = 30 * 60  # 30 minutes of inactivity
# Maximum messages per conversation (to limit token usage)
MAX_HISTORY_LENGTH = 20
# Directory for persistence
MEMORY_DIR = Path("/memory")
# Sub-directory for saved (named) threads
THREADS_DIR = MEMORY_DIR / "threads"

# Valid thread name: letters, digits, hyphens, underscores, up to 32 chars
_THREAD_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


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

    # ------------------------------------------------------------------
    # Named thread persistence (save / resume / list / forget)
    # ------------------------------------------------------------------

    @staticmethod
    def _thread_path(user_id: int, name: str) -> Path:
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:32]
        path = (THREADS_DIR / f"{user_id}_{safe}.json").resolve()
        if not path.is_relative_to(THREADS_DIR.resolve()):
            raise ValueError(f"Invalid thread path: {path}")
        return path

    def save_thread(self, user_id: int, channel_id: int, name: str) -> str:
        """
        Snapshot the active conversation to a named file on disk.
        Returns a human-readable status message.
        """
        if not _THREAD_NAME_RE.match(name):
            return "❌ Thread name must be 1–32 characters: letters, digits, `-` or `_`."

        conv = self._conversations.get((user_id, channel_id))
        if not conv or not conv.history:
            return "❌ No active conversation to save. Start chatting first!"

        path = self._thread_path(user_id, name)
        payload = {
            "name": name,
            "user_name": conv.user_name,
            "saved_at": time.time(),
            "history": conv.history,
        }
        try:
            path.write_text(json.dumps(payload, indent=2))
            log.info("Saved thread '%s' for user %d (%d msgs)", name, user_id, len(conv.history))
            return f"✅ Saved thread **{name}** ({len(conv.history)} messages)."
        except Exception as e:
            log.error("Failed to save thread: %s", e)
            return f"❌ Could not save thread: {e}"

    def load_thread(self, user_id: int, channel_id: int, name: str) -> str:
        """
        Load a named thread from disk and make it the active conversation.
        Returns a human-readable status message.
        """
        if not _THREAD_NAME_RE.match(name):
            return "❌ Thread name must be 1–32 characters: letters, digits, `-` or `_`."

        path = self._thread_path(user_id, name)
        if not path.exists():
            return f"❌ No saved thread named **{name}**. Use `/threads` to see your saved threads."

        try:
            payload = json.loads(path.read_text())
            history = payload.get("history", [])
            user_name = payload.get("user_name", "User")
            saved_at = payload.get("saved_at", 0)

            conv = Conversation(user_name=user_name)
            conv.history = history[-MAX_HISTORY_LENGTH:]
            conv.last_active = time.monotonic()
            self._conversations[(user_id, channel_id)] = conv

            saved_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))
            log.info("Loaded thread '%s' for user %d (%d msgs)", name, user_id, len(history))
            return (
                f"✅ Resumed thread **{name}** — {len(conv.history)} messages "
                f"(saved {saved_str}). Continue with `/ask`."
            )
        except Exception as e:
            log.error("Failed to load thread: %s", e)
            return f"❌ Could not load thread: {e}"

    @staticmethod
    def _auto_thread_path(user_id: int, channel_id: int) -> Path:
        """Path for the auto-saved thread for a given user+channel."""
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        short = str(channel_id)[-8:]  # keep name ≤32 chars and regex-safe
        return THREADS_DIR / f"{user_id}_auto-{short}.json"

    def auto_save_thread(self, user_id: int, channel_id: int, user_name: str = "User") -> None:
        """Silently overwrite the auto-save slot after every /ask exchange."""
        conv = self._conversations.get((user_id, channel_id))
        if not conv or not conv.history:
            return
        path = self._auto_thread_path(user_id, channel_id)
        short = str(channel_id)[-8:]
        payload = {
            "name": f"auto-{short}",
            "user_name": user_name,
            "saved_at": time.time(),
            "auto": True,
            "history": conv.history,
        }
        try:
            path.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            log.warning("Auto-save failed for user %d: %s", user_id, e)

    def list_threads(self, user_id: int) -> str:
        """Return a formatted list of saved threads with size and health indicators."""
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(THREADS_DIR.glob(f"{user_id}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return "📂 No saved threads. Use `/save <name>` after a conversation."

        lines = ["**Saved Threads**\n"]
        total_kb = 0.0
        for f in files:
            try:
                payload = json.loads(f.read_text())
                name = payload.get("name", f.stem)
                msgs = len(payload.get("history", []))
                saved_at = payload.get("saved_at", 0)
                saved_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))
                size_kb = f.stat().st_size / 1024
                total_kb += size_kb
                est_tokens = int(f.stat().st_size / 4)  # rough: ~4 bytes per token
                is_auto = payload.get("auto", False)

                if size_kb > 50 or msgs > 80:
                    icon = "🔴"  # very large — consider deleting
                elif size_kb > 15 or msgs > 30:
                    icon = "⚠️"  # growing — worth pruning soon
                else:
                    icon = "💬"

                tag = " *(auto)*" if is_auto else ""
                lines.append(
                    f"{icon} **{name}**{tag} — {msgs} msgs · {size_kb:.1f} KB"
                    f" (~{est_tokens:,} tokens) · {saved_str}"
                )
            except Exception:
                lines.append(f"• `{f.stem}` (unreadable)")

        lines.append(f"\n📊 **{len(files)} threads · {total_kb:.1f} KB total on disk**")
        lines.append("🗑️ `/forget <name>` to delete · `/resume <name>` to continue")
        return "\n".join(lines)

    def delete_thread(self, user_id: int, name: str) -> str:
        """Delete a named thread from disk."""
        if not _THREAD_NAME_RE.match(name):
            return "❌ Invalid thread name."

        path = self._thread_path(user_id, name)
        if not path.exists():
            return f"❌ No saved thread named **{name}**."

        try:
            path.unlink()
            log.info("Deleted thread '%s' for user %d", name, user_id)
            return f"🗑️ Deleted thread **{name}**."
        except Exception as e:
            return f"❌ Could not delete thread: {e}"


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

store = ConversationStore()
