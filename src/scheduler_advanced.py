"""
OpenClaw Advanced Scheduler — Phase 3
Enhanced scheduling with event triggers, conditional execution, retry policies,
and SQLite-backed execution history.
"""

import asyncio
import datetime
import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("openclaw.scheduler_advanced")

# Database path for advanced scheduler
SCHEDULER_DB = Path(os.getenv("MEMORY_DIR", "/memory")) / "scheduler_advanced.db"


# ---------------------------------------------------------------------------
# Enums and Data Models
# ---------------------------------------------------------------------------


class TriggerType(str, Enum):
    """Types of event triggers."""
    CRON = "cron"
    EVENT = "event"
    THRESHOLD = "threshold"
    API_RESPONSE = "api_response"


class RetryStrategy(str, Enum):
    """Retry backoff strategies."""
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


@dataclass
class RetryPolicy:
    """Retry configuration for failed tasks."""
    max_retries: int = 3
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    base_delay_seconds: int = 60
    max_delay_seconds: int = 3600

    def get_delay(self, retry_count: int) -> int:
        """Calculate delay in seconds for given retry attempt."""
        if self.strategy == RetryStrategy.NONE:
            return 0
        elif self.strategy == RetryStrategy.LINEAR:
            return min(self.base_delay_seconds * (retry_count + 1), self.max_delay_seconds)
        else:  # EXPONENTIAL
            return min(self.base_delay_seconds * (2 ** retry_count), self.max_delay_seconds)


@dataclass
class EventTrigger:
    """Configuration for event-based triggers."""
    trigger_type: TriggerType
    event_name: str = ""  # e.g., "on_message", "on_member_join"
    event_filter: dict[str, Any] | None = None  # conditions for event to fire
    threshold_value: float | None = None  # for threshold triggers
    threshold_operator: str = ">"  # >, <, ==, >=, <=
    api_endpoint: str = ""  # for API response triggers
    api_check_interval: int = 300  # seconds between API checks


@dataclass
class ConditionalExecution:
    """Conditional logic for task execution."""
    enabled: bool = False
    condition_script: str = ""  # Python expression to evaluate
    variables: dict[str, Any] | None = None  # variables available to condition


@dataclass
class AdvancedTask:
    """Enhanced scheduled task with advanced features."""
    task_id: str
    action: str
    args: dict[str, Any]
    
    # Trigger configuration
    trigger: EventTrigger
    
    # Conditional execution
    condition: ConditionalExecution | None = None
    
    # Retry policy
    retry_policy: RetryPolicy | None = None
    
    # Metadata
    enabled: bool = True
    created_by: str = ""
    created_at: str = ""
    last_run: str = ""
    last_result: str = ""
    run_count: int = 0
    retry_count: int = 0
    next_retry_at: str = ""
    
    # Notification settings
    notify_channel_id: int = 0
    alert_only: bool = True


@dataclass
class ExecutionLog:
    """Log entry for task execution."""
    log_id: int
    task_id: str
    executed_at: str
    status: str  # success, failure, skipped
    result: str
    duration_ms: int
    retry_attempt: int = 0


# ---------------------------------------------------------------------------
# SQLite Database Manager
# ---------------------------------------------------------------------------


