"""Tests for report rendering behavior in ReportsCog."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import reports_cog as mod


class _DummyTree:
    def add_command(self, *_args, **_kwargs):
        return None

    def remove_command(self, *_args, **_kwargs):
        return None


class _DummyBot:
    def __init__(self):
        self.tree = _DummyTree()


@pytest.mark.asyncio
async def test_send_chunks_formats_tables_as_text_code_block(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    body = "| Team | Record |\n| --- | --- |\n| Wolves | 10-2 |"
    base_split = mod.split_response

    monkeypatch.setattr(
        mod,
        "split_response",
        lambda text: base_split(text, limit=80),
    )

    await cog._send_chunks(
        interaction,
        title="Report",
        body=body,
        color=discord.Color.green(),
    )

    calls = interaction.followup.send.await_args_list
    assert calls
    descriptions = [call.kwargs["embed"].description for call in calls if "embed" in call.kwargs]
    assert any("```text" in description for description in descriptions)
    assert any("Wolves" in description for description in descriptions)


@pytest.mark.asyncio
async def test_send_chunks_splits_long_rendered_tables_readably(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    rows = "\n".join(f"| Team {i} | {10 + i}-{i} |" for i in range(14))
    body = "| Team | Record |\n| --- | --- |\n" + rows
    base_split = mod.split_response

    monkeypatch.setattr(mod, "split_response", lambda text: base_split(text, limit=140))

    await cog._send_chunks(
        interaction,
        title="Report",
        body=body,
        color=discord.Color.green(),
    )

    descriptions = [
        call.kwargs["embed"].description for call in interaction.followup.send.await_args_list if "embed" in call.kwargs
    ]
    assert len(descriptions) > 1
    assert all(len(description) <= 140 for description in descriptions)
    for description in descriptions:
        assert description.count("```") % 2 == 0


@pytest.mark.asyncio
async def test_send_chunks_marks_follow_up_embeds_as_continuations(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))

    monkeypatch.setattr(mod, "split_response", lambda _text: ["first chunk", "second chunk"])

    await cog._send_chunks(
        interaction,
        title="Weekly Recap",
        body="ignored because split_response mocked",
        color=discord.Color.blurple(),
    )

    calls = [call for call in interaction.followup.send.await_args_list if "embed" in call.kwargs]
    assert len(calls) == 2
    assert calls[0].kwargs["embed"].title == "Weekly Recap"
    assert calls[1].kwargs["embed"].title == "Weekly Recap (cont.)"


@pytest.mark.asyncio
async def test_send_chunks_gracefully_falls_back_when_table_renderer_errors(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))

    fake_renderer = types.SimpleNamespace(
        extract_table_text=lambda _body: (_ for _ in ()).throw(RuntimeError("renderer down")),
        should_render_table_image=lambda _table: True,
        render_table_image=lambda _table: b"",
    )
    monkeypatch.setitem(sys.modules, "table_renderer", fake_renderer)

    await cog._send_chunks(
        interaction,
        title="Report",
        body="| Col |\n| --- |\n| value |",
        color=discord.Color.green(),
    )

    calls = interaction.followup.send.await_args_list
    assert calls
    assert any("embed" in call.kwargs for call in calls)


@pytest.mark.asyncio
async def test_send_chunks_sends_table_image_when_renderer_provides_bytes(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))

    fake_renderer = types.SimpleNamespace(
        extract_table_text=lambda _body: "| Col |\n| --- |\n| value |",
        should_render_table_image=lambda _table: True,
        render_table_image=lambda _table: b"png-bytes",
    )
    monkeypatch.setitem(sys.modules, "table_renderer", fake_renderer)

    await cog._send_chunks(
        interaction,
        title="Report",
        body="| Col |\n| --- |\n| value |",
        color=discord.Color.green(),
    )

    calls = interaction.followup.send.await_args_list
    assert any("embed" in call.kwargs for call in calls)
    assert any("file" in call.kwargs for call in calls)
