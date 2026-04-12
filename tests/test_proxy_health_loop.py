"""Unit tests for start_proxy_health_loop() / stop_proxy_health_loop().

Covers:
1. Idempotent start -- calling start twice returns the same task
2. Stop cancels -- task is cancelled and _health_task is None after stop
3. Restart after stop -- fresh (different) task created, not cancelled
4. Exception from check_proxy_health is swallowed
5. _proxy_healthy updated -- proxy_is_healthy() reflects latest check_proxy_health() return value
"""

import asyncio
import asyncio as _real_asyncio  # capture reference BEFORE any monkeypatching
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports so we avoid heavy optional deps
# ---------------------------------------------------------------------------
_genai_mock = MagicMock()
_genai_mock.types.ThinkingConfig = MagicMock()
_genai_mock.types.ContentDict = dict
_genai_mock.types.GenerateContentConfig = MagicMock()
_genai_mock.types.Tool = MagicMock()
_genai_mock.types.FunctionDeclaration = MagicMock()
_genai_mock.types.Schema = MagicMock()
_genai_mock.types.Type = MagicMock()
_genai_mock.types.Part = MagicMock()
_genai_mock.types.FunctionResponse = MagicMock()
_genai_mock.types.Content = MagicMock()
_genai_mock.types.Blob = MagicMock()
_genai_mock.Client = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)

import llm.providers as providers  # noqa: E402

# Save a reference to the real asyncio.sleep BEFORE any test monkeypatches it.
# providers.asyncio IS the asyncio module, so patching providers.asyncio.sleep
# also patches asyncio.sleep globally. We need the unpatched version to yield
# control to the event loop inside our custom sleep replacements.
_REAL_SLEEP = _real_asyncio.sleep

# ---------------------------------------------------------------------------
# Autouse fixture: stop any running health task after every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_health_task():
    """Cancel and clear _health_task after each test to prevent task leaks."""
    yield
    providers.stop_proxy_health_loop()


# ---------------------------------------------------------------------------
# Helper fixture: replace asyncio.sleep in providers with a zero-delay version
# ---------------------------------------------------------------------------


@pytest.fixture()
def fast_sleep(monkeypatch):
    """Replace asyncio.sleep in providers with a zero-delay coroutine.

    Uses _REAL_SLEEP so that CancelledError is still properly propagated when
    task.cancel() is called -- unlike AsyncMock which does not raise it.
    """
    async def _fast(delay):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(providers.asyncio, "sleep", _fast)


# ---------------------------------------------------------------------------
# Test 1 -- Idempotent start
# ---------------------------------------------------------------------------


async def test_start_is_idempotent(fast_sleep, monkeypatch):
    """Calling start_proxy_health_loop() twice must return the same Task object."""
    monkeypatch.setattr(providers, "check_proxy_health", AsyncMock(return_value=True))

    task_a = providers.start_proxy_health_loop()
    task_b = providers.start_proxy_health_loop()

    assert task_a is task_b, "Second call should return the existing task, not a new one"
    assert isinstance(task_a, asyncio.Task)


# ---------------------------------------------------------------------------
# Test 2 -- Stop cancels the task and clears _health_task
# ---------------------------------------------------------------------------


async def test_stop_cancels_task(fast_sleep, monkeypatch):
    """stop_proxy_health_loop() cancels the task and sets _health_task to None."""
    monkeypatch.setattr(providers, "check_proxy_health", AsyncMock(return_value=True))

    task = providers.start_proxy_health_loop()
    # Give the loop one tick to reach the first await (the patched sleep)
    await _REAL_SLEEP(0)
    assert not task.done(), "Task should be running after start"

    providers.stop_proxy_health_loop()

    # Give the event loop several ticks to process the CancelledError
    for _ in range(5):
        await _REAL_SLEEP(0)

    assert providers._health_task is None
    assert task.cancelled(), "Task should be cancelled after stop"


# ---------------------------------------------------------------------------
# Test 3 -- Restart after stop returns a new, non-cancelled task
# ---------------------------------------------------------------------------


async def test_restart_after_stop_returns_new_task(fast_sleep, monkeypatch):
    """After stop, start returns a fresh task distinct from the cancelled one."""
    monkeypatch.setattr(providers, "check_proxy_health", AsyncMock(return_value=True))

    task_first = providers.start_proxy_health_loop()
    await _REAL_SLEEP(0)

    providers.stop_proxy_health_loop()
    for _ in range(5):
        await _REAL_SLEEP(0)

    task_second = providers.start_proxy_health_loop()

    assert task_second is not task_first, "Should be a brand-new Task after restart"
    assert not task_second.cancelled(), "New task must not be cancelled"
    assert not task_second.done(), "New task should still be running"


# ---------------------------------------------------------------------------
# Test 4 -- Exception from check_proxy_health is swallowed
# ---------------------------------------------------------------------------


async def test_exception_in_health_check_is_swallowed(monkeypatch):
    """Loop must not propagate exceptions raised by check_proxy_health()."""
    done_event = asyncio.Event()
    call_count = 0
    sleep_calls = 0

    async def counting_sleep(delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            done_event.set()
        await _REAL_SLEEP(0)  # real sleep -- no recursion

    async def exploding_check(**kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("health check boom")

    monkeypatch.setattr(providers.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(providers, "check_proxy_health", exploding_check)

    providers.start_proxy_health_loop()

    # Wait until the loop has gone through at least 2 sleep iterations
    await asyncio.wait_for(done_event.wait(), timeout=2.0)

    # The exception must have been swallowed -- check_proxy_health was called at least once
    assert call_count >= 1, "check_proxy_health should have been called despite raising"


# ---------------------------------------------------------------------------
# Test 5 -- _proxy_healthy is updated by check_proxy_health return value
# ---------------------------------------------------------------------------


async def test_proxy_healthy_reflects_check_result(monkeypatch):
    """proxy_is_healthy() must mirror the value last returned by check_proxy_health()."""
    results = [True, False]
    call_index = 0
    done_event = asyncio.Event()

    async def controlled_check(**kwargs):
        nonlocal call_index
        value = results[min(call_index, len(results) - 1)]
        providers._proxy_healthy = value
        call_index += 1
        if call_index >= 2:
            done_event.set()
        return value

    async def fast_sleep_impl(delay):
        await _REAL_SLEEP(0)  # real sleep -- no recursion

    monkeypatch.setattr(providers.asyncio, "sleep", fast_sleep_impl)
    monkeypatch.setattr(providers, "check_proxy_health", controlled_check)

    providers._proxy_healthy = False
    providers.start_proxy_health_loop()

    # Wait until both True and False have been injected
    await asyncio.wait_for(done_event.wait(), timeout=2.0)

    # After two calls: True then False -- final state must be False
    assert providers.proxy_is_healthy() is False, (
        "proxy_is_healthy() should reflect the latest check_proxy_health() result"
    )
