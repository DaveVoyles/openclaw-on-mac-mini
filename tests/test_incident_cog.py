"""Tests for incident list/timeline command behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from cogs import incident_cog as mod


class _InteractionStub:
    def __init__(self, *, channel=None, channel_id: int = 123):
        self.user = SimpleNamespace(id=42, __str__=lambda _self: "Alice")
        self.channel = channel or SimpleNamespace(id=channel_id, parent_id=None)
        self.channel_id = channel_id
        self.response = SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock())
        self.followup = SimpleNamespace(send=AsyncMock())


@pytest.mark.asyncio
async def test_incident_list_defaults_to_active_filter(monkeypatch):
    cog = mod.IncidentCog(SimpleNamespace())
    interaction = _InteractionStub()
    fake_store = SimpleNamespace(
        list_recent=MagicMock(
            return_value=[
                {
                    "id": 7,
                    "title": "API errors",
                    "severity": "high",
                    "status": "investigating",
                    "thread_id": 555,
                    "updated_at": 1_700_000_000.0,
                }
            ]
        )
    )
    monkeypatch.setattr(mod, "incident_store", fake_store)

    await cog.incident_list.callback(cog, interaction, state=None, limit=5)

    fake_store.list_recent.assert_called_once_with(limit=5, include_resolved=False)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "ACTIVE" in kwargs["embed"].title
    assert "#7" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_incident_timeline_uses_explicit_incident_id(monkeypatch):
    cog = mod.IncidentCog(SimpleNamespace())
    interaction = _InteractionStub()
    fake_store = SimpleNamespace(
        get_incident=MagicMock(
            return_value={
                "id": 9,
                "title": "Queue lag",
                "severity": "critical",
                "status": "monitoring",
                "thread_id": 321,
            }
        ),
        get_timeline=MagicMock(
            return_value=[
                {
                    "event_type": "status_update",
                    "note": "Mitigation applied",
                    "actor_name": "Bob",
                    "created_at": 1_700_000_100.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(mod, "incident_store", fake_store)

    await cog.incident_timeline.callback(cog, interaction, incident_id=9, limit=4)

    fake_store.get_incident.assert_called_once_with(9)
    fake_store.get_timeline.assert_called_once_with(9, limit=4)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "Incident #9 Timeline" in kwargs["embed"].title
    assert "Mitigation applied" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_incident_timeline_infers_incident_from_thread(monkeypatch):
    cog = mod.IncidentCog(SimpleNamespace())
    interaction = _InteractionStub(channel=SimpleNamespace(id=222, parent_id=111), channel_id=222)
    fake_store = SimpleNamespace(
        get_incident_for_thread=MagicMock(
            return_value={
                "id": 13,
                "title": "Thread incident",
                "severity": "high",
                "status": "open",
                "thread_id": 222,
            }
        ),
        get_latest_for_channel=MagicMock(return_value=None),
        get_timeline=MagicMock(
            return_value=[
                {
                    "event_type": "created",
                    "note": "Initial alert",
                    "actor_name": "Alice",
                    "created_at": 1_700_000_200.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(mod, "incident_store", fake_store)

    await cog.incident_timeline.callback(cog, interaction, incident_id=None, limit=3)

    fake_store.get_incident_for_thread.assert_called_once_with(222, include_resolved=True)
    fake_store.get_timeline.assert_called_once_with(13, limit=3)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_incident_start_falls_back_when_copilot_times_out(monkeypatch):
    cog = mod.IncidentCog(SimpleNamespace())
    interaction = _InteractionStub()
    fake_store = SimpleNamespace(
        create_incident=MagicMock(
            return_value={
                "id": 17,
                "title": "Container crash loop",
                "severity": "critical",
                "status": "open",
                "thread_id": None,
            }
        ),
        set_context=MagicMock(return_value=None),
        append_event=MagicMock(),
    )
    monkeypatch.setattr(mod, "incident_store", fake_store)
    monkeypatch.setattr(mod, "generate_incident_report", AsyncMock(side_effect=asyncio.TimeoutError()))
    monkeypatch.setattr(mod, "audit_log", MagicMock())
    monkeypatch.setattr(cog, "_try_create_thread", AsyncMock(return_value=None))

    await cog.incident_start.callback(
        cog,
        interaction,
        title="Container crash loop",
        severity=app_commands.Choice(name="critical", value="critical"),
        details="sonarr failing health checks",
        services="sonarr",
    )

    assert interaction.followup.send.await_count == 2
    report_embed = interaction.followup.send.await_args_list[0].kwargs["embed"]
    launch_embed = interaction.followup.send.await_args_list[1].kwargs["embed"]
    field_names = [f.name for f in report_embed.fields]
    assert "Copilot Status" in field_names
    launch_field_names = [f.name for f in launch_embed.fields]
    assert "Operator Commands" in launch_field_names
    assert any(call.kwargs.get("event_type") == "copilot_summary_error" for call in fake_store.append_event.call_args_list)


@pytest.mark.asyncio
async def test_incident_cog_command_error_timeout_gives_retry_guidance():
    cog = mod.IncidentCog(SimpleNamespace())
    interaction = SimpleNamespace(
        response=SimpleNamespace(
            is_done=MagicMock(return_value=False),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    error = asyncio.TimeoutError()

    await cog.cog_command_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.await_args.kwargs.get("embed")
    assert embed is not None
    description = getattr(embed, "description", "")
    assert "timed out" in description.lower()
