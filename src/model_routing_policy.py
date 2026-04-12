"""Policy helpers for profile-driven auto model routing."""

from __future__ import annotations

from dataclasses import dataclass

from config import cfg

VALID_ROUTING_PROFILES = {
    "copilot-first",
    "balanced",
    "gemini-first",
    "cost-saver",
}

_ROUTING_PROFILE_ALIASES = {
    "copilot_first": "copilot-first",
    "gemini_first": "gemini-first",
    "cost_saver": "cost-saver",
}


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    name: str
    available: bool
    supports_native_tools: bool
    supports_multimodal: bool
    supports_raw_output: bool
    low_cost: bool = False


@dataclass(frozen=True, slots=True)
class AutoRouteDecision:
    provider: str
    reason: str
    profile: str


@dataclass(frozen=True, slots=True)
class ToolRouteDecision:
    provider: str
    reason: str


def normalize_routing_profile(profile: str) -> str:
    normalized = (profile or "").strip().lower()
    normalized = _ROUTING_PROFILE_ALIASES.get(normalized, normalized)
    if normalized in VALID_ROUTING_PROFILES:
        return normalized
    configured = (getattr(cfg, "routing_profile", "") or "").strip().lower()
    configured = _ROUTING_PROFILE_ALIASES.get(configured, configured)
    if configured in VALID_ROUTING_PROFILES:
        return configured
    return "copilot-first"


def build_provider_capability_registry(
    *,
    has_openai_key: bool,
    has_anthropic_key: bool,
    copilot_available: bool,
    ollama_alive: bool,
) -> dict[str, ProviderCapabilities]:
    return {
        "gemini": ProviderCapabilities(
            name="gemini",
            available=True,
            supports_native_tools=True,
            supports_multimodal=True,
            supports_raw_output=True,  # Perplexity-direct tool results bypass synthesis
            low_cost=False,
        ),
        "copilot": ProviderCapabilities(
            name="copilot",
            available=bool(copilot_available),
            supports_native_tools=False,
            supports_multimodal=bool(copilot_available),  # GPT-4o-vision via proxy
            supports_raw_output=False,
            low_cost=True,
        ),
        "openai": ProviderCapabilities(
            name="openai",
            available=bool(has_openai_key or copilot_available),
            supports_native_tools=False,
            supports_multimodal=bool(has_openai_key or copilot_available),  # GPT-4o-vision
            supports_raw_output=False,
            low_cost=False,
        ),
        "anthropic": ProviderCapabilities(
            name="anthropic",
            available=bool(has_anthropic_key or copilot_available),
            supports_native_tools=False,
            supports_multimodal=False,
            supports_raw_output=False,
            low_cost=False,
        ),
        "ollama": ProviderCapabilities(
            name="ollama",
            available=bool(ollama_alive),
            supports_native_tools=False,
            supports_multimodal=False,
            supports_raw_output=False,
            low_cost=True,
        ),
    }


def _prefer_specialized_non_tool_route(
    *,
    registry: dict[str, ProviderCapabilities],
    is_code: bool,
    is_creative: bool,
    is_analysis: bool,
    profile: str,
) -> AutoRouteDecision:
    if is_code:
        if registry["copilot"].available:
            return AutoRouteDecision("copilot", f"routing profile {profile}: code query", profile)
        if registry["anthropic"].available:
            return AutoRouteDecision("anthropic", f"routing profile {profile}: code query", profile)
        if registry["openai"].available:
            return AutoRouteDecision("openai", f"routing profile {profile}: code query", profile)
        return AutoRouteDecision("gemini", f"routing profile {profile}: code query fallback", profile)

    if is_creative:
        if registry["copilot"].available:
            return AutoRouteDecision("copilot", f"routing profile {profile}: creative query", profile)
        if registry["openai"].available:
            return AutoRouteDecision("openai", f"routing profile {profile}: creative query", profile)
        return AutoRouteDecision("gemini", f"routing profile {profile}: creative query fallback", profile)

    if is_analysis:
        if registry["copilot"].available:
            return AutoRouteDecision("copilot", f"routing profile {profile}: analysis query", profile)
        return AutoRouteDecision("gemini", f"routing profile {profile}: analysis query fallback", profile)

    if registry["ollama"].available:
        return AutoRouteDecision("ollama", f"routing profile {profile}: simple chat prefers local", profile)
    if registry["copilot"].available:
        return AutoRouteDecision(
            "copilot",
            f"routing profile {profile}: simple chat fallback (Ollama down)",
            profile,
        )
    return AutoRouteDecision(
        "gemini",
        f"routing profile {profile}: simple chat fallback (Ollama down)",
        profile,
    )


