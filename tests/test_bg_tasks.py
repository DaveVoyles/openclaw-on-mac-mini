"""Tests for bg_tasks.py — background task lifecycle management."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bg_tasks

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _reset_bg_tasks():
    """Cancel and clear all background tasks before and after each test."""
    for task in list(bg_tasks._BACKGROUND_TASKS.values()):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    bg_tasks._BACKGROUND_TASKS.clear()
    bg_tasks._BACKGROUND_FACTORIES.clear()
    bg_tasks._BACKGROUND_STOPPING = False

    yield

    for task in list(bg_tasks._BACKGROUND_TASKS.values()):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    bg_tasks._BACKGROUND_TASKS.clear()
    bg_tasks._BACKGROUND_FACTORIES.clear()
    bg_tasks._BACKGROUND_STOPPING = False


async def _idle_loop(*args, **kwargs):
    """A coroutine that runs forever until cancelled."""
    while True:
        await asyncio.sleep(3600)


def _make_idle_bot():
    bot = MagicMock()
    bot.is_closed = MagicMock(return_value=False)
    bot.wait_until_ready = AsyncMock()
    bot.fetch_user = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# _build_background_task_factories
# ---------------------------------------------------------------------------


class TestBuildBackgroundTaskFactories:
    def test_without_alert_channel_id(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        factories = bg_tasks._build_background_task_factories(_make_idle_bot())
        assert "background_cleanup" in factories
        assert "audit_writer" in factories
        assert "reminder" in factories
        assert len(factories) == 3

    def test_with_alert_channel_id(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 123)
        factories = bg_tasks._build_background_task_factories(_make_idle_bot())
        assert "background_cleanup" in factories
        assert "audit_writer" in factories
        assert "reminder" in factories
        assert "morning_briefing" in factories
        assert "evening_digest" in factories
        assert "proactive_insight" in factories
        assert "error_monitor" in factories
        assert "container_health" in factories
        assert "resource_monitor" in factories
        assert len(factories) == 9

    def test_factories_are_callable(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        factories = bg_tasks._build_background_task_factories(_make_idle_bot())
        for name, factory in factories.items():
            assert callable(factory), f"{name} factory is not callable"

    def test_factories_resolve_via_module_dict(self, monkeypatch):
        """Factories should resolve loop functions via sys.modules so monkeypatches work."""
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)

        factories = bg_tasks._build_background_task_factories(_make_idle_bot())
        # The cleanup factory should be the patched idle_loop
        assert factories["background_cleanup"] is _idle_loop


# ---------------------------------------------------------------------------
# start_background_tasks
# ---------------------------------------------------------------------------


class TestStartBackgroundTasks:
    @pytest.mark.asyncio
    async def test_returns_task_count_without_alert(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        count = bg_tasks.start_background_tasks(_make_idle_bot())
        assert count == 3

    @pytest.mark.asyncio
    async def test_returns_task_count_with_alert(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "morning_briefing_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "evening_digest_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "proactive_insight_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "error_monitor_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "container_health_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "resource_monitor_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        count = bg_tasks.start_background_tasks(_make_idle_bot())
        assert count == 9

    @pytest.mark.asyncio
    async def test_idempotent_when_tasks_running(self, monkeypatch):
        """Calling start_background_tasks twice returns same tasks."""
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        bot = _make_idle_bot()
        first_count = bg_tasks.start_background_tasks(bot)
        first_task_ids = {name: id(task) for name, task in bg_tasks._BACKGROUND_TASKS.items()}

        second_count = bg_tasks.start_background_tasks(bot)
        second_task_ids = {name: id(task) for name, task in bg_tasks._BACKGROUND_TASKS.items()}

        assert first_count == second_count == 3
        assert first_task_ids == second_task_ids

    @pytest.mark.asyncio
    async def test_tasks_are_created_in_registry(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        bg_tasks.start_background_tasks(_make_idle_bot())

        assert "background_cleanup" in bg_tasks._BACKGROUND_TASKS
        assert "audit_writer" in bg_tasks._BACKGROUND_TASKS
        assert "reminder" in bg_tasks._BACKGROUND_TASKS


# ---------------------------------------------------------------------------
# stop_background_tasks
# ---------------------------------------------------------------------------


class TestStopBackgroundTasks:
    @pytest.mark.asyncio
    async def test_cancels_all_tasks(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        bg_tasks.start_background_tasks(_make_idle_bot())
        assert len(bg_tasks._BACKGROUND_TASKS) == 3

        await bg_tasks.stop_background_tasks()

        assert len(bg_tasks._BACKGROUND_TASKS) == 0
        assert bg_tasks._BACKGROUND_STOPPING is True

    @pytest.mark.asyncio
    async def test_stop_when_no_tasks_is_noop(self):
        """Calling stop with no tasks doesn't error."""
        assert len(bg_tasks._BACKGROUND_TASKS) == 0
        await bg_tasks.stop_background_tasks()  # should not raise

    @pytest.mark.asyncio
    async def test_clears_factories_registry(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        bg_tasks.start_background_tasks(_make_idle_bot())
        await bg_tasks.stop_background_tasks()

        assert len(bg_tasks._BACKGROUND_FACTORIES) == 0

    @pytest.mark.asyncio
    async def test_sets_stopping_flag(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "background_cleanup_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "audit_writer_loop", _idle_loop)
        monkeypatch.setattr(bg_tasks, "reminder_loop", _idle_loop)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)

        bg_tasks.start_background_tasks(_make_idle_bot())
        await bg_tasks.stop_background_tasks()

        assert bg_tasks._BACKGROUND_STOPPING is True


# ---------------------------------------------------------------------------
# _handle_background_task_done
# ---------------------------------------------------------------------------


class TestHandleBackgroundTaskDone:
    def test_stopping_flag_prevents_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", True)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None

        with patch.object(bg_tasks, "_restart_background_task") as mock_restart:
            bg_tasks._handle_background_task_done("test_task", task)

        mock_restart.assert_not_called()

    def test_cancelled_task_no_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = True

        with patch.object(bg_tasks, "_restart_background_task") as mock_restart:
            bg_tasks._handle_background_task_done("test_task", task)

        mock_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_task_schedules_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")

        restart_called = asyncio.Event()

        def fake_restart(name):
            restart_called.set()

        with patch.object(bg_tasks, "_restart_background_task", side_effect=fake_restart):
            bg_tasks._handle_background_task_done("test_task", task)
            await asyncio.wait_for(restart_called.wait(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_clean_exit_schedules_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None

        restart_called = asyncio.Event()

        def fake_restart(name):
            restart_called.set()

        with patch.object(bg_tasks, "_restart_background_task", side_effect=fake_restart):
            bg_tasks._handle_background_task_done("test_task", task)
            await asyncio.wait_for(restart_called.wait(), timeout=1.0)

    def test_cancelled_error_from_exception_no_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.side_effect = asyncio.CancelledError()

        with patch.object(bg_tasks, "_restart_background_task") as mock_restart:
            bg_tasks._handle_background_task_done("test_task", task)

        mock_restart.assert_not_called()


# ---------------------------------------------------------------------------
# _restart_background_task
# ---------------------------------------------------------------------------


class TestRestartBackgroundTask:
    def test_stopping_flag_prevents_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", True)
        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("nonexistent")
        mock_launch.assert_not_called()

    def test_task_not_done_prevents_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        running_task = MagicMock(spec=asyncio.Task)
        running_task.done.return_value = False
        bg_tasks._BACKGROUND_TASKS["live_task"] = running_task
        bg_tasks._BACKGROUND_FACTORIES["live_task"] = _idle_loop

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("live_task")
        mock_launch.assert_not_called()

    def test_factory_not_found_prevents_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("nonexistent_task")
        mock_launch.assert_not_called()

    def test_successful_restart(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        bg_tasks._BACKGROUND_TASKS["dead_task"] = done_task
        bg_tasks._BACKGROUND_FACTORIES["dead_task"] = _idle_loop

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("dead_task")
        mock_launch.assert_called_once_with("dead_task", _idle_loop)

    def test_no_current_task_restarts(self, monkeypatch):
        """If task not in registry at all, restart happens if factory exists."""
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        bg_tasks._BACKGROUND_FACTORIES["orphan_task"] = _idle_loop

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("orphan_task")
        mock_launch.assert_called_once_with("orphan_task", _idle_loop)


# ---------------------------------------------------------------------------
# _run_supervised_background_task
# ---------------------------------------------------------------------------


class TestRunSupervisedBackgroundTask:
    @pytest.mark.asyncio
    async def test_success_records_metrics(self, monkeypatch):
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)

        async def quick_task():
            pass

        await bg_tasks._run_supervised_background_task("test_task", quick_task)

        mock_collector.record_command.assert_called_once()
        call_kwargs = mock_collector.record_command.call_args.kwargs
        assert call_kwargs["command"] == "background:test_task"
        assert call_kwargs["success"] is True
        assert call_kwargs["user"] == "system"

    @pytest.mark.asyncio
    async def test_exception_records_failure_metrics(self, monkeypatch):
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)

        async def failing_task():
            raise RuntimeError("task failed")

        with pytest.raises(RuntimeError):
            await bg_tasks._run_supervised_background_task("failing_task", failing_task)

        mock_collector.record_command.assert_called_once()
        call_kwargs = mock_collector.record_command.call_args.kwargs
        assert call_kwargs["success"] is False
        assert call_kwargs["error_type"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_cancellation_re_raises(self, monkeypatch):
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)

        async def cancelled_task():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await bg_tasks._run_supervised_background_task("cancelled_task", cancelled_task)

    @pytest.mark.asyncio
    async def test_cancellation_during_stop_skips_metrics(self, monkeypatch):
        """When stopping and task is cancelled, metrics should be skipped."""
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", True)

        async def cancelled_task():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await bg_tasks._run_supervised_background_task("cancelled_task", cancelled_task)

        mock_collector.record_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_duration_is_positive(self, monkeypatch):
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_tasks, "get_collector", lambda: mock_collector)
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)

        async def slow_task():
            await asyncio.sleep(0)

        await bg_tasks._run_supervised_background_task("slow_task", slow_task)

        call_kwargs = mock_collector.record_command.call_args.kwargs
        assert call_kwargs["duration"] >= 0.0


