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

    .. deprecated:: Use ``create_plan()`` from ``agent_loop`` instead for
       persistent, resumable plans stored in ``data/plans/``.
    """
    # Delegate to the unified agent_loop plan system
    from agent_loop import create_plan
    return await create_plan(goal)

async def update_plan_status(phase: int, status: str, note: Optional[str] = None) -> str:
    """
    Update the status of a specific phase in task_plan.md.

    .. deprecated:: Use ``update_plan_step()`` from ``agent_loop`` instead.
    """
    log.warning("update_plan_status is deprecated — use update_plan_step from agent_loop")
    progress_file = Path.cwd() / "progress.md"
    log_entry = f"\n- Phase {phase}: {status}"
    if note:
        log_entry += f" ({note})"

    try:
        with open(progress_file, "a") as f:
            f.write(log_entry)
        return f"✅ Logged progress for Phase {phase} to progress.md."
    except FileNotFoundError:
        return "❌ progress.md not found. Use create_plan/update_plan_step from agent_loop instead."

AUTONOMOUS_SKILLS = {
    "init_planning_files": init_planning_files,
    "update_plan_status": update_plan_status,
    "decompose_goal": None,  # registered below after definition
}


async def decompose_goal(goal: str, project_name: str = "") -> str:
    """
    Break a complex goal into concrete steps using the LLM, then create
    a persistent plan in agent_loop.

    Args:
        goal:         Clear description of what needs to be accomplished.
        project_name: Optional prefix for task titles (e.g. "HomeReno").

    Returns a summary of the created plan with its ID.
    """
    from llm import chat as _llm_chat
    from agent_loop import create_plan

    plan_prompt = (
        "You are a project planner. Break the following goal into 3-7 concrete, "
        "actionable subtasks. Each subtask should be specific and independently completable.\n\n"
        "Goal: " + goal + "\n\n"
        "Reply with ONLY a JSON array of strings, where each string is one step. "
        'Example: ["Research vendors for X", "Compare pricing across 3 options", "Write summary report"]'
    )

    try:
        plan_text, _, _ = await asyncio.wait_for(_llm_chat(plan_prompt), timeout=30)
    except Exception as e:
        return f"❌ Goal decomposition failed: {e}"

    # Parse JSON from the response
    import re as _re
    json_match = _re.search(r"\[.*\]", plan_text, _re.DOTALL)
    if not json_match:
        # Fallback: create a single-step plan with the raw goal
        return await create_plan(goal)

    try:
        subtasks = json.loads(json_match.group())
    except json.JSONDecodeError:
        return await create_plan(goal)

    if not subtasks or not isinstance(subtasks, list):
        return await create_plan(goal)

    # Normalize: ensure all items are strings
    step_lines = []
    for item in subtasks[:7]:
        if isinstance(item, str):
            line = item.strip()
        elif isinstance(item, dict):
            line = item.get("title", item.get("description", str(item)))
        else:
            line = str(item)
        if project_name:
            line = f"[{project_name}] {line}"
        step_lines.append(line)

    steps_text = "\n".join(step_lines)
    return await create_plan(goal, steps_text)


# Patch the AUTONOMOUS_SKILLS registry
# Patch the AUTONOMOUS_SKILLS registry
AUTONOMOUS_SKILLS["decompose_goal"] = decompose_goal
