"""Reporting skills for Discord recaps and sports watch guides."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
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


def _normalize_style(style: str) -> str:
    value = (style or "highlights").strip().lower()
    return value if value in _RECAP_STYLES else "highlights"


def _clean_text(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


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


async def generate_sports_watch_report(
    query: str = "",
    sport: str = "",
    league: str = "",
    team: str = "",
    days: int = 7,
    include_watch_info: bool = True,
) -> str:
    """Create a structured sports watch guide from web search results."""
    lookahead = max(1, min(int(days), 14))
    base_query = build_sports_watch_query(
        query=query,
        sport=sport,
        league=league,
        team=team,
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

    subject = query.strip() or " ".join(bit for bit in (team, league, sport) if bit).strip() or "Upcoming games"
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


REPORTING_SKILLS = {
    "generate_channel_recap_report": generate_channel_recap_report,
    "generate_sports_watch_report": generate_sports_watch_report,
}
