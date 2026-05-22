"""Shared orchestration helpers for /ask streaming flows.

Canonical ask flow — Slack (slack_bot.handle_mention / handle_dm / _ask)
delegates core LLM orchestration here.

The primary entry point is ``run_ask_stream()``, which wraps the llm_stream call,
collects routing metadata, context badges, and returns an ``AskStreamResult``.

TODO: Remaining duplication between slack_bot._ask() and run_ask_stream():
  - slack_bot._ask() calls dashboard.api_handlers._execute_agent_ask (different code path)
    rather than run_ask_stream; unifying these would give Slack the same routing/retry logic
  - Progress streaming (slack_bot._edit_thinking_with_progress) could share a common
    periodic-update abstraction with run_ask_stream
  - Quality-retry logic (_run_quality_auto_repair) is not called in the Slack path;
    Slack responses skip cross-provider retry
  Tracked: td-wave5-ask-dedup
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from model_aliases import normalize_model_input
from runtime_state import request_context


@dataclass(slots=True)
class AskStreamResult:
    response_text: str = ""
    model_used: str = "unknown"
    final_meta: dict[str, Any] = field(default_factory=dict)
    routing_notes: list[str] = field(default_factory=list)
    context_badges: list[str] = field(default_factory=list)
    context_explainability_note: str = ""
    context_quality: dict[str, Any] = field(default_factory=dict)


_RETRIEVAL_BOUNDS = {
    "min_results": (1, 8),
    "max_query_variants": (1, 6),
    "provider_attempt_cap": (1, 6),
}
_REPAIR_BOUNDS = {
    "max_attempts": (0, 1),
    "timeout_seconds": (8, 45),
}

_PROFILE_BUDGETS: dict[str, dict[str, float | int]] = {
    "general": {
        "retrieval_scale": 1.0,
        "retrieval_floor": 1,
        "repair_timeout": 18,
    },
    "sports": {
        "retrieval_scale": 1.1,
        "retrieval_floor": 2,
        "repair_timeout": 24,
    },
    "news": {
        "retrieval_scale": 1.05,
        "retrieval_floor": 2,
        "repair_timeout": 22,
    },
    "gaming": {
        "retrieval_scale": 1.15,
        "retrieval_floor": 3,
        "repair_timeout": 24,
    },
    "engineering": {
        "retrieval_scale": 1.05,
        "retrieval_floor": 2,
        "repair_timeout": 20,
    },
}

_LOAD_TIER_MODIFIERS: dict[str, dict[str, float | int | bool | str]] = {
    "low": {
        "retrieval_scale": 1.0,
        "query_variant_delta": 0,
        "provider_attempt_delta": 0,
        "repair_timeout_scale": 1.0,
        "allow_repair_retry": True,
        "decision": "quality",
    },
    "medium": {
        "retrieval_scale": 0.9,
        "query_variant_delta": -1,
        "provider_attempt_delta": -1,
        "repair_timeout_scale": 0.85,
        "allow_repair_retry": True,
        "decision": "balanced",
    },
    "high": {
        "retrieval_scale": 0.75,
        "query_variant_delta": -2,
        "provider_attempt_delta": -2,
        "repair_timeout_scale": 0.7,
        "allow_repair_retry": False,
        "decision": "latency",
    },
    "unknown": {
        "retrieval_scale": 0.9,
        "query_variant_delta": -1,
        "provider_attempt_delta": -1,
        "repair_timeout_scale": 0.8,
        "allow_repair_retry": True,
        "decision": "failsafe",
    },
}


_DEGRADE_MODE_MODIFIERS: dict[str, dict[str, float | int | bool]] = {
    "normal": {
        "retrieval_scale": 1.0,
        "query_variant_delta": 0,
        "provider_attempt_delta": 0,
        "repair_timeout_scale": 1.0,
        "allow_repair_retry": True,
    },
    "constrained": {
        "retrieval_scale": 0.8,
        "query_variant_delta": -1,
        "provider_attempt_delta": -1,
        "repair_timeout_scale": 0.85,
        "allow_repair_retry": True,
    },
}


def _clamp_int(value: int, *, lower: int, upper: int) -> int:
    return max(lower, min(int(value), upper))


def classify_load_tier(load_stats: dict[str, Any] | None) -> tuple[str, float]:
    """Classify load deterministically from optional metrics snapshot."""
    if not isinstance(load_stats, dict) or not load_stats:
        return "unknown", 0.0

    def _safe_float(key: str, default: float = 0.0) -> float:
        value = load_stats.get(key, default)
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return default

    request_rate_rpm = _safe_float("request_rate_rpm")
    p95_latency_ms = _safe_float("p95_latency_ms")
    error_rate = _safe_float("error_rate")

    saturation = max(
        min(request_rate_rpm / 80.0, 1.0),
        min(p95_latency_ms / 2500.0, 1.0),
        min(error_rate / 0.12, 1.0),
    )
    if saturation >= 0.85:
        return "high", round(saturation, 3)
    if saturation >= 0.55:
        return "medium", round(saturation, 3)
    return "low", round(saturation, 3)


def classify_degrade_mode(
    load_tier: str,
    load_stats: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """Classify deterministic retrieval degradation mode for timeout/sparsity pressure."""
    if not isinstance(load_stats, dict):
        load_stats = {}

    def _safe_float(key: str, default: float = 0.0) -> float:
        value = load_stats.get(key, default)
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return default

    timeout_rate = _safe_float("provider_timeout_rate")
    sparsity_rate = _safe_float("retrieval_sparsity_rate")
    timeout_streak = _safe_float("consecutive_provider_timeouts")
    sparse_streak = _safe_float("consecutive_sparse_retrievals")

    reasons: list[str] = []
    if timeout_rate >= 0.08:
        reasons.append("provider_timeout_rate")
    if sparsity_rate >= 0.2:
        reasons.append("retrieval_sparsity_rate")
    if timeout_streak >= 2:
        reasons.append("provider_timeout_streak")
    if sparse_streak >= 2:
        reasons.append("retrieval_sparsity_streak")
    if load_tier == "high":
        reasons.append("high_load")

    if reasons:
        return "constrained", reasons
    return "normal", []


def _record_degrade_mode_metric(
    *,
    mode: str,
    path: str,
    reason: str = "unspecified",
) -> None:
    """Best-effort telemetry for deterministic degrade mode activations."""
    try:
        from metrics_collector import get_collector

        get_collector().record_degrade_mode_activation(
            mode=mode,
            path=path,
            reason=reason,
        )
    except Exception:  # broad: intentional
        pass


def select_latency_budget_policy(
    *,
    profile_name: str,
    load_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic, bounded budget policy for retrieval/repair paths."""
    profile_key = (profile_name or "general").strip().lower()
    if profile_key not in _PROFILE_BUDGETS:
        profile_key = "general"

    load_tier, saturation = classify_load_tier(load_stats)
    tier_cfg = _LOAD_TIER_MODIFIERS[load_tier]
    profile_cfg = _PROFILE_BUDGETS[profile_key]

    degrade_mode, degrade_reasons = classify_degrade_mode(load_tier, load_stats)
    degrade_cfg = _DEGRADE_MODE_MODIFIERS[degrade_mode]
    allow_retry = bool(tier_cfg["allow_repair_retry"]) and bool(degrade_cfg["allow_repair_retry"])

    if degrade_mode == "constrained":
        _record_degrade_mode_metric(
            mode=degrade_mode,
            path="ask_retrieval",
            reason=degrade_reasons[0] if degrade_reasons else "unspecified",
        )

    return {
        "profile_name": profile_key,
        "load_tier": load_tier,
        "saturation": saturation,
        "decision": str(tier_cfg["decision"]),
        "degrade_mode": degrade_mode,
        "degrade_reasons": degrade_reasons,
        "metrics_available": bool(load_stats),
        "retrieval": {
            "scale": (
                float(profile_cfg["retrieval_scale"])
                * float(tier_cfg["retrieval_scale"])
                * float(degrade_cfg["retrieval_scale"])
            ),
            "floor": int(profile_cfg["retrieval_floor"]),
            "query_variant_delta": int(tier_cfg["query_variant_delta"]) + int(degrade_cfg["query_variant_delta"]),
            "provider_attempt_delta": int(tier_cfg["provider_attempt_delta"])
            + int(degrade_cfg["provider_attempt_delta"]),
            "degrade_mode": degrade_mode,
        },
        "repair": {
            "allow_retry": allow_retry,
            "timeout_seconds": int(
                round(
                    int(profile_cfg["repair_timeout"])
                    * float(tier_cfg["repair_timeout_scale"])
                    * float(degrade_cfg["repair_timeout_scale"])
                )
            ),
        },
    }


