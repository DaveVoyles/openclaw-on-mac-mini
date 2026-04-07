"""
Context management — history trimming, auto-RAG injection, content conversion.
"""

import asyncio
import logging
import re
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory import Conversation

from google import genai

from config import cfg
from llm_client import MODEL_NAME, _client

log = logging.getLogger("openclaw.llm")

_CROSS_CHANNEL_OPT_IN_RE = re.compile(
    r"(?i)(--cross-channel\b|#cross-channel\b|\[cross-channel\])"
)
_RESET_CONTEXT_RE = re.compile(r"(?i)(--reset-context\b|#reset-context\b|\[reset-context\])")
_USE_PRIOR_REPORT_RE = re.compile(r"(?i)(--use-prior-report\b|#use-prior-report\b|\[use-prior-report\])")
_NO_ANCHOR_RE = re.compile(r"(?i)(--no-anchor\b|#no-anchor\b|\[no-anchor\])")
_ANCHOR_OVERRIDE_RE = re.compile(r"(?i)--anchor(?:=|\s+)([A-Za-z0-9_.:\-]+)")
_TOPIC_WORD_RE = re.compile(r"\b[a-z][a-z0-9_-]{3,}\b")
_NUMBER_HINT_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_URL_HINT_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_HINT_RE = re.compile(r"(?:/[\w.\-]+)+")

