"""
OpenClaw Scheduler — Phase 5: Scheduled Task System
Lightweight in-memory task scheduler using asyncio.
Tasks persist across restarts via a JSON file.
"""

import asyncio
import datetime
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Awaitable, Optional

log = logging.getLogger("openclaw.scheduler")

SCHEDULE_FILE = Path(os.getenv("MEMORY_DIR", "/memory")) / "schedules.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    """A single scheduled task."""

    task_id: str
    action: str          # skill name to invoke
    args: dict           # arguments for the skill
    cron_hour: int       # hour (0-23) to run, or -1 for interval-based
    cron_minute: int     # minute (0-59)
    interval_minutes: int = 0   # if > 0, run every N minutes instead of daily
    enabled: bool = True
    created_by: str = ""
    created_at: str = ""
    last_run: str = ""
    last_result: str = ""
    run_count: int = 0

    @property
    def next_run_str(self) -> str:
        """Human-readable next run time."""
        now = datetime.datetime.now()
        if self.interval_minutes > 0:
            if self.last_run:
                try:
                    last = datetime.datetime.fromisoformat(self.last_run)
                    next_run = last + datetime.timedelta(minutes=self.interval_minutes)
                    if next_run < now:
                        return "overdue"
                    delta = next_run - now
                    return f"in {int(delta.total_seconds() // 60)}m"
                except ValueError:
                    pass
            return "soon"
        # Daily schedule
        target = now.replace(hour=self.cron_hour, minute=self.cron_minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        delta = target - now
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"in {hours}h {minutes}m"


# ---------------------------------------------------------------------------
# Scheduler store
# ---------------------------------------------------------------------------


class TaskScheduler:
    """Manages scheduled tasks with persistence."""

    def __init__(self):
        self._tasks: dict[str, ScheduledTask] = {}
        self._counter = 0
        self._skill_registry: dict[str, Callable[..., Awaitable[str]]] = {}
        self._runner_task: asyncio.Task | None = None
        self._running_tasks: set[str] = set()  # task IDs currently executing
        self._load()

    # -- Persistence --

    def _load(self):
        """Load tasks from disk."""
        if SCHEDULE_FILE.exists():
            try:
                data = json.loads(SCHEDULE_FILE.read_text())
                for item in data:
                    task = ScheduledTask(**item)
                    self._tasks[task.task_id] = task
                    num = int(task.task_id.replace("sched-", ""))
                    self._counter = max(self._counter, num)
                log.info("Loaded %d scheduled tasks", len(self._tasks))
            except Exception as e:
                log.error(
                    "Failed to load schedules (file may be corrupted — manual recovery needed): %s",
                    e,
                )
                # Do NOT overwrite the corrupted file; leave _tasks empty until fixed

    def _save(self):
        """Persist tasks to disk."""
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(t) for t in self._tasks.values()]
        SCHEDULE_FILE.write_text(json.dumps(data, indent=2))

    # -- CRUD --

    def register_skills(self, skills: dict[str, Callable[..., Awaitable[str]]]):
        """Register callable skills for the scheduler to invoke."""
        self._skill_registry.update(skills)

    def create(
        self,
        action: str,
        args: dict | None = None,
        hour: int = -1,
        minute: int = 0,
        interval_minutes: int = 0,
        created_by: str = "",
    ) -> ScheduledTask:
        """Create a new scheduled task."""
        self._counter += 1
        task_id = f"sched-{self._counter}"
        task = ScheduledTask(
            task_id=task_id,
            action=action,
            args=args or {},
            cron_hour=hour,
            cron_minute=minute,
            interval_minutes=interval_minutes,
            created_by=created_by,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._tasks[task_id] = task
        self._save()
        log.info("Created scheduled task %s: %s", task_id, action)
        return task

    def remove(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            return True
        return False

    def toggle(self, task_id: str) -> Optional[bool]:
        """Toggle a task's enabled state. Returns new state or None."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.enabled = not task.enabled
        self._save()
        return task.enabled

    def list_tasks(self) -> list[ScheduledTask]:
        """Return all tasks sorted by ID."""
        return sorted(self._tasks.values(), key=lambda t: t.task_id)

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        return self._tasks.get(task_id)

    # -- Runner --

    def start(self):
        """Start the background scheduler loop."""
        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run_loop())
            log.info("Scheduler started")

    async def _run_loop(self):
        """Check tasks every 60 seconds and execute due ones."""
        while True:
            try:
                await self._check_and_run()
            except Exception as e:
                log.error("Scheduler loop error: %s", e)
            await asyncio.sleep(60)

    async def _check_and_run(self):
        """Execute any due tasks."""
        now = datetime.datetime.now()
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if self._is_due(task, now):
                await self._execute_task(task)

    def _is_due(self, task: ScheduledTask, now: datetime.datetime) -> bool:
        """Determine if a task should run now."""
        if task.interval_minutes > 0:
            if not task.last_run:
                return True
            try:
                last = datetime.datetime.fromisoformat(task.last_run)
                return (now - last).total_seconds() >= task.interval_minutes * 60
            except ValueError:
                return True

        # Daily cron: match hour and minute (within the 60s check window)
        return (
            now.hour == task.cron_hour
            and now.minute == task.cron_minute
            and (not task.last_run or task.last_run[:10] != now.date().isoformat())
        )

    async def _execute_task(self, task: ScheduledTask):
        """Execute a scheduled task, guarded against concurrent duplicate runs."""
        if task.task_id in self._running_tasks:
            log.debug("Task %s already running, skipping duplicate execution", task.task_id)
            return

        self._running_tasks.add(task.task_id)
        skill_fn = self._skill_registry.get(task.action)
        if skill_fn is None:
            task.last_result = f"Unknown skill: {task.action}"
            task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save()
            self._running_tasks.discard(task.task_id)
            return

        log.info("Executing scheduled task %s: %s(%s)", task.task_id, task.action, task.args)
        try:
            result = await asyncio.wait_for(skill_fn(**task.args), timeout=300)
            task.last_result = result[:500] if result else "OK"
        except asyncio.TimeoutError:
            task.last_result = "Error: Task timed out after 5 minutes"
            log.error("Scheduled task %s timed out", task.task_id)
        except Exception as e:
            task.last_result = f"Error: {e}"
            log.error("Scheduled task %s failed: %s", task.task_id, e)
        finally:
            self._running_tasks.discard(task.task_id)

        task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task.run_count += 1
        self._save()


# Global instance
scheduler = TaskScheduler()
