"""Reporting skills for Discord recaps and sports watch guides."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import Any, Iterable

import discord

from runtime_state import get_bot, get_current_channel_id

log = logging.getLogger("openclaw.reporting_skills")

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


def _normalize_style(style: str) -> str:
    value = (style or "highlights").strip().lower()
    return value if value in _RECAP_STYLES else "highlights"


def _clean_text(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


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
    days: int = 7,
    focus: str = "",
    style: str = "highlights",
    max_messages: int = 200,
) -> str:
    """Summarize the recent activity in a Discord channel or thread."""
    if channel_id in (None, "", 0, "0"):
        channel_id = get_current_channel_id()

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

    prompt = (
        f"You are writing a weekly Discord recap for #{channel_name}.\n"
        f"Time window: last {window_days} days.\n"
        f"Focus: {focus_text}\n"
        f"Style: {style_key}\n\n"
        f"{style_instructions}\n"
        "Use GitHub-flavored Markdown. Be specific, concise, and do not invent details.\n"
        "If there are no decisions or action items, say so explicitly.\n\n"
        f"Discord transcript:\n{transcript}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="gemini",
                tool_declarations=[],
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "❌ Weekly recap timed out while generating."
    except Exception as exc:
        log.error("Weekly recap generation failed: %s", exc)
        return f"❌ Weekly recap failed: {exc}"

    return (
        f"## Weekly recap for #{channel_name}\n\n"
        f"{response.strip()}\n\n"
        f"_Reviewed {len(messages)} messages from the last {window_days} day(s) via {model_used}_"
    )


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
    base_query = build_sports_watch_query(
        query=query,
        sport=str(inferred["sport"]),
        league=str(inferred["league"]),
        team=str(inferred["team"]),
        days=lookahead,
    )
    search_query = base_query
    if include_watch_info:
        search_query += " TV schedule where to watch streaming ESPN NCAA"

    try:
        from skills.search_skills import search_web

        search_results = await asyncio.wait_for(search_web(search_query, num_results=8), timeout=45)
    except asyncio.TimeoutError:
        return "❌ Sports search timed out."
    except Exception as exc:
        log.error("Sports search failed for %r: %s", search_query, exc)
        return f"❌ Sports search failed: {exc}"

    if search_results.startswith("❌"):
        return search_results

    subject = (
        query.strip()
        or " ".join(
            bit for bit in (
                str(inferred["team"]),
                str(inferred["league"]),
                str(inferred["sport"]),
            ) if bit
        ).strip()
        or "Upcoming games"
    )
    watch_instruction = (
        "Include a Watch column with TV channel, streaming service, or 'TBD' when not available."
        if include_watch_info
        else "Include a Notes column instead of Watch information."
    )

    prompt = (
        f"Create a concise sports watch guide for: {subject}\n"
        f"Window: next {lookahead} days.\n"
        f"{watch_instruction}\n\n"
        "Output requirements:\n"
        "1. Start with a one-line heading.\n"
        "2. Provide a markdown table with columns Date | Matchup | Time (ET) | Watch | Notes.\n"
        "3. Add 2-4 short notes for uncertainties, ranked games, or streaming caveats.\n"
        "4. Do not invent watch details; use TBD when the source does not say.\n\n"
        f"Search results:\n{search_results[:12000]}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="gemini",
                tool_declarations=[],
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "❌ Sports watch guide timed out while generating."
    except Exception as exc:
        log.error("Sports watch guide generation failed: %s", exc)
        return f"❌ Sports watch guide failed: {exc}"

    return f"{response.strip()}\n\n_via {model_used}_"


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

        search_results = await asyncio.wait_for(search_web(search_query, num_results=10), timeout=45)
    except asyncio.TimeoutError:
        return "❌ Box-office search timed out."
    except Exception as exc:
        log.error("Box-office search failed for %r: %s", search_query, exc)
        return f"❌ Box-office search failed: {exc}"

    if search_results.startswith("❌"):
        return search_results

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
        f"Search results:\n{search_results[:14000]}"
    )

    try:
        from llm import chat as llm_chat

        response, _, model_used = await asyncio.wait_for(
            llm_chat(
                user_message=prompt,
                model_preference="gemini",
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
    return f"{report}\n\n_via {model_used}_"


REPORTING_SKILLS = {
    "generate_channel_recap_report": generate_channel_recap_report,
    "generate_sports_watch_report": generate_sports_watch_report,
    "generate_box_office_report": generate_box_office_report,
}
