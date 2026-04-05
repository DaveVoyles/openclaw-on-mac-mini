"""Tests for runtime_state request context bindings and channel profiles."""

import runtime_state as mod
from runtime_state import (
    get_channel_profile,
    get_current_channel_id,
    get_current_thread_id,
    get_effective_channel_profile,
    request_context,
    set_channel_profile,
)


def test_request_context_sets_channel_and_thread():
    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_request_context_nested_contexts_do_not_bleed():
    with request_context(channel_id=100, thread_id=200):
        assert get_current_channel_id() == 100
        assert get_current_thread_id() == 200

        with request_context(channel_id=300):
            assert get_current_channel_id() == 300
            assert get_current_thread_id() == 200

        assert get_current_channel_id() == 100
        assert get_current_thread_id() == 200

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_request_context_resets_after_exception():
    try:
        with request_context(channel_id=77, thread_id=88):
            assert get_current_channel_id() == 77
            assert get_current_thread_id() == 88
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None

    with request_context(channel_id=123, thread_id=456):
        assert get_current_channel_id() == 123
        assert get_current_thread_id() == 456

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_channel_profile_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    profile = get_channel_profile(999)
    assert profile["tone"] == "neutral"
    assert profile["table_style"] == "discord"
    assert profile["emoji_level"] == "light"
    assert profile["report_depth"] == "standard"
    assert profile["source_strictness"] == "balanced"

    mod._reset_channel_profile_store_for_tests()


def test_channel_profile_persists_and_merges_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    first = set_channel_profile(123, tone="friendly", table_style="copy-safe")
    assert first["tone"] == "friendly"
    assert first["table_style"] == "copy-safe"

    second = set_channel_profile(123, report_depth="detailed")
    assert second["tone"] == "friendly"
    assert second["table_style"] == "copy-safe"
    assert second["report_depth"] == "detailed"

    reloaded = get_channel_profile(123)
    assert reloaded == second

    mod._reset_channel_profile_store_for_tests()


def test_thread_profile_falls_back_to_channel_then_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    set_channel_profile(321, tone="concise")
    inherited = get_channel_profile(321, thread_id=777)
    assert inherited["tone"] == "concise"

    set_channel_profile(321, thread_id=777, tone="analytical", emoji_level="none")
    overridden = get_channel_profile(321, thread_id=777)
    assert overridden["tone"] == "analytical"
    assert overridden["emoji_level"] == "none"

    with request_context(channel_id=321, thread_id=777):
        effective = get_effective_channel_profile()
        assert effective["tone"] == "analytical"

    mod._reset_channel_profile_store_for_tests()


def test_anchor_state_scoped_storage_and_reset():
    mod.reset_anchor_state()
    mod.set_anchor_state(10, 20, "anchor_1")
    mod.set_anchor_state(10, None, "anchor_2")

    thread_anchor = mod.get_anchor_state(channel_id=10, thread_id=20)
    channel_anchor = mod.get_anchor_state(channel_id=10, thread_id=None)

    assert thread_anchor["anchor_id"] == "anchor_1"
    assert channel_anchor["anchor_id"] == "anchor_2"
    assert mod.anchor_matches(10, 20) is True

    mod.reset_anchor_state(channel_id=10, thread_id=20)
    assert mod.get_anchor_state(channel_id=10, thread_id=20) is None
    assert mod.get_anchor_state(channel_id=10, thread_id=None)["anchor_id"] == "anchor_2"

    mod.reset_anchor_state()


def test_context_lock_round_trip():
    mod.reset_context_lock("u1")
    lock = mod.set_context_lock(user_id="u1", mode="thread", channel_id=123, thread_id=456, anchor_id="report_1")
    loaded = mod.get_context_lock("u1")
    assert lock["mode"] == "thread"
    assert loaded["channel_id"] == 123
    assert loaded["thread_id"] == 456
    assert loaded["anchor_id"] == "report_1"

    mod.reset_context_lock("u1")
    assert mod.get_context_lock("u1") is None


def test_scoped_recall_alerts_filtered_by_scope():
    mod.record_scoped_recall_alert(
        category="scope_guard_block",
        message="blocked",
        channel_id=100,
        thread_id=200,
        metadata={"blocked_cross_channel": 1},
    )
    mod.record_scoped_recall_alert(
        category="cross_channel_opt_in",
        message="opt-in",
        channel_id=300,
        thread_id=None,
        metadata={},
    )

    scoped = mod.get_scoped_recall_alerts(channel_id=100, thread_id=200, limit=5)
    assert scoped
    assert all(item["channel_id"] == "100" for item in scoped)
