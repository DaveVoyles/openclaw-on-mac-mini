"""Tests for discord_web /metrics output."""

import time
from types import SimpleNamespace

import pytest

import discord_web as mod
from metrics_collector import MetricsCollector


def _request_for_metrics():
    bot = SimpleNamespace(
        start_time=time.monotonic() - 30,
        guilds=[object(), object()],
        latency=0.123,
    )
    return SimpleNamespace(app={"bot": bot})


@pytest.mark.asyncio
async def test_metrics_handler_includes_basic_and_collector_metrics(monkeypatch):
    collector = MetricsCollector()
    collector.record_command("ask", "user1", "general", 0.42)
    collector.record_quality_event("recap_partial_coverage_warning", "sports_recap")
    monkeypatch.setattr(mod, "get_collector", lambda: collector)

    response = await mod._metrics_handler(_request_for_metrics())
    body = response.body.decode("utf-8")

    assert response.status == 200
    assert body.strip()
    assert "openclaw_up 1" in body
    assert "openclaw_uptime_seconds" in body
    assert "openclaw_guilds 2" in body
    assert "openclaw_commands_total" in body
    assert "openclaw_messages_processed_total" in body
    assert "openclaw_quality_events_total" in body


@pytest.mark.asyncio
async def test_metrics_handler_uses_collector_content_type(monkeypatch):
    class FakeCollector:
        def export_prometheus(self) -> bytes:
            return b"# HELP custom_metric custom\n# TYPE custom_metric gauge\ncustom_metric 1\n"

        def get_prometheus_content_type(self) -> str:
            return "text/plain; version=0.0.4; charset=utf-8"

    monkeypatch.setattr(mod, "get_collector", lambda: FakeCollector())

    response = await mod._metrics_handler(_request_for_metrics())
    body = response.body.decode("utf-8")

    assert response.headers["Content-Type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert "custom_metric 1" in body
    assert "openclaw_latency_ms" in body
