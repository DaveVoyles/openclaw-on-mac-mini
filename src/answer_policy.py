"""
OpenClaw Answer Policy — centralized accept/reject decisions for LLM responses.

All response validation and direct-return gates live here so they can be
updated, tested, and reasoned about in one place. Modules that need to decide
whether a response is acceptable or should bypass synthesis import from here
rather than duplicating the logic inline.

Consolidated from:
  - llm_patterns._gemma_response_seems_valid()
  - llm_patterns._provider_response_seems_valid()
  - llm_tools._should_return_tool_result_directly()
"""

from __future__ import annotations

import re

__all__ = [
    "response_seems_valid",
    "should_return_directly",
]

# ---------------------------------------------------------------------------
# Gemma hallucination patterns
# ---------------------------------------------------------------------------
# These phrases appear when Gemma (local model) claims to be performing an
# action it cannot actually do, like browsing the web or checking Docker.

_GEMMA_HALLUCINATION_RE = re.compile(
    r"(i'?m?\s+)?(now\s+)?(searching|browsing|checking|fetching|looking\s+up)\b"
    r"|\b(let\s+me\s+)?(search|check|look\s+that\s+up|fetch)\s+(that|the|for)\b"
    r"|(checking|querying)\s+(zillow|redfin|the\s+server|docker|container|plex)\b"
    r"|\b(i\s+)?(don'?t|cannot|can'?t)\s+(access|browse|check|reach)\s+(the\s+)?(internet|web|real[\s-]?time|live|current)\b"
    r"|\b(as\s+an?\s+ai|as\s+a\s+language\s+model)\b.{0,80}\b(cannot|don'?t|no\s+access)\b"
    r"|\bi\s+don'?t\s+have\s+(real[\s-]?time|access\s+to|live)\b"
    r"|(would\s+need\s+to\s+|i\s+could\s+)?(search|check|query)\s+(this|that|it)\s+for\s+you\b"
    r"|\b(let\s+me\s+)(start|begin|check|search|look|find|locate)\b"
    r"|\bone\s+moment\b"
    r"|\bi'?ll\s+(search|check|look|find|locate|browse|start)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Remote provider placeholder patterns
# ---------------------------------------------------------------------------
# Remote fallback providers (Copilot proxy, OpenAI, Anthropic) sometimes reply
# with placeholder promises instead of actual answers. These should be rejected
# so routing falls through to Gemini.

_REMOTE_PROVIDER_PLACEHOLDER_RE = re.compile(
    r"\bone\s+moment\b"
    r"|\bi'?ll\s+(?:retrieve|check|look|search|find|locate|browse|pull)\b"
    r"|\blet\s+me\s+(?:retrieve|check|look(?:\s+that)?\s+up|search|find|locate|browse|pull)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Direct-return marker strings
# ---------------------------------------------------------------------------
# When a tool embeds these markers in its result, the orchestrator should skip
# Gemini synthesis and return the result text directly to the user.

_DIRECT_RETURN_MARKERS: dict[str, list[str]] = {
    "generate_sports_watch_report": ["_via perplexity-direct_"],
    "generate_news_report": ["_via perplexity-direct_"],
    "generate_weather_report": ["_via perplexity-direct_"],
    "generate_finance_report": ["_via perplexity-direct_"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def response_seems_valid(reply: str, *, provider: str) -> bool:
    """Return True if *reply* should be accepted as a real answer for *provider*.

    Decision matrix:
      - "gemma"   → rejected when it exhibits tool-hallucination patterns
      - all others → rejected when empty/trivial or when they return a
                     placeholder promise instead of a real answer

    Args:
        reply:    The candidate response text.
        provider: Lowercase provider label, e.g. "gemma", "copilot", "openai",
                  "anthropic", "gemini".
    """
    stripped = (reply or "").strip()
    if provider == "gemma":
        if len(stripped) < 10:
            return False
        return not bool(_GEMMA_HALLUCINATION_RE.search(stripped))
    # Remote providers
    if len(stripped) < 10:
        return False
    if _REMOTE_PROVIDER_PLACEHOLDER_RE.search(stripped):
        return False
    return True


def should_return_directly(tool_name: str, result: str) -> bool:
    """Return True when *result* from *tool_name* should bypass LLM synthesis.

    Some tool results (e.g. Perplexity's sports report) are already formatted
    for direct delivery to the user. Skipping synthesis avoids re-phrasing that
    strips source attributions, tables, or structured formatting.

    Args:
        tool_name: The name of the skill/tool that produced the result.
        result:    The raw result string returned by the tool.
    """
    markers = _DIRECT_RETURN_MARKERS.get(tool_name)
    if not markers:
        return False
    text = str(result or "")
    return any(marker in text for marker in markers)
