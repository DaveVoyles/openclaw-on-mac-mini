"""Tests for cogs/nas_cog.py."""
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

import cogs.nas_cog as mod

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
    return mod.NasCog(_FakeBot())


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
    err = app_commands.AppCommandError("NAS error")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.AppCommandError("NAS error")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


# ── status_cmd ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_nas_storage_health", new=AsyncMock(return_value="🟢 All healthy")), \
         patch("cogs.nas_cog.audit_log") as mock_audit:
        await cog.status_cmd.callback(cog, inter)

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_status_cmd_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_nas_storage_health", new=AsyncMock(side_effect=ConnectionError("timeout"))):
        await cog.status_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args[1].get("embed") or inter.followup.send.call_args[0][0]
    assert "Unreachable" in embed.title or "timeout" in embed.description


# ── health_cmd ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_disk_smart_status", new=AsyncMock(return_value="All drives OK")), \
         patch("cogs.nas_cog.audit_log") as mock_audit:
        await cog.health_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_health_cmd_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_disk_smart_status", new=AsyncMock(side_effect=OSError("unreachable"))):
        await cog.health_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()


# ── alerts_cmd ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_nas_alerts", new=AsyncMock(return_value="No alerts")), \
         patch("cogs.nas_cog.audit_log") as mock_audit:
        await cog.alerts_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_alerts_cmd_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.get_nas_alerts", new=AsyncMock(side_effect=RuntimeError("error"))):
        await cog.alerts_cmd.callback(cog, inter)

    inter.followup.send.assert_awaited_once()


# ── browse_cmd ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browse_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.nas_list_folder", new=AsyncMock(return_value="folder1\nfolder2")), \
         patch("cogs.nas_cog.audit_log") as mock_audit:
        await cog.browse_cmd.callback(cog, inter, path="/volume1")

    inter.followup.send.assert_awaited_once()
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_browse_cmd_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.nas_cog.nas_list_folder", new=AsyncMock(side_effect=PermissionError("denied"))):
        await cog.browse_cmd.callback(cog, inter, path="/volume1/private")

    inter.followup.send.assert_awaited_once()
