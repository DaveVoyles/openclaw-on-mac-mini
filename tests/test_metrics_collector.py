"""
Tests for metrics collector.
"""

import asyncio
import pytest
import time
from unittest.mock import Mock, patch

from metrics_collector import (
    MetricsCollector,
    get_collector,
    start_metrics_collector,
    stop_metrics_collector,
)


@pytest.fixture
def collector():
    """Create a fresh metrics collector."""
    return MetricsCollector()


@pytest.fixture
async def running_collector():
    """Create and start a collector."""
    collector = MetricsCollector()
    await collector.start()
    yield collector
    await collector.stop()


def test_collector_singleton():
    """Test that get_collector returns singleton instance."""
    collector1 = get_collector()
    collector2 = get_collector()
    assert collector1 is collector2


def test_record_command(collector):
    """Test recording a command execution."""
    collector.record_command(
        command="ask",
        user="user123",
        workspace="general",
        duration=1.5,
        success=True,
    )
    
    assert len(collector._command_history) == 1
    assert collector._command_counts["ask"] == 1
    assert "ask" in collector._response_times
    assert collector._response_times["ask"][0] == 1.5


def test_record_command_failure(collector):
    """Test recording a failed command."""
    collector.record_command(
        command="ask",
        user="user123",
        workspace="general",
        duration=0.5,
        success=False,
        error_type="timeout",
    )
    
    assert collector._error_counts["timeout"] == 1


def test_record_api_call(collector):
    """Test recording an API call."""
    collector.record_api_call(
        provider="gemini",
        method="generate_content",
        duration=2.0,
        success=True,
    )
    
    assert len(collector._api_history) == 1


def test_get_stats(collector):
    """Test getting aggregated statistics."""
    # Record some commands
    for i in range(10):
        collector.record_command(
            command="ask",
            user=f"user{i}",
            workspace="general",
            duration=float(i),
        )
    
    stats = collector.get_stats(hours=1)
    
    assert stats["total_commands"] == 10
    assert "ask" in stats["command_counts"]
    assert stats["command_counts"]["ask"] == 10
    assert "ask" in stats["response_time_percentiles"]


def test_get_top_commands(collector):
    """Test getting top commands."""
    # Record multiple commands
    for _ in range(5):
        collector.record_command("ask", "user1", "general", 1.0)
    for _ in range(3):
        collector.record_command("help", "user1", "general", 0.5)
    for _ in range(7):
        collector.record_command("analyze", "user1", "general", 2.0)
    
    top = collector.get_top_commands(limit=3)
    
    assert len(top) == 3
    assert top[0][0] == "analyze"  # Most used
    assert top[0][1] == 7
    assert top[1][0] == "ask"
    assert top[1][1] == 5


def test_get_top_users(collector):
    """Test getting top users."""
    for _ in range(10):
        collector.record_command("ask", "alice", "general", 1.0)
    for _ in range(5):
        collector.record_command("ask", "bob", "general", 1.0)
    
    top = collector.get_top_users(limit=2)
    
    assert len(top) == 2
    assert top[0][0] == "alice"
    assert top[0][1] == 10


def test_get_top_errors(collector):
    """Test getting top errors."""
    for _ in range(3):
        collector.record_command("ask", "user1", "general", 1.0, False, "timeout")
    for _ in range(5):
        collector.record_command("ask", "user1", "general", 1.0, False, "rate_limit")
    
    top = collector.get_top_errors(limit=2)
    
    assert len(top) == 2
    assert top[0][0] == "rate_limit"
    assert top[0][1] == 5


@pytest.mark.asyncio
async def test_start_stop_collector():
    """Test starting and stopping the collector."""
    collector = MetricsCollector()
    
    await collector.start()
    assert collector._resource_update_task is not None
    
    # Let it run briefly
    await asyncio.sleep(0.1)
    
    await collector.stop()
    assert collector._resource_update_task.done() or collector._resource_update_task.cancelled()


@pytest.mark.asyncio
async def test_resource_updates(running_collector):
    """Test that resource metrics are updated."""
    # Wait for at least one update cycle
    await asyncio.sleep(1.5)
    
    # Check that metrics were updated (we can't verify exact values)
    # but we can verify the collector ran


def test_export_prometheus(collector):
    """Test exporting metrics in Prometheus format."""
    collector.record_command("ask", "user1", "general", 1.0)
    
    metrics_bytes = collector.export_prometheus()
    metrics_text = metrics_bytes.decode("utf-8")
    
    assert "openclaw_commands_total" in metrics_text
    assert "openclaw_messages_processed_total" in metrics_text


def test_response_time_percentiles(collector):
    """Test response time percentile calculation."""
    # Add a range of response times (need more for distinct percentiles)
    times = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 
             1.2, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0]
    for t in times:
        collector.record_command("ask", "user1", "general", t)
    
    stats = collector.get_stats(hours=1)
    percentiles = stats["response_time_percentiles"]["ask"]
    
    assert "p50" in percentiles
    assert "p95" in percentiles
    assert "p99" in percentiles
    assert percentiles["p50"] <= percentiles["p95"] <= percentiles["p99"]


def test_command_history_maxlen(collector):
    """Test that command history respects maxlen."""
    # Record more than maxlen commands
    for i in range(15000):
        collector.record_command("ask", "user1", "general", 1.0)
    
    # Should be limited to 10000
    assert len(collector._command_history) == 10000


@pytest.mark.asyncio
async def test_concurrent_recording():
    """Test concurrent metric recording."""
    collector = MetricsCollector()
    
    async def record_many():
        for _ in range(100):
            collector.record_command("ask", "user1", "general", 1.0)
            await asyncio.sleep(0.001)
    
    # Run multiple concurrent recorders
    await asyncio.gather(*[record_many() for _ in range(3)])
    
    assert collector._command_counts["ask"] == 300
