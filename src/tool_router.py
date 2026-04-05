"""Lightweight tool routing for plain-English `/ask` requests."""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("openclaw.tool_router")

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+._-]*")
_DAY_WINDOW_RE = re.compile(r"\b(?:last|past|next)\s+(\d{1,2})\s+days?\b")

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
        "sports schedule",
        "streaming schedule",
        "college lacrosse",
        "ncaa",
        "espn",
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


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall((text or "").lower()) if len(tok) > 1}


def _iter_metadata_values(declaration: dict[str, Any]) -> list[str]:
    values = [
        str(declaration.get("name", "")).replace("_", " "),
        str(declaration.get("description", "")),
        str(declaration.get("category", "")),
    ]
    for key in ("aliases", "examples", "keywords"):
        raw = declaration.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(str(item) for item in raw)
    return values


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


def _extract_request_hints(message: str, message_lower: str, message_tokens: set[str]) -> dict[str, Any]:
    hints: dict[str, Any] = {}

    services = [name for name in _SERVICE_NAMES if name in message_tokens]
    if services:
        hints["services"] = services

    for phrase, days in (
        ("today", 1),
        ("tomorrow", 2),
        ("this weekend", 3),
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
    elif "recap" in message_lower:
        hints["report_topic"] = "recap"
    elif "sports" in message_lower:
        hints["report_topic"] = "sports"

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

    message_lower = (message or "").lower()
    if not message_lower.strip():
        return declarations, {"strategy": "fallback-full", "selected": [], "top_score": 0}

    message_tokens = _tokenize(message_lower)
    matched_bundles = _matching_workflow_bundles(message_lower, message_tokens)
    request_hints = _extract_request_hints(message, message_lower, message_tokens)
    bundled_tool_names = {
        str(tool_name)
        for bundle in matched_bundles
        for tool_name in bundle.get("tools", ())
    }
    scored: list[tuple[int, str, dict[str, Any]]] = []
    always_on: list[dict[str, Any]] = []

    for declaration in declarations:
        name = str(declaration.get("name", ""))
        if declaration.get("always_available") or name in _ALWAYS_AVAILABLE:
            always_on.append(declaration)
        score = _score_declaration(message_lower, message_tokens, declaration)
        if name in bundled_tool_names:
            score += 10
        scored.append((score, name, declaration))

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0] if scored else 0

    if top_score < 4:
        return declarations, {
            "strategy": "fallback-full",
            "selected": [name for _, name, _ in scored[: min(12, len(scored))]],
            "top_score": top_score,
            "bundles": [str(bundle.get("name", "")) for bundle in matched_bundles],
            "hints": request_hints,
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
    }
