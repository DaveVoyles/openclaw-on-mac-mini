"""Pure helper functions and constants for conversation memory.

No class dependencies — safe to import from any memory sub-module.
"""

import re
from pathlib import Path
from typing import Any

from config import cfg
from utils import atomic_write

__all__ = [
    # Constants
    "CONTEXT_TTL",
    "MAX_HISTORY_LENGTH",
    "MEMORY_DIR",
    "THREADS_DIR",
    "SUMMARIES_DIR",
    "MIN_MESSAGES_TO_SUMMARIZE",
    # Regex patterns
    "_THREAD_NAME_RE",
    "_TOPIC_WORD_RE",
    "_NUMBER_HINT_RE",
    "_PATH_HINT_RE",
    "_URL_HINT_RE",
    "_SALIENCE_TERMS",
    "_TOPIC_STOPWORDS",
    # Functions
    "_normalize_text",
    "_message_text",
    "_message_salience_score",
    "_extract_key_topics",
    "_build_salience_summary",
    "_relative_age",
    "_atomic_write",
]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTEXT_TTL = cfg.conversation_ttl_minutes * 60
MAX_HISTORY_LENGTH = cfg.llm_max_history_turns
MEMORY_DIR = Path("/memory")
THREADS_DIR = MEMORY_DIR / "threads"
SUMMARIES_DIR = MEMORY_DIR / "summaries"
MIN_MESSAGES_TO_SUMMARIZE = 4

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


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
