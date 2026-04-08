"""Tests for cogs/analytics_cog.py."""
import datetime
import json
import os
from pathlib import Path
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

import cogs.analytics_cog as mod

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
    return mod.AnalyticsCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── cog_command_error ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cog_command_error_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("Analytics error")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.AppCommandError("Analytics error")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


# ── spending_cmd ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spending_cmd_summary():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.analytics_cog.spending_tracker") as mock_tracker, \
         patch("cogs.analytics_cog.audit_log"):
        mock_tracker.summary.return_value = "Total: $1.23"
        mock_tracker.is_over_budget = False
        mock_tracker.budget_limit = 10.0

        await cog.spending_cmd.callback(cog, inter, breakdown=False)

    inter.response.send_message.assert_awaited_once()
    mock_tracker.summary.assert_called_once()


@pytest.mark.asyncio
async def test_spending_cmd_breakdown():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.analytics_cog.spending_tracker") as mock_tracker, \
         patch("cogs.analytics_cog.audit_log"):
        mock_tracker.daily_breakdown.return_value = "Day 1: $0.50\nDay 2: $0.73"
        mock_tracker.is_over_budget = False
        mock_tracker.budget_limit = 10.0

        await cog.spending_cmd.callback(cog, inter, breakdown=True)

    inter.response.send_message.assert_awaited_once()
    mock_tracker.daily_breakdown.assert_called_once()


@pytest.mark.asyncio
async def test_spending_cmd_over_budget():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.analytics_cog.spending_tracker") as mock_tracker, \
         patch("cogs.analytics_cog.audit_log"):
        mock_tracker.summary.return_value = "Over budget!"
        mock_tracker.is_over_budget = True
        mock_tracker.budget_limit = 5.0

        await cog.spending_cmd.callback(cog, inter, breakdown=False)

    inter.response.send_message.assert_awaited_once()


# ── auditlog_cmd ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auditlog_cmd_no_file(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "AUDIT_DIR", tmp_path)

    await cog.auditlog_cmd.callback(cog, inter, lines=10)

    inter.response.send_message.assert_awaited_once()
    assert "No audit" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_auditlog_cmd_with_entries(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "AUDIT_DIR", tmp_path)

    today = datetime.date.today().isoformat()
    audit_file = tmp_path / f"{today}.jsonl"
    entries = [
        json.dumps({"ts": "2024-01-01T10:00:00", "user": "alice", "action": "search", "detail": "q", "result": "success"}),
        json.dumps({"ts": "2024-01-01T11:00:00", "user": "bob", "action": "ask", "detail": "x", "result": "success"}),
    ]
    audit_file.write_text("\n".join(entries))

    with patch("cogs.analytics_cog.audit_log"):
        await cog.auditlog_cmd.callback(cog, inter, lines=10)

    inter.response.send_message.assert_awaited_once()


# ── audit_summary_cmd ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_summary_cmd_no_file(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "AUDIT_DIR", tmp_path)

    await cog.audit_summary_cmd.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()
    assert "No audit" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_audit_summary_cmd_with_entries(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "AUDIT_DIR", tmp_path)

    today = datetime.date.today().isoformat()
    audit_file = tmp_path / f"{today}.jsonl"
    entries = [
        json.dumps({"ts": "2024-01-01T10:00:00", "user": "alice", "action": "search", "detail": "", "result": "success"}),
        json.dumps({"ts": "2024-01-01T10:30:00", "user": "bob", "action": "search", "detail": "", "result": "error"}),
        json.dumps({"ts": "2024-01-01T11:00:00", "user": "alice", "action": "ask", "detail": "", "result": "success"}),
    ]
    audit_file.write_text("\n".join(entries))

    with patch("cogs.analytics_cog.audit_log"):
        await cog.audit_summary_cmd.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()
