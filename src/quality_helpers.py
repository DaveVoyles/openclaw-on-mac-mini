"""
quality_helpers.py — Standalone quality-scoring and ask-recovery helpers.

Extracted from bot.py so they can be imported independently (e.g., in tests
or other modules) without pulling in the full Discord bot runtime.

Provides:
  - Answer quality scoring  (_score_answer_quality, _safe_score_answer_quality)
  - Quality auto-repair     (_run_quality_auto_repair)
  - Ask failure helpers     (_build_ask_failure_message, _classify_ask_failure, …)
  - Coverage/embed helpers  (_build_coverage_summary_for_embed, _build_ask_recovery_block, …)
  - Metric emission stubs   (_record_quality_metric, _record_budget_policy_metric)
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys as _sys
from typing import Any

import discord

from ask_orchestrator import (
    apply_repair_budget,
    get_latency_load_snapshot,
    select_latency_budget_policy,
)
from bot_formatting import should_package_as_attachment as _should_package_as_attachment
from runtime_state import get_effective_channel_profile

log = logging.getLogger(__name__)


_ORIG: dict[str, Any] = {}  # populated at module bottom; see _b()
_SENTINEL = object()


def _b(name: str, local_val: Any) -> Any:
    """Resolve name supporting patches to either this module or the bot module.

    Priority: (1) local-module patch if detected, (2) bot-module attribute, (3) local_val.
    At call time, local_val is the current module global (already patched if tests did setattr).
    We compare against _ORIG to detect local patches.
    """
    orig = _ORIG.get(name)
    if orig is not None and local_val is not orig:
        return local_val  # this module was patched — use the patched value
    bot_mod = _sys.modules.get('bot')
    if bot_mod is not None:
        bot_val = getattr(bot_mod, name, _SENTINEL)
        if bot_val is not _SENTINEL:
            return bot_val
    return local_val

# ---------------------------------------------------------------------------
# Ask-failure helpers
# ---------------------------------------------------------------------------


def _explainability_note_from_meta(meta: dict[str, Any] | None) -> str:
    """Extract a normalized explainability note from LLM metadata."""
    if not isinstance(meta, dict):
        return ""
    note = meta.get("explainability_note")
    return note.strip() if isinstance(note, str) else ""


def _append_explainability_footer(base_footer: str, explainability_note: str | None) -> str:
    """Append explainability details to a footer string when available."""
    note = (explainability_note or "").strip()
    if not note:
        return base_footer
    return f"{base_footer} | 🧭 {note}"


def _build_ask_context_controls(
    *,
    scope: str | None = None,
    reset_context: bool | None = None,
    anchor: str | None = None,
) -> dict[str, Any]:
    """Build structured context controls for /ask slash options."""
    controls: dict[str, Any] = {}
    normalized_scope = (scope or "").strip().lower().replace("_", "-")
    if normalized_scope:
        controls["scope"] = normalized_scope
    if reset_context is not None:
        controls["reset_context"] = bool(reset_context)
    if isinstance(anchor, str) and anchor.strip():
        controls["anchor"] = anchor.strip()
    return controls


def _build_ask_timeout_message(
    *,
    elapsed_seconds: float,
    progress_lines: list[str],
    model_pref: str,
    trace_id: str = "no-trace",
) -> str:
    """Build operator-focused timeout guidance for ``/ask`` failures."""
    elapsed = max(0, int(round(elapsed_seconds)))
    if progress_lines:
        steps = "\n".join(f"• {line}" for line in progress_lines[-6:])
    else:
        steps = "• No progress checkpoints were recorded before timeout."

    return (
        f"⏰ **Timed out after {elapsed}s** while running `/ask` (`model:{model_pref}`).\n\n"
        f"**Trace ID:** `{trace_id}`\n\n"
        f"**Progress before timeout**\n{steps}\n\n"
        "**Try next**\n"
        "• Retry with a narrower prompt\n"
        "• Retry with `/ask model:gemini` for provider fallback/tool-heavy requests\n"
        "• If a tool/provider looks stuck, retry once after ~30s\n"
        "• For active outages, start an incident room with `/incident start`"
    )


def _classify_ask_failure(error_message: str, routing_notes: list[str] | None = None) -> str:
    """Classify a failed /ask execution into user-safe troubleshooting categories."""
    text = (error_message or "").lower()
    notes = " ".join(routing_notes or []).lower()
    merged = f"{text} {notes}".strip()
    if "timed out" in merged or "timeout" in merged:
        return "timeout"
    if any(token in merged for token in ("429", "rate limit", "resource exhausted", "quota")):
        return "rate_limit"
    if any(token in merged for token in ("tool", "function call", "gateway", "invalid tool", "skill")):
        return "tool"
    if any(
        token in merged
        for token in (
            "gemini",
            "openai",
            "anthropic",
            "claude",
            "ollama",
            "provider",
            "api key",
            "unauthorized",
            "forbidden",
            "service unavailable",
            "connection refused",
        )
    ):
        return "provider"
    return "general"


def _build_ask_failure_message(
    *,
    question: str,
    model_pref: str,
    trace_id: str,
    category: str,
) -> str:
    """Build user-safe /ask failure guidance with category-specific hints."""
    category_title = {
        "timeout": "Timeout",
        "rate_limit": "Rate limit",
        "tool": "Tool/provider call issue",
        "provider": "Model provider issue",
        "general": "Request failure",
    }.get(category, "Request failure")
    hint_lines = {
        "timeout": [
            "• Retry with a narrower prompt",
            "• Retry once after ~30s if services are under load",
        ],
        "rate_limit": [
            "• Wait briefly, then retry",
            "• Use a narrower request to reduce tool calls",
        ],
        "tool": [
            "• Retry once to recover transient tool failures",
            "• If it persists, use `/ask model:gemini` to re-route tool-heavy requests",
        ],
        "provider": [
            "• Retry with `/ask model:gemini` (or `auto`)",
            "• If provider issues continue, start an incident with `/incident start`",
        ],
        "general": [
            "• Retry once with a shorter, direct prompt",
            "• If this keeps failing, start an incident with `/incident start`",
        ],
    }.get(category, ["• Retry with a shorter prompt", "• Start `/incident` if failures continue"])
    safe_question = discord.utils.escape_markdown(question)
    hints = "\n".join(hint_lines)
    return (
        f"❌ **{category_title}.** The `/ask` request could not complete (`model:{model_pref}`).\n\n"
        f"**Trace ID:** `{trace_id}`\n\n"
        "**Try next**\n"
        f"{hints}\n\n"
        "**Your message was saved below for easy copy-pasting/retry:**\n"
        f"```\n{safe_question}\n```"
    )


# ---------------------------------------------------------------------------
# Quality-scoring constants
# ---------------------------------------------------------------------------

_QUALITY_RETRY_TIMEOUT_SECONDS = 45
_QUALITY_RETRY_MAX_ATTEMPTS = 2
# W11-1: provider-aware repair timeouts
_REPAIR_TIMEOUTS: dict[str, int] = {
    "copilot": 20,
    "gemini": 45,
    "ollama": 60,
}
_UNCERTAINTY_MARKERS = (
    "not sure",
    "unclear",
    "unknown",
    "might",
    "may ",
    "could ",
    "possibly",
    "likely",
    "partial coverage",
    "insufficient",
    "incomplete",
    "tbd",
)
_FRESHNESS_MARKERS = (
    "today",
    "yesterday",
    "latest",
    "updated",
    "as of",
    "currently",
    "this week",
    "this month",
)
_EVIDENCE_COMPLETENESS_RE = re.compile(
    r"evidence completeness:\s*\*\*(\d{1,3})%\*\*",
    re.IGNORECASE,
)

_REQUESTED_ITEMS_PREFIX_RE = re.compile(
    r"\b(?:top|first|at\s+least|minimum(?:\s+of)?|up\s+to|bring(?:\s+in)?|include|cover|list|give(?:\s+me)?|show(?:\s+me)?|get(?:\s+me)?|provide)\s+(\d{1,2})\s+(?:[a-z][a-z0-9'/-]*\s+){0,2}(stories?|headlines?|games?|items?|results?)\b",
    re.IGNORECASE,
)
_REQUESTED_ITEMS_BARE_RE = re.compile(
    r"^\s*(\d{1,2})\s+(?:[a-z][a-z0-9'/-]*\s+){0,2}(stories?|headlines?|games?|items?|results?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Metric emission (lazy import to avoid circular deps)
# ---------------------------------------------------------------------------


def _record_quality_metric(event: str, context: str = "ask") -> None:
    """Best-effort quality metric emission for /ask reliability signals."""
    try:
        from metrics_collector import get_collector

        get_collector().record_quality_event(event=event, context=context)
    except Exception:
        pass


def _record_budget_policy_metric(
    *,
    path: str,
    profile: str,
    load_tier: str,
    decision: str,
) -> None:
    """Best-effort latency-quality policy metric emission."""
    try:
        from metrics_collector import get_collector

        get_collector().record_budget_policy_decision(
            path=path,
            profile=profile,
            load_tier=load_tier,
            decision=decision,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Markdown / text analysis helpers
# ---------------------------------------------------------------------------


def _count_markdown_table_items(markdown_text: str) -> int:
    """Estimate body rows in markdown tables."""
    rows: list[str] = []
    for line in (markdown_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[\s:\-\|]+\|?$", stripped):
            continue
        rows.append(stripped)
    if len(rows) <= 1:
        return 0
    return max(0, len(rows) - 1)


def _extract_distinct_source_domains(text: str) -> set[str]:
    """Extract unique URL domains from text."""
    matches = re.findall(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?:/|\b)", text or "")
    return {match.lower().lstrip("www.") for match in matches if match}


def _extract_reported_evidence_completeness(
    answer_text: str,
    *,
    final_meta: dict[str, Any] | None = None,
) -> tuple[float | None, bool]:
    """Read reported evidence completeness when recap/report summaries provide it."""
    meta = final_meta if isinstance(final_meta, dict) else {}
    value = meta.get("evidence_completeness")
    if isinstance(value, (float, int)):
        clamped = max(0.0, min(float(value), 1.0))
        source_fields_missing = bool(meta.get("evidence_source_fields_missing"))
        return clamped, source_fields_missing

    match = _EVIDENCE_COMPLETENESS_RE.search(answer_text or "")
    if not match:
        return None, False
    percent = max(0, min(int(match.group(1)), 100))
    source_fields_missing = "fail-safe (source fields missing" in (answer_text or "").lower()
    return round(percent / 100.0, 3), source_fields_missing


def _extract_requested_item_count(question: str) -> int | None:
    """Infer explicit requested story/item count from prompt text."""
    text = question or ""
    match = _REQUESTED_ITEMS_PREFIX_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)), 25))

    bare_match = _REQUESTED_ITEMS_BARE_RE.search(text)
    if bare_match:
        return max(1, min(int(bare_match.group(1)), 25))

    return None


def _with_requested_item_target(
    final_meta: dict[str, Any] | None,
    *,
    question: str,
) -> dict[str, Any]:
    meta = dict(final_meta) if isinstance(final_meta, dict) else {}
    requested = _extract_requested_item_count(question)
    if isinstance(requested, int):
        meta["requested_item_count"] = requested
    return meta


# ---------------------------------------------------------------------------
# Answer quality scoring
# ---------------------------------------------------------------------------


def _score_answer_quality(
    answer_text: str,
    *,
    final_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute deterministic quality metadata for a candidate answer."""
    text = answer_text or ""
    lowered = text.lower()
    meta = final_meta if isinstance(final_meta, dict) else {}

    item_count = _count_markdown_table_items(text)
    bullet_count = sum(
        1
        for line in text.splitlines()
        if line.strip().startswith(("- ", "* ", "• "))
    )
    total_items = max(item_count, bullet_count)

    domains = _extract_distinct_source_domains(text)
    domain_count = len(domains)

    freshness_hits = [marker for marker in _FRESHNESS_MARKERS if marker in lowered]
    freshness_from_meta = 0
    if isinstance(meta.get("freshness_cues"), list):
        freshness_from_meta = len([x for x in meta.get("freshness_cues", []) if isinstance(x, str) and x.strip()])
    freshness_count = len(freshness_hits) + freshness_from_meta

    uncertainty_hits = [marker for marker in _UNCERTAINTY_MARKERS if marker in lowered]
    uncertainty_count = len(uncertainty_hits)
    evidence_completeness, evidence_source_fields_missing = _extract_reported_evidence_completeness(
        text,
        final_meta=meta,
    )

    score = 45
    reasons: list[str] = []
    requested_item_count = meta.get("requested_item_count")
    if isinstance(requested_item_count, int):
        requested_item_count = max(1, min(int(requested_item_count), 25))
    else:
        requested_item_count = None

    if total_items >= 6:
        score += 20
        reasons.append("Strong item coverage detected.")
    elif total_items >= 3:
        score += 10
        reasons.append("Moderate item coverage detected.")
    else:
        score -= 12
        reasons.append("Limited item coverage detected.")

    if domain_count >= 3:
        score += 20
        reasons.append("Good source-domain diversity detected.")
    elif domain_count == 2:
        score += 10
        reasons.append("Some source-domain diversity detected.")
    elif domain_count == 1:
        score += 2
        reasons.append("Only one source domain detected.")
    else:
        score -= 8
        reasons.append("No source domains detected.")

    if freshness_count >= 2:
        score += 12
        reasons.append("Strong freshness cues detected.")
    elif freshness_count == 1:
        score += 6
        reasons.append("Freshness cue detected.")
    else:
        score -= 4
        reasons.append("No freshness cues detected.")

    if uncertainty_count >= 3:
        score -= 25
        reasons.append("Multiple uncertainty/partial-coverage markers detected.")
    elif uncertainty_count >= 1:
        score -= 10
        reasons.append("Some uncertainty markers detected.")
    else:
        score += 8
        reasons.append("Low uncertainty language.")

    if evidence_completeness is None:
        reasons.append("Evidence completeness metric not available.")
    elif evidence_source_fields_missing:
        reasons.append("Evidence completeness in fail-safe mode (source fields missing).")
    elif evidence_completeness >= 0.8:
        score += 10
        reasons.append("Strong claim-to-evidence completeness detected.")
    elif evidence_completeness >= 0.6:
        score += 2
        reasons.append("Moderate claim-to-evidence completeness detected.")
    elif evidence_completeness >= 0.4:
        score -= 12
        reasons.append("Low claim-to-evidence completeness detected.")
    else:
        score -= 22
        reasons.append("Very low claim-to-evidence completeness detected.")

    score = max(0, min(100, int(score)))
    if score >= 75:
        status = "high"
    elif score >= 45:
        status = "medium"
    else:
        status = "low"
    if (
        evidence_completeness is not None
        and not evidence_source_fields_missing
        and evidence_completeness < 0.5
        and status != "low"
    ):
        status = "low"
        reasons.append("Low evidence completeness forced low-confidence status.")
    if isinstance(requested_item_count, int):
        if total_items < requested_item_count:
            score = max(0, score - 30)
            status = "low"
            reasons.append(f"Requested {requested_item_count} items but only {total_items} were included.")
        else:
            score = min(100, score + 8)
            reasons.append(f"Requested item target met ({total_items}/{requested_item_count}).")

    return {
        "score": score,
        "status": status,
        "reasons": reasons,
        "item_count": int(total_items),
        "table_item_count": int(item_count),
        "source_domain_count": int(domain_count),
        "freshness_cue_count": int(freshness_count),
        "uncertainty_marker_count": int(uncertainty_count),
        "evidence_completeness": evidence_completeness,
        "evidence_source_fields_missing": bool(evidence_source_fields_missing),
        "requested_item_count": requested_item_count,
    }