def apply_retrieval_budget(
    *,
    min_results: int,
    max_query_variants: int,
    provider_attempt_cap: int,
    num_results: int,
    policy: dict[str, Any],
) -> dict[str, int]:
    """Apply policy to retrieval parameters while enforcing strict bounds."""
    min_results = _clamp_int(min_results, lower=1, upper=max(1, int(num_results)))
    max_query_variants = _clamp_int(
        max_query_variants,
        lower=_RETRIEVAL_BOUNDS["max_query_variants"][0],
        upper=_RETRIEVAL_BOUNDS["max_query_variants"][1],
    )
    provider_attempt_cap = _clamp_int(
        provider_attempt_cap,
        lower=_RETRIEVAL_BOUNDS["provider_attempt_cap"][0],
        upper=_RETRIEVAL_BOUNDS["provider_attempt_cap"][1],
    )

    retrieval_cfg = policy.get("retrieval", {}) if isinstance(policy, dict) else {}
    scale = float(retrieval_cfg.get("scale", 0.9))
    floor = _clamp_int(
        int(retrieval_cfg.get("floor", 1)),
        lower=_RETRIEVAL_BOUNDS["min_results"][0],
        upper=_RETRIEVAL_BOUNDS["min_results"][1],
    )
    variant_delta = int(retrieval_cfg.get("query_variant_delta", -1))
    provider_delta = int(retrieval_cfg.get("provider_attempt_delta", -1))

    effective_min = int(round(min_results * scale))
    effective_min = max(effective_min, floor)
    effective_min = _clamp_int(
        effective_min,
        lower=_RETRIEVAL_BOUNDS["min_results"][0],
        upper=min(_RETRIEVAL_BOUNDS["min_results"][1], max(1, int(num_results))),
    )
    effective_variants = _clamp_int(
        max_query_variants + variant_delta,
        lower=_RETRIEVAL_BOUNDS["max_query_variants"][0],
        upper=_RETRIEVAL_BOUNDS["max_query_variants"][1],
    )
    effective_provider_cap = _clamp_int(
        provider_attempt_cap + provider_delta,
        lower=_RETRIEVAL_BOUNDS["provider_attempt_cap"][0],
        upper=_RETRIEVAL_BOUNDS["provider_attempt_cap"][1],
    )

    return {
        "min_results": effective_min,
        "max_query_variants": effective_variants,
        "provider_attempt_cap": effective_provider_cap,
    }


