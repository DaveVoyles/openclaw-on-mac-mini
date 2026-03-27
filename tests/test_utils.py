"""
Tests for utils.py — atomic_write and safe_call helpers.

Covers crash-safe file I/O and the async timeout wrapper.
"""

import asyncio

import pytest

from utils import atomic_write, safe_call


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path):
        target = tmp_path / "output.json"
        atomic_write(target, '{"ok": true}')
        assert target.read_text() == '{"ok": true}'

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c" / "deep.txt"
        atomic_write(target, "nested")
        assert target.read_text() == "nested"

    def test_atomic_write_overwrites_existing(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"

    def test_atomic_write_no_partial_on_error(self, tmp_path, monkeypatch):
        """If the write fails mid-way the original file is preserved."""
        target = tmp_path / "safe.txt"
        target.write_text("original")

        # Make os.fsync raise so the rename never happens
        import os
        monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))

        with pytest.raises(OSError, match="disk full"):
            atomic_write(target, "should not appear")

        assert target.read_text() == "original"


# ---------------------------------------------------------------------------
# safe_call
# ---------------------------------------------------------------------------


class TestSafeCall:
    async def test_safe_call_returns_result(self):
        async def ok():
            return "hello"

        result = await safe_call(ok(), label="test")
        assert result == "hello"

    async def test_safe_call_timeout_returns_fallback(self):
        async def slow():
            await asyncio.sleep(60)

        result = await safe_call(slow(), timeout=0, label="slow-op")
        assert "timed out" in result

    async def test_safe_call_exception_returns_fallback(self):
        async def boom():
            raise ValueError("kaboom")

        result = await safe_call(boom(), label="boom")
        assert "timed out" in result or "boom" in result

    async def test_safe_call_custom_fallback(self):
        async def fail():
            raise RuntimeError("oops")

        result = await safe_call(fail(), fallback="custom msg", label="op")
        assert result == "custom msg"

    async def test_safe_call_default_fallback(self):
        async def fail():
            raise RuntimeError("oops")

        result = await safe_call(fail(), label="my-operation")
        assert "my-operation" in result
