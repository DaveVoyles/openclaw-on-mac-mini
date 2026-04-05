"""Tests for report rendering behavior in ReportsCog."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from cogs import reports_cog as mod
from memory import Conversation


class _DummyTree:
    def add_command(self, *_args, **_kwargs):
        return None

    def remove_command(self, *_args, **_kwargs):
        return None


class _DummyBot:
    def __init__(self):
        self.tree = _DummyTree()


@pytest.mark.asyncio
async def test_send_chunks_formats_tables_as_copy_safe_blocks(monkeypatch):
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
    joined = "\n".join(descriptions)
    assert "📋 Table" in joined
    assert "  - Team: Wolves" in joined
    assert "  - Record: 10-2" in joined


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
    joined = "\n".join(descriptions)
    assert "📋 Table" in joined
    assert "Team 0" in joined
    assert "Team 13" in joined


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
async def test_send_chunks_preserves_summary_lines_below_copy_safe_table():
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    body = (
        "| Team | Record |\n| --- | --- |\n| Wolves | 10-2 |\n\n"
        "- ✅ Summary line\n"
        "- 📌 Next action"
    )

    await cog._send_chunks(
        interaction,
        title="Weekly Recap",
        body=body,
        color=discord.Color.blurple(),
    )

    descriptions = [
        call.kwargs["embed"].description for call in interaction.followup.send.await_args_list if "embed" in call.kwargs
    ]
    joined = "\n".join(descriptions)
    assert "📋 Table" in joined
    assert "- ✅ Summary line" in joined
    assert "- 📌 Next action" in joined


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


class _InteractionStub:
    def __init__(self, *, user_id: int = 123, channel_id: int = 456, display_name: str = "Dave"):
        self.user = SimpleNamespace(id=user_id, display_name=display_name)
        self.channel_id = channel_id
        self.response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())


@pytest.mark.asyncio
async def test_recap_copy_latest_returns_ephemeral_copy_block(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub()

    conv = Conversation(user_name="Dave")
    conv.history = [
        {"role": "user", "parts": ["what changed?"]},
        {"role": "model", "parts": ["**Status**\n- Shipped copy flow\n- Added tests"]},
    ]

    fake_store = SimpleNamespace(get=lambda **_kwargs: conv)
    monkeypatch.setattr(mod, "conversation_store", fake_store)

    await cog.recap_copy_latest.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    message = interaction.response.send_message.await_args.args[0]
    assert kwargs["ephemeral"] is True
    assert "Copy-ready export (latest response)" in message
    assert "• Shipped copy flow" in message


@pytest.mark.asyncio
async def test_recap_copy_thread_uses_formatter_and_returns_ephemeral(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub(channel_id=999)

    fake_reporting = types.SimpleNamespace(
        generate_channel_recap_report=AsyncMock(return_value="Recap heading\n- Task A\n- Task B")
    )
    monkeypatch.setitem(sys.modules, "skills.reporting_skills", fake_reporting)
    monkeypatch.setattr(mod, "build_copy_workflow_payload", lambda text: f"formatted::{text[:12]}")

    await cog.recap_copy_thread.callback(cog, interaction, days=3, focus="ship", style="action-items")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    args = interaction.followup.send.await_args.args
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "formatted::Recap" in args[0]
    fake_reporting.generate_channel_recap_report.assert_awaited_once_with(
        channel_id=999,
        days=3,
        focus="ship",
        style="action-items",
    )


@pytest.mark.asyncio
async def test_recap_copy_latest_handles_missing_model_response(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub()

    conv = Conversation(user_name="Dave")
    conv.history = [{"role": "user", "parts": ["hello only"]}]
    monkeypatch.setattr(mod, "conversation_store", SimpleNamespace(get=lambda **_kwargs: conv))

    await cog.recap_copy_latest.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(
        "❌ No recent OpenClaw response found in this channel/thread.",
        ephemeral=True,
    )
