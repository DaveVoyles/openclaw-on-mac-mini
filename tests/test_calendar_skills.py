"""Tests for calendar_skills.py — Google Calendar OAuth2 skill."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import calendar_skills

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resp(status=200, json_data=None, text_data=""):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value=text_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(resp):
    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.post = MagicMock(return_value=resp)
    session.delete = MagicMock(return_value=resp)
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_creds(monkeypatch):
    """Ensure OAuth creds are present and token cache is clean for every test."""
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(calendar_skills, "GOOGLE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setattr(calendar_skills, "_access_token_cache", None)
    monkeypatch.setattr(calendar_skills, "_access_token_expiry", 0.0)


# ---------------------------------------------------------------------------
# _not_configured
# ---------------------------------------------------------------------------

def test_not_configured_message():
    result = calendar_skills._not_configured()
    assert "❌" in result
    assert "GOOGLE_OAUTH_CLIENT_ID" in result


# ---------------------------------------------------------------------------
# _fmt_event_time
# ---------------------------------------------------------------------------

def test_fmt_event_time_datetime():
    result = calendar_skills._fmt_event_time({"dateTime": "2026-03-25T14:00:00Z"})
    assert "Mar" in result
    assert "25" in result


def test_fmt_event_time_date_only():
    result = calendar_skills._fmt_event_time({"date": "2026-03-25"})
    assert result == "2026-03-25"


def test_fmt_event_time_missing_start():
    result = calendar_skills._fmt_event_time({})
    assert result == "?"


def test_fmt_event_time_invalid_iso_falls_back():
    # "T" present but not parseable → raw[:16]
    raw = "2026-99-99TBROKE"
    result = calendar_skills._fmt_event_time({"dateTime": raw})
    assert result == raw[:16]


def test_fmt_event_time_no_T_returns_raw():
    result = calendar_skills._fmt_event_time({"dateTime": "noThere"})
    assert result == "noThere"


# ---------------------------------------------------------------------------
# _get_access_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_access_token_not_configured(monkeypatch):
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_ID", "")
    result = await calendar_skills._get_access_token()
    assert result is None


@pytest.mark.asyncio
async def test_get_access_token_uses_cache(monkeypatch):
    monkeypatch.setattr(calendar_skills, "_access_token_cache", "cached-tok")
    monkeypatch.setattr(calendar_skills, "_access_token_expiry", time.monotonic() + 1000)
    result = await calendar_skills._get_access_token()
    assert result == "cached-tok"


@pytest.mark.asyncio
async def test_get_access_token_fetches_new():
    resp = _make_resp(200, {"access_token": "fresh-tok", "expires_in": 3600})
    session = _make_session(resp)
    with patch("calendar_skills._get_session", AsyncMock(return_value=session)):
        result = await calendar_skills._get_access_token()
    assert result == "fresh-tok"


@pytest.mark.asyncio
async def test_get_access_token_non_200_returns_none():
    resp = _make_resp(401, text_data="invalid_client")
    session = _make_session(resp)
    with patch("calendar_skills._get_session", AsyncMock(return_value=session)):
        result = await calendar_skills._get_access_token()
    assert result is None


@pytest.mark.asyncio
async def test_get_access_token_network_exception_returns_none():
    with patch("calendar_skills._get_session", AsyncMock(side_effect=Exception("timeout"))):
        result = await calendar_skills._get_access_token()
    assert result is None


# ---------------------------------------------------------------------------
# get_upcoming_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_upcoming_events_not_configured(monkeypatch):
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_ID", "")
    result = await calendar_skills.get_upcoming_events()
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_get_upcoming_events_no_token():
    with patch("calendar_skills._get_access_token", AsyncMock(return_value=None)):
        result = await calendar_skills.get_upcoming_events()
    assert "Failed to obtain" in result


@pytest.mark.asyncio
async def test_get_upcoming_events_success():
    data = {
        "items": [
            {"summary": "Team Standup", "start": {"dateTime": "2026-03-25T09:00:00Z"}},
            {"summary": "Lunch", "start": {"date": "2026-03-25"}},
        ]
    }
    resp = _make_resp(200, data)
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
        patch("calendar_skills._truncate", lambda text, *a, **k: text),
    ):
        result = await calendar_skills.get_upcoming_events(days=7)
    assert "Team Standup" in result
    assert "Lunch" in result


@pytest.mark.asyncio
async def test_get_upcoming_events_empty():
    resp = _make_resp(200, {"items": []})
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.get_upcoming_events(days=3)
    assert "No upcoming events" in result


@pytest.mark.asyncio
async def test_get_upcoming_events_api_error():
    resp = _make_resp(403, text_data="forbidden")
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.get_upcoming_events()
    assert "error 403" in result


@pytest.mark.asyncio
async def test_get_upcoming_events_exception():
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(side_effect=Exception("net error"))),
    ):
        result = await calendar_skills.get_upcoming_events()
    assert "failed" in result.lower()


@pytest.mark.asyncio
async def test_get_upcoming_events_clamps_days():
    """days is clamped between 1 and 30."""
    resp = _make_resp(200, {"items": []})
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
        patch("calendar_skills._truncate", lambda text, *a, **k: text),
    ):
        result = await calendar_skills.get_upcoming_events(days=100)
    assert "No upcoming events" in result  # clamped to 30, no error


# ---------------------------------------------------------------------------
# create_calendar_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_event_not_configured(monkeypatch):
    monkeypatch.setattr(calendar_skills, "GOOGLE_REFRESH_TOKEN", "")
    result = await calendar_skills.create_calendar_event("Test", "2026-03-25T10:00:00", "2026-03-25T11:00:00")
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_create_event_no_token():
    with patch("calendar_skills._get_access_token", AsyncMock(return_value=None)):
        result = await calendar_skills.create_calendar_event("Test", "2026-03-25T10:00:00", "2026-03-25T11:00:00")
    assert "Failed to obtain" in result


@pytest.mark.asyncio
async def test_create_event_timed_success():
    data = {"id": "abc123", "htmlLink": "https://calendar.google.com/event/abc123"}
    resp = _make_resp(201, data)
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.create_calendar_event(
            "Team Meeting", "2026-03-25T14:00:00", "2026-03-25T15:00:00", "Notes"
        )
    assert "✅" in result
    assert "Team Meeting" in result
    assert "abc123" in result
    assert "calendar.google.com" in result


@pytest.mark.asyncio
async def test_create_event_all_day_success():
    data = {"id": "xyz789", "htmlLink": ""}
    resp = _make_resp(200, data)
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.create_calendar_event("Holiday", "2026-03-25", "2026-03-26")
    assert "✅" in result
    assert "Holiday" in result


@pytest.mark.asyncio
async def test_create_event_api_error():
    resp = _make_resp(400, text_data="bad request")
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.create_calendar_event("Test", "2026-03-25T10:00:00", "2026-03-25T11:00:00")
    assert "❌" in result


@pytest.mark.asyncio
async def test_create_event_exception():
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(side_effect=Exception("connection reset"))),
    ):
        result = await calendar_skills.create_calendar_event("Test", "2026-03-25T10:00:00", "2026-03-25T11:00:00")
    assert "error" in result.lower()


# ---------------------------------------------------------------------------
# get_todays_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_todays_events_not_configured(monkeypatch):
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_SECRET", "")
    result = await calendar_skills.get_todays_events()
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_get_todays_events_no_token():
    with patch("calendar_skills._get_access_token", AsyncMock(return_value=None)):
        result = await calendar_skills.get_todays_events()
    assert "Failed to obtain" in result


@pytest.mark.asyncio
async def test_get_todays_events_success():
    data = {
        "items": [
            {"summary": "Morning Sync", "start": {"dateTime": "2026-03-25T09:00:00Z"}},
            {"summary": "All-Day Task", "start": {"date": "2026-03-25"}},
        ]
    }
    resp = _make_resp(200, data)
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.get_todays_events()
    assert "Morning Sync" in result
    assert "All-Day Task" in result
    assert "All day" in result


@pytest.mark.asyncio
async def test_get_todays_events_no_items():
    resp = _make_resp(200, {"items": []})
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.get_todays_events()
    assert "No events today" in result


@pytest.mark.asyncio
async def test_get_todays_events_api_error():
    resp = _make_resp(500, text_data="server error")
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.get_todays_events()
    assert "error 500" in result


@pytest.mark.asyncio
async def test_get_todays_events_exception():
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(side_effect=Exception("timeout"))),
    ):
        result = await calendar_skills.get_todays_events()
    assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# delete_calendar_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_event_not_configured(monkeypatch):
    monkeypatch.setattr(calendar_skills, "GOOGLE_CLIENT_ID", "")
    result = await calendar_skills.delete_calendar_event("evt123")
    assert "not configured" in result.lower()


@pytest.mark.asyncio
async def test_delete_event_no_token():
    with patch("calendar_skills._get_access_token", AsyncMock(return_value=None)):
        result = await calendar_skills.delete_calendar_event("evt123")
    assert "Failed to obtain" in result


@pytest.mark.asyncio
async def test_delete_event_success():
    resp = _make_resp(204)
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.delete_calendar_event("evt123")
    assert "✅" in result
    assert "evt123" in result


@pytest.mark.asyncio
async def test_delete_event_not_found():
    resp = _make_resp(404, text_data="not found")
    session = _make_session(resp)
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(return_value=session)),
    ):
        result = await calendar_skills.delete_calendar_event("evt123")
    assert "❌" in result


@pytest.mark.asyncio
async def test_delete_event_exception():
    with (
        patch("calendar_skills._get_access_token", AsyncMock(return_value="tok")),
        patch("calendar_skills._get_session", AsyncMock(side_effect=Exception("disconnect"))),
    ):
        result = await calendar_skills.delete_calendar_event("evt123")
    assert "error" in result.lower()
