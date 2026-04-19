"""Tests for cogs/notify_cog.py."""
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

import cogs.notify_cog as mod

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
    return mod.NotifyCog(_FakeBot())


def _make_prefs(
    enabled=True,
    dm_alerts=True,
    severity_filter="all",
    muted_until=0.0,
    blocked_services=None,
):
    prefs = MagicMock()
    prefs.enabled = enabled
    prefs.dm_alerts = dm_alerts
    prefs.severity_filter = severity_filter
    prefs.muted_until = muted_until
    prefs.blocked_services = blocked_services or []
    return prefs


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_notify_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── show ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_show_not_muted():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(muted_until=0.0)
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.show.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_muted():
    import time
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(muted_until=time.time() + 3600, blocked_services=["sonarr"])
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.show.callback(cog, inter)

    inter.response.send_message.assert_awaited_once()


# ── mute ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mute_valid_duration():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs()
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.mute.callback(cog, inter, duration="30m")

    inter.response.send_message.assert_awaited_once()
    assert "🔇" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_mute_invalid_duration():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.mute.callback(cog, inter, duration="forever")

    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_mute_hours():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs()
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.mute.callback(cog, inter, duration="2h")

    inter.response.send_message.assert_awaited_once()


# ── unmute ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unmute():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs()
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.unmute.callback(cog, inter)

    assert prefs.muted_until == 0.0
    inter.response.send_message.assert_awaited_once()
    assert "🔔" in inter.response.send_message.call_args[0][0]


# ── filter_cmd ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filter_cmd_set_warning():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs()
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    level_choice = app_commands.Choice(name="warning", value="warning")

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.filter_cmd.callback(cog, inter, level=level_choice)

    assert prefs.severity_filter == "warning"
    inter.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_filter_cmd_set_critical():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs()
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    level_choice = app_commands.Choice(name="critical", value="critical")

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.filter_cmd.callback(cog, inter, level=level_choice)

    assert prefs.severity_filter == "critical"


# ── block ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_block_new_service():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(blocked_services=[])
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.block.callback(cog, inter, service="sonarr")

    assert "sonarr" in prefs.blocked_services
    inter.response.send_message.assert_awaited_once()
    assert "🚫" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_block_already_blocked():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(blocked_services=["sonarr"])
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.block.callback(cog, inter, service="sonarr")

    inter.response.send_message.assert_awaited_once()
    assert "already blocked" in inter.response.send_message.call_args[0][0]


# ── unblock ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unblock_existing():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(blocked_services=["sonarr", "radarr"])
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.unblock.callback(cog, inter, service="sonarr")

    assert "sonarr" not in prefs.blocked_services
    inter.response.send_message.assert_awaited_once()
    assert "✅" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_unblock_not_blocked():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(blocked_services=[])
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.unblock.callback(cog, inter, service="plex")

    inter.response.send_message.assert_awaited_once()
    assert "not blocked" in inter.response.send_message.call_args[0][0]


# ── dm_toggle ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dm_toggle_on():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(dm_alerts=False)
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    enabled_choice = app_commands.Choice(name="on", value="on")

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.dm_toggle.callback(cog, inter, enabled=enabled_choice)

    assert prefs.dm_alerts is True
    inter.response.send_message.assert_awaited_once()
    assert "enabled" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_dm_toggle_off():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction()

    prefs = _make_prefs(dm_alerts=True)
    mock_prefs = MagicMock()
    mock_prefs.get.return_value = prefs
    mock_prefs.update = AsyncMock()

    enabled_choice = app_commands.Choice(name="off", value="off")

    with patch("cogs.notify_cog.notif_prefs", mock_prefs):
        await cog.dm_toggle.callback(cog, inter, enabled=enabled_choice)

    assert prefs.dm_alerts is False
    assert "disabled" in inter.response.send_message.call_args[0][0]
