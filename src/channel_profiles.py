"""Channel-aware retrieval profile resolution with bounded overrides."""

from __future__ import annotations

from typing import Any

_RETRIEVAL_PROFILES: dict[str, dict[str, Any]] = {
    "general": {
        "min_results": 2,
        "expand_query": False,
        "retry_on_low_results": True,
        "expansion_context": "general",
        "max_query_variants": 2,
        "provider_attempt_cap": 4,
    },
    "sports": {
        "min_results": 4,
        "expand_query": True,
        "retry_on_low_results": True,
        "expansion_context": "sports_recap",
        "max_query_variants": 4,
        "provider_attempt_cap": 5,
    },
    "news": {
        "min_results": 3,
        "expand_query": True,
        "retry_on_low_results": True,
        "expansion_context": "news_recap",
        "max_query_variants": 3,
        "provider_attempt_cap": 5,
    },
    "gaming": {
        "min_results": 6,
        "expand_query": True,
        "retry_on_low_results": True,
        "expansion_context": "gaming_recap",
        "max_query_variants": 5,
        "provider_attempt_cap": 6,
    },
    "engineering": {
        "min_results": 3,
        "expand_query": True,
        "retry_on_low_results": True,
        "expansion_context": "engineering_ops",
        "max_query_variants": 3,
        "provider_attempt_cap": 5,
    },
}

_RETRIEVAL_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "min_results": (1, 8),
    "max_query_variants": (1, 6),
    "provider_attempt_cap": (1, 6),
}

_SPORTS_TERMS = {
    "sports", "lacrosse", "nba", "nfl", "mlb", "nhl", "watch", "score", "recap",
}
_NEWS_TERMS = {
    "news", "headlines", "breaking", "latest", "update", "timeline", "box office", "market recap",
}
_GAMING_TERMS = {
    "gaming", "videogame", "video game", "games", "steam", "xbox", "playstation", "nintendo",
    "esports", "patch notes", "game recap",
}
_ENGINEERING_TERMS = {
    "engineering", "ops", "incident", "outage", "deploy", "deployment", "latency", "error", "logs",
    "kubernetes", "k8s", "sre", "infrastructure", "service",
}


def _clamp_int(value: Any, *, lower: int, upper: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def _apply_numeric_override(
    base: dict[str, Any],
    *,
    source_value: Any,
    source_field: str,
    target_field: str,
    default_value: int,
    rejections: list[str],
) -> None:
    if source_value in (None, "", 0, "0"):
        return
    try:
        parsed = int(source_value)
    except (TypeError, ValueError):
        rejections.append(source_field)
        return
    lower, upper = _RETRIEVAL_INT_BOUNDS[target_field]
    base[target_field] = _clamp_int(parsed, lower=lower, upper=upper, default=default_value)


def _infer_topic_class(query: str, *, expansion_context: str = "") -> str:
    text = " ".join((query or "").strip().lower().split())
    context = " ".join((expansion_context or "").strip().lower().replace("_", " ").replace("-", " ").split())
    combined = f"{context} {text}".strip()
    if not combined:
        return "general"

    if any(term in combined for term in _SPORTS_TERMS):
        return "sports"
    if any(term in combined for term in _GAMING_TERMS):
        return "gaming"
    if any(term in combined for term in _NEWS_TERMS):
        return "news"
    if any(term in combined for term in _ENGINEERING_TERMS):
        return "engineering"
    return "general"


def resolve_retrieval_profile_settings(
    *,
    query: str,
    expansion_context: str,
    channel_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve deterministic retrieval settings from channel profile + topic."""
    profile_values = channel_profile or {}
    configured_profile = str(profile_values.get("retrieval_profile") or "auto").strip().lower()
    topic_class = _infer_topic_class(query, expansion_context=expansion_context)

    if configured_profile in _RETRIEVAL_PROFILES:
        profile_name = configured_profile
    else:
        profile_name = topic_class
        if profile_name not in _RETRIEVAL_PROFILES:
            profile_name = "general"

    base = dict(_RETRIEVAL_PROFILES[profile_name])
    rejections: list[str] = []

    _apply_numeric_override(
        base,
        source_value=profile_values.get("retrieval_min_results_override", 0),
        source_field="retrieval_min_results_override",
        target_field="min_results",
        default_value=int(base["min_results"]),
        rejections=rejections,
    )
    _apply_numeric_override(
        base,
        source_value=profile_values.get("retrieval_max_query_variants_override", 0),
        source_field="retrieval_max_query_variants_override",
        target_field="max_query_variants",
        default_value=int(base["max_query_variants"]),
        rejections=rejections,
    )
    _apply_numeric_override(
        base,
        source_value=profile_values.get("retrieval_provider_attempt_cap_override", 0),
        source_field="retrieval_provider_attempt_cap_override",
        target_field="provider_attempt_cap",
        default_value=int(base["provider_attempt_cap"]),
        rejections=rejections,
    )

    base["topic_class"] = topic_class
    base["profile_name"] = profile_name
    base["override_rejections"] = rejections
    return base
