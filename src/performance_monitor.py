"""
Performance Monitoring for OpenClaw.

Tracks:
- Request tracing with correlation IDs
- Slow query detection
- Memory leak detection
- Database query profiling
- API latency tracking
"""

import asyncio
import cProfile
import functools
import io
import logging
import pstats
import time
import tracemalloc
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class TraceContext:
    """Context for request tracing."""

    correlation_id: str
    start_time: float
    operation: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    spans: List["Span"] = field(default_factory=list)


@dataclass
class Span:
    """A single span in a trace."""

    name: str
    start_time: float
    end_time: Optional[float] = None
    duration: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def finish(self):
        """Mark the span as finished."""
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time


@dataclass
class SlowQuery:
    """Record of a slow query."""

    operation: str
    duration: float
    timestamp: datetime
    correlation_id: str
    stack_trace: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class PerformanceMonitor:
    """Centralized performance monitoring."""

    def __init__(self, slow_query_threshold: float = 1.0):
        self.slow_query_threshold = slow_query_threshold
        self._active_traces: Dict[str, TraceContext] = {}
        self._slow_queries: deque = deque(maxlen=1000)
        self._memory_snapshots: deque = deque(maxlen=100)
        self._operation_times: Dict[str, List[float]] = defaultdict(list)
        self._is_profiling = False
        self._profiler: Optional[cProfile.Profile] = None

        # Memory tracking
        self._memory_tracking_enabled = False
        self._last_memory_check = time.time()

    def create_trace(self, operation: str, **metadata) -> str:
        """Create a new trace context and return correlation ID."""
        correlation_id = str(uuid4())
        trace = TraceContext(
            correlation_id=correlation_id,
            start_time=time.time(),
            operation=operation,
            metadata=metadata,
        )
        self._active_traces[correlation_id] = trace
        return correlation_id

    def add_span(self, correlation_id: str, span_name: str, **metadata) -> Span:
        """Add a span to a trace."""
        if correlation_id not in self._active_traces:
            logger.warning(f"Trace {correlation_id} not found")
            return Span(name=span_name, start_time=time.time(), metadata=metadata)

        span = Span(name=span_name, start_time=time.time(), metadata=metadata)
        self._active_traces[correlation_id].spans.append(span)
        return span

    def finish_trace(self, correlation_id: str):
        """Finish a trace and record metrics."""
        if correlation_id not in self._active_traces:
            return

        trace = self._active_traces.pop(correlation_id)
        duration = time.time() - trace.start_time

        # Record operation time
        self._operation_times[trace.operation].append(duration)
        if len(self._operation_times[trace.operation]) > 1000:
            self._operation_times[trace.operation] = \
                self._operation_times[trace.operation][-1000:]

        # Check if slow
        if duration > self.slow_query_threshold:
            self._record_slow_query(trace, duration)

    def _record_slow_query(self, trace: TraceContext, duration: float):
        """Record a slow query."""
        slow_query = SlowQuery(
            operation=trace.operation,
            duration=duration,
            timestamp=datetime.now(),
            correlation_id=trace.correlation_id,
            stack_trace="".join(tracemalloc.get_traced_memory()[0] if self._memory_tracking_enabled else ""),
            metadata=trace.metadata,
        )
        self._slow_queries.append(slow_query)
        logger.warning(
            f"Slow query detected: {trace.operation} took {duration:.2f}s "
            f"(threshold: {self.slow_query_threshold}s)"
        )

    def get_slow_queries(self, limit: int = 20) -> List[SlowQuery]:
        """Get recent slow queries."""
        return list(self._slow_queries)[-limit:]

    def get_operation_stats(self, operation: str) -> Dict[str, float]:
        """Get statistics for an operation."""
        times = self._operation_times.get(operation, [])
        if not times:
            return {}

        sorted_times = sorted(times)
        return {
            "count": len(times),
            "min": min(times),
            "max": max(times),
            "mean": sum(times) / len(times),
            "p50": sorted_times[len(sorted_times) // 2],
            "p95": sorted_times[int(len(sorted_times) * 0.95)],
            "p99": sorted_times[int(len(sorted_times) * 0.99)],
        }

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all operations."""
        return {
            op: self.get_operation_stats(op)
            for op in self._operation_times.keys()
        }

    def start_memory_tracking(self):
        """Start tracking memory allocations."""
        if not self._memory_tracking_enabled:
            tracemalloc.start()
            self._memory_tracking_enabled = True
            logger.info("Memory tracking started")

    def stop_memory_tracking(self):
        """Stop tracking memory allocations."""
        if self._memory_tracking_enabled:
            tracemalloc.stop()
            self._memory_tracking_enabled = False
            logger.info("Memory tracking stopped")

    def take_memory_snapshot(self):
        """Take a memory snapshot."""
        if not self._memory_tracking_enabled:
            self.start_memory_tracking()

        snapshot = tracemalloc.take_snapshot()
        self._memory_snapshots.append({
            "timestamp": datetime.now(),
            "snapshot": snapshot,
        })
        return snapshot

    def detect_memory_leaks(self) -> List[str]:
        """Detect potential memory leaks."""
        if len(self._memory_snapshots) < 2:
            return []

        # Compare latest snapshot with previous
        latest = self._memory_snapshots[-1]["snapshot"]
        previous = self._memory_snapshots[-2]["snapshot"]

        top_stats = latest.compare_to(previous, "lineno")

        leaks = []
        for stat in top_stats[:10]:
            if stat.size_diff > 0:
                leaks.append(
                    f"{stat.traceback}: +{stat.size_diff / 1024:.1f} KB "
                    f"({stat.count_diff} allocations)"
                )

        return leaks

    def start_profiling(self):
        """Start CPU profiling."""
        if self._is_profiling:
            logger.warning("Profiling already active")
            return

        self._profiler = cProfile.Profile()
        self._profiler.enable()
        self._is_profiling = True
        logger.info("CPU profiling started")

    def stop_profiling(self) -> str:
        """Stop CPU profiling and return stats."""
        if not self._is_profiling:
            logger.warning("No active profiling session")
            return "No profiling session active"

        self._profiler.disable()
        self._is_profiling = False

        # Get stats
        s = io.StringIO()
        ps = pstats.Stats(self._profiler, stream=s)
        ps.strip_dirs()
        ps.sort_stats("cumulative")
        ps.print_stats(50)  # Top 50 functions

        self._profiler = None
        logger.info("CPU profiling stopped")

        return s.getvalue()


# Global performance monitor
_monitor: Optional[PerformanceMonitor] = None


def get_monitor() -> PerformanceMonitor:
    """Get or create the global performance monitor."""
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor


# Decorators for performance monitoring

def monitor_performance(operation: Optional[str] = None):
    """Decorator to monitor function performance."""
    def decorator(func: Callable) -> Callable:
        op_name = operation or f"{func.__module__}.{func.__name__}"

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            monitor = get_monitor()
            correlation_id = monitor.create_trace(op_name)
            time.time()

            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                monitor.finish_trace(correlation_id)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            monitor = get_monitor()
            correlation_id = monitor.create_trace(op_name)
            time.time()

            try:
                result = func(*args, **kwargs)
                return result
            finally:
                monitor.finish_trace(correlation_id)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


def alert_slow_queries(threshold: float = 1.0):
    """Decorator to alert on slow queries."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start_time

            if duration > threshold:
                logger.warning(
                    f"Slow query: {func.__name__} took {duration:.2f}s "
                    f"(threshold: {threshold}s)"
                )

            return result

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start_time

            if duration > threshold:
                logger.warning(
                    f"Slow query: {func.__name__} took {duration:.2f}s "
                    f"(threshold: {threshold}s)"
                )

            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


@contextmanager
def trace_span(correlation_id: str, span_name: str, **metadata):
    """Context manager for tracing a span."""
    monitor = get_monitor()
    span = monitor.add_span(correlation_id, span_name, **metadata)

    try:
        yield span
    finally:
        span.finish()
