"""Tests for quality_eval_state — scorecard build, persist, list, and ensure."""

from __future__ import annotations

import json
import sys
import time
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


def _make_error_tracker_mock(runs=None):
    """Return a mock error_tracker module whose get_recent_outcomes returns runs."""
    m = ModuleType("error_tracker")
    m.get_recent_outcomes = MagicMock(return_value=runs or [])
    return m


@pytest.fixture(autouse=True)
def _reset_db(tmp_path, monkeypatch):
    """Redirect DB to a temp path and reset connection before each test."""
    monkeypatch.setenv("THREAD_DB_PATH", str(tmp_path / "qe-test.db"))
    import channel_profile_state as mod

    mod._reset_channel_profile_store_for_tests()
    # Clear recall alerts to avoid cross-test bleed into scorecard
    import anchor_context_state as amod

    with amod._SCOPED_RECALL_ALERTS_LOCK:
        amod._SCOPED_RECALL_ALERTS.clear()
    yield
    mod._reset_channel_profile_store_for_tests()


# ---------------------------------------------------------------------------
# Helper private functions
# ---------------------------------------------------------------------------


def test_is_followup_like_short_query():
    from quality_eval_state import _is_followup_like

    assert _is_followup_like("what about that?") is True


def test_is_followup_like_long_query_not_followup():
    from quality_eval_state import _is_followup_like

    long_q = " ".join(["word"] * 12)
    assert _is_followup_like(long_q) is False


def test_is_followup_like_empty_returns_false():
    from quality_eval_state import _is_followup_like

    assert _is_followup_like("") is False
    assert _is_followup_like(None) is False


def test_is_followup_like_hint_phrases():
    from quality_eval_state import _is_followup_like

    for phrase in ["follow up on that", "and also more", "also this too"]:
        assert _is_followup_like(phrase) is True


def test_contains_markdown_table_true():
    from quality_eval_state import _contains_markdown_table

    md = "| col1 | col2 |\n|------|------|\n| a    | b    |"
    assert _contains_markdown_table(md) is True


def test_contains_markdown_table_false():
    from quality_eval_state import _contains_markdown_table

    assert _contains_markdown_table("just plain text") is False
    assert _contains_markdown_table("") is False
    assert _contains_markdown_table(None) is False


def test_contains_discord_table_true():
    from quality_eval_state import _contains_discord_table

    text = "```text\n+----+----+\n| A  | B  |\n```"
    assert _contains_discord_table(text) is True


def test_contains_discord_table_false():
    from quality_eval_state import _contains_discord_table

    assert _contains_discord_table("no table here") is False


def test_contains_copy_safe_table_true():
    from quality_eval_state import _contains_copy_safe_table

    assert _contains_copy_safe_table("📋 Table with data") is True


def test_contains_copy_safe_table_false():
    from quality_eval_state import _contains_copy_safe_table

    assert _contains_copy_safe_table("no emoji") is False


def test_safe_rate_full_pass():
    from quality_eval_state import _safe_rate

    assert _safe_rate(10, 0) == 1.0


def test_safe_rate_full_fail():
    from quality_eval_state import _safe_rate

    assert _safe_rate(0, 10) == 0.0


def test_safe_rate_mixed():
    from quality_eval_state import _safe_rate

    assert _safe_rate(5, 5) == 0.5


def test_safe_rate_zero_total():
    from quality_eval_state import _safe_rate

    assert _safe_rate(0, 0) == 1.0


def test_init_metric_counter():
    from quality_eval_state import _init_metric_counter

    c = _init_metric_counter()
    assert c == {"pass": 0, "fail": 0}


# ---------------------------------------------------------------------------
# build_quality_eval_scorecard — no runs (empty)
# ---------------------------------------------------------------------------


def test_build_quality_eval_scorecard_empty_runs():
    from quality_eval_state import build_quality_eval_scorecard

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = build_quality_eval_scorecard()

    assert "timestamp" in sc
    assert "window_hours" in sc
    assert sc["sample_size"] == 0
    assert "metrics" in sc
    assert "summary" in sc
    for metric in ("channel_leakage_prevention", "followup_anchor_correctness",
                   "profile_adherence", "table_readability_copy_safety"):
        assert metric in sc["metrics"]


def test_build_quality_eval_scorecard_uses_now_param():
    from quality_eval_state import build_quality_eval_scorecard

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        ts = 12345.0
        sc = build_quality_eval_scorecard(now=ts)
    assert sc["timestamp"] == ts


def test_build_quality_eval_scorecard_window_hours_param():
    from quality_eval_state import build_quality_eval_scorecard

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = build_quality_eval_scorecard(window_hours=48)
    assert sc["window_hours"] == 48.0


def test_build_scorecard_channel_leakage_pass():
    from quality_eval_state import build_quality_eval_scorecard

    runs = [{"question": "hello", "scope_mode": "channel", "lock_mode": "none",
              "anchor_id": "", "profile_values": {}}]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["channel_leakage_prevention"]["pass"] >= 1


