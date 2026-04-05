"""Gazetteer-style entity normalization and lightweight disambiguation helpers."""

from __future__ import annotations

import re
from typing import Any

_DISAMBIGUATION_APPLY_THRESHOLD = 0.7

_ENTITY_GAZETTEER: dict[str, dict[str, tuple[str, ...]]] = {
    "services": {
        "plex": ("plex",),
        "sonarr": ("sonarr",),
        "radarr": ("radarr",),
        "lidarr": ("lidarr",),
        "prowlarr": ("prowlarr",),
        "sabnzbd": ("sabnzbd", "sab", "sab nzbd"),
        "qbittorrent": ("qbittorrent", "qbit", "q bittorrent"),
        "overseerr": ("overseerr",),
        "tautulli": ("tautulli",),
        "bazarr": ("bazarr",),
    },
    "leagues": {
        "NCAA Division I": ("ncaa division 1", "ncaa division i", "division 1", "division i", "d1"),
        "NCAA": ("ncaa",),
        "NBA": ("nba",),
        "WNBA": ("wnba",),
        "NFL": ("nfl",),
        "MLB": ("mlb",),
        "NHL": ("nhl",),
        "MLS": ("mls",),
    },
    "wwe": {
        "WWE RAW": ("raw", "wwe raw", "monday night raw"),
        "WWE SmackDown": ("smackdown", "smack down", "wwe smackdown"),
        "WWE NXT": ("nxt", "wwe nxt"),
        "WrestleMania": ("wrestlemania", "wrestle mania"),
        "Royal Rumble": ("royal rumble",),
        "SummerSlam": ("summerslam", "summer slam"),
        "Survivor Series": ("survivor series",),
        "Money in the Bank": ("money in the bank",),
        "Elimination Chamber": ("elimination chamber",),
        "Backlash": ("backlash", "wwe backlash"),
    },
    "platforms": {
        "PlayStation": ("playstation", "ps4", "ps5", "psvr"),
        "Xbox": ("xbox", "xbox one", "xbox series x", "xbox series s"),
        "Nintendo Switch": ("nintendo", "nintendo switch", "switch"),
        "Steam": ("steam",),
        "PC": ("pc", "windows pc", "desktop"),
    },
}

_REFERENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    "channel": re.compile(r"\b(?:this|that)\s+(?:channel|thread)\b"),
    "service": re.compile(r"\b(?:this|that)\s+service\b"),
    "league": re.compile(r"\b(?:this|that)\s+league\b"),
    "platform": re.compile(r"\b(?:this|that)\s+platform\b"),
    "show": re.compile(r"\b(?:this|that)\s+(?:show|event)\b"),
}


def _phrase_in_text(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase)
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def extract_entities(message_lower: str) -> dict[str, list[str]]:
    """Return normalized entities detected in text."""
    entities: dict[str, list[str]] = {}
    for category, canonical_map in _ENTITY_GAZETTEER.items():
        found: list[str] = []
        for canonical, aliases in canonical_map.items():
            checks = (canonical.lower(), *(alias.lower() for alias in aliases))
            if any(_phrase_in_text(message_lower, candidate) for candidate in checks):
                found.append(canonical)
        if found:
            entities[category] = _dedupe(found)
    return entities


def enrich_route_text_and_hints(
    message_lower: str,
    hints: dict[str, Any],
    *,
    matched_bundle_names: set[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Merge normalized entities/disambiguation into hints and routing text."""
    resolved_hints = dict(hints)
    entities = extract_entities(message_lower)
    if entities:
        resolved_hints["entities"] = entities

    service_entities = entities.get("services", [])
    if service_entities and not resolved_hints.get("services"):
        resolved_hints["services"] = service_entities

    league_entities = entities.get("leagues", [])
    if league_entities:
        resolved_hints["league"] = league_entities[0]

    platform_entities = entities.get("platforms", [])
    if platform_entities:
        resolved_hints["platforms"] = platform_entities

    wwe_entities = entities.get("wwe", [])
    if wwe_entities:
        resolved_hints["wwe_entities"] = wwe_entities

    disambiguated: dict[str, str] = {}
    disambiguation_confidence = 0.0
    unresolved_refs: list[str] = []
    bundle_names = matched_bundle_names or set()

    if _REFERENCE_PATTERNS["channel"].search(message_lower):
        disambiguated["channel"] = "current"
        disambiguation_confidence = max(disambiguation_confidence, 0.9)

    if _REFERENCE_PATTERNS["service"].search(message_lower):
        if len(service_entities) == 1:
            disambiguated["service"] = service_entities[0]
            disambiguation_confidence = max(disambiguation_confidence, 0.86)
        elif "plex-activity" in bundle_names:
            disambiguated["service"] = "plex"
            disambiguation_confidence = max(disambiguation_confidence, 0.72)
        else:
            unresolved_refs.append("service")

    if _REFERENCE_PATTERNS["league"].search(message_lower):
        if len(league_entities) == 1:
            disambiguated["league"] = league_entities[0]
            disambiguation_confidence = max(disambiguation_confidence, 0.82)
        else:
            unresolved_refs.append("league")

    if _REFERENCE_PATTERNS["platform"].search(message_lower):
        if len(platform_entities) == 1:
            disambiguated["platform"] = platform_entities[0]
            disambiguation_confidence = max(disambiguation_confidence, 0.8)
        else:
            unresolved_refs.append("platform")

    if _REFERENCE_PATTERNS["show"].search(message_lower):
        if len(wwe_entities) == 1:
            disambiguated["show"] = wwe_entities[0]
            disambiguation_confidence = max(disambiguation_confidence, 0.78)
        else:
            unresolved_refs.append("show")

    if disambiguated and disambiguation_confidence >= _DISAMBIGUATION_APPLY_THRESHOLD:
        resolved_hints["disambiguated_references"] = disambiguated
        resolved_hints["disambiguation_confidence"] = round(disambiguation_confidence, 2)
    elif unresolved_refs:
        resolved_hints["unresolved_references"] = _dedupe(unresolved_refs)

    additions: list[str] = []
    for items in entities.values():
        for value in items:
            lowered = value.lower()
            if lowered not in message_lower:
                additions.append(lowered)
    enriched_lower = message_lower if not additions else f"{message_lower} {' '.join(_dedupe(additions))}"
    return enriched_lower, resolved_hints
