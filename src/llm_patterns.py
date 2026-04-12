"""
OpenClaw LLM Patterns — regex patterns, query classification, and response validation.
"""

import asyncio
import logging
import re

from google import genai

from config import cfg
from llm_client import MAX_TOKENS, MODEL_NAME, _client, _record_usage
from llm_ratelimit import rate_limiter as _rate_limiter

log = logging.getLogger("openclaw.llm.patterns")

# ---------------------------------------------------------------------------
# Routing heuristics — decide whether to use Gemma (local) or Gemini
# ---------------------------------------------------------------------------

# Tier 1 — Route DIRECTLY to Gemini.
# These are imperative action+noun combos that require live tool execution.
_LIVE_ACTION_PATTERN = re.compile(
    # Container / service control verbs
    r"\b(restart|reboot|stop|start|kill)\b.{0,40}\b(container|service|plex|sonarr|radarr|lidarr|sabnzbd|qbittorrent|prowlarr|jellyfin)\b"
    # Requests for live system data
    r"|\b(show|list|get|check|pull|view)\b.{0,40}\b(log|stats?|status|health|container|queue|request|download|backup|alert|metric)\b"
    # Explicit web-search actions
    r"|\b(search|find|look\s+up)\b.{0,40}\b(web|online|house|home|listing|property|zillow|redfin|real[\s-]?estate|news|current\s+price|weather)\b"
    # "Search <domain>" or "search for <topic>"
    r"|\b(search|find|look\s+up)\b.{0,40}\w+\.(com|org|net|io|edu)\b"
    r"|\b(search|find|look\s+up|locate)\b.{0,20}\b(for|about|in|on)\b"
    # NAS / folder / file browsing
    r"|\b(browse|list|show|find|locate|look)\b.{0,40}\b(folders?|director(?:y|ies)|audiobooks?|ebooks?|files?|nas|shares?)\b"
    r"|\b(what|which|do\s+we\s+have)\b.{0,30}\b(folders?|audiobooks?|books?|files?)\b"
    # "check other sites", "try other sources"
    r"|\b(check|try)\b.{0,20}\b(other|more|different)\b.{0,20}\b(site|source|page|place|link)\b"
    # Weather
    r"|\b(weather|forecast|temperature|rain|snow|sunny|humidity|wind\s+speed)\b"
    # Live-data questions
    r"|\bis\s+(the\s+)?(server|plex|sonarr|radarr|nas|docker)\s+(up|running|online|working|down)\b"
    r"|\bwhat'?s?\s+(?:the\s+)?(?:current|latest|running)\b.{0,50}\b(status|usage|queue|activity)\b"
    # Approvals, sends, creates, schedules
    r"|\b(approve|deny)\b.{0,20}\b(request|id)\b"
    r"|\bsend\b.{0,20}\b(email|mail)\b"
    r"|\bcreate\b.{0,30}\b(task|event|entity|connection|calendar)\b"
    r"|\bschedule\b.{0,30}\b(task|report|research|job|recurring|weekly|daily|monthly)\b"
    # Calendar and inbox retrieval in plain English
    r"|\b(what'?s\s+on|what\s+is\s+on|show|list|check|review)\b.{0,40}\b(calendar|schedule|agenda|events?)\b"
    r"|\b(read|check|search|scan|look\s+through|find)\b.{0,40}\b(inbox|email|emails|mail)\b"
    # Recaps / summaries over live Discord history
    r"|\b(recap|summari[sz]e|summary|wrap[\s-]?up)\b.{0,40}\b(channel|thread|discord|conversation|meeting|week)\b"
    # Sports schedules / watch guides / current game listings
    r"|\b(game|games|matchup|matchups|watch|stream(?:ing)?|tv\s+schedule)\b.{0,60}\b(upcoming|this\s+week|today|tomorrow|weekend|espn|ncaa|lacrosse|where\s+to\s+watch)\b"
    # Weekly report / box office asks
    r"|\b(report|recap|summary)\b.{0,60}\b(box\s+office|new\s+releases?|financials?|weekend\s+gross|domestic|worldwide)\b"
    r"|\b(box\s+office|new\s+releases?)\b.{0,60}\b(last\s+week|past\s+week|last\s+\d+\s+days|table|emoji)\b"
    # Diagnostics / jobs
    r"|\brun\b.{0,20}\b(speed\s+test|status\s+report|ping|backup|diagnostic)\b"
    r"|\bping\s+[\w.]+"
    r"|\b(anything|what'?s|what\s+is)\b.{0,20}\b(broken|wrong|down)\b.{0,40}\b(stack|media|service|services|plex|sonarr|radarr|lidarr|prowlarr|nas)\b"
    # URLs always need browse_url
    r"|https?://"
    r"|\b\w+\.(com|org|net|io|edu)\b",
    re.IGNORECASE,
)