def test_build_scorecard_channel_leakage_cross_channel_without_opt_in_fails():
    from quality_eval_state import build_quality_eval_scorecard

    runs = [{"question": "what is the forecast", "scope_mode": "cross-channel",
              "lock_mode": "none", "anchor_id": "", "profile_values": {}}]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["channel_leakage_prevention"]["fail"] >= 1


def test_build_scorecard_followup_anchor_pass():
    from quality_eval_state import build_quality_eval_scorecard

    # Short followup-like question with anchor present → pass
    runs = [{"question": "and also?", "scope_mode": "channel",
              "lock_mode": "none", "anchor_id": "anc-1", "profile_values": {}}]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["followup_anchor_correctness"]["pass"] >= 1


def test_build_scorecard_followup_anchor_fail():
    from quality_eval_state import build_quality_eval_scorecard

    # Short question but no anchor → fail
    runs = [{"question": "and also?", "scope_mode": "channel",
              "lock_mode": "none", "anchor_id": "", "profile_values": {}}]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["followup_anchor_correctness"]["fail"] >= 1


def test_build_scorecard_profile_adherence_emoji_none_pass():
    from quality_eval_state import build_quality_eval_scorecard

    runs = [{
        "question": "tell me about xyz",
        "response_preview": "Here is the answer with no emojis at all.",
        "scope_mode": "channel",
        "lock_mode": "none",
        "anchor_id": "",
        "profile_values": {"emoji_level": "none", "report_depth": "standard", "tone": "neutral"},
    }]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["profile_adherence"]["pass"] >= 1


def test_build_scorecard_profile_adherence_emoji_none_fail():
    from quality_eval_state import build_quality_eval_scorecard

    runs = [{
        "question": "tell me about xyz",
        "response_preview": "Here is the answer 🎉 with an emoji.",
        "scope_mode": "channel",
        "lock_mode": "none",
        "anchor_id": "",
        "profile_values": {"emoji_level": "none"},
    }]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["profile_adherence"]["fail"] >= 1


def test_build_scorecard_table_readability_discord_pass():
    from quality_eval_state import build_quality_eval_scorecard

    table_text = "```text\n+---+---+\n| A | B |\n+---+---+\n```"
    runs = [{
        "question": "table question",
        "response_preview": table_text,
        "scope_mode": "channel",
        "lock_mode": "none",
        "anchor_id": "",
        "profile_values": {"table_style": "discord"},
    }]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["table_readability_copy_safety"]["pass"] >= 1


def test_build_scorecard_cross_channel_opt_in_pass():
    from quality_eval_state import build_quality_eval_scorecard

    runs = [{"question": "--cross-channel search everything", "scope_mode": "cross-channel",
              "lock_mode": "none", "anchor_id": "", "profile_values": {}}]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["channel_leakage_prevention"]["pass"] >= 1


def test_build_scorecard_recall_alerts_boost_leakage():
    """scope_guard_block alerts should boost the leakage pass counter."""
    from anchor_context_state import _SCOPED_RECALL_ALERTS, _SCOPED_RECALL_ALERTS_LOCK
    from quality_eval_state import build_quality_eval_scorecard

    with _SCOPED_RECALL_ALERTS_LOCK:
        _SCOPED_RECALL_ALERTS.append({
            "timestamp": time.time(),
            "category": "scope_guard_block",
            "message": "blocked",
            "channel_id": "999",
            "thread_id": None,
            "metadata": {},
        })

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = build_quality_eval_scorecard()
    assert sc["metrics"]["channel_leakage_prevention"]["pass"] >= 1


def test_build_scorecard_non_dict_run_skipped():
    from quality_eval_state import build_quality_eval_scorecard

    runs = ["not-a-dict", 42, None]
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock(runs)}):
        sc = build_quality_eval_scorecard()
    assert sc["sample_size"] == 3
    assert sc["summary"]["pass"] == 0
    assert sc["summary"]["fail"] == 0


def test_build_scorecard_error_tracker_import_failure():
    """If error_tracker raises, runs defaults to []."""
    from quality_eval_state import build_quality_eval_scorecard

    with patch.dict("sys.modules", {"error_tracker": None}):
        sc = build_quality_eval_scorecard()
    assert sc["sample_size"] == 0


# ---------------------------------------------------------------------------
# save_quality_eval_scorecard
# ---------------------------------------------------------------------------


def test_save_quality_eval_scorecard_persists_and_returns_id():
    from quality_eval_state import build_quality_eval_scorecard, save_quality_eval_scorecard

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = build_quality_eval_scorecard()
    result = save_quality_eval_scorecard(sc)
    assert isinstance(result["scorecard_id"], int)
    assert result["scorecard_id"] > 0


def test_save_quality_eval_scorecard_roundtrips_data():
    from quality_eval_state import (
        build_quality_eval_scorecard,
        list_quality_eval_scorecards,
        save_quality_eval_scorecard,
    )

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = build_quality_eval_scorecard(window_hours=12)
    save_quality_eval_scorecard(sc)
    cards = list_quality_eval_scorecards(limit=1)
    assert len(cards) == 1
    assert cards[0]["window_hours"] == 12.0


