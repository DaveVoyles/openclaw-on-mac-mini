"""Tests for src/habit_tracker.py — HabitTracker CRUD + streak + sparkline."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from habit_tracker import Habit, HabitTracker


@pytest.fixture
def tracker(tmp_path):
    path = tmp_path / "habits.json"
    return HabitTracker(path=path)


class TestHabitTrackerLoad:
    def test_starts_empty(self, tracker):
        assert tracker._habits == {}

    def test_habit_tracker_loads_existing_data(self, tmp_path):
        path = tmp_path / "habits.json"
        data = {
            "abc12345": {
                "id": "abc12345",
                "name": "Exercise",
                "user_id": "u1",
                "frequency": "daily",
                "checkins": [],
                "created_at": "2024-01-01T00:00:00+00:00",
            }
        }
        path.write_text(json.dumps(data))
        t = HabitTracker(path=path)
        assert "abc12345" in t._habits
        assert t._habits["abc12345"].name == "Exercise"

    def test_habit_tracker_bad_json_starts_empty(self, tmp_path):
        path = tmp_path / "habits.json"
        path.write_text("{broken")
        t = HabitTracker(path=path)
        assert t._habits == {}


class TestAddHabit:
    def test_add_returns_habit(self, tracker):
        h = tracker.add_habit("u1", "Exercise")
        assert isinstance(h, Habit)
        assert h.name == "Exercise"
        assert h.user_id == "u1"

    def test_add_default_frequency(self, tracker):
        h = tracker.add_habit("u1", "Read")
        assert h.frequency == "daily"

    def test_add_custom_frequency(self, tracker):
        h = tracker.add_habit("u1", "Long run", frequency="weekly")
        assert h.frequency == "weekly"

    def test_habit_tracker_add_persists(self, tracker):
        tracker.add_habit("u1", "Meditate")
        saved = json.loads(tracker.path.read_text())
        assert len(saved) == 1

    def test_add_two_habits(self, tracker):
        tracker.add_habit("u1", "Exercise")
        tracker.add_habit("u1", "Read")
        assert len(tracker._habits) == 2


class TestCheckin:
    def test_checkin_returns_habit(self, tracker):
        tracker.add_habit("u1", "Exercise")
        result = tracker.checkin("u1", "Exercise")
        assert result is not None
        assert len(result.checkins) == 1

    def test_checkin_case_insensitive(self, tracker):
        tracker.add_habit("u1", "Exercise")
        result = tracker.checkin("u1", "exercise")
        assert result is not None

    def test_checkin_multiple_times(self, tracker):
        tracker.add_habit("u1", "Exercise")
        tracker.checkin("u1", "Exercise")
        tracker.checkin("u1", "Exercise")
        habit = tracker._find("u1", "Exercise")
        assert len(habit.checkins) == 2

    def test_checkin_nonexistent_habit(self, tracker):
        result = tracker.checkin("u1", "Ghost habit")
        assert result is None

    def test_checkin_wrong_user(self, tracker):
        tracker.add_habit("u1", "Exercise")
        result = tracker.checkin("u2", "Exercise")
        assert result is None


class TestGetStreak:
    def test_no_checkins_returns_zero(self, tracker):
        h = tracker.add_habit("u1", "Read")
        assert tracker.get_streak(h) == 0

    def test_one_checkin_today_returns_one(self, tracker):
        h = tracker.add_habit("u1", "Read")
        h.checkins.append(datetime.now(timezone.utc).isoformat())
        assert tracker.get_streak(h) == 1

    def test_consecutive_days_streak(self, tracker):
        h = tracker.add_habit("u1", "Read")
        today = datetime.now(timezone.utc)
        for i in range(3):
            h.checkins.append((today - timedelta(days=i)).isoformat())
        assert tracker.get_streak(h) == 3

    def test_broken_streak_returns_zero(self, tracker):
        h = tracker.add_habit("u1", "Read")
        # Last check-in was 3 days ago — streak broken
        old = datetime.now(timezone.utc) - timedelta(days=3)
        h.checkins.append(old.isoformat())
        assert tracker.get_streak(h) == 0


class TestListForUser:
    def test_list_returns_only_user_habits(self, tracker):
        tracker.add_habit("u1", "Exercise")
        tracker.add_habit("u2", "Yoga")
        habits = tracker.list_for_user("u1")
        assert len(habits) == 1
        assert habits[0].user_id == "u1"

    def test_habit_tracker_list_empty_user(self, tracker):
        assert tracker.list_for_user("nobody") == []


class TestDeleteHabit:
    def test_habit_tracker_delete_returns_true(self, tracker):
        tracker.add_habit("u1", "Exercise")
        result = tracker.delete_habit("u1", "Exercise")
        assert result is True
        assert tracker.list_for_user("u1") == []

    def test_delete_case_insensitive(self, tracker):
        tracker.add_habit("u1", "Exercise")
        result = tracker.delete_habit("u1", "EXERCISE")
        assert result is True

    def test_habit_tracker_delete_nonexistent_returns_false(self, tracker):
        assert tracker.delete_habit("u1", "Ghost") is False

    def test_habit_tracker_delete_wrong_user_returns_false(self, tracker):
        tracker.add_habit("u1", "Exercise")
        assert tracker.delete_habit("u2", "Exercise") is False


class TestSparkline:
    def test_sparkline_length_equals_weeks(self, tracker):
        h = tracker.add_habit("u1", "Read")
        sparkline = tracker.sparkline(h, weeks=8)
        assert len(sparkline) == 8

    def test_sparkline_no_checkins(self, tracker):
        h = tracker.add_habit("u1", "Read")
        sparkline = tracker.sparkline(h, weeks=4)
        # All chars should be the lowest sparkline character (no hits)
        assert all(c == "▁" for c in sparkline)

    def test_sparkline_all_days_checked(self, tracker):
        h = tracker.add_habit("u1", "Read")
        today = datetime.now(timezone.utc)
        # Check in every day for 7 weeks
        for i in range(49):
            h.checkins.append((today - timedelta(days=i)).isoformat())
        sparkline = tracker.sparkline(h, weeks=7)
        assert len(sparkline) == 7
        # Most recent week should be highest char
        assert sparkline[-1] == "█"
