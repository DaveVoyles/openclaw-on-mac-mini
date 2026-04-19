"""Tests for src/expense_tracker.py — ExpenseTracker CRUD with JSON persistence."""

import json

import pytest

import expense_tracker as et
from expense_tracker import Expense, ExpenseTracker


@pytest.fixture
def tracker(tmp_path):
    path = tmp_path / "expenses.json"
    return ExpenseTracker(path=path)


class TestExpenseTrackerLoad:
    def test_expense_tracker_starts_empty_when_no_file(self, tracker):
        assert tracker._expenses == []

    def test_expense_tracker_loads_existing_data(self, tmp_path):
        path = tmp_path / "expenses.json"
        e = Expense(
            id="abc12345",
            amount=9.99,
            category="food",
            note="lunch",
            user_id="u1",
            timestamp="2024-01-01T12:00:00+00:00",
        )
        path.write_text(
            json.dumps(
                [
                    et.__import__("dataclasses").asdict(e)
                    if False
                    else {
                        "id": "abc12345",
                        "amount": 9.99,
                        "category": "food",
                        "note": "lunch",
                        "user_id": "u1",
                        "timestamp": "2024-01-01T12:00:00+00:00",
                    }
                ]
            )
        )
        t = ExpenseTracker(path=path)
        assert len(t._expenses) == 1
        assert t._expenses[0].category == "food"

    def test_expense_tracker_bad_json_starts_empty(self, tmp_path):
        path = tmp_path / "expenses.json"
        path.write_text("not json at all!")
        t = ExpenseTracker(path=path)
        assert t._expenses == []


class TestExpenseTrackerAdd:
    def test_add_returns_expense(self, tracker):
        e = tracker.add("user1", 12.50, "food", "pizza")
        assert isinstance(e, Expense)
        assert e.amount == 12.50
        assert e.category == "food"
        assert e.user_id == "user1"

    def test_expense_tracker_add_persists_to_disk(self, tracker):
        tracker.add("user1", 5.00, "transport", "bus")
        saved = json.loads(tracker.path.read_text())
        assert len(saved) == 1
        assert saved[0]["category"] == "transport"

    def test_add_rounds_amount(self, tracker):
        e = tracker.add("u", 9.999, "other")
        assert e.amount == 10.0

    def test_add_normalizes_category_to_lower(self, tracker):
        e = tracker.add("u", 1.0, "FOOD")
        assert e.category == "food"

    def test_add_multiple(self, tracker):
        tracker.add("u1", 1.0, "food")
        tracker.add("u1", 2.0, "bills")
        assert len(tracker._expenses) == 2


class TestExpenseTrackerList:
    def test_list_for_user_returns_recent(self, tracker):
        tracker.add("u1", 10.0, "food")
        results = tracker.list_for_user("u1", days=7)
        assert len(results) == 1

    def test_list_for_user_excludes_other_users(self, tracker):
        tracker.add("u1", 10.0, "food")
        tracker.add("u2", 5.0, "bills")
        assert len(tracker.list_for_user("u1")) == 1
        assert len(tracker.list_for_user("u2")) == 1

    def test_list_for_user_empty_when_no_expenses(self, tracker):
        assert tracker.list_for_user("nobody") == []


class TestExpenseTrackerSummary:
    def test_summary_by_category(self, tracker):
        tracker.add("u1", 10.0, "food")
        tracker.add("u1", 5.0, "food")
        tracker.add("u1", 20.0, "bills")
        summary = tracker.summary_by_category("u1")
        assert summary["food"] == pytest.approx(15.0)
        assert summary["bills"] == pytest.approx(20.0)

    def test_summary_sorted_by_amount_desc(self, tracker):
        tracker.add("u1", 5.0, "food")
        tracker.add("u1", 50.0, "bills")
        summary = tracker.summary_by_category("u1")
        values = list(summary.values())
        assert values == sorted(values, reverse=True)

    def test_summary_by_period_week(self, tracker):
        tracker.add("u1", 10.0, "food")
        result = tracker.summary_by_period("u1", "week")
        assert "food" in result

    def test_summary_by_period_month(self, tracker):
        tracker.add("u1", 10.0, "food")
        result = tracker.summary_by_period("u1", "month")
        assert "food" in result

    def test_summary_by_period_unknown_defaults_to_week(self, tracker):
        tracker.add("u1", 10.0, "food")
        result = tracker.summary_by_period("u1", "unknown")
        assert isinstance(result, dict)


class TestExpenseTrackerDelete:
    def test_delete_existing_expense(self, tracker):
        e = tracker.add("u1", 5.0, "food")
        result = tracker.delete("u1", e.id)
        assert result is True
        assert len(tracker._expenses) == 0

    def test_delete_returns_false_for_wrong_user(self, tracker):
        e = tracker.add("u1", 5.0, "food")
        result = tracker.delete("u2", e.id)
        assert result is False

    def test_delete_returns_false_for_nonexistent_id(self, tracker):
        result = tracker.delete("u1", "deadbeef")
        assert result is False

    def test_delete_saves_after_removal(self, tracker):
        e = tracker.add("u1", 5.0, "food")
        tracker.delete("u1", e.id)
        saved = json.loads(tracker.path.read_text())
        assert saved == []


class TestExpenseTrackerFormatBar:
    def test_full_bar(self, tracker):
        bar = tracker.format_bar(100.0, 100.0, width=10)
        assert bar == "█" * 10

    def test_empty_bar_when_max_zero(self, tracker):
        bar = tracker.format_bar(50.0, 0.0, width=10)
        assert bar == "░" * 10

    def test_half_bar(self, tracker):
        bar = tracker.format_bar(50.0, 100.0, width=10)
        assert bar.count("█") == 5
        assert bar.count("░") == 5
