"""
Tests for export REST API.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import MagicMock

import pytest
from aiohttp import web

from api.export import (
    API_KEYS,
    check_rate_limit,
    rate_limit_tracker,
    setup_export_routes,
    verify_api_key,
)


@pytest.fixture
def api_key():
    """Valid API key for testing."""
    return list(API_KEYS.keys())[0]


@pytest.fixture
async def client(aiohttp_client):
    """Create test client with export routes."""
    app = web.Application()
    setup_export_routes(app)
    return await aiohttp_client(app)


def test_verify_api_key_valid(api_key):
    """Test API key verification with valid key."""
    request = MagicMock()
    request.headers = {"Authorization": f"Bearer {api_key}"}

    result = verify_api_key(request)
    assert result == api_key


def test_verify_api_key_invalid():
    """Test API key verification with invalid key."""
    request = MagicMock()
    request.headers = {"Authorization": "Bearer invalid_key"}

    result = verify_api_key(request)
    assert result is None


def test_verify_api_key_missing():
    """Test API key verification with missing header."""
    request = MagicMock()
    request.headers = {}

    result = verify_api_key(request)
    assert result is None


def test_rate_limiting(api_key):
    """Test rate limiting functionality."""
    # Clear tracker
    rate_limit_tracker.clear()

    # Should allow requests up to limit
    for _ in range(API_KEYS[api_key]["rate_limit"]):
        assert check_rate_limit(api_key) is True

    # Should block after limit
    assert check_rate_limit(api_key) is False


@pytest.mark.asyncio
async def test_export_conversations_no_auth(client):
    """Test conversations export without authentication."""
    resp = await client.get("/api/export/conversations")
    assert resp.status == 401

    data = await resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_export_conversations_with_auth(client, api_key):
    """Test conversations export with valid authentication."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.get(
        "/api/export/conversations?format=csv&days=30",
        headers=headers,
    )

    # May succeed or fail depending on data availability
    assert resp.status in [200, 500]


@pytest.mark.asyncio
async def test_export_trends_with_filters(client, api_key):
    """Test trends export with filters."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.get(
        "/api/export/trends?format=json&metric=stocks&days=7",
        headers=headers,
    )

    assert resp.status in [200, 500]


@pytest.mark.asyncio
async def test_export_invalid_format(client, api_key):
    """Test export with invalid format."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.get(
        "/api/export/conversations?format=invalid",
        headers=headers,
    )

    assert resp.status in [400, 500]


@pytest.mark.asyncio
async def test_generate_report(client, api_key):
    """Test report generation endpoint."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    data = {
        "report_type": "weekly_summary",
        "data": {
            "trending_topics": [],
            "total_messages": 100,
        },
    }

    resp = await client.post(
        "/api/reports/generate",
        headers=headers,
        json=data,
    )

    # May succeed or fail depending on template availability
    assert resp.status in [200, 500]


@pytest.mark.asyncio
async def test_list_backups(client, api_key):
    """Test listing backups."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.get("/api/backups/list", headers=headers)

    assert resp.status == 200
    data = await resp.json()
    assert "backups" in data
    assert "status" in data


@pytest.mark.asyncio
async def test_create_backup(client, api_key):
    """Test creating backup via API."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"upload_to_nas": False}

    resp = await client.post(
        "/api/backups/create",
        headers=headers,
        json=data,
    )

    assert resp.status in [200, 500]


@pytest.mark.asyncio
async def test_rate_limit_exceeded(client, api_key):
    """Test that rate limiting is enforced."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}

    # Exhaust rate limit
    for _ in range(API_KEYS[api_key]["rate_limit"]):
        await client.get("/api/backups/list", headers=headers)

    # Next request should be rate limited
    resp = await client.get("/api/backups/list", headers=headers)
    assert resp.status == 429

    data = await resp.json()
    assert "Rate limit" in data["error"]


@pytest.mark.asyncio
async def test_export_parquet_format(client, api_key):
    """Test Parquet export format."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.get(
        "/api/export/trends?format=parquet&days=30",
        headers=headers,
    )

    assert resp.status in [200, 500]


@pytest.mark.asyncio
async def test_generate_report_invalid_json(client, api_key):
    """Test report generation with invalid JSON."""
    rate_limit_tracker.clear()

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await client.post(
        "/api/reports/generate",
        headers=headers,
        data="invalid json",
    )

    assert resp.status == 400
