"""
OpenClaw Workflow Engine — Phase 3
DAG-based task execution with parallel processing, error handling, and workflow templates.
"""

import asyncio
import datetime
import inspect
import json
import logging
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import networkx as nx
import yaml

log = logging.getLogger("openclaw.workflow_engine")

WORKFLOW_DIR = Path(os.getenv("MEMORY_DIR", "/memory")) / "workflows"

# Only create directory if it doesn't exist and parent is writable
try:
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    # In tests or when /memory is not accessible, skip directory creation
    pass


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Status of a workflow task."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    """Status of a workflow execution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # Some tasks succeeded, some failed


@dataclass
class WorkflowTask:
    """A single task in a workflow."""
    task_id: str
    action: str  # skill name or function to call
    args: dict[str, Any]
    depends_on: list[str]  # task IDs this task depends on
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    start_time: str = ""
    end_time: str = ""
    duration_ms: int = 0


@dataclass
class Workflow:
    """A complete workflow definition."""
    workflow_id: str
    name: str
    description: str
    tasks: list[WorkflowTask]
    status: WorkflowStatus = WorkflowStatus.PENDING
    created_by: str = ""
    created_at: str = ""
    last_run: str = ""
    run_count: int = 0
    error_handling: str = "fail_fast"  # fail_fast, continue_on_error
    rollback_on_error: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "tasks": [asdict(t) for t in self.tasks],
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "run_count": self.run_count,
            "error_handling": self.error_handling,
            "rollback_on_error": self.rollback_on_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        """Create workflow from dictionary."""
        tasks = [WorkflowTask(**t) for t in data.get("tasks", [])]
        return cls(
            workflow_id=data["workflow_id"],
            name=data["name"],
            description=data.get("description", ""),
            tasks=tasks,
            status=data.get("status", WorkflowStatus.PENDING),
            created_by=data.get("created_by", ""),
            created_at=data.get("created_at", ""),
            last_run=data.get("last_run", ""),
            run_count=data.get("run_count", 0),
            error_handling=data.get("error_handling", "fail_fast"),
            rollback_on_error=data.get("rollback_on_error", False),
        )


@dataclass
class WorkflowExecution:
    """Record of a workflow execution."""
    execution_id: str
    workflow_id: str
    started_at: str
    completed_at: str = ""
    status: WorkflowStatus = WorkflowStatus.RUNNING
    task_results: dict[str, str] = None  # task_id -> result
    errors: list[str] = None

    def __post_init__(self):
        if self.task_results is None:
            self.task_results = {}
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# Workflow Templates
# ---------------------------------------------------------------------------


