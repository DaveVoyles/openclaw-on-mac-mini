"""Lightweight tool routing for plain-English `/ask` requests."""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._-]*")
_DAY_WINDOW_RE = re.compile(r"\b(?:last|past|next)\s+(\d{1,2})\s+days?\b")
_WEEK_WINDOW_RE = re.compile(r"\b(?:last|past|next)\s+(\d{1,2})\s+weeks?\b")
_PACK_DIRECTIVE_RE = re.compile(r"\buse\s*:\s*([a-z0-9_-]+)\b", re.IGNORECASE)
_PACK_PLAIN_RE = re.compile(r"\buse\s+(finance|sports|wwe|gaming)\s*(?:pack|persona|tools?)?\b", re.IGNORECASE)
_REQUESTED_ITEMS_PREFIX_RE = re.compile(
    r"\b(?:top|first|at\s+least|minimum(?:\s+of)?|up\s+to|bring(?:\s+in)?|include|cover|list|give(?:\s+me)?|show(?:\s+me)?|get(?:\s+me)?|provide)\s+(\d{1,2})\s+(?:[a-z][a-z0-9'/-]*\s+){0,2}(stories?|headlines?|games?|items?|results?)\b",
    re.IGNORECASE,
)
_REQUESTED_ITEMS_BARE_RE = re.compile(
    r"^\s*(\d{1,2})\s+(?:[a-z][a-z0-9'/-]*\s+){0,2}(stories?|headlines?|games?|items?|results?)\b",
    re.IGNORECASE,
)

_ALWAYS_AVAILABLE = {
    "search_web",
    "browse_url",
    "remember_fact",
    "recall_fact",
}

_INTENT_HINTS: dict[str, tuple[str, ...]] = {
    "generate_channel_recap_report": (
        "weekly recap",
        "channel recap",
        "thread recap",
        "summarize this channel",
        "summarize this thread",
        "meeting recap",
        "wrap up the week",
    ),
    "generate_sports_watch_report": (
        "where to watch",
        "watch guide",
        "upcoming games",
        "games this week",
        "weekend recap",
        "sports recap",
        "game results",
        "sports schedule",
        "streaming schedule",
        "college lacrosse",
        "ncaa",
        "espn",
    ),
    "generate_sports_scores_report": (
        "final score",
        "score of the game",
        "what was the score",
        "did the",
        "who won",
        "game score",
        "game recap",
        "last night score",
        "yesterday score",
        "match result",
        "box score",
    ),
    "generate_entertainment_report": (
        "movies in theaters",
        "what's on netflix",
        "new on netflix",
        "new on hulu",
        "new on hbo",
        "new on disney",
        "streaming this week",
        "what to watch",
        "rotten tomatoes",
        "now streaming",
        "now playing",
        "worth watching",
        "new releases",
    ),
    "generate_news_report": (
        "what's in the news",
        "latest news",
        "news today",
        "current events",
        "headlines",
        "breaking news",
        "what happened today",
        "what's going on",
        "top stories",
    ),
    "generate_weather_report": (
        "what's the weather",
        "weather today",
        "weather forecast",
        "will it rain",
        "temperature today",
        "current conditions",
        "weather in",
        "forecast for",
        "chance of rain",
        "weather this week",
    ),
    "generate_finance_report": (
        "stock price",
        "market today",
        "stock market",
        "how is the market",
        "crypto price",
        "bitcoin price",
        "s&p 500",
        "dow jones",
        "nasdaq today",
        "market update",
        "earnings today",
        "trade market",
    ),
    "create_status_report": (
        "how's everything",
        "status report",
        "anything broken",
        "check the stack",
    ),
    "get_plex_activity": (
        "what's playing on plex",
        "who is watching",
        "who's watching",
        "now playing",
    ),
    "get_upcoming_events": (
        "what's on my calendar",
        "what is on my calendar",
        "what do i have tomorrow",
        "upcoming events",
        "calendar this week",
    ),
    "create_calendar_event": (
        "add this to my calendar",
        "put this on my calendar",
        "schedule this meeting",
        "create a calendar event",
    ),
    "search_emails": (
        "search my inbox",
        "find that email",
        "look through my mail",
    ),
    "read_inbox": (
        "check my inbox",
        "read my inbox",
        "recent emails",
    ),
    "generate_box_office_report": (
        "box office",
        "new releases",
        "film releases",
        "movie financials",
        "weekend gross",
        "domestic total",
        "worldwide total",
    ),
}

