"""Tests for discord_commands/plugins.py — /plugin command group."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

import discord_commands._helpers as _helpers_mod
import discord_commands.plugins as plugins_mod
from plugin_system import PluginMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(user_id: int = 111):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.channel_id = 100
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _allow_all():
    """Context patch that makes all users authorized via _helpers._is_allowed."""
    return patch.object(_helpers_mod, "_is_allowed", return_value=True)


def _make_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    plugins_mod._register_plugin_commands(bot)
    return bot


def _get_plugin_sub(bot, sub_name):
    group = next(cmd for cmd in bot.tree.get_commands() if cmd.name == "plugin")
    return next(cmd for cmd in group.commands if cmd.name == sub_name)


def _make_registry():
    registry = MagicMock()
    registry.list_plugins = MagicMock(return_value=[])
    registry.list_disabled_plugins = MagicMock(return_value=[])
    registry.get_plugin_info = MagicMock(return_value=None)
    registry.enable_plugin = AsyncMock(return_value=(True, "Enabled"))
    registry.disable_plugin = AsyncMock(return_value=(True, "Disabled"))
    registry.reload_plugin = AsyncMock(return_value=(True, "Reloaded"))
    registry.install_plugin = AsyncMock(return_value=(True, "Installed"))
    return registry


def _make_metadata(**kwargs):
    defaults = dict(name="test-plugin", version="1.0.0", author="Author",
                    description="A test plugin")
    defaults.update(kwargs)
    return PluginMetadata(**defaults)


# ---------------------------------------------------------------------------
# set_plugin_registry
# ---------------------------------------------------------------------------

def test_set_plugin_registry():
    registry = _make_registry()
    plugins_mod.set_plugin_registry(registry)
    assert plugins_mod._plugin_registry is registry
    plugins_mod.set_plugin_registry(None)


# ---------------------------------------------------------------------------
# /plugin list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_list_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "list").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.kwargs.get("content") or \
          interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_list_empty(monkeypatch):
    registry = _make_registry()
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "list").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args.args
    msg = args[0] if args else ""
    assert "no plugins" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_list_with_plugins(monkeypatch):
    registry = _make_registry()
    registry.list_plugins.return_value = [
        _make_metadata(name="plugin-a", version="1.0.0", author="Dev"),
        _make_metadata(name="plugin-b", version="2.0.0", author="Dev2"),
    ]
    registry.list_disabled_plugins.return_value = ["plugin-c"]
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "list").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    call_kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# /plugin info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_info_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "info").callback(interaction, "myplugin")
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_info_not_found(monkeypatch):
    registry = _make_registry()
    registry.get_plugin_info.return_value = None
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "info").callback(interaction, "missing")
    msg = interaction.response.send_message.await_args.args[0]
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_info_found(monkeypatch):
    registry = _make_registry()
    registry.get_plugin_info.return_value = {
        "metadata": {
            "name": "my-plugin", "version": "1.0.0", "author": "Dev",
            "description": "Test plugin", "dependencies": ["dep1"],
            "permissions": ["read"], "homepage": "https://example.com",
        },
        "loaded": True,
        "enabled": True,
        "skills": ["skill_a", "skill_b"],
        "commands": [],
    }
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "info").callback(interaction, "my-plugin")
    interaction.response.send_message.assert_awaited_once()
    call_kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in call_kwargs


@pytest.mark.asyncio
async def test_plugin_info_disabled(monkeypatch):
    registry = _make_registry()
    registry.get_plugin_info.return_value = {
        "metadata": {
            "name": "disabled-plugin", "version": "0.1.0", "author": "Dev",
            "description": "", "dependencies": [], "permissions": [], "homepage": None,
        },
        "loaded": False,
        "enabled": False,
        "skills": [],
        "commands": [],
    }
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "info").callback(interaction, "disabled-plugin")
    interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# /plugin enable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_enable_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "enable").callback(interaction, "myplugin")
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_enable_success(monkeypatch):
    registry = _make_registry()
    registry.enable_plugin = AsyncMock(return_value=(True, "Plugin enabled!"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "enable").callback(interaction, "myplugin")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_plugin_enable_failure(monkeypatch):
    registry = _make_registry()
    registry.enable_plugin = AsyncMock(return_value=(False, "Not found"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "enable").callback(interaction, "badplugin")
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /plugin disable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_disable_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "disable").callback(interaction, "myplugin")
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_disable_success(monkeypatch):
    registry = _make_registry()
    registry.disable_plugin = AsyncMock(return_value=(True, "Disabled"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "disable").callback(interaction, "myplugin")
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_plugin_disable_failure(monkeypatch):
    registry = _make_registry()
    registry.disable_plugin = AsyncMock(return_value=(False, "Error"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "disable").callback(interaction, "myplugin")
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /plugin reload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_reload_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "reload").callback(interaction, "myplugin")
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_reload_success(monkeypatch):
    registry = _make_registry()
    registry.reload_plugin = AsyncMock(return_value=(True, "Reloaded successfully"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "reload").callback(interaction, "myplugin")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_reload_failure(monkeypatch):
    registry = _make_registry()
    registry.reload_plugin = AsyncMock(return_value=(False, "Reload failed"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "reload").callback(interaction, "myplugin")
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /plugin install
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_install_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "install").callback(interaction, "/some/path")
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_install_path_not_found(monkeypatch):
    registry = _make_registry()
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "install").callback(interaction, "/nonexistent/path")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_install_success(monkeypatch, tmp_path):
    registry = _make_registry()
    registry.install_plugin = AsyncMock(return_value=(True, "Installed OK"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "install").callback(interaction, str(tmp_path))
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_plugin_install_failure(monkeypatch, tmp_path):
    registry = _make_registry()
    registry.install_plugin = AsyncMock(return_value=(False, "Bad plugin"))
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "install").callback(interaction, str(tmp_path))
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /plugin uninstall
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_uninstall_no_registry(monkeypatch):
    monkeypatch.setattr(plugins_mod, "_plugin_registry", None)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_plugin_sub(bot, "uninstall").callback(interaction, "myplugin")
    msg = interaction.response.send_message.await_args.args[0]
    assert "not initialized" in msg.lower()


@pytest.mark.asyncio
async def test_plugin_uninstall_confirmation_prompt(monkeypatch):
    registry = _make_registry()
    monkeypatch.setattr(plugins_mod, "_plugin_registry", registry)
    bot = _make_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.plugins.audit_log"):
        await _get_plugin_sub(bot, "uninstall").callback(interaction, "myplugin")
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "myplugin" in msg
    assert "confirm" in msg.lower() or "sure" in msg.lower() or "yes" in msg.lower()


# ---------------------------------------------------------------------------
# Auth gating via require_auth decorator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_list_unauthorized(monkeypatch):
    """Unauthorized users get blocked by require_auth."""
    import discord_commands._helpers as helpers_mod
    monkeypatch.setattr(helpers_mod, "ALLOWED_USER_IDS", [999])
    monkeypatch.setattr(plugins_mod, "_plugin_registry", _make_registry())
    bot = _make_bot()
    interaction = _make_interaction(user_id=1)
    await _get_plugin_sub(bot, "list").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "not authorized" in msg.lower()
