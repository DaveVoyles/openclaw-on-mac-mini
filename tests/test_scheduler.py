"""
Tests for scheduler.py — TaskScheduler CRUD and _is_due logic.

File I/O is redirected to a temp path. The async runner loop is not
started here; _execute_task and _is_due are tested in isolation.
"""

import asyncio
import datetime
import json
import pytest
from unittest.mock import patch, AsyncMock

import scheduler as scheduler_module
from scheduler import TaskScheduler, ScheduledTask


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sched(tmp_path):
    """Fresh TaskScheduler backed by a temp file (no global state)."""
    temp_file = tmp_path / "schedules.json"
    with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
        yield TaskScheduler()


# ---------------------------------------------------------------------------
# ScheduledTask — derived properties
# ---------------------------------------------------------------------------


class TestScheduledTask:
    def _make(self, **overrides):
        defaults = dict(
            task_id="sched-1",
            action="list_containers",
            args={},
            cron_hour=3,
            cron_minute=0,
        )
        defaults.update(overrides)
        return ScheduledTask(**defaults)

    def test_next_run_str_interval_no_last_run(self):
        task = self._make(interval_minutes=30, last_run="")
        assert task.next_run_str == "soon"

    def test_next_run_str_interval_overdue(self):
        past = (datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat()
        task = self._make(interval_minutes=30, last_run=past)
        assert task.next_run_str == "overdue"

    def test_next_run_str_interval_in_future(self):
        recent = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()
        task = self._make(interval_minutes=30, last_run=recent)
        assert "in" in task.next_run_str and "m" in task.next_run_str

    def test_next_run_str_daily_in_future(self):
        # Set target time well in the future
        future = datetime.datetime.now() + datetime.timedelta(hours=3)
        task = self._make(cron_hour=future.hour, cron_minute=future.minute, interval_minutes=0)
        result = task.next_run_str
        assert "in" in result and "h" in result


# ---------------------------------------------------------------------------
# TaskScheduler — CRUD
# ---------------------------------------------------------------------------


class TestTaskSchedulerCreate:
    def test_create_returns_task(self, sched):
        task = sched.create("list_containers", {}, hour=3, minute=0, created_by="Alice")
        assert task.action == "list_containers"
        assert task.cron_hour == 3
        assert task.cron_minute == 0
        assert task.created_by == "Alice"

    def test_create_generates_sequential_ids(self, sched):
        t1 = sched.create("action_a", {})
        t2 = sched.create("action_b", {})
        assert t1.task_id == "sched-1"
        assert t2.task_id == "sched-2"

    def test_create_task_enabled_by_default(self, sched):
        task = sched.create("list_containers", {})
        assert task.enabled

    def test_create_stores_in_task_list(self, sched):
        task = sched.create("list_containers", {})
        assert sched.get(task.task_id) is task

    def test_create_with_interval(self, sched):
        task = sched.create("check_arr_health", {}, interval_minutes=60)
        assert task.interval_minutes == 60

    def test_create_args_stored(self, sched):
        task = sched.create("get_container_logs", {"service": "sonarr", "lines": 50})
        assert task.args == {"service": "sonarr", "lines": 50}


class TestTaskSchedulerRemove:
    def test_remove_existing_returns_true(self, sched):
        task = sched.create("list_containers", {})
        assert sched.remove(task.task_id) is True

    def test_remove_existing_deletes_task(self, sched):
        task = sched.create("list_containers", {})
        sched.remove(task.task_id)
        assert sched.get(task.task_id) is None

    def test_remove_nonexistent_returns_false(self, sched):
        assert sched.remove("sched-999") is False

    def test_remove_does_not_affect_other_tasks(self, sched):
        t1 = sched.create("a", {})
        t2 = sched.create("b", {})
        sched.remove(t1.task_id)
        assert sched.get(t2.task_id) is not None


class TestTaskSchedulerToggle:
    def test_toggle_disables_enabled_task(self, sched):
        task = sched.create("list_containers", {})
        new_state = sched.toggle(task.task_id)
        assert new_state is False
        assert not task.enabled

    def test_toggle_enables_disabled_task(self, sched):
        task = sched.create("list_containers", {})
        task.enabled = False
        new_state = sched.toggle(task.task_id)
        assert new_state is True
        assert task.enabled

    def test_toggle_nonexistent_returns_none(self, sched):
        assert sched.toggle("sched-999") is None


class TestTaskSchedulerList:
    def test_list_tasks_empty_initially(self, sched):
        assert sched.list_tasks() == []

    def test_list_tasks_returns_all(self, sched):
        sched.create("a", {})
        sched.create("b", {})
        tasks = sched.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_sorted_by_id(self, sched):
        sched.create("a", {})
        sched.create("b", {})
        sched.create("c", {})
        ids = [t.task_id for t in sched.list_tasks()]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# TaskScheduler — _is_due logic
# ---------------------------------------------------------------------------


class TestIsDue:
    def _make_interval_task(self, interval_minutes, last_run=""):
        return ScheduledTask(
            task_id="sched-test",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            interval_minutes=interval_minutes,
            last_run=last_run,
        )

    def _make_cron_task(self, hour, minute, last_run=""):
        return ScheduledTask(
            task_id="sched-test",
            action="test",
            args={},
            cron_hour=hour,
            cron_minute=minute,
            interval_minutes=0,
            last_run=last_run,
        )

    def test_interval_task_due_when_no_last_run(self, sched):
        task = self._make_interval_task(30)
        now = datetime.datetime.now()
        assert sched._is_due(task, now)

    def test_interval_task_due_when_elapsed(self, sched):
        past = (datetime.datetime.now() - datetime.timedelta(minutes=35)).isoformat()
        task = self._make_interval_task(30, last_run=past)
        now = datetime.datetime.now()
        assert sched._is_due(task, now)

    def test_interval_task_not_due_when_recent(self, sched):
        recent = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat()
        task = self._make_interval_task(30, last_run=recent)
        now = datetime.datetime.now()
        assert not sched._is_due(task, now)

    def test_interval_task_due_with_invalid_last_run(self, sched):
        task = self._make_interval_task(30, last_run="not-a-date")
        now = datetime.datetime.now()
        assert sched._is_due(task, now)  # Falls back to True on parse error

    def test_cron_task_due_at_exact_time(self, sched):
        now = datetime.datetime.now()
        task = self._make_cron_task(now.hour, now.minute)
        assert sched._is_due(task, now)

    def test_cron_task_not_due_wrong_hour(self, sched):
        now = datetime.datetime.now()
        task = self._make_cron_task((now.hour + 1) % 24, now.minute)
        assert not sched._is_due(task, now)

    def test_cron_task_not_due_wrong_minute(self, sched):
        now = datetime.datetime.now()
        task = self._make_cron_task(now.hour, (now.minute + 1) % 60)
        assert not sched._is_due(task, now)

    def test_cron_task_not_due_already_ran_today(self, sched):
        now = datetime.datetime.now()
        today_run = now.date().isoformat()
        task = self._make_cron_task(now.hour, now.minute, last_run=today_run)
        assert not sched._is_due(task, now)


# ---------------------------------------------------------------------------
# TaskScheduler — _execute_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExecuteTask:
    async def test_execute_calls_registered_skill(self, sched):
        results = []

        async def mock_skill(**kwargs):
            results.append(kwargs)
            return "OK"

        sched.register_skills({"mock_skill": mock_skill})
        task = sched.create("mock_skill", {"service": "sonarr"})
        await sched._execute_task(task)
        assert results == [{"service": "sonarr"}]

    async def test_execute_updates_run_count(self, sched):
        async def mock_skill(**kwargs):
            return "done"

        sched.register_skills({"mock_skill": mock_skill})
        task = sched.create("mock_skill", {})
        await sched._execute_task(task)
        assert task.run_count == 1

    async def test_execute_stores_last_result(self, sched):
        async def mock_skill(**kwargs):
            return "result text"

        sched.register_skills({"mock_skill": mock_skill})
        task = sched.create("mock_skill", {})
        await sched._execute_task(task)
        assert task.last_result == "result text"

    async def test_execute_handles_unknown_skill(self, sched):
        task = sched.create("nonexistent_skill", {})
        await sched._execute_task(task)  # Should not raise
        assert "Unknown skill" in task.last_result

    async def test_execute_handles_skill_exception(self, sched):
        async def failing_skill(**kwargs):
            raise RuntimeError("something went wrong")

        sched.register_skills({"failing_skill": failing_skill})
        task = sched.create("failing_skill", {})
        await sched._execute_task(task)  # Should not raise
        assert "Error" in task.last_result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestSchedulerPersistence:
    def test_task_survives_across_instances(self, tmp_path):
        temp_file = tmp_path / "schedules.json"
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s1 = TaskScheduler()
            t = s1.create("list_containers", {}, hour=3, minute=0)
            task_id = t.task_id

        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s2 = TaskScheduler()
            loaded = s2.get(task_id)
            assert loaded is not None
            assert loaded.action == "list_containers"

    def test_save_writes_valid_json(self, tmp_path):
        temp_file = tmp_path / "schedules.json"
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s = TaskScheduler()
            s.create("list_containers", {})

        data = json.loads(temp_file.read_text())
        assert isinstance(data, list)
        assert data[0]["action"] == "list_containers"
