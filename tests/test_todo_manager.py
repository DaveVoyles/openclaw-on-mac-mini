"""Tests for src/todo_manager.py — TodoManager CRUD with JSON persistence."""
import json
from datetime import datetime, timezone

import pytest

from todo_manager import TodoItem, TodoManager


@pytest.fixture
def mgr(tmp_path):
    path = tmp_path / "todos.json"
    return TodoManager(path=path)


class TestTodoManagerLoad:
    def test_todo_manager_starts_empty_when_no_file(self, mgr):
        assert mgr._items == []

    def test_todo_manager_loads_existing_data(self, tmp_path):
        path = tmp_path / "todos.json"
        item = {
            "id": "abc12345",
            "title": "Buy milk",
            "priority": "low",
            "due_date": None,
            "completed": False,
            "created_at": "2024-01-01T12:00:00+00:00",
            "user_id": 1,
        }
        path.write_text(json.dumps([item]), encoding="utf-8")
        manager = TodoManager(path=path)
        assert len(manager._items) == 1
        assert manager._items[0].title == "Buy milk"

    def test_todo_manager_bad_json_starts_empty(self, tmp_path):
        path = tmp_path / "todos.json"
        path.write_text("{ broken json", encoding="utf-8")
        manager = TodoManager(path=path)
        assert manager._items == []


class TestTodoManagerAdd:
    def test_add_returns_item(self, mgr):
        item = mgr.add("Write tests", user_id=1)
        assert isinstance(item, TodoItem)
        assert item.title == "Write tests"
        assert item.user_id == 1
        assert item.completed is False

    def test_add_default_priority_is_medium(self, mgr):
        item = mgr.add("Task", user_id=1)
        assert item.priority == "medium"

    def test_add_custom_priority(self, mgr):
        item = mgr.add("Urgent", user_id=1, priority="high")
        assert item.priority == "high"

    def test_todo_manager_add_with_due_date(self, mgr):
        item = mgr.add("Task", user_id=1, due_date="2025-12-31")
        assert item.due_date == "2025-12-31"

    def test_todo_manager_add_persists_to_disk(self, mgr):
        mgr.add("Persist me", user_id=1)
        saved = json.loads(mgr._path.read_text(encoding="utf-8"))
        assert len(saved) == 1
        assert saved[0]["title"] == "Persist me"


class TestTodoManagerComplete:
    def test_todo_manager_complete_marks_done(self, mgr):
        item = mgr.add("Do laundry", user_id=1)
        completed = mgr.complete(item.id, 1)
        assert completed is not None
        assert completed.completed is True

    def test_complete_returns_none_wrong_user(self, mgr):
        item = mgr.add("Do laundry", user_id=1)
        result = mgr.complete(item.id, 2)
        assert result is None

    def test_complete_returns_none_bad_id(self, mgr):
        result = mgr.complete("nonexistent", 1)
        assert result is None

    def test_todo_manager_complete_persists(self, mgr):
        item = mgr.add("Task", user_id=1)
        mgr.complete(item.id, 1)
        saved = json.loads(mgr._path.read_text(encoding="utf-8"))
        assert saved[0]["completed"] is True


class TestTodoManagerDelete:
    def test_todo_manager_delete_returns_true(self, mgr):
        item = mgr.add("Delete me", user_id=1)
        assert mgr.delete(item.id, 1) is True
        assert mgr._items == []

    def test_delete_returns_false_wrong_user(self, mgr):
        item = mgr.add("Mine", user_id=1)
        assert mgr.delete(item.id, 2) is False

    def test_delete_returns_false_bad_id(self, mgr):
        assert mgr.delete("nope", 1) is False

    def test_todo_manager_delete_persists(self, mgr):
        item = mgr.add("Task", user_id=1)
        mgr.delete(item.id, 1)
        saved = json.loads(mgr._path.read_text(encoding="utf-8"))
        assert saved == []


class TestTodoManagerListForUser:
    def test_list_excludes_other_users(self, mgr):
        mgr.add("User1 task", user_id=1)
        mgr.add("User2 task", user_id=2)
        assert len(mgr.list_for_user(1)) == 1

    def test_list_all_excludes_completed(self, mgr):
        item = mgr.add("Done", user_id=1)
        mgr.complete(item.id, 1)
        mgr.add("Pending", user_id=1)
        results = mgr.list_for_user(1, filter_="all")
        assert len(results) == 1
        assert results[0].title == "Pending"

    def test_list_done_returns_completed(self, mgr):
        item = mgr.add("Done", user_id=1)
        mgr.complete(item.id, 1)
        mgr.add("Pending", user_id=1)
        results = mgr.list_for_user(1, filter_="done")
        assert len(results) == 1
        assert results[0].completed is True

    def test_list_today_returns_items_due_today(self, mgr):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        item = mgr.add("Today's task", user_id=1, due_date=today)
        mgr.add("No due date", user_id=1)
        results = mgr.list_for_user(1, filter_="today")
        assert any(i.id == item.id for i in results)

    def test_list_overdue_returns_past_due(self, mgr):
        mgr.add("Past task", user_id=1, due_date="2020-01-01")
        mgr.add("No due date", user_id=1)
        results = mgr.list_for_user(1, filter_="overdue")
        assert len(results) == 1
        assert results[0].due_date == "2020-01-01"

    def test_list_overdue_module_method(self, mgr):
        mgr.add("Old task", user_id=1, due_date="2020-01-01")
        overdue = mgr.list_overdue()
        assert len(overdue) == 1

    def test_empty_when_no_items(self, mgr):
        assert mgr.list_for_user(999) == []
