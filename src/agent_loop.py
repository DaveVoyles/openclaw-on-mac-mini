"""
OpenClaw Agent Loop — Persistent observe → think → act → repeat engine.

Creates markdown plan files that track multi-step goals, survive restarts,
and coordinate parallel worker sub-agents.

Plans are stored as .md files in PLANS_DIR with checkbox-based step tracking.
"""

import asyncio
import datetime
import fcntl
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("openclaw.agent_loop")

PLANS_DIR = Path(os.getenv("PLANS_DIR", "data/plans"))
MAX_ACTIVE_PLANS = int(os.getenv("MAX_ACTIVE_PLANS", "20"))
MAX_WORKERS_PER_PLAN = int(os.getenv("MAX_WORKERS_PER_PLAN", "3"))
MAX_WORKERS_GLOBAL = int(os.getenv("MAX_WORKERS_GLOBAL", "10"))
PLAN_TIMEOUT = int(os.getenv("PLAN_TIMEOUT", "600"))


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Step:
    num: int
    description: str
    status: str = "pending"          # pending | in-progress | done | failed | skipped
    output: str = ""
    worker_id: str = ""
    depends_on: list[int] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status in ("done", "skipped", "failed")


@dataclass
class Plan:
    plan_id: str
    goal: str
    status: str = "in-progress"      # in-progress | completed | interrupted | failed
    initiator: str = "user"          # user:<name> | scheduler:<id> | self:proactive
    channel_id: int = 0
    steps: list[Step] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    lessons: list[str] = field(default_factory=list)

    def __post_init__(self):
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def next_incomplete_step(self) -> Step | None:
        """Return the first step whose dependencies are met and is still pending."""
        done_nums = {s.num for s in self.steps if s.is_complete}
        for s in self.steps:
            if s.status == "pending" and all(d in done_nums for d in s.depends_on):
                return s
        return None

    def independent_pending_steps(self) -> list[Step]:
        """Return all pending steps whose dependencies are already met."""
        done_nums = {s.num for s in self.steps if s.is_complete}
        return [
            s for s in self.steps
            if s.status == "pending" and all(d in done_nums for d in s.depends_on)
        ]

    def progress_str(self) -> str:
        done = sum(1 for s in self.steps if s.is_complete)
        return f"{done}/{len(self.steps)}"


# ---------------------------------------------------------------------------
# Markdown serialization
# ---------------------------------------------------------------------------

_STEP_RE = re.compile(
    r"^- \[([ xX!~])\] Step (\d+): (.+)$", re.MULTILINE
)
_STATUS_CHAR = {"done": "x", "failed": "!", "skipped": "~", "in-progress": "/", "pending": " "}
_CHAR_STATUS = {"x": "done", "X": "done", "!": "failed", "~": "skipped", "/": "in-progress", " ": "pending"}


def plan_to_markdown(plan: Plan) -> str:
    """Serialize a Plan to a human-readable .md file."""
    lines = [
        f"# Plan: {plan.goal}",
        "",
        f"- **Plan ID:** {plan.plan_id}",
        f"- **Status:** {plan.status}",
        f"- **Created:** {plan.created_at}",
        f"- **Updated:** {plan.updated_at}",
        f"- **Initiator:** {plan.initiator}",
        f"- **Channel:** {plan.channel_id}",
        f"- **Progress:** {plan.progress_str()}",
        "",
        "## Steps",
        "",
    ]

    for step in plan.steps:
        char = _STATUS_CHAR.get(step.status, " ")
        dep_str = ""
        if step.depends_on:
            dep_str = f" (depends: {','.join(str(d) for d in step.depends_on)})"
        lines.append(f"- [{char}] Step {step.num}: {step.description}{dep_str}")
        if step.output:
            # Indent output under the step
            for ol in step.output.split("\n"):
                lines.append(f"  > {ol}")

    lines.append("")
    lines.append("## Context")
    lines.append("")
    if plan.context:
        for k, v in plan.context.items():
            lines.append(f"**{k}:**")
            lines.append(v[:2000])
            lines.append("")
    else:
        lines.append("*(no shared context yet)*")

    lines.append("")
    lines.append("## Lessons")
    lines.append("")
    if plan.lessons:
        for lesson in plan.lessons:
            lines.append(f"- {lesson}")
    else:
        lines.append("*(none yet)*")

    lines.append("")
    return "\n".join(lines)


