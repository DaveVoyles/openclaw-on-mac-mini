"""
OpenClaw Goal Tracker — Phase 16: Proactive Goal Tracking
Detects statements of intent from conversations and tracks them as active goals.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("openclaw.goal_tracker")

GOALS_FILE = Path("/memory/goals.json")

# Patterns that suggest a user has a goal/intent
_GOAL_PATTERNS = re.compile(
    r"\b(i'?m?\s+)?(looking\s+for|searching\s+for|trying\s+to|want\s+to|need\s+to|planning\s+to"
    r"|hoping\s+to|going\s+to|thinking\s+about|considering|interested\s+in"
    r"|working\s+on|building|creating|learning|studying|researching)\b",
    re.IGNORECASE,
)


def detect_goal(message: str) -> bool:
    """Return True if the message contains a statement of intent/goal."""
    if len(message) < 20:
        return False
    return bool(_GOAL_PATTERNS.search(message))


def _load_goals() -> list[dict]:
    """Load goals from disk."""
    if GOALS_FILE.exists():
        try:
            return json.loads(GOALS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_goals(goals: list[dict]) -> None:
    """Save goals to disk."""
    GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOALS_FILE.write_text(json.dumps(goals, indent=2, default=str))


async def extract_and_store_goal(
    user_message: str,
    user_id: int,
) -> Optional[str]:
    """Extract a goal from a message and store it.

    Uses LLM to extract a concise goal statement.
    Returns the goal text if one was extracted, None otherwise.
    """
    import google.generativeai as genai
    from config import cfg

    if not cfg.google_api_key:
        return None

    prompt = (
        "Extract the user's goal or intention from this message as a single concise statement. "
        "If there is no clear goal, reply with exactly: NONE\n\n"
        f"Message: {user_message}\n\n"
        "Goal (one line):"
    )

    try:
        model = genai.GenerativeModel(
            model_name=cfg.llm_model,
            generation_config=genai.GenerationConfig(
                max_output_tokens=100,
                temperature=0.1,
            ),
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: model.generate_content(prompt)
        )

        goal_text = response.text.strip()
        if not goal_text or goal_text.upper() == "NONE":
            return None

        # Check for duplicate goals
        goals = _load_goals()
        for existing in goals:
            if existing.get("user_id") == user_id:
                # Simple similarity check: if >60% word overlap, it's a duplicate
                existing_words = set(existing["goal"].lower().split())
                new_words = set(goal_text.lower().split())
                if existing_words and new_words:
                    overlap = len(existing_words & new_words) / max(
                        len(existing_words), len(new_words)
                    )
                    if overlap > 0.6:
                        log.debug(
                            "Goal duplicate detected (%.0f%% overlap): %s",
                            overlap * 100,
                            goal_text,
                        )
                        # Update timestamp
                        existing["last_mentioned"] = time.time()
                        existing["mention_count"] = existing.get("mention_count", 1) + 1
                        _save_goals(goals)
                        return None

        # Store new goal
        goal = {
            "goal": goal_text,
            "user_id": user_id,
            "created_at": time.time(),
            "last_mentioned": time.time(),
            "mention_count": 1,
            "status": "active",
        }
        goals.append(goal)
        _save_goals(goals)

        log.info("New goal detected for user %s: %s", user_id, goal_text)
        return goal_text

    except Exception as e:
        log.debug("Goal extraction failed (non-fatal): %s", e)
        return None


def get_active_goals(user_id: int | None = None) -> list[dict]:
    """Get active goals, optionally filtered by user."""
    goals = _load_goals()
    active = [g for g in goals if g.get("status") == "active"]
    if user_id is not None:
        active = [g for g in active if g.get("user_id") == user_id]
    return active


def complete_goal(goal_text: str, user_id: int) -> bool:
    """Mark a goal as completed."""
    goals = _load_goals()
    for g in goals:
        if g.get("user_id") == user_id and g.get("goal", "").lower() == goal_text.lower():
            g["status"] = "completed"
            g["completed_at"] = time.time()
            _save_goals(goals)
            return True
    return False


def dismiss_goal(goal_text: str, user_id: int) -> bool:
    """Dismiss/remove a goal."""
    goals = _load_goals()
    for g in goals:
        if g.get("user_id") == user_id and g.get("goal", "").lower() == goal_text.lower():
            g["status"] = "dismissed"
            _save_goals(goals)
            return True
    return False


def format_goals_for_briefing(user_id: int | None = None) -> str:
    """Format active goals for morning briefing injection."""
    active = get_active_goals(user_id)
    if not active:
        return ""

    lines = ["[Active Goals]"]
    for g in active[:5]:
        mentions = g.get("mention_count", 1)
        lines.append(f"- {g['goal']} (mentioned {mentions}x)")
    return "\n".join(lines)
