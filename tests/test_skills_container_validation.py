"""Security tests for core container skill input validation."""

import pytest

import skills


@pytest.mark.asyncio
async def test_restart_container_rejects_invalid_name():
    result = await skills.restart_container("sonarr; rm -rf /")
    assert "Invalid container name" in result


@pytest.mark.asyncio
async def test_get_container_logs_rejects_invalid_name():
    result = await skills.get_container_logs("$(whoami)")
    assert "Invalid container name" in result
