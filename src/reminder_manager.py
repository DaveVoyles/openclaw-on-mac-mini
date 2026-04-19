"""Personal reminders with time parsing and JSON persistence."""

import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from utils import atomic_write

log = logging.getLogger(__name__)
REMINDERS_FILE = Path(os.getenv("MEMORY_DIR", "/memory")) / "reminders.json"


@dataclass
class Reminder:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_id: int = 0
    channel_id: int = 0
    message: str = ""
    fire_at: float = 0.0  # Unix timestamp
    recurring: str = ""  # "" = one-shot, "daily", "weekly"
    created_at: float = field(default_factory=time.time)
    fired: bool = False


class ReminderManager:
    def __init__(self) -> None:
        self._reminders: list[Reminder] = []
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if REMINDERS_FILE.exists():
            try:
                data = json.loads(REMINDERS_FILE.read_text())
                self._reminders = [Reminder(**r) for r in data]
            except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError) as e:
                log.warning("Failed to load reminders: %s", e)

    def _save(self) -> None:
        atomic_write(
            REMINDERS_FILE,
            json.dumps([asdict(r) for r in self._reminders], indent=2),
        )

    # -- public API ----------------------------------------------------------

    def add(
        self,
        user_id: int,
        channel_id: int,
        message: str,
        fire_at: float,
        recurring: str = "",
    ) -> Reminder:
        r = Reminder(
            user_id=user_id,
            channel_id=channel_id,
            message=message,
            fire_at=fire_at,
            recurring=recurring,
        )
        self._reminders.append(r)
        self._save()
        return r

    def cancel(self, reminder_id: str, user_id: int) -> bool:
        before = len(self._reminders)
        self._reminders = [
            r
            for r in self._reminders
            if not (r.id == reminder_id and r.user_id == user_id)
        ]
        if len(self._reminders) < before:
            self._save()
            return True
        return False

    def list_for_user(self, user_id: int) -> list[Reminder]:
        return [r for r in self._reminders if r.user_id == user_id and not r.fired]

    def get_due(self) -> list[Reminder]:
        now = time.time()
        return [r for r in self._reminders if r.fire_at <= now and not r.fired]

    def mark_fired(self, reminder_id: str) -> None:
        for r in self._reminders:
            if r.id == reminder_id:
                if r.recurring == "daily":
                    r.fire_at += 86400
                elif r.recurring == "weekly":
                    r.fire_at += 604800
                else:
                    r.fired = True
                break
        self._save()


# ---------------------------------------------------------------------------
# Time expression parser
# ---------------------------------------------------------------------------

def parse_time_expression(expr: str) -> float | None:
    """Parse 'in 30m', 'at 3pm', 'at 15:00', 'in 2h' into Unix timestamp."""
    expr = expr.strip().lower()
    now = time.time()

    # "in Xm", "in Xh", "in Xs"
    match = re.match(r"in\s+(\d+)\s*(s|sec|m|min|h|hr|hour)", expr)
    if match:
        val = int(match.group(1))
        unit = match.group(2)[0]
        multiplier = {"s": 1, "m": 60, "h": 3600}
        return now + val * multiplier.get(unit, 60)

    # "at 3pm", "at 3:30pm", "at 15:00"
    match = re.match(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", expr)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        target = datetime.now().replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if target.timestamp() <= now:
            target += timedelta(days=1)  # tomorrow
        return target.timestamp()

    return None


# Singleton — importable from anywhere
reminder_manager = ReminderManager()
