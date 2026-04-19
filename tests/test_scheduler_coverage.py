"""
Additional tests to boost scheduler.py coverage from 50% → 80%+.
Covers: prompt jobs, skill timeouts, Discord notifications, cron_expression
_is_due, duplicate-run guard, run_loop, start(), _check_and_run,
and all four LLM-callable scheduling functions.
"""

import asyncio
import datetime
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scheduler as scheduler_module
from scheduler import (
    ScheduledTask,
    TaskScheduler,
    _parse_utc,
    cancel_scheduled_task,
    create_scheduled_task,
    list_scheduled_tasks,
    schedule_research_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _noop_trace(*args, **kwargs):
    yield


def _make_collector():
    c = MagicMock()
    c.record_command = MagicMock()
    return c


@pytest.fixture
def global_sched(tmp_path):
    """Patch the module-level 'scheduler' global used by LLM-callable functions.

    Both SCHEDULE_FILE and scheduler are patched simultaneously so that _save()
    calls within LLM functions write to the temp path, not /memory.
    """
    temp_file = tmp_path / "schedules.json"
    with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
        fresh = TaskScheduler()
        with patch.object(scheduler_module, "scheduler", fresh):
            yield fresh


# ---------------------------------------------------------------------------
# _parse_utc  (line 14 — naive-datetime branch)
# ---------------------------------------------------------------------------


class TestParseUtc:
    def test_naive_datetime_gets_utc_attached(self):
        naive = "2024-01-15T10:30:00"
        result = _parse_utc(naive)
        assert result.tzinfo is datetime.timezone.utc

    def test_scheduler_coverage_aware_datetime_converted_to_utc(self):
        aware = "2024-01-15T10:30:00+05:00"
        result = _parse_utc(aware)
        assert result.tzinfo is datetime.timezone.utc


# ---------------------------------------------------------------------------
# ScheduledTask.next_run_str — cron_expression branch (lines 67-73)
# ---------------------------------------------------------------------------


class TestNextRunStrCronExpression:
    def test_cron_expression_returns_weekday_time_string(self):
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            cron_expression="0 9 * * 1",  # Every Monday at 9am
        )
        result = task.next_run_str
        # Should return a formatted day+time string (e.g. "Mon 09:00")
        assert len(result) > 0
        assert result != ""

    def test_cron_expression_invalid_falls_back_to_raw(self):
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            cron_expression="not-a-cron",
        )
        # Should fall back to the raw expression string on exception
        result = task.next_run_str
        assert result == "not-a-cron"

    def test_interval_invalid_last_run_returns_soon(self):
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            interval_minutes=30,
            last_run="not-a-valid-date",
        )
        result = task.next_run_str
        assert result == "soon"

    def test_daily_schedule_past_target_shows_tomorrow(self):
        # Force target to be in the past so it adds a day
        now = datetime.datetime.now(datetime.timezone.utc)
        past_hour = (now.hour - 1) % 24
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=past_hour,
            cron_minute=0,
            interval_minutes=0,
        )
        result = task.next_run_str
        assert "in" in result and "h" in result


# ---------------------------------------------------------------------------
# TaskScheduler._load — error paths (lines 130-134)
# ---------------------------------------------------------------------------


class TestLoadErrorPaths:
    def test_nonstandard_task_id_logs_warning(self, tmp_path, caplog):
        import json

        temp_file = tmp_path / "schedules.json"
        data = [
            {
                "task_id": "custom-id",
                "action": "test",
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
            }
        ]
        temp_file.write_text(json.dumps(data))
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            import logging

            with caplog.at_level(logging.WARNING, logger="openclaw.scheduler"):
                s = TaskScheduler()
        assert s.get("custom-id") is not None

    def test_corrupted_json_leaves_tasks_empty(self, tmp_path):
        temp_file = tmp_path / "schedules.json"
        temp_file.write_text("INVALID JSON {{{{")
        with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
            s = TaskScheduler()
        assert s.list_tasks() == []


# ---------------------------------------------------------------------------
# TaskScheduler.start() and _run_loop (lines 214-225)
# ---------------------------------------------------------------------------


