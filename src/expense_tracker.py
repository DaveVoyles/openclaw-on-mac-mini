"""
Expense Tracker — persistence layer for category-based expense logging.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_PATH = Path("/app/data/expenses.json")

CATEGORIES = ["food", "transport", "entertainment", "shopping", "bills", "health", "other"]

CATEGORY_EMOJIS = {
    "food": "🍔",
    "transport": "🚗",
    "entertainment": "🎮",
    "shopping": "🛍️",
    "bills": "📄",
    "health": "💊",
    "other": "📦",
}

BAR_CHARS = "░▒▓█"


@dataclass
class Expense:
    id: str
    amount: float
    category: str
    note: str
    user_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ExpenseTracker:
    """Manages expenses with JSON persistence."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_PATH
        self._expenses: list[Expense] = []
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                self._expenses = [Expense(**e) for e in raw]
            except Exception as e:
                log.error("Failed to load expenses: %s", e)
                self._expenses = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([asdict(e) for e in self._expenses], indent=2)
        )

    # -- public API -----------------------------------------------------------

    def add(
        self,
        user_id: str,
        amount: float,
        category: str,
        note: str = "",
    ) -> Expense:
        expense = Expense(
            id=str(uuid.uuid4())[:8],
            amount=round(amount, 2),
            category=category.lower(),
            note=note,
            user_id=user_id,
        )
        self._expenses.append(expense)
        self._save()
        return expense

    def list_for_user(self, user_id: str, days: int = 7) -> list[Expense]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return [
            e
            for e in self._expenses
            if e.user_id == user_id
            and datetime.fromisoformat(e.timestamp) >= cutoff
        ]

    def summary_by_category(
        self, user_id: str, days: int = 7
    ) -> dict[str, float]:
        expenses = self.list_for_user(user_id, days)
        totals: dict[str, float] = {}
        for e in expenses:
            totals[e.category] = totals.get(e.category, 0) + e.amount
        return dict(sorted(totals.items(), key=lambda x: x[1], reverse=True))

    def summary_by_period(
        self, user_id: str, period: str = "week"
    ) -> dict[str, float]:
        days_map = {"week": 7, "month": 30, "year": 365}
        days = days_map.get(period, 7)
        return self.summary_by_category(user_id, days)

    def delete(self, user_id: str, expense_id: str) -> bool:
        for i, e in enumerate(self._expenses):
            if e.id == expense_id and e.user_id == user_id:
                self._expenses.pop(i)
                self._save()
                return True
        return False

    def format_bar(self, amount: float, max_amount: float, width: int = 10) -> str:
        """Render a text bar for category visualization."""
        if max_amount == 0:
            return "░" * width
        ratio = amount / max_amount
        filled = int(ratio * width)
        return "█" * filled + "░" * (width - filled)
