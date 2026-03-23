"""
Tests for spending.py — SpendingTracker.

All tests use a temporary file via pytest's tmp_path fixture to avoid
touching the real /memory/spending.json on disk.
"""

import json
import pytest
from unittest.mock import patch

import spending as spending_module
from spending import SpendingTracker, PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M, BUDGET_LIMIT


@pytest.fixture
def tracker(tmp_path):
    """Fresh SpendingTracker backed by a temp file (no real disk state)."""
    temp_file = tmp_path / "spending.json"
    with patch.object(spending_module, "SPENDING_FILE", temp_file):
        yield SpendingTracker()


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------


class TestRecord:
    def test_record_increments_call_count(self, tracker):
        tracker.record(100, 50)
        assert tracker._data["calls"] == 1

    def test_record_accumulates_input_tokens(self, tracker):
        tracker.record(1000, 0)
        tracker.record(500, 0)
        assert tracker._data["total_input_tokens"] == 1500

    def test_record_accumulates_output_tokens(self, tracker):
        tracker.record(0, 200)
        tracker.record(0, 300)
        assert tracker._data["total_output_tokens"] == 500

    def test_record_computes_correct_cost_input_only(self, tracker):
        # 1M input tokens at $0.10/M = $0.10
        tracker.record(1_000_000, 0)
        assert abs(tracker.total_cost - PRICE_INPUT_PER_M) < 1e-9

    def test_record_computes_correct_cost_output_only(self, tracker):
        # 1M output tokens at $0.40/M = $0.40
        tracker.record(0, 1_000_000)
        assert abs(tracker.total_cost - PRICE_OUTPUT_PER_M) < 1e-9

    def test_record_computes_combined_cost(self, tracker):
        tracker.record(1_000_000, 1_000_000)
        expected = PRICE_INPUT_PER_M + PRICE_OUTPUT_PER_M
        assert abs(tracker.total_cost - expected) < 1e-9

    def test_record_zero_tokens_adds_zero_cost(self, tracker):
        tracker.record(0, 0)
        assert tracker.total_cost == 0.0
        assert tracker._data["calls"] == 1  # Call still counted

    def test_record_updates_daily_bucket(self, tracker):
        import datetime
        today = datetime.date.today().isoformat()
        tracker.record(500, 250)
        assert today in tracker._data["daily"]
        day = tracker._data["daily"][today]
        assert day["input_tokens"] == 500
        assert day["output_tokens"] == 250
        assert day["calls"] == 1

    def test_record_sets_first_call_on_first_use(self, tracker):
        assert tracker._data["first_call"] is None
        tracker.record(1, 1)
        assert tracker._data["first_call"] is not None

    def test_record_updates_last_call_each_time(self, tracker):
        tracker.record(1, 1)
        first = tracker._data["last_call"]
        tracker.record(1, 1)
        second = tracker._data["last_call"]
        # Both should be set; last_call is a timestamp string
        assert first is not None
        assert second is not None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_total_cost_initially_zero(self, tracker):
        assert tracker.total_cost == 0.0

    def test_budget_remaining_starts_at_full_budget(self, tracker):
        assert abs(tracker.budget_remaining - BUDGET_LIMIT) < 1e-9

    def test_budget_remaining_decreases_after_spending(self, tracker):
        tracker.record(1_000_000, 0)  # costs PRICE_INPUT_PER_M
        expected = BUDGET_LIMIT - PRICE_INPUT_PER_M
        assert abs(tracker.budget_remaining - expected) < 1e-6

    def test_budget_remaining_floored_at_zero(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT + 10.0
        assert tracker.budget_remaining == 0.0

    def test_budget_pct_used_initially_zero(self, tracker):
        assert tracker.budget_pct_used == 0.0

    def test_budget_pct_used_at_full_budget(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT
        assert abs(tracker.budget_pct_used - 100.0) < 1e-6

    def test_budget_pct_used_at_half_budget(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT / 2
        assert abs(tracker.budget_pct_used - 50.0) < 1e-6

    def test_budget_pct_capped_at_100_when_over(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT * 2
        assert tracker.budget_pct_used == 100.0

    def test_is_over_budget_false_when_under(self, tracker):
        assert not tracker.is_over_budget

    def test_is_over_budget_true_at_limit(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT
        assert tracker.is_over_budget

    def test_is_over_budget_true_when_over(self, tracker):
        tracker._data["total_cost_usd"] = BUDGET_LIMIT + 1.0
        assert tracker.is_over_budget


# ---------------------------------------------------------------------------
# Formatting / output
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_summary_returns_string(self, tracker):
        result = tracker.summary()
        assert isinstance(result, str)

    def test_summary_contains_budget_limit(self, tracker):
        result = tracker.summary()
        assert f"${BUDGET_LIMIT:.2f}" in result

    def test_summary_shows_zero_cost_initially(self, tracker):
        result = tracker.summary()
        assert "$0.0000" in result

    def test_summary_shows_progress_bar(self, tracker):
        result = tracker.summary()
        # Progress bar uses block characters
        assert "░" in result or "█" in result

    def test_daily_breakdown_no_data_message(self, tracker):
        result = tracker.daily_breakdown()
        assert "No daily data" in result

    def test_daily_breakdown_shows_data_after_record(self, tracker):
        tracker.record(1000, 500)
        result = tracker.daily_breakdown()
        assert isinstance(result, str)
        assert "$" in result  # Shows cost

    def test_reset_clears_all_data(self, tracker):
        tracker.record(1000, 500)
        tracker.reset()
        assert tracker.total_cost == 0.0
        assert tracker._data["calls"] == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_data_persists_across_instances(self, tmp_path):
        temp_file = tmp_path / "spending.json"
        with patch.object(spending_module, "SPENDING_FILE", temp_file):
            t1 = SpendingTracker()
            t1.record(1000, 500)
            cost1 = t1.total_cost
            calls1 = t1._data["calls"]

        with patch.object(spending_module, "SPENDING_FILE", temp_file):
            t2 = SpendingTracker()
            assert abs(t2.total_cost - cost1) < 1e-9
            assert t2._data["calls"] == calls1

    def test_corrupted_file_falls_back_to_empty(self, tmp_path):
        temp_file = tmp_path / "spending.json"
        temp_file.write_text("this is not valid json{{{}}")
        with patch.object(spending_module, "SPENDING_FILE", temp_file):
            t = SpendingTracker()
            assert t.total_cost == 0.0  # Graceful fallback

    def test_save_writes_valid_json(self, tmp_path):
        temp_file = tmp_path / "spending.json"
        with patch.object(spending_module, "SPENDING_FILE", temp_file):
            t = SpendingTracker()
            t.record(100, 50)

        data = json.loads(temp_file.read_text())
        assert "total_cost_usd" in data
        assert data["calls"] == 1
