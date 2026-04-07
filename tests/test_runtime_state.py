"""Tests for runtime_state request context bindings and channel profiles."""

import time

import pytest

import runtime_state as mod
from runtime_state import (
    RUNTIME_STATE_CONTEXTS,
    create_quality_eval_scorecard,
    get_channel_profile,
    get_current_channel_id,
    get_current_thread_id,
    get_effective_channel_profile,
    get_memory_lifecycle_policy,
    list_quality_eval_scorecards,
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


def test_runtime_state_contexts_expose_bounded_contexts_with_compatibility_facades():
    assert RUNTIME_STATE_CONTEXTS.channel_config is mod._CHANNEL_CONFIG_STATE
    assert RUNTIME_STATE_CONTEXTS.conversation is mod._CONVERSATION_STATE
    assert RUNTIME_STATE_CONTEXTS.interaction is mod._INTERACTION_STATE
    assert mod._CONTEXT_LOCKS is RUNTIME_STATE_CONTEXTS.conversation.context_locks
    assert mod._ANCHOR_STATE_BY_SCOPE is RUNTIME_STATE_CONTEXTS.conversation.anchor_state_by_scope
    assert mod._CHANNEL_PROFILE_DEFAULTS is RUNTIME_STATE_CONTEXTS.channel_config.defaults


def test_set_bot_updates_interaction_bounded_context():
    sentinel = object()
    mod.set_bot(sentinel)  # type: ignore[arg-type]
    assert mod.get_bot() is sentinel
    assert RUNTIME_STATE_CONTEXTS.interaction.bot is sentinel
    mod.set_bot(None)  # type: ignore[arg-type]


def test_channel_profile_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    profile = get_channel_profile(999)
    assert profile["tone"] == "neutral"
    assert profile["table_style"] == "discord"
    assert profile["emoji_level"] == "light"
    assert profile["report_depth"] == "standard"
    assert profile["source_strictness"] == "balanced"
    assert profile["memory_retention_class"] == "standard"
    assert profile["memory_budget_items"] == 200
    assert profile["retrieval_profile"] == "auto"
    assert profile["retrieval_min_results_override"] == 0

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


def test_memory_lifecycle_policy_inherits_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()


def test_channel_retrieval_overrides_are_bounded_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    updated = set_channel_profile(
        777,
        retrieval_profile="sports",
        retrieval_min_results_override=999,
        retrieval_max_query_variants_override=-5,
        retrieval_provider_attempt_cap_override="bad",
    )
    assert updated["retrieval_profile"] == "sports"
    assert updated["retrieval_min_results_override"] == 8
    assert updated["retrieval_max_query_variants_override"] == 0
    assert updated["retrieval_provider_attempt_cap_override"] == 0

    fallback = set_channel_profile(777, retrieval_profile="not-a-profile")
    assert fallback["retrieval_profile"] == "auto"

    mod._reset_channel_profile_store_for_tests()

    set_channel_profile(200, memory_retention_class="long", memory_budget_items=400)
    inherited = get_memory_lifecycle_policy(channel_id=200, thread_id=999)
    assert inherited["retention_class"] == "long"
    assert inherited["memory_budget_items"] == 400

    set_channel_profile(200, thread_id=999, memory_retention_class="short", memory_budget_items=80)
    overridden = get_memory_lifecycle_policy(channel_id=200, thread_id=999)
    assert overridden["retention_class"] == "short"
    assert overridden["memory_budget_items"] == 80

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


def test_resolve_context_lock_scope_mismatch_is_ignored():
    mod.reset_context_lock("u-lock-mismatch")
    mod.set_context_lock(user_id="u-lock-mismatch", mode="thread", channel_id=123, thread_id=456)

    lock, reason = mod.resolve_context_lock(
        user_id="u-lock-mismatch",
        channel_id=123,
        thread_id=999,
    )
    assert lock is None
    assert reason == "scope_mismatch"

    mod.reset_context_lock("u-lock-mismatch")


def test_resolve_context_lock_stale_is_ignored():
    mod.reset_context_lock("u-lock-stale")
    mod.set_context_lock(user_id="u-lock-stale", mode="channel", channel_id=123, thread_id=None)
    stale_ts = time.time() - (mod.CONTEXT_LOCK_EXPIRY_SECONDS + 10)
    mod._CONTEXT_LOCKS["u-lock-stale"]["updated_at"] = stale_ts

    lock, reason = mod.resolve_context_lock(
        user_id="u-lock-stale",
        channel_id=123,
        thread_id=None,
    )
    assert lock is None
    assert reason == "stale"
    assert mod.get_context_lock("u-lock-stale") is None


def test_resolve_anchor_state_stale_is_ignored():
    mod.reset_anchor_state()
    mod.set_anchor_state(
        321,
        654,
        "report_stale",
        timestamp=time.time() - (mod.ANCHOR_EXPIRY_SECONDS + 10),
    )

    anchor, reason = mod.resolve_anchor_state(channel_id=321, thread_id=654)
    assert anchor is None
    assert reason == "stale"
    assert mod.get_anchor_state(channel_id=321, thread_id=654) is None

    mod.reset_anchor_state()


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


def test_profile_recommendations_from_usage_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    for _ in range(3):
        mod.record_channel_profile_signal(901, signal="recap_copy_export")
    for _ in range(4):
        mod.record_channel_profile_signal(901, signal="recap_generated")

    recs = mod.list_channel_profile_recommendations(901)
    fields = {rec["profile_field"] for rec in recs}
    assert "table_style" in fields
    assert "report_depth" in fields

    mod._reset_channel_profile_store_for_tests()


def test_profile_recommendation_approve_apply_revert_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()

    for _ in range(3):
        mod.record_channel_profile_signal(902, signal="recap_copy_export")
    recs = mod.list_channel_profile_recommendations(902)
    rec = next(item for item in recs if item["profile_field"] == "table_style")
    rec_id = rec["recommendation_id"]

    with pytest.raises(ValueError):
        mod.update_channel_profile_recommendation(rec_id, action="apply", actor="tester")

    approved = mod.update_channel_profile_recommendation(rec_id, action="approve", actor="tester")
    assert approved["status"] == "approved"

    applied = mod.update_channel_profile_recommendation(rec_id, action="apply", actor="tester")
    assert applied["status"] == "applied"
    assert mod.get_channel_profile(902)["table_style"] == "copy-safe"

    reverted = mod.update_channel_profile_recommendation(rec_id, action="revert", actor="tester")
    assert reverted["status"] == "reverted"
    assert mod.get_channel_profile(902)["table_style"] == "discord"

    mod._reset_channel_profile_store_for_tests()


def test_memory_compaction_events_filtered_by_scope():
    mod.record_memory_compaction_event(
        collection="memories",
        channel_id=100,
        thread_id=200,
        retention_class="standard",
        memory_budget_items=100,
        before_count=150,
        after_count=100,
        pruned_count=50,
    )
    mod.record_memory_compaction_event(
        collection="research",
        channel_id=300,
        thread_id=None,
        retention_class="long",
        memory_budget_items=300,
        before_count=310,
        after_count=300,
        pruned_count=10,
    )

    scoped = mod.get_memory_compaction_events(channel_id=100, thread_id=200, limit=5)
    assert scoped
    assert all(item["channel_id"] == "100" for item in scoped)


def test_quality_eval_scorecard_calculates_metrics(monkeypatch):
    fake_runs = [
        {
            "question": "follow up on the previous report",
            "scope_mode": "thread",
            "anchor_id": "ask_123",
            "lock_mode": "none",
            "response_preview": "```text\n+---+\n| A |\n+---+\n```",
            "profile_values": {"emoji_level": "none", "report_depth": "brief", "tone": "concise", "table_style": "discord"},
        },
        {
            "question": "follow up with this too",
            "scope_mode": "thread",
            "anchor_id": "",
            "lock_mode": "none",
            "response_preview": "No anchor here 😅",
            "profile_values": {"emoji_level": "none", "report_depth": "brief", "tone": "concise", "table_style": "discord"},
        },
        {
            "question": "summarize [cross-channel] findings",
            "scope_mode": "cross-channel",
            "anchor_id": "",
            "lock_mode": "none",
            "response_preview": "📋 Table\n• Row 1\n  - Item: A",
            "profile_values": {"table_style": "copy-safe"},
        },
    ]
    monkeypatch.setitem(mod.__dict__, "get_scoped_recall_alerts", lambda limit=20, **_: [{"category": "scope_guard_block"}])
    monkeypatch.setitem(
        __import__("sys").modules,
        "error_tracker",
        type("FakeErrorTracker", (), {"get_recent_outcomes": staticmethod(lambda hours=24, limit=250: fake_runs)}),
    )

    scorecard = create_quality_eval_scorecard(persist=False)
    metrics = scorecard["metrics"]
    assert metrics["channel_leakage_prevention"]["pass"] >= 3
    assert metrics["followup_anchor_correctness"]["fail"] >= 1
    assert metrics["profile_adherence"]["sample"] >= 2
    assert metrics["table_readability_copy_safety"]["pass"] >= 2


def test_quality_eval_scorecard_persists_history(tmp_path, monkeypatch):
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    mod._reset_channel_profile_store_for_tests()
    monkeypatch.setitem(
        __import__("sys").modules,
        "error_tracker",
        type("FakeErrorTracker", (), {"get_recent_outcomes": staticmethod(lambda hours=24, limit=250: [])}),
    )

    first = create_quality_eval_scorecard(window_hours=6, persist=True)
    second = create_quality_eval_scorecard(window_hours=6, persist=True)
    history = list_quality_eval_scorecards(limit=5)

    assert first["scorecard_id"] is not None
    assert second["scorecard_id"] is not None
    assert len(history) >= 2
    assert history[0]["scorecard_id"] >= history[1]["scorecard_id"]
