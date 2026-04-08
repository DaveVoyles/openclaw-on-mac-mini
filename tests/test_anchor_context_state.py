"""Tests for anchor_context_state — anchor CRUD, context locks, scoped recall alerts."""

from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear all anchor/lock/alert state before each test."""
    import anchor_context_state as mod

    mod.reset_anchor_state()
    # Clear context locks
    with mod._CONTEXT_LOCKS_LOCK:
        mod._CONTEXT_LOCKS.clear()
    # Clear scoped recall alerts
    with mod._SCOPED_RECALL_ALERTS_LOCK:
        mod._SCOPED_RECALL_ALERTS.clear()
    yield
    # Teardown
    mod.reset_anchor_state()
    with mod._CONTEXT_LOCKS_LOCK:
        mod._CONTEXT_LOCKS.clear()
    with mod._SCOPED_RECALL_ALERTS_LOCK:
        mod._SCOPED_RECALL_ALERTS.clear()


# ---------------------------------------------------------------------------
# set_anchor_state / get_anchor_state — basic
# ---------------------------------------------------------------------------


def test_set_anchor_state_returns_expected_shape():
    from anchor_context_state import set_anchor_state

    anchor = set_anchor_state(1, None, "anchor-abc")
    assert anchor["channel_id"] == 1
    assert anchor["thread_id"] is None
    assert anchor["anchor_id"] == "anchor-abc"
    assert isinstance(anchor["timestamp"], float)


def test_get_anchor_state_returns_last_set():
    from anchor_context_state import get_anchor_state, set_anchor_state

    set_anchor_state(10, None, "anc-1")
    anchor = get_anchor_state()
    assert anchor is not None
    assert anchor["anchor_id"] == "anc-1"


def test_get_anchor_state_scoped_by_channel_and_thread():
    from anchor_context_state import get_anchor_state, set_anchor_state

    set_anchor_state(20, 200, "anc-thread")
    set_anchor_state(20, None, "anc-channel")

    thread_anchor = get_anchor_state(channel_id=20, thread_id=200)
    channel_anchor = get_anchor_state(channel_id=20)
    assert thread_anchor["anchor_id"] == "anc-thread"
    assert channel_anchor["anchor_id"] == "anc-channel"


def test_get_anchor_state_returns_none_for_unknown_scope():
    from anchor_context_state import get_anchor_state

    assert get_anchor_state(channel_id=9999) is None


def test_set_anchor_state_updates_last_anchor():
    from anchor_context_state import get_anchor_state, set_anchor_state

    set_anchor_state(30, None, "first")
    set_anchor_state(30, None, "second")
    anchor = get_anchor_state()
    assert anchor["anchor_id"] == "second"


def test_set_anchor_state_with_explicit_timestamp():
    from anchor_context_state import get_anchor_state, set_anchor_state

    ts = time.time()
    anchor = set_anchor_state(40, None, "anc-ts", timestamp=ts)
    assert anchor["timestamp"] == ts
    fetched = get_anchor_state(channel_id=40)
    assert fetched["timestamp"] == ts


def test_anchor_state_returns_copy_not_reference():
    from anchor_context_state import get_anchor_state, set_anchor_state

    set_anchor_state(50, None, "copy-test")
    a1 = get_anchor_state()
    a1["anchor_id"] = "mutated"
    a2 = get_anchor_state()
    assert a2["anchor_id"] == "copy-test"


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_anchor_expired_on_get():
    from anchor_context_state import ANCHOR_EXPIRY_SECONDS, get_anchor_state, set_anchor_state

    old_ts = time.time() - ANCHOR_EXPIRY_SECONDS - 1
    set_anchor_state(60, None, "old-anchor", timestamp=old_ts)
    # Should return None because expired
    result = get_anchor_state(channel_id=60)
    assert result is None


def test_expired_last_anchor_cleared():
    from anchor_context_state import ANCHOR_EXPIRY_SECONDS, get_anchor_state, set_anchor_state

    old_ts = time.time() - ANCHOR_EXPIRY_SECONDS - 1
    set_anchor_state(61, None, "old", timestamp=old_ts)
    # Global lookup also should be None
    result = get_anchor_state()
    assert result is None


# ---------------------------------------------------------------------------
# reset_anchor_state
# ---------------------------------------------------------------------------


def test_reset_anchor_state_specific_scope():
    from anchor_context_state import get_anchor_state, reset_anchor_state, set_anchor_state

    set_anchor_state(70, None, "anc-70")
    set_anchor_state(71, None, "anc-71")
    reset_anchor_state(channel_id=70)
    assert get_anchor_state(channel_id=70) is None
    assert get_anchor_state(channel_id=71) is not None


def test_reset_anchor_state_all():
    from anchor_context_state import get_anchor_state, reset_anchor_state, set_anchor_state

    set_anchor_state(80, None, "anc-80")
    set_anchor_state(81, 8100, "anc-81")
    reset_anchor_state()
    assert get_anchor_state() is None
    assert get_anchor_state(channel_id=80) is None
    assert get_anchor_state(channel_id=81, thread_id=8100) is None


def test_reset_anchor_state_clears_last_anchor():
    from anchor_context_state import get_anchor_state, reset_anchor_state, set_anchor_state

    set_anchor_state(90, None, "last-one")
    reset_anchor_state(channel_id=90)
    assert get_anchor_state() is None


def test_reset_anchor_thread_scope():
    from anchor_context_state import get_anchor_state, reset_anchor_state, set_anchor_state

    set_anchor_state(91, 910, "thread-anchor")
    reset_anchor_state(channel_id=91, thread_id=910)
    assert get_anchor_state(channel_id=91, thread_id=910) is None


# ---------------------------------------------------------------------------
# anchor_matches
# ---------------------------------------------------------------------------


def test_anchor_matches_returns_true_when_present():
    from anchor_context_state import anchor_matches, set_anchor_state

    set_anchor_state(100, None, "match-me")
    assert anchor_matches(100, None) is True


def test_anchor_matches_returns_false_when_absent():
    from anchor_context_state import anchor_matches

    assert anchor_matches(9999, None) is False


def test_anchor_matches_thread_scope():
    from anchor_context_state import anchor_matches, set_anchor_state

    set_anchor_state(101, 1010, "t-anchor")
    assert anchor_matches(101, 1010) is True
    assert anchor_matches(101, None) is False


# ---------------------------------------------------------------------------
# resolve_anchor_state
# ---------------------------------------------------------------------------


def test_resolve_anchor_state_returns_anchor():
    from anchor_context_state import resolve_anchor_state, set_anchor_state

    set_anchor_state(110, None, "resolved")
    anchor, reason = resolve_anchor_state(channel_id=110, thread_id=None)
    assert anchor is not None
    assert anchor["anchor_id"] == "resolved"
    assert reason is None


def test_resolve_anchor_state_no_scope():
    from anchor_context_state import resolve_anchor_state

    anchor, reason = resolve_anchor_state(channel_id=None, thread_id=None)
    assert anchor is None
    assert reason == "no_scope"


def test_resolve_anchor_state_stale():
    from anchor_context_state import ANCHOR_EXPIRY_SECONDS, resolve_anchor_state, set_anchor_state

    old_ts = time.time() - ANCHOR_EXPIRY_SECONDS - 1
    set_anchor_state(111, None, "stale", timestamp=old_ts)
    anchor, reason = resolve_anchor_state(channel_id=111, thread_id=None)
    assert anchor is None
    assert reason == "stale"


# ---------------------------------------------------------------------------
# set_context_lock / get_context_lock
# ---------------------------------------------------------------------------


def test_set_context_lock_returns_expected_shape():
    from anchor_context_state import set_context_lock

    lock = set_context_lock(user_id="u1", mode="channel", channel_id=200)
    assert lock["mode"] == "channel"
    assert lock["channel_id"] == 200
    assert lock["thread_id"] is None
    assert "updated_at" in lock


def test_get_context_lock_returns_set_lock():
    from anchor_context_state import get_context_lock, set_context_lock

    set_context_lock(user_id="u2", mode="thread", channel_id=210, thread_id=2100)
    lock = get_context_lock("u2")
    assert lock is not None
    assert lock["mode"] == "thread"
    assert lock["thread_id"] == 2100


def test_get_context_lock_returns_none_for_unknown_user():
    from anchor_context_state import get_context_lock

    assert get_context_lock("nobody") is None


def test_get_context_lock_none_user_returns_none():
    from anchor_context_state import get_context_lock

    assert get_context_lock(None) is None


def test_get_context_lock_empty_string_returns_none():
    from anchor_context_state import get_context_lock

    assert get_context_lock("") is None


def test_set_context_lock_normalizes_mode():
    from anchor_context_state import get_context_lock, set_context_lock

    set_context_lock(user_id="u3", mode="CHANNEL", channel_id=220)
    lock = get_context_lock("u3")
    assert lock["mode"] == "channel"


def test_set_context_lock_strips_whitespace_mode():
    from anchor_context_state import get_context_lock, set_context_lock

    set_context_lock(user_id="u4", mode="  thread  ", channel_id=230, thread_id=2300)
    lock = get_context_lock("u4")
    assert lock["mode"] == "thread"


# ---------------------------------------------------------------------------
# reset_context_lock
# ---------------------------------------------------------------------------


def test_reset_context_lock_removes_lock():
    from anchor_context_state import get_context_lock, reset_context_lock, set_context_lock

    set_context_lock(user_id="u5", mode="channel", channel_id=240)
    reset_context_lock("u5")
    assert get_context_lock("u5") is None


def test_reset_context_lock_noop_for_unknown_user():
    from anchor_context_state import reset_context_lock

    reset_context_lock("nobody-here")  # Should not raise


def test_reset_context_lock_none_is_noop():
    from anchor_context_state import reset_context_lock

    reset_context_lock(None)  # Should not raise


# ---------------------------------------------------------------------------
# resolve_context_lock
# ---------------------------------------------------------------------------


def test_resolve_context_lock_returns_valid_lock():
    from anchor_context_state import resolve_context_lock, set_context_lock

    set_context_lock(user_id="u6", mode="channel", channel_id=250)
    lock, reason = resolve_context_lock(user_id="u6", channel_id=250, thread_id=None)
    assert lock is not None
    assert reason is None


def test_resolve_context_lock_scope_mismatch():
    from anchor_context_state import resolve_context_lock, set_context_lock

    set_context_lock(user_id="u7", mode="channel", channel_id=260)
    lock, reason = resolve_context_lock(user_id="u7", channel_id=999, thread_id=None)
    assert lock is None
    assert reason == "scope_mismatch"


def test_resolve_context_lock_stale():
    """Lock with updated_at=0 should be treated as stale."""
    from anchor_context_state import _CONTEXT_LOCKS, _CONTEXT_LOCKS_LOCK, resolve_context_lock

    with _CONTEXT_LOCKS_LOCK:
        _CONTEXT_LOCKS["u8"] = {
            "mode": "channel",
            "channel_id": 270,
            "thread_id": None,
            "anchor_id": None,
            "updated_at": 0,  # zero → treated as stale
        }
    lock, reason = resolve_context_lock(user_id="u8", channel_id=270, thread_id=None)
    assert lock is None
    assert reason == "stale"


def test_resolve_context_lock_no_user():
    from anchor_context_state import resolve_context_lock

    lock, reason = resolve_context_lock(user_id=None, channel_id=280, thread_id=None)
    assert lock is None
    assert reason is None


def test_resolve_context_lock_invalid_mode():
    from anchor_context_state import _CONTEXT_LOCKS, _CONTEXT_LOCKS_LOCK, resolve_context_lock

    with _CONTEXT_LOCKS_LOCK:
        _CONTEXT_LOCKS["u9"] = {
            "mode": "badmode",
            "channel_id": 290,
            "thread_id": None,
            "anchor_id": None,
            "updated_at": time.time(),
        }
    lock, reason = resolve_context_lock(user_id="u9", channel_id=290, thread_id=None)
    assert lock is None
    assert reason == "invalid"


def test_resolve_context_lock_thread_mode_mismatch():
    from anchor_context_state import resolve_context_lock, set_context_lock

    set_context_lock(user_id="u10", mode="thread", channel_id=300, thread_id=3000)
    # Different thread should mismatch
    lock, reason = resolve_context_lock(user_id="u10", channel_id=300, thread_id=9999)
    assert lock is None
    assert reason == "scope_mismatch"


# ---------------------------------------------------------------------------
# record_scoped_recall_alert / get_scoped_recall_alerts
# ---------------------------------------------------------------------------


def test_record_scoped_recall_alert_returns_event():
    from anchor_context_state import record_scoped_recall_alert

    event = record_scoped_recall_alert(
        category="test_cat",
        message="hello",
        channel_id=400,
        thread_id=None,
    )
    assert event["category"] == "test_cat"
    assert event["message"] == "hello"
    assert event["channel_id"] == "400"
    assert event["thread_id"] is None
    assert isinstance(event["timestamp"], float)


def test_get_scoped_recall_alerts_returns_recorded_events():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(category="info", message="msg1", channel_id=410, thread_id=None)
    record_scoped_recall_alert(category="warn", message="msg2", channel_id=410, thread_id=None)
    alerts = get_scoped_recall_alerts(channel_id=410)
    assert len(alerts) == 2


def test_scoped_recall_alerts_filtered_by_channel():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(category="x", message="a", channel_id=420, thread_id=None)
    record_scoped_recall_alert(category="x", message="b", channel_id=421, thread_id=None)
    alerts = get_scoped_recall_alerts(channel_id=420)
    assert len(alerts) == 1
    assert alerts[0]["message"] == "a"


def test_scoped_recall_alerts_filtered_by_thread():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(category="x", message="t1", channel_id=430, thread_id=4300)
    record_scoped_recall_alert(category="x", message="t2", channel_id=430, thread_id=4301)
    alerts = get_scoped_recall_alerts(channel_id=430, thread_id=4300)
    assert len(alerts) == 1
    assert alerts[0]["message"] == "t1"


def test_scoped_recall_alerts_sorted_newest_first():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(category="x", message="old", channel_id=440, thread_id=None,
                                metadata={"ts_override": 1})
    time.sleep(0.01)
    record_scoped_recall_alert(category="x", message="new", channel_id=440, thread_id=None)
    alerts = get_scoped_recall_alerts(channel_id=440)
    assert alerts[0]["message"] == "new"


def test_scoped_recall_alerts_limit_respected():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    for i in range(10):
        record_scoped_recall_alert(category="x", message=f"m{i}", channel_id=450, thread_id=None)
    alerts = get_scoped_recall_alerts(channel_id=450, limit=3)
    assert len(alerts) == 3


def test_scoped_recall_alert_message_truncated_at_240():
    from anchor_context_state import record_scoped_recall_alert

    long_msg = "x" * 300
    event = record_scoped_recall_alert(category="t", message=long_msg, channel_id=460, thread_id=None)
    assert len(event["message"]) <= 240


def test_scoped_recall_alert_category_normalized():
    from anchor_context_state import record_scoped_recall_alert

    event = record_scoped_recall_alert(
        category="  SCOPE_GUARD_BLOCK  ", message="blocked", channel_id=470, thread_id=None
    )
    assert event["category"] == "scope_guard_block"


def test_scoped_recall_alerts_none_channel_returns_all():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(category="x", message="m1", channel_id=480, thread_id=None)
    record_scoped_recall_alert(category="x", message="m2", channel_id=481, thread_id=None)
    alerts = get_scoped_recall_alerts()
    assert len(alerts) >= 2


def test_scoped_recall_alert_metadata_stored():
    from anchor_context_state import get_scoped_recall_alerts, record_scoped_recall_alert

    record_scoped_recall_alert(
        category="x",
        message="meta",
        channel_id=490,
        thread_id=None,
        metadata={"key": "val"},
    )
    alerts = get_scoped_recall_alerts(channel_id=490)
    assert alerts[0]["metadata"]["key"] == "val"


def test_scoped_recall_alert_max_size_enforced():
    """Exceed max and ensure oldest are dropped."""
    from anchor_context_state import (
        _MAX_SCOPED_RECALL_ALERTS,
        _SCOPED_RECALL_ALERTS,
        _SCOPED_RECALL_ALERTS_LOCK,
        record_scoped_recall_alert,
    )

    # Fill to capacity + 10
    for i in range(_MAX_SCOPED_RECALL_ALERTS + 10):
        record_scoped_recall_alert(category="x", message=f"msg{i}", channel_id=500, thread_id=None)
    with _SCOPED_RECALL_ALERTS_LOCK:
        size = len(_SCOPED_RECALL_ALERTS)
    assert size <= _MAX_SCOPED_RECALL_ALERTS
