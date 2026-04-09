"""Tests for cogs/note_cog.py."""
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

import cogs.note_cog as mod

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
    return mod.NoteCog(_FakeBot())


# ── __init__ ──────────────────────────────────────────────────────────────────

def test_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── note_create ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_create_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.save_to_vault", new=AsyncMock(return_value="Note saved")):
        await cog.note_create.callback(
            cog, inter, title="My Note", content="Content here", tags="python,testing"
        )

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_create_no_tags():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.save_to_vault", new=AsyncMock(return_value="Saved!")):
        await cog.note_create.callback(
            cog, inter, title="Untitled", content="Quick note", tags=""
        )

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_create_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.save_to_vault", new=AsyncMock(side_effect=IOError("Disk full"))):
        await cog.note_create.callback(
            cog, inter, title="Fail Note", content="Content", tags=""
        )

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── note_list ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_list_all():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.list_vault", new=AsyncMock(return_value="note1.md\nnote2.md")):
        await cog.note_list.callback(cog, inter, content_type="all")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_list_filtered():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.list_vault", new=AsyncMock(return_value="research1.md")):
        await cog.note_list.callback(cog, inter, content_type="research")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_list_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("obsidian_writer.list_vault", new=AsyncMock(side_effect=RuntimeError("Vault gone"))):
        await cog.note_list.callback(cog, inter, content_type="all")

    inter.followup.send.assert_awaited_once()
    assert "❌" in inter.followup.send.call_args[0][0]


# ── note_view ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_view_not_found(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    await cog.note_view.callback(cog, inter, filename="nonexistent.md")

    inter.followup.send.assert_awaited_once()
    assert "not found" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_note_view_found(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    note_file = tmp_path / "my_note.md"
    note_file.write_text("# My Note\n\nSome content here.")

    await cog.note_view.callback(cog, inter, filename="my_note")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_view_large_file(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    note_file = tmp_path / "large_note.md"
    note_file.write_text("x" * 5000)

    await cog.note_view.callback(cog, inter, filename="large_note")

    inter.followup.send.assert_awaited_once()


# ── note_search ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_note_search_no_matches(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    note_file = tmp_path / "test.md"
    note_file.write_text("This file talks about apples and oranges.")

    await cog.note_search.callback(cog, inter, query="python")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_search_with_matches(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    note_file = tmp_path / "python_notes.md"
    note_file.write_text("# Python Notes\n\nPython is great for automation.")

    await cog.note_search.callback(cog, inter, query="python")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_search_empty_vault(tmp_path, monkeypatch):
    cog = _make_cog()
    inter = _make_interaction()
    monkeypatch.setattr(mod, "VAULT_DIR", tmp_path)

    await cog.note_search.callback(cog, inter, query="anything")

    inter.followup.send.assert_awaited_once()
