"""Tests for report rendering behavior in ReportsCog."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

import runtime_state as runtime_state_mod
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

    calls = interaction.followup.send.await_args_list
    assert len(calls) >= 1
    kwargs = calls[0].kwargs
    assert "embed" in kwargs
    assert "file" in kwargs
    assert "📋 Table" in kwargs["embed"].description
    assert "Full report attached as file" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_send_chunks_packages_multichunk_reports_as_single_attachment(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))

    monkeypatch.setattr(mod, "split_response", lambda _text: ["first chunk", "second chunk"])

    await cog._send_chunks(
        interaction,
        title="Weekly Recap",
        body="ignored because split_response mocked",
        color=discord.Color.blurple(),
    )

    calls = interaction.followup.send.await_args_list
    assert len(calls) == 1
    kwargs = calls[0].kwargs
    assert "embed" in kwargs
    assert "file" in kwargs
    assert kwargs["embed"].title == "Weekly Recap"
    assert "Full report attached as file" in kwargs["embed"].description


def test_extract_report_recovery_summary_builds_compact_line():
    report = (
        "## 📎 Coverage Summary\n"
        "- Coverage shortfall: add **2** more item(s) to hit the target.\n"
        "- Retry scope hint: narrow to one league/team or a shorter date window, then rerun.\n"
        "- Status: ⚠️ **Partial coverage**\n"
    )
    summary = mod._extract_report_recovery_summary(report)
    assert summary is not None
    assert "Partial coverage" in summary
    assert "add **2** more item(s)" in summary
    assert "Retry scope" in summary


def test_extract_report_recovery_summary_includes_runtime_constrained_signal():
    report = (
        "## 📎 Coverage Summary\n"
        "- Runtime mode: constrained (high latency)\n"
        "- Retry scope hint: narrow to one topic and shorter window, then rerun.\n"
    )
    summary = mod._extract_report_recovery_summary(report)
    assert summary is not None
    assert "Runtime constrained" in summary
    assert "Retry scope" in summary


@pytest.mark.asyncio
async def test_send_chunks_attachment_embed_includes_recovery_summary(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(mod, "split_response", lambda _text: ["chunk-a", "chunk-b"])
    body = (
        "## 📎 Coverage Summary\n"
        "- Coverage shortfall: add **2** more item(s) to hit the target.\n"
        "- Retry scope hint: narrow to one league/team or a shorter date window, then rerun.\n"
        "- Status: ⚠️ **Partial coverage**\n"
    )

    await cog._send_chunks(
        interaction,
        title="Weekly Recap",
        body=body,
        color=discord.Color.blurple(),
    )

    kwargs = interaction.followup.send.await_args_list[0].kwargs
    assert "embed" in kwargs
    assert "Partial coverage" in kwargs["embed"].description
    assert "Retry scope" in kwargs["embed"].description


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


@pytest.mark.asyncio
async def test_send_text_package_uses_attachment_for_chunked_payload(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(mod, "split_mobile_safe_bundle", lambda _text: ["part1", "part2"])

    await cog._send_text_package(
        interaction,
        label="🧾 Brief+Detail package",
        text="chunked payload",
    )

    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs
    assert "file" in kwargs
    assert kwargs["embed"].title == "🧾 Brief+Detail package"
    assert "Full package attached as file" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_send_text_package_sends_plain_message_for_single_chunk(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    monkeypatch.setattr(mod, "split_mobile_safe_bundle", lambda _text: ["single chunk"])

    await cog._send_text_package(
        interaction,
        label="📋 Copy-safe text bundle",
        text="single chunk",
    )

    interaction.followup.send.assert_awaited_once_with(
        "📋 Copy-safe text bundle\nsingle chunk",
        ephemeral=True,
    )


class _InteractionStub:
    def __init__(self, *, user_id: int = 123, channel_id: int = 456, display_name: str = "Dave"):
        self.user = SimpleNamespace(id=user_id, display_name=display_name)
        self.channel_id = channel_id
        self.response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())


@pytest.mark.asyncio
async def test_recap_copy_latest_returns_ephemeral_copy_block(monkeypatch, tmp_path):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub()
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "reports-cog-test.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

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
    runtime_state_mod._reset_channel_profile_store_for_tests()


@pytest.mark.asyncio
async def test_recap_copy_thread_uses_formatter_and_returns_ephemeral(monkeypatch, tmp_path):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub(channel_id=999)
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "reports-cog-test.db"))
    runtime_state_mod._reset_channel_profile_store_for_tests()

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
    runtime_state_mod._reset_channel_profile_store_for_tests()


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


@pytest.mark.asyncio
async def test_recap_package_latest_uses_selected_variant(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub(channel_id=321)

    conv = Conversation(user_name="Dave")
    conv.history = [{"role": "model", "parts": ["Report body\n- item one\n- item two"]}]
    monkeypatch.setattr(mod, "conversation_store", SimpleNamespace(get=lambda **_kwargs: conv))
    package_mock = AsyncMock()
    monkeypatch.setattr(cog, "_package_response", package_mock)

    await cog.recap_package_latest.callback(cog, interaction, variant="brief-detail")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    package_mock.assert_awaited_once_with(
        interaction,
        source_text="Report body\n- item one\n- item two",
        variant="brief-detail",
        source_label="latest response",
    )


@pytest.mark.asyncio
async def test_recap_package_thread_generates_report_and_packages(monkeypatch):
    cog = mod.ReportsCog(_DummyBot())
    interaction = _InteractionStub(channel_id=999)

    fake_reporting = types.SimpleNamespace(
        generate_channel_recap_report=AsyncMock(return_value="Thread recap body")
    )
    monkeypatch.setitem(sys.modules, "skills.reporting_skills", fake_reporting)
    package_mock = AsyncMock()
    monkeypatch.setattr(cog, "_package_response", package_mock)

    await cog.recap_package_thread.callback(
        cog,
        interaction,
        days=5,
        focus="risks",
        style="table",
        variant="artifact",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    fake_reporting.generate_channel_recap_report.assert_awaited_once_with(
        channel_id=999,
        days=5,
        focus="risks",
        style="table",
    )
    package_mock.assert_awaited_once_with(
        interaction,
        source_text="Thread recap body",
        variant="artifact",
        source_label="thread recap",
    )