def select_auto_route(
    *,
    has_openai_key: bool,
    has_anthropic_key: bool,
    copilot_available: bool,
    ollama_alive: bool,
    is_code: bool,
    is_creative: bool,
    is_analysis: bool,
    routing_profile: str = "",
) -> AutoRouteDecision:
    profile = normalize_routing_profile(routing_profile)
    registry = build_provider_capability_registry(
        has_openai_key=has_openai_key,
        has_anthropic_key=has_anthropic_key,
        copilot_available=copilot_available,
        ollama_alive=ollama_alive,
    )

    if profile == "gemini-first":
        return AutoRouteDecision("gemini", "routing profile gemini-first", profile)

    if profile == "cost-saver":
        if registry["ollama"].available:
            return AutoRouteDecision("ollama", "routing profile cost-saver: local-first", profile)
        if registry["copilot"].available:
            return AutoRouteDecision("copilot", "routing profile cost-saver: proxy fallback", profile)
        return _prefer_specialized_non_tool_route(
            registry=registry,
            is_code=is_code,
            is_creative=is_creative,
            is_analysis=is_analysis,
            profile=profile,
        )

    if profile == "balanced":
        return _prefer_specialized_non_tool_route(
            registry=registry,
            is_code=is_code,
            is_creative=is_creative,
            is_analysis=is_analysis,
            profile=profile,
        )

    if registry["copilot"].available:
        if is_code:
            return AutoRouteDecision("copilot", "routing profile copilot-first: code query", profile)
        if is_creative:
            return AutoRouteDecision("copilot", "routing profile copilot-first: creative query", profile)
        if is_analysis:
            return AutoRouteDecision("copilot", "routing profile copilot-first: analysis query", profile)
        return AutoRouteDecision("copilot", "routing profile copilot-first: non-tool query", profile)

    return _prefer_specialized_non_tool_route(
        registry=registry,
        is_code=is_code,
        is_creative=is_creative,
        is_analysis=is_analysis,
        profile=profile,
    )


def select_tool_route(
    *,
    has_openai_key: bool,
    has_anthropic_key: bool,
    copilot_available: bool,
    ollama_alive: bool,
) -> ToolRouteDecision:
    registry = build_provider_capability_registry(
        has_openai_key=has_openai_key,
        has_anthropic_key=has_anthropic_key,
        copilot_available=copilot_available,
        ollama_alive=ollama_alive,
    )

    for provider_name in ("gemini", "anthropic", "openai", "copilot", "ollama"):
        capabilities = registry.get(provider_name)
        if capabilities and capabilities.available and capabilities.supports_native_tools:
            return ToolRouteDecision(
                provider=provider_name,
                reason=f"requires tool/function calling; selected native-tool provider: {provider_name}",
            )

    return ToolRouteDecision(
        provider="gemini",
        reason="requires tool/function calling; defaulted to Gemini because no native-tool provider was available",
    )


@dataclass(frozen=True, slots=True)
class ReflectionRouteDecision:
    provider: str
    reason: str


def select_reflection_route(*, copilot_available: bool) -> ReflectionRouteDecision:
    """Choose the best provider for self-evaluation reflection calls.

    Reflection is a plain-text generation task (no tools needed), so Copilot is
    preferred when available — it avoids spending Gemini quota on meta-tasks.

    Args:
        copilot_available: Whether the Copilot proxy endpoint is reachable.

    Returns:
        A ``ReflectionRouteDecision`` naming the provider and stating why.
    """
    if copilot_available:
        return ReflectionRouteDecision(
            provider="copilot",
            reason="reflection is a plain-text task; Copilot preferred to save Gemini quota",
        )
    return ReflectionRouteDecision(
        provider="gemini",
        reason="Copilot unavailable; falling back to Gemini for reflection",
    )


@dataclass(frozen=True, slots=True)
class SummarizationRouteDecision:
    provider: str
    reason: str


