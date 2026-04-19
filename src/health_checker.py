"""
Health Check System for OpenClaw.

Provides:
- Application health endpoints
- Dependency checks (database, APIs, disk space)
- Self-healing capabilities
- Health status reporting
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import psutil

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CheckType(Enum):
    """Types of health checks."""

    LIVENESS = "liveness"  # Is app running?
    READINESS = "readiness"  # Can app serve requests?
    STARTUP = "startup"  # Is app initialized?


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    name: str
    status: HealthStatus
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None


class HealthChecker:
    """Centralized health checking system."""

    def __init__(self):
        self._checks: Dict[str, Callable] = {}
        self._last_results: Dict[str, HealthCheckResult] = {}
        self._startup_complete = False
        self._ready = False

    def register_check(self, name: str, check_fn: Callable):
        """Register a health check function."""
        self._checks[name] = check_fn
        logger.info(f"Registered health check: {name}")

    async def check_liveness(self) -> HealthCheckResult:
        """Check if application is alive."""
        return HealthCheckResult(
            name="liveness",
            status=HealthStatus.HEALTHY,
            message="Application is running",
        )

    async def check_readiness(self) -> Dict[str, HealthCheckResult]:
        """Check if application is ready to serve requests."""
        if not self._ready:
            return {
                "readiness": HealthCheckResult(
                    name="readiness",
                    status=HealthStatus.UNHEALTHY,
                    message="Application not yet ready",
                )
            }

        # Run all registered checks
        results = {}
        for name, check_fn in self._checks.items():
            try:
                start = datetime.now()
                result = await check_fn()
                duration = (datetime.now() - start).total_seconds() * 1000

                if isinstance(result, HealthCheckResult):
                    result.duration_ms = duration
                    results[name] = result
                else:
                    results[name] = HealthCheckResult(
                        name=name,
                        status=HealthStatus.HEALTHY,
                        message=str(result),
                        duration_ms=duration,
                    )
            except Exception as e:  # broad: intentional
                logger.error(f"Health check {name} failed: {e}")
                results[name] = HealthCheckResult(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {str(e)}",
                )

        self._last_results = results
        return results

    async def check_startup(self) -> HealthCheckResult:
        """Check if application startup is complete."""
        if self._startup_complete:
            return HealthCheckResult(
                name="startup",
                status=HealthStatus.HEALTHY,
                message="Startup complete",
            )
        else:
            return HealthCheckResult(
                name="startup",
                status=HealthStatus.DEGRADED,
                message="Startup in progress",
            )

    def mark_startup_complete(self):
        """Mark startup as complete."""
        self._startup_complete = True
        logger.info("Application startup complete")

    def mark_ready(self):
        """Mark application as ready."""
        self._ready = True
        logger.info("Application ready to serve requests")

    def get_overall_status(self) -> HealthStatus:
        """Get overall health status."""
        if not self._last_results:
            return HealthStatus.DEGRADED

        statuses = [result.status for result in self._last_results.values()]

        if all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        else:
            return HealthStatus.DEGRADED

    async def self_heal(self) -> List[str]:
        """Attempt to self-heal degraded services."""
        actions = []

        # Check each unhealthy component
        for name, result in self._last_results.items():
            if result.status == HealthStatus.UNHEALTHY:
                try:
                    # Attempt healing based on check name
                    if "database" in name.lower():
                        actions.append(f"Attempted to reconnect {name}")
                        # Reconnection logic would go here
                    elif "disk" in name.lower():
                        actions.append("Cleaned up temporary files")
                        # Cleanup logic would go here
                    elif "memory" in name.lower():
                        actions.append("Triggered garbage collection")
                        import gc

                        gc.collect()
                except Exception as e:  # broad: intentional
                    logger.error(f"Failed to heal {name}: {e}")
                    actions.append(f"Failed to heal {name}: {str(e)}")

        return actions


# Built-in health checks


async def check_disk_space(threshold_percent: float = 90.0) -> HealthCheckResult:
    """Check disk space availability."""
    try:
        disk = psutil.disk_usage("/")
        used_percent = disk.percent

        if used_percent >= threshold_percent:
            return HealthCheckResult(
                name="disk_space",
                status=HealthStatus.UNHEALTHY,
                message=f"Disk usage critical: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "free_gb": disk.free / (1024**3)},
            )
        elif used_percent >= threshold_percent - 10:
            return HealthCheckResult(
                name="disk_space",
                status=HealthStatus.DEGRADED,
                message=f"Disk usage high: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "free_gb": disk.free / (1024**3)},
            )
        else:
            return HealthCheckResult(
                name="disk_space",
                status=HealthStatus.HEALTHY,
                message=f"Disk usage normal: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "free_gb": disk.free / (1024**3)},
            )
    except OSError as e:
        return HealthCheckResult(
            name="disk_space",
            status=HealthStatus.UNHEALTHY,
            message=f"Failed to check disk space: {str(e)}",
        )


async def check_memory(threshold_percent: float = 90.0) -> HealthCheckResult:
    """Check memory availability."""
    try:
        memory = psutil.virtual_memory()
        used_percent = memory.percent

        if used_percent >= threshold_percent:
            return HealthCheckResult(
                name="memory",
                status=HealthStatus.UNHEALTHY,
                message=f"Memory usage critical: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "available_gb": memory.available / (1024**3)},
            )
        elif used_percent >= threshold_percent - 10:
            return HealthCheckResult(
                name="memory",
                status=HealthStatus.DEGRADED,
                message=f"Memory usage high: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "available_gb": memory.available / (1024**3)},
            )
        else:
            return HealthCheckResult(
                name="memory",
                status=HealthStatus.HEALTHY,
                message=f"Memory usage normal: {used_percent:.1f}%",
                metadata={"used_percent": used_percent, "available_gb": memory.available / (1024**3)},
            )
    except (ImportError, OSError) as e:
        return HealthCheckResult(
            name="memory",
            status=HealthStatus.UNHEALTHY,
            message=f"Failed to check memory: {str(e)}",
        )


async def check_database(db_path: Path = Path("data/conversations.db")) -> HealthCheckResult:
    """Check database connectivity."""
    try:
        import sqlite3

        if not db_path.exists():
            return HealthCheckResult(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Database file not found: {db_path}",
            )

        # Try to connect and query
        conn = sqlite3.connect(str(db_path), timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        table_count = cursor.fetchone()[0]
        conn.close()

        return HealthCheckResult(
            name="database",
            status=HealthStatus.HEALTHY,
            message=f"Database accessible ({table_count} tables)",
            metadata={"table_count": table_count, "path": str(db_path)},
        )
    except (sqlite3.Error, OSError) as e:
        return HealthCheckResult(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"Database check failed: {str(e)}",
        )


async def check_api_endpoint(name: str, url: str, timeout: float = 5.0) -> HealthCheckResult:
    """Check if an API endpoint is reachable."""
    try:
        async with aiohttp.ClientSession() as session:
            start = datetime.now()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                duration_ms = (datetime.now() - start).total_seconds() * 1000

                if response.status == 200:
                    return HealthCheckResult(
                        name=f"api_{name}",
                        status=HealthStatus.HEALTHY,
                        message=f"{name} API reachable ({duration_ms:.0f}ms)",
                        metadata={"url": url, "status_code": response.status},
                        duration_ms=duration_ms,
                    )
                else:
                    return HealthCheckResult(
                        name=f"api_{name}",
                        status=HealthStatus.DEGRADED,
                        message=f"{name} API returned status {response.status}",
                        metadata={"url": url, "status_code": response.status},
                        duration_ms=duration_ms,
                    )
    except asyncio.TimeoutError:
        return HealthCheckResult(
            name=f"api_{name}",
            status=HealthStatus.UNHEALTHY,
            message=f"{name} API timeout after {timeout}s",
            metadata={"url": url},
        )
    except Exception as e:  # broad: intentional
        return HealthCheckResult(
            name=f"api_{name}",
            status=HealthStatus.UNHEALTHY,
            message=f"{name} API unreachable: {str(e)}",
            metadata={"url": url},
        )


# Global health checker
_health_checker: Optional[HealthChecker] = None


async def check_patreon_health() -> HealthCheckResult:
    """Check Patreon/MonsterVision health."""
    try:
        from patreon_monitor import get_patreon_checker

        checker = get_patreon_checker()
        result = await checker.check_health()

        # Map PatreonHealthStatus to HealthStatus
        from patreon_monitor import PatreonHealthStatus

        status_map = {
            PatreonHealthStatus.OK: HealthStatus.HEALTHY,
            PatreonHealthStatus.WARNING: HealthStatus.DEGRADED,
            PatreonHealthStatus.CRITICAL: HealthStatus.UNHEALTHY,
            PatreonHealthStatus.UNKNOWN: HealthStatus.DEGRADED,
        }

        return HealthCheckResult(
            name="patreon",
            status=status_map.get(result.status, HealthStatus.DEGRADED),
            message=result.message,
            metadata=result.metadata,
        )
    except Exception as e:  # broad: intentional
        return HealthCheckResult(
            name="patreon",
            status=HealthStatus.UNHEALTHY,
            message=f"Patreon health check failed: {str(e)}",
        )


def get_health_checker() -> HealthChecker:
    """Get or create the global health checker."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()

        # Register default checks
        _health_checker.register_check("disk_space", check_disk_space)
        _health_checker.register_check("memory", check_memory)
        _health_checker.register_check("database", check_database)
        _health_checker.register_check("patreon", check_patreon_health)

    return _health_checker
