"""Tests for cogs/poll_cog.py."""
import asyncio
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

import cogs.poll_cog as mod

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
    return mod.PollCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── poll_cmd ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_cmd_too_few_options():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.poll_cmd.callback(cog, inter, question="Favorite?", options="Pizza")
    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_poll_cmd_too_many_options():
    cog = _make_cog()
    inter = _make_interaction()

    options = ",".join([f"Option {i}" for i in range(11)])
    await cog.poll_cmd.callback(cog, inter, question="Pick one?", options=options)
    inter.response.send_message.assert_awaited_once()
    assert "❌" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_poll_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    fake_msg = AsyncMock()
    fake_msg.id = 42
    fake_msg.reactions = []
    inter.original_response = AsyncMock(return_value=fake_msg)

    mock_task = MagicMock()
    with patch("asyncio.create_task", return_value=mock_task) as mock_create_task:
        await cog.poll_cmd.callback(
            cog, inter,
            question="Best food?",
            options="Pizza, Tacos, Sushi",
            duration=60,
        )

    inter.response.send_message.assert_awaited_once()
    inter.original_response.assert_awaited_once()
    assert fake_msg.add_reaction.await_count == 3
    mock_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_poll_cmd_two_options():
    cog = _make_cog()
    inter = _make_interaction()

    fake_msg = AsyncMock()
    fake_msg.id = 99
    fake_msg.reactions = []
    inter.original_response = AsyncMock(return_value=fake_msg)

    with patch("asyncio.create_task"):
        await cog.poll_cmd.callback(
            cog, inter,
            question="Yes or No?",
            options="Yes, No",
        )

    assert fake_msg.add_reaction.await_count == 2


@pytest.mark.asyncio
async def test_poll_cmd_exactly_ten_options():
    cog = _make_cog()
    inter = _make_interaction()

    fake_msg = AsyncMock()
    fake_msg.id = 100
    fake_msg.reactions = []
    inter.original_response = AsyncMock(return_value=fake_msg)

    options = ",".join([f"Option {i}" for i in range(10)])
    with patch("asyncio.create_task"):
        await cog.poll_cmd.callback(
            cog, inter, question="Pick one?", options=options
        )

    assert fake_msg.add_reaction.await_count == 10
