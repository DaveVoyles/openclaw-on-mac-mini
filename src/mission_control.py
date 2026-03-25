"""
OpenClaw Mission Control Skill
Kanban-style task management via the Mission Control ClawHub skill.
Dashboard: https://davevoyles.github.io/openclaw-dashboard/
Repo:      https://github.com/DaveVoyles/openclaw-dashboard

Tasks live in the dashboard repo (data/tasks.json).
mc-update.sh is used to modify tasks and push back to GitHub.
"""

import asyncio
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("openclaw.mission_control")

TASKS_FILE = Path(os.getenv("MC_TASKS_FILE", "/app/data/tasks.json"))
MC_SCRIPT = Path(__file__).parent.parent / "skills" / "mission-control" / "scripts" / "mc-update.sh"
DASHBOARD_URL = "https://davevoyles.github.io/openclaw-dashboard/"

STATUS_EMOJI = {
    "permanent": "🔁",
    "backlog": "📋",
    "in_progress": "🔄",
    "review": "👀",
    "done": "✅",
}

PRIORITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
}

_tasks_cache: dict | None = None
_tasks_mtime: float = 0.0
_tasks_lock = threading.Lock()


def _load_tasks() -> dict:
    """Load tasks.json with mtime-based caching to avoid repeated disk reads."""
    global _tasks_cache, _tasks_mtime
    with _tasks_lock:
        candidates = [
            TASKS_FILE,
            Path("/app/data/tasks.json"),
            Path.home() / "openclaw" / "data" / "tasks.json",
            Path("/Users/davevoyles/openclaw/data/tasks.json"),
        ]
        for p in candidates:
            try:
                current_mtime = p.stat().st_mtime if p.exists() else 0.0
            except OSError:
                current_mtime = 0.0
            if current_mtime == 0.0:
                continue
            if _tasks_cache is not None and current_mtime == _tasks_mtime:
                return _tasks_cache
            try:
                with open(p, encoding="utf-8") as f:
                    _tasks_cache = json.load(f)
                _tasks_mtime = current_mtime
                return _tasks_cache
            except Exception as e:
                log.debug("Skipped tasks path %s: %s", p, e)
        return {"tasks": []}


async def get_mission_tasks(status: str | None = None) -> str:
    """List Mission Control tasks, optionally filtered by status."""
    data = _load_tasks()
    tasks = data.get("tasks", [])

    if status:
        tasks = [t for t in tasks if t.get("status") == status]

    if not tasks:
        label = f" with status '{status}'" if status else ""
        return f"📋 No tasks{label} found.\n🖥️ Dashboard: {DASHBOARD_URL}"

    lines = [f"**Mission Control Tasks**{' — ' + status if status else ''}"]
    for t in tasks:
        s = t.get("status", "unknown")
        s_icon = STATUS_EMOJI.get(s, "❓")
        p_icon = PRIORITY_EMOJI.get(t.get("priority", ""), "")
        title = t.get("title", "Untitled")
        tid = t.get("id", "?")
        subtasks = t.get("subtasks", [])
        done_subs = sum(1 for st in subtasks if st.get("done"))
        sub_str = f" [{done_subs}/{len(subtasks)} subtasks]" if subtasks else ""
        lines.append(f"{s_icon} {p_icon} **{tid}**: {title}{sub_str}")

    lines.append(f"\n🖥️ [Dashboard]({DASHBOARD_URL})")
    return "\n".join(lines)


async def get_task_detail(task_id: str) -> str:
    """Get full details for a specific task by ID."""
    if not task_id or len(task_id) > 200:
        return "❌ Invalid task_id (must be 1–200 characters)."
    data = _load_tasks()
    task = next((t for t in data.get("tasks", []) if t.get("id") == task_id), None)

    if not task:
        return f"❌ Task `{task_id}` not found."

    s = task.get("status", "unknown")
    s_icon = STATUS_EMOJI.get(s, "❓")
    p_icon = PRIORITY_EMOJI.get(task.get("priority", ""), "")
    lines = [
        f"{s_icon} {p_icon} **{task.get('title', 'Untitled')}** (`{task_id}`)",
        f"**Status**: {s}",
    ]
    if task.get("description"):
        desc = task["description"][:300] + ("…" if len(task["description"]) > 300 else "")
        lines.append(f"**Description**: {desc}")
    subtasks = task.get("subtasks", [])
    if subtasks:
        lines.append("**Subtasks**:")
        for st in subtasks:
            icon = "✅" if st.get("done") else "⬜"
            lines.append(f"  {icon} {st.get('title', '?')}")
    comments = task.get("comments", [])
    if comments:
        last = comments[-1]
        lines.append(f"**Last comment** ({last.get('author', '?')}): {last.get('text', '')[:200]}")
    return "\n".join(lines)


async def _run_mc_script(*args: str) -> str:
    """Run mc-update.sh with the given args. Returns combined stdout/stderr."""
    if not MC_SCRIPT.exists():
        return f"❌ mc-update.sh not found at {MC_SCRIPT}"

    cmd = ["bash", str(MC_SCRIPT)] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "TASKS_FILE": str(TASKS_FILE)},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode().strip()
        err = stderr.decode().strip()
        return (out + ("\n" + err if err else "")).strip()
    except asyncio.TimeoutError:
        return "❌ mc-update.sh timed out"
    except Exception as e:
        return f"❌ Error running mc-update.sh: {e}"


async def update_task_status(task_id: str, new_status: str) -> str:
    """Update a task's status (backlog, in_progress, review, done)."""
    if not task_id or len(task_id) > 200:
        return "❌ Invalid task_id (must be 1–200 characters)."
    valid = {"backlog", "in_progress", "review", "done", "permanent"}
    if new_status not in valid:
        return f"❌ Invalid status. Valid: {', '.join(sorted(valid))}"
    result = await _run_mc_script("status", task_id, new_status)
    return f"{'✅' if '✓' in result else '❌'} {result}"


async def complete_task(task_id: str, summary: str) -> str:
    """Mark a task as complete (moves to review) with a summary."""
    if not task_id or len(task_id) > 200:
        return "❌ Invalid task_id (must be 1–200 characters)."
    result = await _run_mc_script("complete", task_id, summary)
    return f"{'✅' if '✓' in result else '❌'} {result}"


async def add_task_comment(task_id: str, comment: str) -> str:
    """Add a comment to a task."""
    if not task_id or len(task_id) > 200:
        return "❌ Invalid task_id (must be 1–200 characters)."
    result = await _run_mc_script("comment", task_id, comment)
    return f"{'✅' if '✓' in result else '❌'} {result}"


MISSION_CONTROL_SKILLS = {
    "get_mission_tasks": get_mission_tasks,
    "get_task_detail": get_task_detail,
    "update_task_status": update_task_status,
    "complete_task": complete_task,
    "add_task_comment": add_task_comment,
}
