import asyncio
import os
import sys
import logging
import json
from pathlib import Path
from typing import Optional

_SKILLS_DIR = Path(__file__).parent.parent / "skills"
_PLANNING_DIR = _SKILLS_DIR / "planning-with-files"
_TEMPLATES_DIR = _PLANNING_DIR / "templates"

log = logging.getLogger("openclaw.autonomous")

async def init_planning_files(goal: str) -> str:
    """
    Initialize task_plan.md, findings.md, and progress.md in the current workspace.
    This implements the 'Planning With Files' pattern for complex tasks.
    """
    project_root = Path.cwd()
    plan_file = project_root / "task_plan.md"
    findings_file = project_root / "findings.md"
    progress_file = project_root / "progress.md"

    # Only initialize if they don't exist
    if plan_file.exists():
        return "⚠️ Planning files already exist. Read task_plan.md to continue."

    try:
        # Read templates
        plan_tmpl = (_TEMPLATES_DIR / "task_plan.md").read_text()
        findings_tmpl = (_TEMPLATES_DIR / "findings.md").read_text()
        progress_tmpl = (_TEMPLATES_DIR / "progress.md").read_text()

        # Simple string replacement for the goal
        plan_content = plan_tmpl.replace("[One sentence describing the end state]", goal)
        plan_content = plan_content.replace("[Brief Description]", goal[:50])

        # Write to project root
        plan_file.write_text(plan_content)
        findings_file.write_text(findings_tmpl)
        progress_file.write_text(progress_tmpl)

        return f"✅ Initialized planning files in {project_root}. Read task_plan.md to begin Phase 1."
    except Exception as e:
        return f"❌ Failed to initialize planning files: {str(e)}"

async def update_plan_status(phase: int, status: str, note: Optional[str] = None) -> str:
    """
    Update the status of a specific phase in task_plan.md.
    """
    plan_file = Path.cwd() / "task_plan.md"
    if not plan_file.exists():
        return "❌ task_plan.md not found. Run init_planning_files first."

    # This is a simplified implementation. A robust one would use regex to find and replace status.
    # For now, we'll suggest the agent use standard file edit tools for granular updates,
    # but provide this as a high-level helper if they just want to log progress.
    log_entry = f"\\n- Phase {phase}: {status}"
    if note:
        log_entry += f" ({note})"

    progress_file = Path.cwd() / "progress.md"
    with open(progress_file, "a") as f:
        f.write(log_entry)

    return f"✅ Logged progress for Phase {phase} to progress.md."

AUTONOMOUS_SKILLS = {
    "init_planning_files": init_planning_files,
    "update_plan_status": update_plan_status,
    "decompose_goal": None,  # registered below after definition
}


async def decompose_goal(goal: str, project_name: str = "") -> str:
    """
    Break a complex goal into concrete Mission Control tasks using the LLM.

    The agent analyzes the goal, produces an ordered task list, and creates
    each task in Mission Control so progress can be tracked on the kanban board.

    Args:
        goal:         Clear description of what needs to be accomplished.
        project_name: Optional prefix for task titles (e.g. "HomeReno").

    Returns a summary of created tasks with their IDs.
    """
    from llm import chat as _llm_chat

    plan_prompt = (
        "You are a project planner. Break the following goal into 3-7 concrete, "
        "actionable subtasks. Each subtask should be specific and independently completable.\n\n"
        "Goal: " + goal + "\n\n"
        "Reply with ONLY a JSON array of objects with 'title' and 'description' fields. "
        'Example: [{"title": "Research vendors", "description": "Find 3 vendors for X"}]'
    )

    try:
        plan_text, _, _ = await asyncio.wait_for(_llm_chat(plan_prompt), timeout=30)
    except Exception as e:
        return f"❌ Goal decomposition failed: {e}"

    # Parse JSON from the response
    import json as _json
    import re as _re
    json_match = _re.search(r"\[.*\]", plan_text, _re.DOTALL)
    if not json_match:
        return f"❌ LLM did not return a valid JSON task list. Response:\n{plan_text[:500]}"

    try:
        subtasks = _json.loads(json_match.group())
    except _json.JSONDecodeError as e:
        return f"❌ Could not parse task list: {e}\nRaw: {json_match.group()[:300]}"

    if not subtasks or not isinstance(subtasks, list):
        return "❌ No tasks generated."

    # Create each task in Mission Control
    from mission_control import _run_mc_script, MISSION_CONTROL_SKILLS

    created: list[str] = []
    for i, task in enumerate(subtasks[:7], 1):
        title = task.get("title", f"Task {i}")
        if project_name:
            title = f"[{project_name}] {title}"
        description = task.get("description", "")
        result = await _run_mc_script("add", title, description, "backlog", "medium")
        log.info("decompose_goal created task: %s → %s", title, result[:80])
        created.append(f"• {title}")

    summary = "\n".join(created)
    return (
        f"✅ Created **{len(created)} tasks** for goal: *{goal[:80]}*\n\n"
        f"{summary}\n\n"
        "Tasks are now in Mission Control backlog. Use `/tasks` to track progress."
    )


# Patch the AUTONOMOUS_SKILLS registry
AUTONOMOUS_SKILLS["decompose_goal"] = decompose_goal