_WORKFLOW_BUNDLES: tuple[dict[str, Any], ...] = (
    {
        "name": "media-health",
        "phrases": (
            "anything broken",
            "how's the stack",
            "how is the stack",
            "check the media stack",
            "media stack health",
            "system health",
        ),
        "token_groups": (
            {"broken", "health", "healthy", "status", "wrong", "down", "issues", "issue"},
            {"stack", "media", "service", "services", "plex", "sonarr", "radarr", "lidarr", "prowlarr"},
        ),
        "tools": (
            "create_status_report",
            "check_arr_health",
            "check_download_clients",
            "check_plex_status",
        ),
    },
    {
        "name": "plex-activity",
        "phrases": (
            "what's playing on plex",
            "what is playing on plex",
            "who's watching plex",
            "who is watching plex",
            "plex activity",
            "now playing",
        ),
        "token_groups": (
            {"plex"},
            {"watching", "playing", "activity", "stream", "streams", "now"},
        ),
        "tools": ("get_plex_activity", "check_plex_status"),
    },
    {
        "name": "calendar",
        "phrases": (
            "what's on my calendar",
            "what is on my calendar",
            "what's on my schedule",
            "add this to my calendar",
            "put this on my calendar",
            "schedule a meeting",
            "schedule an event",
        ),
        "token_groups": (
            {"calendar", "schedule", "agenda", "meeting", "event", "events"},
            {"today", "tomorrow", "week", "weekend", "tonight", "monday", "tuesday", "wednesday",
             "thursday", "friday", "saturday", "sunday", "add", "create", "schedule"},
        ),
        "tools": ("get_todays_events", "get_upcoming_events", "create_calendar_event"),
    },
    {
        "name": "email",
        "phrases": (
            "search my inbox",
            "check my inbox",
            "read my inbox",
            "send an email",
            "email this to",
        ),
        "token_groups": (
            {"email", "emails", "inbox", "mail"},
            {"send", "search", "check", "read", "find", "draft"},
        ),
        "tools": ("read_inbox", "search_emails", "send_email"),
    },
    {
        "name": "weekly-market-report",
        "phrases": (
            "box office",
            "new releases",
            "movie financials",
            "film financials",
            "weekend gross",
            "domestic total",
            "worldwide total",
        ),
        "token_groups": (
            {"box", "office", "release", "releases", "movie", "film"},
            {"gross", "financials", "financial", "revenue", "weekend", "domestic", "worldwide"},
        ),
        "tools": ("generate_box_office_report", "search_web", "browse_url"),
    },
)

_SERVICE_NAMES = (
    "plex",
    "sonarr",
    "radarr",
    "lidarr",
    "prowlarr",
    "sabnzbd",
    "qbittorrent",
    "overseerr",
    "tautulli",
    "bazarr",
)

_SPORT_TERMS = ("lacrosse", "basketball", "baseball", "football", "soccer", "hockey")
_LEAGUE_TERMS = ("ncaa division 1", "division 1", "ncaa", "nba", "wnba", "nfl", "mlb", "nhl", "mls")