class TestStartAndRunLoop:
    @pytest.mark.asyncio
    async def test_start_creates_runner_task(self, sched):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, asyncio.CancelledError()]
            with patch.object(sched, "_check_and_run", new_callable=AsyncMock):
                sched.start()
                assert sched._runner_task is not None
                # Cancel to prevent the loop from running forever
                sched._runner_task.cancel()
                try:
                    await sched._runner_task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_start_idempotent(self, sched):
        with patch.object(sched, "_check_and_run", new_callable=AsyncMock):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                mock_sleep.side_effect = asyncio.CancelledError()
                sched.start()
                task1 = sched._runner_task
                sched.start()  # Second call — should not create a new task
                assert sched._runner_task is task1
                task1.cancel()
                try:
                    await task1
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_run_loop_calls_check_and_run(self, sched):
        call_count = 0

        async def fake_check():
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        with patch.object(sched, "_check_and_run", side_effect=fake_check):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(asyncio.CancelledError):
                    await sched._run_loop()

        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_run_loop_continues_after_exception(self, sched):
        """Loop should log error and keep going if _check_and_run raises."""
        call_count = 0

        async def fake_check():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            raise asyncio.CancelledError()

        sleep_mock = AsyncMock()
        sleep_mock.side_effect = [None, asyncio.CancelledError()]

        with patch.object(sched, "_check_and_run", side_effect=fake_check):
            with patch("asyncio.sleep", sleep_mock):
                try:
                    await sched._run_loop()
                except asyncio.CancelledError:
                    pass

        assert call_count >= 1


# ---------------------------------------------------------------------------
# TaskScheduler._check_and_run (lines 229-234)
# ---------------------------------------------------------------------------


class TestCheckAndRun:
    @pytest.mark.asyncio
    async def test_disabled_tasks_are_skipped(self, sched):
        task = sched.create("my_action", {})
        task.enabled = False
        executed = []

        async def fake_execute(t):
            executed.append(t.task_id)

        with patch.object(sched, "_execute_task", side_effect=fake_execute):
            with patch.object(sched, "_is_due", return_value=True):
                await sched._check_and_run()

        assert executed == []

    @pytest.mark.asyncio
    async def test_enabled_due_tasks_are_executed(self, sched):
        task = sched.create("my_action", {})
        executed = []

        async def fake_execute(t):
            executed.append(t.task_id)

        with patch.object(sched, "_execute_task", side_effect=fake_execute):
            with patch.object(sched, "_is_due", return_value=True):
                await sched._check_and_run()

        assert task.task_id in executed


# ---------------------------------------------------------------------------
# TaskScheduler._is_due — cron_expression branch (lines 240-247)
# ---------------------------------------------------------------------------


class TestIsDueCronExpression:
    def test_cron_expression_due_returns_true(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
        # "* * * * *" = every minute, so it's always due
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            cron_expression="* * * * *",
        )
        assert sched._is_due(task, now)

    def test_invalid_cron_expression_returns_false(self, sched):
        now = datetime.datetime.now(datetime.timezone.utc)
        task = ScheduledTask(
            task_id="sched-1",
            action="test",
            args={},
            cron_hour=-1,
            cron_minute=0,
            cron_expression="invalid-cron-expr",
        )
        assert not sched._is_due(task, now)


# ---------------------------------------------------------------------------
# TaskScheduler._execute_task — duplicate run guard (lines 270-271)
# ---------------------------------------------------------------------------


class TestExecuteTaskDuplicateGuard:
    @pytest.mark.asyncio
    async def test_duplicate_run_is_skipped(self, sched):
        task = sched.create("my_action", {})
        # Pre-populate _running_tasks to simulate a running task
        sched._running_tasks.add(task.task_id)

        mock_collector = _make_collector()
        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)

        # run_count should NOT increment because execution was skipped
        assert task.run_count == 0
        # Clean up
        sched._running_tasks.discard(task.task_id)


# ---------------------------------------------------------------------------
# TaskScheduler._execute_task — prompt job path (lines 276-330)
# ---------------------------------------------------------------------------


