"""
OpenClaw Scheduler — Phase 5: Scheduled Task System
Lightweight in-memory task scheduler using asyncio.
Tasks persist across restarts via a JSON file.
"""

import asyncio
import datetime


def _parse_utc(dt_str):
    dt = datetime.datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from utils import atomic_write

log = logging.getLogger(__name__)

SCHEDULE_FILE = Path(os.getenv("MEMORY_DIR", "/memory")) / "schedules.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


_ALERT_PATTERNS = ("error", "warn", "exception", "critical", "fatal", "❌", "⚠️", "failed", "unreachable", "timeout")

_RETRY_DELAYS = [300, 900, 2700]  # 5min → 15min → 45min exponential backoff


@dataclass
class _RetryTask:
    """A failed task queued for retry with exponential backoff."""

    fn: Callable       # zero-arg async callable that re-runs the operation
    label: str         # human-readable name, e.g. "sched-1/weekly_recap"
    attempts_left: int
    next_retry_at: float  # time.monotonic() value when next attempt is allowed


@dataclass
class ScheduledTask:
    """A single scheduled task."""

    task_id: str
    action: str          # skill name to invoke
    args: dict           # arguments for the skill
    cron_hour: int       # hour (0-23) to run, or -1 for interval-based
    cron_minute: int     # minute (0-59)
    interval_minutes: int = 0   # if > 0, run every N minutes instead of daily
    cron_expression: str = ""   # real cron syntax, e.g. "0 7 * * 1,5" (takes priority)
    prompt: str = ""            # if set, sends this prompt to LLM instead of calling a skill
    enabled: bool = True
    created_by: str = ""
    created_at: str = ""
    last_run: str = ""
    last_result: str = ""
    run_count: int = 0
    notify_channel_id: int = 0  # if set, post result to this Discord channel after each run
    alert_only: bool = True     # if True, only post when result contains alert keywords

    @property
    def next_run_str(self) -> str:
        """Human-readable next run time."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Cron expression takes priority
        if self.cron_expression:
            try:
                from croniter import croniter
                cron = croniter(self.cron_expression, now)
                next_dt = cron.get_next(datetime.datetime).astimezone(datetime.timezone.utc).astimezone(datetime.timezone.utc)
                return next_dt.strftime("%a %H:%M")
            except (ImportError, ValueError, TypeError):
                return self.cron_expression

        if self.interval_minutes > 0:
            if self.last_run:
                try:
                    last = _parse_utc(self.last_run)
                    next_run = last + datetime.timedelta(minutes=self.interval_minutes)
                    if next_run < now:
                        return "overdue"
                    delta = next_run - now
                    return f"in {int(delta.total_seconds() // 60)}m"
                except ValueError:
                    pass
            return "soon"
        # Daily schedule
        target = now.replace(hour=self.cron_hour, minute=self.cron_minute, second=0, microsecond=0, tzinfo=datetime.timezone.utc)
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
        self._running_lock = asyncio.Lock()  # protects _running_tasks
        self._retry_queue: list[_RetryTask] = []
        self._last_retry_check: float = 0.0
        # Optional async callback: (task_id, action, result, is_alert) -> None
        # Set by bot.py after startup to enable Discord notifications
        self.notify_callback: Optional[Callable[[str, str, str, bool], Awaitable[None]]] = None
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
                    try:
                        num = int(task.task_id.replace("sched-", ""))
                        self._counter = max(self._counter, num)
                    except ValueError:
                        log.warning("Non-standard task_id: %s", task.task_id)
                log.info("Loaded %d scheduled tasks", len(self._tasks))
            except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
                log.error(
                    "Failed to load schedules (file may be corrupted — manual recovery needed): %s",
                    e,
                )
                # Do NOT overwrite the corrupted file; leave _tasks empty until fixed

    def _save(self):
        """Persist tasks to disk atomically."""
        _JSON_SAFE = (str, int, float, bool, type(None))

        def _sanitize_args(args: dict) -> dict:
            """Strip non-JSON-serializable values so discord clients etc. don't break saves."""
            return {k: v for k, v in args.items() if isinstance(v, _JSON_SAFE)}

        data = []
        for t in self._tasks.values():
            d = asdict(t)
            d["args"] = _sanitize_args(d.get("args") or {})
            data.append(d)
        atomic_write(SCHEDULE_FILE, json.dumps(data, indent=2))

    # -- CRUD --

    def register_skills(self, skills: dict[str, Callable[..., Awaitable[str]]]) -> None:
        """Register callable skills for the scheduler to invoke."""
        self._skill_registry.update(skills)

    def create(
        self,
        action: str,
        args: dict | None = None,
        hour: int = -1,
        minute: int = 0,
        interval_minutes: int = 0,
        cron_expression: str = "",
        prompt: str = "",
        created_by: str = "",
        notify_channel_id: int = 0,
        alert_only: bool = True,
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
            cron_expression=cron_expression,
            prompt=prompt,
            created_by=created_by,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            notify_channel_id=notify_channel_id,
            alert_only=alert_only,
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

    def update(
        self,
        task_id: str,
        *,
        action: str | None = None,
        prompt: str | None = None,
        cron_expression: str | None = None,
        interval_minutes: int | None = None,
        cron_hour: int | None = None,
        cron_minute: int | None = None,
        enabled: bool | None = None,
    ) -> Optional[ScheduledTask]:
        """Update mutable task fields and persist the change."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if action is not None:
            task.action = action
        if prompt is not None:
            task.prompt = prompt
        if cron_expression is not None:
            task.cron_expression = cron_expression
        if interval_minutes is not None:
            task.interval_minutes = max(0, int(interval_minutes))
        if cron_hour is not None:
            task.cron_hour = int(cron_hour)
        if cron_minute is not None:
            task.cron_minute = int(cron_minute)
        if enabled is not None:
            task.enabled = bool(enabled)
        self._save()
        return task

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
            except Exception as e:  # broad: intentional — outer scheduler loop guard
                log.error("Scheduler loop error: %s", e)
            now_mono = time.monotonic()
            if now_mono - self._last_retry_check >= 300:  # every 5 minutes
                try:
                    await self._process_retry_queue()
                except Exception as e:  # broad: intentional — outer retry-queue guard
                    log.error("Retry queue processing error: %s", e)
                self._last_retry_check = now_mono
            await asyncio.sleep(60)

    async def _process_retry_queue(self) -> None:
        """Drain the retry queue, re-running tasks whose backoff window has elapsed."""
        if not self._retry_queue:
            return
        now = time.monotonic()
        still_pending: list[_RetryTask] = []
        for retry_task in self._retry_queue:
            if retry_task.next_retry_at > now:
                still_pending.append(retry_task)
                continue
            try:
                await retry_task.fn()
                log.info("Retry succeeded for %s", retry_task.label)
            except Exception as exc:  # broad: intentional — retry fn wraps arbitrary tasks
                retry_task.attempts_left -= 1
                if retry_task.attempts_left > 0:
                    delay_idx = 3 - retry_task.attempts_left
                    retry_task.next_retry_at = now + _RETRY_DELAYS[min(delay_idx, len(_RETRY_DELAYS) - 1)]
                    log.warning(
                        "Retry failed for %s (%d attempt(s) left): %s",
                        retry_task.label, retry_task.attempts_left, exc,
                    )
                    still_pending.append(retry_task)
                else:
                    log.critical(
                        "Task %s exhausted all retries — dropping. Last error: %s",
                        retry_task.label, exc,
                    )
        self._retry_queue[:] = still_pending

    async def _check_and_run(self) -> None:
        """Execute any due tasks."""
        now = datetime.datetime.now(datetime.timezone.utc)
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if self._is_due(task, now):
                await self._execute_task(task)

    def _is_due(self, task: ScheduledTask, now: datetime.datetime) -> bool:
        """Determine if a task should run now."""
        # Cron expression takes priority
        if task.cron_expression:
            try:
                from croniter import croniter
                cron = croniter(task.cron_expression, now - datetime.timedelta(minutes=2))
                next_run = cron.get_next(datetime.datetime)
                return abs((next_run - now).total_seconds()) < 120
            except (ImportError, ValueError, TypeError) as e:
                log.warning("Invalid cron expression '%s': %s", task.cron_expression, e)
                return False

        # Legacy: interval-based
        if task.interval_minutes > 0:
            if not task.last_run:
                return True
            try:
                last = _parse_utc(task.last_run)
                return (now - last).total_seconds() >= task.interval_minutes * 60
            except ValueError:
                return True

        # Legacy: daily cron — match hour and minute (within the 60s check window)
        return (
            now.hour == task.cron_hour
            and now.minute == task.cron_minute
            and (not task.last_run or task.last_run[:10] != now.date().isoformat())
        )

    async def _execute_task(self, task: ScheduledTask):
        """Execute a scheduled task, guarded against concurrent duplicate runs."""
        async with self._running_lock:
            if task.task_id in self._running_tasks:
                log.debug("Task %s already running, skipping duplicate execution", task.task_id)
                return
            self._running_tasks.add(task.task_id)

        # Prompt job — send to LLM with full tool access
        if task.prompt:
            from metrics_collector import get_collector
            from trace_context import trace_context

            with trace_context(command=task.action or "prompt-job", user_id=task.created_by or 0, channel_id=task.notify_channel_id or 0):
                log.info("Executing prompt job %s: %s", task.task_id, task.prompt[:80])
                start = time.time()
                success = True
                error_type = None
                try:
                    from llm import chat

                    response_text, _, model_used = await chat(
                        task.prompt,
                        model_preference="auto",
                    )
                    result = response_text or "No response from LLM"
                    task.last_result = str(result)[:500]
                except asyncio.TimeoutError:
                    task.last_result = "Error: Prompt job timed out"
                    log.error("Prompt job %s timed out", task.task_id)
                    success = False
                    error_type = "timeout"
                except Exception as e:  # broad: intentional — LLM providers raise many exception types
                    task.last_result = f"❌ Prompt job failed: {e}"
                    log.error("Prompt job %s failed: %s", task.task_id, e)
                    success = False
                    error_type = type(e).__name__
                finally:
                    duration = time.time() - start
                    get_collector().record_command(
                        command=task.action or "prompt-job",
                        user=str(task.created_by or "scheduler"),
                        workspace="scheduler",
                        duration=duration,
                        success=success,
                        error_type=error_type,
                    )
                    async with self._running_lock:
                        self._running_tasks.discard(task.task_id)

                task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
                task.run_count += 1
                self._save()

                if not success:
                    _captured_prompt = task.prompt
                    _label = f"{task.task_id}/{task.action or 'prompt-job'}"

                    async def _retry_prompt(_p=_captured_prompt):
                        from llm import chat
                        await asyncio.wait_for(chat(_p, model_preference="auto"), timeout=300)

                    self._retry_queue.append(_RetryTask(
                        fn=_retry_prompt,
                        label=_label,
                        attempts_left=3,
                        next_retry_at=time.monotonic() + _RETRY_DELAYS[0],
                    ))
                    log.warning(
                        "Prompt job %s queued for retry (3 attempts, first in %ds)",
                        task.task_id, _RETRY_DELAYS[0],
                    )

                # Post result to Discord if configured
                if task.notify_channel_id and self.notify_callback:
                    result_text = task.last_result or ""
                    is_alert = any(kw in result_text.lower() for kw in _ALERT_PATTERNS)
                    should_notify = (not task.alert_only) or is_alert
                    if should_notify:
                        try:
                            await self.notify_callback(task.task_id, task.action or "prompt-job", result_text, is_alert)
                        except Exception as e:  # broad: intentional — notify_callback wraps arbitrary Discord code
                            log.error("Scheduler notify callback failed for %s: %s", task.task_id, e)
                return

        # Skill job — existing behavior
        skill_fn = self._skill_registry.get(task.action)
        if skill_fn is None:
            from metrics_collector import get_collector
            from trace_context import trace_context

            with trace_context(command=task.action, user_id=task.created_by or 0, channel_id=task.notify_channel_id or 0):
                task.last_result = f"Unknown skill: {task.action}"
                task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
                self._save()
                get_collector().record_command(
                    command=task.action,
                    user=str(task.created_by or "scheduler"),
                    workspace="scheduler",
                    duration=0.0,
                    success=False,
                    error_type="unknown_skill",
                )
                async with self._running_lock:
                    self._running_tasks.discard(task.task_id)
            return

        from metrics_collector import get_collector
        from trace_context import trace_context
        with trace_context(command=task.action, user_id=task.created_by or 0, channel_id=task.notify_channel_id or 0):
            log.info("Executing scheduled task %s: %s(%s)", task.task_id, task.action, task.args)
            start = time.time()
            success = True
            error_type = None
            try:
                result = await asyncio.wait_for(skill_fn(**task.args), timeout=300)
                task.last_result = str(result)[:500] if result else "OK"
            except asyncio.TimeoutError:
                task.last_result = "Error: Task timed out after 5 minutes"
                log.error("Scheduled task %s timed out", task.task_id)
                success = False
                error_type = "timeout"
            except Exception as e:  # broad: intentional — skill_fn wraps arbitrary registered functions
                task.last_result = f"Error: {e}"
                log.error("Scheduled task %s failed: %s", task.task_id, e)
                success = False
                error_type = type(e).__name__
            finally:
                duration = time.time() - start
                get_collector().record_command(
                    command=task.action,
                    user=str(task.created_by or "scheduler"),
                    workspace="scheduler",
                    duration=duration,
                    success=success,
                    error_type=error_type,
                )
                async with self._running_lock:
                    self._running_tasks.discard(task.task_id)

        task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task.run_count += 1
        self._save()

        if not success:
            _captured_fn = skill_fn
            _captured_args = dict(task.args)
            _label = f"{task.task_id}/{task.action}"

            async def _retry_skill(_fn=_captured_fn, _args=_captured_args):
                await asyncio.wait_for(_fn(**_args), timeout=300)

            self._retry_queue.append(_RetryTask(
                fn=_retry_skill,
                label=_label,
                attempts_left=3,
                next_retry_at=time.monotonic() + _RETRY_DELAYS[0],
            ))
            log.warning(
                "Task %s queued for retry (3 attempts, first in %ds)",
                task.task_id, _RETRY_DELAYS[0],
            )

        # Post result to Discord if configured
        if task.notify_channel_id and self.notify_callback:
            result_text = task.last_result or ""
            is_alert = any(kw in result_text.lower() for kw in _ALERT_PATTERNS)
            should_notify = (not task.alert_only) or is_alert
            if should_notify:
                try:
                    await self.notify_callback(task.task_id, task.action, result_text, is_alert)
                except Exception as e:  # broad: intentional — notify_callback wraps arbitrary Discord code
                    log.error("Scheduler notify callback failed for %s: %s", task.task_id, e)
# Global instance
scheduler = TaskScheduler()


# ---------------------------------------------------------------------------
# LLM-callable scheduling skills
# These functions are registered in the skill registry so Gemini can
# autonomously create, list, and cancel scheduled tasks.
# ---------------------------------------------------------------------------


async def create_scheduled_task(
    skill_name: str = "",
    prompt: str = "",
    cron_expression: str = "",
    hour: float = -1,
    minute: float = 0,
    interval_minutes: float = 0,
    args_json: str = "{}",
    label: str = "",
    channel_id: str = "",
) -> str:
    """
    Create a new scheduled task (callable by the LLM).

    Can be either a skill call (specify skill_name) or a prompt job (specify prompt).
    Schedule via cron_expression, interval_minutes, or hour+minute.
    """
    import json as _json

    if not skill_name and not prompt:
        return "❌ Provide either `skill_name` (for a skill job) or `prompt` (for a prompt job)."

    if skill_name and skill_name not in scheduler._skill_registry:
        available = ", ".join(sorted(scheduler._skill_registry.keys())[:20])
        return f"❌ Unknown skill `{skill_name}`. Available: {available}…"

    try:
        args = _json.loads(args_json) if args_json.strip() not in ("", "{}") else {}
    except _json.JSONDecodeError as e:
        return f"❌ Invalid args_json: {e}"

    if cron_expression:
        try:
            from croniter import croniter
            if not croniter.is_valid(cron_expression):
                return f"❌ Invalid cron expression `{cron_expression}`. Expected format: minute hour day month weekday (e.g. `0 9 * * 1` for Mondays at 9am)."
        except (ImportError, ValueError, TypeError) as e:
            return f"❌ Could not validate cron expression: {e}"

    if prompt:
        task = scheduler.create(
            action=label or "prompt-job",
            prompt=prompt,
            cron_expression=cron_expression,
            hour=int(hour),
            minute=int(minute),
            interval_minutes=int(interval_minutes),
            created_by="llm",
            notify_channel_id=int(channel_id) if channel_id else 0,
            alert_only=False,
        )
    else:
        task = scheduler.create(
            action=skill_name,
            args=args,
            cron_expression=cron_expression,
            hour=int(hour),
            minute=int(minute),
            interval_minutes=int(interval_minutes),
            created_by="llm",
            notify_channel_id=int(channel_id) if channel_id else 0,
        )

    # Build human-readable schedule description
    if cron_expression:
        schedule_desc = f"cron `{cron_expression}`"
    elif interval_minutes > 0:
        schedule_desc = f"every {int(interval_minutes)} minutes"
    elif hour >= 0:
        schedule_desc = f"daily at {int(hour):02d}:{int(minute):02d}"
    else:
        schedule_desc = "on demand"

    action_desc = "prompt job" if prompt else f"`{skill_name}`"
    hint = f" ({label})" if label else ""
    return f"✅ Scheduled task `{task.task_id}` created: {action_desc} runs {schedule_desc}{hint}."


async def cancel_scheduled_task(task_id: str) -> str:
    """Cancel (remove) a scheduled task by its task ID."""
    if not scheduler.get(task_id):
        tasks = [t.task_id for t in scheduler.list_tasks()]
        hint = f" Active tasks: {tasks}" if tasks else " No active tasks."
        return f"❌ Task `{task_id}` not found.{hint}"

    scheduler.remove(task_id)
    return f"✅ Scheduled task `{task_id}` cancelled."


async def list_scheduled_tasks() -> str:
    """List all active scheduled tasks."""
    tasks = scheduler.list_tasks()
    if not tasks:
        return "No scheduled tasks."

    lines = []
    for t in tasks:
        state = "✅" if t.enabled else "⏸️"
        if t.cron_expression:
            when = f"cron `{t.cron_expression}`"
        elif t.interval_minutes > 0:
            when = f"every {t.interval_minutes}m"
        elif t.cron_hour >= 0:
            when = f"daily {t.cron_hour:02d}:{t.cron_minute:02d}"
        else:
            when = "manual"
        action_label = t.action
        if t.prompt:
            action_label = f"💬 {t.action}"
        lines.append(
            f"{state} `{t.task_id}` — `{action_label}` ({when}) "
            f"· runs: {t.run_count} · next: {t.next_run_str}"
        )
    return "\n".join(lines)


async def schedule_research_report(topic: str, cron_expression: str = "0 8 * * 0") -> str:
    """Schedule a recurring research report on a topic.

    Creates a scheduled task that runs ``run_scheduled_research`` with the
    given topic on the specified cron schedule.  Defaults to Sunday 8 AM.
    """
    if not topic:
        return "❌ Please provide a research topic."

    # Validate cron expression early
    try:
        from croniter import croniter
        croniter(cron_expression)
    except (ImportError, ValueError, TypeError) as exc:
        return f"❌ Invalid cron expression `{cron_expression}`: {exc}"

    task = scheduler.create(
        action="run_scheduled_research",
        args={"query": topic, "deep": False},
        cron_expression=cron_expression,
        created_by="llm",
        notify_channel_id=int(os.getenv("ALERT_CHANNEL_ID", "0")),
        alert_only=False,
    )

    # Build human-readable schedule hint
    try:
        import datetime as _dt

        from croniter import croniter as _cron
        next_dt = _cron(cron_expression, _dt.datetime.now()).get_next(_dt.datetime)
        next_str = next_dt.strftime("%A %H:%M")
    except (ImportError, ValueError, TypeError, AttributeError):
        next_str = cron_expression

    return (
        f"✅ Scheduled research report `{task.task_id}` created.\n"
        f"**Topic**: {topic}\n"
        f"**Schedule**: `{cron_expression}` (next: {next_str})\n"
        f"Results will be posted to the alert channel automatically."
    )


SCHEDULER_SKILLS = {
    "create_scheduled_task": create_scheduled_task,
    "cancel_scheduled_task": cancel_scheduled_task,
    "list_scheduled_tasks": list_scheduled_tasks,
    "schedule_research_report": schedule_research_report,
}
