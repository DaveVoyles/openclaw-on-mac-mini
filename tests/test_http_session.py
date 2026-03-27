"""
Tests for http_session.py — SessionManager and close_all().

Covers: registration in WeakSet, lazy session creation, close,
close_all bulk shutdown, and WeakSet garbage-collection cleanup.
"""

import gc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import http_session as mod
from http_session import SessionManager, close_all


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the global registry is clean before/after each test."""
    original = set(mod._registry)
    yield
    # Remove anything we added during the test
    for mgr in set(mod._registry) - original:
        mod._registry.discard(mgr)


# ---------------------------------------------------------------------------
# SessionManager creation
# ---------------------------------------------------------------------------


class TestSessionManagerCreation:
    def test_registers_in_weakset(self):
        mgr = SessionManager(name="test-reg")
        assert mgr in mod._registry

    def test_default_name(self):
        mgr = SessionManager()
        assert mgr.name.startswith("session-")

    def test_custom_name(self):
        mgr = SessionManager(name="my-session")
        assert mgr.name == "my-session"


# ---------------------------------------------------------------------------
# get() — lazy session creation
# ---------------------------------------------------------------------------


class TestSessionManagerGet:
    async def test_creates_session_lazily(self):
        mgr = SessionManager(name="test-get")
        assert mgr._session is None
        with patch("http_session.aiohttp.ClientSession") as MockCS:
            mock_session = MagicMock()
            mock_session.closed = False
            MockCS.return_value = mock_session
            session = await mgr.get()
        assert session is mock_session
        assert mgr._session is mock_session

    async def test_reuses_open_session(self):
        mgr = SessionManager(name="test-reuse")
        mock_session = MagicMock()
        mock_session.closed = False
        mgr._session = mock_session
        session = await mgr.get()
        assert session is mock_session


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


class TestSessionManagerClose:
    async def test_close_calls_session_close(self):
        mgr = SessionManager(name="test-close")
        mock_session = AsyncMock()
        mock_session.closed = False
        mgr._session = mock_session
        await mgr.close()
        mock_session.close.assert_awaited_once()
        assert mgr._session is None

    async def test_close_noop_when_no_session(self):
        mgr = SessionManager(name="test-close-noop")
        await mgr.close()  # should not raise


# ---------------------------------------------------------------------------
# close_all()
# ---------------------------------------------------------------------------


class TestCloseAll:
    async def test_closes_all_registered(self):
        mgr1 = SessionManager(name="all-1")
        mgr2 = SessionManager(name="all-2")
        s1, s2 = AsyncMock(), AsyncMock()
        s1.closed = False
        s2.closed = False
        mgr1._session = s1
        mgr2._session = s2
        await close_all()
        s1.close.assert_awaited_once()
        s2.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# WeakSet cleanup
# ---------------------------------------------------------------------------


class TestWeakSetCleanup:
    def test_weakset_gc(self):
        mgr = SessionManager(name="gc-test")
        assert mgr in mod._registry
        ref_id = id(mgr)
        del mgr
        gc.collect()
        # After deletion + GC, the WeakSet entry should be gone
        remaining_ids = {id(m) for m in mod._registry}
        assert ref_id not in remaining_ids
