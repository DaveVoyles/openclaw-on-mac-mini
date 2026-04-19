"""
bg_tasks — Background task lifecycle management.

Concerns: launching, supervising, restarting, and stopping all background asyncio
tasks. Also owns the shared module-level task registry dicts.
"""

import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from bg_briefing import evening_digest_loop, morning_briefing_loop  # noqa: F401
from bg_healing import audit_writer_loop, background_cleanup_loop, proactive_insight_loop  # noqa: F401
from bg_monitoring import container_health_loop, error_monitor_loop, resource_monitor_loop  # noqa: F401
from metrics_collector import get_collector
from trace_context import trace_context

log = logging.getLogger("openclaw")

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}
_BACKGROUND_FACTORIES: dict[str, Callable[[], Awaitable[None]]] = {}
_BACKGROUND_STOPPING = False
_BACKGROUND_RESTART_DELAY_SECONDS: int = 5  # initial restart delay before exponential backoff

# --- managed_task: lightweight fire-and-forget wrapper ---

_active_tasks: set[asyncio.Task] = set()


def managed_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str = "managed",
    timeout: float | None = 300.0,
    error_callback: Any = None,
) -> asyncio.Task:
    """Create a tracked asyncio task with timeout and error logging.

    Prevents fire-and-forget tasks from silently failing. All exceptions
    are logged. Tasks are tracked in _active_tasks and removed on completion.

    Args:
        coro: The coroutine to run as a task.
        name: Human-readable name for logging.
        timeout: Seconds before task is cancelled (None = unlimited).
        error_callback: Optional callable(exc) called on task failure.
    """
    async def _wrapped() -> None:
        try:
            if timeout is not None:
                await asyncio.wait_for(coro, timeout=timeout)
            else:
                await coro
        except asyncio.TimeoutError:
            log.warning("managed_task '%s' timed out after %.0fs", name, timeout)
        except asyncio.CancelledError:
            log.debug("managed_task '%s' cancelled", name)
            raise
        except Exception as exc:
            log.error("managed_task '%s' failed: %s", name, exc, exc_info=True)
            if error_callback is not None:
                try:
                    error_callback(exc)
                except Exception:
                    pass

    task = asyncio.create_task(_wrapped(), name=name)
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)
    return task


def get_active_task_count() -> int:
    """Return number of currently running managed tasks."""
    return len(_active_tasks)


class _BackoffTracker:
    """Per-task exponential backoff tracker for background task restarts."""

    DELAYS = [5, 15, 60, 300]  # 5 s, 15 s, 1 min, 5 min

    def __init__(self):
        self._attempt = 0
        self._clean_start: float | None = None

    def next_delay(self) -> int:
        delay = self.DELAYS[min(self._attempt, len(self.DELAYS) - 1)]
        self._attempt += 1
        return delay

    def mark_clean(self):
        """Call when the task runs cleanly; resets after 30 min of clean operation."""
        now = time.monotonic()
        if self._clean_start is None:
            self._clean_start = now
        elif now - self._clean_start > 1800:  # 30 minutes
            self._attempt = 0
            self._clean_start = None


_BACKGROUND_BACKOFF: dict[str, _BackoffTracker] = {}


def _build_background_task_factories(bot) -> dict[str, Callable[[], Awaitable[None]]]:
    _me = sys.modules[__name__]  # resolve via module dict so monkeypatches work
    factories: dict[str, Callable[[], Awaitable[None]]] = {
        "background_cleanup": _me.background_cleanup_loop,
        "audit_writer": _me.audit_writer_loop,
        "reminder": lambda: _me.reminder_loop(bot),
    }
    if ALERT_CHANNEL_ID:
        factories.update({
            "morning_briefing": lambda: _me.morning_briefing_loop(bot),
            "evening_digest": lambda: _me.evening_digest_loop(bot),
            "proactive_insight": lambda: _me.proactive_insight_loop(bot),
            "error_monitor": lambda: _me.error_monitor_loop(bot),
            "container_health": lambda: _me.container_health_loop(bot),
            "resource_monitor": lambda: _me.resource_monitor_loop(bot),
        })
    return factories