# Tier 2 — Well-known domains where Gemma consistently fabricates answers.
_GEMMA_WEAK_DOMAINS = re.compile(
    r"\b(zillow|redfin|trulia|narberth|upper\s+darby|maton|tailscale|tautulli"
    r"|overseerr|prowlarr|sabnzbd|synology|hyper\s+backup|ontology"
    r"|audiobooks?|ebooks?|nas\b.{0,20}(folder|share|storage)|filestation)\b",
    re.IGNORECASE,
)


def _needs_tools(message: str) -> bool:
    """Return True if the query requires live tool execution and should bypass Gemma."""
    return bool(_LIVE_ACTION_PATTERN.search(message) or _GEMMA_WEAK_DOMAINS.search(message))


# ---------------------------------------------------------------------------
# Response validation patterns
# ---------------------------------------------------------------------------

_VAGUE_RESPONSE_RE = re.compile(
    r"i'?m\s+not\s+sure"
    r"|\bi\s+don'?t\s+have\s+specific\b"
    r"|\bi\s+couldn'?t\s+find\b"
    r"|\bi\s+don'?t\s+have\s+access\s+to\s+real[\s-]?time\b"
    r"|\bmy\s+training\s+data\b"
    r"|\bmy\s+knowledge\s+cutoff\b"
    r"|\bi\s+recommend\s+checking\b"
    r"|\byou\s+might\s+want\s+to\s+search\b",
    re.IGNORECASE,
)

_FACTUAL_QUESTION_RE = re.compile(
    r"^(who|what|when|where|how|is|are|was|were|did|does|do|can|could|will|has|have)\b",
    re.IGNORECASE,
)

# Compiled patterns that signal Gemma is pretending to call tools it doesn't have.
_GEMMA_HALLUCINATION_RE = re.compile(
    r"(i'?m?\s+)?(now\s+)?(searching|browsing|checking|fetching|looking\s+up)\b"
    r"|\b(let\s+me\s+)?(search|check|look\s+that\s+up|fetch)\s+(that|the|for)\b"
    r"|(checking|querying)\s+(zillow|redfin|the\s+server|docker|container|plex)\b"
    r"|\b(i\s+)?(don'?t|cannot|can'?t)\s+(access|browse|check|reach)\s+(the\s+)?(internet|web|real[\s-]?time|live|current)\b"
    r"|\b(as\s+an?\s+ai|as\s+a\s+language\s+model)\b.{0,80}\b(cannot|don'?t|no\s+access)\b"
    r"|\bi\s+don'?t\s+have\s+(real[\s-]?time|access\s+to|live)\b"
    r"|(would\s+need\s+to\s+|i\s+could\s+)?(search|check|query)\s+(this|that|it)\s+for\s+you\b"
    # Promises to do something without actually doing it
    r"|\b(let\s+me\s+)(start|begin|check|search|look|find|locate)\b"
    r"|\bone\s+moment\b"
    r"|\bi'?ll\s+(search|check|look|find|locate|browse|start)\b",
    re.IGNORECASE,
)

_REMOTE_PROVIDER_PLACEHOLDER_RE = re.compile(
    r"\bone\s+moment\b"
    r"|\bi'?ll\s+(?:retrieve|check|look|search|find|locate|browse|pull)\b"
    r"|\blet\s+me\s+(?:retrieve|check|look(?:\s+that)?\s+up|search|find|locate|browse|pull)\b",
    re.IGNORECASE,
)