def apply_repair_budget(
    *,
    max_attempts: int,
    timeout_seconds: int,
    policy: dict[str, Any],
) -> dict[str, int]:
    """Apply policy to repair limits while keeping deterministic fail-safe bounds."""
    capped_attempts = _clamp_int(
        max_attempts,
        lower=_REPAIR_BOUNDS["max_attempts"][0],
        upper=_REPAIR_BOUNDS["max_attempts"][1],
    )
    timeout_seconds = _clamp_int(
        timeout_seconds,
        lower=_REPAIR_BOUNDS["timeout_seconds"][0],
        upper=_REPAIR_BOUNDS["timeout_seconds"][1],
    )
    repair_cfg = policy.get("repair", {}) if isinstance(policy, dict) else {}
    allow_retry = bool(repair_cfg.get("allow_retry", True))
    policy_timeout = repair_cfg.get("timeout_seconds", timeout_seconds)
    try:
        policy_timeout = int(policy_timeout)
    except (TypeError, ValueError):
        policy_timeout = timeout_seconds
    effective_timeout = _clamp_int(
        policy_timeout,
        lower=_REPAIR_BOUNDS["timeout_seconds"][0],
        upper=_REPAIR_BOUNDS["timeout_seconds"][1],
    )
    effective_attempts = capped_attempts if allow_retry else 0
    return {
        "max_attempts": effective_attempts,
        "timeout_seconds": effective_timeout,
    }


