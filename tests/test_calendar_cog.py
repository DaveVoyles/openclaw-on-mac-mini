"""Tests for cogs/calendar_cog.py."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

import cog_helpers as _ch

_orig_require_auth = _ch.require_auth


def _noop_auth(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    return lambda f: f


_ch.require_auth = _noop_auth

import cogs.calendar_cog as mod

_ch.require_auth = _orig_require_auth


class _FakeTree:
    def add_command(self, *a, **k):
        pass

    def remove_command(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_interaction(user_id=1, done=False):
    inter = AsyncMock()
    inter.user.id = user_id
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = 100
    inter.guild_id = 999
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.followup.send = AsyncMock()
    return inter


def _make_cog():
    return mod.CalendarCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── calendar_today ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_today_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.get_todays_events", new=AsyncMock(return_value="Meeting at 10am")):
        await cog.calendar_today.callback(cog, inter)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_calendar_today_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.get_todays_events", new=AsyncMock(side_effect=RuntimeError("Calendar unavailable"))):
        await cog.calendar_today.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    call_args = inter.followup.send.call_args[0][0]
    assert "❌" in call_args


# ── calendar_upcoming ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_upcoming_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.get_upcoming_events", new=AsyncMock(return_value="Team sync on Friday")):
        await cog.calendar_upcoming.callback(cog, inter, days=7)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_calendar_upcoming_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.get_upcoming_events", new=AsyncMock(side_effect=Exception("API error"))):
        await cog.calendar_upcoming.callback(cog, inter, days=14)

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── calendar_add ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_add_success():
    cog = _make_cog()
    inter = _make_interaction()

    import datetime
    fake_dt = datetime.datetime(2025, 1, 15, 14, 0)

    with patch("dateutil.parser.parse", return_value=fake_dt), \
         patch("calendar_skills.create_calendar_event", new=AsyncMock(return_value="Event created")):
        await cog.calendar_add.callback(
            cog, inter,
            title="Team Meeting",
            when="Wednesday 2pm",
            description="Quarterly sync",
            location="Zoom",
        )

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_calendar_add_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("dateutil.parser.parse", side_effect=ValueError("Cannot parse")):
        await cog.calendar_add.callback(
            cog, inter,
            title="Meeting",
            when="invalid date",
        )

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── calendar_delete ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calendar_delete_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.delete_calendar_event", new=AsyncMock(return_value="Event deleted")):
        await cog.calendar_delete.callback(cog, inter, event_id="abc123")

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_calendar_delete_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("calendar_skills.delete_calendar_event", new=AsyncMock(side_effect=Exception("Not found"))):
        await cog.calendar_delete.callback(cog, inter, event_id="badid")

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]
