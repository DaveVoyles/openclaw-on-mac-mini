"""Tests for cogs/network_cog.py."""

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

import cogs.network_cog as mod

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
    return mod.NetworkCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_network_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── cog_command_error ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_network_cog_cog_command_error_not_done():
    from discord import app_commands

    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("Something broke")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_already_done():
    from discord import app_commands

    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.AppCommandError("Something broke")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


# ── network_cmd ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_network_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.network_cog.get_network_status", new=AsyncMock(return_value="All OK")),
        patch("cogs.network_cog.audit_log") as mock_audit,
    ):
        await cog.network_cmd.callback(cog, inter)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_network_cmd_embed_content():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.network_cog.get_network_status", new=AsyncMock(return_value="Network OK")),
        patch("cogs.network_cog.audit_log"),
    ):
        await cog.network_cmd.callback(cog, inter)

    embed = inter.followup.send.call_args[1].get("embed") or inter.followup.send.call_args[0][0]
    assert "Network" in str(embed.title) or embed.description == "Network OK"


# ── tailscale_cmd ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tailscale_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.network_cog.get_tailscale_status", new=AsyncMock(return_value="VPN OK")),
        patch("cogs.network_cog.audit_log") as mock_audit,
    ):
        await cog.tailscale_cmd.callback(cog, inter)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


# ── speedtest_cmd ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speedtest_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with (
        patch("cogs.network_cog.run_speed_test", new=AsyncMock(return_value="100 Mbps")),
        patch("cogs.network_cog.audit_log") as mock_audit,
    ):
        await cog.speedtest_cmd.callback(cog, inter)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()