def test_save_multiple_scorecards():
    from quality_eval_state import (
        build_quality_eval_scorecard,
        list_quality_eval_scorecards,
        save_quality_eval_scorecard,
    )

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        for _ in range(3):
            save_quality_eval_scorecard(build_quality_eval_scorecard())
    cards = list_quality_eval_scorecards(limit=10)
    assert len(cards) == 3


# ---------------------------------------------------------------------------
# create_quality_eval_scorecard
# ---------------------------------------------------------------------------


def test_create_quality_eval_scorecard_persist_true():
    from quality_eval_state import create_quality_eval_scorecard, list_quality_eval_scorecards

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = create_quality_eval_scorecard(persist=True)
    assert sc["scorecard_id"] is not None
    assert sc["scorecard_id"] > 0
    cards = list_quality_eval_scorecards()
    assert len(cards) >= 1


def test_create_quality_eval_scorecard_persist_false():
    from quality_eval_state import create_quality_eval_scorecard, list_quality_eval_scorecards

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = create_quality_eval_scorecard(persist=False)
    assert sc["scorecard_id"] is None
    cards = list_quality_eval_scorecards()
    assert len(cards) == 0


# ---------------------------------------------------------------------------
# list_quality_eval_scorecards
# ---------------------------------------------------------------------------


def test_list_quality_eval_scorecards_newest_first():
    from quality_eval_state import (
        build_quality_eval_scorecard,
        list_quality_eval_scorecards,
        save_quality_eval_scorecard,
    )

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc1 = build_quality_eval_scorecard(now=100.0)
        sc2 = build_quality_eval_scorecard(now=200.0)
    save_quality_eval_scorecard(sc1)
    save_quality_eval_scorecard(sc2)
    cards = list_quality_eval_scorecards()
    assert cards[0]["timestamp"] >= cards[1]["timestamp"]


def test_list_quality_eval_scorecards_empty():
    from quality_eval_state import list_quality_eval_scorecards

    assert list_quality_eval_scorecards() == []


def test_list_quality_eval_scorecards_limit_capped():
    from quality_eval_state import (
        build_quality_eval_scorecard,
        list_quality_eval_scorecards,
        save_quality_eval_scorecard,
    )

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        for _ in range(5):
            save_quality_eval_scorecard(build_quality_eval_scorecard())
    cards = list_quality_eval_scorecards(limit=2)
    assert len(cards) == 2


def test_list_quality_eval_scorecard_metrics_json_invalid_handled():
    """Corrupt metrics_json should result in empty dict, not a crash."""
    from channel_profile_state import _get_channel_profile_db
    from quality_eval_state import list_quality_eval_scorecards

    db = _get_channel_profile_db()
    db.execute(
        "INSERT INTO quality_eval_scorecards "
        "(ts, window_hours, sample_size, summary_passes, summary_failures, summary_rate, metrics_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (time.time(), 24.0, 0, 0, 0, 1.0, "INVALID{{{"),
    )
    db.commit()
    cards = list_quality_eval_scorecards()
    assert isinstance(cards[0]["metrics"], dict)


# ---------------------------------------------------------------------------
# ensure_quality_eval_scorecard
# ---------------------------------------------------------------------------


def test_ensure_quality_eval_scorecard_creates_when_empty():
    from quality_eval_state import ensure_quality_eval_scorecard, list_quality_eval_scorecards

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc = ensure_quality_eval_scorecard()
    assert sc["scorecard_id"] is not None
    cards = list_quality_eval_scorecards()
    assert len(cards) >= 1


def test_ensure_quality_eval_scorecard_returns_fresh_when_stale():
    from quality_eval_state import (
        build_quality_eval_scorecard,
        ensure_quality_eval_scorecard,
        list_quality_eval_scorecards,
        save_quality_eval_scorecard,
    )

    old_ts = time.time() - 10000
    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        old_sc = build_quality_eval_scorecard(now=old_ts)
    save_quality_eval_scorecard(old_sc)
    assert len(list_quality_eval_scorecards()) == 1

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        result = ensure_quality_eval_scorecard(min_interval_seconds=1800)
    assert len(list_quality_eval_scorecards()) == 2
    assert result["scorecard_id"] != list_quality_eval_scorecards()[-1]["scorecard_id"]


def test_ensure_quality_eval_scorecard_reuses_fresh():
    from quality_eval_state import (
        ensure_quality_eval_scorecard,
        list_quality_eval_scorecards,
    )

    with patch.dict("sys.modules", {"error_tracker": _make_error_tracker_mock([])}):
        sc1 = ensure_quality_eval_scorecard(min_interval_seconds=9999)
        sc2 = ensure_quality_eval_scorecard(min_interval_seconds=9999)

    assert sc1["scorecard_id"] == sc2["scorecard_id"]
    assert len(list_quality_eval_scorecards()) == 1