def plan_from_markdown(text: str, plan_id: str = "") -> Plan:
    """Parse a plan .md file back into a Plan object."""
    plan = Plan(plan_id=plan_id, goal="")

    # Parse header fields
    for line in text.split("\n"):
        if line.startswith("# Plan: "):
            plan.goal = line[8:].strip()
        elif line.startswith("- **Plan ID:**"):
            plan.plan_id = line.split("**Plan ID:**")[1].strip()
        elif line.startswith("- **Status:**"):
            plan.status = line.split("**Status:**")[1].strip()
        elif line.startswith("- **Created:**"):
            plan.created_at = line.split("**Created:**")[1].strip()
        elif line.startswith("- **Updated:**"):
            plan.updated_at = line.split("**Updated:**")[1].strip()
        elif line.startswith("- **Initiator:**"):
            plan.initiator = line.split("**Initiator:**")[1].strip()
        elif line.startswith("- **Channel:**"):
            try:
                plan.channel_id = int(line.split("**Channel:**")[1].strip())
            except ValueError:
                pass

    # Parse steps
    for match in _STEP_RE.finditer(text):
        char, num_str, desc = match.group(1), match.group(2), match.group(3)
        status = _CHAR_STATUS.get(char, "pending")

        # Extract depends_on from description
        depends: list[int] = []
        dep_match = re.search(r"\(depends:\s*([\d,]+)\)", desc)
        if dep_match:
            depends = [int(d.strip()) for d in dep_match.group(1).split(",") if d.strip()]
            desc = re.sub(r"\s*\(depends:\s*[\d,]+\)", "", desc).strip()

        step = Step(num=int(num_str), description=desc, status=status, depends_on=depends)

        # Collect output lines (indented > lines after the step)
        step_line_idx = text.find(match.group(0))
        after = text[step_line_idx + len(match.group(0)):]
        output_lines = []
        for ol in after.split("\n"):
            if ol.startswith("  > "):
                output_lines.append(ol[4:])
            elif ol.strip() == "":
                continue
            else:
                break
        if output_lines:
            step.output = "\n".join(output_lines)

        plan.steps.append(step)

    # Parse context section
    ctx_match = re.search(r"## Context\n\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if ctx_match:
        ctx_text = ctx_match.group(1).strip()
        if ctx_text and ctx_text != "*(no shared context yet)*":
            current_key = ""
            current_val_lines: list[str] = []
            for line in ctx_text.split("\n"):
                if line.startswith("**") and line.endswith(":**"):
                    if current_key:
                        plan.context[current_key] = "\n".join(current_val_lines).strip()
                    current_key = line[2:-3]
                    current_val_lines = []
                else:
                    current_val_lines.append(line)
            if current_key:
                plan.context[current_key] = "\n".join(current_val_lines).strip()

    return plan


# ---------------------------------------------------------------------------
# File I/O with locking
# ---------------------------------------------------------------------------


def _ensure_dir():
    PLANS_DIR.mkdir(parents=True, exist_ok=True)


def _plan_path(plan_id: str) -> Path:
    # Sanitize plan_id to prevent path traversal
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", plan_id)
    return PLANS_DIR / f"{safe_id}.md"


def save_plan(plan: Plan, _retries: int = 3, _backoff: float = 0.5) -> None:
    """Atomically write a plan to disk with file locking and retry."""
    _ensure_dir()
    plan.updated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    md = plan_to_markdown(plan)
    path = _plan_path(plan.plan_id)
    last_exc: Exception | None = None
    for attempt in range(_retries):
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(md)
                f.flush()
                os.fsync(f.fileno())
                fcntl.flock(f, fcntl.LOCK_UN)
            tmp.rename(path)
            return
        except Exception as exc:
            last_exc = exc
            tmp.unlink(missing_ok=True)
            if attempt < _retries - 1:
                import time
                time.sleep(min(_backoff * (2 ** attempt), 1.0))  # short sync sleep; only on write failure
                log.warning("save_plan retry %d/%d for %s: %s", attempt + 1, _retries, plan.plan_id, exc)
    log.error("save_plan failed after %d attempts for %s: %s", _retries, plan.plan_id, last_exc)
    raise last_exc  # type: ignore[misc]


def load_plan(plan_id: str) -> Plan | None:
    """Load a plan from disk."""
    path = _plan_path(plan_id)
    if not path.exists():
        return None
    with open(path) as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        text = f.read()
        fcntl.flock(f, fcntl.LOCK_UN)
    return plan_from_markdown(text, plan_id=plan_id)


def list_plans(status_filter: str = "all") -> list[Plan]:
    """List all plans, optionally filtered by status."""
    _ensure_dir()
    plans = []
    for path in sorted(PLANS_DIR.glob("*.md"), reverse=True):
        try:
            p = load_plan(path.stem)
            if p and (status_filter == "all" or p.status == status_filter):
                plans.append(p)
        except Exception as e:
            log.warning("Failed to load plan %s: %s", path.name, e)
    return plans


# ---------------------------------------------------------------------------
# Plan lifecycle — LLM-callable skill functions
# ---------------------------------------------------------------------------

_active_workers: int = 0
_active_workers_lock: asyncio.Lock | None = None


def _get_workers_lock() -> asyncio.Lock:
    """Lazy-init the workers lock inside the running event loop."""
    global _active_workers_lock
    if _active_workers_lock is None:
        _active_workers_lock = asyncio.Lock()
    return _active_workers_lock


def _make_plan_id(goal: str) -> str:
    """Generate a plan ID: date_slug."""
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    # Create a short slug from the goal
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower().strip())[:40].strip("-")
    short = uuid.uuid4().hex[:6]
    return f"{date}_{slug}_{short}"


