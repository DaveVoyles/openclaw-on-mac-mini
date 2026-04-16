"""Unit tests for memory_thread_persistence.py — ThreadPersistence class."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import memory_thread_persistence as mtp_module
from memory_thread_persistence import ThreadPersistence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conv(history=None, user_name="TestUser"):
    conv = MagicMock()
    conv.history = history if history is not None else [{"role": "user", "parts": ["hi"]}]
    conv.user_name = user_name
    return conv


def _write_thread_file(threads_dir: Path, filename: str, payload: dict) -> Path:
    threads_dir.mkdir(parents=True, exist_ok=True)
    p = threads_dir / filename
    p.write_text(json.dumps(payload))
    return p


# ---------------------------------------------------------------------------
# _thread_path
# ---------------------------------------------------------------------------

class TestThreadPath:
    def test_returns_path_under_threads_dir(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        path = tp._thread_path(1, "mythread")
        assert path.parent == threads.resolve()

    def test_sanitizes_special_chars_in_name(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        path = tp._thread_path(1, "my thread!!")
        assert "!" not in path.name
        assert " " not in path.name

    def test_truncates_name_to_32_chars(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        path = tp._thread_path(1, "a" * 50)
        # The safe name part should be at most 32 chars
        stem = path.stem  # e.g. "1_aaaa...aaa"
        name_part = stem.split("_", 1)[1]
        assert len(name_part) <= 32


# ---------------------------------------------------------------------------
# save_thread
# ---------------------------------------------------------------------------

class TestSaveThread:
    def test_invalid_name_returns_error(self):
        tp = ThreadPersistence()
        result = tp.save_thread(_make_conv(), 1, "invalid name!")
        assert "❌" in result

    def test_none_conv_returns_error(self):
        tp = ThreadPersistence()
        result = tp.save_thread(None, 1, "validname")
        assert "❌" in result

    def test_empty_history_returns_error(self):
        tp = ThreadPersistence()
        conv = _make_conv(history=[])
        result = tp.save_thread(conv, 1, "validname")
        assert "❌" in result

    def test_successful_save_returns_ok_message(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        with patch("memory_thread_persistence._atomic_write") as mock_aw:
            tp = ThreadPersistence()
            conv = _make_conv()
            result = tp.save_thread(conv, 42, "mythread")
            assert "✅" in result
            mock_aw.assert_called_once()

    def test_atomic_write_failure_returns_error(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        with patch("memory_thread_persistence._atomic_write", side_effect=OSError("disk full")):
            tp = ThreadPersistence()
            conv = _make_conv()
            result = tp.save_thread(conv, 42, "mythread")
            assert "❌" in result


# ---------------------------------------------------------------------------
# load_thread
# ---------------------------------------------------------------------------

class TestLoadThread:
    def test_invalid_name_returns_error(self):
        tp = ThreadPersistence()
        conv, msg = tp.load_thread(1, "bad name!!")
        assert conv is None
        assert "❌" in msg

    def test_missing_file_returns_error(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        conv, msg = tp.load_thread(1, "noexist")
        assert conv is None
        assert "❌" in msg

    def test_expired_thread_returns_warning(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        monkeypatch.setattr(mtp_module, "CONTEXT_TTL", 1)  # 1 second TTL
        tp = ThreadPersistence()
        # Write a thread file with an old timestamp
        payload = {
            "name": "oldthread",
            "user_name": "User",
            "saved_at": time.time() - 100,
            "history": [{"role": "user", "parts": ["hello"]}],
        }
        path = threads / "1_oldthread.json"
        threads.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        conv, msg = tp.load_thread(1, "oldthread")
        assert conv is None
        assert "expired" in msg.lower() or "⚠️" in msg

    def test_valid_thread_returns_conversation(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        monkeypatch.setattr(mtp_module, "CONTEXT_TTL", 999999)
        tp = ThreadPersistence()
        payload = {
            "name": "goodthread",
            "user_name": "Alice",
            "saved_at": time.time(),
            "history": [{"role": "user", "parts": ["hello"]}],
        }
        threads.mkdir(parents=True, exist_ok=True)
        path = threads / "1_goodthread.json"
        path.write_text(json.dumps(payload))
        conv, msg = tp.load_thread(1, "goodthread")
        assert conv is not None
        assert "✅" in msg
        assert conv.user_name == "Alice"


# ---------------------------------------------------------------------------
# auto_save_thread
# ---------------------------------------------------------------------------

class TestAutoSaveThread:
    def test_no_conv_does_nothing(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        with patch("memory_thread_persistence._atomic_write") as mock_aw:
            tp = ThreadPersistence()
            tp.auto_save_thread(None, 1, 100)
            mock_aw.assert_not_called()

    def test_empty_history_does_nothing(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        with patch("memory_thread_persistence._atomic_write") as mock_aw:
            tp = ThreadPersistence()
            conv = _make_conv(history=[])
            tp.auto_save_thread(conv, 1, 100)
            mock_aw.assert_not_called()

    def test_valid_conv_writes_auto_slot(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        with patch("memory_thread_persistence._atomic_write") as mock_aw:
            tp = ThreadPersistence()
            conv = _make_conv()
            tp.auto_save_thread(conv, 1, 123456789)
            mock_aw.assert_called_once()
            path_used = mock_aw.call_args[0][0]
            assert "auto" in path_used.name


# ---------------------------------------------------------------------------
# list_threads
# ---------------------------------------------------------------------------

class TestListThreads:
    def test_no_threads_returns_empty_message(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        result = tp.list_threads(999)
        assert "No saved threads" in result

    def test_threads_listed_with_name(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        threads.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": "mythread",
            "saved_at": time.time(),
            "history": [{"role": "user", "parts": ["hello"]}],
        }
        (threads / "1_mythread.json").write_text(json.dumps(payload))
        tp = ThreadPersistence()
        result = tp.list_threads(1)
        assert "mythread" in result

    def test_unreadable_file_handled_gracefully(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        threads.mkdir(parents=True, exist_ok=True)
        (threads / "1_broken.json").write_text("{{not json}}")
        tp = ThreadPersistence()
        result = tp.list_threads(1)
        assert "unreadable" in result or "1_broken" in result


# ---------------------------------------------------------------------------
# delete_thread
# ---------------------------------------------------------------------------

class TestDeleteThread:
    def test_invalid_name_returns_error(self):
        tp = ThreadPersistence()
        result = tp.delete_thread(1, "bad name!!")
        assert "❌" in result

    def test_missing_file_returns_error(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        tp = ThreadPersistence()
        result = tp.delete_thread(1, "noexist")
        assert "❌" in result

    def test_existing_file_deleted_successfully(self, tmp_path, monkeypatch):
        threads = tmp_path / "threads"
        monkeypatch.setattr(mtp_module, "THREADS_DIR", threads)
        threads.mkdir(parents=True, exist_ok=True)
        payload = {"name": "delme", "history": []}
        p = threads / "1_delme.json"
        p.write_text(json.dumps(payload))
        tp = ThreadPersistence()
        result = tp.delete_thread(1, "delme")
        assert "🗑️" in result
        assert not p.exists()