WORKFLOW_TEMPLATES = {
    "morning-briefing": {
        "name": "Morning Briefing",
        "description": "Daily morning briefing with weather, news, and stocks",
        "tasks": [
            {
                "task_id": "get_weather",
                "action": "get_weather",
                "args": {"location": "default"},
                "depends_on": [],
            },
            {
                "task_id": "get_news",
                "action": "search_news",
                "args": {"query": "top headlines", "max_results": 5},
                "depends_on": [],
            },
            {
                "task_id": "get_stocks",
                "action": "get_stock_prices",
                "args": {"symbols": ["AAPL", "GOOGL", "MSFT"]},
                "depends_on": [],
            },
            {
                "task_id": "send_summary",
                "action": "send_discord_message",
                "args": {"channel": "general"},
                "depends_on": ["get_weather", "get_news", "get_stocks"],
            },
        ],
    },
    "market-close-report": {
        "name": "Market Close Report",
        "description": "Daily market close summary with stock prices and analysis",
        "tasks": [
            {
                "task_id": "get_stock_prices",
                "action": "get_stock_prices",
                "args": {"symbols": ["SPY", "QQQ", "DIA"]},
                "depends_on": [],
            },
            {
                "task_id": "analyze_market",
                "action": "analyze_market_trends",
                "args": {},
                "depends_on": ["get_stock_prices"],
            },
            {
                "task_id": "send_report",
                "action": "send_discord_message",
                "args": {"channel": "trading"},
                "depends_on": ["analyze_market"],
            },
        ],
    },
    "backup-and-monitor": {
        "name": "Backup and Monitor",
        "description": "Run backups and system health checks",
        "tasks": [
            {
                "task_id": "backup_databases",
                "action": "backup_databases",
                "args": {},
                "depends_on": [],
            },
            {
                "task_id": "check_disk_space",
                "action": "check_disk_space",
                "args": {},
                "depends_on": [],
            },
            {
                "task_id": "check_docker_health",
                "action": "list_containers",
                "args": {},
                "depends_on": [],
            },
            {
                "task_id": "send_health_report",
                "action": "send_discord_message",
                "args": {"channel": "monitoring"},
                "depends_on": ["backup_databases", "check_disk_space", "check_docker_health"],
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Workflow Engine
# ---------------------------------------------------------------------------


class WorkflowEngine:
    """Executes workflows with DAG-based task scheduling."""

    def __init__(self):
        self._workflows: dict[str, Workflow] = {}
        self._skill_registry: dict[str, Callable[..., Awaitable[str]]] = {}
        self._counter = 0
        self._execution_counter = 0
        self._load_workflows()

    def _load_workflows(self):
        """Load workflows from disk."""
        if not WORKFLOW_DIR.exists():
            return

        for workflow_file in WORKFLOW_DIR.glob("*.json"):
            try:
                data = json.loads(workflow_file.read_text())
                workflow = Workflow.from_dict(data)
                self._workflows[workflow.workflow_id] = workflow

                # Update counter
                try:
                    num = int(workflow.workflow_id.replace("wf-", ""))
                    self._counter = max(self._counter, num)
                except ValueError:
                    pass
            except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
                log.error("Failed to load workflow from %s: %s", workflow_file, e)

        log.info("Loaded %d workflows from disk", len(self._workflows))

    def _save_workflow(self, workflow: Workflow):
        """Persist workflow to disk."""
        workflow_file = WORKFLOW_DIR / f"{workflow.workflow_id}.json"
        workflow_file.write_text(json.dumps(workflow.to_dict(), indent=2))

    def register_skills(self, skills: dict[str, Callable[..., Awaitable[str]]]) -> None:
        """Register callable skills."""
        self._skill_registry.update(skills)

    def create_workflow(
        self,
        name: str,
        description: str = "",
        tasks: list[dict[str, Any]] | None = None,
        error_handling: str = "fail_fast",
        rollback_on_error: bool = False,
        created_by: str = "",
    ) -> Workflow:
        """Create a new workflow."""
        self._counter += 1
        workflow_id = f"wf-{self._counter}"

        workflow_tasks = []
        if tasks:
            for task_data in tasks:
                task = WorkflowTask(
                    task_id=task_data["task_id"],
                    action=task_data["action"],
                    args=task_data.get("args", {}),
                    depends_on=task_data.get("depends_on", []),
                )
                workflow_tasks.append(task)

        workflow = Workflow(
            workflow_id=workflow_id,
            name=name,
            description=description,
            tasks=workflow_tasks,
            error_handling=error_handling,
            rollback_on_error=rollback_on_error,
            created_by=created_by,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        self._workflows[workflow_id] = workflow
        self._save_workflow(workflow)
        log.info("Created workflow %s: %s", workflow_id, name)
        return workflow

    def create_from_template(self, template_name: str, created_by: str = "") -> Optional[Workflow]:
        """Create workflow from template."""
        template = WORKFLOW_TEMPLATES.get(template_name)
        if not template:
            log.warning("Unknown template: %s", template_name)
            return None

        return self.create_workflow(
            name=template["name"],
            description=template["description"],
            tasks=template["tasks"],
            created_by=created_by,
        )

    def create_from_yaml(self, yaml_content: str, created_by: str = "") -> Workflow:
        """Create workflow from YAML definition."""
        data = yaml.safe_load(yaml_content)

        return self.create_workflow(
            name=data["workflow"],
            description=data.get("description", ""),
            tasks=data.get("tasks", []),
            error_handling=data.get("error_handling", "fail_fast"),
            rollback_on_error=data.get("rollback_on_error", False),
            created_by=created_by,
        )

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get workflow by ID."""
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> list[Workflow]:
        """List all workflows."""
        return sorted(self._workflows.values(), key=lambda w: w.workflow_id)

    def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow."""
        if workflow_id not in self._workflows:
            return False

        del self._workflows[workflow_id]
        workflow_file = WORKFLOW_DIR / f"{workflow_id}.json"
        if workflow_file.exists():
            workflow_file.unlink()
        return True

    def _build_dag(self, workflow: Workflow) -> nx.DiGraph:
        """Build directed acyclic graph from workflow tasks."""
        G = nx.DiGraph()

        # Add nodes
        for task in workflow.tasks:
            G.add_node(task.task_id, task=task)

        # Add edges (dependencies)
        for task in workflow.tasks:
            for dep in task.depends_on:
                G.add_edge(dep, task.task_id)

        # Validate DAG
        if not nx.is_directed_acyclic_graph(G):
            raise ValueError("Workflow contains cycles - not a valid DAG")

        return G

    async def _execute_task(
        self,
        task: WorkflowTask,
        context: dict[str, Any],
    ) -> tuple[str, bool]:
        """Execute a single task. Returns (result, success)."""
        skill_fn = self._skill_registry.get(task.action)
        if not skill_fn:
            task.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            task.status = TaskStatus.FAILED
            task.error = f"Unknown skill '{task.action}'"
            return f"Error: {task.error}", False

        task.status = TaskStatus.RUNNING
        task.start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

        try:
            # Merge task args with context
            exec_args = {**context, **task.args}
            signature = inspect.signature(skill_fn)
            accepts_var_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            if accepts_var_kwargs:
                call_args = exec_args
            else:
                call_args = {
                    key: value
                    for key, value in exec_args.items()
                    if key in signature.parameters
                }

            result = await asyncio.wait_for(skill_fn(**call_args), timeout=300)

            task.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            task.duration_ms = int(
                (datetime.datetime.fromisoformat(task.end_time) -
                 datetime.datetime.fromisoformat(task.start_time)).total_seconds() * 1000
            )
            task.result = result or "OK"
            task.status = TaskStatus.SUCCESS
            return result, True

        except asyncio.TimeoutError:
            task.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            task.status = TaskStatus.FAILED
            task.error = "Task timed out after 5 minutes"
            return task.error, False

        except Exception as e:  # broad: intentional
            task.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            task.status = TaskStatus.FAILED
            task.error = str(e)
            log.error("Task %s failed: %s", task.task_id, e)
            return str(e), False

    async def execute_workflow(
        self,
        workflow_id: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Execute a workflow and return execution record."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        self._execution_counter += 1
        execution_id = f"exec-{self._execution_counter}"

        execution = WorkflowExecution(
            execution_id=execution_id,
            workflow_id=workflow_id,
            started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        # Update workflow metadata
        workflow.status = WorkflowStatus.RUNNING
        workflow.run_count += 1
        workflow.last_run = execution.started_at

        # Build DAG
        try:
            dag = self._build_dag(workflow)
        except ValueError as e:
            execution.status = WorkflowStatus.FAILED
            execution.errors.append(str(e))
            workflow.status = WorkflowStatus.FAILED
            self._save_workflow(workflow)
            return execution

        # Execute tasks in topological order
        task_results: dict[str, str] = {}
        exec_context = context or {}

        try:
            # Get execution order
            execution_order = list(nx.topological_sort(dag))

            # Group tasks by level for parallel execution
            levels: list[list[str]] = []
            remaining = set(execution_order)

            while remaining:
                # Find tasks with no remaining dependencies
                ready = []
                for task_id in remaining:
                    deps = dag.predecessors(task_id)
                    if all(d not in remaining for d in deps):
                        ready.append(task_id)

                if not ready:
                    break  # Shouldn't happen with valid DAG

                levels.append(ready)
                remaining -= set(ready)

            # Execute levels in order, tasks within level in parallel
            for level in levels:
                # Get task objects
                level_tasks = [dag.nodes[tid]["task"] for tid in level]

                # Execute in parallel
                results = await asyncio.gather(
                    *[self._execute_task(task, exec_context) for task in level_tasks],
                    return_exceptions=True,
                )

                # Process results
                for task, result in zip(level_tasks, results):
                    if isinstance(result, Exception):
                        task.status = TaskStatus.FAILED
                        task.error = str(result)
                        execution.errors.append(f"{task.task_id}: {result}")

                        if workflow.error_handling == "fail_fast":
                            # Stop execution
                            raise result
                    else:
                        result_text, success = result
                        task_results[task.task_id] = result_text
                        exec_context[f"{task.task_id}_result"] = result_text

                        if not success and workflow.error_handling == "fail_fast":
                            raise RuntimeError(f"Task {task.task_id} failed: {result_text}")

            # Determine final status
            failed_tasks = [t for t in workflow.tasks if t.status == TaskStatus.FAILED]
            if failed_tasks:
                execution.status = WorkflowStatus.PARTIAL
                workflow.status = WorkflowStatus.PARTIAL
            else:
                execution.status = WorkflowStatus.SUCCESS
                workflow.status = WorkflowStatus.SUCCESS

        except Exception as e:  # broad: intentional
            log.error("Workflow %s execution failed: %s", workflow_id, e)
            execution.status = WorkflowStatus.FAILED
            workflow.status = WorkflowStatus.FAILED
            execution.errors.append(str(e))

        execution.completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        execution.task_results = task_results

        # Save updated workflow
        self._save_workflow(workflow)

        return execution

    def get_templates(self) -> list[str]:
        """Get list of available templates."""
        return list(WORKFLOW_TEMPLATES.keys())


# Global instance
workflow_engine = WorkflowEngine()


# ---------------------------------------------------------------------------
# LLM-callable workflow skills
# ---------------------------------------------------------------------------


async def create_workflow_from_template(template_name: str) -> str:
    """Create a workflow from a built-in template."""
    if template_name not in WORKFLOW_TEMPLATES:
        available = ", ".join(workflow_engine.get_templates())
        return f"❌ Unknown template '{template_name}'. Available: {available}"

    workflow = workflow_engine.create_from_template(template_name, created_by="llm")
    if not workflow:
        return f"❌ Failed to create workflow from template '{template_name}'"

    return f"✅ Created workflow `{workflow.workflow_id}`: {workflow.name}"


async def run_workflow(workflow_id: str) -> str:
    """Execute a workflow."""
    workflow = workflow_engine.get_workflow(workflow_id)
    if not workflow:
        workflows = [w.workflow_id for w in workflow_engine.list_workflows()]
        hint = f" Available: {workflows}" if workflows else " No workflows available."
        return f"❌ Workflow '{workflow_id}' not found.{hint}"

    try:
        execution = await workflow_engine.execute_workflow(workflow_id)

        if execution.status == WorkflowStatus.SUCCESS:
            return f"✅ Workflow `{workflow_id}` completed successfully. Execution: {execution.execution_id}"
        elif execution.status == WorkflowStatus.PARTIAL:
            return f"⚠️ Workflow `{workflow_id}` completed with errors: {', '.join(execution.errors)}"
        else:
            return f"❌ Workflow `{workflow_id}` failed: {', '.join(execution.errors)}"

    except Exception as e:  # broad: intentional
        return f"❌ Failed to execute workflow: {e}"


async def list_workflows_skill() -> str:
    """List all available workflows."""
    workflows = workflow_engine.list_workflows()
    if not workflows:
        return "No workflows defined."

    lines = []
    for wf in workflows:
        status_emoji = {
            WorkflowStatus.PENDING: "⏸️",
            WorkflowStatus.RUNNING: "▶️",
            WorkflowStatus.SUCCESS: "✅",
            WorkflowStatus.FAILED: "❌",
            WorkflowStatus.PARTIAL: "⚠️",
        }.get(wf.status, "❓")

        lines.append(
            f"{status_emoji} `{wf.workflow_id}` — **{wf.name}** "
            f"({len(wf.tasks)} tasks) · runs: {wf.run_count}"
        )

    return "\n".join(lines)


WORKFLOW_SKILLS = {
    "create_workflow_from_template": create_workflow_from_template,
    "run_workflow": run_workflow,
    "list_workflows": list_workflows_skill,
}
