"""Policy helpers for profile-driven auto model routing."""

from __future__ import annotations

import os as _os
from dataclasses import dataclass

from config import cfg

_MINI_MODEL = _os.getenv("OPENAI_MINI_MODEL", "gpt-4o-mini")
_MINI_TOKEN_THRESHOLD = int(_os.getenv("MINI_TOKEN_THRESHOLD", "25"))

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
    text: str = "",
    has_tools: bool = False,
    recalled_context: bool = False,
) -> AutoRouteDecision:
    profile = normalize_routing_profile(routing_profile)

    # Fast-path: cheap mini-model for short queries
    token_count = len(text.split())  # rough word count as proxy
    if (
        token_count <= _MINI_TOKEN_THRESHOLD
        and not has_tools
        and not recalled_context
        and copilot_available
    ):
        return AutoRouteDecision("copilot", f"mini-model fast-path (≤{_MINI_TOKEN_THRESHOLD} tokens)", profile)

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
# Coding / programming route selection  (Phase 29)
# ---------------------------------------------------------------------------

_CODING_PATTERNS = _re.compile(
    r"\b("
    r"(?:debug(?:ging)?|fix\s+(?:the\s+)?(?:bug|error|issue|code|this))"
    r"|(?:refactor(?:ing)?)"
    r"|(?:implement(?:ing)?)"
    r"|(?:write\s+(?:a\s+)?(?:function|class|script|test|method|module|program|snippet))"
    r"|(?:code\s+review)"
    r"|(?:syntax\s+error)"
    r"|(?:traceback|stack\s+trace)"
    r"|(?:import\s+error|module\s+not\s+found|nameerror|typeerror|valueerror|attributeerror|keyerror|indexerror)"
    r"|(?:how\s+(?:do\s+i|to)\s+(?:write|code|implement|create|build|fix|debug))"
    r"|(?:python|javascript|typescript|rust|golang|java\b|c\+\+|c#|kotlin|swift|ruby|php|bash|shell\s+script|sql|react|vue|angular|django|fastapi|flask|node(?:\.js)?)"
    r"|(?:async(?:hronous)?|await|coroutine|decorator|lambda|generator|iterator|recursion)"
    r"|(?:unit\s+test|pytest|jest|mocha|test\s+case)"
    r"|(?:git\s+(?:commit|merge|rebase|branch|push|pull|diff|blame|stash))"
    r"|(?:docker(?:file)?|kubernetes|k8s|container)"
    r"|(?:api\s+(?:endpoint|route|call|request|response)|rest(?:ful)?|graphql)"
    r"|(?:regex|regular\s+expression)"
    r"|(?:linting|type\s+hints?|mypy|eslint|prettier)"
    r")\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CodingRouteDecision:
    """Routing decision for coding / programming queries."""

    matches: bool
    reason: str


def select_coding_route(query: str) -> CodingRouteDecision:
    """Decide whether a query should be fast-pathed to the Copilot proxy.

    Matches debugging, refactoring, implementation requests, language keywords,
    and common programming error types. When matched, callers should route to
    ``_try_copilot_proxy_reply`` (only when ``COPILOT_PROXY_ENABLED``).

    Args:
        query: The raw user query string.

    Returns:
        A ``CodingRouteDecision`` with ``matches=True`` when the query is a
        coding/programming query, ``matches=False`` otherwise.
    """
    if _CODING_PATTERNS.search(query or ""):
        return CodingRouteDecision(
            matches=True,
            reason="coding/programming query → Copilot",
        )
    return CodingRouteDecision(
        matches=False,
        reason="no coding pattern detected",
    )



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


# ---------------------------------------------------------------------------
# Generic web-search route selection
# ---------------------------------------------------------------------------

_WEB_SEARCH_PATTERNS = _re.compile(
    r"\b(search|find|look(ing)?\s+up|what('s|\s+is)|who('s|\s+is)|when|where|how\s+(much|many)|"
    r"news|latest|current|today|recent|price|cost|weather|forecast|score|match|game|"
    r"home[s]?\s+(for\s+sale|listing)|real\s+estate|buy|rent|stock|market|movie|film|"
    r"show|episode|actor|sport|team|player|result|standings|transfer|deal|rumor)\b",
    _re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WebSearchRouteDecision:
    """Routing decision for queries that benefit from live web data."""

    prefer_search: bool
    reason: str


def select_web_search_route(query: str) -> WebSearchRouteDecision:
    """Return prefer_search=True for any query that benefits from live web data.

    Matches broad web-search vocabulary (news, prices, weather, sports, etc.)
    and question forms.  Callers should prefer ``generate_web_search_report``
    which returns a Perplexity answer directly (marked ``_via perplexity-direct_``)
    without Gemini synthesis.

    Args:
        query: The raw user query string.

    Returns:
        A ``WebSearchRouteDecision`` with ``prefer_search=True`` when matched,
        or ``prefer_search=False`` otherwise.
    """
    q = (query or "").strip()
    if not q:
        return WebSearchRouteDecision(prefer_search=False, reason="empty query")
    if _WEB_SEARCH_PATTERNS.search(q):
        return WebSearchRouteDecision(
            prefer_search=True,
            reason="matched web-search pattern",
        )
    # Also trigger for queries that look like questions
    if q.endswith("?") or _re.match(
        r"^(what|who|where|when|why|how|can|does|is|are|show|find|get)\b",
        q,
        _re.IGNORECASE,
    ):
        return WebSearchRouteDecision(prefer_search=True, reason="question form")
    return WebSearchRouteDecision(prefer_search=False, reason="no web-search signal")


# ---------------------------------------------------------------------------
# Two-tier intent classification — regex fast path + LLM fallback
# ---------------------------------------------------------------------------

import asyncio as _asyncio
import logging as _logging
import os as _os

# Query-type patterns used by the two-tier classifier (mirrors model_router.py)
_CLASSIFY_CODE_PATTERN = _re.compile(
    r"\b(write|create|generate|fix|debug|refactor|review|explain)\s+(a\s+)?"
    r"(code|script|function|class|program|snippet|regex|query|sql)\b"
    r"|\b(python|javascript|typescript|rust|go|java|c\+\+|bash|shell|html|css)\b.{0,40}"
    r"\b(code|script|function|error|bug)\b"
    r"|\bcode\s+review\b"
    r"|\b(stack\s*trace|traceback|syntax\s+error|compile\s+error|runtime\s+error)\b",
    _re.IGNORECASE,
)

_CLASSIFY_CREATIVE_PATTERN = _re.compile(
    r"\b(write|compose|draft|create)\s+(a\s+)?"
    r"(story|poem|essay|article|blog\s+post|letter|email\s+draft|speech|song|haiku)\b"
    r"|\b(creative\s+writing|brainstorm|ideate)\b",
    _re.IGNORECASE,
)

_CLASSIFY_ANALYSIS_PATTERN = _re.compile(
    r"\b(analyze|compare|evaluate|assess|critique|summarize|synthesize)\b.{0,60}"
    r"\b(data|report|document|paper|article|research|findings|results|trends)\b"
    r"|\b(pros?\s+and\s+cons?|trade[\s-]?offs?|swot|cost[\s-]?benefit)\b",
    _re.IGNORECASE,
)

# Set ROUTING_USE_LLM_INTENT=true to enable the LLM fallback tier.
# When false (default) classify_query() runs pure regex, adding no latency.
ROUTING_USE_LLM_INTENT: bool = _os.getenv("ROUTING_USE_LLM_INTENT", "false").lower() == "true"

_LLM_CLASSIFY_PROMPT = (
    "Classify this user message into exactly one of: coding, vision, search, math, general.\n"
    "Reply with ONLY the single category word.\n"
    "Message: {text}"
)

_VALID_LLM_CATEGORIES = frozenset({"coding", "vision", "search", "math", "general"})

_policy_logger = _logging.getLogger(__name__)


async def _classify_text_with_llm(text: str) -> str | None:
    """Async Tier-2 helper: classify *text* via quick_generate.

    Returns a category from ``_VALID_LLM_CATEGORIES`` or ``None`` on failure.
    """
    try:
        from llm_client import quick_generate  # noqa: PLC0415

        prompt = _LLM_CLASSIFY_PROMPT.format(text=text[:500])
        raw = await quick_generate(prompt, max_tokens=10, temperature=0.0)
        category = raw.strip().lower()
        if category in _VALID_LLM_CATEGORIES:
            return category
        _policy_logger.debug("classify_query: LLM returned unknown category %r", category)
        return None
    except Exception:  # noqa: BLE001
        _policy_logger.debug("classify_query: LLM intent detection failed", exc_info=True)
        return None


def _llm_classify_sync(text: str) -> str | None:
    """Synchronous wrapper around :func:`_classify_text_with_llm`.

    Uses ``asyncio.run()`` when no event loop is running.  When called from
    inside a running loop it logs a debug message and returns ``None`` so the
    caller silently falls back to regex-only routing.
    """
    try:
        _asyncio.get_running_loop()
        _policy_logger.debug(
            "classify_query: skipping LLM fallback (running inside async loop); "
            "await classify_query_llm() directly from async callers."
        )
        return None
    except RuntimeError:
        pass  # No running loop — asyncio.run() is safe.

    return _asyncio.run(_classify_text_with_llm(text))


async def classify_query_llm(text: str) -> str | None:
    """Public async API for LLM-based intent classification.

    Classifies *text* into one of: ``"coding"``, ``"vision"``, ``"search"``,
    ``"math"``, ``"general"``.  Returns the category string or ``None`` on
    failure.  Async callers should use this instead of ``classify_query()``
    when they want an explicit LLM-based classification result.
    """
    return await _classify_text_with_llm(text)


# ---------------------------------------------------------------------------
# ModelRoute — routing decision envelope
# ---------------------------------------------------------------------------

class ModelRoute:
    """Represents a model routing decision produced by :func:`classify_query`."""

    __slots__ = ("model_type", "reason")

    def __init__(self, model_type: str, reason: str) -> None:
        self.model_type = model_type
        self.reason = reason

    def __repr__(self) -> str:
        return f"ModelRoute({self.model_type!r}, {self.reason!r})"


def classify_query(
    message: str,
    *,
    has_openai_key: bool = False,
    has_anthropic_key: bool = False,
    copilot_available: bool = False,
    has_image: bool = False,
    needs_tools: bool = False,
    model_preference: str = "auto",
    ollama_alive: bool = True,
    routing_profile: str = "",
    recalled_context: bool = False,
) -> ModelRoute:
    """Classify a query and return the optimal model route.

    Implements a two-tier intent detection strategy:

    **Tier 1 — regex fast path** (always runs):
    Strong structural signals (code blocks, programming keywords, creative
    writing phrases, analysis vocabulary) are matched against compiled
    regular expressions.  When a confident signal is found, the result is
    used immediately with no extra latency.

    **Tier 2 — LLM fallback** (optional, off by default):
    When no regex pattern fires AND ``ROUTING_USE_LLM_INTENT=true`` is set,
    a compact single-turn prompt is sent to ``quick_generate()`` to classify
    the message into one of ``coding | vision | search | math | general``.
    The result is mapped back to ``is_code``/``is_analysis`` flags and fed
    into :func:`select_auto_route`.  If the LLM call fails or returns an
    unknown category, the router falls back to ``"general"`` routing.

    When ``ROUTING_USE_LLM_INTENT`` is *not* set (the default) this function
    is a pure-regex, zero-latency classifier identical to the implementation
    in ``model_router.classify_query``.

    Priority order:
    1. Explicit user preference (not ``"auto"``) → honor it
    2. Image attached → Gemini (best multimodal)
    3. Needs tools → Gemini (native function calling)
    4. Two-tier intent classification → select provider via select_auto_route
    """
    # --- Explicit user preference ---
    if model_preference == "local":
        if not ollama_alive:
            return ModelRoute("gemini", "user preference: local but Ollama down — fallback to Gemini")
        return ModelRoute("ollama", "user preference: local")
    if model_preference == "gemini":
        return ModelRoute("gemini", "user preference: gemini")
    if model_preference == "copilot":
        if copilot_available:
            return ModelRoute("copilot", "user preference: copilot")
        return ModelRoute("gemini", "user preference: copilot but proxy unavailable — fallback to Gemini")
    if model_preference == "openai" and (has_openai_key or copilot_available):
        return ModelRoute("openai", "user preference: openai")
    if model_preference == "anthropic" and (has_anthropic_key or copilot_available):
        return ModelRoute("anthropic", "user preference: anthropic")

    # --- Image → Gemini (best multimodal) ---
    if has_image:
        return ModelRoute("gemini", "multimodal query (image attached)")

    # --- Tool-requiring → Gemini ---
    if needs_tools:
        tool_decision = select_tool_route(
            has_openai_key=has_openai_key,
            has_anthropic_key=has_anthropic_key,
            copilot_available=copilot_available,
            ollama_alive=ollama_alive,
        )
        return ModelRoute(tool_decision.provider, tool_decision.reason)

    # --- Tier 1: regex fast path ---
    is_code = bool(_CLASSIFY_CODE_PATTERN.search(message or ""))
    is_creative = bool(_CLASSIFY_CREATIVE_PATTERN.search(message or ""))
    is_analysis = bool(_CLASSIFY_ANALYSIS_PATTERN.search(message or ""))
    regex_matched = is_code or is_creative or is_analysis

    # --- Tier 2: LLM fallback (when enabled and regex gave no confident signal) ---
    if ROUTING_USE_LLM_INTENT and not regex_matched and message:
        llm_category = _llm_classify_sync(message)
        if llm_category == "coding":
            is_code = True
        elif llm_category in ("search", "math"):
            # No dedicated "search" provider; treat as analytical routing
            is_analysis = True
        # "vision" without has_image and "general" → leave all flags False

    decision = select_auto_route(
        has_openai_key=has_openai_key,
        has_anthropic_key=has_anthropic_key,
        copilot_available=copilot_available,
        ollama_alive=ollama_alive,
        is_code=is_code,
        is_creative=is_creative,
        is_analysis=is_analysis,
        routing_profile=routing_profile,
        text=message,
        has_tools=needs_tools,
        recalled_context=recalled_context,
    )
    return ModelRoute(decision.provider, decision.reason)