_PACK_PROFILES: dict[str, dict[str, Any]] = {
    "finance": {
        "persona": "finance-analyst",
        "aliases": ("markets", "stocks", "investing"),
        "terms": (
            "finance", "financial", "market", "markets", "stock", "stocks", "invest",
            "investing", "portfolio", "etf", "earnings", "economic", "economy", "indices",
        ),
    },
    "sports": {
        "persona": "sports-analyst",
        "aliases": ("sportsbook",),
        "terms": (
            "sports", "game", "games", "matchup", "watch", "schedule", "scores", "team",
            "teams", "league", "espn", "ncaa", "nba", "nfl", "mlb", "nhl", "mls", "lacrosse",
        ),
    },
    "wwe": {
        "persona": "wwe-reporter",
        "aliases": ("wrestling", "pro-wrestling"),
        "terms": (
            "wwe", "wrestling", "raw", "smackdown", "nxt", "wrestlemania",
            "pay-per-view", "ppv", "premium live event", "sports entertainment",
        ),
    },
    "gaming": {
        "persona": "gaming-scout",
        "aliases": ("videogames", "video-games"),
        "terms": (
            "gaming", "game", "games", "steam", "xbox", "playstation", "nintendo",
            "pc", "esports", "patch", "release notes", "multiplayer", "twitch",
        ),
    },
}

_PACK_ALIAS_LOOKUP: dict[str, str] = {}
for _pack_name, _profile in _PACK_PROFILES.items():
    _PACK_ALIAS_LOOKUP[_pack_name] = _pack_name
    for _alias in _profile.get("aliases", ()):
        _PACK_ALIAS_LOOKUP[str(_alias).lower()] = _pack_name


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall((text or "").lower()) if len(tok) > 1}


def _iter_metadata_values(declaration: dict[str, Any]) -> list[str]:
    values = [
        str(declaration.get("name", "")).replace("_", " "),
        str(declaration.get("description", "")),
        str(declaration.get("category", "")),
    ]
    for key in ("aliases", "examples", "keywords", "domains", "packs", "personas"):
        raw = declaration.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(str(item) for item in raw)
    return values


def _declaration_domains(declaration: dict[str, Any]) -> set[str]:
    domains: set[str] = set()
    raw_values: list[str] = []
    for key in ("domains", "packs", "personas"):
        raw = declaration.get(key)
        if isinstance(raw, str):
            raw_values.append(raw.lower())
        elif isinstance(raw, list):
            raw_values.extend(str(item).lower() for item in raw)
    if any(value in {"sports", "sports-analyst"} for value in raw_values):
        domains.add("sports")
    if any(value in {"wwe", "wwe-reporter"} for value in raw_values):
        domains.add("wwe")
    if any(value in {"gaming", "gaming-scout"} for value in raw_values):
        domains.add("gaming")

    metadata_text = " ".join(_iter_metadata_values(declaration)).lower()
    for domain in ("sports", "wwe", "gaming"):
        terms = tuple(str(item).lower() for item in _PACK_PROFILES.get(domain, {}).get("terms", ()))
        hits = sum(1 for term in terms if term and term in metadata_text)
        if domain == "wwe" and hits >= 1:
            domains.add("wwe")
        elif domain == "gaming" and hits >= 2:
            domains.add("gaming")
        elif domain == "sports" and hits >= 2:
            domains.add("sports")
    return domains


def _infer_message_domains(message_lower: str, message_tokens: set[str]) -> set[str]:
    domains: set[str] = set()
    for domain in ("sports", "wwe", "gaming"):
        terms = tuple(str(item).lower() for item in _PACK_PROFILES.get(domain, {}).get("terms", ()))
        token_hits = sum(1 for term in terms if term in message_tokens)
        phrase_hits = sum(1 for term in terms if " " in term and term in message_lower)
        if domain == "wwe" and (token_hits + phrase_hits) >= 1:
            domains.add("wwe")
        elif domain == "gaming" and (token_hits + phrase_hits) >= 2:
            domains.add("gaming")
        elif domain == "sports" and (token_hits + phrase_hits) >= 2:
            domains.add("sports")
    return domains


