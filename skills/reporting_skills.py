"""Reporting skills for Discord recaps and sports watch guides."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import Any, Iterable

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

    recap_output = (
        f"## Weekly recap for #{channel_name}\n\n"
        f"{response.strip()}\n\n"
        f"_Reviewed {len(messages)} messages from the last {window_days} day(s) via {model_used}_"
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

        # Get NBA scores from yesterday
        yesterday = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            nba_scores = await asyncio.wait_for(
                sports_skills.get_nba_scores(date=yesterday),
                timeout=15,
            )

            if nba_scores.get("status") == "ok" and nba_scores.get("games"):
                report_sections.append(f"### NBA Scores ({yesterday})\n")

                for game in nba_scores["games"][:5]:  # Top 5 games
                    home = game["teams"]["home"]
                    away = game["teams"]["away"]
                    status = game.get("status", "Unknown")

                    report_sections.append(
                        f"- **{away['name']}** {away['score']} @ "
                        f"**{home['name']}** {home['score']} - *{status}*\n"
                    )

                report_sections.append("\n")
                sources_used.append("API-Sports (NBA Scores)")
            elif "rate limit" in nba_scores.get("message", "").lower():
                sources_failed.append("API-Sports (NBA) - rate limit")
                report_sections.append("*NBA scores unavailable (rate limit)*\n\n")
        except asyncio.TimeoutError:
            sources_failed.append("API-Sports (NBA) - timeout")
            report_sections.append("*NBA scores unavailable (timeout)*\n\n")
        except Exception as e:
            sources_failed.append(f"API-Sports (NBA) - {str(e)[:50]}")
            log.error(f"Sports API error: {e}")

        # Get NBA standings
        try:
            standings = await asyncio.wait_for(
                sports_skills.get_team_standings(sport="nba"),
                timeout=15,
            )

            if standings.get("status") == "ok" and standings.get("standings"):
                report_sections.append("### NBA Standings (Top 5)\n")

                for team in standings["standings"][:5]:
                    rank = team.get("rank", "?")
                    name = team.get("team", "Unknown")
                    wins = team.get("wins", 0)
                    losses = team.get("losses", 0)

                    report_sections.append(f"{rank}. **{name}** - {wins}W-{losses}L\n")

                report_sections.append("\n")
                sources_used.append("API-Sports (NBA Standings)")
            elif "rate limit" not in standings.get("message", "").lower():
                log.debug("No NBA standings available")
        except asyncio.TimeoutError:
            log.error("NBA standings timeout")
        except Exception as e:
            log.error(f"NBA standings error: {e}")

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

                    for article in market_news["feed"][:3]:
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
