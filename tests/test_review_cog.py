"""Tests for cogs/review_cog.py."""

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

import cogs.review_cog as mod

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
    return mod.ReviewCog(_FakeBot())


def _make_attachment(filename="test.txt", content=b"Sample content for review"):
    attachment = AsyncMock()
    attachment.filename = filename
    attachment.read = AsyncMock(return_value=content)
    return attachment


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_review_cog_init():
    cog = _make_cog()
    assert cog.bot is not None


# ── review_text ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_text_sends_modal():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.review_text.callback(cog, inter, mode="writing")
    inter.response.send_modal.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_text_technical_mode():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.review_text.callback(cog, inter, mode="technical")
    inter.response.send_modal.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_text_quick_mode():
    cog = _make_cog()
    inter = _make_interaction()

    await cog.review_text.callback(cog, inter, mode="quick")
    inter.response.send_modal.assert_awaited_once()


# ── review_file ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_file_txt_success():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("myfile.txt", b"This is a sample text for review.")

    with patch("llm.chat.chat", new=AsyncMock(return_value=("Great writing!", [], "gemini"))):
        await cog.review_file.callback(cog, inter, file=attachment, mode="writing")

    inter.response.defer.assert_awaited_once()
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_file_md_success():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("readme.md", b"# My Project\n\nThis is awesome.")

    with patch("llm.chat.chat", new=AsyncMock(return_value=("Good doc!", [], "gemini"))):
        await cog.review_file.callback(cog, inter, file=attachment, mode="technical")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_file_py_success():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("script.py", b"def hello():\n    print('hello')")

    with patch("llm.chat.chat", new=AsyncMock(return_value=("Clean code!", [], "gemini"))):
        await cog.review_file.callback(cog, inter, file=attachment, mode="quick")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_file_unsupported_format():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("image.png", b"\x89PNG\r\n")

    await cog.review_file.callback(cog, inter, file=attachment, mode="writing")

    inter.followup.send.assert_awaited_once()
    assert "Unsupported format" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_review_file_docx():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("document.docx", b"fake docx bytes")

    with (
        patch("document_skills.read_word", return_value="Document content"),
        patch("llm.chat.chat", new=AsyncMock(return_value=("Good work!", [], "gemini"))),
    ):
        await cog.review_file.callback(cog, inter, file=attachment, mode="writing")

    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_file_error():
    cog = _make_cog()
    inter = _make_interaction()
    attachment = _make_attachment("test.txt", b"Sample text")
    attachment.read = AsyncMock(side_effect=IOError("Read error"))

    await cog.review_file.callback(cog, inter, file=attachment, mode="writing")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title
