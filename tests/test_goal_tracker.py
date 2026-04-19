"""Tests for src/goal_tracker.py — detect_goal, load/save, get_active_goals, complete, dismiss."""

import pytest

# goal_tracker module-level code only uses stdlib — google/genai only inside async functions
import goal_tracker as gt  # noqa: E402
from goal_tracker import (
    _load_goals,
    _save_goals,
    complete_goal,
    detect_goal,
    dismiss_goal,
    format_goals_for_briefing,
    get_active_goals,
)


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    """Point GOALS_FILE at a temporary path for every test."""
    goal_file = tmp_path / "goals.json"
    monkeypatch.setattr(gt, "GOALS_FILE", goal_file)
    yield goal_file


# ---------------------------------------------------------------------------
# detect_goal
# ---------------------------------------------------------------------------

class TestDetectGoal:
    def test_detects_looking_for(self):
        assert detect_goal("I'm looking for a new apartment in Portland.") is True

    def test_detects_trying_to(self):
        assert detect_goal("I'm trying to learn Python.") is True

    def test_detects_want_to(self):
        assert detect_goal("I want to build a mobile app.") is True

    def test_detects_working_on(self):
        assert detect_goal("I'm working on a new side project.") is True

    def test_short_message_returns_false(self):
        assert detect_goal("Hi") is False

    def test_no_goal_pattern_returns_false(self):
        assert detect_goal("The weather is nice today in the afternoon.") is False

    def test_detects_planning_to(self):
        assert detect_goal("I'm planning to visit Japan next year.") is True


# ---------------------------------------------------------------------------
# _load_goals / _save_goals
# ---------------------------------------------------------------------------

class TestLoadSaveGoals:
    def test_goal_tracker_load_returns_empty_when_no_file(self):
        assert _load_goals() == []

    def test_goal_tracker_save_and_load_roundtrip(self):
        goals = [{"goal": "Learn Rust", "user_id": 1, "status": "active"}]
        _save_goals(goals)
        loaded = _load_goals()
        assert loaded == goals

    def test_goal_tracker_save_creates_parent_dirs(self, tmp_path, monkeypatch):
        nested = tmp_path / "deep" / "goals.json"
        monkeypatch.setattr(gt, "GOALS_FILE", nested)
        _save_goals([{"goal": "test"}])
        assert nested.exists()

    def test_load_handles_bad_json(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        monkeypatch.setattr(gt, "GOALS_FILE", bad)
        result = _load_goals()
        assert result == []


# ---------------------------------------------------------------------------
# get_active_goals
# ---------------------------------------------------------------------------

class TestGetActiveGoals:
    def test_returns_only_active(self):
        goals = [
            {"goal": "Learn Rust", "user_id": 1, "status": "active"},
            {"goal": "Build app", "user_id": 1, "status": "completed"},
            {"goal": "Lose weight", "user_id": 1, "status": "dismissed"},
        ]
        _save_goals(goals)
        active = get_active_goals()
        assert len(active) == 1
        assert active[0]["goal"] == "Learn Rust"

    def test_goal_tracker_filters_by_user_id(self):
        goals = [
            {"goal": "User1 goal", "user_id": 1, "status": "active"},
            {"goal": "User2 goal", "user_id": 2, "status": "active"},
        ]
        _save_goals(goals)
        assert len(get_active_goals(user_id=1)) == 1
        assert len(get_active_goals(user_id=2)) == 1

    def test_no_user_filter_returns_all_active(self):
        goals = [
            {"goal": "A", "user_id": 1, "status": "active"},
            {"goal": "B", "user_id": 2, "status": "active"},
        ]
        _save_goals(goals)
        assert len(get_active_goals()) == 2

    def test_empty_file_returns_empty(self):
        assert get_active_goals() == []


# ---------------------------------------------------------------------------
# complete_goal
# ---------------------------------------------------------------------------

class TestCompleteGoal:
    def test_complete_marks_as_completed(self):
        _save_goals([{"goal": "Learn Rust", "user_id": 1, "status": "active"}])
        result = complete_goal("Learn Rust", 1)
        assert result is True
        loaded = _load_goals()
        assert loaded[0]["status"] == "completed"
        assert "completed_at" in loaded[0]

    def test_complete_case_insensitive(self):
        _save_goals([{"goal": "Learn Rust", "user_id": 1, "status": "active"}])
        result = complete_goal("learn rust", 1)
        assert result is True

    def test_complete_wrong_user_returns_false(self):
        _save_goals([{"goal": "Learn Rust", "user_id": 1, "status": "active"}])
        assert complete_goal("Learn Rust", 2) is False

    def test_complete_nonexistent_returns_false(self):
        _save_goals([])
        assert complete_goal("Nonexistent goal", 1) is False


# ---------------------------------------------------------------------------
# dismiss_goal
# ---------------------------------------------------------------------------

class TestDismissGoal:
    def test_dismiss_marks_as_dismissed(self):
        _save_goals([{"goal": "Exercise daily", "user_id": 1, "status": "active"}])
        result = dismiss_goal("Exercise daily", 1)
        assert result is True
        loaded = _load_goals()
        assert loaded[0]["status"] == "dismissed"

    def test_dismiss_wrong_user_returns_false(self):
        _save_goals([{"goal": "Exercise daily", "user_id": 1, "status": "active"}])
        assert dismiss_goal("Exercise daily", 2) is False

    def test_dismiss_nonexistent_returns_false(self):
        assert dismiss_goal("ghost goal", 1) is False


# ---------------------------------------------------------------------------
# format_goals_for_briefing
# ---------------------------------------------------------------------------

class TestFormatGoalsForBriefing:
    def test_empty_goals_returns_empty_string(self):
        assert format_goals_for_briefing() == ""

    def test_active_goals_formatted(self):
        _save_goals([
            {"goal": "Learn Rust", "user_id": 1, "status": "active", "mention_count": 3},
        ])
        result = format_goals_for_briefing()
        assert "Learn Rust" in result
        assert "3x" in result

    def test_only_up_to_5_goals_shown(self):
        goals = [
            {"goal": f"Goal {i}", "user_id": 1, "status": "active", "mention_count": 1}
            for i in range(10)
        ]
        _save_goals(goals)
        result = format_goals_for_briefing()
        # Only up to 5 goals should appear
        assert result.count("- Goal") == 5

    def test_filters_by_user(self):
        _save_goals([
            {"goal": "User1 goal", "user_id": 1, "status": "active", "mention_count": 1},
            {"goal": "User2 goal", "user_id": 2, "status": "active", "mention_count": 1},
        ])
        result = format_goals_for_briefing(user_id=1)
        assert "User1 goal" in result
        assert "User2 goal" not in result
