"""Skills facade - single entry point for all OpenClaw skills.

This module provides a cleaner interface to skills, reducing coupling between
the bot core and individual skill modules.
"""

from skills import SKILLS

__all__ = ["SKILLS", "get_skill", "list_skills", "skill_exists"]


def get_skill(name: str) -> dict | None:
    """Get a skill by name."""
    return SKILLS.get(name)


def list_skills() -> list[str]:
    """List all available skill names."""
    return sorted(SKILLS.keys())


def skill_exists(name: str) -> bool:
    """Check if a skill exists."""
    return name in SKILLS
