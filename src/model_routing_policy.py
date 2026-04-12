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
# Sports schedule / watch-guide route selection
# ---------------------------------------------------------------------------

_SPORTS_SCHEDULE_PATTERNS = _re.compile(
    r"\b("
    r"(?:lacrosse|football|basketball|baseball|hockey|soccer|tennis|golf"
    r"|volleyball|wrestling|swimming|track|rowing|crew|rugby|cricket"
    r"|softball|field\s+hockey|water\s+polo|cross\s+country|gymnastics"
    r"|nfl|nba|nhl|mlb|mls|ncaa|college)\s+"
    r"(?:game|games|match|matches|schedule|today|tonight|this\s+week|watch)"
    r"|(?:games?|matches?|schedule)\s+(?:today|tonight|this\s+week)"
    r"|what\s+(?:games?|sports?)\s+(?:are|is)\s+(?:on|today|tonight|playing)"
    r"|watch\s+guide"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SportsRouteDecision:
    """Routing decision for sports schedule / watch-guide queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_sports_watch_report" when matched, "" otherwise
    reason: str


def select_sports_route(query: str) -> SportsRouteDecision:
    """Decide whether a query should be fast-pathed to the sports watch skill.

    Matches sports schedule vocabulary.  When matched, callers should prefer
    ``generate_sports_watch_report`` which returns a Perplexity answer directly
    (marked ``_via perplexity-direct_``) without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``SportsRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_sports_watch_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _SPORTS_SCHEDULE_PATTERNS.search(query or ""):
        return SportsRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_sports_watch_report",
            reason="query matches sports schedule / watch-guide pattern → Perplexity direct",
        )
    return SportsRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no sports schedule pattern detected",
    )


# ---------------------------------------------------------------------------
# Sports scores / results route selection
# ---------------------------------------------------------------------------

_SPORTS_SCORES_PATTERNS = _re.compile(
    r"\b("
    r"(?:final\s+score|score\s+of|game\s+result|match\s+result)"
    r"|(?:did\s+(?:the\s+)?[a-z][\w\s]{1,30}(?:win|lose|beat|fall\s+to))"
    r"|(?:who\s+won(?:\s+(?:last\s+night|yesterday|this\s+morning|the\s+[\w\s]{1,25}(?:game|match|series|championship|title|race|tournament))))"
    r"|who\s+won\?"
    r"|(?:(?:last\s+night|yesterday|this\s+morning).{0,30}(?:game|match|score))"
    r"|(?:(?:game|match|score).{0,30}(?:last\s+night|yesterday))"
    r"|(?:recap\s+(?:the\s+)?(?:game|match|series))"
    r"|what\s+(?:was\s+)?the\s+(?:final\s+)?score"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SportsScoresRouteDecision:
    """Routing decision for sports scores / historical results queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_sports_scores_report" when matched, "" otherwise
    reason: str


def select_sports_scores_route(query: str) -> SportsScoresRouteDecision:
    """Decide whether a query should be fast-pathed to the sports scores skill.

    Matches historical game result vocabulary — distinct from schedule/watch-guide
    queries handled by ``select_sports_route()``.  When matched, callers should
    prefer ``generate_sports_scores_report`` which returns a Perplexity answer
    directly without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``SportsScoresRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_sports_scores_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _SPORTS_SCORES_PATTERNS.search(query or ""):
        return SportsScoresRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_sports_scores_report",
            reason="query matches sports scores / results pattern → Perplexity direct",
        )
    return SportsScoresRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no sports scores pattern detected",
    )


# ---------------------------------------------------------------------------
# Entertainment / streaming route selection
# ---------------------------------------------------------------------------

_ENTERTAINMENT_PATTERNS = _re.compile(
    r"\b("
    r"(?:movies?\s+(?:in\s+theaters?|playing|out\s+now|this\s+week(?:end)?))"
    r"|(?:what(?:'s|\s+is)\s+(?:new|playing|on|streaming)\s+(?:on\s+)?(?:netflix|hulu|hbo|disney\+?|apple\s+tv\+?|paramount\+?|peacock|max|prime\s+video))"
    r"|(?:(?:new\s+)?(?:releases?|arrivals?|titles?)\s+(?:on|to)\s+(?:netflix|hulu|hbo|disney\+?|apple\s+tv\+?|paramount\+?|peacock|max|prime\s+video))"
    r"|rotten\s+tomatoes"
    r"|metacritic\s+score"
    r"|(?:is\s+.{1,40}\s+(?:worth\s+watching|good|worth\s+it|worth\s+the\s+hype))"
    r"|what\s+to\s+watch\s+(?:this\s+week(?:end)?|tonight|today)"
    r"|streaming\s+(?:this\s+week(?:end)?|releases?|new|now)"
    r"|(?:now\s+(?:streaming|playing|in\s+theaters?))"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EntertainmentRouteDecision:
    """Routing decision for entertainment / streaming queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_entertainment_report" when matched, "" otherwise
    reason: str


def select_entertainment_route(query: str) -> EntertainmentRouteDecision:
    """Decide whether a query should be fast-pathed to the entertainment skill.

    Matches movies-in-theaters, streaming service new arrivals, critic score
    lookups, and "what to watch" queries.  When matched, callers should prefer
    ``generate_entertainment_report`` which returns a Perplexity answer directly
    without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        An ``EntertainmentRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_entertainment_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _ENTERTAINMENT_PATTERNS.search(query or ""):
        return EntertainmentRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_entertainment_report",
            reason="query matches entertainment / streaming pattern → Perplexity direct",
        )
    return EntertainmentRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no entertainment pattern detected",
    )


