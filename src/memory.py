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
from typing import Any

from model_aliases import model_input_suggestion, normalize_model_input
from utils import atomic_write

log = logging.getLogger("openclaw.memory")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# How long to keep a conversation context alive (seconds)
from config import cfg

CONTEXT_TTL = cfg.conversation_ttl_minutes * 60
# Maximum messages per conversation (to limit token usage)
MAX_HISTORY_LENGTH = cfg.llm_max_history_turns
# Directory for persistence
MEMORY_DIR = Path("/memory")
# Sub-directory for saved (named) threads
THREADS_DIR = MEMORY_DIR / "threads"
# Directory for auto-generated session summaries
SUMMARIES_DIR = MEMORY_DIR / "summaries"
# Minimum messages before we bother summarizing (avoid trivial sessions)
MIN_MESSAGES_TO_SUMMARIZE = 4

# Valid thread name: letters, digits, hyphens, underscores, up to 32 chars
_THREAD_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_TOPIC_WORD_RE = re.compile(r"\b[a-z][a-z0-9_-]{3,}\b")
_NUMBER_HINT_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_PATH_HINT_RE = re.compile(r"(?:/[\w.\-]+)+")
_URL_HINT_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_SALIENCE_TERMS = (
    "decide",
    "decided",
    "decision",
    "must",
    "should",
    "plan",
    "next step",
    "todo",
    "action item",
    "blocked",
    "fix",
    "fixed",
    "error",
    "root cause",
    "deadline",
    "ship",
    "release",
)
_TOPIC_STOPWORDS = {
    "this",
    "that",
    "with",
    "from",
    "your",
    "have",
    "will",
    "about",
    "would",
    "there",
    "their",
    "what",
    "when",
    "where",
    "which",
    "while",
    "could",
    "should",
    "into",
    "thread",
    "context",
    "user",
    "model",
    "assistant",
}


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _message_text(msg: dict[str, Any]) -> str:
    parts = msg.get("parts", [])
    joined = " ".join(p for p in parts if isinstance(p, str))
    return _normalize_text(joined)


def _message_salience_score(msg: dict[str, Any], index: int) -> int:
    text = _message_text(msg)
    if not text:
        return 0
    lowered = text.lower()
    score = 0
    score += sum(4 for term in _SALIENCE_TERMS if term in lowered)
    if text.endswith("?"):
        score += 2
    if _NUMBER_HINT_RE.search(text):
        score += 2
    if _PATH_HINT_RE.search(text) or _URL_HINT_RE.search(text):
        score += 3
    if len(text) > 160:
        score += 1
    if msg.get("role") == "model":
        score += 1
    # Favor newer items on ties while keeping deterministic ordering.
    score += min(index, 6)
    return score


def _extract_key_topics(messages: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for msg in messages:
        text = _message_text(msg).lower()
        for token in _TOPIC_WORD_RE.findall(text):
            if token in _TOPIC_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[: max(1, limit)]]