def _gemma_response_seems_valid(reply: str) -> bool:
    """Return True if the Gemma response is genuine and not a tool-use hallucination.

    Delegates to the centralized answer policy.
    """
    from answer_policy import response_seems_valid
    return response_seems_valid(reply, provider="gemma")


def _provider_response_seems_valid(reply: str, *, provider: str) -> bool:
    """Validate fallback responses with provider-aware guardrails.

    Delegates to the centralized answer policy.
    """
    from answer_policy import response_seems_valid
    return response_seems_valid(reply, provider=provider)


# ---------------------------------------------------------------------------
# Self-evaluation / reflection (Phase 7)
# ---------------------------------------------------------------------------


async def _reflect_on_response(
    text: str,
    user_message: str,
    rounds: int,
) -> str:
    """Self-evaluate a response and refine if issues are found.

    Only runs for complex responses (tool calls involved). Routes the reflection
    call through the best available provider (Copilot when available, Gemini
    otherwise) rather than always consuming Gemini quota for meta-tasks.

    Returns the original or improved text.
    """
    text = text or ""
    if not cfg.reflection_enabled:
        return text
    # Skip reflection for tool-based responses — tool results are factual.
    if rounds >= 1:
        return text
    # Don't reflect on very short or error responses
    if len(text) < 50 or text.startswith("❌") or text.startswith("⚠️"):
        return text

    reflection_prompt = (
        "You are a quality reviewer. Examine this AI response to a user query.\n"
        "IMPORTANT: This AI assistant HAS tool access and CAN execute actions like "
        "searching folders, checking services, and browsing the web. If the response "
        "reports results from a tool call (e.g. 'no items found', 'search completed'), "
        "do NOT change it to say 'I cannot access' or 'I don't have the ability to'. "
        "The tool was already executed and the result is accurate.\n\n"
        f"USER QUERY: {user_message}\n\n"
        f"AI RESPONSE:\n{text}\n\n"
        "Check for:\n"
        "1. Factual errors or contradictions\n"
        "2. Missing important information the user asked for\n"
        "3. Misinterpreted data or tool results\n"
        "4. Confusing or unclear explanations\n\n"
        "If the response is good, reply with EXACTLY: LGTM\n"
        "If you find issues, reply with a corrected version of the response "
        "(just the improved response, no meta-commentary)."
    )

    try:
        from model_router import COPILOT_PROXY_ENABLED, chat_openai
        from model_routing_policy import select_reflection_route

        route = select_reflection_route(copilot_available=COPILOT_PROXY_ENABLED)
        log.debug("Reflection route: %s (%s)", route.provider, route.reason)

        reflection: str | None = None

        if route.provider == "copilot":
            reflection = await chat_openai(
                reflection_prompt,
                history=[],
                system_prompt="",
                temperature=0.2,
                max_tokens=MAX_TOKENS,
            )

        if reflection is None:
            # Fall back to direct Gemini call
            reflection_config = genai.types.GenerateContentConfig(
                max_output_tokens=MAX_TOKENS,
                temperature=0.2,
            )
            loop = asyncio.get_running_loop()
            _rate_limiter.record()
            response = await loop.run_in_executor(
                None,
                lambda: _client.models.generate_content(
                    model=MODEL_NAME, contents=reflection_prompt,
                    config=reflection_config,
                ),
            )
            await _record_usage(response)
            reflection = response.text.strip()

        if not reflection:
            return text

        if reflection.upper() == "LGTM" or reflection.upper().startswith("LGTM"):
            log.debug("Reflection: response passed self-evaluation")
            return text

        # Safeguard: if reflection is much shorter, it over-summarized — keep original
        if len(reflection) < len(text) * 0.5 and len(text) > 100:
            log.info("Reflection: discarded (%.0f%% shorter than original — likely over-summarized)",
                     (1 - len(reflection) / len(text)) * 100)
            return text
        log.info("Reflection: response was refined (original %d chars → refined %d chars)",
                 len(text), len(reflection))
        return reflection

    except Exception as e:
        log.debug("Reflection failed (non-fatal): %s", e)
        return text
