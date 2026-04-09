"""
Tests for the retry queue added to TaskScheduler.

Covers:
1. Successful task → nothing added to retry queue
2. Failed task → added to retry queue with attempts_left=3
3. Retry succeeds on 2nd attempt → removed from queue
4. All 3 retries fail → CRITICAL logged, removed from queue
5. Backoff timing: next_retry_at increases correctly
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scheduler as scheduler_module
from scheduler import _RETRY_DELAYS, TaskScheduler, _RetryTask

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sched(tmp_path):
    """Fresh TaskScheduler backed by a temp file (no global state)."""
    temp_file = tmp_path / "schedules.json"
    with patch.object(scheduler_module, "SCHEDULE_FILE", temp_file):
        yield TaskScheduler()


def _make_retry_task(fn, label="test/task", attempts_left=3, delay=0.0):
    """Helper: create a _RetryTask whose window has already elapsed (delay=0)."""
    return _RetryTask(
        fn=fn,
        label=label,
        attempts_left=attempts_left,
        next_retry_at=time.monotonic() - 1 + delay,  # -1 so it's due immediately
    )


# ---------------------------------------------------------------------------
# 1. Successful _execute_task → retry queue stays empty
# ---------------------------------------------------------------------------


class TestNoRetryOnSuccess:
    @pytest.mark.asyncio
    async def test_success_leaves_retry_queue_empty(self, sched, tmp_path):
        """A skill that succeeds must not populate the retry queue."""
        skill_mock = AsyncMock(return_value="all good")
        sched.register_skills({"my_skill": skill_mock})
        task = sched.create("my_skill", {}, hour=3, minute=0)

        with (
            patch("metrics_collector.get_collector", return_value=MagicMock()),
            patch("trace_context.trace_context", MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            await sched._execute_task(task)

        assert sched._retry_queue == []


# ---------------------------------------------------------------------------
# 2. Failed task → queued with attempts_left=3
# ---------------------------------------------------------------------------


class TestRetryEnqueueOnFailure:
    @pytest.mark.asyncio
    async def test_failed_skill_enqueues_retry(self, sched):
        """A skill that raises must be added to _retry_queue with attempts_left=3."""
        skill_mock = AsyncMock(side_effect=RuntimeError("boom"))
        sched.register_skills({"bad_skill": skill_mock})
        task = sched.create("bad_skill", {}, hour=3, minute=0)

        with (
            patch("metrics_collector.get_collector", return_value=MagicMock()),
            patch("trace_context.trace_context", MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            await sched._execute_task(task)

        assert len(sched._retry_queue) == 1
        queued = sched._retry_queue[0]
        assert queued.attempts_left == 3
        assert queued.label == f"{task.task_id}/bad_skill"

    @pytest.mark.asyncio
    async def test_failed_skill_next_retry_at_set_to_first_delay(self, sched):
        """next_retry_at must be approximately now + _RETRY_DELAYS[0] (5 min)."""
        skill_mock = AsyncMock(side_effect=RuntimeError("boom"))
        sched.register_skills({"bad_skill": skill_mock})
        task = sched.create("bad_skill", {}, hour=3, minute=0)

        before = time.monotonic()
        with (
            patch("metrics_collector.get_collector", return_value=MagicMock()),
            patch("trace_context.trace_context", MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
        ):
            await sched._execute_task(task)
        after = time.monotonic()

        queued = sched._retry_queue[0]
        expected_lo = before + _RETRY_DELAYS[0]
        expected_hi = after + _RETRY_DELAYS[0]
        assert expected_lo <= queued.next_retry_at <= expected_hi


# ---------------------------------------------------------------------------
# 3. Retry succeeds on 2nd attempt → removed from queue
# ---------------------------------------------------------------------------


class TestRetrySucceedsOnSecondAttempt:
    @pytest.mark.asyncio
    async def test_retry_success_removes_task_from_queue(self, sched):
        """A retry fn that succeeds must be drained from the queue."""
        success_fn = AsyncMock()
        retry_task = _make_retry_task(fn=success_fn, label="test/flaky")
        sched._retry_queue.append(retry_task)

        await sched._process_retry_queue()

        assert sched._retry_queue == []
        success_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_success_after_initial_failure(self, sched):
        """Task placed in queue after failure is removed when retry call succeeds."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            # fails on first retry attempt, succeeds on second
            if call_count == 1:
                raise RuntimeError("first retry fails")

        # Simulate: initial attempt failed → in queue with attempts_left=3
        # First queue drain: fails → attempts_left=2, stays in queue
        retry_task = _RetryTask(
            fn=flaky,
            label="test/flaky",
            attempts_left=3,
            next_retry_at=time.monotonic() - 1,
        )
        sched._retry_queue.append(retry_task)

        await sched._process_retry_queue()
        assert len(sched._retry_queue) == 1  # still in queue after first retry failure
        assert sched._retry_queue[0].attempts_left == 2

        # Force window to elapse, second drain: succeeds → removed
        sched._retry_queue[0].next_retry_at = time.monotonic() - 1
        await sched._process_retry_queue()

        assert sched._retry_queue == []
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_success_logs_info(self, sched, caplog):
        """Successful retry must emit an INFO log."""
        import logging

        success_fn = AsyncMock()
        retry_task = _make_retry_task(fn=success_fn, label="test/success")
        sched._retry_queue.append(retry_task)

        with caplog.at_level(logging.INFO, logger="openclaw.scheduler"):
            await sched._process_retry_queue()

        assert any("Retry succeeded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. All 3 retries fail → CRITICAL logged, removed from queue
# ---------------------------------------------------------------------------


class TestAllRetriesExhausted:
    @pytest.mark.asyncio
    async def test_exhausted_retries_drops_task(self, sched):
        """After attempts_left reaches 0, the task must be removed from the queue."""
        always_fail = AsyncMock(side_effect=RuntimeError("always fails"))
        retry_task = _make_retry_task(fn=always_fail, label="test/exhaust", attempts_left=1)
        sched._retry_queue.append(retry_task)

        await sched._process_retry_queue()

        assert sched._retry_queue == []

    @pytest.mark.asyncio
    async def test_exhausted_retries_logs_critical(self, sched, caplog):
        """After exhausting all retries, a CRITICAL must be logged."""
        import logging

        always_fail = AsyncMock(side_effect=RuntimeError("fatal"))
        retry_task = _make_retry_task(fn=always_fail, label="test/exhaust", attempts_left=1)
        sched._retry_queue.append(retry_task)

        with caplog.at_level(logging.CRITICAL, logger="openclaw.scheduler"):
            await sched._process_retry_queue()

        assert any(r.levelname == "CRITICAL" for r in caplog.records)
        assert any("test/exhaust" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_full_three_retry_cycle_ends_with_empty_queue(self, sched):
        """Simulate a task that fails all 3 retries across three queue drains."""
        always_fail = AsyncMock(side_effect=RuntimeError("always fails"))
        # Start with attempts_left=3 and make the window already elapsed
        retry_task = _RetryTask(
            fn=always_fail,
            label="test/three-retries",
            attempts_left=3,
            next_retry_at=time.monotonic() - 1,
        )
        sched._retry_queue.append(retry_task)

        # Drain 1: fails, attempts_left → 2
        await sched._process_retry_queue()
        assert len(sched._retry_queue) == 1
        assert sched._retry_queue[0].attempts_left == 2

        # Force window to elapse for next drain
        sched._retry_queue[0].next_retry_at = time.monotonic() - 1

        # Drain 2: fails, attempts_left → 1
        await sched._process_retry_queue()
        assert len(sched._retry_queue) == 1
        assert sched._retry_queue[0].attempts_left == 1

        sched._retry_queue[0].next_retry_at = time.monotonic() - 1

        # Drain 3: fails, attempts_left → 0 → dropped
        await sched._process_retry_queue()
        assert sched._retry_queue == []


# ---------------------------------------------------------------------------
# 5. Backoff timing increases correctly across retries
# ---------------------------------------------------------------------------


class TestBackoffTiming:
    @pytest.mark.asyncio
    async def test_backoff_delay_increases_on_each_failure(self, sched):
        """next_retry_at should grow: delay[0] → delay[1] → delay[2] on successive failures."""
        always_fail = AsyncMock(side_effect=RuntimeError("nope"))
        retry_task = _RetryTask(
            fn=always_fail,
            label="test/backoff",
            attempts_left=3,
            next_retry_at=time.monotonic() - 1,
        )
        sched._retry_queue.append(retry_task)

        # First failure: attempts_left 3→2, delay_idx=1 → _RETRY_DELAYS[1]
        before_1 = time.monotonic()
        await sched._process_retry_queue()
        after_1 = time.monotonic()
        assert len(sched._retry_queue) == 1
        task_after_1 = sched._retry_queue[0]
        assert task_after_1.attempts_left == 2
        assert before_1 + _RETRY_DELAYS[1] <= task_after_1.next_retry_at <= after_1 + _RETRY_DELAYS[1]

        # Force window to elapse
        sched._retry_queue[0].next_retry_at = time.monotonic() - 1

        # Second failure: attempts_left 2→1, delay_idx=2 → _RETRY_DELAYS[2]
        before_2 = time.monotonic()
        await sched._process_retry_queue()
        after_2 = time.monotonic()
        assert len(sched._retry_queue) == 1
        task_after_2 = sched._retry_queue[0]
        assert task_after_2.attempts_left == 1
        assert before_2 + _RETRY_DELAYS[2] <= task_after_2.next_retry_at <= after_2 + _RETRY_DELAYS[2]

    @pytest.mark.asyncio
    async def test_not_due_task_stays_in_queue_untouched(self, sched):
        """A task whose next_retry_at is in the future must not be executed."""
        call_count = 0

        async def track():
            nonlocal call_count
            call_count += 1

        retry_task = _RetryTask(
            fn=track,
            label="test/future",
            attempts_left=3,
            next_retry_at=time.monotonic() + 9999,  # far future
        )
        sched._retry_queue.append(retry_task)

        await sched._process_retry_queue()

        assert call_count == 0
        assert len(sched._retry_queue) == 1
