"""
Tests for scheduler.py — TaskScheduler CRUD and _is_due logic.

File I/O is redirected to a temp path. The async runner loop is not
started here; _execute_task and _is_due are tested in isolation.
"""

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

import scheduler as scheduler_module
from scheduler import ScheduledTask, TaskScheduler

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        task = self._make(interval_minutes=30, last_run=past)
        assert task.next_run_str == "overdue"

    def test_next_run_str_interval_in_future(self):
        recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
        task = self._make(interval_minutes=30, last_run=recent)
        assert "in" in task.next_run_str and "m" in task.next_run_str

    def test_next_run_str_daily_in_future(self):
        # Set target time well in the future
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
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
    def test_scheduler_remove_existing_returns_true(self, sched):
        task = sched.create("list_containers", {})
        assert sched.remove(task.task_id) is True

    def test_remove_existing_deletes_task(self, sched):
        task = sched.create("list_containers", {})
        sched.remove(task.task_id)
        assert sched.get(task.task_id) is None

    def test_scheduler_remove_nonexistent_returns_false(self, sched):
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
        now = datetime.datetime.now(datetime.timezone.utc)
        assert sched._is_due(task, now)

    def test_interval_task_due_when_elapsed(self, sched):
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=35)).isoformat()
        task = self._make_interval_task(30, last_run=past)
        now = datetime.datetime.now(datetime.timezone.utc)
        assert sched._is_due(task, now)

    def test_interval_task_not_due_when_recent(self, sched):
        recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
        task = self._make_interval_task(30, last_run=recent)
        now = datetime.datetime.now(datetime.timezone.utc)
        assert not sched._is_due(task, now)

    def test_interval_task_due_with_invalid_last_run(self, sched):
        task = self._make_interval_task(30, last_run="not-a-date")
        now = datetime.datetime.now()
        assert sched._is_due(task, now)  # Falls back to True on parse error

    def test_cron_task_due_at_exact_time(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
        task = self._make_cron_task(now.hour, now.minute)
        assert sched._is_due(task, now)

    def test_cron_task_not_due_wrong_hour(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
        task = self._make_cron_task((now.hour + 1) % 24, now.minute)
        assert not sched._is_due(task, now)

    def test_cron_task_not_due_wrong_minute(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
        task = self._make_cron_task(now.hour, (now.minute + 1) % 60)
        assert not sched._is_due(task, now)

    def test_cron_task_not_due_already_ran_today(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
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
        mock_collector = MagicMock()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            await sched._execute_task(task)  # Should not raise

        assert "Unknown skill" in task.last_result
        mock_collector.record_command.assert_called_once_with(
            command="nonexistent_skill",
            user="scheduler",
            workspace="scheduler",
            duration=0.0,
            success=False,
            error_type="unknown_skill",
        )

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

    def test_scheduler_save_writes_valid_json(self, tmp_path):
        temp_file = tmp_path / "schedules.json"
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s = TaskScheduler()
            s.create("list_containers", {})

        data = json.loads(temp_file.read_text())
        assert isinstance(data, list)
        assert data[0]["action"] == "list_containers"



# ---------------------------------------------------------------------------
# Additional tests for improved scheduler coverage
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock


def test_parse_utc_naive_datetime():
    """_parse_utc adds UTC timezone to naive datetimes."""
    from scheduler import _parse_utc
    dt_str = "2024-01-15T09:00:00"  # naive
    result = _parse_utc(dt_str)
    assert result.tzinfo == datetime.timezone.utc
    assert result.hour == 9


def test_parse_utc_aware_datetime():
    """_parse_utc converts aware datetimes to UTC."""
    from scheduler import _parse_utc
    dt_str = "2024-01-15T14:00:00+05:00"  # +5 timezone
    result = _parse_utc(dt_str)
    assert result.tzinfo == datetime.timezone.utc
    assert result.hour == 9  # 14 - 5


class TestNextRunStrCronExpression:
    def _make_cron_expr_task(self, cron_expr: str, last_run: str = ""):
        return ScheduledTask(
            task_id="sched-cron",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            interval_minutes=0,
            cron_expression=cron_expr,
            last_run=last_run,
        )

    def test_next_run_str_valid_cron_expression(self):
        """next_run_str returns formatted time for valid cron expression."""
        task = self._make_cron_expr_task("0 9 * * *")
        result = task.next_run_str
        # Should return something like "Mon 09:00" or similar
        assert isinstance(result, str)
        assert len(result) > 0

    def test_next_run_str_invalid_cron_falls_back_to_expr(self):
        """next_run_str falls back to raw expression for invalid cron."""
        task = self._make_cron_expr_task("invalid-cron-expr")
        result = task.next_run_str
        assert result == "invalid-cron-expr"

    def test_next_run_str_interval_with_invalid_last_run(self):
        """next_run_str handles ValueError in last_run parsing."""
        task = ScheduledTask(
            task_id="sched-test",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            interval_minutes=30,
            last_run="not-a-valid-datetime",
        )
        result = task.next_run_str
        assert result == "soon"  # Falls through to default

    def test_next_run_str_daily_target_in_past(self):
        """next_run_str adds a day when daily target already passed."""
        now = datetime.datetime.now(datetime.timezone.utc)
        # Set to an hour that already passed today
        past_hour = (now.hour - 1) % 24
        task = ScheduledTask(
            task_id="sched-daily",
            action="test",
            args={},
            cron_hour=past_hour,
            cron_minute=0,
            interval_minutes=0,
        )
        result = task.next_run_str
        # Should be "in Xh Ym" with a future time (next day)
        assert "in" in result


class TestSchedulerLoadEdgeCases:
    def test_load_with_non_standard_task_id(self, tmp_path):
        """Non-standard task IDs don't crash _load."""
        temp_file = tmp_path / "schedules.json"
        task_data = [{
            "task_id": "custom-non-standard-id",
            "action": "list_containers",
            "args": {},
            "cron_hour": 3,
            "cron_minute": 0,
            "interval_minutes": 0,
            "cron_expression": "",
            "prompt": "",
            "enabled": True,
            "created_by": "",
            "created_at": "",
            "last_run": "",
            "last_result": "",
            "run_count": 0,
            "notify_channel_id": 0,
            "alert_only": True,
        }]
        temp_file.write_text(json.dumps(task_data))
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s = TaskScheduler()
        assert s.get("custom-non-standard-id") is not None

    def test_load_corrupted_file_leaves_tasks_empty(self, tmp_path):
        """Corrupted schedule file leaves tasks empty (safe fallback)."""
        temp_file = tmp_path / "schedules.json"
        temp_file.write_text("not valid json {{{")
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s = TaskScheduler()
        assert s.list_tasks() == []


@pytest.mark.asyncio
class TestPromptJobExecution:
    """Tests for prompt-job execution paths (lines 286-340)."""

    async def test_execute_prompt_job_calls_llm(self, sched):
        """Prompt task invokes LLM and stores result."""
        fake_response = ("LLM response text", [], "gemini")
        fake_collector = MagicMock()

        with patch("llm.chat", new_callable=AsyncMock, return_value=fake_response), \
             patch("metrics_collector.get_collector", return_value=fake_collector):
            task = sched.create("prompt-job", {}, prompt="Tell me the weather")
            await sched._execute_task(task)

        assert "LLM response text" in task.last_result
        assert task.run_count == 1

    async def test_execute_prompt_job_handles_timeout(self, sched):
        """Prompt task handles asyncio.TimeoutError gracefully."""
        fake_collector = MagicMock()

        async def slow_chat(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("llm.chat", side_effect=slow_chat), \
             patch("metrics_collector.get_collector", return_value=fake_collector):
            task = sched.create("prompt-job", {}, prompt="Slow query")
            await sched._execute_task(task)

        assert "timed out" in task.last_result.lower() or "Error" in task.last_result

    async def test_execute_prompt_job_handles_exception(self, sched):
        """Prompt task handles general exceptions gracefully."""
        fake_collector = MagicMock()

        async def failing_chat(*args, **kwargs):
            raise RuntimeError("LLM crashed")

        with patch("llm.chat", side_effect=failing_chat), \
             patch("metrics_collector.get_collector", return_value=fake_collector):
            task = sched.create("prompt-job", {}, prompt="Test prompt")
            await sched._execute_task(task)

        assert "failed" in task.last_result.lower() or "Error" in task.last_result

    async def test_execute_prompt_job_with_notify_callback_alert(self, sched):
        """Prompt task invokes notify_callback when result contains alert keyword."""
        fake_response = ("❌ error detected in service", [], "gemini")
        fake_collector = MagicMock()
        notify_calls = []

        async def notify(task_id, action, result_text, is_alert):
            notify_calls.append(is_alert)

        sched.notify_callback = notify

        with patch("llm.chat", new_callable=AsyncMock, return_value=fake_response), \
             patch("metrics_collector.get_collector", return_value=fake_collector):
            task = sched.create("alert-job", {}, prompt="Monitor service", notify_channel_id=12345)
            task.alert_only = True
            await sched._execute_task(task)

        assert len(notify_calls) > 0
        assert notify_calls[0] is True


@pytest.mark.asyncio
class TestSkillJobTimeout:
    """Tests for skill-job timeout path (lines 375-378)."""

    async def test_execute_skill_with_timeout(self, sched):
        """Skill task handles timeout."""
        fake_collector = MagicMock()

        async def slow_skill(**kwargs):
            await asyncio.sleep(999)

        async def fake_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError()

        sched.register_skills({"slow_skill": slow_skill})
        task = sched.create("slow_skill", {})

        with patch("scheduler.asyncio.wait_for", side_effect=fake_wait_for), \
             patch("metrics_collector.get_collector", return_value=fake_collector):
            await sched._execute_task(task)

        assert "timed out" in task.last_result.lower() or "Error" in task.last_result

    async def test_execute_skill_with_notify_callback_alert(self, sched):
        """Skill task invokes notify_callback for alert results."""
        notify_calls = []

        async def notify(task_id, action, result, is_alert):
            notify_calls.append(is_alert)

        async def alert_skill(**kwargs):
            return "❌ critical error detected"

        fake_collector = MagicMock()
        sched.register_skills({"alert_skill": alert_skill})
        sched.notify_callback = notify
        task = sched.create("alert_skill", {}, notify_channel_id=99999)
        task.alert_only = True

        with patch("metrics_collector.get_collector", return_value=fake_collector):
            await sched._execute_task(task)

        assert len(notify_calls) > 0

    async def test_duplicate_task_skipped(self, sched):
        """A task already running is not executed again."""
        results = []

        async def slow_skill(**kwargs):
            await asyncio.sleep(0.2)
            results.append("ran")
            return "done"

        sched.register_skills({"slow_skill": slow_skill})
        task = sched.create("slow_skill", {})

        # Pre-mark as running
        async with sched._running_lock:
            sched._running_tasks.add(task.task_id)

        await sched._execute_task(task)
        assert len(results) == 0  # Never ran


@pytest.mark.asyncio
async def test_scheduler_start_creates_task(sched):
    """start() creates a background runner task."""
    assert sched._runner_task is None or sched._runner_task.done()
    sched.start()
    assert sched._runner_task is not None
    assert not sched._runner_task.done()
    # Cancel the task so it doesn't keep running
    sched._runner_task.cancel()
    try:
        await sched._runner_task
    except asyncio.CancelledError:
        pass


class TestLLMSchedulingSkills:
    """Tests for LLM-callable create/cancel/list/schedule_research functions."""

    @pytest.fixture(autouse=True)
    def patch_global_scheduler_save(self, tmp_path):
        """Patch global scheduler save to avoid writing to /memory."""
        temp_file = tmp_path / "global_schedules.json"
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            yield

    @pytest.mark.asyncio
    async def test_create_scheduled_task_no_skill_or_prompt(self):
        """create_scheduled_task returns error when neither skill nor prompt given."""
        from scheduler import create_scheduled_task
        result = await create_scheduled_task()
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_unknown_skill(self):
        """create_scheduled_task returns error for unknown skill name."""
        from scheduler import create_scheduled_task
        from scheduler import scheduler as global_sched
        global_sched._skill_registry.pop("nonexistent_skill_xyz", None)
        result = await create_scheduled_task(skill_name="nonexistent_skill_xyz")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_invalid_args_json(self):
        """create_scheduled_task returns error for invalid args_json."""
        from scheduler import create_scheduled_task
        from scheduler import scheduler as global_sched
        global_sched.register_skills({"list_containers": AsyncMock(return_value="OK")})
        result = await create_scheduled_task(skill_name="list_containers", args_json="{invalid")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_with_prompt_success(self):
        """create_scheduled_task creates prompt job successfully."""
        from scheduler import create_scheduled_task
        result = await create_scheduled_task(prompt="Daily weather check", interval_minutes=60)
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_with_skill_success(self):
        """create_scheduled_task creates skill job successfully."""
        from scheduler import create_scheduled_task
        from scheduler import scheduler as global_sched
        global_sched.register_skills({"my_test_skill": AsyncMock(return_value="OK")})
        result = await create_scheduled_task(skill_name="my_test_skill", hour=9)
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_invalid_cron(self):
        """create_scheduled_task validates cron expressions."""
        from scheduler import create_scheduled_task
        result = await create_scheduled_task(prompt="test", cron_expression="not a valid cron")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_create_scheduled_task_cron_with_label(self):
        """create_scheduled_task with cron and label creates task."""
        from scheduler import create_scheduled_task
        result = await create_scheduled_task(
            prompt="daily check",
            cron_expression="0 9 * * *",
            label="Morning Check",
        )
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_cancel_scheduled_task_success(self):
        """cancel_scheduled_task removes existing task."""
        from scheduler import cancel_scheduled_task
        from scheduler import scheduler as global_sched
        task = global_sched.create("list_containers", {})
        result = await cancel_scheduled_task(task.task_id)
        assert "✅" in result
        assert global_sched.get(task.task_id) is None

    @pytest.mark.asyncio
    async def test_cancel_scheduled_task_not_found(self):
        """cancel_scheduled_task returns error for non-existent task."""
        from scheduler import cancel_scheduled_task
        result = await cancel_scheduled_task("sched-9999999")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_list_scheduled_tasks_empty(self):
        """list_scheduled_tasks returns message when no tasks."""
        from scheduler import list_scheduled_tasks
        from scheduler import scheduler as global_sched
        orig_tasks = dict(global_sched._tasks)
        global_sched._tasks.clear()
        try:
            result = await list_scheduled_tasks()
            assert "No scheduled tasks" in result
        finally:
            global_sched._tasks.update(orig_tasks)

    @pytest.mark.asyncio
    async def test_list_scheduled_tasks_shows_entries(self):
        """list_scheduled_tasks returns formatted list."""
        from scheduler import list_scheduled_tasks
        from scheduler import scheduler as global_sched
        orig_tasks = dict(global_sched._tasks)
        global_sched._tasks.clear()
        try:
            global_sched.create("list_containers", {}, interval_minutes=60)
            result = await list_scheduled_tasks()
            assert "list_containers" in result
        finally:
            global_sched._tasks.clear()
            global_sched._tasks.update(orig_tasks)

    @pytest.mark.asyncio
    async def test_list_scheduled_tasks_with_prompt_job(self):
        """list_scheduled_tasks shows prompt jobs with icon."""
        from scheduler import list_scheduled_tasks
        from scheduler import scheduler as global_sched
        orig_tasks = dict(global_sched._tasks)
        global_sched._tasks.clear()
        try:
            global_sched.create("prompt-job", {}, prompt="Check weather", interval_minutes=30)
            result = await list_scheduled_tasks()
            assert "💬" in result
        finally:
            global_sched._tasks.clear()
            global_sched._tasks.update(orig_tasks)

    @pytest.mark.asyncio
    async def test_list_scheduled_tasks_cron_expression_shown(self):
        """list_scheduled_tasks shows cron expression correctly."""
        from scheduler import list_scheduled_tasks
        from scheduler import scheduler as global_sched
        orig_tasks = dict(global_sched._tasks)
        global_sched._tasks.clear()
        try:
            global_sched.create("my_action", {}, cron_expression="0 9 * * *")
            result = await list_scheduled_tasks()
            assert "0 9 * * *" in result
        finally:
            global_sched._tasks.clear()
            global_sched._tasks.update(orig_tasks)

    @pytest.mark.asyncio
    async def test_schedule_research_report_success(self):
        """schedule_research_report creates a scheduled research task."""
        from scheduler import schedule_research_report
        from scheduler import scheduler as global_sched
        orig_tasks = dict(global_sched._tasks)
        try:
            result = await schedule_research_report("AI trends in healthcare")
            assert "✅" in result
            assert "AI trends" in result
        finally:
            global_sched._tasks.clear()
            global_sched._tasks.update(orig_tasks)

    @pytest.mark.asyncio
    async def test_schedule_research_report_no_topic(self):
        """schedule_research_report returns error with empty topic."""
        from scheduler import schedule_research_report
        result = await schedule_research_report("")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_schedule_research_report_invalid_cron(self):
        """schedule_research_report validates cron expression."""
        from scheduler import schedule_research_report
        result = await schedule_research_report("test topic", cron_expression="bad cron!")
        assert "❌" in result
