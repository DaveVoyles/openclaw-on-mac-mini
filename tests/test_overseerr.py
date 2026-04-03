"""Tests for overseerr.py — Overseerr API skill functions."""

from unittest.mock import patch

import pytest

import overseerr as mod


@pytest.fixture(autouse=True)
def _patch_overseerr_config(monkeypatch):
    """Ensure tests never hit a real Overseerr instance."""
    monkeypatch.setattr(mod, "OVERSEERR_URL", "http://fake-overseerr:5055")
    monkeypatch.setattr(mod, "OVERSEERR_API_KEY", "test-api-key")


# ---------------------------------------------------------------------------
# get_request_stats
# ---------------------------------------------------------------------------

class TestGetRequestStats:
    @pytest.mark.asyncio
    async def test_parses_counts_from_api(self):
        """Verify stat counts are extracted from paginated responses."""
        fake_responses = [
            {"pageInfo": {"results": 50}},   # all
            {"pageInfo": {"results": 3}},    # pending
            {"pageInfo": {"results": 20}},   # approved
            {"pageInfo": {"results": 25}},   # available
            {"pageInfo": {"results": 2}},    # processing
        ]

        call_count = 0

        async def mock_get(path):
            nonlocal call_count
            resp = fake_responses[call_count]
            call_count += 1
            return resp

        with patch.object(mod, "_get", side_effect=mock_get):
            result = await mod.get_request_stats()

        assert "50" in result        # total
        assert "3" in result         # pending
        assert "20" in result        # approved
        assert "2" in result         # processing
        assert "25" in result        # available

    @pytest.mark.asyncio
    async def test_handles_api_error_string(self):
        """If _get returns an error string, stats should show '?' for counts."""
        async def mock_get(path):
            return "Request timed out (10s)"

        with patch.object(mod, "_get", side_effect=mock_get):
            result = await mod.get_request_stats()

        assert "?" in result


# ---------------------------------------------------------------------------
# approve_request
# ---------------------------------------------------------------------------

class TestApproveRequest:
    @pytest.mark.asyncio
    async def test_success(self):
        async def mock_post(path):
            return {}  # 204-style empty response

        with patch.object(mod, "_post", side_effect=mock_post):
            result = await mod.approve_request(42)

        assert "✅" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_failure(self):
        async def mock_post(path):
            return "HTTP 404: not found"

        with patch.object(mod, "_post", side_effect=mock_post):
            result = await mod.approve_request(99)

        assert "❌" in result
        assert "99" in result

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await mod.approve_request("not-a-number")
        assert "❌" in result
        assert "Invalid" in result


# ---------------------------------------------------------------------------
# deny_request
# ---------------------------------------------------------------------------

class TestDenyRequest:
    @pytest.mark.asyncio
    async def test_success(self):
        async def mock_post(path):
            return {}

        with patch.object(mod, "_post", side_effect=mock_post):
            result = await mod.deny_request(7)

        assert "declined" in result.lower() or "❌" in result

    @pytest.mark.asyncio
    async def test_failure(self):
        async def mock_post(path):
            return "HTTP 500: internal error"

        with patch.object(mod, "_post", side_effect=mock_post):
            result = await mod.deny_request(7)

        assert "Failed" in result


# ---------------------------------------------------------------------------
# get_pending_requests
# ---------------------------------------------------------------------------

class TestGetPendingRequests:
    @pytest.mark.asyncio
    async def test_no_pending(self):
        async def mock_get(path):
            return {"results": [], "pageInfo": {"results": 0}}

        with patch.object(mod, "_get", side_effect=mock_get):
            result = await mod.get_pending_requests()

        assert "No pending" in result

    @pytest.mark.asyncio
    async def test_with_pending(self):
        async def mock_get(path):
            return {
                "results": [{
                    "id": 1,
                    "type": "movie",
                    "media": {"title": "Dune"},
                    "requestedBy": {"displayName": "Dave"},
                }],
                "pageInfo": {"results": 1},
            }

        with patch.object(mod, "_get", side_effect=mock_get):
            result = await mod.get_pending_requests()

        assert "Dune" in result
        assert "Dave" in result