async def create_plan(goal: str, steps_text: str = "") -> str:
    """Create a new task plan with a goal and steps. Returns plan_id.

    Args:
        goal:       What needs to be accomplished.
        steps_text: Ordered list of steps, one per line. If empty, a single
                    step matching the goal is created.
    """
    # Guard: limit active plans
    active = list_plans("in-progress")
    if len(active) >= MAX_ACTIVE_PLANS:
        return f"❌ Too many active plans ({len(active)}/{MAX_ACTIVE_PLANS}). Complete or cancel some first."

    plan_id = _make_plan_id(goal)

    # Parse steps from text (one per line)
    step_lines = [s.strip() for s in steps_text.strip().split("\n") if s.strip()] if steps_text.strip() else [goal]
    steps = [Step(num=i + 1, description=desc) for i, desc in enumerate(step_lines)]

    plan = Plan(plan_id=plan_id, goal=goal, steps=steps)
    save_plan(plan)
    log.info("Plan created: %s (%d steps)", plan_id, len(steps))
    return f"✅ Created plan `{plan_id}` with {len(steps)} steps."


async def update_plan_step(plan_id: str, step_num: int, status: str, output: str = "") -> str:
    """Update a step's status in an active plan.

    Args:
        plan_id:  The plan identifier.
        step_num: Step number (1-based).
        status:   New status — one of: done, failed, skipped.
        output:   Result text or error message.
    """
    if status not in ("done", "failed", "skipped", "in-progress"):
        return f"❌ Invalid status: {status}. Use: done, failed, skipped, in-progress."

    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."

    step = next((s for s in plan.steps if s.num == step_num), None)
    if not step:
        return f"❌ Step {step_num} not found in plan `{plan_id}`."

    step.status = status
    if output:
        step.output = output[:3000]

    # Auto-complete plan if all steps are done
    if all(s.is_complete for s in plan.steps):
        plan.status = "completed"
        log.info("Plan completed: %s", plan_id)

    save_plan(plan)
    return f"✅ Step {step_num} → {status} in plan `{plan_id}` ({plan.progress_str()})."


