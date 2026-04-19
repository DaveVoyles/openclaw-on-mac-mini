"""Tests for cogs/journal_cog.py."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")
os.environ.setdefault("VAULT_DIR", "/tmp/vault")

import pytest

import cog_helpers as _ch

_orig_require_auth = _ch.require_auth


def _noop_auth(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    return lambda f: f


_ch.require_auth = _noop_auth

import cogs.journal_cog as mod

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
    inter.response.send_modal = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.followup.send = AsyncMock()
    return inter


def _make_cog():
    return mod.JournalCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_journal_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── journal_write ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_write_no_entry_sends_modal():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.journal_write.callback(cog, inter, entry="")
    inter.response.send_modal.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_write_with_entry():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.save_to_vault", new=AsyncMock(return_value="Saved to vault")):
        await cog.journal_write.callback(cog, inter, entry="Today was great!")

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_write_with_entry_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.save_to_vault", new=AsyncMock(side_effect=IOError("disk full"))):
        await cog.journal_write.callback(cog, inter, entry="Some entry text")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── journal_read ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_read_no_file():
    cog = _make_cog()
    inter = _make_interaction()

    with patch.object(mod, "_find_journal_file", return_value=None):
        await cog.journal_read.callback(cog, inter, date="today")

    inter.followup.send.assert_awaited_once()
    assert "No journal entry" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_journal_read_with_file(tmp_path):
    cog = _make_cog()
    inter = _make_interaction()

    fake_file = tmp_path / "journal.md"
    fake_file.write_text("# Journal\n\nToday was productive.")

    with patch.object(mod, "_find_journal_file", return_value=fake_file):
        await cog.journal_read.callback(cog, inter, date="today")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_read_invalid_date():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.journal_read.callback(cog, inter, date="not-a-date-xyz")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── journal_streak ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_streak_no_entries():
    cog = _make_cog()
    inter = _make_interaction()

    with patch.object(mod, "_find_journal_file", return_value=None):
        await cog.journal_streak.callback(cog, inter)

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_streak_consecutive(tmp_path):
    cog = _make_cog()
    inter = _make_interaction()

    fake_file = tmp_path / "journal.md"
    fake_file.write_text("entry")

    call_count = [0]

    def fake_find(d):
        call_count[0] += 1
        # Return a file for the first 3 calls (3 consecutive days), then None
        if call_count[0] <= 3:
            return fake_file
        return None

    with patch.object(mod, "_find_journal_file", side_effect=fake_find):
        await cog.journal_streak.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args[1].get("embed") or inter.followup.send.call_args[0][0]
    # Should show 3-day streak
    assert embed is not None


# ── journal_prompt ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_prompt_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("llm.chat.chat", new=AsyncMock(return_value="What made you smile today?")):
        await cog.journal_prompt.callback(cog, inter)

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_journal_prompt_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("llm.chat.chat", new=AsyncMock(side_effect=RuntimeError("LLM unavailable"))):
        await cog.journal_prompt.callback(cog, inter)

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed") or inter.followup.send.call_args[0][0]
    title = getattr(embed, "title", str(embed))
    assert "Error" in title or "❌" in title or "⚠" in title
