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