async def read_plan(plan_id: str) -> str:
    """Read the current state of a plan including all step statuses.

    Args:
        plan_id: The plan identifier.
    """
    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."

    lines = [
        f"**Plan:** {plan.goal}",
        f"**Status:** {plan.status} ({plan.progress_str()})",
        f"**Created:** {plan.created_at}",
        "",
    ]
    for s in plan.steps:
        icon = {"done": "✅", "failed": "❌", "skipped": "⏭️", "in-progress": "🔄", "pending": "⬜"}.get(s.status, "⬜")
        lines.append(f"{icon} Step {s.num}: {s.description}")
        if s.output:
            lines.append(f"   → {s.output[:200]}")

    return "\n".join(lines)


async def list_plans_skill(status: str = "all") -> str:
    """List plans filtered by status.

    Args:
        status: Filter — one of: in-progress, completed, interrupted, all.
    """
    plans = list_plans(status)
    if not plans:
        return f"📋 No plans found (filter: {status})."

    lines = [f"📋 **Plans** (filter: {status})\n"]
    for p in plans[:20]:
        icon = {"completed": "✅", "interrupted": "⚠️", "failed": "❌"}.get(p.status, "🔄")
        lines.append(f"{icon} `{p.plan_id}` — {p.goal[:60]} ({p.progress_str()})")
    return "\n".join(lines)


async def adjust_plan(plan_id: str, action: str, step_description: str = "", position: int = 0) -> str:
    """Add, remove, or reorder steps in an active plan.

    Args:
        plan_id:          The plan identifier.
        action:           One of: add_step, remove_step, insert_after.
        step_description: Description for the new step (add_step / insert_after).
        position:         Step number to insert after or remove.
    """
    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."

    if action == "add_step":
        new_num = max((s.num for s in plan.steps), default=0) + 1
        plan.steps.append(Step(num=new_num, description=step_description))
        save_plan(plan)
        return f"✅ Added step {new_num}: {step_description}"

    elif action == "remove_step":
        plan.steps = [s for s in plan.steps if s.num != position]
        save_plan(plan)
        return f"✅ Removed step {position} from plan `{plan_id}`."

    elif action == "insert_after":
        new_num = position + 1
        # Shift existing steps
        for s in plan.steps:
            if s.num > position:
                s.num += 1
        plan.steps.append(Step(num=new_num, description=step_description))
        plan.steps.sort(key=lambda s: s.num)
        save_plan(plan)
        return f"✅ Inserted step {new_num}: {step_description}"

    return f"❌ Unknown action: {action}. Use: add_step, remove_step, insert_after."


async def cancel_plan(plan_id: str) -> str:
    """Cancel an active plan, marking it as interrupted.

    Args:
        plan_id: The plan identifier.
    """
    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."

    plan.status = "interrupted"
    # Reset any in-progress steps to pending (safe for resume)
    for s in plan.steps:
        if s.status == "in-progress":
            s.status = "pending"
    save_plan(plan)
    log.info("Plan cancelled: %s", plan_id)
    return f"⚠️ Plan `{plan_id}` marked as interrupted. Use /resume to continue later."


async def resume_plan(plan_id: str) -> str:
    """Resume an interrupted plan from where it left off.

    Args:
        plan_id: The plan identifier.
    """
    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."

    if plan.status not in ("interrupted", "in-progress"):
        return f"⚠️ Plan `{plan_id}` is {plan.status} — cannot resume."

    plan.status = "in-progress"
    save_plan(plan)

    next_step = plan.next_incomplete_step()
    if not next_step:
        plan.status = "completed"
        save_plan(plan)
        return f"✅ Plan `{plan_id}` has no remaining steps — marked completed."

    return (
        f"🔄 Resumed plan `{plan_id}` — {plan.progress_str()} complete.\n"
        f"Next: Step {next_step.num}: {next_step.description}"
    )


def scan_interrupted() -> list[Plan]:
    """Find plans with status=interrupted. Called on startup."""
    return list_plans("interrupted")


# ---------------------------------------------------------------------------
# Autonomous plan execution — runs the plan steps via LLM tool calls
# ---------------------------------------------------------------------------

