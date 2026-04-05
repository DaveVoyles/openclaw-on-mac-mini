"""
Tests for health checker.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from health_checker import (
    HealthChecker,
    HealthStatus,
    CheckType,
    get_health_checker,
    check_disk_space,
    check_memory,
    check_database,
    check_api_endpoint,
)


@pytest.fixture
def checker():
    """Create a fresh health checker."""
    return HealthChecker()


def test_health_checker_singleton():
    """Test that get_health_checker returns singleton instance."""
    checker1 = get_health_checker()
    checker2 = get_health_checker()
    assert checker1 is checker2


@pytest.mark.asyncio
async def test_check_liveness(checker):
    """Test liveness check."""
    result = await checker.check_liveness()
    
    assert result.name == "liveness"
    assert result.status == HealthStatus.HEALTHY
    assert "running" in result.message.lower()


@pytest.mark.asyncio
async def test_check_readiness_not_ready(checker):
    """Test readiness check when not ready."""
    results = await checker.check_readiness()
    
    assert "readiness" in results
    assert results["readiness"].status == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_check_readiness_ready(checker):
    """Test readiness check when ready."""
    # Register a simple check
    async def simple_check():
        from health_checker import HealthCheckResult, HealthStatus
        return HealthCheckResult("simple", HealthStatus.HEALTHY, "OK")
    
    checker.register_check("simple", simple_check)
    checker.mark_ready()
    results = await checker.check_readiness()
    
    # Should have our registered check
    assert "simple" in results


@pytest.mark.asyncio
async def test_check_startup(checker):
    """Test startup check."""
    # Before startup complete
    result = await checker.check_startup()
    assert result.status == HealthStatus.DEGRADED
    
    # After startup complete
    checker.mark_startup_complete()
    result = await checker.check_startup()
    assert result.status == HealthStatus.HEALTHY


def test_register_check(checker):
    """Test registering a custom check."""
    async def custom_check():
        return HealthCheckResult(
            name="custom",
            status=HealthStatus.HEALTHY,
            message="Custom check passed",
        )
    
    checker.register_check("custom", custom_check)
    assert "custom" in checker._checks


@pytest.mark.asyncio
async def test_readiness_with_custom_check(checker):
    """Test readiness with custom check."""
    async def passing_check():
        from health_checker import HealthCheckResult
        return HealthCheckResult(
            name="custom_pass",
            status=HealthStatus.HEALTHY,
            message="Pass",
        )
    
    async def failing_check():
        from health_checker import HealthCheckResult
        return HealthCheckResult(
            name="custom_fail",
            status=HealthStatus.UNHEALTHY,
            message="Fail",
        )
    
    checker.register_check("custom_pass", passing_check)
    checker.register_check("custom_fail", failing_check)
    checker.mark_ready()
    
    results = await checker.check_readiness()
    
    assert results["custom_pass"].status == HealthStatus.HEALTHY
    assert results["custom_fail"].status == HealthStatus.UNHEALTHY


def test_get_overall_status(checker):
    """Test overall status calculation."""
    from health_checker import HealthCheckResult
    
    # No results yet
    assert checker.get_overall_status() == HealthStatus.DEGRADED
    
    # All healthy
    checker._last_results = {
        "check1": HealthCheckResult("check1", HealthStatus.HEALTHY, "OK"),
        "check2": HealthCheckResult("check2", HealthStatus.HEALTHY, "OK"),
    }
    assert checker.get_overall_status() == HealthStatus.HEALTHY
    
    # One degraded
    checker._last_results["check2"] = HealthCheckResult(
        "check2", HealthStatus.DEGRADED, "Warning"
    )
    assert checker.get_overall_status() == HealthStatus.DEGRADED
    
    # One unhealthy
    checker._last_results["check2"] = HealthCheckResult(
        "check2", HealthStatus.UNHEALTHY, "Error"
    )
    assert checker.get_overall_status() == HealthStatus.UNHEALTHY


@pytest.mark.asyncio
async def test_self_heal(checker):
    """Test self-healing capabilities."""
    from health_checker import HealthCheckResult
    
    checker._last_results = {
        "database": HealthCheckResult("database", HealthStatus.UNHEALTHY, "Error"),
        "disk": HealthCheckResult("disk", HealthStatus.UNHEALTHY, "Full"),
    }
    
    actions = await checker.self_heal()
    
    assert len(actions) > 0
    assert any("database" in action.lower() for action in actions)


@pytest.mark.asyncio
async def test_check_disk_space():
    """Test disk space check."""
    result = await check_disk_space(threshold_percent=90.0)
    
    assert result.name == "disk_space"
    assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
    assert "used_percent" in result.metadata
    assert "free_gb" in result.metadata


@pytest.mark.asyncio
async def test_check_memory():
    """Test memory check."""
    result = await check_memory(threshold_percent=90.0)
    
    assert result.name == "memory"
    assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED, HealthStatus.UNHEALTHY]
    assert "used_percent" in result.metadata
    assert "available_gb" in result.metadata


@pytest.mark.asyncio
async def test_check_database_missing(tmp_path):
    """Test database check with missing database."""
    db_path = tmp_path / "missing.db"
    result = await check_database(db_path)
    
    assert result.status == HealthStatus.UNHEALTHY
    assert "not found" in result.message.lower()


@pytest.mark.asyncio
async def test_check_database_exists(tmp_path):
    """Test database check with existing database."""
    import sqlite3
    
    db_path = tmp_path / "test.db"
    
    # Create a simple database
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (id INTEGER)")
    conn.close()
    
    result = await check_database(db_path)
    
    assert result.status == HealthStatus.HEALTHY
    assert "table_count" in result.metadata


@pytest.mark.asyncio
async def test_check_api_endpoint_success():
    """Test API endpoint check - success case."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_get = AsyncMock(return_value=mock_response)
        mock_session = AsyncMock()
        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        mock_session_class.return_value = mock_session
        
        result = await check_api_endpoint("test", "http://example.com/health")
        
        assert result.status == HealthStatus.HEALTHY
        assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_check_api_endpoint_timeout():
    """Test API endpoint check - timeout case."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        mock_session_class.return_value = mock_session
        
        result = await check_api_endpoint("test", "http://example.com/health", timeout=1.0)
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "timeout" in result.message.lower()


@pytest.mark.asyncio
async def test_check_api_endpoint_error():
    """Test API endpoint check - error case."""
    with patch("aiohttp.ClientSession") as mock_session:
        mock_session.return_value.__aenter__.return_value.get.side_effect = Exception("Connection failed")
        
        result = await check_api_endpoint("test", "http://example.com/health")
        
        assert result.status == HealthStatus.UNHEALTHY
        assert "unreachable" in result.message.lower()


@pytest.mark.asyncio
async def test_check_duration_recorded(checker):
    """Test that check duration is recorded."""
    async def slow_check():
        from health_checker import HealthCheckResult
        await asyncio.sleep(0.1)
        return HealthCheckResult("slow", HealthStatus.HEALTHY, "OK")
    
    checker.register_check("slow", slow_check)
    checker.mark_ready()
    
    results = await checker.check_readiness()
    
    assert "slow" in results
    assert results["slow"].duration_ms is not None
    assert results["slow"].duration_ms >= 100  # At least 100ms


@pytest.mark.asyncio
async def test_check_error_handling(checker):
    """Test that check errors are handled gracefully."""
    async def failing_check():
        raise ValueError("Check failed!")
    
    checker.register_check("failing", failing_check)
    checker.mark_ready()
    
    results = await checker.check_readiness()
    
    assert "failing" in results
    assert results["failing"].status == HealthStatus.UNHEALTHY
    assert "failed" in results["failing"].message.lower()


def test_mark_startup_complete(checker):
    """Test marking startup as complete."""
    assert not checker._startup_complete
    
    checker.mark_startup_complete()
    
    assert checker._startup_complete


def test_mark_ready(checker):
    """Test marking application as ready."""
    assert not checker._ready
    
    checker.mark_ready()
    
    assert checker._ready
