"""Tests for gateway module — request size limits and input validation."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_gateway_rejects_oversized_body():
    """Gateway rejects request bodies larger than 1MB."""
    with patch("gateway.MATON_API_KEY", "test-key"):
        from gateway import gateway_request
        huge_body = {"data": "x" * 2_000_000}
        result = await gateway_request("test-app", "api/endpoint", "POST", body=huge_body)
        assert "too large" in result.lower()


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_app_name():
    """Gateway rejects app names with invalid characters."""
    with patch("gateway.MATON_API_KEY", "test-key"):
        from gateway import gateway_request
        result = await gateway_request("INVALID APP!", "api/endpoint")
        assert "invalid app name" in result.lower()


@pytest.mark.asyncio
async def test_gateway_rejects_long_app_name():
    """Gateway rejects app names exceeding 100 characters."""
    with patch("gateway.MATON_API_KEY", "test-key"):
        from gateway import gateway_request
        result = await gateway_request("a" * 101, "api/endpoint")
        assert "too long" in result.lower()


@pytest.mark.asyncio
async def test_gateway_requires_api_key():
    """Gateway returns helpful error when MATON_API_KEY is missing."""
    with patch("gateway.MATON_API_KEY", ""):
        from gateway import gateway_request
        result = await gateway_request("slack", "api/chat.postMessage")
        assert "MATON_API_KEY" in result


@pytest.mark.asyncio
async def test_gateway_successful_request():
    """Gateway makes successful request and returns formatted result."""
    with patch("gateway.MATON_API_KEY", "test-key"), \
         patch("gateway._http_request", new_callable=AsyncMock) as mock_http:
        mock_http.return_value = {"ok": True, "message": "sent"}
        from gateway import gateway_request
        result = await gateway_request("slack", "api/chat.postMessage", "POST", body={"text": "hello"})
        assert "✅" in result
        assert "slack" in result


@pytest.mark.asyncio
async def test_gateway_handles_timeout():
    """Gateway handles timeout gracefully."""
    import asyncio
    with patch("gateway.MATON_API_KEY", "test-key"), \
         patch("gateway._http_request", new_callable=AsyncMock) as mock_http:
        mock_http.side_effect = asyncio.TimeoutError()
        from gateway import gateway_request
        result = await gateway_request("github", "user/repos")
        assert "timed out" in result.lower()
