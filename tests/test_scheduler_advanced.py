"""
Tests for advanced scheduler features (Phase 3).
"""

import asyncio
import datetime
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
def scheduler(temp_db, monkeypatch):
    """AdvancedScheduler instance with temp storage."""
    monkeypatch.setattr("scheduler_advanced.SCHEDULER_DB", temp_db)
    # Also need to ensure parent directory exists
    temp_db.parent.mkdir(parents=True, exist_ok=True)
    sched = AdvancedScheduler()
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
        db = SchedulerDatabase(db_path=temp_db)
        
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
    
    def test_delete_task(self, scheduler_db):
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
    
    def test_delete_task(self, scheduler):
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
    async def test_execute_task_success(self, scheduler):
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
        assert duration > 0
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
    async def test_execute_task_timeout(self, scheduler):
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
    async def test_trigger_event(self, scheduler):
        mock_skill = AsyncMock(return_value="Event handled")
        scheduler.register_skills({"event_handler": mock_skill})
        
        task = scheduler.create_task(
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
        
        async def test_skill(param: str = ""):
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
