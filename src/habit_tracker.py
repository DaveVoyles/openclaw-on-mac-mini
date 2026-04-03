"""
Habit Tracker — persistence layer for daily/weekly habit tracking with streaks.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_PATH = Path("/app/data/habits.json")

SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


@dataclass
class Habit:
    id: str
    name: str
    user_id: str
    frequency: str = "daily"  # "daily" or "weekly"
    checkins: list[str] = field(default_factory=list)  # ISO timestamps
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class HabitTracker:
    """Manages habits with JSON persistence."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_PATH
        self._habits: dict[str, Habit] = {}
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                self._habits = {
                    k: Habit(**v) for k, v in raw.items()
                }
            except Exception as e:
                log.error("Failed to load habits: %s", e)
                self._habits = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {k: asdict(v) for k, v in self._habits.items()},
                indent=2,
            )
        )

    # -- public API -----------------------------------------------------------

    def add_habit(self, user_id: str, name: str, frequency: str = "daily") -> Habit:
        habit_id = str(uuid.uuid4())[:8]
        habit = Habit(
            id=habit_id,
            name=name,
            user_id=user_id,
            frequency=frequency,
        )
        self._habits[habit_id] = habit
        self._save()
        return habit

    def checkin(self, user_id: str, name: str) -> Habit | None:
        habit = self._find(user_id, name)
        if not habit:
            return None
        now = datetime.now(timezone.utc).isoformat()
        habit.checkins.append(now)
        self._save()
        return habit

    def get_streak(self, habit: Habit) -> int:
        """Count consecutive days (ending today) with at least one check-in."""
        if not habit.checkins:
            return 0

        dates = sorted(
            {
                datetime.fromisoformat(ts).date()
                for ts in habit.checkins
            },
            reverse=True,
        )
        today = datetime.now(timezone.utc).date()

        # If the most recent check-in isn't today or yesterday, streak is 0
        if dates[0] < today and (today - dates[0]).days > 1:
            return 0

        streak = 0
        expected = today
        for d in dates:
            if d == expected:
                streak += 1
                expected = d - __import__("datetime").timedelta(days=1)
            elif d < expected:
                break
        return streak

    def list_for_user(self, user_id: str) -> list[Habit]:
        return [h for h in self._habits.values() if h.user_id == user_id]

    def delete_habit(self, user_id: str, name: str) -> bool:
        habit = self._find(user_id, name)
        if not habit:
            return False
        del self._habits[habit.id]
        self._save()
        return True

    def sparkline(self, habit: Habit, weeks: int = 8) -> str:
        """Build a sparkline string showing weekly completion rates."""
        from datetime import timedelta

        today = datetime.now(timezone.utc).date()
        checkin_dates = {datetime.fromisoformat(ts).date() for ts in habit.checkins}
        chars = []
        for w in range(weeks - 1, -1, -1):
            week_start = today - timedelta(weeks=w + 1)
            days_in_week = [(week_start + timedelta(days=d)) for d in range(7)]
            hits = sum(1 for d in days_in_week if d in checkin_dates)
            idx = min(int(hits / 7 * (len(SPARKLINE_CHARS) - 1)), len(SPARKLINE_CHARS) - 1)
            chars.append(SPARKLINE_CHARS[idx])
        return "".join(chars)

    # -- helpers --------------------------------------------------------------

    def _find(self, user_id: str, name: str) -> Habit | None:
        name_lower = name.lower()
        for h in self._habits.values():
            if h.user_id == user_id and h.name.lower() == name_lower:
                return h
        return None