def _safe_score_answer_quality(
    answer_text: str,
    *,
    final_meta: dict[str, Any] | None = None,
    context: str = "ask",
) -> dict[str, Any]:
    """Failure-safe wrapper for answer quality scoring."""
    try:
        result = _b("_score_answer_quality", _score_answer_quality)(answer_text, final_meta=final_meta)
        evidence = result.get("evidence_completeness")
        source_fields_missing = bool(result.get("evidence_source_fields_missing"))
        if isinstance(evidence, (int, float)) and float(evidence) < 0.5 and not source_fields_missing:
            _b("_record_quality_metric", _record_quality_metric)("ask_low_evidence_completeness", context=context)
        return result
    except Exception as exc:
        _b("_record_quality_metric", _record_quality_metric)("ask_quality_scoring_error", context=context)
        log.debug("Answer quality scoring failed: %s", exc)
        requested_item_count = None
        if isinstance(final_meta, dict) and isinstance(final_meta.get("requested_item_count"), int):
            requested_item_count = max(1, min(int(final_meta["requested_item_count"]), 25))
        return {
            "score": 50,
            "status": "medium",
            "reasons": ["Quality scoring unavailable; using neutral fallback."],
            "item_count": 0,
            "table_item_count": 0,
            "source_domain_count": 0,
            "freshness_cue_count": 0,
            "uncertainty_marker_count": 0,
            "requested_item_count": requested_item_count,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Multi-chunk / attachment helpers
# ---------------------------------------------------------------------------


def _should_prefer_file_for_multichunk_response(
    *,
    question: str,
    chunks: list[str],
    response_text: str,
) -> bool:
    """Use one attachment when recap/list responses would otherwise fragment across messages."""
    requested = _extract_requested_item_count(question)
    lowered = (question or "").lower()
    recap_like = any(token in lowered for token in ("recap", "headlines", "stories", "this week", "weekend"))
    if _b("_should_package_as_attachment", _should_package_as_attachment)(response_text, chunks):
        return True
    if len(chunks) <= 1:
        return False
    if isinstance(requested, int) and requested >= 6:
        return True
    if recap_like and len(response_text) >= 2800:
        return True
    return False


# ---------------------------------------------------------------------------
# Coverage / embed summary helpers
# ---------------------------------------------------------------------------


def _build_coverage_summary_for_embed(final_meta: dict[str, Any] | None) -> str | None:
    """Create compact quality/coverage text for attachment-first embeds."""
    if not isinstance(final_meta, dict):
        return None
    retry_meta = final_meta.get("answer_quality_retry")
    degrade_mode = ""
    if isinstance(retry_meta, dict):
        degrade_mode = str(retry_meta.get("degrade_mode", "")).strip().lower()
    degrade_context = "Runtime constrained · retry narrower scope/timeframe" if degrade_mode == "constrained" else None

    answer_quality = final_meta.get("answer_quality")
    if not isinstance(answer_quality, dict):
        return degrade_context

    status = str(answer_quality.get("status", "")).strip().lower()
    if status not in {"high", "medium", "low"}:
        return degrade_context

    item_count = answer_quality.get("item_count")
    requested = answer_quality.get("requested_item_count")
    if isinstance(item_count, int) and isinstance(requested, int) and requested > 0:
        shortfall = max(int(requested) - int(item_count), 0)
        if status == "low" and shortfall > 0:
            summary = f"Coverage {status} · {item_count}/{requested} items (short {shortfall}) · retry narrower scope"
        else:
            summary = f"Coverage {status} · {item_count}/{requested} items"
        return f"{summary} · {degrade_context}" if degrade_context else summary

    evidence = answer_quality.get("evidence_completeness")
    if isinstance(evidence, (int, float)):
        pct = max(0, min(100, int(round(float(evidence) * 100))))
        summary = f"Coverage {status} · evidence {pct}%"
        return f"{summary} · {degrade_context}" if degrade_context else summary

    summary = f"Coverage {status}"
    return f"{summary} · {degrade_context}" if degrade_context else summary


def _build_ask_recovery_block(final_meta: dict[str, Any] | None) -> str | None:
    """Build compact user-facing guidance when ask coverage/confidence is weak."""
    if not isinstance(final_meta, dict):
        return None
    retry_meta = final_meta.get("answer_quality_retry")
    degrade_mode = ""
    if isinstance(retry_meta, dict):
        degrade_mode = str(retry_meta.get("degrade_mode", "")).strip().lower()
    runtime_constrained = degrade_mode == "constrained"

    answer_quality = final_meta.get("answer_quality")
    if not isinstance(answer_quality, dict) and not runtime_constrained:
        return None
    answer_quality = answer_quality if isinstance(answer_quality, dict) else {}

    status = str(answer_quality.get("status", "")).strip().lower()
    if status not in {"low"} and not runtime_constrained:
        return None

    item_count = answer_quality.get("item_count")
    requested = answer_quality.get("requested_item_count")
    has_numeric_target = isinstance(item_count, int) and isinstance(requested, int) and requested > 0
    shortfall = max(int(requested) - int(item_count), 0) if has_numeric_target else 0
    evidence = answer_quality.get("evidence_completeness")
    evidence_low = isinstance(evidence, (int, float)) and float(evidence) < 0.6

    # Only show recovery block when there's a concrete numeric shortfall (user asked for N
    # items but fewer were delivered) or runtime is constrained. General "low" quality on
    # conversational or search responses doesn't warrant a user-visible warning.
    if shortfall <= 0 and not runtime_constrained:
        return None

    if shortfall > 0 and has_numeric_target:
        coverage_line = f"Coverage shortfall: **{item_count}/{requested}** requested items covered."
        scope_hint = (
            f"Scope hint: retry with a narrower ask (for example, top {max(3, min(int(requested), 6))}) "
            f"or ask explicitly for the missing **{shortfall}** item(s)."
        )
    else:
        coverage_line = "Coverage may be partial for this answer."
        scope_hint = "Scope hint: retry with a tighter timeframe, channel, or fewer requested items."
    if runtime_constrained:
        coverage_line = f"{coverage_line} Runtime mode is constrained right now."

    confidence_line = (
        "Confidence: partial — verify high-impact details with primary sources before acting."
        if evidence_low or status == "low"
        else "Confidence: mixed — verify key details if they are high impact."
    )
    return (
        "\n\n"
        "> ℹ️ **Recovery note:**\n"
        f"> - {coverage_line}\n"
        f"> - 🧭 {scope_hint}\n"
        f"> - 🔎 {confidence_line}\n"
    )


# ---------------------------------------------------------------------------
# Quality retry helpers
# ---------------------------------------------------------------------------


def _build_quality_broadening_prompt(question: str, quality_reasons: list[str]) -> str:
    """Build a one-pass broadening prompt for low-quality responses."""
    reason_text = "; ".join(quality_reasons[:3])
    return (
        f"{question}\n\n"
        "Please retry this answer once with broader coverage while staying concise:\n"
        "- Improve completeness (include key items if recap/table-like).\n"
        "- Include multiple independent sources when available.\n"
        "- Add freshness cues (dates/timeframe) when relevant.\n"
        "- Keep uncertainty notes brief and explicit if data is incomplete.\n"
        f"Prior quality signals: {reason_text or 'low confidence detected'}.\n\n"
        "Do NOT change the scope, timeframe, or subject of the original query. "
        "Only expand source coverage and add freshness. Answer the exact same question."
    )


def _quality_retry_improved(
    *,
    original: dict[str, Any],
    retried: dict[str, Any],
) -> bool:
    """Return True when retry quality is meaningfully better."""
    original_score = int(original.get("score", 0))
    retried_score = int(retried.get("score", 0))
    original_status = str(original.get("status", "low"))
    retried_status = str(retried.get("status", "low"))
    if retried_status == "high" and original_status != "high":
        return True
    return retried_score >= original_score + 10


async def _run_quality_auto_repair(
    *,
    question: str,
    response_text: str,
    model_used: str,
    final_meta: dict[str, Any] | None,
    quality_meta: dict[str, Any],
    context: str,
    run_retry_stream: Any,
    think_hook: Any | None = None,
    run_copilot_retry_stream: Any | None = None,
) -> dict[str, Any]:
    """Run quality repair path with bounded timeout and optional second Copilot attempt.

    W11-1: uses provider-aware timeout from _REPAIR_TIMEOUTS.
    W11-3: tries up to _QUALITY_RETRY_MAX_ATTEMPTS (2); second attempt uses Copilot if available.
    W11-4: returns repair_skipped / repair_improved flags for caller transparency.
    """
    profile_values = _b("get_effective_channel_profile", get_effective_channel_profile)()
    profile_name = str(
        (profile_values.get("retrieval_profile") if isinstance(profile_values, dict) else None)
        or "general"
    ).strip().lower()
    if profile_name == "auto":
        profile_name = "general"

    load_stats = _b("get_latency_load_snapshot", get_latency_load_snapshot)(command_hint=context)
    latency_policy = select_latency_budget_policy(
        profile_name=profile_name,
        load_stats=load_stats,
    )
    base_attempts = min(_QUALITY_RETRY_MAX_ATTEMPTS, 2) if _QUALITY_RETRY_MAX_ATTEMPTS > 0 else 0

    # W11-1: derive provider from model_used label and select provider-aware timeout
    _provider_hint = "gemini"
    _model_lower = (model_used or "").lower()
    if "copilot" in _model_lower or "gpt" in _model_lower or "claude" in _model_lower:
        _provider_hint = "copilot"
    elif "ollama" in _model_lower or "gemma" in _model_lower:
        _provider_hint = "ollama"
    base_timeout_seconds = _REPAIR_TIMEOUTS.get(_provider_hint, int(_QUALITY_RETRY_TIMEOUT_SECONDS))

    repair_budget = _b("apply_repair_budget", apply_repair_budget)(
        max_attempts=base_attempts,
        timeout_seconds=base_timeout_seconds,
        policy=latency_policy,
    )
    max_attempts = int(repair_budget["max_attempts"])
    timeout_seconds = int(repair_budget["timeout_seconds"])
    if load_stats is None:
        _b("_record_quality_metric", _record_quality_metric)("ask_budget_metrics_missing", context=context)
    _b("_record_budget_policy_metric", _record_budget_policy_metric)(
        path="ask_repair",
        profile=profile_name,
        load_tier=str(latency_policy.get("load_tier", "unknown")),
        decision=str(latency_policy.get("decision", "failsafe")),
    )
    _b("_record_quality_metric", _record_quality_metric)(
        f"ask_budget_decision_{latency_policy.get('decision', 'failsafe')}",
        context=context,
    )

    status = str(quality_meta.get("status", "unknown"))
    eligible = (
        status == "low"
        and model_used != "error"
        and max_attempts > 0
        and len((question or "").strip()) > 10  # skip repair for short follow-ups like "yes/no/ok"
    )

    current_meta = _with_requested_item_target(final_meta, question=question)
    requested_item_count = current_meta.get("requested_item_count")
    if isinstance(requested_item_count, int):
        requested_item_count = max(1, min(int(requested_item_count), 25))
    else:
        requested_item_count = None

    quality_payload = dict(quality_meta) if isinstance(quality_meta, dict) else {}
    if isinstance(requested_item_count, int):
        quality_payload["requested_item_count"] = requested_item_count
    current_meta["answer_quality"] = quality_payload
    retry_summary: dict[str, Any] = {
        "policy": "latency_aware_single_attempt",
        "max_attempts": max_attempts,
        "timeout_seconds": timeout_seconds,
        "profile_name": profile_name,
        "load_tier": latency_policy.get("load_tier", "unknown"),
        "latency_decision": latency_policy.get("decision", "failsafe"),
        "degrade_mode": latency_policy.get("degrade_mode", "normal"),
        "degrade_reasons": latency_policy.get("degrade_reasons", []),
        "metrics_available": latency_policy.get("metrics_available", False),
        "attempted": False,
        "attempt_count": 0,
        "eligible": eligible,
        "outcome": "skipped",
        "status_path": [status],
        "improved": False,
        "repair_skipped": False,
        "repair_improved": False,
        "requested_item_count": requested_item_count,
    }

    if not eligible:
        retry_summary["skip_reason"] = "high_quality" if status != "low" else "ineligible"
        # W11-4: load-based skip flag
        retry_summary["repair_skipped"] = latency_policy.get("decision") in ("skip", "degrade")
        current_meta["answer_quality_retry"] = retry_summary
        _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_skipped", context=context)
        return {
            "response_text": response_text,
            "model_used": model_used,
            "final_meta": current_meta,
            "quality_meta": quality_meta,
            "retry_summary": retry_summary,
            "retry_result": None,
            "repair_skipped": retry_summary["repair_skipped"],
            "repair_improved": False,
        }

    _b("_record_quality_metric", _record_quality_metric)("ask_low_score_detected", context=context)
    _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_attempted", context=context)
    retry_summary["attempted"] = True
    retry_summary["attempt_count"] = 1

    retry_question = _build_quality_broadening_prompt(
        question,
        quality_meta.get("reasons", []),
    )
    if think_hook is not None:
        await think_hook("Low confidence detected — broadening once…")

    # --- Attempt 1: primary repair (Gemini via run_retry_stream) ---
    first_result = None
    first_quality = None
    try:
        first_result = await asyncio.wait_for(
            run_retry_stream(retry_question),
            timeout=timeout_seconds,
        )
        first_quality = _b("_safe_score_answer_quality", _safe_score_answer_quality)(
            first_result.response_text,
            final_meta=_with_requested_item_target(first_result.final_meta, question=question),
            context=context,
        )
        retry_summary["status_path"].append(first_quality.get("status", "unknown"))
    except asyncio.TimeoutError:
        retry_summary["outcome"] = "failed"
        retry_summary["error"] = "timeout"
        _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_failed", context=context)
        current_meta["answer_quality_retry"] = retry_summary
        return {
            "response_text": response_text,
            "model_used": model_used,
            "final_meta": current_meta,
            "quality_meta": quality_meta,
            "retry_summary": retry_summary,
            "retry_result": None,
            "repair_skipped": False,
            "repair_improved": False,
        }
    except Exception as retry_exc:
        retry_summary["outcome"] = "failed"
        retry_summary["error"] = str(retry_exc)
        _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_failed", context=context)
        log.debug("Quality broadening retry failed (%s): %s", context, retry_exc)
        current_meta["answer_quality_retry"] = retry_summary
        return {
            "response_text": response_text,
            "model_used": model_used,
            "final_meta": current_meta,
            "quality_meta": quality_meta,
            "retry_summary": retry_summary,
            "retry_result": None,
            "repair_skipped": False,
            "repair_improved": False,
        }

    if _b("_quality_retry_improved", _quality_retry_improved)(original=quality_meta, retried=first_quality):
        improved_meta = _with_requested_item_target(first_result.final_meta, question=question)
        improved_meta["answer_quality"] = first_quality
        retry_summary["improved"] = True
        retry_summary["repair_improved"] = True
        retry_summary["outcome"] = "improved"
        improved_meta["answer_quality_retry"] = retry_summary
        _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_improved", context=context)
        return {
            "response_text": first_result.response_text,
            "model_used": first_result.model_used,
            "final_meta": improved_meta,
            "quality_meta": first_quality,
            "retry_summary": retry_summary,
            "retry_result": first_result,
            "repair_skipped": False,
            "repair_improved": True,
        }

    # --- Attempt 2 (W11-3): Copilot second attempt if first didn't improve ---
    if max_attempts >= 2 and run_copilot_retry_stream is not None:
        retry_summary["attempt_count"] = 2
        copilot_timeout = _REPAIR_TIMEOUTS.get("copilot", 20)
        try:
            if think_hook is not None:
                await think_hook("First repair attempt didn't improve — trying Copilot…")
            second_result = await asyncio.wait_for(
                run_copilot_retry_stream(retry_question),
                timeout=copilot_timeout,
            )
            second_quality = _b("_safe_score_answer_quality", _safe_score_answer_quality)(
                second_result.response_text,
                final_meta=_with_requested_item_target(second_result.final_meta, question=question),
                context=context,
            )
            retry_summary["status_path"].append(second_quality.get("status", "unknown"))
            if _b("_quality_retry_improved", _quality_retry_improved)(original=quality_meta, retried=second_quality):
                improved_meta = _with_requested_item_target(second_result.final_meta, question=question)
                improved_meta["answer_quality"] = second_quality
                retry_summary["improved"] = True
                retry_summary["repair_improved"] = True
                retry_summary["outcome"] = "improved_copilot"
                improved_meta["answer_quality_retry"] = retry_summary
                _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_improved_copilot", context=context)
                return {
                    "response_text": second_result.response_text,
                    "model_used": second_result.model_used,
                    "final_meta": improved_meta,
                    "quality_meta": second_quality,
                    "retry_summary": retry_summary,
                    "retry_result": second_result,
                    "repair_skipped": False,
                    "repair_improved": True,
                }
        except Exception as second_exc:
            log.debug("Copilot second repair attempt failed (%s): %s", context, second_exc)

    retry_summary["outcome"] = "no_improvement"
    current_meta["answer_quality_retry"] = retry_summary
    _b("_record_quality_metric", _record_quality_metric)("ask_quality_retry_no_improvement", context=context)
    return {
        "response_text": response_text,
        "model_used": model_used,
        "final_meta": current_meta,
        "quality_meta": quality_meta,
        "retry_summary": retry_summary,
        "retry_result": None,
        "repair_skipped": False,
        "repair_improved": False,
    }

# ---------------------------------------------------------------------------
# Originals registry — populated after all definitions so _b() can detect patches
# ---------------------------------------------------------------------------
_ORIG.update({
    "_score_answer_quality": _score_answer_quality,
    "_record_quality_metric": _record_quality_metric,
    "_record_budget_policy_metric": _record_budget_policy_metric,
    "_should_package_as_attachment": _should_package_as_attachment,
    "get_effective_channel_profile": get_effective_channel_profile,
    "get_latency_load_snapshot": get_latency_load_snapshot,
    "apply_repair_budget": apply_repair_budget,
    "_safe_score_answer_quality": _safe_score_answer_quality,
    "_quality_retry_improved": _quality_retry_improved,
})
