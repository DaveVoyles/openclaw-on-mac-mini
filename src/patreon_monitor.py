"""
Patreon/MonsterVision Health Monitoring Module.

Monitors:
- MonsterVision container status
- Patreon cookie freshness
- Download failures
- API availability

Provides structured health status for alerting and auto-recovery.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

from config import TIMEOUT_FAST, cfg

log = logging.getLogger(__name__)


class PatreonHealthStatus(Enum):
    """Patreon health status levels."""

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class PatreonHealthResult:
    """Result of Patreon health check."""

    status: PatreonHealthStatus
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)


class PatreonHealthChecker:
    """Comprehensive health checker for Patreon/MonsterVision."""

    def __init__(self):
        self.api_url = f"http://{cfg.docker_host_ip}:{cfg.monstervision_port}/api/status"
        self.container_name = "monstervision"
        self._last_check: Optional[PatreonHealthResult] = None

    async def check_health(self) -> PatreonHealthResult:
        """
        Run comprehensive health check.

        Returns:
            PatreonHealthResult with status, issues, and action items
        """
        issues: List[str] = []
        metadata: Dict[str, Any] = {}
        action_items: List[str] = []

        # Check 1: Container status
        container_status = await self._check_container_status()
        metadata["container_status"] = container_status

        if container_status == "stopped":
            issues.append("Container is stopped")
            action_items.append("Container needs to be started: `docker start monstervision`")
            result = PatreonHealthResult(
                status=PatreonHealthStatus.CRITICAL,
                message="MonsterVision container is stopped",
                metadata=metadata,
                issues=issues,
                action_items=action_items,
            )
            self._last_check = result
            return result

        if container_status not in ("running", "healthy"):
            issues.append(f"Container is {container_status}")
            action_items.append("Container may need restart: `docker restart monstervision`")

        # Check 2: API availability
        api_available, api_data = await self._check_api()
        metadata["api_available"] = api_available

        if not api_available:
            issues.append("MonsterVision API is unreachable")
            action_items.append("Check container logs: `docker logs monstervision`")
            result = PatreonHealthResult(
                status=PatreonHealthStatus.CRITICAL,
                message="MonsterVision API unreachable",
                metadata=metadata,
                issues=issues,
                action_items=action_items,
            )
            self._last_check = result
            return result

        # Check 3: Cookie freshness
        cookie_age_hours = await self._check_cookie_age(api_data)
        metadata["cookie_age_hours"] = cookie_age_hours

        if cookie_age_hours is not None:
            if cookie_age_hours > 72:
                issues.append(f"Patreon cookies expired ({cookie_age_hours:.0f}h old)")
                action_items.extend(self._get_cookie_refresh_steps())
            elif cookie_age_hours > 48:
                issues.append(f"Patreon cookies expiring soon ({cookie_age_hours:.0f}h old)")
                action_items.append("Consider refreshing cookies before they expire")

        # Check 4: Failed downloads
        failed_count = api_data.get("failed", 0) if api_data else 0
        metadata["failed_downloads"] = failed_count

        if failed_count >= 3:
            issues.append(f"{failed_count} failed downloads detected")
            action_items.append("Check for cookie expiration or network issues")
        elif failed_count > 0:
            issues.append(f"{failed_count} failed download(s)")

        # Check 5: Error patterns from logs
        error_patterns = await self._check_error_patterns()
        if error_patterns:
            metadata["error_patterns"] = error_patterns
            for pattern in error_patterns:
                if pattern not in [i.lower() for i in issues]:
                    issues.append(pattern)

        # Check 6: Disk space
        disk_space_ok = await self._check_disk_space()
        metadata["disk_space_ok"] = disk_space_ok
        if not disk_space_ok:
            issues.append("Low disk space detected")
            action_items.append("Free up disk space in /app/downloads")

        # Determine overall status
        status = self._determine_status(cookie_age_hours, failed_count, container_status, api_available)

        # Build message
        if status == PatreonHealthStatus.OK:
            message = "Patreon downloads are healthy"
        elif status == PatreonHealthStatus.WARNING:
            message = f"Patreon downloads need attention: {', '.join(issues[:2])}"
        else:
            message = f"Patreon downloads are failing: {', '.join(issues[:2])}"

        result = PatreonHealthResult(
            status=status,
            message=message,
            metadata=metadata,
            issues=issues,
            action_items=action_items,
        )

        self._last_check = result
        return result

    async def _check_container_status(self) -> str:
        """Check MonsterVision container status using docker inspect."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}}",
                self.container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0:
                return stdout.decode().strip()
            else:
                log.warning(f"docker inspect failed: {stderr.decode()}")
                return "unknown"
        except asyncio.TimeoutError:
            log.warning("docker inspect timed out")
            return "unknown"
        except (OSError, UnicodeDecodeError) as e:
            log.error(f"Error checking container status: {e}")
            return "unknown"

    async def _check_api(self) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Check MonsterVision API and return (available, data)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.api_url, timeout=aiohttp.ClientTimeout(total=TIMEOUT_FAST)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return True, data
                    else:
                        log.warning(f"MonsterVision API returned {resp.status}")
                        return False, None
        except asyncio.TimeoutError:
            log.warning("MonsterVision API timeout")
            return False, None
        except aiohttp.ClientError as e:
            log.warning(f"MonsterVision API client error: {e}")
            return False, None
        except Exception as e:  # broad: intentional
            log.error(f"Error checking MonsterVision API: {e}")
            return False, None

    async def _check_cookie_age(self, api_data: Optional[Dict[str, Any]]) -> Optional[float]:
        """
        Extract cookie age from API data or logs.

        Returns:
            Age in hours, or None if unable to determine
        """
        if not api_data:
            return None

        # Check API cookie_status first
        cookie_info = api_data.get("cookie_status", {})
        if "age_hours" in cookie_info:
            return float(cookie_info["age_hours"])
        if "age_days" in cookie_info:
            return float(cookie_info["age_days"]) * 24
        # remaining_hours + ttl_days gives us cookie age indirectly
        if "remaining_hours" in cookie_info and "ttl_days" in cookie_info:
            ttl_hours = float(cookie_info["ttl_days"]) * 24
            age_hours = ttl_hours - float(cookie_info["remaining_hours"])
            return max(0.0, age_hours)

        # Fallback: Parse from logs
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                self.container_name,
                "tail",
                "-50",
                "/app/state/cron.log",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0:
                log_text = stdout.decode()
                # Look for pattern like "cookies.txt is 67h old"
                match = re.search(r"cookies\.txt is (\d+)h old", log_text)
                if match:
                    return float(match.group(1))

                # Alternative: Check file modification time
                proc2 = await asyncio.create_subprocess_exec(
                    "docker",
                    "exec",
                    self.container_name,
                    "stat",
                    "-c",
                    "%Y",
                    "/app/cookies.txt",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5.0)
                if proc2.returncode == 0:
                    mtime = int(stdout2.decode().strip())
                    age_seconds = datetime.now().timestamp() - mtime
                    return age_seconds / 3600
        except asyncio.TimeoutError:
            log.warning("Cookie age check timed out")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            log.debug(f"Could not determine cookie age: {e}")

        return None

    async def _check_error_patterns(self) -> List[str]:
        """Check logs for common error patterns."""
        patterns = []

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                self.container_name,
                "tail",
                "-100",
                "/app/state/cron.log",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0:
                log_text = stdout.decode().lower()

                if "403" in log_text or "cookies have expired" in log_text:
                    patterns.append("HTTP 403 - cookies expired")

                if "connection refused" in log_text or "network error" in log_text:
                    patterns.append("Network connectivity issues")

                if "disk full" in log_text or "no space" in log_text:
                    patterns.append("Disk space errors")

        except (asyncio.TimeoutError, Exception) as e:
            log.debug(f"Error checking log patterns: {e}")

        return patterns

    async def _check_disk_space(self) -> bool:
        """Check if there's sufficient disk space."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                self.container_name,
                "df",
                "-h",
                "/app/downloads",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0:
                # Parse df output to check usage percentage
                lines = stdout.decode().splitlines()
                if len(lines) > 1:
                    fields = lines[1].split()
                    if len(fields) >= 5:
                        usage_str = fields[4].rstrip("%")
                        try:
                            usage_pct = int(usage_str)
                            return usage_pct < 90
                        except ValueError:
                            pass
        except (asyncio.TimeoutError, Exception) as e:
            log.debug(f"Disk space check failed: {e}")

        return True  # Assume OK if we can't check

    def _determine_status(
        self,
        cookie_age_hours: Optional[float],
        failed_count: int,
        container_status: str,
        api_available: bool,
    ) -> PatreonHealthStatus:
        """Determine overall health status based on checks."""
        if container_status == "stopped" or not api_available:
            return PatreonHealthStatus.CRITICAL

        if cookie_age_hours is not None and cookie_age_hours > 72:
            return PatreonHealthStatus.CRITICAL

        if failed_count >= 3:
            return PatreonHealthStatus.CRITICAL

        if cookie_age_hours is not None and cookie_age_hours > 48:
            return PatreonHealthStatus.WARNING

        if failed_count > 0:
            return PatreonHealthStatus.WARNING

        if container_status not in ("running", "healthy"):
            return PatreonHealthStatus.WARNING

        return PatreonHealthStatus.OK

    def _get_cookie_refresh_steps(self) -> List[str]:
        """Get step-by-step cookie refresh instructions."""
        return [
            "1. Log into patreon.com in Chrome/Firefox",
            "2. Install EditThisCookie extension (Chrome) or Cookie-Editor (Firefox)",
            "3. Export cookies for patreon.com to cookies.txt (Netscape format)",
            "4. Copy to container: `docker cp cookies.txt monstervision:/app/cookies.txt`",
            "5. Restart container: `docker restart monstervision`",
            "6. Verify: Check /patreon status in ~5 minutes",
        ]

    def get_last_check(self) -> Optional[PatreonHealthResult]:
        """Get the last health check result."""
        return self._last_check


# Global instance
_checker: Optional[PatreonHealthChecker] = None


def get_patreon_checker() -> PatreonHealthChecker:
    """Get or create the global Patreon health checker."""
    global _checker
    if _checker is None:
        _checker = PatreonHealthChecker()
    return _checker
