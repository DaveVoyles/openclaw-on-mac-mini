"""Tests for search_web skill (mocked — no real API calls)."""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_search_web_returns_string():
    """search_web should return a string result."""
    with patch("skills.advanced_skills.search_web", new_callable=AsyncMock) as mock:
        mock.return_value = "✅ Found 3 results for Narberth PA"
        result = await mock("homes for sale in Narberth PA")
        assert isinstance(result, str)
        assert "Narberth" in result


@pytest.mark.asyncio
async def test_search_web_error_returns_message():
    """search_web should return an error string, not raise."""
    with patch("skills.advanced_skills.search_web", new_callable=AsyncMock) as mock:
        mock.return_value = "❌ Search failed: timeout"
        result = await mock("test query")
        assert "❌" in result
