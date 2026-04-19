"""Disk-based persistence for named conversation threads."""

import json
import logging
import re
import time
from pathlib import Path

from memory_helpers import (
    _THREAD_NAME_RE,
    CONTEXT_TTL,
    MAX_HISTORY_LENGTH,
    THREADS_DIR,
    _atomic_write,
    _relative_age,
)

log = logging.getLogger(__name__)

__all__ = ["ThreadPersistence"]


class ThreadPersistence:
    """Read/write named conversation threads to disk.

    Responsibilities:
    - Validate and resolve safe file paths for named threads.
    - Serialise/deserialise conversation history to/from JSON files.
    - Provide a formatted directory listing of all saved threads.
    - Silently maintain an auto-save slot after every exchange.
    """

    # ------------------------------------------------------------------
    # Internal path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _thread_path(user_id: int, name: str) -> Path:
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:32]
        path = (THREADS_DIR / f"{user_id}_{safe}.json").resolve()
        if not path.is_relative_to(THREADS_DIR.resolve()):
            raise ValueError(f"Invalid thread path: {path}")
        return path

    @staticmethod
    def _auto_thread_path(user_id: int, channel_id: int) -> Path:
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        short = str(channel_id)[-8:]
        return THREADS_DIR / f"{user_id}_auto-{short}.json"

    # ------------------------------------------------------------------
    # Public API (called by ConversationStore)
    # ------------------------------------------------------------------

    def save_thread(self, conv, user_id: int, name: str) -> str:
        """Snapshot *conv* to a named file on disk.

        Args:
            conv: The active ``Conversation`` object (may be ``None``).
            user_id: Discord user ID.
            name: Thread name (validated against ``_THREAD_NAME_RE``).

        Returns:
            A human-readable status message.
        """
        if not _THREAD_NAME_RE.match(name):
            return "❌ Thread name must be 1–32 characters: letters, digits, `-` or `_`."
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
            _atomic_write(path, json.dumps(payload, indent=2))
            log.info("Saved thread '%s' for user %d (%d msgs)", name, user_id, len(conv.history))
            return f"✅ Saved thread **{name}** ({len(conv.history)} messages)."
        except (OSError, TypeError, ValueError) as e:
            log.error("Failed to save thread: %s", e)
            return f"❌ Could not save thread: {e}"

    def load_thread(self, user_id: int, name: str):
        """Load a named thread from disk.

        Args:
            user_id: Discord user ID.
            name: Thread name.

        Returns:
            ``(Conversation, status_message)`` on success, or
            ``(None, error_message)`` on failure.
        """
        # Imported lazily to avoid a circular import at module load time.
        from memory_conversation import Conversation  # noqa: PLC0415

        if not _THREAD_NAME_RE.match(name):
            return None, "❌ Thread name must be 1–32 characters: letters, digits, `-` or `_`."

        path = self._thread_path(user_id, name)
        if not path.exists():
            return None, f"❌ No saved thread named **{name}**. Use `/threads` to see your saved threads."

        try:
            payload = json.loads(path.read_text())
            history = payload.get("history", [])
            user_name = payload.get("user_name", "User")
            saved_at = payload.get("saved_at", 0)

            # W5-3: Align disk TTL with in-memory TTL (CONTEXT_TTL from cfg)
            if saved_at and (time.time() - saved_at) > CONTEXT_TTL:
                log.info(
                    "Thread '%s' for user %d has expired (TTL=%ds) — treating as stale",
                    name, user_id, CONTEXT_TTL,
                )
                return None, (
                    f"⚠️ Thread **{name}** has expired "
                    f"({int(CONTEXT_TTL // 60)} min TTL). Start a fresh conversation with `/ask`."
                )

            conv = Conversation(user_name=user_name)
            conv.history = history[-MAX_HISTORY_LENGTH:]
            conv.last_active = time.monotonic()

            saved_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved_at))
            log.info("Loaded thread '%s' for user %d (%d msgs)", name, user_id, len(history))
            status = (
                f"✅ Resumed thread **{name}** — {len(conv.history)} messages "
                f"(saved {saved_str}). Continue with `/ask`."
            )
            return conv, status
        except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
            log.error("Failed to load thread: %s", e)
            return None, f"❌ Could not load thread: {e}"

    def auto_save_thread(self, conv, user_id: int, channel_id: int, user_name: str = "User") -> None:
        """Silently overwrite the auto-save slot after every ``/ask`` exchange."""
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
            _atomic_write(path, json.dumps(payload, indent=2))
        except (OSError, TypeError, ValueError) as e:
            log.warning("Auto-save failed for user %d: %s", user_id, e)

    def list_threads(self, user_id: int) -> str:
        """Return a formatted list of saved threads with size and health indicators."""
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(
            THREADS_DIR.glob(f"{user_id}_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return "📂 No saved threads. Use `/save <name>` after a conversation."

        lines = ["**Saved Threads**\n"]
        total_kb = 0.0
        now = time.time()
        for f in files:
            try:
                payload = json.loads(f.read_text())
                name = payload.get("name", f.stem)
                msgs = len(payload.get("history", []))
                saved_at = payload.get("saved_at", 0)
                size_kb = f.stat().st_size / 1024
                total_kb += size_kb
                est_tokens = int(f.stat().st_size / 4)
                is_auto = payload.get("auto", False)

                age_text = _relative_age(now - saved_at) if saved_at else "unknown"

                if size_kb > 50 or msgs > 80:
                    icon = "🔴"
                elif size_kb > 15 or msgs > 30:
                    icon = "⚠️"
                else:
                    icon = "💬"

                tag = " *(auto)*" if is_auto else ""
                lines.append(
                    f"{icon} **{name}**{tag} — {msgs} msgs · {size_kb:.1f} KB"
                    f" (~{est_tokens:,} tokens) · saved {age_text}"
                )
            except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
                lines.append(f"• `{f.stem}` (unreadable)")
                log.debug("Thread file unreadable %s: %s", f.name, exc)

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
        except OSError as e:
            return f"❌ Could not delete thread: {e}"