# ---------------------------------------------------------------------------
# reminder_loop
# ---------------------------------------------------------------------------


class TestReminderLoop:
    @pytest.mark.asyncio
    async def test_sends_due_reminders(self, monkeypatch):
        """Due reminders are sent to users via DM."""
        bot = _make_idle_bot()
        bot.is_closed = MagicMock(side_effect=[False, True])

        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        bot.fetch_user = AsyncMock(return_value=mock_user)

        reminder = MagicMock()
        reminder.user_id = 12345
        reminder.message = "Take a break"
        reminder.id = "r1"
        reminder.recurring = None
        reminder.fire_at = 1000000

        mock_rm = MagicMock()
        mock_rm.get_due = MagicMock(return_value=[reminder])
        mock_rm.mark_fired = MagicMock()
        mock_reminder_manager_mod = MagicMock(reminder_manager=mock_rm)

        with patch("bg_tasks.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"reminder_manager": mock_reminder_manager_mod}):
            await bg_tasks.reminder_loop(bot)

        bot.fetch_user.assert_awaited_with(12345)
        mock_user.send.assert_awaited_once()
        mock_rm.mark_fired.assert_called_with("r1")

    @pytest.mark.asyncio
    async def test_fetch_user_failure_still_marks_fired(self, monkeypatch):
        """Even if fetch_user fails, reminder is marked fired."""
        bot = _make_idle_bot()
        bot.is_closed = MagicMock(side_effect=[False, True])
        bot.fetch_user = AsyncMock(side_effect=Exception("user not found"))

        reminder = MagicMock()
        reminder.user_id = 99999
        reminder.message = "Test"
        reminder.id = "r2"
        reminder.recurring = None

        mock_rm = MagicMock()
        mock_rm.get_due = MagicMock(return_value=[reminder])
        mock_rm.mark_fired = MagicMock()
        mock_reminder_manager_mod = MagicMock(reminder_manager=mock_rm)

        with patch("bg_tasks.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"reminder_manager": mock_reminder_manager_mod}):
            await bg_tasks.reminder_loop(bot)

        mock_rm.mark_fired.assert_called_with("r2")

    @pytest.mark.asyncio
    async def test_no_due_reminders_no_fetch(self, monkeypatch):
        bot = _make_idle_bot()
        bot.is_closed = MagicMock(side_effect=[False, True])

        mock_rm = MagicMock()
        mock_rm.get_due = MagicMock(return_value=[])
        mock_reminder_manager_mod = MagicMock(reminder_manager=mock_rm)

        with patch("bg_tasks.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"reminder_manager": mock_reminder_manager_mod}):
            await bg_tasks.reminder_loop(bot)

        bot.fetch_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reminder_loop_exception_handled(self, monkeypatch):
        """Exception in the main loop body is caught and loop continues."""
        bot = _make_idle_bot()
        bot.is_closed = MagicMock(side_effect=[False, False, True])

        mock_rm = MagicMock()
        mock_rm.get_due = MagicMock(side_effect=Exception("db error"))
        mock_reminder_manager_mod = MagicMock(reminder_manager=mock_rm)

        with patch("bg_tasks.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"reminder_manager": mock_reminder_manager_mod}):
            await bg_tasks.reminder_loop(bot)


    async def test_recurring_reminder_shown_in_embed(self, monkeypatch):
        """Recurring reminder has recurrence in embed footer."""
        bot = _make_idle_bot()
        bot.is_closed = MagicMock(side_effect=[False, True])

        mock_user = AsyncMock()
        mock_user.send = AsyncMock()
        bot.fetch_user = AsyncMock(return_value=mock_user)

        reminder = MagicMock()
        reminder.user_id = 12345
        reminder.message = "Daily standup"
        reminder.id = "r3"
        reminder.recurring = "daily"
        reminder.fire_at = 1000000

        mock_rm = MagicMock()
        mock_rm.get_due = MagicMock(return_value=[reminder])
        mock_rm.mark_fired = MagicMock()
        mock_reminder_manager_mod = MagicMock(reminder_manager=mock_rm)

        sent_embeds = []

        async def capture_send(embed=None, **kwargs):
            if embed:
                sent_embeds.append(embed)

        mock_user.send = capture_send

        with patch("bg_tasks.asyncio.sleep", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"reminder_manager": mock_reminder_manager_mod}):
            await bg_tasks.reminder_loop(bot)

        assert sent_embeds
        assert "daily" in sent_embeds[0].footer.text
