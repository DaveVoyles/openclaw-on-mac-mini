"""Tests for Incident Copilot context and action generation."""

import pytest

import incident_copilot


def test_sanitize_actions_filters_unsafe_targets():
    raw = [
        {
            "title": "Restart Plex",
            "description": "Try restarting plex",
            "command": "restart_container",
            "target": "plex",
            "risk_level": "high",
            "rationale": "quick fix",
        },
        {
            "title": "Restart Sonarr",
            "description": "Try restarting sonarr",
            "command": "restart_container",
            "target": "sonarr",
            "risk_level": "high",
            "rationale": "quick fix",
        },
    ]
    sanitized = incident_copilot._sanitize_actions(raw)
    assert len(sanitized) == 2
    assert sanitized[0]["executable"] is False
    assert sanitized[0]["command"] == ""
    assert sanitized[1]["executable"] is True
    assert sanitized[1]["command"] == "restart_container"


@pytest.mark.asyncio
async def test_generate_incident_report_falls_back_to_heuristics(monkeypatch):
    async def fake_context(*_args, **_kwargs):
        return {
            "service_errors": {"sonarr": 5, "radarr": 1},
            "service_details": [],
            "health_trends": [],
            "audit_tail": [],
            "system_stats": "ok",
            "memory_hits": [],
            "services": ["sonarr", "radarr"],
        }

    async def fake_llm_chat(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(incident_copilot, "build_incident_context", fake_context)
    monkeypatch.setattr(incident_copilot, "llm_chat", fake_llm_chat)

    incident = {
        "id": 42,
        "title": "Sonarr failures",
        "description": "processing errors",
        "severity": "high",
        "status": "open",
    }
    report = await incident_copilot.generate_incident_report(incident)
    assert "summary" in report and report["summary"]
    assert report["model_used"] == "heuristic"
    assert report["actions"]
    assert report["actions"][0]["title"].lower().startswith("restart sonarr")
    assert report["actions"][0]["executable"] is True


@pytest.mark.asyncio
async def test_execute_incident_action_rejects_unsupported():
    result = await incident_copilot.execute_incident_action(
        {"command": "restart_container", "target": "plex", "executable": True}
    )
    assert "Unsupported" in result