def _build_salience_summary(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not messages:
        return "", {}

    scored: list[tuple[int, int, dict[str, Any], str]] = []
    for idx, msg in enumerate(messages):
        text = _message_text(msg)
        if not text:
            continue
        score = _message_salience_score(msg, idx)
        if score <= 0:
            continue
        scored.append((score, idx, msg, text))

    scored.sort(key=lambda item: (-item[0], -item[1]))
    top = scored[:8]
    # Preserve chronological order in the final packed summary.
    top.sort(key=lambda item: item[1])

    salient_lines: list[str] = []
    for _, _, msg, text in top:
        role = "User" if msg.get("role") == "user" else "Assistant"
        compact = text[:220]
        if len(text) > 220:
            compact = compact.rstrip() + "…"
        salient_lines.append(f"- {role}: {compact}")

    original_topics = _extract_key_topics(messages)
    retained_topics = _extract_key_topics(
        [{"role": "model", "parts": [line]} for line in salient_lines]
    )
    retained_topic_set = set(retained_topics)
    retained_count = sum(1 for topic in original_topics if topic in retained_topic_set)
    topic_total = len(original_topics)
    topic_ratio = (retained_count / topic_total) if topic_total else 1.0
    drift_risk = "low" if topic_ratio >= 0.7 else ("medium" if topic_ratio >= 0.45 else "high")
    missing_topics = [topic for topic in original_topics if topic not in retained_topic_set][:5]

    summary_lines = ["[Compressed Thread Context — salient facts & decisions]"]
    summary_lines.extend(salient_lines if salient_lines else ["- (no salient points extracted)"])
    summary_lines.append(
        f"[Anti-drift] topics_retained={retained_count}/{topic_total} "
        f"({topic_ratio:.2f}) facts_retained={len(salient_lines)} drift_risk={drift_risk}"
    )
    if missing_topics:
        summary_lines.append(f"[Anti-drift] missing_topics={', '.join(missing_topics)}")

    meta = {
        "compression_applied": bool(salient_lines),
        "retained_key_facts_count": len(salient_lines),
        "key_topics_total": topic_total,
        "key_topics_retained": retained_count,
        "topic_retention_ratio": round(topic_ratio, 3),
        "drift_risk": drift_risk,
        "missing_topics": missing_topics,
    }
    return "\n".join(summary_lines), meta


def _relative_age(seconds: float) -> str:
    """Convert seconds elapsed into a short human-readable string like '3h ago'."""
    if seconds < 60:
        return "just now"
    minutes = int(seconds / 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes / 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours / 24)
    return f"{days}d ago"


def _atomic_write(path: Path, data: str) -> None:
    """Write data to *path* atomically. Delegates to shared utility."""
    atomic_write(path, data)


# ---------------------------------------------------------------------------
# In-memory conversation store
# ---------------------------------------------------------------------------


class Conversation:
    """A single conversation thread with history and metadata."""

    __slots__ = ("history", "last_active", "user_name", "summarized")

    def __init__(self, user_name: str = "User"):
        self.history: list[dict] = []      # [{"role": "user"|"model", "parts": [str]}]
        self.last_active: float = time.monotonic()
        self.user_name: str = user_name
        self.summarized: bool = False      # True after auto-summarization to prevent re-summarizing

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
            overflow = self.history[len(preserve_head):-recent_keep] if recent_keep < len(self.history) else []

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
    """Manage per-user conversation contexts."""

    def __init__(self):
        # Key: (user_id, channel_id) → Conversation
        self._conversations: dict[tuple[int, int], Conversation] = {}

    def get(self, user_id: int, channel_id: int, user_name: str = "User") -> "Conversation":
        """Get or create a conversation for a user+channel pair.

        If the previous conversation expired, attaches a recall note so the
        model knows what was discussed last time.
        """
        key = (user_id, channel_id)
        conv = self._conversations.get(key)
        expired = conv is not None and conv.is_expired
        if conv is None or expired:
            conv = Conversation(user_name=user_name)
            self._conversations[key] = conv
            # Inject a brief recall note from the last session summary
            recall = _load_last_summary(user_id)
            if recall:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Recall from last session] {recall}"],
                })
            # Inject handover context if available (Phase 14C)
            handover = load_last_handover(user_id)
            if handover:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Session handover — pending items & next steps]\n{handover}"],
                })
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
        """Remove expired conversations to free memory. Auto-summarizes before discarding."""
        expired = [k for k, v in self._conversations.items() if v.is_expired]
        for k in expired:
            conv = self._conversations.pop(k)
            # Fire-and-forget background summarization for sessions worth keeping
            if conv.message_count >= MIN_MESSAGES_TO_SUMMARIZE:
                user_id, channel_id = k
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        _summarize_and_store(user_id, conv.user_name, conv.history)
                    )
                    # Also generate a proactive handover (Phase 14C)
                    loop.create_task(
                        create_session_handover(user_id, conv.user_name, conv.history)
                    )
                except RuntimeError:
                    log.debug("No running loop for summarization")
        if expired:
            log.info("Cleaned up %d expired conversations (summarized those with %d+ msgs)",
                     len(expired), MIN_MESSAGES_TO_SUMMARIZE)

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
            _atomic_write(path, json.dumps(payload, indent=2))
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
            _atomic_write(path, json.dumps(payload, indent=2))
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
        now = time.time()
        for f in files:
            try:
                payload = json.loads(f.read_text())
                name = payload.get("name", f.stem)
                msgs = len(payload.get("history", []))
                saved_at = payload.get("saved_at", 0)
                size_kb = f.stat().st_size / 1024
                total_kb += size_kb
                est_tokens = int(f.stat().st_size / 4)  # rough: ~4 bytes per token
                is_auto = payload.get("auto", False)

                # Relative age
                age_text = _relative_age(now - saved_at) if saved_at else "unknown"

                if size_kb > 50 or msgs > 80:
                    icon = "🔴"  # very large — consider deleting
                elif size_kb > 15 or msgs > 30:
                    icon = "⚠️"  # growing — worth pruning soon
                else:
                    icon = "💬"

                tag = " *(auto)*" if is_auto else ""
                lines.append(
                    f"{icon} **{name}**{tag} — {msgs} msgs · {size_kb:.1f} KB"
                    f" (~{est_tokens:,} tokens) · saved {age_text}"
                )
            except Exception as exc:
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
        except Exception as e:
            return f"❌ Could not delete thread: {e}"