def _extract_pack_directive(message: str) -> tuple[str | None, str | None, str]:
    """Extract `use:<pack>` or plain-English `use <pack> pack` directives."""
    pack_name: str | None = None

    directive_match = _PACK_DIRECTIVE_RE.search(message)
    plain_match = _PACK_PLAIN_RE.search(message) if directive_match is None else None

    if directive_match:
        raw = str(directive_match.group(1)).lower().strip()
        pack_name = _PACK_ALIAS_LOOKUP.get(raw, raw if raw in _PACK_PROFILES else None)
    elif plain_match:
        raw = str(plain_match.group(1)).lower().strip()
        pack_name = _PACK_ALIAS_LOOKUP.get(raw, raw if raw in _PACK_PROFILES else None)

    cleaned = _PACK_DIRECTIVE_RE.sub(" ", message)
    cleaned = _PACK_PLAIN_RE.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())

    persona = None
    if pack_name:
        persona = str(_PACK_PROFILES.get(pack_name, {}).get("persona") or "")
    return pack_name, persona or None, cleaned


def _declaration_matches_pack(declaration: dict[str, Any], pack_name: str) -> bool:
    if declaration.get("always_available") or str(declaration.get("name", "")) in _ALWAYS_AVAILABLE:
        return True

    profile = _PACK_PROFILES.get(pack_name)
    if not profile:
        return True

    metadata_text = " ".join(_iter_metadata_values(declaration)).lower()
    metadata_tokens = _tokenize(metadata_text)
    explicit_values: set[str] = set()
    for key in ("domains", "packs", "personas"):
        raw = declaration.get(key)
        if isinstance(raw, str):
            explicit_values.add(raw.lower())
        elif isinstance(raw, list):
            explicit_values.update(str(item).lower() for item in raw)

    persona_name = str(profile.get("persona", "")).lower()
    if pack_name in explicit_values or persona_name in explicit_values:
        return True

    pack_terms = {str(item).lower() for item in profile.get("terms", ())}
    if metadata_tokens & pack_terms:
        return True
    return False


def _score_declaration(message_lower: str, message_tokens: set[str], declaration: dict[str, Any]) -> int:
    name = str(declaration.get("name", ""))
    metadata_values = _iter_metadata_values(declaration)
    metadata_text = " ".join(metadata_values).lower()
    metadata_tokens = _tokenize(metadata_text)

    score = 0
    overlap = message_tokens & metadata_tokens
    score += len(overlap) * 2

    normalized_name = name.replace("_", " ").lower()
    if normalized_name and normalized_name in message_lower:
        score += 10

    for phrase in declaration.get("aliases", []) or []:
        phrase_text = str(phrase).strip().lower()
        if phrase_text and phrase_text in message_lower:
            score += 8 if " " in phrase_text else 4

    for example in declaration.get("examples", []) or []:
        example_text = str(example).strip().lower()
        if example_text and example_text in message_lower:
            score += 6

    for phrase in _INTENT_HINTS.get(name, ()):
        if phrase in message_lower:
            score += 8

    if declaration.get("always_available"):
        score += 1

    return score


