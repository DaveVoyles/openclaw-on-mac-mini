"""Tests for cogs/translate_cog.py."""
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

import cogs.translate_cog as mod

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
    return mod.TranslateCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_translate_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── translate_cmd ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_translate_cmd_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("llm.chat.chat", new=AsyncMock(return_value=("Hola mundo", [], "gemini"))):
        await cog.translate_cmd.callback(cog, inter, text="Hello world", to="Spanish")

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed") or inter.followup.send.call_args[1].get("embed")
    assert embed is not None
    assert "Spanish" in embed.title


@pytest.mark.asyncio
async def test_translate_cmd_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("llm.chat.chat", new=AsyncMock(side_effect=RuntimeError("LLM down"))):
        await cog.translate_cmd.callback(cog, inter, text="Hello", to="French")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


@pytest.mark.asyncio
async def test_translate_cmd_long_text():
    cog = _make_cog()
    inter = _make_interaction()
    long_text = "A" * 2000

    with patch("llm.chat.chat", new=AsyncMock(return_value=("B" * 2000, [], "gemini"))):
        await cog.translate_cmd.callback(cog, inter, text=long_text, to="Japanese")

    inter.followup.send.assert_awaited_once()
