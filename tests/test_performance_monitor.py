"""
Tests for performance monitor.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from performance_monitor import (
    PerformanceMonitor,
    alert_slow_queries,
    get_monitor,
    monitor_performance,
    trace_span,
)


@pytest.fixture
def monitor():
    """Create a fresh performance monitor."""
    return PerformanceMonitor(slow_query_threshold=1.0)


def test_monitor_singleton():
    """Test that get_monitor returns singleton instance."""
    monitor1 = get_monitor()
    monitor2 = get_monitor()
    assert monitor1 is monitor2


def test_create_trace(monitor):
    """Test creating a trace context."""
    correlation_id = monitor.create_trace("test_operation", key="value")

    assert correlation_id in monitor._active_traces
    trace = monitor._active_traces[correlation_id]
    assert trace.operation == "test_operation"
    assert trace.metadata["key"] == "value"


def test_add_span(monitor):
    """Test adding a span to a trace."""
    correlation_id = monitor.create_trace("test_operation")
    span = monitor.add_span(correlation_id, "span1", detail="test")

    assert span.name == "span1"
    assert span.metadata["detail"] == "test"
    assert span in monitor._active_traces[correlation_id].spans


@pytest.mark.slow
def test_finish_trace(monitor):
    """Test finishing a trace."""
    correlation_id = monitor.create_trace("test_operation")
    time.sleep(0.1)
    monitor.finish_trace(correlation_id)

    assert correlation_id not in monitor._active_traces
    assert "test_operation" in monitor._operation_times


@pytest.mark.slow
def test_slow_query_detection(monitor):
    """Test slow query detection."""
    correlation_id = monitor.create_trace("slow_operation")
    time.sleep(1.2)  # Exceed threshold
    monitor.finish_trace(correlation_id)

    slow_queries = monitor.get_slow_queries()
    assert len(slow_queries) > 0
    assert slow_queries[-1].operation == "slow_operation"
    assert slow_queries[-1].duration > 1.0


def test_operation_stats(monitor):
    """Test operation statistics calculation."""
    # Record multiple operations
    for _ in range(10):
        correlation_id = monitor.create_trace("test_op")
        time.sleep(0.01)
        monitor.finish_trace(correlation_id)

    stats = monitor.get_operation_stats("test_op")

    assert stats["count"] == 10
    assert "min" in stats
    assert "max" in stats
    assert "mean" in stats
    assert "p50" in stats
    assert "p95" in stats
    assert "p99" in stats


def test_get_all_stats(monitor):
    """Test getting all operation stats."""
    for i in range(3):
        correlation_id = monitor.create_trace(f"op{i}")
        monitor.finish_trace(correlation_id)

    all_stats = monitor.get_all_stats()
    assert len(all_stats) == 3
    assert "op0" in all_stats
    assert "op1" in all_stats
    assert "op2" in all_stats


def test_memory_tracking(monitor):
    """Test memory tracking start/stop."""
    monitor.start_memory_tracking()
    assert monitor._memory_tracking_enabled

    monitor.stop_memory_tracking()
    assert not monitor._memory_tracking_enabled


def test_memory_snapshot(monitor):
    """Test taking memory snapshots."""
    snapshot = monitor.take_memory_snapshot()
    assert snapshot is not None
    assert len(monitor._memory_snapshots) == 1


def test_cpu_profiling(monitor):
    """Test CPU profiling."""
    monitor.start_profiling()
    assert monitor._is_profiling

    # Do some work
    sum([i ** 2 for i in range(1000)])

    stats = monitor.stop_profiling()
    assert not monitor._is_profiling
    assert "function calls" in stats.lower() or "cumulative" in stats.lower()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_monitor_performance_decorator_async():
    """Test monitor_performance decorator on async function."""
    monitor = get_monitor()

    @monitor_performance("test_async_op")
    async def test_performance_monitor_func():
        await asyncio.sleep(0.1)
        return "done"

    result = await test_func()
    assert result == "done"
    assert "test_async_op" in monitor._operation_times


@pytest.mark.slow
def test_monitor_performance_decorator_sync():
    """Test monitor_performance decorator on sync function."""
    monitor = get_monitor()

    @monitor_performance("test_sync_op")
    def test_performance_monitor_func_v2():
        time.sleep(0.1)
        return "done"

    result = test_func()
    assert result == "done"
    assert "test_sync_op" in monitor._operation_times


@pytest.mark.slow
@pytest.mark.asyncio
async def test_alert_slow_queries_decorator():
    """Test alert_slow_queries decorator."""
    @alert_slow_queries(threshold=0.1)
    async def slow_func():
        await asyncio.sleep(0.2)
        return "done"

    with patch("performance_monitor.logger") as mock_logger:
        result = await slow_func()
        assert result == "done"
        # Should have logged a warning
        mock_logger.warning.assert_called()


def test_trace_span_context_manager(monitor):
    """Test trace_span context manager."""
    correlation_id = monitor.create_trace("test_operation")

    with trace_span(correlation_id, "test_span", key="value") as span:
        time.sleep(0.05)
        assert span.name == "test_span"
        assert span.metadata["key"] == "value"
        assert span.end_time is None

    # After context exit, span should be finished
    assert span.end_time is not None
    assert span.duration > 0


def test_span_finish(monitor):
    """Test span finish method."""
    correlation_id = monitor.create_trace("test_operation")
    span = monitor.add_span(correlation_id, "test_span")

    assert span.end_time is None
    assert span.duration is None

    span.finish()

    assert span.end_time is not None
    assert span.duration is not None
    assert span.duration >= 0


def test_slow_query_threshold_configurable():
    """Test that slow query threshold is configurable."""
    monitor_fast = PerformanceMonitor(slow_query_threshold=0.1)
    monitor_slow = PerformanceMonitor(slow_query_threshold=5.0)

    assert monitor_fast.slow_query_threshold == 0.1
    assert monitor_slow.slow_query_threshold == 5.0


@pytest.mark.asyncio
async def test_concurrent_tracing():
    """Test concurrent trace creation and finishing."""
    monitor = PerformanceMonitor()

    async def create_and_finish_trace(op_name):
        correlation_id = monitor.create_trace(op_name)
        await asyncio.sleep(0.01)
        monitor.finish_trace(correlation_id)

    # Create multiple concurrent traces
    await asyncio.gather(*[
        create_and_finish_trace(f"op{i}")
        for i in range(10)
    ])

    # All should be finished
    assert len(monitor._active_traces) == 0
    # All should be recorded
    assert len(monitor._operation_times) == 10
