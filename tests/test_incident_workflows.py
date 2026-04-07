"""Tests for incident workflow persistence and postmortem capture."""

import pytest

from incident_workflows import IncidentStore, parse_action_items


def test_parse_action_items_handles_list_formats():
    raw = "- Restart worker\n2) Add health checks; write runbook"
    assert parse_action_items(raw) == ["Restart worker", "Add health checks", "write runbook"]


def test_incident_lifecycle_transitions_and_timeline(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="API timeout spike",
        severity="high",
        description="Investigating elevated 5xx responses",
        channel_id=10,
        channel_name="ops",
        thread_id=None,
        thread_name=None,
        created_by=1,
        created_by_name="Alice",
        created_at=1_700_000_000.0,
    )
    assert incident["status"] == "open"
    assert incident["severity"] == "high"

    investigating = store.transition_status(
        incident["id"],
        new_status="investigating",
        note="Correlated to upstream dependency",
        actor_id=2,
        actor_name="Bob",
        changed_at=1_700_000_100.0,
    )
    assert investigating is not None
    assert investigating["status"] == "investigating"

    monitoring = store.transition_status(
        incident["id"],
        new_status="monitoring",
        note="Mitigation deployed",
        actor_id=2,
        actor_name="Bob",
        changed_at=1_700_000_200.0,
    )
    assert monitoring is not None
    assert monitoring["status"] == "monitoring"

    timeline = store.get_timeline(incident["id"])
    assert len(timeline) == 3
    assert timeline[0]["event_type"] == "status_update"
    assert timeline[-1]["event_type"] == "created"


def test_incident_resolve_captures_postmortem_and_blocks_updates(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="Queue backlog",
        severity="critical",
        description="Job queue saturation",
        channel_id=20,
        channel_name="incident-war-room",
        thread_id=200,
        thread_name="incident-200",
        created_by=3,
        created_by_name="Charlie",
    )
    resolved = store.resolve_incident(
        incident["id"],
        summary="Workers scaled and backlog drained.",
        action_items="- add autoscaling\n- add queue alerts",
        postmortem_notes="Need better pre-scaling thresholds.",
        actor_id=4,
        actor_name="Dana",
        resolved_at=1_700_000_500.0,
    )
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] == pytest.approx(1_700_000_500.0)
    assert resolved["action_items"] == ["add autoscaling", "add queue alerts"]
    assert "Workers scaled" in resolved["summary"]

    with pytest.raises(ValueError):
        store.transition_status(
            incident["id"],
            new_status="monitoring",
            note="post-resolution update",
            actor_id=4,
            actor_name="Dana",
        )

    timeline = store.get_timeline(incident["id"])
    event_types = [entry["event_type"] for entry in timeline]
    assert "resolved" in event_types
    assert "postmortem" in event_types


def test_create_incident_rejects_unknown_severity(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    with pytest.raises(ValueError):
        store.create_incident(
            title="Bad severity",
            severity="urgent",
            description="invalid severity path",
            channel_id=1,
            channel_name="ops",
            thread_id=None,
            thread_name=None,
            created_by=1,
            created_by_name="Alice",
        )


def test_transition_rejects_invalid_transition_and_resolved_filtering(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="Latency spike",
        severity="medium",
        description="initial",
        channel_id=1,
        channel_name="ops",
        thread_id=None,
        thread_name=None,
        created_by=1,
        created_by_name="Alice",
    )
    store.transition_status(
        incident["id"],
        new_status="investigating",
        note="triage",
        actor_id=2,
        actor_name="Bob",
    )
    with pytest.raises(ValueError):
        store.transition_status(
            incident["id"],
            new_status="open",
            note="rollback status",
            actor_id=2,
            actor_name="Bob",
        )

    store.resolve_incident(
        incident["id"],
        summary="fixed",
        action_items=[],
        postmortem_notes="",
        actor_id=2,
        actor_name="Bob",
    )
    unresolved = store.list_recent(limit=10, include_resolved=False)
    assert unresolved == []


def test_append_event_supports_incident_copilot_events(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="Copilot event capture",
        severity="high",
        description="initial",
        channel_id=1,
        channel_name="ops",
        thread_id=None,
        thread_name=None,
        created_by=7,
        created_by_name="Eve",
    )
    ok = store.append_event(
        incident["id"],
        event_type="copilot_summary",
        note='{"summary":"test"}',
        actor_id=8,
        actor_name="Frank",
    )
    assert ok is True
    timeline = store.get_timeline(incident["id"])
    assert timeline[0]["event_type"] == "copilot_summary"
    assert timeline[0]["status"] == "open"


def test_append_event_rejects_unknown_event_type(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="Bad event type",
        severity="medium",
        description="initial",
        channel_id=1,
        channel_name="ops",
        thread_id=None,
        thread_name=None,
        created_by=1,
        created_by_name="Alice",
    )
    with pytest.raises(ValueError):
        store.append_event(
            incident["id"],
            event_type="copilot_magic",
            note="invalid",
            actor_id=1,
            actor_name="Alice",
        )


def test_list_recent_filters_by_status_and_context(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    primary = store.create_incident(
        title="Primary incident",
        severity="high",
        description="initial",
        channel_id=11,
        channel_name="ops",
        thread_id=101,
        thread_name="incident-101",
        created_by=1,
        created_by_name="Alice",
    )
    secondary = store.create_incident(
        title="Secondary incident",
        severity="medium",
        description="initial",
        channel_id=11,
        channel_name="ops",
        thread_id=102,
        thread_name="incident-102",
        created_by=2,
        created_by_name="Bob",
    )
    store.transition_status(
        secondary["id"],
        new_status="investigating",
        note="triage",
        actor_id=2,
        actor_name="Bob",
    )
    store.resolve_incident(
        primary["id"],
        summary="resolved",
        action_items=[],
        postmortem_notes="",
        actor_id=1,
        actor_name="Alice",
    )

    investigating = store.list_recent(limit=5, status="investigating")
    assert [row["id"] for row in investigating] == [secondary["id"]]

    channel_rows = store.list_recent(limit=5, include_resolved=True, channel_id=11)
    assert {row["id"] for row in channel_rows} == {primary["id"], secondary["id"]}

    thread_rows = store.list_recent(limit=5, thread_id=101, include_resolved=True)
    assert [row["id"] for row in thread_rows] == [primary["id"]]


def test_context_lookup_prefers_latest_thread_and_channel(tmp_path):
    store = IncidentStore(tmp_path / "incidents.db")
    older = store.create_incident(
        title="Older thread incident",
        severity="high",
        description="initial",
        channel_id=20,
        channel_name="ops",
        thread_id=999,
        thread_name="incident-999",
        created_by=1,
        created_by_name="Alice",
        created_at=1_700_000_000.0,
    )
    latest = store.create_incident(
        title="Latest thread incident",
        severity="critical",
        description="initial",
        channel_id=20,
        channel_name="ops",
        thread_id=999,
        thread_name="incident-999",
        created_by=1,
        created_by_name="Alice",
        created_at=1_700_000_300.0,
    )
    assert store.get_incident_for_thread(999)["id"] == latest["id"]
    assert store.get_latest_for_channel(20)["id"] == latest["id"]

    store.resolve_incident(
        latest["id"],
        summary="fixed",
        action_items=[],
        postmortem_notes="",
        actor_id=1,
        actor_name="Alice",
        resolved_at=1_700_000_600.0,
    )
    unresolved = store.get_incident_for_thread(999, include_resolved=False)
    assert unresolved is not None
    assert unresolved["id"] == older["id"]
