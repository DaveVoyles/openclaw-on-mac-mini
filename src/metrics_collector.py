"""
Metrics Collection System for OpenClaw.

Collects and exports metrics in Prometheus format:
- Command execution counts
- Response times (p50, p95, p99)
- Error rates
- API usage
- Resource usage
- Active users
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import psutil
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
    CollectorRegistry,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger(__name__)


# Prometheus metrics
REGISTRY = CollectorRegistry()

# Command metrics
command_counter = Counter(
    "openclaw_commands_total",
    "Total number of commands executed",
    ["command", "user", "workspace"],
    registry=REGISTRY,
)

command_duration = Histogram(
    "openclaw_command_duration_seconds",
    "Command execution duration in seconds",
    ["command"],
    registry=REGISTRY,
)

# Error metrics
error_counter = Counter(
    "openclaw_errors_total",
    "Total number of errors",
    ["type", "endpoint"],
    registry=REGISTRY,
)

# API metrics
api_calls = Counter(
    "openclaw_api_calls_total",
    "Total number of API calls",
    ["provider", "method"],
    registry=REGISTRY,
)

api_errors = Counter(
    "openclaw_api_errors_total",
    "Total number of API errors",
    ["provider", "error_type"],
    registry=REGISTRY,
)

api_latency = Summary(
    "openclaw_api_latency_seconds",
    "API call latency in seconds",
    ["provider"],
    registry=REGISTRY,
)

# Resource metrics
cpu_usage = Gauge(
    "openclaw_cpu_usage_percent",
    "CPU usage percentage",
    registry=REGISTRY,
)

memory_usage = Gauge(
    "openclaw_memory_usage_bytes",
    "Memory usage in bytes",
    registry=REGISTRY,
)

disk_usage = Gauge(
    "openclaw_disk_usage_percent",
    "Disk usage percentage",
    registry=REGISTRY,
)

# User metrics
active_users = Gauge(
    "openclaw_active_users",
    "Number of active users",
    registry=REGISTRY,
)

messages_processed = Counter(
    "openclaw_messages_processed_total",
    "Total number of messages processed",
    registry=REGISTRY,
)


@dataclass
class CommandMetrics:
    """Metrics for a single command execution."""
    
    command: str
    user: str
    workspace: str
    duration: float
    timestamp: datetime
    success: bool
    error_type: Optional[str] = None


@dataclass
class APIMetrics:
    """Metrics for an API call."""
    
    provider: str
    method: str
    duration: float
    timestamp: datetime
    success: bool
    error_type: Optional[str] = None


class MetricsCollector:
    """Centralized metrics collection and aggregation."""
    
    def __init__(self):
        self._command_history: deque = deque(maxlen=10000)
        self._api_history: deque = deque(maxlen=10000)
        self._active_user_set: set = set()
        self._start_time = time.time()
        self._resource_update_task: Optional[asyncio.Task] = None
        
        # In-memory aggregations
        self._response_times: Dict[str, List[float]] = defaultdict(list)
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._command_counts: Dict[str, int] = defaultdict(int)
    
    async def start(self):
        """Start background metrics collection."""
        self._resource_update_task = asyncio.create_task(self._update_resources())
        logger.info("Metrics collector started")
    
    async def stop(self):
        """Stop background metrics collection."""
        if self._resource_update_task:
            self._resource_update_task.cancel()
            try:
                await self._resource_update_task
            except asyncio.CancelledError:
                pass
        logger.info("Metrics collector stopped")
    
    async def _update_resources(self):
        """Periodically update resource usage metrics."""
        while True:
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=1)
                cpu_usage.set(cpu_percent)
                
                # Memory usage
                memory = psutil.virtual_memory()
                memory_usage.set(memory.used)
                
                # Disk usage
                disk = psutil.disk_usage("/")
                disk_usage.set(disk.percent)
                
                # Active users (count unique users in last 5 minutes)
                cutoff = datetime.now() - timedelta(minutes=5)
                recent_users = {
                    m.user
                    for m in self._command_history
                    if m.timestamp > cutoff
                }
                active_users.set(len(recent_users))
                
                await asyncio.sleep(10)  # Update every 10 seconds
            except Exception as e:
                logger.error(f"Error updating resource metrics: {e}")
                await asyncio.sleep(60)
    
    def record_command(
        self,
        command: str,
        user: str,
        workspace: str,
        duration: float,
        success: bool = True,
        error_type: Optional[str] = None,
    ):
        """Record a command execution."""
        # Update Prometheus metrics
        command_counter.labels(
            command=command, user=user, workspace=workspace
        ).inc()
        command_duration.labels(command=command).observe(duration)
        
        if not success and error_type:
            error_counter.labels(type=error_type, endpoint=command).inc()
        
        messages_processed.inc()
        
        # Store in history
        metrics = CommandMetrics(
            command=command,
            user=user,
            workspace=workspace,
            duration=duration,
            timestamp=datetime.now(),
            success=success,
            error_type=error_type,
        )
        self._command_history.append(metrics)
        
        # Update in-memory aggregations
        self._response_times[command].append(duration)
        if len(self._response_times[command]) > 1000:
            self._response_times[command] = self._response_times[command][-1000:]
        
        if not success:
            self._error_counts[error_type or "unknown"] += 1
        
        self._command_counts[command] += 1
        self._active_user_set.add(user)
    
    def record_api_call(
        self,
        provider: str,
        method: str,
        duration: float,
        success: bool = True,
        error_type: Optional[str] = None,
    ):
        """Record an API call."""
        # Update Prometheus metrics
        api_calls.labels(provider=provider, method=method).inc()
        api_latency.labels(provider=provider).observe(duration)
        
        if not success and error_type:
            api_errors.labels(provider=provider, error_type=error_type).inc()
        
        # Store in history
        metrics = APIMetrics(
            provider=provider,
            method=method,
            duration=duration,
            timestamp=datetime.now(),
            success=success,
            error_type=error_type,
        )
        self._api_history.append(metrics)
    
    def get_stats(self, hours: int = 1) -> Dict[str, any]:
        """Get aggregated statistics for the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        # Filter recent commands
        recent_commands = [
            m for m in self._command_history if m.timestamp > cutoff
        ]
        
        # Calculate response time percentiles
        percentiles = {}
        for cmd, times in self._response_times.items():
            if times:
                sorted_times = sorted(times)
                percentiles[cmd] = {
                    "p50": sorted_times[len(sorted_times) // 2],
                    "p95": sorted_times[int(len(sorted_times) * 0.95)],
                    "p99": sorted_times[int(len(sorted_times) * 0.99)],
                }
        
        # Command counts
        cmd_counts = defaultdict(int)
        for cmd in recent_commands:
            cmd_counts[cmd.command] += 1
        
        # User counts
        user_counts = defaultdict(int)
        for cmd in recent_commands:
            user_counts[cmd.user] += 1
        
        # Error counts
        error_counts = defaultdict(int)
        for cmd in recent_commands:
            if not cmd.success and cmd.error_type:
                error_counts[cmd.error_type] += 1
        
        # API stats
        recent_apis = [m for m in self._api_history if m.timestamp > cutoff]
        api_counts = defaultdict(int)
        api_errors_count = defaultdict(int)
        for api in recent_apis:
            api_counts[api.provider] += 1
            if not api.success and api.error_type:
                api_errors_count[api.provider] += 1
        
        return {
            "period_hours": hours,
            "total_commands": len(recent_commands),
            "total_messages": len(recent_commands),
            "command_counts": dict(cmd_counts),
            "user_counts": dict(user_counts),
            "error_counts": dict(error_counts),
            "response_time_percentiles": percentiles,
            "api_calls": dict(api_counts),
            "api_errors": dict(api_errors_count),
            "active_users": len({m.user for m in recent_commands}),
            "uptime_seconds": int(time.time() - self._start_time),
        }
    
    def get_top_commands(self, limit: int = 10) -> List[tuple]:
        """Get top N most used commands."""
        return sorted(
            self._command_counts.items(), key=lambda x: x[1], reverse=True
        )[:limit]
    
    def get_top_users(self, limit: int = 10) -> List[tuple]:
        """Get top N most active users."""
        user_counts = defaultdict(int)
        for cmd in self._command_history:
            user_counts[cmd.user] += 1
        return sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    
    def get_top_errors(self, limit: int = 10) -> List[tuple]:
        """Get top N most common errors."""
        return sorted(
            self._error_counts.items(), key=lambda x: x[1], reverse=True
        )[:limit]
    
    def export_prometheus(self) -> bytes:
        """Export metrics in Prometheus format."""
        return generate_latest(REGISTRY)
    
    def get_prometheus_content_type(self) -> str:
        """Get Prometheus content type header."""
        return CONTENT_TYPE_LATEST


# Global metrics collector instance
_collector: Optional[MetricsCollector] = None


def get_collector() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


async def start_metrics_collector():
    """Start the global metrics collector."""
    collector = get_collector()
    await collector.start()


async def stop_metrics_collector():
    """Stop the global metrics collector."""
    collector = get_collector()
    await collector.stop()
