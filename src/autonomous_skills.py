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
}