async def execute_plan(
    plan_id: str,
    on_progress: Any | None = None,
) -> str:
    """Execute a plan's pending steps autonomously via LLM.

    For each pending step whose dependencies are met, the agent sends the step
    description to Gemini as a user message, lets the LLM invoke tools, then
    records the result.  Stops on first failed step or when all steps complete.

    Args:
        plan_id:     The plan identifier.
        on_progress: Optional async callback ``(step_num, status, text) -> None``
                     for reporting live progress (e.g. posting to Discord).

    Returns a summary string.
    """
    plan = load_plan(plan_id)
    if not plan:
        return f"❌ Plan `{plan_id}` not found."
    if plan.status not in ("in-progress", "interrupted"):
        return f"⚠️ Plan `{plan_id}` is {plan.status} — cannot execute."

    plan.status = "in-progress"
    save_plan(plan)

    async def _post(step_num: int, status: str, text: str):
        if on_progress:
            try:
                await on_progress(step_num, status, text)
            except Exception as e:
                log.debug("execute_plan on_progress error: %s", e)

    executed = 0
    failed = 0

    while True:
        # Reload plan each iteration (may have been modified externally)
        plan = load_plan(plan_id)
        if not plan or plan.status != "in-progress":
            break

        step = plan.next_incomplete_step()
        if not step:
            break  # all done or blocked on dependencies

        step.status = "in-progress"
        save_plan(plan)
        await _post(step.num, "in-progress", f"Starting step {step.num}: {step.description}")

        # Build the prompt for this step
        context_parts = []
        if plan.context:
            context_parts.append("Context from previous steps:")
            for k, v in plan.context.items():
                context_parts.append(f"  {k}: {v[:500]}")

        step_prompt = (
            f"You are executing step {step.num} of a plan.\n"
            f"Overall goal: {plan.goal}\n"
            f"This step: {step.description}\n"
        )
        if context_parts:
            step_prompt += "\n".join(context_parts) + "\n"
        step_prompt += (
            "\nExecute this step using the tools available to you. "
            "Return a concise summary of what you found or accomplished."
        )

        try:
            from llm import chat as _llm_chat
            result_text, _, _ = await asyncio.wait_for(
                _llm_chat(step_prompt),
                timeout=PLAN_TIMEOUT,
            )

            step.status = "done"
            step.output = result_text[:3000]
            # Store result in plan context for subsequent steps
            plan.context[f"step_{step.num}_output"] = result_text[:2000]
            save_plan(plan)
            executed += 1
            await _post(step.num, "done", f"✅ Step {step.num} complete")

        except asyncio.TimeoutError:
            step.status = "failed"
            step.output = f"Timed out after {PLAN_TIMEOUT}s"
            save_plan(plan)
            failed += 1
            await _post(step.num, "failed", f"❌ Step {step.num} timed out")
            break

        except Exception as e:
            step.status = "failed"
            step.output = str(e)[:1000]
            save_plan(plan)
            failed += 1
            await _post(step.num, "failed", f"❌ Step {step.num} failed: {e}")
            break

    # Final status
    plan = load_plan(plan_id)
    if plan:
        if all(s.is_complete for s in plan.steps):
            plan.status = "completed"
            save_plan(plan)
        elif failed > 0:
            plan.status = "interrupted"
            save_plan(plan)

    summary = f"Plan `{plan_id}`: {executed} steps executed, {failed} failed"
    if plan:
        summary += f" ({plan.progress_str()} total)"
    return summary


async def execute_plan_skill(plan_id: str) -> str:
    """Execute all pending steps of a plan autonomously.

    Args:
        plan_id: The plan identifier.
    """
    return await execute_plan(plan_id)


# ---------------------------------------------------------------------------
# Exported skill registry
# ---------------------------------------------------------------------------

AGENT_LOOP_SKILLS = {
    "create_plan": create_plan,
    "update_plan_step": update_plan_step,
    "read_plan": read_plan,
    "list_plans": list_plans_skill,
    "adjust_plan": adjust_plan,
    "cancel_plan": cancel_plan,
    "resume_plan": resume_plan,
    "execute_plan": execute_plan_skill,
}