class SchedulerDatabase:
    """Manages SQLite storage for advanced scheduler."""
    
    def __init__(self, db_path: Path = SCHEDULER_DB):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS advanced_tasks (
                    task_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    condition_json TEXT,
                    retry_policy_json TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_by TEXT,
                    created_at TEXT,
                    last_run TEXT,
                    last_result TEXT,
                    run_count INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    next_retry_at TEXT,
                    notify_channel_id INTEGER DEFAULT 0,
                    alert_only INTEGER DEFAULT 1
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    executed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    duration_ms INTEGER,
                    retry_attempt INTEGER DEFAULT 0,
                    FOREIGN KEY (task_id) REFERENCES advanced_tasks(task_id)
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_task_id 
                ON execution_logs(task_id)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_logs_executed_at 
                ON execution_logs(executed_at DESC)
            """)
            
            conn.commit()
            log.info("Advanced scheduler database initialized at %s", self.db_path)
    
    def save_task(self, task: AdvancedTask) -> None:
        """Persist task to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO advanced_tasks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                task.task_id,
                task.action,
                json.dumps(task.args),
                json.dumps(asdict(task.trigger)),
                json.dumps(asdict(task.condition)) if task.condition else None,
                json.dumps(asdict(task.retry_policy)) if task.retry_policy else None,
                1 if task.enabled else 0,
                task.created_by,
                task.created_at,
                task.last_run,
                task.last_result,
                task.run_count,
                task.retry_count,
                task.next_retry_at,
                task.notify_channel_id,
                1 if task.alert_only else 0,
            ))
            conn.commit()
    
    def load_tasks(self) -> list[AdvancedTask]:
        """Load all tasks from database."""
        tasks = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM advanced_tasks")
            for row in cursor:
                task = AdvancedTask(
                    task_id=row["task_id"],
                    action=row["action"],
                    args=json.loads(row["args_json"]),
                    trigger=EventTrigger(**json.loads(row["trigger_json"])),
                    condition=ConditionalExecution(**json.loads(row["condition_json"])) 
                              if row["condition_json"] else None,
                    retry_policy=RetryPolicy(**json.loads(row["retry_policy_json"])) 
                                 if row["retry_policy_json"] else None,
                    enabled=bool(row["enabled"]),
                    created_by=row["created_by"] or "",
                    created_at=row["created_at"] or "",
                    last_run=row["last_run"] or "",
                    last_result=row["last_result"] or "",
                    run_count=row["run_count"] or 0,
                    retry_count=row["retry_count"] or 0,
                    next_retry_at=row["next_retry_at"] or "",
                    notify_channel_id=row["notify_channel_id"] or 0,
                    alert_only=bool(row["alert_only"]),
                )
                tasks.append(task)
        return tasks
    
    def delete_task(self, task_id: str) -> bool:
        """Delete task from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM advanced_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def log_execution(
        self,
        task_id: str,
        status: str,
        result: str,
        duration_ms: int,
        retry_attempt: int = 0,
    ) -> int:
        """Log task execution."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO execution_logs (
                    task_id, executed_at, status, result, duration_ms, retry_attempt
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                task_id,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                status,
                result[:1000],  # Truncate long results
                duration_ms,
                retry_attempt,
            ))
            conn.commit()
            return cursor.lastrowid
    
    def get_execution_history(
        self,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[ExecutionLog]:
        """Retrieve execution history."""
        logs = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if task_id:
                cursor = conn.execute("""
                    SELECT * FROM execution_logs 
                    WHERE task_id = ? 
                    ORDER BY executed_at DESC 
                    LIMIT ?
                """, (task_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM execution_logs 
                    ORDER BY executed_at DESC 
                    LIMIT ?
                """, (limit,))
            
            for row in cursor:
                logs.append(ExecutionLog(
                    log_id=row["log_id"],
                    task_id=row["task_id"],
                    executed_at=row["executed_at"],
                    status=row["status"],
                    result=row["result"] or "",
                    duration_ms=row["duration_ms"] or 0,
                    retry_attempt=row["retry_attempt"] or 0,
                ))
        return logs


# ---------------------------------------------------------------------------
# Advanced Task Scheduler
# ---------------------------------------------------------------------------


class AdvancedScheduler:
    """Advanced task scheduler with event triggers and retry logic."""
    
    def __init__(self, db_path: Path | None = None):
        self.db = SchedulerDatabase(db_path=db_path or SCHEDULER_DB)
        self._tasks: dict[str, AdvancedTask] = {}
        self._counter = 0
        self._skill_registry: dict[str, Callable[..., Awaitable[str]]] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._runner_task: asyncio.Task | None = None
        self._event_processor_task: asyncio.Task | None = None
        self._load_tasks()
    
    def _load_tasks(self):
        """Load tasks from database."""
        tasks = self.db.load_tasks()
        for task in tasks:
            self._tasks[task.task_id] = task
            # Update counter
            try:
                num = int(task.task_id.replace("adv-", ""))
                self._counter = max(self._counter, num)
            except ValueError:
                pass
        log.info("Loaded %d advanced tasks from database", len(self._tasks))
    
    def register_skills(self, skills: dict[str, Callable[..., Awaitable[str]]]) -> None:
        """Register callable skills."""
        self._skill_registry.update(skills)
    
    def create_task(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        trigger_type: TriggerType = TriggerType.CRON,
        cron_expression: str = "",
        event_name: str = "",
        condition_script: str = "",
        retry_max: int = 3,
        retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        created_by: str = "",
        notify_channel_id: int = 0,
    ) -> AdvancedTask:
        """Create a new advanced task."""
        self._counter += 1
        task_id = f"adv-{self._counter}"
        
        trigger = EventTrigger(
            trigger_type=trigger_type,
            event_name=event_name,
            threshold_value=None,
        )
        
        if trigger_type == TriggerType.CRON and cron_expression:
            trigger.event_name = cron_expression
        
        condition = None
        if condition_script:
            condition = ConditionalExecution(
                enabled=True,
                condition_script=condition_script,
                variables={},
            )
        
        retry_policy = RetryPolicy(
            max_retries=retry_max,
            strategy=retry_strategy,
        ) if retry_max > 0 else None
        
        task = AdvancedTask(
            task_id=task_id,
            action=action,
            args=args or {},
            trigger=trigger,
            condition=condition,
            retry_policy=retry_policy,
            created_by=created_by,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            notify_channel_id=notify_channel_id,
        )
        
        self._tasks[task_id] = task
        self.db.save_task(task)
        log.info("Created advanced task %s: %s", task_id, action)
        return task
    
    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        if task_id not in self._tasks:
            return False
        del self._tasks[task_id]
        return self.db.delete_task(task_id)
    
    def get_task(self, task_id: str) -> Optional[AdvancedTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    def list_tasks(self) -> list[AdvancedTask]:
        """List all tasks."""
        return sorted(self._tasks.values(), key=lambda t: t.task_id)
    
    async def trigger_event(self, event_name: str, event_data: dict[str, Any] | None = None):
        """Queue an event for processing."""
        await self._event_queue.put((event_name, event_data or {}))
    
    async def _evaluate_condition(self, task: AdvancedTask, context: dict[str, Any]) -> bool:
        """Evaluate conditional execution logic."""
        if not task.condition or not task.condition.enabled:
            return True
        
        try:
            # Safe eval with limited context
            safe_globals = {"__builtins__": {}}
            eval_context = {**context, **(task.condition.variables or {})}
            result = eval(task.condition.condition_script, safe_globals, eval_context)
            return bool(result)
        except Exception as e:
            log.warning("Condition evaluation failed for %s: %s", task.task_id, e)
            return False
    
    async def _execute_task(
        self,
        task: AdvancedTask,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        """Execute a task and return (result, duration_ms)."""
        start_time = datetime.datetime.now()
        
        # Check condition
        if not await self._evaluate_condition(task, context or {}):
            log.info("Task %s skipped due to condition", task.task_id)
            return "Skipped: condition not met", 0
        
        # Execute skill
        skill_fn = self._skill_registry.get(task.action)
        if not skill_fn:
            return f"Error: Unknown skill '{task.action}'", 0
        
        try:
            result = await asyncio.wait_for(skill_fn(**task.args), timeout=300)
            duration_ms = int((datetime.datetime.now() - start_time).total_seconds() * 1000)
            return result or "OK", duration_ms
        except asyncio.TimeoutError:
            duration_ms = int((datetime.datetime.now() - start_time).total_seconds() * 1000)
            return "Error: Task timed out", duration_ms
        except Exception as e:
            duration_ms = int((datetime.datetime.now() - start_time).total_seconds() * 1000)
            return f"Error: {e}", duration_ms
    
    async def _execute_with_retry(self, task: AdvancedTask, context: dict[str, Any] | None = None):
        """Execute task with retry logic."""
        result, duration_ms = await self._execute_task(task, context)
        
        # Determine if execution was successful
        is_success = not result.startswith("Error:")
        status = "success" if is_success else "failure"
        
        # Log execution
        self.db.log_execution(task.task_id, status, result, duration_ms, task.retry_count)
        
        # Update task metadata
        task.last_run = datetime.datetime.now(datetime.timezone.utc).isoformat()
        task.last_result = result[:500]
        task.run_count += 1
        
        # Handle retry logic
        if not is_success and task.retry_policy and task.retry_count < task.retry_policy.max_retries:
            task.retry_count += 1
            delay = task.retry_policy.get_delay(task.retry_count)
            task.next_retry_at = (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delay)
            ).isoformat()
            log.info("Task %s failed, will retry in %ds (attempt %d/%d)",
                    task.task_id, delay, task.retry_count, task.retry_policy.max_retries)
        else:
            task.retry_count = 0
            task.next_retry_at = ""
        
        self.db.save_task(task)
        return result, is_success
    
    async def _process_events(self):
        """Process event queue."""
        while True:
            try:
                event_name, event_data = await self._event_queue.get()
                
                # Find tasks triggered by this event
                for task in self._tasks.values():
                    if not task.enabled:
                        continue
                    
                    if (task.trigger.trigger_type == TriggerType.EVENT and 
                        task.trigger.event_name == event_name):
                        log.info("Event %s triggered task %s", event_name, task.task_id)
                        await self._execute_with_retry(task, event_data)
                
            except Exception as e:
                log.error("Event processor error: %s", e)
    
    async def _check_cron_tasks(self):
        """Check and execute cron-based tasks."""
        while True:
            try:
                now = datetime.datetime.now()
                
                for task in self._tasks.values():
                    if not task.enabled:
                        continue
                    
                    # Check cron triggers
                    if task.trigger.trigger_type == TriggerType.CRON:
                        if await self._is_cron_due(task, now):
                            await self._execute_with_retry(task)
                    
                    # Check retry schedule
                    if task.next_retry_at:
                        retry_time = datetime.datetime.fromisoformat(task.next_retry_at)
                        if now >= retry_time.replace(tzinfo=datetime.timezone.utc):
                            log.info("Retrying task %s", task.task_id)
                            await self._execute_with_retry(task)
                
                await asyncio.sleep(60)
            except Exception as e:
                log.error("Cron checker error: %s", e)
    
    async def _is_cron_due(self, task: AdvancedTask, now: datetime.datetime) -> bool:
        """Check if cron task is due."""
        try:
            from croniter import croniter
            cron_expr = task.trigger.event_name
            cron = croniter(cron_expr, now - datetime.timedelta(minutes=2))
            next_run = cron.get_next(datetime.datetime)
            return abs((next_run - now).total_seconds()) < 120
        except Exception as e:
            log.warning("Invalid cron expression for %s: %s", task.task_id, e)
            return False
    
    def start(self):
        """Start scheduler background tasks."""
        if self._runner_task is None or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._check_cron_tasks())
            log.info("Advanced scheduler cron checker started")
        
        if self._event_processor_task is None or self._event_processor_task.done():
            self._event_processor_task = asyncio.create_task(self._process_events())
            log.info("Advanced scheduler event processor started")
    
    def get_execution_history(self, task_id: str | None = None, limit: int = 50) -> list[ExecutionLog]:
        """Get execution history."""
        return self.db.get_execution_history(task_id, limit)


# Global instance (lazy initialization to avoid issues at import time)
_advanced_scheduler = None

def get_advanced_scheduler():
    """Get or create the global advanced scheduler instance."""
    global _advanced_scheduler
    if _advanced_scheduler is None:
        _advanced_scheduler = AdvancedScheduler()
    return _advanced_scheduler