# ---------------------------------------------------------------------------
# Global instance
# ---------------------------------------------------------------------------

store = ConversationStore()


# ---------------------------------------------------------------------------
# Per-user model preference (persisted to disk)
# ---------------------------------------------------------------------------

_PREFS_DIR = MEMORY_DIR / "preferences"
_VALID_MODEL_PREFS = {"auto", "local", "gemini", "openai", "anthropic"}


def _prefs_path(user_id: int) -> Path:
    _PREFS_DIR.mkdir(parents=True, exist_ok=True)
    return _PREFS_DIR / f"{user_id}.json"


def _load_prefs(user_id: int) -> dict:
    path = _prefs_path(user_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        log.debug("Failed to load preferences for user %d: %s", user_id, exc)
        return {}


def _save_prefs(user_id: int, prefs: dict) -> None:
    _atomic_write(_prefs_path(user_id), json.dumps(prefs, indent=2))


def get_model_preference(user_id: int) -> str:
    """Return the user's sticky model preference (default from config: 'auto')."""
    from config import cfg
    return _load_prefs(user_id).get("model_preference", cfg.default_model_preference)


def set_model_preference(user_id: int, pref: str) -> str:
    """Set the user's sticky model preference. Returns status message."""
    raw_pref = (pref or "").strip()
    pref = normalize_model_input(raw_pref)
    if pref not in _VALID_MODEL_PREFS:
        suggestion = model_input_suggestion(raw_pref)
        suggestion_suffix = f" {suggestion}" if suggestion else ""
        return (
            f"❌ Invalid preference `{raw_pref.lower()}`."
            f"{suggestion_suffix} Choose: `auto`, `local`, `gemini`, `openai`, or `anthropic`."
        )
    prefs = _load_prefs(user_id)
    prefs["model_preference"] = pref
    _save_prefs(user_id, prefs)
    labels = {"auto": "🔄 Auto (Copilot → Gemini)", "local": "🏠 Local (Gemma/Ollama)", "gemini": "☁️ Gemini (cloud)", "openai": "🟢 OpenAI (GPT-4o)", "anthropic": "🟣 Anthropic (Claude)"}
    return f"✅ Model preference set to **{labels.get(pref, pref)}**."


# ---------------------------------------------------------------------------
# Session summary helpers  (Phase A)
# ---------------------------------------------------------------------------

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
    """
    Generate a concise summary of the conversation and persist it so it
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

        # Also push to QMD for long-term semantic recall
        try:
            from qmd import remember_fact
            await remember_fact(
                content=f"[Session summary for {user_name}] {summary}",
                tags=f"session,{user_name.split('#')[0].lower().replace(' ', '_')}",
            )
        except Exception as e:
            log.debug("QMD session save failed (non-critical): %s", e)

        # Embed summary into ChromaDB conversations collection
        try:
            import vector_store
            await vector_store.add_conversation_summary(
                user_id, f"session_{user_name}", summary
            )
        except Exception as e:
            log.debug("Vector embed for summary failed (non-critical): %s", e)
    except Exception as e:
        log.warning("Session summarization failed: %s", e)


# ---------------------------------------------------------------------------
# Session handover  (Phase 14C — proactive context persistence)
# ---------------------------------------------------------------------------

HANDOVER_DIR = MEMORY_DIR / "handovers"


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
        # Build a condensed transcript for the LLM
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

        # Persist to disk
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

        # Embed in ChromaDB for semantic retrieval
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


# ---------------------------------------------------------------------------
# Unified Memory Facade (formerly memory_manager.py — merged Phase 16)
# Single interface for all memory operations across QMD, ChromaDB, rules,
# and user profile. Import directly from this module:
#   from memory import store_memory, recall_memories, forget_memory, memory_stats
# ---------------------------------------------------------------------------

import hashlib
import time as _time

_mem_log = logging.getLogger("openclaw.memory_manager")


async def store_memory(
    content: str,
    *,
    source: str = "user-explicit",
    confidence: float = 1.0,
    tags: list[str] | None = None,
    dedup: bool = True,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> dict:
    """Store a fact/memory across all backends (vector store + QMD).

    Args:
        content: The fact or memory text to store.
        source: Origin — "user-explicit", "auto-extracted", "correction", "profile".
        confidence: 0.0-1.0 reliability score.
        tags: Optional tags for categorization.
        dedup: If True, skip storing identical content.
        channel_id / thread_id: Scope the memory to a specific channel or thread.

    Returns dict with {"stored": bool, "id": str, "duplicate": bool}.
    """
    import vector_store
    from qmd import remember_fact

    result: dict = {"stored": False, "id": "", "duplicate": False}
    fact_id = _mem_content_id(content) if dedup else _mem_unique_id(content)

    try:
        if dedup:
            stored = await vector_store.add_memory_deduped(
                fact_id=fact_id,
                content=content,
                tags=tags,
                metadata={"source": source, "confidence": confidence},
                channel_id=channel_id,
                thread_id=thread_id,
            )
            if not stored:
                result["duplicate"] = True
                return result
        else:
            await vector_store.add_memory(
                fact_id=fact_id,
                content=content,
                tags=tags,
                source=source,
                confidence=confidence,
                channel_id=channel_id,
                thread_id=thread_id,
            )
        result["stored"] = True
        result["id"] = f"mem_{fact_id}"
    except Exception as exc:
        _mem_log.warning("Vector store failed: %s", exc)

    try:
        await remember_fact(content, tags=",".join(tags or []), source=source)
    except Exception as exc:
        _mem_log.debug("QMD store failed: %s", exc)

    return result


async def recall_memories(
    query: str,
    *,
    top_k: int = 5,
    include_rules: bool = True,
    include_profile: bool = True,
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
) -> list[dict]:
    """Recall relevant memories from all sources (vector store + rules).

    Returns list of dicts: [{"text", "source", "similarity", "type", "id"}, ...]
    sorted by relevance descending.
    """
    results: list[dict] = []

    try:
        import vector_store
        vs_results = await vector_store.search_all(
            query, top_k=top_k, channel_id=channel_id, thread_id=thread_id,
        )
        for r in vs_results:
            results.append({
                "text": r["text"],
                "source": r.get("metadata", {}).get("source", "unknown"),
                "similarity": r.get("similarity", 0),
                "type": r.get("collection", "memory"),
                "id": r.get("id", ""),
            })
    except Exception as exc:
        _mem_log.debug("Vector recall failed: %s", exc)

    if include_rules:
        try:
            from rules_engine import get_relevant_rules
            for rule in await get_relevant_rules(query, top_k=3):
                if isinstance(rule, str):
                    results.append({
                        "text": rule, "source": "rule",
                        "similarity": 0.8, "type": "rule", "id": "",
                    })
        except Exception as exc:
            _mem_log.debug("Rules recall failed: %s", exc)

    results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
    return results[:top_k]


async def forget_memory(memory_id: str) -> bool:
    """Remove a memory by ID from all vector store collections."""
    removed = False
    try:
        import vector_store
        for collection in [
            vector_store.MEMORIES_COLLECTION,
            vector_store.CONVERSATIONS_COLLECTION,
            vector_store.RESEARCH_COLLECTION,
        ]:
            try:
                await vector_store.delete_document(collection, memory_id)
                removed = True
            except Exception as exc:
                _mem_log.debug(
                    "Vector delete from %s failed for %s: %s", collection, memory_id, exc
                )
    except Exception as exc:
        _mem_log.debug("Vector forget failed: %s", exc)
    return removed


async def memory_stats() -> dict:
    """Aggregated stats across all memory backends (vector store, QMD, rules, profile)."""
    result: dict = {
        "vector_store": {},
        "qmd": {"count": 0},
        "rules": {"count": 0},
        "profile": {"exists": False},
    }
    try:
        import vector_store
        result["vector_store"] = await vector_store.get_stats()
    except Exception as exc:
        _mem_log.debug("Vector store stats failed: %s", exc)

    try:
        from qmd import list_memories
        memories = await list_memories()
        if memories and memories != "Memory is empty.":
            result["qmd"]["count"] = memories.count("\n") + 1
    except Exception as exc:
        _mem_log.debug("QMD stats failed: %s", exc)

    try:
        from rules_engine import get_all_rules
        result["rules"]["count"] = len(await get_all_rules())
    except Exception as exc:
        _mem_log.debug("Rules stats failed: %s", exc)

    try:
        from user_profile import load_profile
        result["profile"]["exists"] = bool(load_profile())
    except Exception as exc:
        _mem_log.debug("Profile stats failed: %s", exc)

    return result


def _mem_content_id(content: str) -> str:
    """Deterministic ID from content (stable for dedup)."""
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _mem_unique_id(content: str) -> str:
    """Unique ID from content + timestamp (for non-dedup stores)."""
    return hashlib.md5(f"{content}_{_time.time()}".encode()).hexdigest()[:12]


# Backward-compatible aliases (legacy callers and tests that imported from memory_manager)
# NOTE: 'store' is already in use as the ConversationStore singleton — do NOT shadow it.
#       Callers should use store_memory / recall_memories / forget_memory / memory_stats directly.
recall = recall_memories
forget = forget_memory
stats = memory_stats
_content_id = _mem_content_id
_unique_id = _mem_unique_id