# ---------------------------------------------------------------------------
# Weather query route selection
# ---------------------------------------------------------------------------

_WEATHER_PATTERNS = _re.compile(
    r"\b("
    r"(?:what(?:'s|\s+is)\s+the\s+weather)"
    r"|(?:weather\s+(?:today|tonight|this\s+week(?:end)?|tomorrow|forecast|in|for|at|right\s+now|currently|conditions?))"
    r"|(?:(?:current\s+)?(?:temperature|temp|conditions?)\s+(?:in|at|for|outside))"
    r"|(?:is\s+it\s+(?:going\s+to\s+)?(?:rain|snow|hot|cold|warm|humid|sunny|cloudy|windy)\b)"
    r"|(?:will\s+it\s+(?:rain|snow|be\s+(?:hot|cold|warm|sunny|cloudy|windy)))"
    r"|(?:how\s+(?:hot|cold|warm|rainy|snowy|windy)\s+(?:is\s+it|will\s+it\s+be))"
    r"|(?:(?:rain|snow|thunder(?:storm)?|hurricane|tornado|blizzard|heatwave)\s+(?:forecast|warning|watch|expected|coming))"
    r"|(?:umbrella|jacket|coat)\s+(?:today|tomorrow|needed)"
    r"|weather\s+report"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WeatherRouteDecision:
    """Routing decision for weather queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_weather_report" when matched, "" otherwise
    reason: str


def select_weather_route(query: str) -> WeatherRouteDecision:
    """Decide whether a query should be fast-pathed to the weather skill.

    Matches current conditions, forecasts, and weather-related planning queries.
    When matched, callers should prefer ``generate_weather_report`` which returns
    a Perplexity answer directly without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``WeatherRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_weather_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _WEATHER_PATTERNS.search(query or ""):
        return WeatherRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_weather_report",
            reason="query matches weather / forecast pattern → Perplexity direct",
        )
    return WeatherRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no weather pattern detected",
    )


# ---------------------------------------------------------------------------
# Finance / markets query route selection
# ---------------------------------------------------------------------------

_FINANCE_PATTERNS = _re.compile(
    r"\b("
    r"(?:stock\s+(?:price|prices?|market|markets?|news|update|today|performance|forecast))"
    r"|(?:(?:current|today(?:'s)?|live)\s+(?:stock|market|share|index|crypto)\s*(?:price|prices?|value|update)?)"
    r"|(?:(?:how\s+is|how\s+are|what(?:'s|\s+is))\s+(?:the\s+)?(?:market|markets?|nasdaq|s&p|dow|nyse|crypto)\s+(?:doing|today|now|performing|trading))"
    r"|(?:(?:nasdaq|s&p\s*500|dow\s+jones?|nyse|russell)\s+(?:today|now|this\s+week|performance|update|forecast))"
    r"|(?:(?:bitcoin|btc|ethereum|eth|crypto(?:currency)?)\s+(?:price|prices?|today|now|update|forecast|market))"
    r"|(?:market\s+(?:open|close|update|recap|summary|report|news|today|this\s+week))"
    r"|(?:earnings?\s+(?:report|announcement|release|today|this\s+week|results?))"
    r"|(?:(?:interest|mortgage|fed|federal\s+reserve)\s+(?:rate|rates?))"
    r"|(?:inflation\s+(?:rate|data|report|today|update|news))"
    r"|financial\s+(?:news|update|report|market|summary)"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FinanceRouteDecision:
    """Routing decision for finance / market queries."""

    prefer_perplexity: bool
    tool_name: str  # "generate_finance_report" when matched, "" otherwise
    reason: str


def select_finance_route(query: str) -> FinanceRouteDecision:
    """Decide whether a query should be fast-pathed to the finance skill.

    Matches stock prices, market updates, crypto prices, earnings reports, and
    macroeconomic data queries. When matched, callers should prefer
    ``generate_finance_report`` which returns a Perplexity answer directly
    without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``FinanceRouteDecision`` with ``prefer_perplexity=True`` and
        ``tool_name="generate_finance_report"`` when matched, or
        ``prefer_perplexity=False`` and an empty ``tool_name`` otherwise.
    """
    if _FINANCE_PATTERNS.search(query or ""):
        return FinanceRouteDecision(
            prefer_perplexity=True,
            tool_name="generate_finance_report",
            reason="query matches finance / market pattern → Perplexity direct",
        )
    return FinanceRouteDecision(
        prefer_perplexity=False,
        tool_name="",
        reason="no finance pattern detected",
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
