"""Tests for memory_compaction_state — event tracking and retrieval."""

from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear memory compaction events before each test."""
    import memory_compaction_state as mod

    with mod._MEMORY_COMPACTION_EVENTS_LOCK:
        mod._MEMORY_COMPACTION_EVENTS.clear()
    yield
    with mod._MEMORY_COMPACTION_EVENTS_LOCK:
        mod._MEMORY_COMPACTION_EVENTS.clear()


# ---------------------------------------------------------------------------
# record_memory_compaction_event
# ---------------------------------------------------------------------------


def test_record_event_returns_expected_shape():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="my-collection",
        channel_id=100,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=50,
        after_count=30,
        pruned_count=20,
    )
    assert event["collection"] == "my-collection"
    assert event["channel_id"] == "100"
    assert event["thread_id"] is None
    assert event["retention_class"] == "standard"
    assert event["memory_budget_items"] == 200
    assert event["before_count"] == 50
    assert event["after_count"] == 30
    assert event["pruned_count"] == 20
    assert isinstance(event["timestamp"], float)
    assert isinstance(event["metadata"], dict)


def test_record_event_with_metadata():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id=101,
        thread_id=None,
        retention_class="long",
        memory_budget_items=100,
        before_count=10,
        after_count=5,
        pruned_count=5,
        metadata={"reason": "overflow"},
    )
    assert event["metadata"]["reason"] == "overflow"


def test_record_event_thread_id_stored():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id=102,
        thread_id=1020,
        retention_class="short",
        memory_budget_items=50,
        before_count=10,
        after_count=8,
        pruned_count=2,
    )
    assert event["thread_id"] == "1020"


def test_record_event_none_channel_stored_as_none():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id=None,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    assert event["channel_id"] is None


def test_record_event_empty_string_channel_stored_as_none():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id="",
        thread_id="",
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    assert event["channel_id"] is None
    assert event["thread_id"] is None


def test_record_event_collection_stripped():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="  my-col  ",
        channel_id=103,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    assert event["collection"] == "my-col"


def test_record_event_empty_collection_defaults_to_unknown():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="",
        channel_id=104,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    assert event["collection"] == "unknown"


def test_record_event_retention_class_normalized():
    from memory_compaction_state import record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id=105,
        thread_id=None,
        retention_class="  LONG  ",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    assert event["retention_class"] == "long"


def test_record_event_returns_copy():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    event = record_memory_compaction_event(
        collection="col",
        channel_id=106,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    event["collection"] = "mutated"
    stored = get_memory_compaction_events(channel_id=106)
    assert stored[0]["collection"] == "col"


# ---------------------------------------------------------------------------
# get_memory_compaction_events — filtering and ordering
# ---------------------------------------------------------------------------


def test_get_events_returns_recorded():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="c1",
        channel_id=200,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    events = get_memory_compaction_events(channel_id=200)
    assert len(events) == 1


def test_get_events_filtered_by_channel():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="c1",
        channel_id=201,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    record_memory_compaction_event(
        collection="c2",
        channel_id=202,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    events = get_memory_compaction_events(channel_id=201)
    assert len(events) == 1
    assert events[0]["collection"] == "c1"


def test_get_events_filtered_by_thread():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="t1",
        channel_id=203,
        thread_id=2030,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    record_memory_compaction_event(
        collection="t2",
        channel_id=203,
        thread_id=2031,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    events = get_memory_compaction_events(channel_id=203, thread_id=2030)
    assert len(events) == 1
    assert events[0]["collection"] == "t1"


def test_get_events_sorted_newest_first():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="old",
        channel_id=204,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    time.sleep(0.01)
    record_memory_compaction_event(
        collection="new",
        channel_id=204,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=10,
        after_count=5,
        pruned_count=5,
    )
    events = get_memory_compaction_events(channel_id=204)
    assert events[0]["collection"] == "new"


def test_get_events_limit_respected():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    for i in range(10):
        record_memory_compaction_event(
            collection=f"col{i}",
            channel_id=205,
            thread_id=None,
            retention_class="standard",
            memory_budget_items=200,
            before_count=i,
            after_count=i,
            pruned_count=0,
        )
    events = get_memory_compaction_events(channel_id=205, limit=3)
    assert len(events) == 3


def test_get_events_no_filter_returns_all():
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="a",
        channel_id=206,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    record_memory_compaction_event(
        collection="b",
        channel_id=207,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    events = get_memory_compaction_events()
    assert len(events) >= 2


def test_get_events_empty_when_no_records():
    from memory_compaction_state import get_memory_compaction_events

    events = get_memory_compaction_events(channel_id=99999)
    assert events == []


def test_get_events_limit_capped_at_100():
    """Limit of 0 or negative should be treated as 1 (minimum)."""
    from memory_compaction_state import get_memory_compaction_events, record_memory_compaction_event

    record_memory_compaction_event(
        collection="x",
        channel_id=208,
        thread_id=None,
        retention_class="standard",
        memory_budget_items=200,
        before_count=0,
        after_count=0,
        pruned_count=0,
    )
    # limit=0 should return at least 1 (clamped internally)
    events = get_memory_compaction_events(channel_id=208, limit=0)
    assert len(events) >= 1


# ---------------------------------------------------------------------------
# Max size enforcement
# ---------------------------------------------------------------------------


def test_max_events_enforced():
    from memory_compaction_state import (
        _MAX_MEMORY_COMPACTION_EVENTS,
        _MEMORY_COMPACTION_EVENTS,
        _MEMORY_COMPACTION_EVENTS_LOCK,
        record_memory_compaction_event,
    )

    for i in range(_MAX_MEMORY_COMPACTION_EVENTS + 20):
        record_memory_compaction_event(
            collection=f"col{i}",
            channel_id=300,
            thread_id=None,
            retention_class="standard",
            memory_budget_items=200,
            before_count=i,
            after_count=i,
            pruned_count=0,
        )

    with _MEMORY_COMPACTION_EVENTS_LOCK:
        size = len(_MEMORY_COMPACTION_EVENTS)
    assert size <= _MAX_MEMORY_COMPACTION_EVENTS
