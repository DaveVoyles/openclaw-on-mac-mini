"""Tests for channel_profile_state — CRUD, normalization, signals, and recommendations."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_db(tmp_path, monkeypatch):
    """Point the DB at a temp file and reset the connection before each test."""
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "openclaw-test.db"))
    import channel_profile_state as mod

    mod._reset_channel_profile_store_for_tests()
    yield
    mod._reset_channel_profile_store_for_tests()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_get_channel_profile_returns_defaults_for_unknown_channel():
    from channel_profile_state import get_channel_profile

    profile = get_channel_profile(9999)
    assert profile["tone"] == "neutral"
    assert profile["table_style"] == "discord"
    assert profile["emoji_level"] == "light"
    assert profile["report_depth"] == "standard"
    assert profile["source_strictness"] == "balanced"
    assert profile["memory_retention_class"] == "standard"
    assert profile["memory_budget_items"] == 200
    assert profile["retrieval_profile"] == "auto"
    assert profile["retrieval_min_results_override"] == 0
    assert profile["retrieval_max_query_variants_override"] == 0
    assert profile["retrieval_provider_attempt_cap_override"] == 0


def test_get_channel_profile_returns_defaults_for_none_channel():
    from channel_profile_state import get_channel_profile

    profile = get_channel_profile(None)
    assert profile["tone"] == "neutral"
    assert profile["memory_budget_items"] == 200


def test_get_channel_profile_defaults_returns_dict_copy():
    from channel_profile_state import get_channel_profile_defaults

    d1 = get_channel_profile_defaults()
    d2 = get_channel_profile_defaults()
    assert d1 == d2
    d1["tone"] = "mutated"
    assert get_channel_profile_defaults()["tone"] != "mutated"


# ---------------------------------------------------------------------------
# set_channel_profile / get_channel_profile — basic CRUD
# ---------------------------------------------------------------------------


def test_set_and_get_channel_profile_string_fields():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(1, tone="concise", emoji_level="none", report_depth="brief")
    p = get_channel_profile(1)
    assert p["tone"] == "concise"
    assert p["emoji_level"] == "none"
    assert p["report_depth"] == "brief"
    # Unchanged defaults
    assert p["table_style"] == "discord"


def test_set_channel_profile_updates_existing_record():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(2, tone="concise")
    set_channel_profile(2, tone="analytical")
    p = get_channel_profile(2)
    assert p["tone"] == "analytical"


def test_set_channel_profile_integer_fields():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(3, memory_budget_items=500, retrieval_min_results_override=3)
    p = get_channel_profile(3)
    assert p["memory_budget_items"] == 500
    assert p["retrieval_min_results_override"] == 3


def test_set_channel_profile_all_retrieval_overrides():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(
        4,
        retrieval_min_results_override=2,
        retrieval_max_query_variants_override=4,
        retrieval_provider_attempt_cap_override=5,
    )
    p = get_channel_profile(4)
    assert p["retrieval_min_results_override"] == 2
    assert p["retrieval_max_query_variants_override"] == 4
    assert p["retrieval_provider_attempt_cap_override"] == 5


def test_set_channel_profile_raises_on_missing_channel_id():
    from channel_profile_state import set_channel_profile

    with pytest.raises(ValueError):
        set_channel_profile(0)


def test_channel_profile_invalid_string_value_falls_back_to_default():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(5, tone="invalid_tone_xyz")
    p = get_channel_profile(5)
    assert p["tone"] == "neutral"


def test_channel_profile_invalid_int_value_falls_back_to_default():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(6, memory_budget_items="not-a-number")
    p = get_channel_profile(6)
    assert p["memory_budget_items"] == 200


def test_channel_profile_int_bounds_clamping():
    from channel_profile_state import get_channel_profile, set_channel_profile

    # retrieval_min_results_override has bounds (0, 8)
    set_channel_profile(7, retrieval_min_results_override=999)
    p = get_channel_profile(7)
    assert p["retrieval_min_results_override"] == 8


def test_channel_profile_memory_budget_lower_bound():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(8, memory_budget_items=0)
    p = get_channel_profile(8)
    assert p["memory_budget_items"] >= 1


def test_channel_profile_memory_budget_upper_bound():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(9, memory_budget_items=99999)
    p = get_channel_profile(9)
    assert p["memory_budget_items"] <= 5000


# ---------------------------------------------------------------------------
# Thread scoping
# ---------------------------------------------------------------------------


def test_channel_profile_thread_override_takes_precedence():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(10, tone="neutral")
    set_channel_profile(10, thread_id=1001, tone="concise")
    # Thread-scoped read
    assert get_channel_profile(10, thread_id=1001)["tone"] == "concise"
    # Channel-level read unaffected
    assert get_channel_profile(10)["tone"] == "neutral"


def test_channel_profile_thread_fallback_to_channel():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(11, tone="analytical")
    # Thread with no dedicated row falls back to channel row
    p = get_channel_profile(11, thread_id=9999)
    assert p["tone"] == "analytical"


def test_channel_profile_different_channels_isolated():
    from channel_profile_state import get_channel_profile, set_channel_profile

    set_channel_profile(100, tone="concise")
    set_channel_profile(200, tone="analytical")
    assert get_channel_profile(100)["tone"] == "concise"
    assert get_channel_profile(200)["tone"] == "analytical"


# ---------------------------------------------------------------------------
# clear_channel_profile
# ---------------------------------------------------------------------------


def test_clear_channel_profile_removes_row():
    from channel_profile_state import clear_channel_profile, get_channel_profile, set_channel_profile

    set_channel_profile(20, tone="concise")
    clear_channel_profile(20)
    p = get_channel_profile(20)
    assert p["tone"] == "neutral"  # back to default


def test_clear_channel_profile_thread_only():
    from channel_profile_state import clear_channel_profile, get_channel_profile, set_channel_profile

    set_channel_profile(21, tone="analytical")
    set_channel_profile(21, thread_id=111, tone="concise")
    clear_channel_profile(21, thread_id=111)
    # Thread now falls back to channel profile
    assert get_channel_profile(21, thread_id=111)["tone"] == "analytical"
    # Channel still intact
    assert get_channel_profile(21)["tone"] == "analytical"


def test_clear_channel_profile_noop_for_zero_channel():
    from channel_profile_state import clear_channel_profile

    # Should not raise
    clear_channel_profile(0)


# ---------------------------------------------------------------------------
# get_effective_channel_profile — request context integration
# ---------------------------------------------------------------------------


def test_get_effective_channel_profile_uses_request_context():
    from channel_profile_state import (
        get_effective_channel_profile,
        request_context,
        set_channel_profile,
    )

    set_channel_profile(30, tone="analytical")
    with request_context(channel_id=30):
        p = get_effective_channel_profile()
    assert p["tone"] == "analytical"


def test_get_effective_channel_profile_explicit_overrides_context():
    from channel_profile_state import (
        get_effective_channel_profile,
        request_context,
        set_channel_profile,
    )

    set_channel_profile(31, tone="concise")
    set_channel_profile(32, tone="friendly")
    with request_context(channel_id=31):
        p = get_effective_channel_profile(channel_id=32)
    assert p["tone"] == "friendly"


# ---------------------------------------------------------------------------
# get_memory_lifecycle_policy
# ---------------------------------------------------------------------------


def test_get_memory_lifecycle_policy_defaults():
    from channel_profile_state import get_memory_lifecycle_policy

    policy = get_memory_lifecycle_policy(channel_id=None)
    assert policy["retention_class"] == "standard"
    assert policy["memory_budget_items"] == 200


def test_get_memory_lifecycle_policy_from_profile():
    from channel_profile_state import get_memory_lifecycle_policy, set_channel_profile

    set_channel_profile(40, memory_retention_class="long", memory_budget_items=300)
    policy = get_memory_lifecycle_policy(channel_id=40)
    assert policy["retention_class"] == "long"
    assert policy["memory_budget_items"] == 300


# ---------------------------------------------------------------------------
# record_channel_profile_signal / get_channel_profile_usage_signals
# ---------------------------------------------------------------------------


def test_record_usage_signal_increments_count():
    from channel_profile_state import (
        get_channel_profile_usage_signals,
        record_channel_profile_signal,
    )

    record_channel_profile_signal(50, signal="table_render_discord")
    signals = get_channel_profile_usage_signals(50)
    assert signals["table_render_discord"] == 1


def test_record_usage_signal_cumulates():
    from channel_profile_state import (
        get_channel_profile_usage_signals,
        record_channel_profile_signal,
    )

    record_channel_profile_signal(51, signal="recap_generated")
    record_channel_profile_signal(51, signal="recap_generated")
    record_channel_profile_signal(51, signal="recap_generated")
    signals = get_channel_profile_usage_signals(51)
    assert signals["recap_generated"] == 3


def test_record_usage_signal_invalid_ignored():
    from channel_profile_state import (
        get_channel_profile_usage_signals,
        record_channel_profile_signal,
    )

    record_channel_profile_signal(52, signal="not_a_real_signal")
    signals = get_channel_profile_usage_signals(52)
    assert all(v == 0 for v in signals.values())


def test_record_usage_signal_none_channel_noop():
    from channel_profile_state import record_channel_profile_signal

    # Should not raise
    record_channel_profile_signal(None, signal="recap_generated")


def test_usage_signals_zero_for_unknown_channel():
    from channel_profile_state import get_channel_profile_usage_signals

    signals = get_channel_profile_usage_signals(99999)
    assert all(v == 0 for v in signals.values())


def test_usage_signals_thread_scoped():
    from channel_profile_state import (
        get_channel_profile_usage_signals,
        record_channel_profile_signal,
    )

    record_channel_profile_signal(53, thread_id=5300, signal="recap_copy_export")
    # Channel-level query should not see thread signal
    assert get_channel_profile_usage_signals(53)["recap_copy_export"] == 0
    # Thread-level query should see it
    assert get_channel_profile_usage_signals(53, thread_id=5300)["recap_copy_export"] == 1


def test_usage_signals_all_keys_present():
    from channel_profile_state import get_channel_profile_usage_signals

    signals = get_channel_profile_usage_signals(99998)
    expected_keys = {
        "table_render_discord",
        "table_render_copy_safe",
        "recap_generated",
        "recap_copy_export",
    }
    assert expected_keys.issubset(signals.keys())


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def test_recommendations_empty_when_no_signals():
    from channel_profile_state import list_channel_profile_recommendations

    recs = list_channel_profile_recommendations(60, refresh=True)
    assert recs == []


def test_recommendation_generated_for_copy_exports():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
    )

    for _ in range(3):
        record_channel_profile_signal(61, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(61, refresh=True)
    assert any(r["profile_field"] == "table_style" for r in recs)


def test_recommendation_for_frequent_recaps():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
    )

    for _ in range(5):
        record_channel_profile_signal(62, signal="recap_generated")
    recs = list_channel_profile_recommendations(62, refresh=True)
    assert any(r["profile_field"] == "report_depth" for r in recs)


def test_recommendation_not_duplicated():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
    )

    for _ in range(3):
        record_channel_profile_signal(63, signal="recap_copy_export")

    list_channel_profile_recommendations(63, refresh=True)
    list_channel_profile_recommendations(63, refresh=True)
    recs = list_channel_profile_recommendations(63, refresh=True)
    table_recs = [r for r in recs if r["profile_field"] == "table_style"]
    assert len(table_recs) == 1


def test_update_recommendation_approve():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(64, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(64, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    updated = update_channel_profile_recommendation(rec_id, action="approve", actor="admin")
    assert updated["status"] == "approved"
    assert updated["decision_actor"] == "admin"


def test_update_recommendation_reject():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(65, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(65, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    updated = update_channel_profile_recommendation(rec_id, action="reject", actor="user1")
    assert updated["status"] == "rejected"


def test_update_recommendation_apply():
    from channel_profile_state import (
        get_channel_profile,
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(66, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(66, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    update_channel_profile_recommendation(rec_id, action="approve", actor="admin")
    update_channel_profile_recommendation(rec_id, action="apply", actor="admin")
    p = get_channel_profile(66)
    assert p["table_style"] == "copy-safe"


def test_update_recommendation_revert():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(67, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(67, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    update_channel_profile_recommendation(rec_id, action="approve", actor="admin")
    update_channel_profile_recommendation(rec_id, action="apply", actor="admin")
    result = update_channel_profile_recommendation(rec_id, action="revert", actor="admin")
    assert result["status"] == "reverted"
    assert result["reverted_at"] is not None


def test_update_recommendation_invalid_action_raises():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(68, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(68, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    with pytest.raises(ValueError, match="Invalid action"):
        update_channel_profile_recommendation(rec_id, action="zap")


def test_update_recommendation_not_found_raises():
    from channel_profile_state import update_channel_profile_recommendation

    with pytest.raises(ValueError, match="not found"):
        update_channel_profile_recommendation(99999, action="approve")


def test_update_recommendation_wrong_state_raises():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(69, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(69, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    # Can't apply a suggested recommendation (must approve first)
    with pytest.raises(ValueError):
        update_channel_profile_recommendation(rec_id, action="apply")


def test_list_recommendations_include_history():
    from channel_profile_state import (
        list_channel_profile_recommendations,
        record_channel_profile_signal,
        update_channel_profile_recommendation,
    )

    for _ in range(3):
        record_channel_profile_signal(70, signal="recap_copy_export")
    recs = list_channel_profile_recommendations(70, refresh=True)
    rec_id = recs[0]["recommendation_id"]
    update_channel_profile_recommendation(rec_id, action="reject", actor="admin")

    without_hist = list_channel_profile_recommendations(70, include_history=False, refresh=False)
    with_hist = list_channel_profile_recommendations(70, include_history=True, refresh=False)
    assert len(with_hist) >= len(without_hist)


def test_recommendations_none_channel_returns_empty():
    from channel_profile_state import list_channel_profile_recommendations

    assert list_channel_profile_recommendations(None) == []


# ---------------------------------------------------------------------------
# Interaction state helpers — request_context, get_current_*
# ---------------------------------------------------------------------------


def test_channel_profile_state_request_context_sets_channel_and_thread():
    from channel_profile_state import (
        get_current_channel_id,
        get_current_thread_id,
        request_context,
    )

    with request_context(channel_id=80, thread_id=8000):
        assert get_current_channel_id() == 80
        assert get_current_thread_id() == 8000

    assert get_current_channel_id() is None
    assert get_current_thread_id() is None


def test_request_context_nested_isolation():
    from channel_profile_state import get_current_channel_id, request_context

    with request_context(channel_id=81):
        with request_context(channel_id=82):
            assert get_current_channel_id() == 82
        assert get_current_channel_id() == 81


def test_request_context_resets_on_exception():
    from channel_profile_state import get_current_channel_id, request_context

    try:
        with request_context(channel_id=83):
            raise RuntimeError("oops")
    except RuntimeError:
        pass
    assert get_current_channel_id() is None


def test_get_current_user_id_via_context():
    from channel_profile_state import get_current_user_id, request_context

    with request_context(user_id="user123"):
        assert get_current_user_id() == "user123"
    assert get_current_user_id() is None


def test_set_current_user_id():
    from channel_profile_state import get_current_user_id, set_current_user_id

    set_current_user_id("tester42")
    assert get_current_user_id() == "tester42"


# ---------------------------------------------------------------------------
# _normalize_profile_value / _normalize_profile_int_value edge cases
# ---------------------------------------------------------------------------


def test_normalize_profile_value_strips_whitespace():
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("tone", "  concise  ") == "concise"


def test_normalize_profile_value_empty_returns_default():
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("tone", "") == "neutral"


def test_normalize_profile_value_case_insensitive():
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("tone", "CONCISE") == "concise"


def test_normalize_profile_int_value_string_input():
    from channel_profile_state import _normalize_profile_int_value

    assert _normalize_profile_int_value("memory_budget_items", "150") == 150


def test_normalize_profile_int_value_none_returns_default():
    from channel_profile_state import _normalize_profile_int_value

    assert _normalize_profile_int_value("memory_budget_items", None) == 200


# ---------------------------------------------------------------------------
# All allowed string values roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tone", ["neutral", "concise", "analytical", "friendly"])
def test_all_tone_values_accepted(tone):
    from channel_profile_state import set_channel_profile

    set_channel_profile(200 + hash(tone) % 100, tone=tone)
    # validate via normalize
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("tone", tone) == tone


@pytest.mark.parametrize("depth", ["brief", "standard", "detailed"])
def test_all_report_depth_values_accepted(depth):
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("report_depth", depth) == depth


@pytest.mark.parametrize("style", ["discord", "copy-safe"])
def test_all_table_style_values_accepted(style):
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("table_style", style) == style


@pytest.mark.parametrize("level", ["none", "light", "rich"])
def test_all_emoji_level_values_accepted(level):
    from channel_profile_state import _normalize_profile_value

    assert _normalize_profile_value("emoji_level", level) == level
