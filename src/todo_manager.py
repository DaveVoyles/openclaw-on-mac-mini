"""
Personal Todo Manager — JSON-backed per-user task list.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

DATA_PATH = Path("/app/data/todos.json")

Priority = Literal["low", "medium", "high"]


@dataclass
class TodoItem:
    id: str
    title: str
    priority: Priority
    due_date: str | None  # ISO-8601 date string or None
    completed: bool
    created_at: str  # ISO-8601 timestamp
    user_id: int


class TodoManager:
    """CRUD operations for per-user todo items with JSON persistence."""

    def __init__(self, path: Path = DATA_PATH) -> None:
        self._path = path
        self._items: list[TodoItem] = []
        self._load()

    # ── persistence ────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._items = [TodoItem(**item) for item in raw]
            except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
                self._items = []
        else:
            self._items = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([asdict(i) for i in self._items], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── public API ─────────────────────────────────────────

    def add(
        self,
        title: str,
        user_id: int,
        priority: Priority = "medium",
        due_date: str | None = None,
    ) -> TodoItem:
        item = TodoItem(
            id=uuid.uuid4().hex[:8],
            title=title,
            priority=priority,
            due_date=due_date,
            completed=False,
            created_at=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
        )
        self._items.append(item)
        self._save()
        return item

    def complete(self, item_id: str, user_id: int) -> TodoItem | None:
        for item in self._items:
            if item.id == item_id and item.user_id == user_id:
                item.completed = True
                self._save()
                return item
        return None

    def delete(self, item_id: str, user_id: int) -> bool:
        before = len(self._items)
        self._items = [
            i for i in self._items if not (i.id == item_id and i.user_id == user_id)
        ]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def list_for_user(
        self, user_id: int, *, filter_: str = "all"
    ) -> list[TodoItem]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        items = [i for i in self._items if i.user_id == user_id]
        if filter_ == "today":
            items = [i for i in items if i.due_date == today and not i.completed]
        elif filter_ == "overdue":
            items = [
                i
                for i in items
                if i.due_date and i.due_date < today and not i.completed
            ]
        elif filter_ == "done":
            items = [i for i in items if i.completed]
        elif filter_ == "all":
            items = [i for i in items if not i.completed]
        return items

    def list_overdue(self) -> list[TodoItem]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [
            i
            for i in self._items
            if i.due_date and i.due_date < today and not i.completed
        ]
