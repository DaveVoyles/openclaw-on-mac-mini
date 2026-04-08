"""Tests for reminder_manager.py — ReminderManager CRUD and parse_time_expression."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import reminder_manager as mod
from reminder_manager import Reminder, ReminderManager, parse_time_expression


# ===========================================================================
# parse_time_expression
# ===========================================================================

class TestParseTimeExpression:
    def test_in_minutes(self):
        before = time.time()
        result = parse_time_expression("in 30m")
        assert result is not None
        assert abs(result - (before + 30 * 60)) < 2

    def test_in_hours(self):
        before = time.time()
        result = parse_time_expression("in 2h")
        assert result is not None
        assert abs(result - (before + 7200)) < 2

    def test_in_seconds(self):
        before = time.time()
        result = parse_time_expression("in 45s")
        assert result is not None
        assert abs(result - (before + 45)) < 2

    def test_in_minutes_full_word(self):
        before = time.time()
        result = parse_time_expression("in 10min")
        assert result is not None
        assert abs(result - (before + 600)) < 2

    def test_at_3pm(self):
        result = parse_time_expression("at 3pm")
        assert result is not None
        assert result > time.time()

    def test_at_with_minutes(self):
        result = parse_time_expression("at 3:30pm")
        assert result is not None

    def test_at_24h_format(self):
        result = parse_time_expression("at 15:00")
        assert result is not None

    def test_invalid_returns_none(self):
        assert parse_time_expression("tomorrow morning") is None
        assert parse_time_expression("") is None
        assert parse_time_expression("blah blah") is None

    def test_midnight_12am(self):
        result = parse_time_expression("at 12am")
        assert result is not None

    def test_noon_12pm(self):
        result = parse_time_expression("at 12pm")
        assert result is not None


# ===========================================================================
# ReminderManager
# ===========================================================================

class TestReminderManager:
    @pytest.fixture
    def mgr(self, tmp_path, monkeypatch):
        """ReminderManager backed by a temp file."""
        monkeypatch.setattr(mod, "REMINDERS_FILE", tmp_path / "reminders.json")
        return ReminderManager()

    def test_empty_initially(self, mgr):
        assert mgr.list_for_user(1) == []

    def test_add_reminder(self, mgr):
        r = mgr.add(1, 100, "Buy milk", time.time() + 3600)
        assert isinstance(r, Reminder)
        assert r.user_id == 1
        assert r.message == "Buy milk"

    def test_list_for_user_returns_own(self, mgr):
        mgr.add(1, 100, "Task A", time.time() + 3600)
        mgr.add(2, 100, "Task B", time.time() + 3600)  # different user
        result = mgr.list_for_user(1)
        assert len(result) == 1
        assert result[0].message == "Task A"

    def test_fired_excluded_from_list(self, mgr):
        r = mgr.add(1, 100, "Old task", time.time() - 1)
        mgr.mark_fired(r.id)
        assert mgr.list_for_user(1) == []

    def test_cancel_removes_reminder(self, mgr):
        r = mgr.add(1, 100, "Cancel me", time.time() + 3600)
        result = mgr.cancel(r.id, user_id=1)
        assert result is True
        assert mgr.list_for_user(1) == []

    def test_cancel_wrong_user_fails(self, mgr):
        r = mgr.add(1, 100, "Mine", time.time() + 3600)
        result = mgr.cancel(r.id, user_id=99)
        assert result is False
        assert len(mgr.list_for_user(1)) == 1

    def test_cancel_nonexistent_fails(self, mgr):
        assert mgr.cancel("badid", user_id=1) is False

    def test_get_due_returns_past_reminders(self, mgr):
        past = mgr.add(1, 100, "Past", time.time() - 1)
        future = mgr.add(1, 100, "Future", time.time() + 3600)
        due = mgr.get_due()
        assert any(r.id == past.id for r in due)
        assert not any(r.id == future.id for r in due)

    def test_mark_fired_one_shot(self, mgr):
        r = mgr.add(1, 100, "Once", time.time() - 1)
        mgr.mark_fired(r.id)
        due = mgr.get_due()
        assert not any(x.id == r.id for x in due)

    def test_mark_fired_daily_reschedules(self, mgr):
        fire_at = time.time() - 1
        r = mgr.add(1, 100, "Daily", fire_at, recurring="daily")
        mgr.mark_fired(r.id)
        # Should not be fired, just rescheduled
        reminder = next(x for x in mgr._reminders if x.id == r.id)
        assert not reminder.fired
        assert reminder.fire_at > time.time()

    def test_mark_fired_weekly_reschedules(self, mgr):
        fire_at = time.time() - 1
        r = mgr.add(1, 100, "Weekly", fire_at, recurring="weekly")
        mgr.mark_fired(r.id)
        reminder = next(x for x in mgr._reminders if x.id == r.id)
        assert not reminder.fired
        assert reminder.fire_at > time.time()

    def test_persistence_across_instances(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "REMINDERS_FILE", tmp_path / "reminders.json")
        mgr1 = ReminderManager()
        mgr1.add(1, 100, "Persistent", time.time() + 3600)

        mgr2 = ReminderManager()
        assert len(mgr2.list_for_user(1)) == 1
        assert mgr2.list_for_user(1)[0].message == "Persistent"

    def test_reminder_has_auto_id(self, mgr):
        r = mgr.add(1, 100, "Test", time.time() + 60)
        assert r.id is not None
        assert len(r.id) == 8  # hex[:8]
