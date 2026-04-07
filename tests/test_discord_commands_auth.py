"""Auth coverage tests for modular discord slash commands."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

import permissions as perms
from discord_commands import conversation as conversation_mod
from discord_commands import media as media_mod
from discord_commands import schedule as schedule_mod


def _fake_interaction(user_id: int = 999) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.channel_id = 1234
    interaction.channel = MagicMock()
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def _root_command(bot: commands.Bot, name: str):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == name)


def _group_command(bot: commands.Bot, group_name: str, sub_name: str):
    group = _root_command(bot, group_name)
    return next(cmd for cmd in group.commands if cmd.name == sub_name)


@pytest.mark.asyncio
async def test_schedule_commands_block_unauthorized_user(monkeypatch):
    monkeypatch.setattr(perms, "ALLOWED_USER_IDS", [111])
    monkeypatch.setattr(schedule_mod.scheduler, "list_tasks", MagicMock(return_value=[]))
    monkeypatch.setattr(schedule_mod.scheduler, "create", MagicMock())
    monkeypatch.setattr(schedule_mod.scheduler, "remove", MagicMock(return_value=False))
    monkeypatch.setattr(schedule_mod.scheduler, "toggle", MagicMock(return_value=None))

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    schedule_mod._register_schedule_commands(bot)
    interaction = _fake_interaction(user_id=999)

    cases = [
        ("list", []),
        ("add", ["check_arr_health", 6, 30, 0]),
        ("remove", ["sched-1"]),
        ("toggle", ["sched-1"]),
    ]
    for sub_name, extra_args in cases:
        interaction.response.send_message.reset_mock()
        await _group_command(bot, "schedule", sub_name).callback(interaction, *extra_args)
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.await_args.args[0]
        assert "not authorized" in msg.lower()

    schedule_mod.scheduler.list_tasks.assert_not_called()
    schedule_mod.scheduler.create.assert_not_called()
    schedule_mod.scheduler.remove.assert_not_called()
    schedule_mod.scheduler.toggle.assert_not_called()


@pytest.mark.asyncio
async def test_media_analyze_commands_block_unauthorized_user(monkeypatch):
    monkeypatch.setattr(perms, "ALLOWED_USER_IDS", [111])

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    media_mod._register_media_commands(bot, AsyncMock())
    interaction = _fake_interaction(user_id=999)

    image = MagicMock(spec=discord.Attachment)
    image.content_type = "image/png"
    image.size = 10
    image.url = "https://example.com/test.png"
    image.filename = "test.png"

    file = MagicMock(spec=discord.Attachment)
    file.content_type = "text/plain"
    file.size = 10
    file.url = "https://example.com/test.txt"
    file.filename = "test.txt"

    await _root_command(bot, "analyze-image").callback(interaction, image)
    interaction.response.send_message.assert_awaited_once()
    assert "not authorized" in interaction.response.send_message.await_args.args[0].lower()
    interaction.response.defer.assert_not_awaited()

    interaction.response.send_message.reset_mock()
    await _root_command(bot, "analyze-file").callback(interaction, file)
    interaction.response.send_message.assert_awaited_once()
    assert "not authorized" in interaction.response.send_message.await_args.args[0].lower()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversation_model_show_blocks_unauthorized_user(monkeypatch):
    monkeypatch.setattr(perms, "ALLOWED_USER_IDS", [111])
    monkeypatch.setattr(conversation_mod, "get_model_preference", MagicMock(return_value="auto"))

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    conversation_mod._register_conversation_commands(bot)
    interaction = _fake_interaction(user_id=999)

    await _group_command(bot, "model", "show").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert "not authorized" in interaction.response.send_message.await_args.args[0].lower()
    conversation_mod.get_model_preference.assert_not_called()


@pytest.mark.asyncio
async def test_conversation_model_set_blocks_unauthorized_user(monkeypatch):
    monkeypatch.setattr(perms, "ALLOWED_USER_IDS", [111])
    monkeypatch.setattr(conversation_mod, "set_model_preference", MagicMock(return_value="ok"))

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    conversation_mod._register_conversation_commands(bot)
    interaction = _fake_interaction(user_id=999)
    choice = app_commands.Choice(name="Auto", value="auto")

    await _group_command(bot, "model", "set").callback(interaction, choice)
    interaction.response.send_message.assert_awaited_once()
    assert "not authorized" in interaction.response.send_message.await_args.args[0].lower()
    conversation_mod.set_model_preference.assert_not_called()