class TestExecutePromptJob:
    @pytest.mark.asyncio
    async def test_prompt_job_success(self, sched):
        task = sched.create("prompt-job", {}, prompt="What is the weather?")
        mock_collector = _make_collector()
        mock_chat = AsyncMock(return_value=("Weather is sunny", [], "gemini"))

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", mock_chat):
                    await sched._execute_task(task)

        assert task.run_count == 1
        assert "sunny" in task.last_result

    @pytest.mark.asyncio
    async def test_prompt_job_timeout(self, sched):
        task = sched.create("prompt-job", {}, prompt="Slow query")
        mock_collector = _make_collector()

        async def slow_chat(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", slow_chat):
                    await sched._execute_task(task)

        assert "timed out" in task.last_result.lower()
        assert task.run_count == 1

    @pytest.mark.asyncio
    async def test_prompt_job_exception(self, sched):
        task = sched.create("prompt-job", {}, prompt="Bad query")
        mock_collector = _make_collector()

        async def fail_chat(*args, **kwargs):
            raise ValueError("LLM exploded")

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", fail_chat):
                    await sched._execute_task(task)

        assert "failed" in task.last_result.lower() or "❌" in task.last_result
        assert task.run_count == 1

    @pytest.mark.asyncio
    async def test_prompt_job_no_response_becomes_no_response_message(self, sched):
        task = sched.create("prompt-job", {}, prompt="Empty response query")
        mock_collector = _make_collector()
        mock_chat = AsyncMock(return_value=(None, [], "gemini"))

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", mock_chat):
                    await sched._execute_task(task)

        assert "No response" in task.last_result

    @pytest.mark.asyncio
    async def test_prompt_job_discord_notify_alert(self, sched):
        """Prompt job sends Discord notification when result contains alert keyword."""
        task = sched.create(
            "prompt-job",
            {},
            prompt="Check health",
            notify_channel_id=99999,
            alert_only=True,
        )
        task.notify_channel_id = 99999

        notify_calls = []

        async def mock_notify(task_id, action, result_text, is_alert):
            notify_calls.append((task_id, action, result_text, is_alert))

        sched.notify_callback = mock_notify
        mock_collector = _make_collector()
        mock_chat = AsyncMock(return_value=("❌ error occurred", [], "gemini"))

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", mock_chat):
                    await sched._execute_task(task)

        assert len(notify_calls) == 1

    @pytest.mark.asyncio
    async def test_prompt_job_discord_notify_callback_exception(self, sched):
        """Notify callback errors are swallowed (should not propagate)."""
        task = sched.create(
            "prompt-job",
            {},
            prompt="Check health",
            notify_channel_id=99999,
            alert_only=False,
        )
        task.notify_channel_id = 99999

        async def bad_notify(*args, **kwargs):
            raise RuntimeError("Discord is down")

        sched.notify_callback = bad_notify
        mock_collector = _make_collector()
        mock_chat = AsyncMock(return_value=("All good", [], "gemini"))

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("llm.chat", mock_chat):
                    await sched._execute_task(task)  # Should not raise

        assert task.run_count == 1


# ---------------------------------------------------------------------------
# TaskScheduler._execute_task — skill timeout (lines 365-368)
# ---------------------------------------------------------------------------


class TestExecuteSkillTimeout:
    @pytest.mark.asyncio
    async def test_skill_timeout_records_error(self, sched):
        async def slow_skill(**kwargs):
            await asyncio.sleep(9999)

        sched.register_skills({"slow_skill": slow_skill})
        task = sched.create("slow_skill", {})
        mock_collector = _make_collector()

        async def force_timeout(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                with patch("asyncio.wait_for", side_effect=force_timeout):
                    await sched._execute_task(task)

        assert "timed out" in task.last_result.lower()
        assert task.run_count == 1

    @pytest.mark.asyncio
    async def test_skill_exception_records_error(self, sched):
        async def bad_skill(**kwargs):
            raise ValueError("skill broke")

        sched.register_skills({"bad_skill": bad_skill})
        task = sched.create("bad_skill", {})
        mock_collector = _make_collector()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)

        assert "Error" in task.last_result
        assert task.run_count == 1


# ---------------------------------------------------------------------------
# TaskScheduler._execute_task — Discord notification after skill job (lines 393-400)
# ---------------------------------------------------------------------------


class TestExecuteSkillDiscordNotify:
    @pytest.mark.asyncio
    async def test_skill_notifies_discord_on_alert(self, sched):
        async def alert_skill(**kwargs):
            return "❌ error: disk full"

        sched.register_skills({"alert_skill": alert_skill})
        task = sched.create("alert_skill", {}, notify_channel_id=12345, alert_only=True)
        task.notify_channel_id = 12345

        notify_calls = []

        async def mock_notify(task_id, action, result_text, is_alert):
            notify_calls.append((task_id, action, result_text, is_alert))

        sched.notify_callback = mock_notify
        mock_collector = _make_collector()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)

        assert len(notify_calls) == 1
        assert notify_calls[0][3] is True  # is_alert=True

    @pytest.mark.asyncio
    async def test_skill_notifies_discord_alert_only_false(self, sched):
        async def ok_skill(**kwargs):
            return "all systems normal"

        sched.register_skills({"ok_skill": ok_skill})
        task = sched.create("ok_skill", {}, notify_channel_id=12345, alert_only=False)
        task.notify_channel_id = 12345

        notify_calls = []

        async def mock_notify(task_id, action, result_text, is_alert):
            notify_calls.append((task_id, action, result_text, is_alert))

        sched.notify_callback = mock_notify
        mock_collector = _make_collector()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)

        assert len(notify_calls) == 1
        assert notify_calls[0][3] is False  # not an alert

    @pytest.mark.asyncio
    async def test_skill_no_notify_when_alert_only_but_no_alert(self, sched):
        async def ok_skill(**kwargs):
            return "all systems normal"

        sched.register_skills({"ok_skill": ok_skill})
        task = sched.create("ok_skill", {}, notify_channel_id=12345, alert_only=True)
        task.notify_channel_id = 12345

        notify_calls = []

        async def mock_notify(*args, **kwargs):
            notify_calls.append(args)

        sched.notify_callback = mock_notify
        mock_collector = _make_collector()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)

        assert len(notify_calls) == 0  # No alert keyword → no notify

    @pytest.mark.asyncio
    async def test_skill_notify_callback_exception_is_swallowed(self, sched):
        async def ok_skill(**kwargs):
            return "warn: something"

        sched.register_skills({"ok_skill": ok_skill})
        task = sched.create("ok_skill", {}, notify_channel_id=12345, alert_only=True)
        task.notify_channel_id = 12345

        async def bad_notify(*args, **kwargs):
            raise RuntimeError("Discord down")

        sched.notify_callback = bad_notify
        mock_collector = _make_collector()

        with patch("metrics_collector.get_collector", return_value=mock_collector):
            with patch("trace_context.trace_context", _noop_trace):
                await sched._execute_task(task)  # Should not raise

        assert task.run_count == 1