_SALIENCE_TERMS = (
    "decision",
    "decided",
    "plan",
    "next step",
    "todo",
    "action item",
    "must",
    "should",
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


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _message_text(msg: dict[str, Any]) -> str:
    parts = msg.get("parts", [])
    return _normalize_text(" ".join(p for p in parts if isinstance(p, str)))


def _extract_topics_from_messages(messages: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    counts: Counter[str] = Counter()
    for msg in messages:
        text = _message_text(msg).lower()
        for token in _TOPIC_WORD_RE.findall(text):
            if token in _TOPIC_STOPWORDS:
                continue
            counts[token] += 1
    return [token for token, _ in counts.most_common(max(1, limit))]


def _salience_score(msg: dict[str, Any], index: int) -> int:
    text = _message_text(msg)
    if not text:
        return 0
    lowered = text.lower()
    score = sum(4 for term in _SALIENCE_TERMS if term in lowered)
    if text.endswith("?"):
        score += 2
    if _NUMBER_HINT_RE.search(text):
        score += 2
    if _URL_HINT_RE.search(text) or _PATH_HINT_RE.search(text):
        score += 3
    if len(text) > 160:
        score += 1
    if msg.get("role") == "model":
        score += 1
    score += min(index, 6)
    return score


def _build_salience_lines(messages: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    scored: list[tuple[int, int, str, str]] = []
    for idx, msg in enumerate(messages):
        text = _message_text(msg)
        if not text:
            continue
        score = _salience_score(msg, idx)
        if score <= 0:
            continue
        role = "User" if msg.get("role") == "user" else "Assistant"
        scored.append((score, idx, role, text))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    selected = scored[: max(1, limit)]
    selected.sort(key=lambda item: item[1])
    lines: list[str] = []
    for _, _, role, text in selected:
        compact = text[:220]
        if len(text) > 220:
            compact = compact.rstrip() + "…"
        lines.append(f"- {role}: {compact}")
    return lines


def _build_context_quality_meta(
    *,
    original_history: list[dict[str, Any]],
    compressed_history: list[dict[str, Any]],
    compressed_slice: list[dict[str, Any]],
    salient_lines: list[str],
) -> dict[str, Any]:
    before_turns = len(original_history)
    after_turns = len(compressed_history)
    before_chars = _estimate_chars(original_history)
    after_chars = _estimate_chars(compressed_history)
    ratio = (after_chars / before_chars) if before_chars else 1.0

    original_topics = _extract_topics_from_messages(compressed_slice)
    retained_topics = _extract_topics_from_messages(
        [{"role": "model", "parts": [line]} for line in salient_lines]
    )
    retained_topic_set = set(retained_topics)
    retained_count = sum(1 for topic in original_topics if topic in retained_topic_set)
    topic_total = len(original_topics)
    topic_ratio = (retained_count / topic_total) if topic_total else 1.0
    drift_risk = "low" if topic_ratio >= 0.7 else ("medium" if topic_ratio >= 0.45 else "high")
    missing_topics = [topic for topic in original_topics if topic not in retained_topic_set][:5]

    return {
        "compression_applied": bool(salient_lines),
        "compression_ratio": round(ratio, 3),
        "turns_before": before_turns,
        "turns_after": after_turns,
        "chars_before": before_chars,
        "chars_after": after_chars,
        "retained_key_facts_count": len(salient_lines),
        "key_topics_total": topic_total,
        "key_topics_retained": retained_count,
        "topic_retention_ratio": round(topic_ratio, 3),
        "drift_risk": drift_risk,
        "missing_topics": missing_topics,
    }


def _compress_history_with_salience(
    history: list[dict[str, Any]],
    *,
    max_turns: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(history) <= max_turns or max_turns < 6:
        return list(history), {
            "compression_applied": False,
            "compression_ratio": 1.0,
            "turns_before": len(history),
            "turns_after": len(history),
            "chars_before": _estimate_chars(history),
            "chars_after": _estimate_chars(history),
            "retained_key_facts_count": 0,
            "key_topics_total": 0,
            "key_topics_retained": 0,
            "topic_retention_ratio": 1.0,
            "drift_risk": "low",
            "missing_topics": [],
        }

    preserve_head = history[:2]
    recent_keep = max(1, max_turns - len(preserve_head) - 1)
    recent_slice = history[-recent_keep:]
    compressed_slice = history[len(preserve_head):-recent_keep] if recent_keep < len(history) else []
    salient_lines = _build_salience_lines(compressed_slice)
    if not salient_lines:
        trimmed = preserve_head + recent_slice
        return trimmed[:max_turns], _build_context_quality_meta(
            original_history=history,
            compressed_history=trimmed[:max_turns],
            compressed_slice=compressed_slice,
            salient_lines=[],
        )

    summary_lines = ["[Compressed Thread Context — salient facts & decisions]"]
    summary_lines.extend(salient_lines)
    summary_turn = {"role": "model", "parts": ["\n".join(summary_lines)]}
    compressed = preserve_head + [summary_turn] + recent_slice
    quality = _build_context_quality_meta(
        original_history=history,
        compressed_history=compressed,
        compressed_slice=compressed_slice,
        salient_lines=salient_lines,
    )
    compressed_summary = (
        f"[Anti-drift] topics_retained={quality['key_topics_retained']}/{quality['key_topics_total']} "
        f"({quality['topic_retention_ratio']:.2f}) facts_retained={quality['retained_key_facts_count']} "
        f"drift_risk={quality['drift_risk']}"
    )
    if quality["missing_topics"]:
        compressed_summary += "\n[Anti-drift] missing_topics=" + ", ".join(quality["missing_topics"])
    summary_turn["parts"][0] += "\n" + compressed_summary
    return compressed, quality


async def _trim_history(
    history: list[dict],
    model_hint: str = "default",
    *,
    conversation: "Conversation | None" = None,
    context_quality: dict[str, Any] | None = None,
) -> list[dict]:
    """Keep first 2 turns (persona context) + last N to avoid context overflow."""
    max_turns, max_chars = _get_context_limits(model_hint)
    original_history = list(history)
    quality: dict[str, Any]

    compressed_history, quality = _compress_history_with_salience(history, max_turns=max_turns)
    history = list(compressed_history)
    if quality.get("compression_applied"):
        if conversation is not None:
            conversation.summarized = True
        log.info(
            "Context compressed by salience: %d turns → %d turns (ratio %.2f, facts=%d, drift=%s)",
            quality.get("turns_before"),
            quality.get("turns_after"),
            quality.get("compression_ratio"),
            quality.get("retained_key_facts_count"),
            quality.get("drift_risk"),
        )
        if quality.get("drift_risk") == "high":
            log.warning(
                "High context drift risk detected during compression; missing topics=%s",
                ",".join(quality.get("missing_topics") or []),
            )

    while len(history) > 4 and _estimate_chars(history) > max_chars:
        history = history[:2] + history[3:]
        log.debug("Trimmed history to %d turns (%d chars)", len(history), _estimate_chars(history))

    # Refresh quality metadata after char-based trims so surfaced metrics stay accurate.
    quality["turns_after"] = len(history)
    quality["chars_after"] = _estimate_chars(history)
    before_chars = quality.get("chars_before") or _estimate_chars(original_history)
    quality["compression_ratio"] = round((quality["chars_after"] / before_chars), 3) if before_chars else 1.0

    if context_quality is not None:
        context_quality.clear()
        context_quality.update(quality)

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


from runtime_state import (
    get_current_user_id,
    get_effective_channel_profile,
    record_scoped_recall_alert,
    reset_anchor_state,
    resolve_anchor_state,
    resolve_context_lock,
)


def _build_context_explainability(
    *,
    cross_channel: bool,
    followup: bool,
    use_prior_report: bool,
    anchor_override: str | None,
    disable_anchor: bool,
) -> dict[str, Any]:
    """Build concise explainability details for context scope/lock/profile behavior."""
    from runtime_state import get_current_channel_id, get_current_thread_id

    channel_id = get_current_channel_id()
    thread_id = get_current_thread_id()
    user_id = get_current_user_id()

    lock, lock_ignored_reason = resolve_context_lock(
        user_id=user_id,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    lock_mode = str((lock or {}).get("mode") or "none")
    anchor, anchor_state_reason = resolve_anchor_state(channel_id=channel_id, thread_id=thread_id)

    effective_use_prior_report = bool(use_prior_report)
    resolved_anchor_override = anchor_override
    ignored: list[str] = []
    if lock_ignored_reason:
        ignored.append(f"lock:{lock_ignored_reason}")

    if lock_mode == "prior_report" and lock.get("anchor_id"):
        effective_use_prior_report = True
        if not resolved_anchor_override:
            resolved_anchor_override = str(lock.get("anchor_id"))

    anchor_id: str | None = None
    if not disable_anchor and resolved_anchor_override:
        anchor_id = str(resolved_anchor_override)
    elif disable_anchor:
        ignored.append("anchor:disabled")
    elif (
        not disable_anchor
        and (effective_use_prior_report or followup)
        and anchor
        and channel_id is not None
    ):
        raw_anchor = anchor.get("anchor_id")
        anchor_id = str(raw_anchor) if raw_anchor else None
    elif (effective_use_prior_report or followup) and anchor_state_reason:
        ignored.append(f"anchor:{anchor_state_reason}")
    elif (effective_use_prior_report or followup) and not anchor:
        ignored.append("anchor:missing")

    anchor_age_seconds: int | None = None
    if anchor_id and anchor and str(anchor.get("anchor_id") or "") == anchor_id:
        ts = anchor.get("timestamp")
        if ts:
            anchor_age_seconds = max(0, int(time.time() - float(ts)))

    scope_mode = "cross-channel" if cross_channel else ("thread" if thread_id is not None else "channel")
    effective_profile = get_effective_channel_profile(channel_id=channel_id, thread_id=thread_id)

    return {
        "scope_mode": scope_mode,
        "lock_mode": lock_mode,
        "anchor_id": anchor_id,
        "anchor_age_seconds": anchor_age_seconds,
        "effective_profile": effective_profile,
        "ignored": ignored,
    }


def _format_context_explainability_note(payload: dict[str, Any]) -> str:
    """Return a compact, footer-safe explainability note string."""
    scope = str(payload.get("scope_mode") or "channel")
    lock_mode = str(payload.get("lock_mode") or "none")
    anchor_id = payload.get("anchor_id")
    anchor_age = payload.get("anchor_age_seconds")
    if anchor_id:
        anchor_label = str(anchor_id)
        if len(anchor_label) > 18:
            anchor_label = anchor_label[:18] + "…"
        if isinstance(anchor_age, int):
            anchor_label = f"{anchor_label}@{anchor_age}s"
    else:
        anchor_label = "none"

    profile = payload.get("effective_profile") or {}
    profile_sig = "/".join(
        str(profile.get(key) or "-")
        for key in ("tone", "table_style", "emoji_level", "report_depth", "source_strictness")
    )
    ignored = payload.get("ignored") or []
    ignored_note = f" · ignored:{','.join(str(x) for x in ignored)}" if ignored else ""
    return f"{scope} · lock:{lock_mode} · anchor:{anchor_label} · profile:{profile_sig}{ignored_note}"


async def _auto_recall_context(
    user_message: str,
    *,
    cross_channel: bool = False,
    routing_notes: list[str] | None = None,
    followup: bool = False,
    reset_context: bool = False,
    use_prior_report: bool = False,
    anchor_override: str | None = None,
    disable_anchor: bool = False,
) -> str:
    """Fetch recalled context from the vector store for Auto-RAG injection."""
    if not cfg.auto_recall_enabled:
        return ""

    parts = []
    from runtime_state import get_current_channel_id, get_current_thread_id

    channel_id = get_current_channel_id()
    thread_id = get_current_thread_id()

    # Anchor follow-up mode: if followup and anchor matches, use anchor's report/job id
    if reset_context:
        reset_anchor_state(channel_id=channel_id, thread_id=thread_id)

    anchor, _ = resolve_anchor_state(channel_id=channel_id, thread_id=thread_id, prune_stale=False)
    anchor_id = None
    lock, lock_ignored_reason = resolve_context_lock(
        user_id=get_current_user_id(),
        channel_id=channel_id,
        thread_id=thread_id,
    )
    if lock and lock.get("mode") == "prior_report" and lock.get("anchor_id"):
        use_prior_report = True
        if not anchor_override:
            anchor_override = str(lock.get("anchor_id"))
    elif lock_ignored_reason and channel_id is not None:
        record_scoped_recall_alert(
            category="scope_guard_block",
            message=f"Context lock ignored ({lock_ignored_reason}).",
            channel_id=channel_id,
            thread_id=thread_id,
            metadata={"reason": lock_ignored_reason},
        )
    if not disable_anchor and anchor_override:
        anchor_id = anchor_override
    elif not disable_anchor and (use_prior_report or followup) and anchor and channel_id is not None:
        anchor_id = anchor.get("anchor_id")

    try:
        import vector_store
        context = await vector_store.recall_for_context(
            user_message,
            channel_id=channel_id,
            thread_id=thread_id,
            cross_channel=cross_channel,
            anchor_id=anchor_id,
        )
        guard_notes = []
        try:
            guard_notes = vector_store.consume_recall_guard_notes()
        except Exception:
            guard_notes = []
        if routing_notes is not None and guard_notes:
            routing_notes.extend(f"Context guard: {note}" for note in guard_notes)
        if cross_channel and channel_id is not None:
            record_scoped_recall_alert(
                category="cross_channel_opt_in",
                message="Cross-channel recall opt-in used.",
                channel_id=channel_id,
                thread_id=thread_id,
                metadata={"anchor_id": anchor_id},
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


def _extract_cross_channel_opt_in(user_message: str) -> tuple[str, bool]:
    """Extract explicit cross-channel retrieval opt-in markers from a user prompt."""
    if not user_message:
        return "", False
    matched = _CROSS_CHANNEL_OPT_IN_RE.search(user_message) is not None
    if not matched:
        return user_message, False
    cleaned = _CROSS_CHANNEL_OPT_IN_RE.sub(" ", user_message)
    cleaned = " ".join(cleaned.split())
    return (cleaned or user_message), True


def _extract_context_controls(user_message: str) -> tuple[str, dict[str, str | bool | None]]:
    """Extract explicit context-control markers from a user prompt."""
    if not user_message:
        return "", {
            "reset_context": False,
            "use_prior_report": False,
            "disable_anchor": False,
            "anchor_override": None,
        }

    cleaned = user_message
    controls: dict[str, str | bool | None] = {
        "reset_context": _RESET_CONTEXT_RE.search(user_message) is not None,
        "use_prior_report": _USE_PRIOR_REPORT_RE.search(user_message) is not None,
        "disable_anchor": _NO_ANCHOR_RE.search(user_message) is not None,
        "anchor_override": None,
    }
    match = _ANCHOR_OVERRIDE_RE.search(user_message)
    if match:
        controls["anchor_override"] = match.group(1).strip()

    for rx in (_RESET_CONTEXT_RE, _USE_PRIOR_REPORT_RE, _NO_ANCHOR_RE, _ANCHOR_OVERRIDE_RE):
        cleaned = rx.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())
    return (cleaned or user_message), controls


def _merge_structured_context_controls(
    *,
    cross_channel: bool,
    controls: dict[str, str | bool | None],
    structured_controls: dict[str, Any] | None,
) -> tuple[bool, dict[str, str | bool | None]]:
    """Merge structured slash-command context controls with legacy text controls.

    Structured options take precedence when explicitly provided, while legacy
    inline markers remain supported for backward compatibility.
    """
    merged = dict(controls or {})
    if not isinstance(structured_controls, dict):
        return cross_channel, merged

    scope_value = structured_controls.get("scope")
    if isinstance(scope_value, str):
        scope = scope_value.strip().lower().replace("_", "-")
        if scope:
            if scope == "cross-channel":
                cross_channel = True
                merged["use_prior_report"] = False
            elif scope in {"prior-report", "priorreport"}:
                cross_channel = False
                merged["use_prior_report"] = True
            elif scope in {"current", "channel", "thread", "auto", "default"}:
                cross_channel = False
                merged["use_prior_report"] = False

    if "reset_context" in structured_controls:
        merged["reset_context"] = bool(structured_controls.get("reset_context"))

    anchor_value = structured_controls.get("anchor")
    if isinstance(anchor_value, str):
        anchor = anchor_value.strip()
        if anchor:
            anchor_token = anchor.lower()
            if anchor_token in {"none", "off", "disable", "disabled", "no-anchor", "false"}:
                merged["disable_anchor"] = True
                merged["anchor_override"] = None
            else:
                merged["anchor_override"] = anchor
                merged["disable_anchor"] = False

    if isinstance(structured_controls.get("anchor_override"), str):
        override = str(structured_controls.get("anchor_override") or "").strip()
        if override:
            merged["anchor_override"] = override
            merged["disable_anchor"] = False

    if "disable_anchor" in structured_controls:
        disable = bool(structured_controls.get("disable_anchor"))
        merged["disable_anchor"] = disable
        if disable:
            merged["anchor_override"] = None

    return cross_channel, merged


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
