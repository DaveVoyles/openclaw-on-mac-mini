"""Tests for DocCog commands and helpers."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

import cogs.doc_cog as mod

# ── Fixtures ─────────────────────────────────────────────────────────────────

class _FakeTree:
    def add_command(self, *a, **k): pass
    def remove_command(self, *a, **k): pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_interaction(done=False):
    inter = AsyncMock()
    inter.user.id = 1
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = 100
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.response.edit_message = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


def _make_attachment(filename="test.docx", content=b"fake docx content"):
    att = AsyncMock()
    att.filename = filename
    att.read = AsyncMock(return_value=content)
    return att


def _make_cog():
    return mod.DocCog(_FakeBot())


# ── Helper functions ──────────────────────────────────────────────────────────

def test_parse_json_clean():
    result = mod._parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_parse_json_with_markdown_fences():
    raw = "```json\n{\"hello\": \"world\"}\n```"
    result = mod._parse_json(raw)
    assert result == {"hello": "world"}


def test_parse_json_with_bare_fences():
    raw = "```\n[1, 2, 3]\n```"
    result = mod._parse_json(raw)
    assert result == [1, 2, 3]


@pytest.mark.asyncio
async def test_llm_chat():
    with patch("llm.chat.chat", AsyncMock(return_value=("response text", [], "gemini"))):
        result = await mod._llm_chat("hello world")
    assert result == "response text"


@pytest.mark.asyncio
async def test_parse_edit_instructions_word():
    raw_response = '{"old text": "new text"}'
    with patch("cogs.doc_cog._llm_chat", AsyncMock(return_value=raw_response)):
        result = await mod._parse_edit_instructions("doc content", "replace old text with new text", "word")
    assert result == {"old text": "new text"}


@pytest.mark.asyncio
async def test_parse_edit_instructions_excel():
    raw_response = '[{"cell": "A1", "value": "hello"}]'
    with patch("cogs.doc_cog._llm_chat", AsyncMock(return_value=raw_response)):
        result = await mod._parse_edit_instructions("sheet content", "set A1 to hello", "excel")
    assert result == [{"cell": "A1", "value": "hello"}]


@pytest.mark.asyncio
async def test_generate_doc_content():
    raw = '{"title": "My Doc", "headers": ["Intro", "Body"], "body": "This is the content."}'
    with patch("cogs.doc_cog._llm_chat", AsyncMock(return_value=raw)):
        title, body, headers = await mod._generate_doc_content("Create a report about AI")
    assert title == "My Doc"
    assert "content" in body
    assert "Intro" in headers


@pytest.mark.asyncio
async def test_generate_sheet_content():
    raw = '{"title": "Budget", "headers": ["Month", "Amount"], "rows": [["Jan", 100], ["Feb", 200]]}'
    with patch("cogs.doc_cog._llm_chat", AsyncMock(return_value=raw)):
        title, headers, rows = await mod._generate_sheet_content("Create a budget spreadsheet")
    assert title == "Budget"
    assert headers == ["Month", "Amount"]
    assert len(rows) == 2


# ── /doc read ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_read_wrong_extension():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.pdf")

    await cog.doc_read.callback(cog, inter, file=file)
    inter.response.send_message.assert_awaited_once()
    assert ".docx" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_doc_read_success_short():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value="Short document content")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.doc_read.callback(cog, inter, file=file)

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "test.docx" in embed.title
    assert "Short document" in embed.description


@pytest.mark.asyncio
async def test_doc_read_success_long():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    long_text = "x" * 5000

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value=long_text)), \
         patch("cogs.doc_cog.audit_log"):
        await cog.doc_read.callback(cog, inter, file=file)

    inter.followup.send.assert_awaited_once()
    kwargs = inter.followup.send.call_args.kwargs
    assert "file" in kwargs


@pytest.mark.asyncio
async def test_doc_read_empty_document():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value="   ")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.doc_read.callback(cog, inter, file=file)

    msg = inter.followup.send.call_args[0][0]
    assert "empty" in msg


@pytest.mark.asyncio
async def test_doc_read_exception():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(side_effect=Exception("corrupt file"))):
        await cog.doc_read.callback(cog, inter, file=file)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /doc edit ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_edit_wrong_extension():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.txt")

    await cog.doc_edit.callback(cog, inter, file=file, instructions="fix grammar")
    assert ".docx" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_doc_edit_success():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    edits = {"old text": "new text"}

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value="old text here")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(return_value=edits)), \
         patch("cogs.doc_cog.edit_word", AsyncMock(return_value=b"modified docx")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.doc_edit.callback(cog, inter, file=file, instructions="replace old with new")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "Edited" in embed.title


@pytest.mark.asyncio
async def test_doc_edit_no_edits_determined():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value="content")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(return_value={})):
        await cog.doc_edit.callback(cog, inter, file=file, instructions="do something vague")

    msg = inter.followup.send.call_args[0][0]
    assert "Could not determine" in msg


@pytest.mark.asyncio
async def test_doc_edit_json_decode_error():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(return_value="content")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(side_effect=json.JSONDecodeError("err", "doc", 0))):
        await cog.doc_edit.callback(cog, inter, file=file, instructions="bad instructions")

    assert "invalid edit instructions" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_doc_edit_exception():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.docx")

    with patch("cogs.doc_cog.read_word", AsyncMock(side_effect=Exception("IO error"))):
        await cog.doc_edit.callback(cog, inter, file=file, instructions="edit it")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /doc create ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doc_create_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_doc_content", AsyncMock(return_value=("My Report", "Content here.", ["Intro"]))), \
         patch("cogs.doc_cog.create_word", AsyncMock(return_value=b"docx bytes")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.doc_create.callback(cog, inter, instructions="Create a report about AI")

    inter.followup.send.assert_awaited_once()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "My Report" in embed.title


@pytest.mark.asyncio
async def test_doc_create_json_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_doc_content", AsyncMock(side_effect=json.JSONDecodeError("e", "d", 0))):
        await cog.doc_create.callback(cog, inter, instructions="create doc")

    assert "invalid content" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_doc_create_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_doc_content", AsyncMock(side_effect=Exception("LLM down"))):
        await cog.doc_create.callback(cog, inter, instructions="create doc")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /sheet read ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sheet_read_wrong_extension():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.csv")

    await cog.sheet_read.callback(cog, inter, file=file)
    assert ".xlsx" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_read_success_short():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value="| Col1 | Col2 |\n| A | B |")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.sheet_read.callback(cog, inter, file=file)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "data.xlsx" in embed.title


@pytest.mark.asyncio
async def test_sheet_read_success_long():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    long_text = "| Col | \n" * 1000

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value=long_text)), \
         patch("cogs.doc_cog.audit_log"):
        await cog.sheet_read.callback(cog, inter, file=file)

    kwargs = inter.followup.send.call_args.kwargs
    assert "file" in kwargs


@pytest.mark.asyncio
async def test_sheet_read_empty():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value="")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.sheet_read.callback(cog, inter, file=file)

    assert "empty" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_read_exception():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(side_effect=Exception("parse error"))):
        await cog.sheet_read.callback(cog, inter, file=file)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /sheet edit ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sheet_edit_wrong_extension():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="test.xls")

    await cog.sheet_edit.callback(cog, inter, file=file, instructions="update A1")
    assert ".xlsx" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_edit_success():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    edits = [{"cell": "A1", "value": "New Value"}]

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value="| A | B |\n| old | data |")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(return_value=edits)), \
         patch("cogs.doc_cog.edit_excel", AsyncMock(return_value=b"modified xlsx")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.sheet_edit.callback(cog, inter, file=file, instructions="set A1 to New Value")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "Edited" in embed.title
    assert "A1" in embed.description


@pytest.mark.asyncio
async def test_sheet_edit_no_edits():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value="content")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(return_value=[])):
        await cog.sheet_edit.callback(cog, inter, file=file, instructions="vague instruction")

    assert "Could not determine" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_edit_json_error():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(return_value="content")), \
         patch("cogs.doc_cog._parse_edit_instructions", AsyncMock(side_effect=json.JSONDecodeError("e", "d", 0))):
        await cog.sheet_edit.callback(cog, inter, file=file, instructions="bad")

    assert "invalid edit instructions" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_edit_exception():
    cog = _make_cog()
    inter = _make_interaction()
    file = _make_attachment(filename="data.xlsx")

    with patch("cogs.doc_cog.read_excel", AsyncMock(side_effect=Exception("parse fail"))):
        await cog.sheet_edit.callback(cog, inter, file=file, instructions="edit it")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── /sheet create ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sheet_create_success():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_sheet_content", AsyncMock(return_value=("Budget 2024", ["Month", "Amount"], [["Jan", 100], ["Feb", 200]]))), \
         patch("cogs.doc_cog.create_excel", AsyncMock(return_value=b"xlsx bytes")), \
         patch("cogs.doc_cog.audit_log"):
        await cog.sheet_create.callback(cog, inter, instructions="Create a budget spreadsheet")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "Budget 2024" in embed.title
    assert "Month" in embed.description
    assert "2" in embed.description  # row count


@pytest.mark.asyncio
async def test_sheet_create_json_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_sheet_content", AsyncMock(side_effect=json.JSONDecodeError("e", "d", 0))):
        await cog.sheet_create.callback(cog, inter, instructions="create sheet")

    assert "invalid content" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_sheet_create_exception():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("cogs.doc_cog._generate_sheet_content", AsyncMock(side_effect=Exception("LLM error"))):
        await cog.sheet_create.callback(cog, inter, instructions="create sheet")

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Error" in embed.title


# ── cog_app_command_error ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cog_app_command_error_check_failure_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.CheckFailure("Not authorized")
    await cog.cog_app_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "Not authorized" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_app_command_error_check_failure_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.CheckFailure("Denied")
    await cog.cog_app_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_app_command_error_other_error_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("unexpected")
    await cog.cog_app_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "unexpected error" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_app_command_error_other_error_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.AppCommandError("something went wrong")
    await cog.cog_app_command_error(inter, err)
    inter.followup.send.assert_awaited_once()
    assert "unexpected error" in inter.followup.send.call_args[0][0]


# ── _SaveToNASView ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_to_nas_view_success():
    view = mod._SaveToNASView(file_bytes=b"content", filename="test.docx")
    inter = _make_interaction()

    mock_nas = MagicMock()
    mock_nas.nas_create_folder = AsyncMock()
    mock_nas.nas_write_file = AsyncMock(return_value="Saved successfully")

    with patch.dict(sys.modules, {"nas": mock_nas}), \
         patch("cogs.doc_cog.audit_log"):
        # Button.callback takes just (interaction,) after binding to view
        await view.save_nas.callback(inter)

    inter.followup.send.assert_awaited_once()
    assert "Saved" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_save_to_nas_view_failure():
    view = mod._SaveToNASView(file_bytes=b"content", filename="test.docx")
    inter = _make_interaction()

    with patch.dict(sys.modules, {"nas": None}), \
         patch("cogs.doc_cog.audit_log"):
        await view.save_nas.callback(inter)

    inter.followup.send.assert_awaited_once()
    assert "Save failed" in inter.followup.send.call_args[0][0]
