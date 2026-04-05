"""Tests for reporting_skills.py."""

import datetime as dt
from types import SimpleNamespace

import pytest

from skills import reporting_skills as mod


def _msg(
    author_name: str,
    content: str,
    *,
    bot: bool = False,
    attachments: list[str] | None = None,
):
    author = SimpleNamespace(display_name=author_name, name=author_name, bot=bot)
    attachment_objs = [SimpleNamespace(filename=name) for name in (attachments or [])]
    return SimpleNamespace(
        author=author,
        content=content,
        clean_content=content,
        attachments=attachment_objs,
        created_at=dt.datetime(2026, 4, 1, 12, 30, tzinfo=dt.timezone.utc),
    )


def test_build_sports_watch_query_prefers_explicit_query():
    query = mod.build_sports_watch_query(
        query="men's division 1 college lacrosse this week",
        sport="lacrosse",
        league="NCAA",
        team="Maryland",
        days=7,
    )
    assert query == "men's division 1 college lacrosse this week"


def test_build_sports_watch_query_assembles_structured_inputs():
    query = mod.build_sports_watch_query(
        sport="college lacrosse",
        league="NCAA Division 1",
        team="Maryland",
        days=5,
    )
    assert "Maryland NCAA Division 1 college lacrosse" in query
    assert "next 5 days" in query


def test_infer_sports_request_extracts_slots_from_plain_english():
    slots = mod.infer_sports_request(
        "What games does Maryland have in the next 5 days in men's division 1 lacrosse?"
    )
    assert slots["team"] == "Maryland"
    assert slots["sport"] == "lacrosse"
    assert slots["league"] == "NCAA Division 1"
    assert slots["days"] == 5


def test_infer_report_request_extracts_format_and_window_hints():
    slots = mod.infer_report_request(
        "Give me the box office financials and new releases for the last week in table form with emojis."
    )
    assert slots["topic"] == "box office"
    assert slots["days"] == 7
    assert slots["output_style"] == "discord-table-detailed"
    assert slots["emoji_level"] in {"light", "rich"}


def test_append_report_guardrails_adds_required_sections():
    report = mod._append_report_guardrails(
        "## Weekly box office recap\n\nTop titles moved this week.",
        timeframe_label="last 7 day(s)",
    )
    assert "Time window: last 7 day(s)" in report
    assert "| Item | Metric | Value | Notes |" in report
    assert "Sources" in report
    assert "N/A" in report


def test_format_message_history_skips_bot_messages_and_keeps_attachments():
    transcript = mod._format_message_history(
        [
            _msg("OpenClaw", "Automated note", bot=True),
            _msg("Dave", "Need a weekly recap of sports updates", attachments=["schedule.png"]),
            _msg("Pat", "Let's track the ESPN games list."),
        ],
        max_chars=1000,
    )
    assert "OpenClaw" not in transcript
    assert "Dave" in transcript
    assert "schedule.png" in transcript
    assert "Pat" in transcript


@pytest.mark.asyncio
async def test_generate_channel_recap_report_requires_live_bot(monkeypatch):
    monkeypatch.setattr(mod, "get_bot", lambda: None)
    result = await mod.generate_channel_recap_report(channel_id=1234)
    assert "not available yet" in result


@pytest.mark.asyncio
async def test_generate_channel_recap_report_uses_bound_channel_context(monkeypatch):
    monkeypatch.setattr(mod, "get_bot", lambda: None)
    monkeypatch.setattr(mod, "get_current_channel_id", lambda: 4321)
    result = await mod.generate_channel_recap_report(channel_id=None)
    assert "not available yet" in result


@pytest.mark.asyncio
async def test_generate_channel_recap_report_without_context_guides_user(monkeypatch):
    monkeypatch.setattr(mod, "get_current_channel_id", lambda: None)
    result = await mod.generate_channel_recap_report(channel_id=None)
    assert "No Discord channel context" in result
