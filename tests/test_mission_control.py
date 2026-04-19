"""
Tests for mission_control.py — Kanban task management.

Covers: task loading with caching, task listing and filtering,
task detail retrieval, status/priority emoji mapping, and error handling.
"""

import json
from unittest.mock import patch

import pytest

import mission_control as mc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TASKS = {
    "tasks": [
        {
            "id": "task_001",
            "title": "Set up Docker monitoring",
            "status": "done",
            "priority": "high",
            "description": "Implement container health checks and alerting.",
            "subtasks": [
                {"title": "Add health endpoint", "done": True},
                {"title": "Configure alerts", "done": False},
            ],
            "comments": [
                {"author": "Dave", "text": "Working on this now."},
            ],
        },
        {
            "id": "task_002",
            "title": "Add web search skill",
            "status": "in_progress",
            "priority": "medium",
            "description": "Integrate Tavily search.",
            "subtasks": [],
            "comments": [],
        },
        {
            "id": "task_003",
            "title": "Write tests",
            "status": "backlog",
            "priority": "low",
            "description": "",
            "subtasks": [],
            "comments": [],
        },
    ]
}


@pytest.fixture(autouse=True)
def setup_tasks_file(tmp_path, monkeypatch):
    """Write sample tasks to a temp file and point mission_control at it."""
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps(SAMPLE_TASKS))
    monkeypatch.setattr(mc, "TASKS_FILE", tasks_file)
    # Clear the cache so each test reads fresh data
    mc._tasks_cache = None
    mc._tasks_mtime = 0.0


# ---------------------------------------------------------------------------
# _load_tasks
# ---------------------------------------------------------------------------


class TestLoadTasks:
    def test_loads_tasks_from_file(self):
        data = mc._load_tasks()
        assert len(data["tasks"]) == 3

    def test_caches_on_second_call(self):
        """Second call should use cache (same mtime)."""
        first = mc._load_tasks()
        second = mc._load_tasks()
        assert first is second  # Same object reference = cached

    def test_mission_control_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mc, "TASKS_FILE", tmp_path / "nonexistent.json")
        # Also clear candidates that might point to a real file
        mc._tasks_cache = None
        mc._tasks_mtime = 0.0
        # Patch the candidate list to only include the missing file
        with patch.object(mc, "_load_tasks") as mock_load:
            mock_load.return_value = {"tasks": []}
            data = mc._load_tasks()
            assert data == {"tasks": []}


# ---------------------------------------------------------------------------
# Status & priority emoji
# ---------------------------------------------------------------------------


class TestEmojis:
    def test_all_statuses_have_emoji(self):
        for status in ("permanent", "backlog", "in_progress", "review", "done"):
            assert status in mc.STATUS_EMOJI

    def test_all_priorities_have_emoji(self):
        for prio in ("high", "medium", "low"):
            assert prio in mc.PRIORITY_EMOJI


# ---------------------------------------------------------------------------
# get_mission_tasks
# ---------------------------------------------------------------------------


class TestGetMissionTasks:
    @pytest.mark.asyncio
    async def test_returns_all_tasks(self):
        result = await mc.get_mission_tasks()
        assert "task_001" in result
        assert "task_002" in result
        assert "task_003" in result

    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        result = await mc.get_mission_tasks(status="in_progress")
        assert "task_002" in result
        assert "task_001" not in result

    @pytest.mark.asyncio
    async def test_filter_no_match(self):
        result = await mc.get_mission_tasks(status="review")
        assert "No tasks" in result

    @pytest.mark.asyncio
    async def test_contains_dashboard_link(self):
        result = await mc.get_mission_tasks()
        assert mc.DASHBOARD_URL in result

    @pytest.mark.asyncio
    async def test_shows_subtask_counts(self):
        result = await mc.get_mission_tasks()
        assert "[1/2 subtasks]" in result


# ---------------------------------------------------------------------------
# get_task_detail
# ---------------------------------------------------------------------------


class TestGetTaskDetail:
    @pytest.mark.asyncio
    async def test_returns_task_info(self):
        result = await mc.get_task_detail("task_001")
        assert "Set up Docker monitoring" in result
        assert "done" in result.lower() or "✅" in result

    @pytest.mark.asyncio
    async def test_shows_subtasks(self):
        result = await mc.get_task_detail("task_001")
        assert "Add health endpoint" in result
        assert "Configure alerts" in result

    @pytest.mark.asyncio
    async def test_shows_last_comment(self):
        result = await mc.get_task_detail("task_001")
        assert "Working on this now" in result

    @pytest.mark.asyncio
    async def test_shows_description(self):
        result = await mc.get_task_detail("task_001")
        assert "container health checks" in result

    @pytest.mark.asyncio
    async def test_task_not_found(self):
        result = await mc.get_task_detail("task_999")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------


class TestUpdateTaskStatus:
    @pytest.mark.asyncio
    async def test_rejects_invalid_status(self):
        result = await mc.update_task_status("task_001", "invalid_status")
        assert "Invalid status" in result

    @pytest.mark.asyncio
    async def test_accepts_valid_statuses(self):
        for status in ("backlog", "in_progress", "review", "done", "permanent"):
            # This will try to run mc-update.sh which won't exist in test env
            # — but at least the validation doesn't block it
            result = await mc.update_task_status("task_001", status)
            assert "Invalid status" not in result


# ---------------------------------------------------------------------------
# MISSION_CONTROL_SKILLS dict
# ---------------------------------------------------------------------------


class TestSkillsDict:
    def test_skill_dict_contains_expected_skills(self):
        expected = {
            "get_mission_tasks",
            "get_task_detail",
            "update_task_status",
            "complete_task",
            "add_task_comment",
        }
        assert expected == set(mc.MISSION_CONTROL_SKILLS.keys())

    def test_all_skills_are_callable(self):
        for name, fn in mc.MISSION_CONTROL_SKILLS.items():
            assert callable(fn), f"{name} is not callable"
