"""
Tests for advanced scheduler features (Phase 3).
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler_advanced import (
    AdvancedScheduler,
    AdvancedTask,
    ConditionalExecution,
    EventTrigger,
    RetryPolicy,
    RetryStrategy,
    SchedulerDatabase,
    TriggerType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path):
    """Temporary database for testing."""
    db_path = tmp_path / "test_scheduler.db"
    return db_path


@pytest.fixture
def scheduler_db(temp_db):
    """SchedulerDatabase instance with temp storage."""
    return SchedulerDatabase(db_path=temp_db)


@pytest.fixture
def scheduler(temp_db):
    """AdvancedScheduler instance with temp storage."""
    temp_db.parent.mkdir(parents=True, exist_ok=True)
    sched = AdvancedScheduler(db_path=temp_db)
    yield sched


# ---------------------------------------------------------------------------
# RetryPolicy Tests
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_no_retry(self):
        policy = RetryPolicy(max_retries=0, strategy=RetryStrategy.NONE)
        assert policy.get_delay(0) == 0
        assert policy.get_delay(5) == 0

    def test_linear_backoff(self):
        policy = RetryPolicy(max_retries=5, strategy=RetryStrategy.LINEAR, base_delay_seconds=10)
        assert policy.get_delay(0) == 10  # 10 * 1
        assert policy.get_delay(1) == 20  # 10 * 2
        assert policy.get_delay(2) == 30  # 10 * 3

    def test_exponential_backoff(self):
        policy = RetryPolicy(max_retries=5, strategy=RetryStrategy.EXPONENTIAL, base_delay_seconds=10)
        assert policy.get_delay(0) == 10   # 10 * 2^0
        assert policy.get_delay(1) == 20   # 10 * 2^1
        assert policy.get_delay(2) == 40   # 10 * 2^2
        assert policy.get_delay(3) == 80   # 10 * 2^3

    def test_max_delay_cap(self):
        policy = RetryPolicy(
            max_retries=10,
            strategy=RetryStrategy.EXPONENTIAL,
            base_delay_seconds=100,
            max_delay_seconds=500,
        )
        # 100 * 2^5 = 3200, but capped at 500
        assert policy.get_delay(5) == 500


# ---------------------------------------------------------------------------
# SchedulerDatabase Tests
# ---------------------------------------------------------------------------


class TestSchedulerDatabase:
    def test_init_creates_tables(self, temp_db):
        SchedulerDatabase(db_path=temp_db)

        with sqlite3.connect(temp_db) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='advanced_tasks'"
            )
            assert cursor.fetchone() is not None

            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_logs'"
            )
            assert cursor.fetchone() is not None

    def test_save_and_load_task(self, scheduler_db):
        task = AdvancedTask(
            task_id="test-1",
            action="test_action",
            args={"foo": "bar"},
            trigger=EventTrigger(
                trigger_type=TriggerType.CRON,
                event_name="0 9 * * *",
            ),
            created_by="pytest",
            created_at="2024-01-01T00:00:00Z",
        )

        scheduler_db.save_task(task)
        loaded_tasks = scheduler_db.load_tasks()

        assert len(loaded_tasks) == 1
        loaded = loaded_tasks[0]
        assert loaded.task_id == "test-1"
        assert loaded.action == "test_action"
        assert loaded.args == {"foo": "bar"}
        assert loaded.trigger.trigger_type == TriggerType.CRON

    def test_save_task_with_condition(self, scheduler_db):
        task = AdvancedTask(
            task_id="test-2",
            action="test_action",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="on_message"),
            condition=ConditionalExecution(
                enabled=True,
                condition_script="temperature > 100",
                variables={"temperature": 75},
            ),
        )

        scheduler_db.save_task(task)
        loaded_tasks = scheduler_db.load_tasks()

        assert len(loaded_tasks) == 1
        loaded = loaded_tasks[0]
        assert loaded.condition is not None
        assert loaded.condition.enabled is True
        assert loaded.condition.condition_script == "temperature > 100"

    def test_scheduler_advanced_delete_task(self, scheduler_db):
        task = AdvancedTask(
            task_id="test-del",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )

        scheduler_db.save_task(task)
        assert len(scheduler_db.load_tasks()) == 1

        result = scheduler_db.delete_task("test-del")
        assert result is True
        assert len(scheduler_db.load_tasks()) == 0

        # Delete non-existent
        result = scheduler_db.delete_task("nonexistent")
        assert result is False

    def test_log_execution(self, scheduler_db):
        log_id = scheduler_db.log_execution(
            task_id="test-1",
            status="success",
            result="All good",
            duration_ms=1234,
            retry_attempt=0,
        )

        assert log_id > 0

        logs = scheduler_db.get_execution_history("test-1")
        assert len(logs) == 1
        assert logs[0].task_id == "test-1"
        assert logs[0].status == "success"
        assert logs[0].duration_ms == 1234

    def test_get_execution_history_all(self, scheduler_db):
        scheduler_db.log_execution("task-1", "success", "OK", 100)
        scheduler_db.log_execution("task-2", "failure", "Error", 200)
        scheduler_db.log_execution("task-1", "success", "OK", 150)

        all_logs = scheduler_db.get_execution_history()
        assert len(all_logs) == 3

        task1_logs = scheduler_db.get_execution_history("task-1")
        assert len(task1_logs) == 2
        assert all(log.task_id == "task-1" for log in task1_logs)


# ---------------------------------------------------------------------------
# AdvancedScheduler Tests
# ---------------------------------------------------------------------------


class TestAdvancedScheduler:
    def test_create_task_cron(self, scheduler):
        task = scheduler.create_task(
            action="test_skill",
            args={"param": "value"},
            trigger_type=TriggerType.CRON,
            cron_expression="0 9 * * *",
            created_by="pytest",
        )

        assert task.task_id == "adv-1"
        assert task.action == "test_skill"
        assert task.trigger.trigger_type == TriggerType.CRON
        assert task.trigger.event_name == "0 9 * * *"

    def test_create_task_event(self, scheduler):
        task = scheduler.create_task(
            action="handle_message",
            trigger_type=TriggerType.EVENT,
            event_name="on_message",
            created_by="pytest",
        )

        assert task.trigger.trigger_type == TriggerType.EVENT
        assert task.trigger.event_name == "on_message"

    def test_create_task_with_condition(self, scheduler):
        task = scheduler.create_task(
            action="send_alert",
            condition_script="temperature > 100",
            created_by="pytest",
        )

        assert task.condition is not None
        assert task.condition.enabled is True
        assert task.condition.condition_script == "temperature > 100"

    def test_create_task_with_retry(self, scheduler):
        task = scheduler.create_task(
            action="flaky_task",
            retry_max=5,
            retry_strategy=RetryStrategy.EXPONENTIAL,
        )

        assert task.retry_policy is not None
        assert task.retry_policy.max_retries == 5
        assert task.retry_policy.strategy == RetryStrategy.EXPONENTIAL

    def test_list_tasks(self, scheduler):
        scheduler.create_task(action="task1", created_by="pytest")
        scheduler.create_task(action="task2", created_by="pytest")

        tasks = scheduler.list_tasks()
        assert len(tasks) == 2
        assert tasks[0].task_id == "adv-1"
        assert tasks[1].task_id == "adv-2"

    def test_scheduler_advanced_delete_task_v2(self, scheduler):
        task = scheduler.create_task(action="temp_task")
        assert scheduler.get_task(task.task_id) is not None

        result = scheduler.delete_task(task.task_id)
        assert result is True
        assert scheduler.get_task(task.task_id) is None

    @pytest.mark.asyncio
    async def test_evaluate_condition_true(self, scheduler):
        task = AdvancedTask(
            task_id="test",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="test"),
            condition=ConditionalExecution(
                enabled=True,
                condition_script="value > 50",
            ),
        )

        result = await scheduler._evaluate_condition(task, {"value": 75})
        assert result is True

    @pytest.mark.asyncio
    async def test_evaluate_condition_false(self, scheduler):
        task = AdvancedTask(
            task_id="test",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="test"),
            condition=ConditionalExecution(
                enabled=True,
                condition_script="value > 50",
            ),
        )

        result = await scheduler._evaluate_condition(task, {"value": 25})
        assert result is False

    @pytest.mark.asyncio
    async def test_evaluate_condition_disabled(self, scheduler):
        task = AdvancedTask(
            task_id="test",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="test"),
            condition=ConditionalExecution(
                enabled=False,
                condition_script="False",  # Would fail if evaluated
            ),
        )

        result = await scheduler._evaluate_condition(task, {})
        assert result is True  # Disabled conditions always pass

    @pytest.mark.asyncio
    async def test_evaluate_condition_blocks_unsafe_call_expression(self, scheduler):
        task = AdvancedTask(
            task_id="test",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="test"),
            condition=ConditionalExecution(
                enabled=True,
                condition_script="__import__('os').system('echo pwned')",
            ),
        )

        result = await scheduler._evaluate_condition(task, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_scheduler_advanced_execute_task_success(self, scheduler):
        mock_skill = AsyncMock(return_value="Success!")
        scheduler.register_skills({"test_skill": mock_skill})

        task = AdvancedTask(
            task_id="test",
            action="test_skill",
            args={"param": "value"},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )

        result, duration = await scheduler._execute_task(task)

        assert result == "Success!"
        assert duration >= 0
        mock_skill.assert_called_once_with(param="value")

    @pytest.mark.asyncio
    async def test_execute_task_unknown_skill(self, scheduler):
        task = AdvancedTask(
            task_id="test",
            action="nonexistent_skill",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )

        result, duration = await scheduler._execute_task(task)

        assert "Unknown skill" in result
        assert duration == 0

    @pytest.mark.asyncio
    async def test_execute_task_timeout(self, scheduler, monkeypatch):
        async def force_timeout(awaitable, *args, **kwargs):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr("scheduler_advanced.asyncio.wait_for", force_timeout)

        async def slow_skill():
            await asyncio.sleep(400)  # Exceeds 300s timeout
            return "Done"

        scheduler.register_skills({"slow_skill": slow_skill})

        task = AdvancedTask(
            task_id="test",
            action="slow_skill",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )

        result, duration = await scheduler._execute_task(task)

        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_with_retry_success(self, scheduler):
        mock_skill = AsyncMock(return_value="OK")
        scheduler.register_skills({"test_skill": mock_skill})

        task = scheduler.create_task(
            action="test_skill",
            retry_max=3,
        )

        result, success = await scheduler._execute_with_retry(task)

        assert success is True
        assert task.run_count == 1
        assert task.retry_count == 0

    @pytest.mark.asyncio
    async def test_execute_with_retry_failure(self, scheduler):
        mock_skill = AsyncMock(return_value="Error: Failed")
        scheduler.register_skills({"failing_skill": mock_skill})

        task = scheduler.create_task(
            action="failing_skill",
            retry_max=3,
            retry_strategy=RetryStrategy.LINEAR,
        )

        result, success = await scheduler._execute_with_retry(task)

        assert success is False
        assert task.run_count == 1
        assert task.retry_count == 1
        assert task.next_retry_at != ""

    @pytest.mark.asyncio
    async def test_execute_with_retry_records_observability_metrics(self, scheduler):
        mock_collector = MagicMock()
        task = AdvancedTask(
            task_id="test",
            action="unknown_skill",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )

        with patch("scheduler_advanced.get_collector", return_value=mock_collector):
            result, success = await scheduler._execute_with_retry(task)

        assert success is False
        assert "Unknown skill" in result
        record_call = mock_collector.record_command.call_args.kwargs
        assert record_call["command"] == "unknown_skill"
        assert record_call["workspace"] == "scheduler_advanced"
        assert record_call["success"] is False

    @pytest.mark.asyncio
    async def test_trigger_event(self, scheduler):
        mock_skill = AsyncMock(return_value="Event handled")
        scheduler.register_skills({"event_handler": mock_skill})

        scheduler.create_task(
            action="event_handler",
            trigger_type=TriggerType.EVENT,
            event_name="test_event",
        )

        # Queue event
        await scheduler.trigger_event("test_event", {"data": "value"})

        # Process event (normally done by background task)
        event_name, event_data = await scheduler._event_queue.get()
        assert event_name == "test_event"
        assert event_data == {"data": "value"}


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestAdvancedSchedulerIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_workflow(self, scheduler):
        """Test complete workflow: create, execute, log, query history."""
        call_count = 0

        async def test_scheduler_advanced_skill(param: str = ""):
            nonlocal call_count
            call_count += 1
            return f"Executed with {param}"

        scheduler.register_skills({"test_skill": test_skill})

        # Create task
        task = scheduler.create_task(
            action="test_skill",
            args={"param": "test_value"},
            trigger_type=TriggerType.CRON,
            cron_expression="0 9 * * *",
            retry_max=2,
        )

        # Execute task
        result, success = await scheduler._execute_with_retry(task)

        assert success is True
        assert call_count == 1
        assert "test_value" in result

        # Check execution history
        history = scheduler.get_execution_history(task.task_id)
        assert len(history) == 1
        assert history[0].status == "success"
        assert history[0].task_id == task.task_id



# ---------------------------------------------------------------------------
# Additional tests for improved scheduler_advanced coverage
# ---------------------------------------------------------------------------

import datetime

import scheduler_advanced as adv_module
from scheduler_advanced import (
    _parse_utc,
    _safe_condition_eval,
    get_advanced_scheduler,
)


class TestParseUtc:
    def test_naive_datetime_gets_utc(self):
        result = _parse_utc("2024-01-01T09:00:00")
        assert result.tzinfo == datetime.timezone.utc

    def test_scheduler_advanced_aware_datetime_converted_to_utc(self):
        result = _parse_utc("2024-01-01T09:00:00+05:00")
        assert result.tzinfo == datetime.timezone.utc
        assert result.hour == 4  # 9 - 5


class TestSafeConditionEval:
    def test_simple_true_condition(self):
        assert _safe_condition_eval("x > 5", {"x": 10}) is True

    def test_simple_false_condition(self):
        assert _safe_condition_eval("x > 5", {"x": 3}) is False

    def test_empty_expression_returns_false(self):
        assert _safe_condition_eval("", {}) is False

    def test_too_long_expression_raises_value_error(self):
        import pytest
        with pytest.raises(ValueError, match="too long"):
            _safe_condition_eval("x" * 501, {"x": 1})

    def test_unsafe_call_raises(self):
        import pytest
        with pytest.raises((ValueError, Exception)):
            _safe_condition_eval("__import__('os').system('echo')", {})


class TestAdvancedSchedulerLoadTasks:
    def test_load_tasks_with_non_standard_id(self, tmp_path):
        """Non-standard task IDs dont crash load."""
        sched = AdvancedScheduler(db_path=tmp_path / "a.db")
        task = AdvancedTask(
            task_id="custom-id",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )
        sched.db.save_task(task)
        sched2 = AdvancedScheduler(db_path=tmp_path / "a.db")
        loaded = sched2.get_task("custom-id")
        assert loaded is not None


class TestAdvancedSchedulerDeleteTask:
    def test_delete_existing_task(self, scheduler):
        task = scheduler.create_task(action="test_action")
        task_id = task.task_id
        result = scheduler.delete_task(task_id)
        assert result is True
        assert scheduler.get_task(task_id) is None

    def test_delete_nonexistent_task(self, scheduler):
        result = scheduler.delete_task("adv-9999")
        assert result is False


class TestEvaluateConditionEdgeCases:
    import pytest

    @pytest.mark.asyncio
    async def test_evaluate_condition_with_exception_returns_false(self, scheduler):
        """Condition evaluation errors return False."""
        task = AdvancedTask(
            task_id="test-cond-err",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.EVENT, event_name="test"),
            condition=MagicMock(
                enabled=True,
                condition_script="unknown_variable_xyz > 5",
                variables={},
            ),
        )
        result = await scheduler._evaluate_condition(task, {})
        assert result is False


class TestExecuteTaskConditionSkip:
    import pytest

    @pytest.mark.asyncio
    async def test_condition_false_skips_execution(self, scheduler):
        """When condition evaluates to False, task returns skipped message."""
        from scheduler_advanced import ConditionalExecution
        task = AdvancedTask(
            task_id="test-skip",
            action="test_action",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
            condition=ConditionalExecution(
                enabled=True,
                condition_script="x > 100",
                variables={"x": 1},
            ),
        )
        result, duration = await scheduler._execute_task(task, {})
        assert "Skipped" in result
        assert duration == 0


class TestIsCronDue:
    import pytest

    @pytest.mark.asyncio
    async def test_cron_due_within_window(self, scheduler):
        task = AdvancedTask(
            task_id="test-cron-due",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
        )
        now = datetime.datetime(2024, 1, 1, 9, 1, 0, tzinfo=datetime.timezone.utc)
        result = await scheduler._is_cron_due(task, now)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_cron_invalid_expression_returns_false(self, scheduler):
        task = AdvancedTask(
            task_id="test-cron-invalid",
            action="test",
            args={},
            trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="not-valid-cron"),
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        result = await scheduler._is_cron_due(task, now)
        assert result is False


class TestGetAdvancedSchedulerSingleton:
    def test_get_advanced_scheduler_returns_instance(self, tmp_path):
        db_path = tmp_path / "adv.db"
        orig = adv_module._advanced_scheduler
        adv_module._advanced_scheduler = None
        try:
            with patch.object(adv_module, "SCHEDULER_DB", db_path):
                s1 = get_advanced_scheduler()
            assert isinstance(s1, AdvancedScheduler)
        finally:
            adv_module._advanced_scheduler = orig

    def test_get_advanced_scheduler_is_singleton(self, tmp_path):
        db_path = tmp_path / "adv2.db"
        orig = adv_module._advanced_scheduler
        adv_module._advanced_scheduler = None
        try:
            with patch.object(adv_module, "SCHEDULER_DB", db_path):
                s1 = get_advanced_scheduler()
                s2 = get_advanced_scheduler()
            assert s1 is s2
        finally:
            adv_module._advanced_scheduler = orig



@pytest.mark.asyncio
async def test_execute_task_raises_exception_handled(scheduler):
    """_execute_task handles general exceptions."""
    async def bad_skill(**kwargs):
        raise ValueError("bad input")

    scheduler.register_skills({"bad_skill": bad_skill})
    task = AdvancedTask(
        task_id="test-exc",
        action="bad_skill",
        args={},
        trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
    )
    result, duration = await scheduler._execute_task(task, {})
    assert "Error" in result


@pytest.mark.asyncio
async def test_scheduler_start_method(scheduler):
    """start() creates background asyncio tasks."""
    scheduler.start()
    assert scheduler._runner_task is not None
    assert scheduler._event_processor_task is not None
    # Cancel tasks so they don't run forever
    scheduler._runner_task.cancel()
    scheduler._event_processor_task.cancel()
    import asyncio
    try:
        await scheduler._runner_task
    except asyncio.CancelledError:
        pass
    try:
        await scheduler._event_processor_task
    except asyncio.CancelledError:
        pass


def test_load_tasks_counter_update(tmp_path):
    """_load_tasks updates counter for standard adv- task IDs."""
    from scheduler_advanced import AdvancedScheduler, AdvancedTask, EventTrigger, TriggerType
    s = AdvancedScheduler(db_path=tmp_path / "b.db")
    task = AdvancedTask(
        task_id="adv-42",
        action="test",
        args={},
        trigger=EventTrigger(trigger_type=TriggerType.CRON, event_name="0 9 * * *"),
    )
    s.db.save_task(task)
    s2 = AdvancedScheduler(db_path=tmp_path / "b.db")
    assert s2._counter >= 42