def get_latency_load_snapshot(*, command_hint: str = "") -> dict[str, float] | None:
    """Best-effort metrics snapshot for policy decisions."""
    try:
        from metrics_collector import get_collector

        stats = get_collector().get_stats(hours=1)
        if not isinstance(stats, dict):
            return None
        total_commands = int(stats.get("total_commands", 0) or 0)
        if total_commands <= 0:
            return None

        percentiles = stats.get("response_time_percentiles", {})
        p95_latency = 0.0
        if isinstance(percentiles, dict):
            hint = (command_hint or "").strip().lower()
            if hint and isinstance(percentiles.get(hint), dict):
                p95_latency = float(percentiles[hint].get("p95", 0.0) or 0.0)
            if p95_latency <= 0.0:
                for values in percentiles.values():
                    if isinstance(values, dict):
                        try:
                            p95_latency = max(p95_latency, float(values.get("p95", 0.0) or 0.0))
                        except (TypeError, ValueError):
                            continue

        error_counts = stats.get("error_counts", {})
        total_errors = 0.0
        timeout_errors = 0.0
        sparsity_errors = 0.0
        if isinstance(error_counts, dict):
            for key, value in error_counts.items():
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                total_errors += numeric_value
                label = str(key or "").strip().lower()
                if "timeout" in label:
                    timeout_errors += numeric_value
                if any(token in label for token in ("spars", "sparse", "no_results", "insufficient_results")):
                    sparsity_errors += numeric_value

        return {
            "request_rate_rpm": round(total_commands / 60.0, 3),
            "p95_latency_ms": round(p95_latency * 1000.0, 3),
            "error_rate": round(total_errors / max(total_commands, 1), 4),
            "provider_timeout_rate": round(timeout_errors / max(total_commands, 1), 4),
            "retrieval_sparsity_rate": round(sparsity_errors / max(total_commands, 1), 4),
        }
    except Exception:  # broad: intentional
        return None


def normalize_model_preference(
    user_message: str,
    model_preference: str,
    needs_tools_fn: Callable[[str], bool],
) -> tuple[str, bool]:
    """Return effective model preference and whether local mode was upgraded."""
    model_preference = normalize_model_input(model_preference)
    if model_preference in {"local", "copilot"} and needs_tools_fn(user_message):
        return "gemini", True
    return model_preference, False


async def run_ask_stream(
    *,
    llm_stream: Callable[..., Any],
    user_message: str,
    history: list[dict[str, Any]],
    user_name: str,
    model_preference: str,
    channel_id: int | None,
    thread_id: int | None,
    user_id: str,
    on_tool_call: Any | None = None,
    on_partial_chunk: Callable[[str], Awaitable[None]] | None = None,
    on_finalized: Callable[[str, str], None] | None = None,
    update_history: Callable[[list[dict[str, Any]]], None] | None = None,
    context_controls: dict[str, Any] | None = None,
    routing_profile: str = "",
) -> AskStreamResult:
    """Run the LLM stream pipeline and return final /ask orchestration metadata."""
    result = AskStreamResult()
    with request_context(channel_id=channel_id, thread_id=thread_id, user_id=user_id):
        async for chunk_text, is_final, meta in llm_stream(
            user_message=user_message,
            history=history,
            user_name=user_name,
            on_tool_call=on_tool_call,
            model_preference=model_preference,
            context_controls=context_controls,
            routing_profile=routing_profile,
        ):
            if isinstance(meta, dict):
                badge = meta.get("context_badge")
                if isinstance(badge, str) and badge and badge not in result.context_badges:
                    result.context_badges.append(badge)

            if is_final:
                if isinstance(meta, dict):
                    result.final_meta = dict(meta)
                    model_name = meta.get("model_used")
                    if isinstance(model_name, str) and model_name:
                        result.model_used = model_name
                    routing_notes = meta.get("routing_notes")
                    if isinstance(routing_notes, list):
                        result.routing_notes.extend(str(note) for note in routing_notes if isinstance(note, str))
                    explainability_note = meta.get("explainability_note")
                    if isinstance(explainability_note, str):
                        result.context_explainability_note = explainability_note.strip()
                    context_quality = meta.get("context_quality")
                    if isinstance(context_quality, dict):
                        result.context_quality = dict(context_quality)
                    updated_history = meta.get("updated_history")
                    if isinstance(updated_history, list) and update_history is not None:
                        update_history(updated_history)

                result.response_text = chunk_text
                if on_finalized is not None:
                    on_finalized(result.model_used, result.response_text)
                break

            if on_partial_chunk is not None and chunk_text:
                await on_partial_chunk(chunk_text)

    return result