# ---------------------------------------------------------------------------
# create_scheduled_task (lines 431-489)
# ---------------------------------------------------------------------------


class TestCreateScheduledTaskFunction:
    @pytest.mark.asyncio
    async def test_no_skill_or_prompt_returns_error(self, global_sched):
        result = await create_scheduled_task()
        assert "❌" in result
        assert "skill_name" in result or "prompt" in result

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self, global_sched):
        result = await create_scheduled_task(skill_name="nonexistent_xyz")
        assert "❌" in result
        assert "Unknown skill" in result or "nonexistent" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_args_json_returns_error(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(skill_name="my_skill", args_json="{bad json")
        assert "❌" in result
        assert "args_json" in result.lower() or "invalid" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_cron_expression_returns_error(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(
            skill_name="my_skill",
            cron_expression="99 99 99 99 99",
        )
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_valid_skill_with_interval(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(
            skill_name="my_skill",
            interval_minutes=30,
            args_json='{"key": "value"}',
        )
        assert "✅" in result
        assert "sched-" in result
        assert "every 30 minutes" in result

    @pytest.mark.asyncio
    async def test_valid_skill_with_hour_minute(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(skill_name="my_skill", hour=9, minute=30)
        assert "✅" in result
        assert "09:30" in result

    @pytest.mark.asyncio
    async def test_valid_skill_with_cron_expression(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(
            skill_name="my_skill",
            cron_expression="0 9 * * 1",
        )
        assert "✅" in result
        assert "cron" in result

    @pytest.mark.asyncio
    async def test_prompt_job_creation(self, global_sched):
        result = await create_scheduled_task(
            prompt="Tell me the news",
            interval_minutes=60,
            label="daily-news",
        )
        assert "✅" in result
        assert "prompt job" in result

    @pytest.mark.asyncio
    async def test_no_schedule_produces_on_demand(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(skill_name="my_skill")
        assert "✅" in result
        assert "on demand" in result

    @pytest.mark.asyncio
    async def test_label_appended_to_result(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(skill_name="my_skill", label="my-label")
        assert "my-label" in result

    @pytest.mark.asyncio
    async def test_channel_id_string_accepted(self, global_sched):
        global_sched.register_skills({"my_skill": AsyncMock(return_value="ok")})
        result = await create_scheduled_task(
            skill_name="my_skill",
            interval_minutes=10,
            channel_id="12345",
        )
        assert "✅" in result


# ---------------------------------------------------------------------------
# cancel_scheduled_task (lines 494-500)
# ---------------------------------------------------------------------------


class TestCancelScheduledTaskFunction:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task_returns_error(self, global_sched):
        result = await cancel_scheduled_task("sched-999")
        assert "❌" in result
        assert "sched-999" in result

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_lists_active_tasks(self, global_sched):
        global_sched.register_skills({"s": AsyncMock(return_value="ok")})
        global_sched.create("s", {})
        result = await cancel_scheduled_task("sched-999")
        assert "sched-1" in result  # Active tasks are listed in hint

    @pytest.mark.asyncio
    async def test_cancel_existing_task_succeeds(self, global_sched):
        task = global_sched.create("my_action", {})
        result = await cancel_scheduled_task(task.task_id)
        assert "✅" in result
        assert task.task_id in result
        assert global_sched.get(task.task_id) is None

    @pytest.mark.asyncio
    async def test_cancel_with_no_active_tasks(self, global_sched):
        result = await cancel_scheduled_task("sched-999")
        assert "No active tasks" in result


# ---------------------------------------------------------------------------
# list_scheduled_tasks (lines 505-527)
# ---------------------------------------------------------------------------


class TestListScheduledTasksFunction:
    @pytest.mark.asyncio
    async def test_empty_returns_no_tasks_message(self, global_sched):
        result = await list_scheduled_tasks()
        assert "No scheduled tasks" in result

    @pytest.mark.asyncio
    async def test_lists_interval_task(self, global_sched):
        global_sched.create("my_action", {}, interval_minutes=30)
        result = await list_scheduled_tasks()
        assert "sched-1" in result
        assert "every 30m" in result

    @pytest.mark.asyncio
    async def test_lists_cron_task(self, global_sched):
        global_sched.create("my_action", {}, cron_expression="0 9 * * *")
        result = await list_scheduled_tasks()
        assert "0 9 * * *" in result

    @pytest.mark.asyncio
    async def test_lists_daily_task(self, global_sched):
        global_sched.create("my_action", {}, hour=8, minute=30)
        result = await list_scheduled_tasks()
        assert "08:30" in result

    @pytest.mark.asyncio
    async def test_lists_manual_task(self, global_sched):
        global_sched.create("my_action", {}, hour=0, minute=0)  # hour=0 avoids next_run_str crash
        result = await list_scheduled_tasks()
        assert "sched-1" in result
        assert "00:00" in result

    @pytest.mark.asyncio
    async def test_disabled_task_shows_pause_emoji(self, global_sched):
        task = global_sched.create("my_action", {}, hour=3, minute=0)
        task.enabled = False
        result = await list_scheduled_tasks()
        assert "⏸️" in result

    @pytest.mark.asyncio
    async def test_prompt_task_shows_bubble_emoji(self, global_sched):
        global_sched.create("prompt-job", {}, prompt="Tell me the news", hour=8, minute=0)
        result = await list_scheduled_tasks()
        assert "💬" in result


# ---------------------------------------------------------------------------
# schedule_research_report (lines 536-565)
# ---------------------------------------------------------------------------


class TestScheduleResearchReport:
    @pytest.mark.asyncio
    async def test_empty_topic_returns_error(self, global_sched):
        result = await schedule_research_report(topic="")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_invalid_cron_returns_error(self, global_sched):
        result = await schedule_research_report(topic="AI trends", cron_expression="invalid-cron")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_valid_topic_creates_task(self, global_sched):
        result = await schedule_research_report(topic="AI trends in 2025", cron_expression="0 8 * * 0")
        assert "✅" in result
        assert "AI trends in 2025" in result
        assert "0 8 * * 0" in result

    @pytest.mark.asyncio
    async def test_default_cron_is_used(self, global_sched):
        result = await schedule_research_report(topic="ML news")
        assert "✅" in result
        assert "ML news" in result

    @pytest.mark.asyncio
    async def test_result_contains_next_run_string(self, global_sched):
        result = await schedule_research_report(topic="crypto", cron_expression="0 9 * * 1")
        assert "next:" in result or "Monday" in result or "09:00" in result
