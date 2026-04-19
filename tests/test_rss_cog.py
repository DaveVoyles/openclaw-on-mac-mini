"""Tests for cogs/rss_cog.py."""

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

import cogs.rss_cog as mod

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
    return mod.RSSCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_rss_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── cog_command_error ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cog_command_error_check_failure_not_done():
    from discord import app_commands

    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.CheckFailure("Not authorized")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "Not authorized" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_generic_done():
    from discord import app_commands

    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.AppCommandError("Boom")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── rss_list_cmd ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rss_list_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("rss_skills.list_rss_feeds", new=AsyncMock(return_value="Feed1\nFeed2")),
        patch("cogs.rss_cog.audit_log") as mock_audit,
    ):
        await cog.rss_list_cmd.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()
    mock_audit.assert_called_once()


# ── rss_fetch_cmd ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rss_fetch_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    feed_result = "My Feed\nItem 1\nItem 2"
    with (
        patch("rss_skills.fetch_rss_feed", new=AsyncMock(return_value=feed_result)),
        patch("cogs.rss_cog.audit_log") as mock_audit,
    ):
        await cog.rss_fetch_cmd.callback(cog, inter, url="https://example.com/feed", limit=5)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_rss_fetch_cmd_error_response():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("rss_skills.fetch_rss_feed", new=AsyncMock(return_value="❌ Failed to fetch")),
        patch("cogs.rss_cog.audit_log"),
    ):
        await cog.rss_fetch_cmd.callback(cog, inter, url="https://bad.url/feed", limit=10)

    inter.followup.send.assert_awaited_once()


# ── rss_search_cmd ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rss_search_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("rss_skills.search_rss", new=AsyncMock(return_value="Found 2 items")),
        patch("cogs.rss_cog.audit_log") as mock_audit,
    ):
        await cog.rss_search_cmd.callback(cog, inter, url="https://example.com/feed", query="python")

    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_rss_search_cmd_no_results():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("rss_skills.search_rss", new=AsyncMock(return_value="🔍 No items matched")),
        patch("cogs.rss_cog.audit_log"),
    ):
        await cog.rss_search_cmd.callback(cog, inter, url="https://example.com/feed", query="xyz123")

    inter.followup.send.assert_awaited_once()


# ── rss_digest_cmd ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rss_digest_cmd_no_feeds():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("rss_skills._load_feeds", return_value=[]), patch("cogs.rss_cog.audit_log"):
        await cog.rss_digest_cmd.callback(cog, inter, topic="")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args[1].get("embed") or inter.followup.send.call_args[0][0]
    assert "No feeds" in embed.description


@pytest.mark.asyncio
async def test_rss_digest_cmd_with_feeds():
    cog = _make_cog()
    inter = _make_interaction()

    feeds = [{"url": "https://example.com/feed"}]
    with (
        patch("rss_skills._load_feeds", return_value=feeds),
        patch("rss_skills.get_rss_digest", new=AsyncMock(return_value="Digest content")),
        patch("cogs.rss_cog.audit_log") as mock_audit,
    ):
        await cog.rss_digest_cmd.callback(cog, inter, topic="tech")

    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()
