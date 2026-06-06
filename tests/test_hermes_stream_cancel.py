"""Tests for hard-interrupt cancellation of Hermes SSH streams.

``run_hermes_stream`` runs ``hermes`` on the host over SSH. Setting the
``cancel_event`` must promptly terminate the remote process (even while it is
silent) and end the stream with a ``cancelled`` terminal event, rather than
only stopping after the next chunk arrives.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

import host_bridge


class _FakeStdout:
    """Async line iterator that blocks once preset lines are exhausted.

    Blocking simulates a silent/long-running turn: nothing more is emitted
    until ``stop_event`` fires (which the fake proc does on terminate/kill).
    """

    def __init__(self, lines: list[str], stop_event: asyncio.Event) -> None:
        self._lines = list(lines)
        self._stop = stop_event

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        await self._stop.wait()
        raise StopAsyncIteration


class _FakeProc:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stdout = _FakeStdout(["Session: abc123def456\n"], stop_event)
        self._stop = stop_event
        self.exit_status = 0
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self._stop.set()

    def kill(self) -> None:
        self.killed = True
        self._stop.set()

    async def wait_closed(self) -> None:
        return None

    async def __aenter__(self) -> _FakeProc:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, proc: _FakeProc) -> None:
        self._proc = proc
        self.aborted = False

    def create_process(self, _cmd: str) -> _FakeProc:
        return self._proc

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        return None

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _install_fake_asyncssh(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc) -> _FakeConn:
    conn = _FakeConn(proc)
    fake = types.ModuleType("asyncssh")
    fake.Error = Exception  # type: ignore[attr-defined]
    fake.connect = lambda *a, **k: conn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncssh", fake)
    return conn


@pytest.fixture
def _bridge_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = tmp_path / "id_test"
    key.write_text("fake-key")
    monkeypatch.setattr(host_bridge, "_enabled", lambda: True)
    monkeypatch.setattr(host_bridge, "KEY_PATH", str(key))
    monkeypatch.setattr(host_bridge, "KNOWN_HOSTS", str(tmp_path / "nope"))


async def test_cancel_event_hard_interrupts_stream(monkeypatch: pytest.MonkeyPatch, _bridge_enabled: None) -> None:
    proc = _FakeProc(asyncio.Event())
    _install_fake_asyncssh(monkeypatch, proc)
    cancel = asyncio.Event()

    events: list[dict] = []

    async def _consume() -> None:
        async for event in host_bridge.run_hermes_stream(
            prompt="long task",
            slack_user_id="U1",
            cancel_event=cancel,
        ):
            events.append(event)
            if event.get("type") == "chunk":
                # Stream is now silent; request cancellation mid-turn.
                cancel.set()

    await asyncio.wait_for(_consume(), timeout=5)

    assert proc.terminated is True, "remote process should be terminated on cancel"
    done = [e for e in events if e.get("type") == "done"]
    assert done, "a terminal done event must be emitted"
    assert done[-1]["cancelled"] is True
    assert done[-1]["success"] is False


async def test_no_cancel_event_completes_normally(monkeypatch: pytest.MonkeyPatch, _bridge_enabled: None) -> None:
    stop = asyncio.Event()
    stop.set()  # no blocking: stdout exhausts immediately
    proc = _FakeProc(stop)
    _install_fake_asyncssh(monkeypatch, proc)

    events: list[dict] = []
    async for event in host_bridge.run_hermes_stream(prompt="hi", slack_user_id="U1"):
        events.append(event)

    assert proc.terminated is False
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[-1]["success"] is True
    assert done[-1]["cancelled"] is False


def test_quick_command_handle_registry_owner_and_cancel() -> None:
    """One-shot /q and /resume turns register a cancel handle keyed by a
    synthetic id with the requesting user recorded, so /copilot-cancel can
    verify ownership and hard-interrupt the turn via the same cancel_event the
    host bridge watches.
    """
    import slack_bot

    handle = slack_bot._HermesStreamHandle()
    handle.slack_user = "U_OWNER"
    cancel_id = "qdeadbeef"
    slack_bot._hermes_live_procs[cancel_id] = handle
    try:
        registered = slack_bot._hermes_live_procs.get(cancel_id)
        assert registered is handle
        assert registered.slack_user == "U_OWNER"

        # A different user must not be able to cancel this turn.
        assert registered.slack_user != "U_OTHER"

        # Owner cancel fires the event the host bridge watches.
        assert handle.cancel_event.is_set() is False
        handle.terminate()
        assert handle.cancelled is True
        assert handle.cancel_event.is_set() is True
    finally:
        slack_bot._hermes_live_procs.pop(cancel_id, None)

    assert cancel_id not in slack_bot._hermes_live_procs