def _handle_background_task_done(task_name: str, task: asyncio.Task) -> None:
    if _BACKGROUND_STOPPING:
        return
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return

    if error:
        delay = _BACKGROUND_BACKOFF.setdefault(task_name, _BackoffTracker()).next_delay()
        log.warning(
            "Background task %s crashed: %s; restarting in %ss",
            task_name,
            error,
            delay,
        )
    else:
        delay = _BACKGROUND_BACKOFF.setdefault(task_name, _BackoffTracker()).next_delay()
        log.warning(
            "Background task %s exited unexpectedly; restarting in %ss",
            task_name,
            delay,
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_later(delay, _restart_background_task, task_name)


def _launch_background_task(task_name: str, task_factory: Callable[[], Awaitable[None]]) -> None:
    _BACKGROUND_FACTORIES[task_name] = task_factory
    task = asyncio.create_task(
        _run_supervised_background_task(task_name, task_factory),
        name=f"openclaw.background.{task_name}",
    )
    _BACKGROUND_TASKS[task_name] = task
    task.add_done_callback(lambda done, name=task_name: _handle_background_task_done(name, done))


async def _run_supervised_background_task(
    task_name: str,
    task_factory: Callable[[], Awaitable[None]],
) -> None:
    start = time.monotonic()
    success = True
    error_type: str | None = None
    cancelled = False

    try:
        with trace_context(command=f"background:{task_name}", user_id=0, channel_id=ALERT_CHANNEL_ID, component="background"):
            await task_factory()
        # Task completed cleanly — notify backoff tracker
        _BACKGROUND_BACKOFF.setdefault(task_name, _BackoffTracker()).mark_clean()
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:  # broad: intentional
        success = False
        error_type = type(exc).__name__
        raise
    finally:
        if not (cancelled and _BACKGROUND_STOPPING):
            get_collector().record_command(
                command=f"background:{task_name}",
                user="system",
                workspace="background",
                duration=max(0.0, time.monotonic() - start),
                success=success,
                error_type=error_type,
            )


def _restart_background_task(task_name: str) -> None:
    if _BACKGROUND_STOPPING:
        return
    current = _BACKGROUND_TASKS.get(task_name)
    if current and not current.done():
        return
    task_factory = _BACKGROUND_FACTORIES.get(task_name)
    if task_factory is None:
        return
    _launch_background_task(task_name, task_factory)


def start_background_tasks(bot) -> int:
    """Create all background asyncio tasks. Called from OpenClawBot.on_ready."""
    global _BACKGROUND_STOPPING

    if any(not task.done() for task in _BACKGROUND_TASKS.values()):
        log.info("Background tasks already running (%d active)", len(_BACKGROUND_TASKS))
        return len(_BACKGROUND_TASKS)

    _BACKGROUND_STOPPING = False
    _BACKGROUND_TASKS.clear()
    _BACKGROUND_FACTORIES.clear()
    _BACKGROUND_BACKOFF.clear()

    for task_name, task_factory in _build_background_task_factories(bot).items():
        _launch_background_task(task_name, task_factory)

    if ALERT_CHANNEL_ID:
        log.info("Proactive tasks started (alert channel: %d)", ALERT_CHANNEL_ID)
    else:
        log.info("ALERT_CHANNEL_ID not set — proactive push notifications disabled")
    log.info("Background task supervisor started (%d loops)", len(_BACKGROUND_TASKS))
    return len(_BACKGROUND_TASKS)


async def stop_background_tasks() -> None:
    """Cancel and await all supervised background tasks."""
    global _BACKGROUND_STOPPING

    if not _BACKGROUND_TASKS:
        return

    _BACKGROUND_STOPPING = True
    tasks = list(_BACKGROUND_TASKS.items())
    for _, task in tasks:
        task.cancel()

    results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
    for (task_name, _), result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            log.debug("Background task %s stopped with error: %s", task_name, result)

    _BACKGROUND_TASKS.clear()
    _BACKGROUND_FACTORIES.clear()
    _BACKGROUND_BACKOFF.clear()
    log.info("Background task supervisor stopped")


async def reminder_loop(bot):
    """Check for due reminders every 15 seconds and DM users."""
    import discord

    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            from reminder_manager import reminder_manager

            due = reminder_manager.get_due()
            for r in due:
                try:
                    user = await bot.fetch_user(r.user_id)
                    embed = discord.Embed(
                        title="⏰ Reminder",
                        description=r.message,
                        color=discord.Color.gold(),
                    )
                    recur = f" (🔁 {r.recurring})" if r.recurring else ""
                    embed.set_footer(text=f"ID: {r.id}{recur}")
                    await user.send(embed=embed)
                except Exception as e:  # broad: intentional — mark_fired must run even if send fails
                    log.debug("Failed to send reminder %s: %s", r.id, e)
                reminder_manager.mark_fired(r.id)
        except Exception as e:  # broad: intentional
            log.debug("Reminder loop error: %s", e)
        await asyncio.sleep(15)
