"""Security tests for core container skill input validation."""

from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_get_docker_stats_uses_default_truncation_limit(monkeypatch):
    long_stats = "NAME\tCPU\tMEM\tNET\n" + ("x" * 2500)

    monkeypatch.setattr(skills, "_run", AsyncMock(return_value=(0, long_stats, "")))

    result = await skills.get_docker_stats()

    assert result.endswith("...")
    assert len(result) == 1900
