"""Targeted tests for research cog UX semantics and persistence receipts."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs import research_cog as mod


def _fake_interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=123, __str__=lambda self: "TestUser"),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_rerun_button_schedules_full_research_workflow():
    view = mod._ResearchView(query="openclaw roadmap", report="report body")
    rerun_btn = next(btn for btn in view.children if "Re-run full research" in str(btn.label))
    interaction = _fake_interaction()

    scheduler_stub = SimpleNamespace(
        scheduler=SimpleNamespace(
            create=MagicMock(return_value=SimpleNamespace(task_id="task-42"))
        )
    )

    with (
        patch.dict(sys.modules, {"scheduler": scheduler_stub}),
        patch.object(mod, "audit_log", MagicMock()),
    ):
        await rerun_btn.callback(interaction)

    scheduler_stub.scheduler.create.assert_called_once()
    kwargs = scheduler_stub.scheduler.create.call_args.kwargs
    assert kwargs["action"] == "run_scheduled_research"
    assert kwargs["args"]["query"] == "openclaw roadmap"
    assert kwargs["args"]["deep"] is False
    assert kwargs["interval_minutes"] == 1440

    message = interaction.response.send_message.await_args.args[0]
    assert "full research re-run every 24h" in message


def test_format_persistence_receipts_includes_expected_targets():
    text = mod._format_persistence_receipts(
        {
            "session": {"saved": True, "location": "discord-thread:12", "detail": "posted"},
            "vault": {"saved": True, "location": "data/vault/Research/a.md", "detail": "saved"},
            "vector": {"saved": True, "location": "research/r-1", "detail": "indexed"},
            "gdoc": {"saved": False, "location": "google-docs", "detail": "skipped"},
        }
    )

    assert "Persistence receipts" in text
    assert "Session" in text
    assert "Vault" in text
    assert "Vector" in text
    assert "Google Doc" in text
    assert "discord-thread:12" in text
    assert "research/r-1" in text
