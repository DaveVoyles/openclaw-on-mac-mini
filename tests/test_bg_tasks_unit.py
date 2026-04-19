"""
test_bg_tasks_unit.py — Unit tests for src/bg_tasks.py

Tests the managed_task() helper and related utilities.
Async tests use pytest-asyncio (asyncio_mode = "auto" via pyproject.toml).
"""
from __future__ import annotations

import asyncio
import importlib as _importlib
import logging
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy external deps before importing bg_tasks
# (mirrors the pattern in tests/test_bg_tasks.py)
# ---------------------------------------------------------------------------


def _try_stub(mod_name: str) -> None:
    if mod_name in sys.modules:
        return
    try:
        _importlib.import_module(mod_name)
    except (ImportError, ModuleNotFoundError):
        sys.modules[mod_name] = MagicMock()


if "discord" not in sys.modules:
    try:
        _importlib.import_module("discord")
    except (ImportError, ModuleNotFoundError):
        _discord_stub = MagicMock()
        sys.modules["discord"] = _discord_stub
        sys.modules["discord.ext"] = MagicMock()
        sys.modules["discord.ext.commands"] = MagicMock()

for _mod in [
    "google", "google.genai", "google.genai.types",
    "aiohttp", "pandas", "scipy", "scipy.stats",
    "psutil", "prometheus_client",
]:
    _try_stub(_mod)

# metrics_collector must return a real-ish collector stub so bg_tasks doesn't crash
if "metrics_collector" not in sys.modules:
    _mc = MagicMock()
    _mc.get_collector.return_value = MagicMock()
    sys.modules["metrics_collector"] = _mc

from bg_tasks import get_active_task_count, managed_task  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop() -> None:
    pass


# ---------------------------------------------------------------------------
# managed_task — happy path
# ---------------------------------------------------------------------------


async def test_managed_task_completes_normally():
    """managed_task runs a coroutine to completion."""
    result: list[int] = []

    async def _work() -> None:
        result.append(1)

    t = managed_task(_work(), name="test-normal")
    await asyncio.sleep(0.05)
    assert result == [1]
    assert t.done()


# ---------------------------------------------------------------------------
# managed_task — timeout
# ---------------------------------------------------------------------------


async def test_managed_task_timeout_logs_warning(caplog: pytest.LogCaptureFixture):
    """managed_task cancels and logs a WARNING when the timeout is exceeded."""
    async def _slow() -> None:
        await asyncio.sleep(10)

    with caplog.at_level(logging.WARNING):
        t = managed_task(_slow(), name="timeout-test", timeout=0.05)
        await asyncio.sleep(0.2)

    assert "timed out" in caplog.text


# ---------------------------------------------------------------------------
# managed_task — exception logging
# ---------------------------------------------------------------------------


async def test_managed_task_exception_is_logged(caplog: pytest.LogCaptureFixture):
    """managed_task logs exceptions as ERROR without re-raising."""
    async def _boom() -> None:
        raise ValueError("test error")

    with caplog.at_level(logging.ERROR):
        t = managed_task(_boom(), name="error-test")
        await asyncio.sleep(0.05)

    assert "test error" in caplog.text
    assert t.done()


# ---------------------------------------------------------------------------
# managed_task — error_callback
# ---------------------------------------------------------------------------


async def test_managed_task_error_callback_called():
    """error_callback receives the exception when the task raises."""
    caught: list[Exception] = []

    async def _boom() -> None:
        raise RuntimeError("cb-test")

    managed_task(_boom(), name="cb-test", error_callback=caught.append, timeout=5)
    await asyncio.sleep(0.1)

    assert len(caught) == 1
    assert isinstance(caught[0], RuntimeError)
    assert str(caught[0]) == "cb-test"


# ---------------------------------------------------------------------------
# managed_task — task tracking (increment / decrement)
# ---------------------------------------------------------------------------


async def test_active_task_count_increments_and_decrements():
    """Task count increases while task runs, decreases after completion."""
    before = get_active_task_count()

    async def _slow() -> None:
        await asyncio.sleep(0.1)

    managed_task(_slow(), name="count-test")
    assert get_active_task_count() == before + 1

    await asyncio.sleep(0.2)
    assert get_active_task_count() == before


# ---------------------------------------------------------------------------
# managed_task — CancelledError not logged as error
# ---------------------------------------------------------------------------


async def test_managed_task_cancelled_does_not_log_error(caplog: pytest.LogCaptureFixture):
    """CancelledError is expected; it must NOT be logged at ERROR level."""
    async def _long() -> None:
        await asyncio.sleep(10)

    with caplog.at_level(logging.ERROR):
        t = managed_task(_long(), name="cancel-test", timeout=None)
        t.cancel()
        await asyncio.sleep(0.05)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not error_records


# ---------------------------------------------------------------------------
# managed_task — timeout=None (no asyncio.wait_for wrapper)
# ---------------------------------------------------------------------------


async def test_managed_task_no_timeout():
    """timeout=None runs the task without any asyncio.wait_for wrapping."""
    done: list[bool] = []

    async def _work() -> None:
        done.append(True)

    managed_task(_work(), name="no-timeout", timeout=None)
    await asyncio.sleep(0.05)
    assert done == [True]


# ---------------------------------------------------------------------------
# managed_task — error_callback exception is swallowed
# ---------------------------------------------------------------------------


async def test_managed_task_error_callback_exception_swallowed(caplog: pytest.LogCaptureFixture):
    """If error_callback itself raises, the exception is silently ignored."""
    def _bad_callback(exc: Exception) -> None:
        raise RuntimeError("callback boom")

    async def _boom() -> None:
        raise ValueError("original")

    # Should not raise and should not cause an unhandled exception
    t = managed_task(_boom(), name="callback-exc-test", error_callback=_bad_callback)
    await asyncio.sleep(0.1)
    assert t.done()


# ---------------------------------------------------------------------------
# get_active_task_count — basic sanity
# ---------------------------------------------------------------------------


async def test_get_active_task_count_returns_int():
    """get_active_task_count() always returns a non-negative int."""
    count = get_active_task_count()
    assert isinstance(count, int)
    assert count >= 0


# ---------------------------------------------------------------------------
# managed_task — multiple concurrent tasks tracked correctly
# ---------------------------------------------------------------------------


async def test_multiple_managed_tasks_tracked():
    """All concurrent tasks are reflected in the active count."""
    before = get_active_task_count()

    async def _hold() -> None:
        await asyncio.sleep(0.15)

    managed_task(_hold(), name="multi-1")
    managed_task(_hold(), name="multi-2")
    managed_task(_hold(), name="multi-3")

    assert get_active_task_count() >= before + 3

    await asyncio.sleep(0.3)
    assert get_active_task_count() == before