def _matching_workflow_bundles(message_lower: str, message_tokens: set[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for bundle in _WORKFLOW_BUNDLES:
        if any(phrase in message_lower for phrase in bundle.get("phrases", ())):
            matches.append(bundle)
            continue

        token_groups = bundle.get("token_groups", ())
        if token_groups and all(message_tokens & set(group) for group in token_groups):
            matches.append(bundle)

    return matches


def _extract_requested_item_count(message: str) -> int | None:
    """Infer explicit item-count ask from plain-English prompts."""
    text = message or ""
    match = _REQUESTED_ITEMS_PREFIX_RE.search(text)
    if match:
        return max(1, min(int(match.group(1)), 25))

    bare_match = _REQUESTED_ITEMS_BARE_RE.search(text)
    if bare_match:
        return max(1, min(int(bare_match.group(1)), 25))

    return None


def _extract_request_hints(message: str, message_lower: str, message_tokens: set[str]) -> dict[str, Any]:
    hints: dict[str, Any] = {}

    services = [name for name in _SERVICE_NAMES if name in message_tokens]
    if services:
        hints["services"] = services

    for phrase, days in (
        ("today", 1),
        ("tomorrow", 2),
        ("this weekend", 3),
        ("last weekend", 3),
        ("weekend recap", 3),
        ("this week", 7),
        ("next week", 7),
        ("last week", 7),
    ):
        if phrase in message_lower:
            hints["days"] = days
            hints["timeframe"] = phrase
            break

    explicit_days = _DAY_WINDOW_RE.search(message_lower)
    if explicit_days:
        hints["days"] = max(1, min(int(explicit_days.group(1)), 30))
        hints["timeframe"] = explicit_days.group(0)
    explicit_weeks = _WEEK_WINDOW_RE.search(message_lower)
    if explicit_weeks:
        hints["days"] = max(1, min(int(explicit_weeks.group(1)) * 7, 30))
        hints["timeframe"] = explicit_weeks.group(0)

    requested_item_count = _extract_requested_item_count(message)
    if isinstance(requested_item_count, int):
        hints["requested_item_count"] = requested_item_count

    for sport in _SPORT_TERMS:
        if sport in message_tokens:
            hints["sport"] = sport
            break

    for league in _LEAGUE_TERMS:
        if league in message_lower:
            hints["league"] = league.title()
            break

    team_match = re.search(r"\bdoes\s+([A-Z][A-Za-z0-9&.\- ]{1,30}?)\s+(?:have|play|face)\b", message)
    if not team_match:
        team_match = re.search(
            r"\bfor\s+([A-Z][A-Za-z0-9&.\- ]{1,30}?)(?:\s+in\b|\s+this\b|\s+next\b|$)",
            message,
        )
    if team_match:
        hints["team"] = " ".join(team_match.group(1).split())

    if "box office" in message_lower:
        hints["report_topic"] = "box-office"
        hints["retrieval_profile"] = "news"
    elif any(term in message_lower for term in ("gaming", "esports", "videogame", "video game", "steam", "xbox", "playstation", "nintendo")):
        hints["report_topic"] = "gaming"
        hints["retrieval_profile"] = "gaming"
    elif any(term in message_tokens for term in _SPORT_TERMS) or "sports" in message_lower:
        hints["report_topic"] = "sports"
        hints["retrieval_profile"] = "sports"
    elif any(term in message_lower for term in ("news", "headline", "breaking", "latest update")):
        hints["report_topic"] = "news"
        hints["retrieval_profile"] = "news"
    elif any(term in message_lower for term in ("incident", "outage", "deploy", "latency", "engineering", "ops")):
        hints["report_topic"] = "engineering"
        hints["retrieval_profile"] = "engineering"
    elif "recap" in message_lower:
        hints["report_topic"] = "recap"
        hints["retrieval_profile"] = "general"

    if "table" in message_lower or "markdown" in message_lower:
        hints["output_style"] = "table"
    elif "bullet" in message_lower:
        hints["output_style"] = "bullet"

    if "emoji" in message_lower:
        if any(term in message_lower for term in ("more emoji", "rich emoji", "use emojis")):
            hints["emoji_level"] = "rich"
        elif any(term in message_lower for term in ("no emoji", "without emoji")):
            hints["emoji_level"] = "none"
        else:
            hints["emoji_level"] = "light"

    if any(term in message_lower for term in ("quick", "brief", "short")):
        hints["detail_level"] = "brief"
    elif any(term in message_lower for term in ("detailed", "deep", "comprehensive")):
        hints["detail_level"] = "detailed"

    return hints


def route_tool_declarations(
    message: str,
    declarations: list[dict[str, Any]],
    *,
    max_tools: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the best-fit tool declarations for a plain-English request.

    Falls back to the full declaration set when confidence is low so existing
    behavior remains intact.
    """
    if not declarations:
        return [], {"strategy": "empty", "selected": [], "top_score": 0}

    pack_name, persona_name, cleaned_message = _extract_pack_directive(message or "")
    route_message = cleaned_message or (message or "")
    message_lower = route_message.lower()
    if not message_lower.strip():
        return declarations, {"strategy": "fallback-full", "selected": [], "top_score": 0}

    candidate_declarations = declarations
    if pack_name:
        filtered_declarations = [
            declaration
            for declaration in declarations
            if _declaration_matches_pack(declaration, pack_name)
        ]
        if filtered_declarations:
            candidate_declarations = filtered_declarations

    message_tokens = _tokenize(message_lower)
    message_domains = _infer_message_domains(message_lower, message_tokens)
    matched_bundles = _matching_workflow_bundles(message_lower, message_tokens)
    request_hints = _extract_request_hints(route_message, message_lower, message_tokens)
    if pack_name:
        request_hints["pack"] = pack_name
    if persona_name:
        request_hints["persona"] = persona_name
    bundled_tool_names = {
        str(tool_name)
        for bundle in matched_bundles
        for tool_name in bundle.get("tools", ())
    }
    scored: list[tuple[int, str, dict[str, Any]]] = []
    always_on: list[dict[str, Any]] = []
    guard_suppressed: list[str] = []
    guard_domains: list[str] = []
    guarded_domains = {"sports", "wwe"} - message_domains if not pack_name else set()

    for declaration in candidate_declarations:
        name = str(declaration.get("name", ""))
        declaration_domains = _declaration_domains(declaration)
        if guarded_domains and (declaration_domains & guarded_domains):
            if not declaration.get("always_available") and name not in _ALWAYS_AVAILABLE:
                guard_suppressed.append(name)
                continue
        if declaration.get("always_available") or name in _ALWAYS_AVAILABLE:
            always_on.append(declaration)
        score = _score_declaration(message_lower, message_tokens, declaration)
        if name in bundled_tool_names:
            score += 10
        scored.append((score, name, declaration))

    if guard_suppressed:
        guard_domains = sorted(guarded_domains)
        request_hints["guarded_domains"] = guard_domains
        request_hints["guard_suppressed"] = guard_suppressed[:8]

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0] if scored else 0

    if top_score < 4:
        if pack_name and candidate_declarations is not declarations:
            return candidate_declarations, {
                "strategy": "pack-filter",
                "selected": [name for _, name, _ in scored[: min(12, len(scored))]],
                "top_score": top_score,
                "bundles": [str(bundle.get("name", "")) for bundle in matched_bundles],
                "hints": request_hints,
                "pack": pack_name,
                "persona": persona_name,
                "guard_domains": guard_domains,
                "guard_suppressed": guard_suppressed,
            }
        fallback_declarations = declarations
        fallback_strategy = "fallback-full"
        if guard_suppressed:
            fallback_declarations = [
                declaration
                for declaration in declarations
                if str(declaration.get("name", "")) not in set(guard_suppressed)
                or declaration.get("always_available")
                or str(declaration.get("name", "")) in _ALWAYS_AVAILABLE
            ]
            fallback_strategy = "guarded-fallback"
        return fallback_declarations, {
            "strategy": fallback_strategy,
            "selected": [name for _, name, _ in scored[: min(12, len(scored))]],
            "top_score": top_score,
            "bundles": [str(bundle.get("name", "")) for bundle in matched_bundles],
            "hints": request_hints,
            "pack": pack_name,
            "persona": persona_name,
            "guard_domains": guard_domains,
            "guard_suppressed": guard_suppressed,
        }

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for declaration in always_on:
        name = str(declaration.get("name", ""))
        if name and name not in seen:
            selected.append(declaration)
            seen.add(name)

    for score, name, declaration in scored:
        if score <= 0 or not name or name in seen:
            continue
        selected.append(declaration)
        seen.add(name)
        if len(selected) >= max_tools:
            break

    log.debug(
        "Tool router selected %d/%d tools for %r (top_score=%d): %s",
        len(selected),
        len(declarations),
        message[:80],
        top_score,
        ", ".join(str(d.get("name", "")) for d in selected),
    )
    return selected, {
        "strategy": "shortlist",
        "selected": [str(d.get("name", "")) for d in selected],
        "top_score": top_score,
        "bundles": [str(bundle.get("name", "")) for bundle in matched_bundles],
        "hints": request_hints,
        "pack": pack_name,
        "persona": persona_name,
        "guard_domains": guard_domains,
        "guard_suppressed": guard_suppressed,
    }
