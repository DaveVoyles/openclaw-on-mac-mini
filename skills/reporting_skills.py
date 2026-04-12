"""Reporting skills for Discord recaps and sports watch guides."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from runtime_state import (
    get_bot,
    get_current_channel_id,
    get_current_thread_id,
    get_effective_channel_profile,
    set_anchor_state,
)
from skills import finance_skills, news_skills, sports_skills

log = logging.getLogger("openclaw.reporting_skills")


def _record_quality_metric(event: str, context: str = "reporting") -> None:
    """Best-effort metric emission for recap reliability signals."""
    try:
        from metrics_collector import get_collector

        get_collector().record_quality_event(event=event, context=context)
    except Exception:
        # Metrics must not interfere with recap generation.
        pass

_RECAP_STYLES = {
    "highlights": (
        "Use a concise weekly-recap format with: Overview, Highlights, Decisions, and Action Items."
    ),
    "action-items": (
        "Prioritize follow-ups, owners, deadlines, blockers, and anything that needs attention next."
    ),
    "table": (
        "Return a markdown table with columns Topic | Summary | Next Step, followed by a short bullet list."
    ),
}

_REPORT_STYLE_ALIASES = {
    "table": "discord-table-detailed",
    "markdown table": "discord-table-detailed",
    "detailed table": "discord-table-detailed",
    "brief table": "discord-table-brief",
    "brief": "discord-table-brief",
    "summary": "discord-recap-hybrid",
    "hybrid": "discord-recap-hybrid",
}

_EMOJI_STYLE_ALIASES = {
    "none": "none",
    "no emoji": "none",
    "without emoji": "none",
    "light": "light",
    "few emoji": "light",
    "some emoji": "light",
    "rich": "rich",
    "many emoji": "rich",
    "with emojis": "rich",
}

_SPORT_KEYWORDS = {
    "lacrosse": "lacrosse",
    "basketball": "basketball",
    "baseball": "baseball",
    "football": "football",
    "soccer": "soccer",
    "hockey": "hockey",
}

_LEAGUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:(?:ncaa|college|men'?s|women'?s)\s+)?division\s*1\b|\bd1\b", re.IGNORECASE), "NCAA Division 1"),
    (re.compile(r"\bnba\b", re.IGNORECASE), "NBA"),
    (re.compile(r"\bwnba\b", re.IGNORECASE), "WNBA"),
    (re.compile(r"\bnfl\b", re.IGNORECASE), "NFL"),
    (re.compile(r"\bmlb\b", re.IGNORECASE), "MLB"),
    (re.compile(r"\bnhl\b", re.IGNORECASE), "NHL"),
    (re.compile(r"\bmls\b", re.IGNORECASE), "MLS"),
)

_TEAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdoes\s+([A-Z][A-Za-z0-9&.\- ]{1,30}?)\s+(?:have|play|face)\b"),
    re.compile(r"\bfor\s+([A-Z][A-Za-z0-9&.\- ]{1,30}?)(?:\s+in\b|\s+this\b|\s+next\b|$)"),
    re.compile(r"\babout\s+([A-Z][A-Za-z0-9&.\- ]{1,30}?)(?:\s+in\b|\s+this\b|\s+next\b|$)"),
)

_MATCHUP_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&'().\- ]{1,30}\s+(?:vs\.?|at)\s+[A-Z][A-Za-z0-9&'().\- ]{1,30}\b"
)
_RESULT_TITLE_RE = re.compile(r"^\*\*\d+\.\s+(.*?)\*\*$")
_URL_RE = re.compile(r"https?://[^\s<>)]+", re.IGNORECASE)
_SCORE_RE = re.compile(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\b")
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:m|mn|million|b|bn|billion)?\b", re.IGNORECASE)
_STATUS_RE = re.compile(r"\b(final|postponed|cancelled|canceled|delayed|live|tbd)\b", re.IGNORECASE)
_DOMAIN_TOKEN_RE = re.compile(r"\b(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)
_PLACEHOLDER_SOURCE_VALUES = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "unknown",
    "tbd",
    "not available",
    "unverified",
}
_UNCERTAINTY_SIGNAL_RE = re.compile(
    r"\b(unverified|uncertain|tentative|provisional|may|might|appears|reportedly|likely|not yet confirmed)\b",
    re.IGNORECASE,
)
_LOW_TRUST_THRESHOLD = 55.0
_LOW_FRESHNESS_THRESHOLD = 45.0


def _normalize_style(style: str) -> str:
    value = (style or "highlights").strip().lower()
    return value if value in _RECAP_STYLES else "highlights"


def _clean_text(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _get_reporting_reference_context() -> tuple[dt.datetime, str]:
    """Return the local datetime context used for relative-date sports prompts."""
    tz_name = (os.getenv("TZ") or "America/New_York").strip()
    try:
        return dt.datetime.now(ZoneInfo(tz_name)), tz_name
    except ZoneInfoNotFoundError:
        local_now = dt.datetime.now().astimezone()
        fallback_name = getattr(local_now.tzinfo, "key", str(local_now.tzinfo or "local"))
        return local_now, fallback_name


def _detect_competition_qualifier(*parts: str) -> str:
    text = " ".join(part for part in parts if part).lower()
    if re.search(r"\bmen(?:'s|s)?\b", text):
        return "men's"
    if re.search(r"\bwomen(?:'s|s)?\b", text):
        return "women's"
    return ""


def _normalize_direct_sports_provider_answer(raw_text: str, *, provider_label: str) -> str:
    """Clean a direct provider answer so it can be returned without extra synthesis."""
    text = (raw_text or "").strip()
    if not text or text.startswith(("❌", "⚠️")):
        return ""
    if text.startswith("**Perplexity AI Answer:**"):
        text = text[len("**Perplexity AI Answer:**") :].strip()
    if re.search(
        r"\b(women'?s|division\s+ii|division\s+iii|dii\b|diii\b|non-di|non d1|naia|mcla|club)\b",
        text,
        re.IGNORECASE,
    ):
        return ""
    url_count = len(re.findall(r"https?://\S+", text))
    if len(text) < 120 and "**Sources:**" not in text and "http" not in text:
        return ""
    if _count_markdown_table_items(text) < 5:
        return ""
    if len(_extract_distinct_source_domains(text)) < 2:
        return ""
    if url_count < 2:
        return ""
    return f"{text}\n\n_via {provider_label}_"


async def _try_direct_sports_provider_answer(
    *,
    user_query: str,
    subject: str,
    reference_date_label: str,
    reference_timezone: str,
) -> str:
    """Use a strong provider directly for broad same-day sports schedules when available."""
    from skills.search_skills import search_web

    provider_query = (
        f"Today is {reference_date_label} in {reference_timezone}. "
        f"User request: {user_query or subject}. "
        "Create a concise same-day sports watch guide. "
        "Only include NCAA Division I men's lacrosse games. Exclude women's games, Division II, Division III, NAIA, club, MCLA, and pro leagues. "
        "If a source is not clearly NCAA Division I men's lacrosse, omit it. "
        "Return the broad full schedule with all confirmed games, not just ranked, televised, or marquee matchups. "
        "Start with one short overview sentence, then a markdown table with columns "
        "Time (ET) | Matchup | Watch | Notes. "
        "List as many confirmed games as you can find in chronological order, preserve rankings when sources include them, "
        "include non-ranked games throughout the slate rather than clustering only the headline windows, "
        "use compact network labels (for example ESPN+, ESPNU, ACCN, BTN, BTN+, CBS Sports Network, FloSports, YouTube), "
        "and use TBD when watch info is not confirmed. "
        "After the table, add 2-4 short notes on marquee windows or coverage gaps, then a Sources section."
    )
    result = await asyncio.wait_for(
        search_web(provider_query, num_results=10, provider="perplexity"),
        timeout=45,
    )
    return _normalize_direct_sports_provider_answer(result, provider_label="perplexity-direct")


def _strip_ascii_tables(text: str) -> str:
    """Convert ASCII/markdown tables to Discord-friendly bullet lines.

    Discord does not render markdown tables or ASCII box-drawing tables.
    This function detects table structures (pipe-delimited rows or +---+ borders)
    and replaces them with plain text so the content stays readable.
    """
    lines = text.split("\n")
    result: list[str] = []
    headers: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        # ASCII box border line (e.g. +----+----+)
        if re.match(r"^\+[-=+]+\+", stripped):
            in_table = True
            continue
        # Markdown table separator (e.g. |---|---|)
        if re.match(r"^\|[-: |]+\|$", stripped):
            in_table = True
            continue
        # Pipe-delimited row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not headers:
                headers = cells
                in_table = True
                continue
            # Map cells to headers as "**Header:** value" pairs
            parts = []
            for i, cell in enumerate(cells):
                if cell:
                    label = headers[i] if i < len(headers) else ""
                    parts.append(f"**{label}:** {cell}" if label else cell)
            if parts:
                result.append("• " + " | ".join(parts))
            continue
        # Non-table line — reset headers if we were mid-table
        if in_table:
            headers = []
            in_table = False
        result.append(line)

    return "\n".join(result)


def _dedupe_sources_section(text: str) -> str:
    """Remove duplicate Sources sections from Perplexity output.

    Perplexity sometimes returns an inline numbered-reference block
    (e.g. '[1] https://...') AND a plain 'Sources:\\n...' block.
    Keep only whichever appears first; strip the second one.
    """
    # Patterns that start a sources block
    sources_re = re.compile(
        r"^(sources?\s*:?\s*$|sources?\s*\n[-\d\[\]])",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(sources_re.finditer(text))
    if len(matches) < 2:
        return text
    # Keep everything up to (and including) the first block; drop the second
    second_start = matches[1].start()
    return text[:second_start].rstrip()


def _normalize_direct_provider_answer(raw_text: str, *, provider_label: str) -> str:
    """Light normalization for a general direct provider answer.

    Strips the Perplexity header prefix, rejects error strings, converts
    ASCII/markdown tables to Discord-friendly text, deduplicates Sources
    sections, and appends the provider attribution suffix.
    """
    text = (raw_text or "").strip()
    if not text or text.startswith(("❌", "⚠️")):
        return ""
    if text.startswith("**Perplexity AI Answer:**"):
        text = text[len("**Perplexity AI Answer:**"):].strip()
    text = _strip_ascii_tables(text)
    text = _dedupe_sources_section(text)
    if len(text) < 80:
        return ""
    return f"{text}\n\n_via {provider_label}_"


async def generate_news_report(*, query: str) -> str:
    """Return current-events / news headlines via Perplexity without LLM rewriting.

    Designed to be returned directly (bypassing Gemini synthesis) when the
    answer_policy detects the ``_via perplexity-direct_`` marker.
    """
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label} UTC. "
        f"User request: {query}. "
        "Provide an up-to-date, factual summary with sourced bullet points or a short structured answer. "
        "Include specific facts, numbers, and named sources where relevant. "
        "Do not speculate about future events. "
        "End with a Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_news_report Perplexity call failed: %s", exc)
        return ""
    return _normalize_direct_provider_answer(result, provider_label="perplexity-direct")


async def generate_weather_report(*, query: str) -> str:
    """Return a weather report sourced directly from Perplexity."""
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label} UTC. "
        f"User request: {query}. "
        "Provide an up-to-date, factual weather summary with current conditions and forecast. "
        "Include specific temperatures, precipitation chances, and conditions where relevant. "
        "Do not speculate. "
        "End with a Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_weather_report Perplexity call failed: %s", exc)
        return "❌ Could not retrieve weather information. Try again shortly."
    answer = _normalize_direct_provider_answer(result, provider_label="perplexity-direct")
    if not answer:
        return "❌ Could not retrieve weather information. Try again shortly."
    return answer


async def generate_finance_report(*, query: str) -> str:
    """Return a market/finance report sourced directly from Perplexity."""
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label} UTC. "
        f"User request: {query}. "
        "Provide an up-to-date, factual financial or market summary with sourced data. "
        "Include specific prices, percentages, and named sources where relevant. "
        "Do not speculate about future market movements. "
        "End with a Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_finance_report Perplexity call failed: %s", exc)
        return "❌ Could not retrieve financial data. Try again shortly."
    answer = _normalize_direct_provider_answer(result, provider_label="perplexity-direct")
    if not answer:
        return "❌ Could not retrieve financial data. Try again shortly."
    return answer


async def generate_property_search_report(*, query: str) -> str:
    """Return real estate / property search results via Perplexity without Gemini synthesis.

    Designed to bypass the Gemini tool-round chain for home/property search queries.
    The ``query`` parameter should be ``model_message`` (which includes any recalled
    user context such as price range, location preferences, and tax criteria) so that
    saved preferences are incorporated into the search.

    Designed to be returned directly (bypassing Gemini synthesis) when the
    answer_policy detects the ``_via perplexity-direct_`` marker.
    """
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label}. "
        f"Real estate search request: {query}. "
        "Find specific current home listings or property information matching the stated criteria. "
        "Format each listing as a numbered entry like this (do NOT use markdown tables or ASCII tables):\n"
        "**1. 123 Main St, Springfield PA 19064**\n"
        "• Price: $385,000\n"
        "• Beds/Baths: 3 bed / 2 bath\n"
        "• Sq Ft: 1,850\n"
        "• Features: Updated kitchen, large backyard\n"
        "• Link: https://zillow.com/...\n\n"
        "List at least 3 properties. Only include real, currently-listed properties — not general market analysis. "
        "End with a short Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_property_search_report search failed: %s", exc)
        return ""
    return _normalize_direct_provider_answer(result, provider_label="perplexity-direct")


async def generate_sports_scores_report(*, query: str) -> str:
    """Return historical sports game scores/results via Perplexity without LLM rewriting.

    Distinct from generate_sports_watch_report (upcoming schedule).  This skill
    handles past-result queries: "did the Lakers win?", "what was the final score
    of the [team] game?", "who won last night?".

    Designed to be returned directly (bypassing Gemini synthesis) when the
    answer_policy detects the ``_via perplexity-direct_`` marker.
    """
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label} UTC. "
        f"User request: {query}. "
        "Provide final scores, results, and standings for the specific game(s) asked about. "
        "Include team names, final score, game date, and key stats or highlights if available. "
        "Format results as a bullet list (do NOT use markdown or ASCII tables) — one game per entry like:\n"
        "**Team A vs Team B** — Final: 105-98\n"
        "• Date: April 12\n"
        "• Key stats: Player X had 30 pts, Player Y had 12 reb\n"
        "Do not speculate or predict — only report confirmed results. "
        "End with a Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_sports_scores_report Perplexity call failed: %s", exc)
        return ""
    return _normalize_direct_provider_answer(result, provider_label="perplexity-direct")


async def generate_entertainment_report(*, query: str) -> str:
    """Return movies, streaming, and entertainment info via Perplexity without LLM rewriting.

    Handles queries like: "what movies are in theaters?", "what's new on Netflix?",
    "Rotten Tomatoes score for X", "what to watch this weekend", "streaming releases".

    Designed to be returned directly (bypassing Gemini synthesis) when the
    answer_policy detects the ``_via perplexity-direct_`` marker.
    """
    from skills.search_skills import search_web

    now_utc = dt.datetime.now(dt.timezone.utc)
    date_label = now_utc.strftime("%A, %B %-d, %Y")
    provider_query = (
        f"Today is {date_label} UTC. "
        f"User request: {query}. "
        "Provide an up-to-date entertainment summary covering movies in theaters, new streaming releases, "
        "critic scores (Rotten Tomatoes, Metacritic), and where to watch when relevant. "
        "Format as a structured list (do NOT use markdown or ASCII tables) — one title per entry like:\n"
        "**Movie/Show Title** (Platform or Theater)\n"
        "• Rating: 92% RT / 8.1 IMDb\n"
        "• Genre: Action\n"
        "• Notes: Opening weekend, directed by X\n"
        "Include specific titles, ratings, and streaming platforms. "
        "End with a Sources section listing URLs."
    )
    try:
        result = await asyncio.wait_for(
            search_web(provider_query, num_results=10, provider="perplexity"),
            timeout=45,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        log.warning("generate_entertainment_report Perplexity call failed: %s", exc)
        return ""
    answer = _normalize_direct_provider_answer(result, provider_label="perplexity-direct")
    if not answer:
        return "❌ Could not retrieve entertainment information. Try again shortly."
    return answer


def _extract_day_window(
    text: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
    direction: str,
) -> int:
    lowered = (text or "").lower()

    if direction == "future":
        match = re.search(r"\bnext\s+(\d{1,2})\s+days?\b", lowered)
        if match:
            return max(minimum, min(int(match.group(1)), maximum))
        if "today" in lowered or "tonight" in lowered:
            return max(minimum, min(1, maximum))
        if "tomorrow" in lowered:
            return max(minimum, min(2, maximum))
        if "this weekend" in lowered or "weekend" in lowered:
            return max(minimum, min(3, maximum))
        if "next week" in lowered or "this week" in lowered:
            return max(minimum, min(7, maximum))
    else:
        match = re.search(r"\b(?:last|past)\s+(\d{1,2})\s+days?\b", lowered)
        if match:
            return max(minimum, min(int(match.group(1)), maximum))
        if "yesterday" in lowered or "today" in lowered:
            return max(minimum, min(1, maximum))
        if "last week" in lowered or "this week" in lowered:
            return max(minimum, min(7, maximum))
        if "last month" in lowered:
            return max(minimum, min(30, maximum))

    return max(minimum, min(int(default), maximum))


def infer_report_request(
    request: str,
    *,
    days: int = 7,
    output_style: str = "discord-table-detailed",
    emoji_level: str = "light",
) -> dict[str, Any]:
    """Infer report-oriented slots from a natural-language request."""
    text = (request or "").strip()
    lowered = text.lower()

    inferred_style = output_style
    for key, value in _REPORT_STYLE_ALIASES.items():
        if key in lowered:
            inferred_style = value
            break
    if "table" in lowered:
        inferred_style = "discord-table-detailed"

    inferred_emoji = emoji_level
    for key, value in _EMOJI_STYLE_ALIASES.items():
        if key in lowered:
            inferred_emoji = value
            break
    if "emoji" in lowered and inferred_emoji == "light":
        inferred_emoji = "rich"

    inferred_days = _extract_day_window(
        lowered,
        default=days,
        minimum=1,
        maximum=30,
        direction="past",
    )

    topic = "general report"
    if "box office" in lowered:
        topic = "box office"
    elif "new release" in lowered:
        topic = "new releases"
    elif "sports" in lowered:
        topic = "sports"
    elif "recap" in lowered:
        topic = "recap"

    detail_level = "brief" if "brief" in lowered or "quick" in lowered else "detailed"
    if "deep" in lowered or "detailed" in lowered:
        detail_level = "detailed"

    return {
        "topic": topic,
        "days": inferred_days,
        "output_style": inferred_style,
        "emoji_level": inferred_emoji,
        "detail_level": detail_level,
    }


def infer_sports_request(
    query: str = "",
    *,
    sport: str = "",
    league: str = "",
    team: str = "",
    days: int = 7,
) -> dict[str, Any]:
    text = (query or "").strip()
    lowered = text.lower()

    detected_sport = sport.strip()
    if not detected_sport:
        for needle, normalized in _SPORT_KEYWORDS.items():
            if needle in lowered:
                detected_sport = normalized
                break

    detected_league = league.strip()
    if not detected_league:
        for pattern, normalized in _LEAGUE_PATTERNS:
            if pattern.search(text):
                detected_league = normalized
                break

    detected_team = team.strip()
    if text and not detected_team:
        for pattern in _TEAM_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = " ".join(match.group(1).split())
                if candidate and candidate.lower() not in {"this week", "next week"}:
                    detected_team = candidate
                    break

    detected_days = _extract_day_window(
        text,
        default=days,
        minimum=1,
        maximum=14,
        direction="future",
    )

    return {
        "sport": detected_sport,
        "league": detected_league,
        "team": detected_team,
        "days": detected_days,
    }


def _format_message_history(messages: Iterable[Any], max_chars: int = 12000) -> str:
    """Turn Discord messages into a compact transcript for the LLM."""
    lines: list[str] = []
    total = 0

    for message in messages:
        author = getattr(message, "author", None)
        if getattr(author, "bot", False):
            continue

        display_name = (
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or "Unknown"
        )
        content = (getattr(message, "clean_content", None) or getattr(message, "content", "")).strip()
        attachments = getattr(message, "attachments", []) or []
        attachment_names = [getattr(a, "filename", "attachment") for a in attachments[:3]]

        if not content and not attachment_names:
            continue

        created_at = getattr(message, "created_at", None)
        if isinstance(created_at, dt.datetime):
            ts = created_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        else:
            ts = "unknown time"

        line = f"[{ts}] {display_name}: {_clean_text(content)}"
        if attachment_names:
            line += f" [attachments: {', '.join(attachment_names)}]"

        projected = total + len(line) + 1
        if projected > max_chars:
            remaining = max_chars - total
            if remaining > 50:
                lines.append(line[: remaining - 1] + "…")
            break

        lines.append(line)
        total = projected

    return "\n".join(lines) or "(No non-bot messages found in the selected time window.)"


async def _resolve_channel(channel_id: int) -> discord.abc.Messageable | None:
    bot = get_bot()
    if bot is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    try:
        fetched = await bot.fetch_channel(channel_id)
    except Exception as exc:
        log.warning("Could not fetch channel %s for recap: %s", channel_id, exc)
        return None
    return fetched


async def generate_channel_recap_report(
    channel_id: int | str | None = None,
    thread_id: int | str | None = None,
    days: int = 7,
    focus: str = "",
    style: str | None = None,
    max_messages: int = 200,
) -> str:
    """Summarize the recent activity in a Discord channel or thread."""
    if channel_id in (None, "", 0, "0"):
        channel_id = get_current_channel_id()
    if thread_id in (None, "", 0, "0"):
        thread_id = get_current_thread_id()

    try:
        channel_int = int(channel_id)
    except (TypeError, ValueError):
        return (
            "❌ No Discord channel context is available for recap generation. "
            "Run this from the channel you want to summarize or use `/recap weekly`."
        )

    channel = await _resolve_channel(channel_int)
    if channel is None:
        return "❌ Discord channel not available yet. Try again once the bot is fully online."

    window_days = max(1, min(int(days), 30))
    try:
        thread_int = int(thread_id) if thread_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        thread_int = None

    profile = get_effective_channel_profile(channel_id=channel_int, thread_id=thread_int)
    if style is None:
        profile_depth = profile.get("report_depth", "standard")
        profile_table_style = profile.get("table_style", "discord")
        if profile_depth == "detailed" or profile_table_style == "copy-safe":
            style = "table"
        elif profile_depth == "brief":
            style = "highlights"
        else:
            style = "action-items"
    style_key = _normalize_style(style)
    cutoff = discord.utils.utcnow() - dt.timedelta(days=window_days)
    history_limit = max(25, min(int(max_messages), 300))

    try:
        messages = [
            message
            async for message in channel.history(
                limit=history_limit,
                after=cutoff,
                oldest_first=True,
            )
        ]
    except Exception as exc:
        log.error("Failed to read channel history for %s: %s", channel_int, exc)
        return f"❌ Could not read Discord history for this recap: {exc}"

    transcript = _format_message_history(messages)
    if transcript.startswith("(No non-bot messages"):
        channel_name = getattr(channel, "name", f"channel-{channel_int}")
        return f"⚠️ No user messages found in #{channel_name} for the last {window_days} day(s)."

    channel_name = getattr(channel, "name", f"channel-{channel_int}")
    focus_text = focus.strip() or "general updates, decisions, blockers, and follow-ups"
    style_instructions = _RECAP_STYLES[style_key]
    tone_instruction = {
        "concise": "Keep sentences short and direct.",
        "analytical": "Emphasize patterns, root causes, and implications.",
        "friendly": "Use a warm, approachable voice while staying professional.",
    }.get(profile.get("tone", "neutral"), "Use a neutral, professional tone.")
    depth_instruction = {
        "brief": "Limit output to the most important points and keep sections short.",
        "detailed": "Add richer context, rationale, and concrete follow-up details.",
    }.get(profile.get("report_depth", "standard"), "Balance brevity with useful detail.")
    source_instruction = (
        "Only include facts directly grounded in the transcript. Mark uncertain claims as 'Unverified'."
        if profile.get("source_strictness") == "strict"
        else "Avoid speculation and stay grounded in the transcript."
    )

    prompt = (
        f"You are writing a weekly Discord recap for #{channel_name}.\n"
        f"Time window: last {window_days} days.\n"
        f"Focus: {focus_text}\n"
        f"Style: {style_key}\n\n"
        f"{style_instructions}\n"
        f"{tone_instruction}\n"
        f"{depth_instruction}\n"
        f"Emoji level: {profile.get('emoji_level', 'light')}.\n"
        f"{source_instruction}\n"
        "Use GitHub-flavored Markdown. Be specific, concise, and do not invent details.\n"
        "If there are no decisions or action items, say so explicitly.\n\n"
        f"Discord transcript:\n{transcript}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="copilot",
                tool_declarations=[],
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "❌ Weekly recap timed out while generating."
    except Exception as exc:
        log.error("Weekly recap generation failed: %s", exc)
        return f"❌ Weekly recap failed: {exc}"

    grounding = _compute_evidence_completeness(response.strip())
    require_uncertainty = (not grounding["fail_safe"]) and (
        grounding["unsupported_claim_count"] > 0 or grounding["evidence_completeness"] < 0.6
    )
    partial_coverage = bool(require_uncertainty)
    reviewed_response = _enforce_uncertainty_wording(
        response.strip(),
        require_uncertainty=require_uncertainty,
    )
    warning_banner = ""
    if partial_coverage:
        evidence_pct = int(round(float(grounding["evidence_completeness"]) * 100))
        warning_banner = _build_coverage_shortfall_block(
            label=(
                f"Evidence completeness is {evidence_pct}% and "
                f"{grounding['unsupported_claim_count']} claim-like statement(s) are unsupported."
            ),
            scope_hint="limit recap scope (single thread/topic or fewer days) and rerun",
            confidence_hint="Keep key details tentative until verified in source messages",
        )
    recap_output = (
        f"## Weekly recap for #{channel_name}\n\n"
        f"{reviewed_response}\n\n"
        f"_Reviewed {len(messages)} messages from the last {window_days} day(s) via {model_used}_"
    )
    evidence_pct = int(round(float(grounding["evidence_completeness"]) * 100))
    if grounding["fail_safe"]:
        _record_quality_metric("recap_evidence_fail_safe", context="channel_recap")
    elif grounding["evidence_completeness"] < 0.6:
        _record_quality_metric("recap_low_evidence_completeness", context="channel_recap")
    evidence_status_line = (
        "- Evidence status: ⚪ fail-safe (source fields missing; no automatic penalty)\n"
        if grounding["fail_safe"]
        else (
            "- Evidence status: ⚠️ unsupported claim rows detected\n"
            if grounding["unsupported_claim_count"] > 0
            else "- Evidence status: ✅ claim rows appear grounded\n"
        )
    )
    recap_output = (
        f"{warning_banner}{recap_output}\n\n"
        "## 📎 Coverage Summary\n"
        f"- Evidence completeness: **{evidence_pct}%** "
        f"({grounding['supported_claim_count']}/{max(grounding['claim_like_count'], 1)} claim-like statements supported)\n"
        f"{evidence_status_line}"
        + (
            "- Coverage shortfall: add source backing for unsupported claim-like statements.\n"
            "- Retry scope hint: limit recap scope (single thread/topic or fewer days) and rerun.\n"
            if partial_coverage
            else ""
        )
    )
    try:
        import vector_store

        recap_anchor_id = f"recap_{int(dt.datetime.now(dt.timezone.utc).timestamp())}_{channel_int}"
        await vector_store.add_document(
            vector_store.RESEARCH_COLLECTION,
            doc_id=recap_anchor_id,
            text=recap_output,
            metadata={
                "type": "report",
                "query": f"weekly recap {channel_name}",
                "anchor_id": recap_anchor_id,
                "source": "generate_channel_recap_report",
            },
            channel_id=channel_int,
            thread_id=thread_int,
        )
        set_anchor_state(channel_int, thread_int, recap_anchor_id)
    except Exception as exc:
        log.debug("Recap anchoring failed: %s", exc)

    return recap_output


def build_sports_watch_query(
    query: str = "",
    sport: str = "",
    league: str = "",
    team: str = "",
    days: int = 7,
) -> str:
    """Build a search-friendly sports query from structured parameters."""
    if query.strip():
        return query.strip()

    subject_bits = [bit.strip() for bit in (team, league, sport) if bit and bit.strip()]
    subject = " ".join(subject_bits) or "sports"
    lookahead = max(1, min(int(days), 14))
    return (
        f"{subject} upcoming games next {lookahead} days "
        "watch TV streaming schedule"
    )


def _report_format_instructions(style: str, emoji_level: str) -> str:
    style_key = (style or "discord-table-detailed").strip().lower()
    emoji_key = (emoji_level or "light").strip().lower()

    if style_key == "discord-table-brief":
        style_text = (
            "Provide one concise markdown table followed by no more than 3 short bullet highlights."
        )
    elif style_key == "discord-recap-hybrid":
        style_text = (
            "Provide a compact markdown table first, then a short recap section with key takeaways."
        )
    else:
        style_text = (
            "Provide a detailed markdown table first, then a brief commentary section with trends."
        )

    if emoji_key == "none":
        emoji_text = "Do not use emojis."
    elif emoji_key == "rich":
        emoji_text = (
            "Use emojis to improve scanability (e.g., 🎬 releases, 💰 gross, 📈 up, 📉 down), but keep it readable."
        )
    else:
        emoji_text = "Use light emojis sparingly for visual grouping."

    return f"{style_text} {emoji_text}"


def _append_report_guardrails(
    report_text: str,
    *,
    timeframe_label: str,
    require_table: bool = True,
) -> str:
    """Ensure the generated report includes required framing and disclosures."""
    text = (report_text or "").strip()
    lines: list[str] = [text] if text else []

    if timeframe_label.lower() not in text.lower():
        lines.append(f"\n_Time window: {timeframe_label}_")

    has_table = "\n|" in text and "\n| ---" in text
    if require_table and not has_table:
        lines.append(
            "\n| Item | Metric | Value | Notes |\n"
            "| --- | --- | --- | --- |\n"
            "| N/A | N/A | N/A | Source data did not include a structured table-ready result. |"
        )

    if "source" not in text.lower():
        lines.append("\n**Sources:** Aggregated from live web search results used in this report.")

    if "n/a" not in text.lower():
        lines.append("_Missing values are marked as N/A when source data is incomplete._")

    return "\n".join(part for part in lines if part).strip()


def _count_markdown_table_items(markdown_text: str) -> int:
    """Count data rows in markdown tables (excluding header + separator rows)."""
    rows = []
    for line in (markdown_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[\s:\-\|]+\|?$", stripped):
            continue
        rows.append(stripped)

    if len(rows) <= 1:
        return 0
    # First non-separator row is assumed to be table header.
    return max(0, len(rows) - 1)


def _extract_distinct_source_domains(text: str) -> set[str]:
    """Extract unique URL domains from free-form text."""
    matches = re.findall(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?:/|\b)", text or "")
    return {m.lower().lstrip("www.") for m in matches if m}


def _sports_watch_quality_targets(*, subject: str, lookahead_days: int) -> tuple[int, int, str]:
    """Return deterministic quality thresholds for sports recap outputs."""
    lowered = (subject or "").lower()
    if "weekend" in lowered:
        return 8, 3, "full-weekend sports recap"
    if lookahead_days >= 7:
        return 5, 2, f"{lookahead_days}-day sports recap"
    return 2, 1, f"{lookahead_days}-day sports recap"


def _estimate_matchup_mentions(text: str) -> int:
    """Heuristic estimate of unique game mentions in search/provider text."""
    if not text:
        return 0
    return len({match.group(0).strip().lower() for match in _MATCHUP_RE.finditer(text)})


def _extract_domain(url: str) -> str:
    match = re.search(r"^https?://([^/]+)", (url or "").strip(), re.IGNORECASE)
    if not match:
        return ""
    host = match.group(1).lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _split_markdown_row(line: str) -> list[str]:
    stripped = (line or "").strip()
    if not stripped.startswith("|"):
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells if cells and any(cell for cell in cells) else []


def _is_claim_like_text(text: str) -> bool:
    normalized = " ".join((text or "").split())
    lowered = normalized.lower()
    if not normalized or lowered.startswith(("source:", "sources:")):
        return False
    if len(normalized) < 16:
        return False
    if _SCORE_RE.search(normalized) or _MONEY_RE.search(normalized) or _STATUS_RE.search(normalized):
        return True
    if _MATCHUP_RE.search(normalized):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\b", normalized):
        return True
    return bool(
        re.search(
            r"\b(increase|decrease|rose|fell|deploy|rollback|mitigation|impact|timeline|risk|action item|follow-up)\b",
            lowered,
        )
    )


def _contains_source_evidence(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False
    if _URL_RE.search(normalized):
        return True
    lowered = normalized.lower()
    if lowered in _PLACEHOLDER_SOURCE_VALUES:
        return False
    return bool(_DOMAIN_TOKEN_RE.search(normalized))


def _compute_evidence_completeness(markdown_text: str) -> dict[str, Any]:
    """Compute deterministic claim-grounding completeness for recap/report text."""
    lines = (markdown_text or "").splitlines()
    source_fields_present = False
    global_sources_present = False
    claim_like_count = 0
    supported_claim_count = 0

    table_idx = 0
    while table_idx < len(lines):
        line = lines[table_idx].strip()
        if not line.startswith("|"):
            table_idx += 1
            continue
        table_lines: list[str] = []
        while table_idx < len(lines) and lines[table_idx].strip().startswith("|"):
            table_lines.append(lines[table_idx].strip())
            table_idx += 1
        if len(table_lines) < 2:
            continue
        header_cells = _split_markdown_row(table_lines[0])
        source_idx: int | None = None
        for idx, cell in enumerate(header_cells):
            if any(label in cell.lower() for label in ("source", "citation", "evidence", "reference", "ref")):
                source_idx = idx
                source_fields_present = True
                break
        for row_line in table_lines[1:]:
            if re.match(r"^\|[\s:\-\|]+\|?$", row_line):
                continue
            row_cells = _split_markdown_row(row_line)
            row_text = " | ".join(row_cells) if row_cells else row_line
            if source_idx is not None and row_cells:
                claim_cells = [cell for idx, cell in enumerate(row_cells) if idx != source_idx]
                claim_text = " | ".join(claim_cells)
            else:
                claim_text = row_text
            if not _is_claim_like_text(claim_text):
                continue
            claim_like_count += 1
            supported = _contains_source_evidence(row_text)
            if not supported and source_idx is not None and source_idx < len(row_cells):
                supported = _contains_source_evidence(row_cells[source_idx])
            if supported:
                supported_claim_count += 1

    in_sources_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("|") or line.startswith("#"):
            continue
        lowered = line.lower()
        if lowered.startswith(("source:", "sources:")):
            source_fields_present = True
            in_sources_section = True
            if _contains_source_evidence(line):
                global_sources_present = True
            continue
        if in_sources_section and line.startswith("-"):
            if _contains_source_evidence(line):
                global_sources_present = True
            continue
        if in_sources_section and not line.startswith(("-", "*")):
            in_sources_section = False

        if _is_claim_like_text(line):
            claim_like_count += 1
            supported = _contains_source_evidence(line) or (source_fields_present and global_sources_present)
            if supported:
                supported_claim_count += 1

    fail_safe = claim_like_count > 0 and not source_fields_present
    if claim_like_count == 0 or fail_safe:
        completeness = 1.0
        unsupported_claim_count = 0
    else:
        completeness = round(supported_claim_count / claim_like_count, 3)
        unsupported_claim_count = max(claim_like_count - supported_claim_count, 0)

    return {
        "evidence_completeness": completeness,
        "claim_like_count": int(claim_like_count),
        "supported_claim_count": int(supported_claim_count if not fail_safe else claim_like_count),
        "unsupported_claim_count": int(unsupported_claim_count),
        "source_fields_present": bool(source_fields_present),
        "fail_safe": bool(fail_safe),
    }


def _parse_search_evidence_rows(search_results: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    def _flush_current() -> None:
        nonlocal current
        if not current:
            return
        current["title"] = (current.get("title", "") or "").strip() or "Untitled"
        current["snippet"] = " ".join((current.get("snippet", "") or "").split()).strip()
        rows.append(current)
        current = None

    for raw_line in (search_results or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### Query:") or line.startswith("### Provider override:"):
            _flush_current()
            continue

        title_match = _RESULT_TITLE_RE.match(line)
        if title_match:
            _flush_current()
            current = {"title": title_match.group(1).strip(), "url": "", "snippet": ""}
            continue

        url_match = _URL_RE.search(line)
        if line.startswith("🔗") and url_match:
            if current is None:
                current = {"title": "Untitled", "url": "", "snippet": ""}
            current["url"] = url_match.group(0).rstrip(".,)")
            continue

        if url_match and current is None:
            url = url_match.group(0).rstrip(".,)")
            title = line.replace(url_match.group(0), "").strip(" -:[]*")
            current = {"title": title or "Untitled", "url": url, "snippet": ""}
            continue

        if line.lower().startswith("*providers queried:") or line.lower().startswith("*via "):
            continue
        if line.startswith("**Web Search Results**"):
            continue

        if current is None:
            current = {"title": "Untitled", "url": "", "snippet": line}
        else:
            snippet = current.get("snippet", "")
            current["snippet"] = f"{snippet} {line}".strip()

    _flush_current()
    return rows


def _rank_search_evidence_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    try:
        from skills.search_skills import rank_hits_for_evidence

        ranked = rank_hits_for_evidence(rows)
        if ranked:
            return ranked
        log.info("Evidence ranker returned no rows; falling back to parsed order")
        _record_quality_metric("reporting_ranker_empty_fallback", context="reporting")
    except Exception:
        log.warning("Evidence ranker failed; falling back to parsed order", exc_info=True)
        _record_quality_metric("reporting_ranker_error_fallback", context="reporting")
    return list(rows)


def _normalize_url_for_dedup(url: str) -> str:
    cleaned = (url or "").strip().rstrip(".,)")
    if not cleaned:
        return ""
    cleaned = re.sub(r"#.*$", "", cleaned)
    cleaned = re.sub(r"\?.*$", "", cleaned)
    return cleaned.rstrip("/")


def _canonical_row_text(row: dict[str, str]) -> str:
    text = " ".join(
        part
        for part in ((row.get("title", "") or "").strip(), (row.get("snippet", "") or "").strip())
        if part
    ).lower()
    text = re.sub(r"\b(today|latest|update|updated|report|coverage|recap|preview)\b", " ", text)
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _event_identity_for_row(row: dict[str, str]) -> str:
    text = " ".join(
        part
        for part in ((row.get("title", "") or "").strip(), (row.get("snippet", "") or "").strip())
        if part
    )
    matchup = ""
    match = _MATCHUP_RE.search(text)
    if match:
        matchup = re.sub(r"\s+", " ", match.group(0).lower()).strip()
    scores = sorted({m.group(0).replace("–", "-") for m in _SCORE_RE.finditer(text)})
    dates = sorted(
        {
            m.group(0).lower()
            for m in re.finditer(
                r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day)?\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
                text,
                re.IGNORECASE,
            )
        }
    )
    status_terms = sorted({m.group(1).lower() for m in _STATUS_RE.finditer(text)})
    if not matchup and not scores and not dates and not status_terms:
        return ""
    return "|".join(
        part
        for part in (
            f"matchup:{matchup}" if matchup else "",
            f"scores:{','.join(scores[:2])}" if scores else "",
            f"dates:{','.join(dates[:2])}" if dates else "",
            f"status:{','.join(status_terms[:2])}" if status_terms else "",
        )
        if part
    )


def _row_identity_key(row: dict[str, str]) -> str:
    url_key = _normalize_url_for_dedup(str(row.get("url", "")))
    if url_key:
        return f"url:{url_key}"
    event_identity = _event_identity_for_row(row)
    if event_identity:
        return f"event:{event_identity}|domain:{_extract_domain(str(row.get('url', '')))}"
    return f"text:{_canonical_row_text(row)}"


def _filter_near_duplicate_evidence_rows(rows: list[dict[str, str]], *, similarity_threshold: float = 0.97) -> list[dict[str, str]]:
    """Drop obvious duplicates while preserving distinct events with similar phrasing."""
    if not rows:
        return []
    filtered: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_identity_keys: set[str] = set()
    canonical_by_domain: dict[str, list[str]] = {}
    event_identities_by_domain: dict[str, list[str]] = {}

    for row in rows:
        normalized_row = {
            "title": (row.get("title", "") or "").strip() or "Untitled",
            "url": (row.get("url", "") or "").strip(),
            "snippet": " ".join((row.get("snippet", "") or "").split()).strip(),
        }
        normalized_row.update({k: v for k, v in row.items() if k not in {"title", "url", "snippet"}})

        normalized_url = _normalize_url_for_dedup(normalized_row.get("url", ""))
        if normalized_url and normalized_url in seen_urls:
            continue

        domain = _extract_domain(normalized_url or normalized_row.get("url", "")) or "unknown"
        canonical = _canonical_row_text(normalized_row)
        identity_key = _row_identity_key(normalized_row)
        if identity_key in seen_identity_keys and identity_key.startswith("url:"):
            continue

        near_duplicate = False
        existing_canonicals = canonical_by_domain.get(domain, [])
        existing_events = event_identities_by_domain.get(domain, [])
        for idx, existing in enumerate(existing_canonicals):
            if canonical and existing and SequenceMatcher(None, canonical, existing).ratio() >= similarity_threshold:
                event_a = _event_identity_for_row(normalized_row)
                event_b = existing_events[idx] if idx < len(existing_events) else ""
                if event_a and event_b and event_a != event_b:
                    continue
                near_duplicate = True
                break
        if near_duplicate:
            continue

        filtered.append(normalized_row)
        if normalized_url:
            seen_urls.add(normalized_url)
        if identity_key:
            seen_identity_keys.add(identity_key)
        row_event_identity = _event_identity_for_row(normalized_row)
        if canonical:
            canonical_by_domain.setdefault(domain, []).append(canonical)
            event_identities_by_domain.setdefault(domain, []).append(row_event_identity)
    return filtered


def _merge_ranked_with_parsed_rows(
    ranked_rows: list[dict[str, str]],
    parsed_rows: list[dict[str, str]],
    *,
    max_rows: int = 24,
    dedupe: bool = True,
) -> list[dict[str, str]]:
    """Recover distinct events that may have been collapsed upstream, then de-duplicate safely."""
    merged: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for source_rows in (ranked_rows, parsed_rows):
        for row in source_rows:
            key = _row_identity_key(row)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(row)
            if len(merged) >= max_rows:
                break
        if len(merged) >= max_rows:
            break
    if not dedupe:
        return merged
    return _filter_near_duplicate_evidence_rows(merged)


def _build_source_diversity_feedback(*, source_count: int, min_sources: int) -> dict[str, Any]:
    required = max(int(min_sources), 1)
    observed = max(int(source_count), 0)
    shortfall = max(required - observed, 0)
    floor_met = shortfall == 0
    warning_text = ""
    summary_note = ""
    if shortfall:
        scope_hint = "Re-run with a tighter scope (single league/team or shorter window) to improve coverage."
        warning_text = (
            f"> ⚠️ **Partial coverage warning:** Source diversity floor missed ({observed}/{required} distinct domains).\n"
            f"> 🧭 **Actionable shortfall:** Add at least **{shortfall}** more distinct source domain(s) "
            "before treating this recap/report as complete.\n"
            f"> 🔁 **Retry scope hint:** {scope_hint}\n"
            "> ℹ️ **Confidence posture:** Keep conclusions tentative until source diversity improves.\n\n"
        )
        summary_note = (
            f"- Source diversity shortfall: add **{shortfall}** more distinct domain(s) to reach the required floor.\n"
            f"- Retry scope hint: {scope_hint}\n"
        )
    return {
        "required": required,
        "observed": observed,
        "shortfall": shortfall,
        "floor_met": floor_met,
        "warning_text": warning_text,
        "summary_note": summary_note,
    }


def _claim_key_for_row(row: dict[str, str]) -> str:
    title = (row.get("title", "") or "").strip().lower()
    title = re.sub(r"[^a-z0-9 ]", " ", title)
    title = re.sub(r"\b(today|latest|update|updated|report|coverage)\b", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    if title:
        return title
    return _extract_domain(str(row.get("url", ""))) or "unknown"


def _claim_signature_for_row(row: dict[str, str]) -> str:
    text = " ".join(
        part
        for part in (row.get("title", ""), row.get("snippet", ""))
        if part
    )
    parts: list[str] = []
    parts.extend(sorted({m.group(0).replace("–", "-") for m in _SCORE_RE.finditer(text)}))
    parts.extend(sorted({m.group(0).lower() for m in _MONEY_RE.finditer(text)}))
    parts.extend(sorted({m.group(1).lower() for m in _STATUS_RE.finditer(text)}))
    return "|".join(parts[:4])


def _collect_conflict_clusters(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        key = _claim_key_for_row(row)
        signature = _claim_signature_for_row(row)
        if not signature:
            continue
        bucket = grouped.setdefault(key, {"signatures": set(), "domains": set()})
        bucket["signatures"].add(signature)
        domain = _extract_domain(str(row.get("url", "")))
        if domain:
            bucket["domains"].add(domain)
    clusters: list[dict[str, Any]] = []
    for key, bucket in grouped.items():
        signatures = bucket.get("signatures", set())
        domains = bucket.get("domains", set())
        if len(signatures) > 1 and len(domains) >= 2:
            clusters.append(
                {
                    "key": key,
                    "signature_count": len(signatures),
                    "domain_count": len(domains),
                }
            )
    return clusters


def _count_conflicting_evidence_groups(rows: list[dict[str, str]]) -> int:
    return len(_collect_conflict_clusters(rows))


def _summarize_evidence_health(rows: list[dict[str, str]]) -> dict[str, Any]:
    total = len(rows)
    stale_count = sum(
        1
        for row in rows
        if str(row.get("stale_signal", "")).strip().lower() in {"1", "true", "yes"}
    )
    if stale_count == 0:
        stale_count = sum(1 for row in rows if _safe_float(row.get("freshness_score", 100), 100) < 40)
    conflict_clusters = _collect_conflict_clusters(rows)
    low_trust_low_fresh_count = sum(
        1
        for row in rows
        if str(row.get("low_trust_low_fresh_signal", "")).strip().lower() in {"1", "true", "yes"}
        or (
            _safe_float(row.get("trust_score", 100), 100) < _LOW_TRUST_THRESHOLD
            and _safe_float(row.get("freshness_score", 100), 100) < _LOW_FRESHNESS_THRESHOLD
        )
    )
    strict_uncertainty_required = low_trust_low_fresh_count > 0
    return {
        "total": total,
        "stale_count": stale_count,
        "conflict_groups": len(conflict_clusters),
        "low_trust_low_fresh_count": low_trust_low_fresh_count,
        "strict_uncertainty_required": strict_uncertainty_required,
    }


def _format_evidence_health_lines(health: dict[str, Any]) -> list[str]:
    total = max(int(health.get("total", 0)), 0)
    stale_count = max(int(health.get("stale_count", 0)), 0)
    conflict_groups = max(int(health.get("conflict_groups", 0)), 0)
    low_trust_low_fresh_count = max(int(health.get("low_trust_low_fresh_count", 0)), 0)
    strict_uncertainty_required = bool(health.get("strict_uncertainty_required", False))
    freshness_line = (
        f"- Freshness signals: ⚠️ stale evidence in **{stale_count}**/{total} ranked item(s)\n"
        if total and stale_count
        else "- Freshness signals: ✅ no strong stale evidence detected\n"
    )
    consistency_line = (
        f"- Consistency signals: ⚠️ conflicting claims detected across **{conflict_groups}** topic group(s)\n"
        if conflict_groups
        else "- Consistency signals: ✅ no direct evidence conflicts detected\n"
    )
    reliability_line = (
        f"- Reliability signals: ⚠️ low-trust + low-freshness evidence in **{low_trust_low_fresh_count}**/{total} ranked item(s)\n"
        if total and low_trust_low_fresh_count
        else "- Reliability signals: ✅ no low-trust + low-freshness evidence cluster detected\n"
    )
    confidence_line = (
        "- Confidence posture: ℹ️ use tentative wording and verify high-impact details with primary sources\n"
        if strict_uncertainty_required
        else "- Confidence posture: ✅ standard confidence wording is acceptable\n"
    )
    return [freshness_line, consistency_line, reliability_line, confidence_line]


def _build_coverage_shortfall_block(
    *,
    label: str,
    scope_hint: str,
    confidence_hint: str,
) -> str:
    """Compact, explicit recovery guidance for partial coverage scenarios."""
    return (
        f"> ⚠️ **Partial coverage warning:** {label}\n"
        "> 🧭 **Actionable shortfall:** Coverage target was missed.\n"
        f"> 🔁 **Retry scope hint:** {scope_hint}\n"
        f"> ℹ️ **Confidence posture:** {confidence_hint}\n\n"
    )


def _enforce_uncertainty_wording(
    text: str,
    *,
    require_uncertainty: bool,
) -> str:
    if not text:
        return text
    if not require_uncertainty:
        return text
    if _UNCERTAINTY_SIGNAL_RE.search(text):
        return text
    note = (
        "> ℹ️ **Confidence note:** Evidence quality is mixed, so key details below should be treated as "
        "tentative until verified against primary sources."
    )
    return f"{note}\n\n{text.strip()}"


def _format_ranked_evidence_context(rows: list[dict[str, str]], *, max_items: int = 12) -> str:
    if not rows:
        return "(No ranked evidence rows available.)"
    lines: list[str] = []
    for index, row in enumerate(rows[:max_items], 1):
        title = (row.get("title", "") or "Untitled").strip()
        url = (row.get("url", "") or "").strip()
        snippet = (row.get("snippet", "") or "").strip()[:180]
        trust = _safe_float(row.get("trust_score", 0), 0)
        freshness = _safe_float(row.get("freshness_score", 0), 0)
        stale_tag = " stale" if _safe_float(row.get("freshness_score", 100), 100) < 40 else ""
        source = f"[{title}]({url})" if url else title
        lines.append(
            f"{index}. {source} — trust {trust:.0f}/100, freshness {freshness:.0f}/100{stale_tag}"
            + (f" | {snippet}" if snippet else "")
        )
    return "\n".join(lines)


async def generate_sports_watch_report(
    query: str = "",
    sport: str = "",
    league: str = "",
    team: str = "",
    days: int = 7,
    include_watch_info: bool = True,
) -> str:
    """Create a structured sports watch guide from web search results."""
    inferred = infer_sports_request(
        query=query,
        sport=sport,
        league=league,
        team=team,
        days=days,
    )
    lookahead = max(1, min(int(inferred["days"]), 14))
    competition_qualifier = _detect_competition_qualifier(
        query,
        str(inferred.get("league") or ""),
        str(inferred.get("sport") or ""),
    )
    qualified_sport = " ".join(
        part
        for part in (
            competition_qualifier,
            str(inferred.get("sport") or "").strip(),
        )
        if part
    ).strip()
    base_query = build_sports_watch_query(
        query=query,
        sport=qualified_sport or str(inferred["sport"]),
        league=str(inferred["league"]),
        team=str(inferred["team"]),
        days=lookahead,
    )
    search_query = base_query
    if include_watch_info:
        search_query += " TV schedule where to watch streaming ESPN NCAA"
    recap_mode = any(
        phrase in (query or "").lower()
        for phrase in ("recap", "results", "final", "scoreboard", "last weekend")
    )
    query_lower = (query or "").lower()
    weekend_mode = "weekend" in query_lower
    structured_same_day_mode = (
        not query_lower.strip()
        and lookahead == 1
        and not recap_mode
        and not str(inferred.get("team") or "").strip()
    )
    same_day_mode = any(phrase in query_lower for phrase in ("today", "tonight")) or structured_same_day_mode
    broad_subject = " ".join(
        part
        for part in (
            str(inferred.get("league") or "").strip(),
            qualified_sport,
        )
        if part
    ).strip()
    full_slate_mode = (
        not str(inferred.get("team") or "").strip()
        and (
            same_day_mode
            or "all games" in query_lower
            or "full schedule" in query_lower
            or "full slate" in query_lower
        )
    )
    query_num_results = 15 if full_slate_mode else 10
    query_min_results = 8 if full_slate_mode else 5
    reference_now, reference_timezone = _get_reporting_reference_context()
    date_hint = reference_now.strftime("%B %d %Y")
    reference_date_label = reference_now.strftime("%A, %B %d, %Y")
    subject = (
        query.strip()
        or " ".join(
            bit for bit in (
                str(inferred["team"]),
                str(inferred["league"]),
                qualified_sport or str(inferred["sport"]),
            ) if bit
        ).strip()
        or "Upcoming games"
    )

    if full_slate_mode and same_day_mode and not str(inferred.get("team") or "").strip():
        try:
            direct_provider_answer = await _try_direct_sports_provider_answer(
                user_query=query.strip(),
                subject=subject,
                reference_date_label=reference_date_label,
                reference_timezone=reference_timezone,
            )
        except asyncio.TimeoutError:
            direct_provider_answer = ""
        except Exception as exc:
            log.debug("Direct sports provider answer failed: %s", exc)
            direct_provider_answer = ""
        if direct_provider_answer:
            return direct_provider_answer

    if structured_same_day_mode and broad_subject:
        search_query = f"{broad_subject} today all games schedule"
        if include_watch_info:
            search_query += " TV schedule where to watch streaming ESPN NCAA"

    search_queries: list[str] = [search_query]
    if weekend_mode:
        search_queries.extend([f"{search_query} Saturday", f"{search_query} Sunday"])
    if recap_mode:
        search_queries.append(f"{search_query} all games final scores")
    if broad_subject:
        if full_slate_mode:
            search_queries.extend(
                [
                    f"{broad_subject} today all games schedule {date_hint}",
                    f"{broad_subject} today TV schedule ESPN ESPN+ all games {date_hint}",
                    f"{broad_subject} today scoreboard all games {date_hint}",
                ]
            )
        elif not str(inferred.get("team") or "").strip():
            search_queries.append(
                f"{broad_subject} {'weekend results all games' if recap_mode else f'next {lookahead} days all games schedule'}"
            )

    search_chunks: list[str] = []
    seen_chunks: set[str] = set()
    try:
        from skills.search_skills import search_web

        for q in search_queries:
            result = await asyncio.wait_for(
                search_web(
                    q,
                    num_results=query_num_results,
                    min_results=query_min_results,
                    retry_on_low_results=True,
                    expand_query=True,
                    expansion_context="sports_recap",
                ),
                timeout=45,
            )
            normalized = (result or "").strip()
            if not normalized or normalized.startswith("❌"):
                continue
            key = " ".join(normalized.split()).lower()
            if key in seen_chunks:
                continue
            seen_chunks.add(key)
            search_chunks.append(f"### Query: {q}\n{normalized}")
    except asyncio.TimeoutError:
        return "❌ Sports search timed out."
    except Exception as exc:
        log.error("Sports search failed for %r: %s", search_query, exc)
        return f"❌ Sports search failed: {exc}"

    if not search_chunks:
        return "❌ Sports search failed: no usable results returned."

    search_results = "\n\n---\n\n".join(search_chunks)
    parsed_rows = _parse_search_evidence_rows(search_results)
    ranked_evidence = _rank_search_evidence_rows(parsed_rows)
    evidence_rows = _merge_ranked_with_parsed_rows(
        ranked_evidence,
        parsed_rows,
        max_rows=60 if full_slate_mode else 28,
        dedupe=not full_slate_mode,
    )
    if not evidence_rows:
        evidence_rows = _filter_near_duplicate_evidence_rows(parsed_rows)
    evidence_health = _summarize_evidence_health(evidence_rows)
    ranked_evidence_context = _format_ranked_evidence_context(
        evidence_rows,
        max_items=40 if full_slate_mode else 14,
    )
    fallback_applied = False
    initial_source_count = len(_extract_distinct_source_domains(search_results))
    initial_matchup_mentions = _estimate_matchup_mentions(search_results)
    needs_strong_coverage = weekend_mode or recap_mode or full_slate_mode
    if needs_strong_coverage and (initial_source_count < 2 or initial_matchup_mentions < max(4, lookahead * 2)):
        fallback_applied = True
        fallback_query = (
            f"{broad_subject} today all games TV schedule scoreboard {date_hint}".strip()
            if full_slate_mode and broad_subject
            else " ".join(
                part
                for part in (
                    str(inferred.get("league") or "").strip(),
                    str(inferred.get("sport") or "").strip(),
                    "weekend all games final scores" if recap_mode else f"next {max(lookahead, 7)} days all games schedule",
                )
                if part
            ).strip()
        )
        for provider in ("perplexity", "serper", "duckduckgo", ""):
            try:
                fallback_results = await asyncio.wait_for(
                    search_web(
                        fallback_query,
                        num_results=query_num_results,
                        provider=provider,
                        min_results=max(4, query_min_results - 1),
                        retry_on_low_results=True,
                        expand_query=True,
                        expansion_context="sports_recap",
                    ),
                    timeout=45,
                )
            except Exception:
                continue
            normalized = (fallback_results or "").strip()
            if not normalized or normalized.startswith("❌"):
                continue
            key = " ".join(normalized.split()).lower()
            if key in seen_chunks:
                continue
            seen_chunks.add(key)
            search_chunks.append(f"### Query: {fallback_query}\n### Provider override: {provider or 'auto'}\n{normalized}")
        search_results = "\n\n---\n\n".join(search_chunks)

    evidence_matchup_mentions = _estimate_matchup_mentions(search_results)
    target_rows = 0
    if weekend_mode or recap_mode or full_slate_mode:
        # Use evidence-driven row targets for fuller recap output when broad requests
        # ask for league/weekend coverage (e.g. "this weekend's games").
        target_rows = max(6, min(20, evidence_matchup_mentions))
        if full_slate_mode and not str(inferred.get("team") or "").strip():
            target_rows = max(target_rows, 10)
        elif not str(inferred.get("team") or "").strip():
            target_rows = max(target_rows, 10 if weekend_mode else 8)

    watch_instruction = (
        "Include a Watch column with compact network abbreviations (for example ESPN+, ESPNU, ACCN, BTN, BTN+, CBS Sports Network, FloSports, YouTube), or 'TBD' when not available."
        if include_watch_info
        else "Include a Notes column instead of Watch information."
    )
    recap_instruction = (
        "This request is recap-oriented. Prioritize completed games and include final scores when available.\n"
        if recap_mode
        else ""
    )
    row_target_step = "4a" if (full_slate_mode and include_watch_info) else "3a"
    row_target_instruction = (
        f"{row_target_step}. Target at least {target_rows} distinct game rows when evidence supports that many games.\n"
        if target_rows > 0
        else ""
    )
    overview_instruction = (
        "2. Add one short overview sentence that estimates the full slate size when inferable, highlights the busiest TV windows, and calls out 2-4 marquee or ranked matchups.\n"
        if full_slate_mode and include_watch_info
        else ""
    )
    window_label = "today" if same_day_mode else ("this weekend" if weekend_mode else f"next {lookahead} days")
    reference_instruction = (
        f"Reference date: {reference_date_label} ({reference_timezone}). Interpret relative date phrases such as 'today' and 'tonight' using that local date and timezone.\n"
    )
    same_day_guardrail = (
        "For same-day schedule requests, do not shift the answer to a different calendar day unless the evidence explicitly says the games are tomorrow.\n"
        if same_day_mode
        else ""
    )

    prompt = (
        f"Create a concise sports watch guide for: {subject}\n"
        f"Window: {window_label}.\n"
        f"{reference_instruction}"
        f"{same_day_guardrail}"
        f"{recap_instruction}"
        f"{watch_instruction}\n\n"
        "Output requirements:\n"
        "1. Start with a one-line heading.\n"
        f"{overview_instruction}"
        "3. Provide a markdown table with columns Date | Matchup | Time/Result | Watch | Notes | Sources.\n"
        "4. Sort rows chronologically, preserve ranking indicators in the Matchup cell when the evidence includes them (for example #3 Princeton), and deduplicate duplicate listings across search snippets.\n"
        f"{row_target_instruction}"
        "5. Add 2-4 short notes covering marquee games, streaming caveats, or coverage gaps.\n"
        "6. Do not invent watch details; use TBD when the source does not say.\n\n"
        f"Prioritized evidence (highest trust/freshness first):\n{ranked_evidence_context}\n\n"
        f"Search results:\n{search_results[:40000] if full_slate_mode else search_results[:20000]}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="auto",
                tool_declarations=[],
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "❌ Sports watch guide timed out while generating."
    except Exception as exc:
        log.error("Sports watch guide generation failed: %s", exc)
        return f"❌ Sports watch guide failed: {exc}"

    response_text = response.strip()
    item_count = _count_markdown_table_items(response_text)
    source_domains = {domain for domain in (_extract_domain(str(row.get("url", "")) ) for row in evidence_rows) if domain}
    source_count = max(len(source_domains), len(_extract_distinct_source_domains(search_results)))
    matchup_mentions = _estimate_matchup_mentions(search_results)
    grounding = _compute_evidence_completeness(response_text)
    low_evidence_completeness = (not grounding["fail_safe"]) and grounding["evidence_completeness"] < 0.6
    expected_items, min_sources, context_label = _sports_watch_quality_targets(
        subject=subject,
        lookahead_days=lookahead,
    )
    if full_slate_mode:
        expected_items = max(expected_items, 8)
        min_sources = max(min_sources, 3)
        context_label = "same-day full-slate sports schedule"
    source_feedback = _build_source_diversity_feedback(source_count=source_count, min_sources=min_sources)
    partial_coverage = (
        item_count < expected_items
        or (not source_feedback["floor_met"])
        or (recap_mode and matchup_mentions < expected_items)
        or low_evidence_completeness
    )

    warning_banner = ""
    if partial_coverage:
        warning_banner = source_feedback["warning_text"]
        if not warning_banner:
            shortfall_bits: list[str] = []
            if item_count < expected_items:
                shortfall_bits.append(f"items {item_count}/{expected_items}")
            if recap_mode and matchup_mentions < expected_items:
                shortfall_bits.append(f"search mentions {matchup_mentions}/{expected_items}")
            if low_evidence_completeness:
                shortfall_bits.append(
                    f"evidence completeness {int(round(float(grounding['evidence_completeness']) * 100))}% (<60%)"
                )
            label = "This recap may be incomplete based on item/source thresholds."
            if shortfall_bits:
                label = f"{label} Shortfalls: {', '.join(shortfall_bits)}."
            warning_banner = _build_coverage_shortfall_block(
                label=label,
                scope_hint="narrow to one league/team or a shorter date window, then rerun",
                confidence_hint="Treat this as partial until key details are verified in primary sources",
            )
    if fallback_applied:
        _record_quality_metric("recap_fallback_activation", context="sports_recap")
    if partial_coverage:
        _record_quality_metric("recap_partial_coverage_warning", context="sports_recap")
    if evidence_health["stale_count"] > 0:
        _record_quality_metric("recap_stale_source_signal", context="sports_recap")
    if evidence_health["conflict_groups"] > 0:
        _record_quality_metric("recap_conflict_signal", context="sports_recap")
    if low_evidence_completeness:
        _record_quality_metric("recap_low_evidence_completeness", context="sports_recap")
    if grounding["fail_safe"]:
        _record_quality_metric("recap_evidence_fail_safe", context="sports_recap")

    coverage_summary = "".join(
        [
            "## 📎 Coverage Summary\n",
            f"- Context: {context_label}\n",
            f"- Search game mentions: **{matchup_mentions}**\n",
            f"- Items listed: **{item_count}** (expected ≥ {expected_items})\n",
            f"- Source count: **{source_count}** distinct domains (required ≥ {min_sources})\n",
            str(source_feedback["summary_note"]),
            (
                f"- Coverage shortfall: add **{expected_items - item_count}** more item(s) to hit the target.\n"
                if item_count < expected_items
                else ""
            ),
            (
                f"- Coverage shortfall: add evidence for **{grounding['unsupported_claim_count']}** unsupported claim-like row(s).\n"
                if int(grounding.get("unsupported_claim_count", 0)) > 0
                else ""
            ),
            (
                "- Retry scope hint: narrow to one league/team or a shorter date window, then rerun.\n"
                if partial_coverage
                else ""
            ),
            (
                f"- Evidence completeness: **{int(round(float(grounding['evidence_completeness']) * 100))}%** "
                f"({grounding['supported_claim_count']}/{max(grounding['claim_like_count'], 1)} claim-like rows supported)\n"
            ),
            "".join(_format_evidence_health_lines(evidence_health)),
            f"- Fallback broadening used: {'yes' if fallback_applied else 'no'}\n",
            "- Status: ⚠️ **Partial coverage**\n" if partial_coverage else "- Status: ✅ Coverage thresholds met\n",
        ]
    )

    guarded_response_text = _enforce_uncertainty_wording(
        response_text,
        require_uncertainty=bool(evidence_health.get("strict_uncertainty_required", False))
        or bool(low_evidence_completeness)
        or bool(evidence_health.get("conflict_groups", 0) > 0),
    )
    return f"{warning_banner}{guarded_response_text}\n\n{coverage_summary}\n_via {model_used}_"


async def generate_box_office_report(
    query: str = "",
    days: int = 7,
    output_style: str = "discord-table-detailed",
    emoji_level: str = "light",
    include_new_releases: bool = True,
) -> str:
    """Create a weekly box-office financial report with new-release context."""
    inferred = infer_report_request(
        query or "weekly box office report",
        days=days,
        output_style=output_style,
        emoji_level=emoji_level,
    )
    window_days = max(1, min(int(inferred["days"]), 30))
    timeframe_label = f"last {window_days} day(s)"

    if query.strip():
        search_query = query.strip()
    else:
        search_query = (
            f"box office financials new releases {timeframe_label} "
            "weekend gross domestic worldwide"
        )

    try:
        from skills.search_skills import search_web

        search_results = await asyncio.wait_for(
            search_web(
                search_query,
                num_results=10,
                min_results=6,
                retry_on_low_results=True,
                expand_query=True,
                expansion_context="news_recap",
                provider="perplexity",
            ),
            timeout=45,
        )
    except asyncio.TimeoutError:
        return "❌ Box-office search timed out."
    except Exception as exc:
        log.error("Box-office search failed for %r: %s", search_query, exc)
        return f"❌ Box-office search failed: {exc}"

    if search_results.startswith("❌"):
        return search_results

    parsed_rows = _parse_search_evidence_rows(search_results)
    ranked_evidence = _rank_search_evidence_rows(parsed_rows)
    evidence_rows = _merge_ranked_with_parsed_rows(ranked_evidence, parsed_rows, max_rows=20)
    if not evidence_rows:
        evidence_rows = _filter_near_duplicate_evidence_rows(parsed_rows)
    evidence_health = _summarize_evidence_health(evidence_rows)
    ranked_evidence_context = _format_ranked_evidence_context(evidence_rows, max_items=10)
    source_count = len({domain for domain in (_extract_domain(str(row.get("url", "")) ) for row in evidence_rows) if domain})

    release_requirement = (
        "Include notable new releases from this window in either the table or a follow-up bullet section."
        if include_new_releases
        else "Focus on financial performance only."
    )
    format_instructions = _report_format_instructions(
        str(inferred["output_style"]),
        str(inferred["emoji_level"]),
    )

    prompt = (
        "Create a Discord-ready weekly box-office report.\n"
        f"Time window: {timeframe_label}.\n"
        f"{release_requirement}\n"
        f"{format_instructions}\n\n"
        "Output requirements:\n"
        "1. Use GitHub-flavored markdown.\n"
        "2. Include at least one markdown table.\n"
        "3. Include financial columns with available values: Weekend Gross, Domestic Total, Worldwide Total.\n"
        "4. Use N/A explicitly for unavailable values.\n"
        "5. Include a short section named 'Sources'.\n"
        "6. Keep the response concise and readable in Discord.\n\n"
        f"Prioritized evidence (highest trust/freshness first):\n{ranked_evidence_context}\n\n"
        f"Search results:\n{search_results[:14000]}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="auto",
                tool_declarations=[],
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "❌ Box-office report timed out while generating."
    except Exception as exc:
        log.error("Box-office report generation failed: %s", exc)
        return f"❌ Box-office report failed: {exc}"

    report = _append_report_guardrails(
        response.strip(),
        timeframe_label=timeframe_label,
        require_table=True,
    )
    item_count = _count_markdown_table_items(report)
    grounding = _compute_evidence_completeness(report)
    low_evidence_completeness = (not grounding["fail_safe"]) and grounding["evidence_completeness"] < 0.6
    min_sources = 2
    source_feedback = _build_source_diversity_feedback(source_count=source_count, min_sources=min_sources)
    partial_coverage = (not source_feedback["floor_met"]) or low_evidence_completeness
    if evidence_health["stale_count"] > 0:
        _record_quality_metric("report_stale_source_signal", context="news_recap")
    if evidence_health["conflict_groups"] > 0:
        _record_quality_metric("report_conflict_signal", context="news_recap")
    if low_evidence_completeness:
        _record_quality_metric("report_low_evidence_completeness", context="news_recap")
    if grounding["fail_safe"]:
        _record_quality_metric("report_evidence_fail_safe", context="news_recap")
    report = _enforce_uncertainty_wording(
        report,
        require_uncertainty=bool(evidence_health.get("strict_uncertainty_required", False))
        or bool(low_evidence_completeness)
        or bool(evidence_health.get("conflict_groups", 0) > 0),
    )
    warning_banner = ""
    if partial_coverage:
        warning_banner = source_feedback["warning_text"]
        if not warning_banner:
            label = "This report may be incomplete based on evidence/source thresholds."
            if low_evidence_completeness:
                label = (
                    f"{label} Evidence completeness is "
                    f"{int(round(float(grounding['evidence_completeness']) * 100))}% (<60%)."
                )
            warning_banner = _build_coverage_shortfall_block(
                label=label,
                scope_hint="tighten the timeframe or focus on top titles, then rerun",
                confidence_hint="Treat these figures as provisional until cross-checked with additional sources",
            )
    coverage_summary = "".join(
        [
            "## 📎 Coverage Summary\n",
            f"- Context: box office report ({timeframe_label})\n",
            f"- Items listed: **{item_count}**\n",
            f"- Source count: **{source_count}** distinct domains (required ≥ {min_sources})\n",
            str(source_feedback["summary_note"]),
            (
                f"- Coverage shortfall: add evidence for **{grounding['unsupported_claim_count']}** unsupported claim-like row(s).\n"
                if int(grounding.get("unsupported_claim_count", 0)) > 0
                else ""
            ),
            (
                "- Retry scope hint: tighten the timeframe or focus on top titles, then rerun.\n"
                if partial_coverage
                else ""
            ),
            (
                f"- Evidence completeness: **{int(round(float(grounding['evidence_completeness']) * 100))}%** "
                f"({grounding['supported_claim_count']}/{max(grounding['claim_like_count'], 1)} claim-like rows supported)\n"
            ),
            "".join(_format_evidence_health_lines(evidence_health)),
            "- Status: ⚠️ **Partial coverage**\n" if partial_coverage else "- Status: ✅ Coverage thresholds met\n",
        ]
    )
    return f"{warning_banner}{report}\n\n{coverage_summary}\n\n_via {model_used}_"


async def generate_weekly_recap(
    topics: list[str] | None = None,
    date_range: str = "last_week",
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """
    Generate a unified weekly recap aggregating data from multiple premium APIs.

    Combines NewsAPI, API-Sports (NBA), and Alpha Vantage (stocks/sentiment) into
    a comprehensive Discord-ready markdown report.

    Args:
        topics: List of topics to include. Options: ["entertainment", "sports", "tech", "finance", "general"]
                If None, includes all available topics.
        date_range: Preset range - "last_week" (7 days), "last_3_days", "last_month" (30 days), "custom"
        from_date: Custom start date in YYYY-MM-DD format (required if date_range="custom")
        to_date: Custom end date in YYYY-MM-DD format (optional, defaults to today)

    Returns:
        Markdown-formatted report with sections:
        - News Highlights (by topic)
        - Sports Recap (NBA scores/standings if "sports" in topics)
        - Financial Summary (stocks/sentiment if "finance" or "entertainment" in topics)
        - Key Trends (notable patterns)
        - Sources (API citations)

    Rate limits:
        - NewsAPI: 100 req/day (uses ~1-5 per call depending on topics)
        - API-Sports: 100 req/day (uses ~2 if sports enabled)
        - Alpha Vantage: 25 req/day (uses ~1-3 if finance enabled)

    Example:
        >>> recap = await generate_weekly_recap(
        ...     topics=["tech", "sports", "finance"],
        ...     date_range="last_3_days"
        ... )
    """
    # Default to all topics if none specified
    if topics is None:
        topics = ["entertainment", "sports", "tech", "finance", "general"]

    # Normalize topics to lowercase
    topics = [t.lower() for t in topics]

    # Calculate date range
    today = dt.datetime.now()
    if date_range == "custom":
        if not from_date:
            return "❌ Error: from_date required when date_range='custom'"
        end_date_str = to_date or today.strftime("%Y-%m-%d")
    elif date_range == "last_3_days":
        (today - dt.timedelta(days=3)).strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")
        range_label = "Last 3 Days"
    elif date_range == "last_month":
        (today - dt.timedelta(days=30)).strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")
        range_label = "Last 30 Days"
    else:  # last_week (default)
        (today - dt.timedelta(days=7)).strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")
        range_label = "Last 7 Days"

    if date_range == "custom":
        range_label = f"{from_date} to {end_date_str}"

    # Track which sources succeeded/failed
    sources_used = []
    sources_failed = []

    # Initialize report sections
    report_sections = []
    report_sections.append(f"# 📊 Weekly Recap: {range_label}\n")
    report_sections.append(f"*Generated {today.strftime('%B %d, %Y at %I:%M %p')}*\n")

    # ========== NEWS HIGHLIGHTS SECTION ==========
    news_articles_by_topic = {}
    news_total_count = 0
    sports_games_count = 0
    standings_count = 0
    stock_entries_count = 0
    market_news_count = 0

    for topic in topics:
        if topic in ["entertainment", "tech", "finance", "general", "sports"]:
            try:
                # Map topic to NewsAPI category
                category_map = {
                    "entertainment": "entertainment",
                    "tech": "technology",
                    "finance": "business",
                    "sports": "sports",
                    "general": "general",
                }

                category = category_map.get(topic, "general")

                # Use top_headlines for better relevance
                news_result = await asyncio.wait_for(
                    news_skills.top_headlines(
                        category=category,
                        page_size=5,
                    ),
                    timeout=15,
                )

                if news_result.get("status") == "ok" and news_result.get("articles"):
                    news_articles_by_topic[topic] = news_result["articles"]
                    news_total_count += len(news_result["articles"])
                    sources_used.append(f"NewsAPI ({category})")
                elif "rate limit" in news_result.get("message", "").lower():
                    sources_failed.append(f"NewsAPI ({category}) - rate limit")
                    log.warning(f"NewsAPI rate limit hit for {category}")
                else:
                    log.debug(f"No news articles for {category}")

            except asyncio.TimeoutError:
                sources_failed.append(f"NewsAPI ({topic}) - timeout")
                log.error(f"NewsAPI timeout for {topic}")
            except Exception as e:
                sources_failed.append(f"NewsAPI ({topic}) - {str(e)[:50]}")
                log.error(f"NewsAPI error for {topic}: {e}")

    # Format news section
    if news_articles_by_topic:
        report_sections.append("## 🗞️ News Highlights\n")

        for topic, articles in news_articles_by_topic.items():
            if articles:
                topic_emoji = {
                    "entertainment": "🎬",
                    "tech": "💻",
                    "finance": "💰",
                    "sports": "⚽",
                    "general": "📰",
                }.get(topic, "📌")

                report_sections.append(f"### {topic_emoji} {topic.title()}\n")

                for article in articles[:3]:  # Top 3 per topic
                    title = article.get("title", "Untitled")
                    source = article.get("source", {}).get("name", "Unknown")
                    url = article.get("url", "")
                    description = (article.get("description") or "")[:150]

                    # Format: - **Title** - Source
                    #         Description... [Read more](url)
                    report_sections.append(f"- **{title}** - *{source}*\n")
                    if description:
                        report_sections.append(f"  {description}... [Read more]({url})\n")

                report_sections.append("\n")

    # ========== SPORTS RECAP SECTION ==========
    if "sports" in topics:
        report_sections.append("## 🏀 Sports Recap\n")

        # Get yesterday's date for score lookups
        yesterday = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")

        async def _fetch_scores(label: str, coro, emoji: str) -> None:
            """Fetch scores for a single sport and append to report_sections."""
            nonlocal sports_games_count
            try:
                scores = await asyncio.wait_for(coro, timeout=15)
                if scores.get("status") == "ok" and scores.get("games"):
                    report_sections.append(f"### {emoji} {label} Scores ({yesterday})\n")
                    for game in scores["games"][:5]:
                        home = game["teams"]["home"]
                        away = game["teams"]["away"]
                        status = game.get("status", "Unknown")
                        report_sections.append(
                            f"- **{away['name']}** {away['score']} @ "
                            f"**{home['name']}** {home['score']} - *{status}*\n"
                        )
                    report_sections.append("\n")
                    sources_used.append(f"API-Sports ({label} Scores)")
                    sports_games_count += len(scores["games"][:5])
                elif "rate limit" in scores.get("message", "").lower():
                    sources_failed.append(f"API-Sports ({label}) - rate limit")
                    report_sections.append(f"*{label} scores unavailable (rate limit)*\n\n")
            except asyncio.TimeoutError:
                sources_failed.append(f"API-Sports ({label}) - timeout")
                report_sections.append(f"*{label} scores unavailable (timeout)*\n\n")
            except Exception as e:
                sources_failed.append(f"API-Sports ({label}) - {str(e)[:50]}")
                log.error(f"{label} API error: {e}")

        # NBA (Oct–Jun)
        if sports_skills.is_sport_in_season("nba"):
            await _fetch_scores("NBA", sports_skills.get_nba_scores(date=yesterday), "🏀")

        # NFL (Sep–Feb)
        if sports_skills.is_sport_in_season("nfl"):
            await _fetch_scores("NFL", sports_skills.get_nfl_scores(date=yesterday), "🏈")

        # NHL (Oct–Jun)
        if sports_skills.is_sport_in_season("nhl"):
            await _fetch_scores("NHL", sports_skills.get_nhl_scores(date=yesterday), "🏒")

        # MLB (Mar–Oct)
        if sports_skills.is_sport_in_season("mlb"):
            await _fetch_scores("MLB", sports_skills.get_mlb_scores(date=yesterday), "⚾")

        # If no sport is in season, say so
        if not any(
            sports_skills.is_sport_in_season(s) for s in ("nba", "nfl", "nhl", "mlb")
        ):
            report_sections.append("*No major sports leagues currently in season.*\n\n")

        # Standings for in-season sports (pick the most prominent active sport)
        standings_sport = next(
            (s for s in ("nba", "nfl", "nhl", "mlb") if sports_skills.is_sport_in_season(s)),
            None,
        )
        if standings_sport:
            sport_labels = {"nba": ("🏀", "NBA"), "nfl": ("🏈", "NFL"), "nhl": ("🏒", "NHL"), "mlb": ("⚾", "MLB")}
            emoji, label = sport_labels[standings_sport]
            try:
                standings = await asyncio.wait_for(
                    sports_skills.get_team_standings(sport=standings_sport),
                    timeout=15,
                )
                if standings.get("status") == "ok" and standings.get("standings"):
                    report_sections.append(f"### {emoji} {label} Standings (Top 5)\n")
                    for team in standings["standings"][:5]:
                        rank = team.get("rank", "?")
                        name = team.get("team", "Unknown")
                        wins = team.get("wins", 0)
                        losses = team.get("losses", 0)
                        report_sections.append(f"{rank}. **{name}** - {wins}W-{losses}L\n")
                    report_sections.append("\n")
                    sources_used.append(f"API-Sports ({label} Standings)")
                    standings_count += len(standings["standings"][:5])
                elif "rate limit" not in standings.get("message", "").lower():
                    log.debug(f"No {label} standings available")
            except asyncio.TimeoutError:
                log.error(f"{label} standings timeout")
            except Exception as e:
                log.error(f"{label} standings error: {e}")

    # ========== FINANCIAL SUMMARY SECTION ==========
    if "finance" in topics or "entertainment" in topics:
        report_sections.append("## 💰 Financial Summary\n")

        # Get entertainment stocks if entertainment topic included
        if "entertainment" in topics:
            try:
                box_office_stocks = await asyncio.wait_for(
                    finance_skills.get_box_office_stocks(),
                    timeout=20,
                )

                if box_office_stocks.get("status") == "ok" and box_office_stocks.get("studios"):
                    report_sections.append("### 🎬 Entertainment Stocks\n")

                    for studio, data in box_office_stocks["studios"].items():
                        if "error" not in data:
                            symbol = data.get("symbol", "")
                            price = data.get("price", 0)
                            change = data.get("change", "N/A")

                            # Format with color indicator
                            indicator = "🟢" if "+" in str(change) else "🔴" if "-" in str(change) else "⚪"
                            report_sections.append(f"- {indicator} **{studio}** ({symbol}): ${price:.2f} ({change})\n")
                            stock_entries_count += 1

                    report_sections.append("\n")
                    sources_used.append("Alpha Vantage (Entertainment Stocks)")
                elif "rate limit" in box_office_stocks.get("message", "").lower():
                    sources_failed.append("Alpha Vantage (Stocks) - rate limit")
                    report_sections.append("*Stock data unavailable (rate limit)*\n\n")
            except asyncio.TimeoutError:
                sources_failed.append("Alpha Vantage (Stocks) - timeout")
                report_sections.append("*Stock data unavailable (timeout)*\n\n")
            except Exception as e:
                sources_failed.append(f"Alpha Vantage (Stocks) - {str(e)[:50]}")
                log.error(f"Finance API error: {e}")

        # Get market news with sentiment
        if "finance" in topics:
            try:
                market_news = await asyncio.wait_for(
                    finance_skills.get_market_news(
                        topics="financial_markets,technology",
                        limit=3,
                    ),
                    timeout=15,
                )

                if market_news.get("status") == "ok" and market_news.get("feed"):
                    report_sections.append("### 📈 Market News & Sentiment\n")

                    market_items = market_news["feed"][:3]
                    market_news_count += len(market_items)
                    for article in market_items:
                        title = article.get("title", "Untitled")
                        sentiment = article.get("sentiment", {})
                        sentiment_label = sentiment.get("label", "Neutral")
                        sentiment_score = sentiment.get("score", 0)
                        source = article.get("source", "Unknown")

                        # Sentiment emoji
                        sent_emoji = "🟢" if sentiment_score > 0.15 else "🔴" if sentiment_score < -0.15 else "⚪"

                        report_sections.append(
                            f"- {sent_emoji} **{title}** - *{source}*\n"
                            f"  Sentiment: {sentiment_label} ({sentiment_score:.2f})\n"
                        )

                    report_sections.append("\n")
                    sources_used.append("Alpha Vantage (Market News)")
                elif "rate limit" not in market_news.get("message", "").lower():
                    log.debug("No market news available")
            except asyncio.TimeoutError:
                log.error("Market news timeout")
            except Exception as e:
                log.error(f"Market news error: {e}")

    # ========== KEY TRENDS SECTION ==========
    report_sections.append("## 📊 Key Trends\n")

    # Generate brief insights
    insights = []

    if news_total_count > 0:
        insights.append(f"- Collected **{news_total_count}** news articles across {len(news_articles_by_topic)} categories")

    if sources_failed:
        insights.append(f"- ⚠️ **{len(sources_failed)}** data sources unavailable (see below)")

    if not insights:
        insights.append("- All requested data sources processed successfully")

    report_sections.extend([f"{insight}\n" for insight in insights])
    report_sections.append("\n")

    # ========== COVERAGE SUMMARY SECTION ==========
    distinct_sources = sorted(set(sources_used))
    source_count = len(distinct_sources)
    total_items = (
        news_total_count + sports_games_count + standings_count + stock_entries_count + market_news_count
    )
    expected_min_items = 6 if date_range == "last_week" else 3
    expected_min_sources = 2 if len(topics) >= 2 else 1
    partial_coverage = total_items < expected_min_items or source_count < expected_min_sources

    report_sections.append("## 📎 Coverage Summary\n")
    report_sections.append(f"- Items captured: **{total_items}** (expected ≥ {expected_min_items})\n")
    report_sections.append(f"- Source count: **{source_count}** distinct providers (required ≥ {expected_min_sources})\n")
    if partial_coverage:
        missing_items = max(expected_min_items - total_items, 0)
        missing_sources = max(expected_min_sources - source_count, 0)
        report_sections.append(
            _build_coverage_shortfall_block(
                label=(
                    f"Coverage targets missed: items {total_items}/{expected_min_items}, "
                    f"sources {source_count}/{expected_min_sources}."
                ),
                scope_hint="limit topics or shorten date range and regenerate",
                confidence_hint="Use this recap as directional until missing coverage is filled",
            )
        )
        if missing_items > 0:
            report_sections.append(f"- Coverage shortfall: add **{missing_items}** more item(s).\n")
        if missing_sources > 0:
            report_sections.append(f"- Coverage shortfall: add **{missing_sources}** more source provider(s).\n")
        report_sections.append("- Retry scope hint: limit topics or shorten date range and regenerate.\n")
        report_sections.append("- Status: ⚠️ **Partial coverage**\n")
    else:
        report_sections.append("- Status: ✅ Coverage thresholds met\n")
    report_sections.append("\n")

    # ========== SOURCES SECTION ==========
    report_sections.append("## 📚 Data Sources\n")

    if sources_used:
        report_sections.append("**Active:**\n")
        for source in set(sources_used):
            report_sections.append(f"- ✅ {source}\n")
        report_sections.append("\n")

    if sources_failed:
        report_sections.append("**Unavailable:**\n")
        for source in sources_failed:
            report_sections.append(f"- ❌ {source}\n")
        report_sections.append("\n")

    # Add rate limit info
    report_sections.append(
        "**Rate Limits:**\n"
        "- NewsAPI: 100 req/day\n"
        "- API-Sports: 100 req/day\n"
        "- Alpha Vantage: 25 req/day\n\n"
    )

    # ========== FOOTER ==========
    report_sections.append("---\n")
    report_sections.append("*Generated by OpenClaw Weekly Recap Engine*\n")

    # Combine all sections
    full_report = "".join(report_sections)

    # Check Discord embed limits (2000 chars per field recommended, 6000 total)
    # Split into multiple messages if needed
    if len(full_report) > 5500:
        log.warning(f"Report length ({len(full_report)} chars) exceeds Discord optimal length")
        full_report += "\n*⚠️ Note: Report may need to be split across multiple Discord messages*\n"

    return full_report


REPORTING_SKILLS = {
    "generate_channel_recap_report": generate_channel_recap_report,
    "generate_sports_watch_report": generate_sports_watch_report,
    "generate_box_office_report": generate_box_office_report,
    "generate_weekly_recap": generate_weekly_recap,
}