def select_summarization_route(*, copilot_available: bool) -> SummarizationRouteDecision:
    """Choose the best provider for conversation summarization tasks.

    Summarization is a plain-text generation task (no tools needed), so Copilot
    is preferred when available — it avoids spending Gemini quota on meta-tasks.
    Falls back to Gemini when Copilot is unavailable.

    Args:
        copilot_available: Whether the Copilot proxy endpoint is reachable.

    Returns:
        A ``SummarizationRouteDecision`` naming the provider and stating why.
    """
    if copilot_available:
        return SummarizationRouteDecision(
            provider="copilot",
            reason="summarization is a plain-text task; Copilot preferred to save Gemini quota",
        )
    return SummarizationRouteDecision(
        provider="gemini",
        reason="Copilot unavailable; falling back to Gemini for summarization",
    )


@dataclass(frozen=True, slots=True)
class MultimodalRouteDecision:
    provider: str
    reason: str


def select_multimodal_route(
    *,
    copilot_available: bool,
    has_openai_key: bool,
) -> MultimodalRouteDecision:
    """Choose the best provider for multimodal (image + text) analysis.

    GPT-4o served through the Copilot proxy supports vision, so Copilot is
    preferred when available.  Falls back to OpenAI direct if a key is present,
    and finally to Gemini (which always supports multimodal via the native SDK).

    Args:
        copilot_available: Whether the Copilot proxy endpoint is reachable.
        has_openai_key: Whether a direct OpenAI API key is configured.

    Returns:
        A ``MultimodalRouteDecision`` naming the provider and stating why.
    """
    if copilot_available:
        return MultimodalRouteDecision(
            provider="copilot",
            reason="GPT-4o-vision via Copilot proxy preferred for multimodal tasks",
        )
    if has_openai_key:
        return MultimodalRouteDecision(
            provider="openai",
            reason="GPT-4o-vision via direct OpenAI API key",
        )
    return MultimodalRouteDecision(
        provider="gemini",
        reason="Copilot unavailable and no OpenAI key; falling back to Gemini vision",
    )


# ---------------------------------------------------------------------------
# Real-time / current-events route selection
# ---------------------------------------------------------------------------

import re as _re

_REALTIME_PATTERNS = _re.compile(
    r"\b("
    r"news|headline|breaking|current\s+event"
    r"|what.s\s+(happening|going\s+on|in\s+the\s+news)"
    r"|latest|today.s\s+(news|headline|story|update)"
    r"|top\s+stor(?:y|ies)"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RealtimeRouteDecision:
    """Routing decision for real-time / current-events queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_news_report" when matched, "" otherwise
    reason: str


def select_realtime_route(query: str) -> RealtimeRouteDecision:
    """Decide whether a query should be routed to Perplexity for real-time data.

    Matches news, headlines, and current-events vocabulary.  When matched,
    callers should prefer the ``generate_news_report`` skill which returns
    a Perplexity answer directly (marked ``_via perplexity-direct_``) without
    Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``RealtimeRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_news_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _REALTIME_PATTERNS.search(query or ""):
        return RealtimeRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_news_report",
            reason="query matches real-time / current-events pattern → Perplexity direct",
        )
    return RealtimeRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no real-time pattern detected",
    )


# ---------------------------------------------------------------------------
# Research synthesis route selection
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResearchSynthesisRouteDecision:
    """Routing decision for long-form research synthesis tasks."""

    provider: str  # "copilot", "gemini"
    reason: str


def select_research_synthesis_route(
    *, copilot_available: bool
) -> ResearchSynthesisRouteDecision:
    """Choose the best provider for research synthesis (no tool-calling needed).

    Long-form synthesis is a text-generation task that does not require
    Gemini-native tool calling.  Copilot (GPT-4o) is preferred when available
    to save Gemini quota; Gemini thinking mode is used as fallback.

    Args:
        copilot_available: Whether the Copilot proxy endpoint is reachable.

    Returns:
        A ``ResearchSynthesisRouteDecision`` naming the selected provider.
    """
    if copilot_available:
        return ResearchSynthesisRouteDecision(
            provider="copilot",
            reason="long-form synthesis routed to Copilot to save Gemini quota",
        )
    return ResearchSynthesisRouteDecision(
        provider="gemini",
        reason="Copilot unavailable; falling back to Gemini thinking model for synthesis",
    )
