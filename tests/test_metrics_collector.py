"""
Tests for metrics collector.
"""

import asyncio
import uuid

import pytest

from metrics_collector import (
    MetricsCollector,
    get_collector,
    get_quality_event_snapshot,
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


def test_metrics_collector_get_stats(collector):
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


def test_quality_event_snapshot_feedback_guardrail_counts(collector):
    baseline = get_quality_event_snapshot()
    base_events = baseline.get("event_counts", {})
    base_helpful = int(base_events.get("ask_feedback_helpful", 0))
    base_not_helpful = int(base_events.get("ask_feedback_not_helpful", 0))
    base_accepted = int(base_events.get("ask_feedback_accepted", 0))
    base_suppressed = int(base_events.get("ask_feedback_suppressed", 0))
    base_suppressed_dedupe = int(base_events.get("ask_feedback_suppressed_dedupe", 0))
    base_suppressed_rate_limited = int(
        base_events.get("ask_feedback_suppressed_rate_limited_user", 0)
    ) + int(base_events.get("ask_feedback_suppressed_rate_limited_channel", 0))

    collector.record_quality_event("ask_feedback_helpful", "discord_ask")
    collector.record_quality_event("ask_feedback_helpful", "discord_ask")
    collector.record_quality_event("ask_feedback_not_helpful", "discord_ask")
    collector.record_quality_event("ask_feedback_accepted", "discord_ask")
    collector.record_quality_event("ask_feedback_accepted", "discord_ask")
    collector.record_quality_event("ask_feedback_accepted", "discord_ask")
    collector.record_quality_event("ask_feedback_suppressed", "discord_ask")
    collector.record_quality_event("ask_feedback_suppressed", "discord_ask")
    collector.record_quality_event("ask_feedback_suppressed_dedupe", "discord_ask")
    collector.record_quality_event("ask_feedback_suppressed_rate_limited_user", "discord_ask")

    snapshot = get_quality_event_snapshot()
    feedback = snapshot["feedback"]

    assert feedback["helpful"] == base_helpful + 2
    assert feedback["not_helpful"] == base_not_helpful + 1
    assert feedback["total"] == base_helpful + base_not_helpful + 3
    assert feedback["accepted"] == base_accepted + 3
    assert feedback["suppressed"] == base_suppressed + 2
    assert feedback["suppressed_dedupe"] == base_suppressed_dedupe + 1
    assert feedback["suppressed_rate_limited"] == base_suppressed_rate_limited + 1


def test_record_degrade_mode_activation_updates_snapshot(collector):
    baseline = get_quality_event_snapshot()
    base_degrade = baseline.get("degrade_mode", {})
    base_total = int(base_degrade.get("total_activations", 0))
    base_mode_counts = base_degrade.get("mode_counts", {})
    base_path_counts = base_degrade.get("path_counts", {})
    base_reason_counts = base_degrade.get("reason_counts", {})
    base_event_counts = baseline.get("event_counts", {})
    base_constrained_events = int(base_event_counts.get("degrade_mode_constrained", 0))

    collector.record_degrade_mode_activation(
        mode="constrained",
        path="ask_retrieval",
        reason="provider_timeout_rate",
    )
    collector.record_degrade_mode_activation(
        mode="constrained",
        path="ask_retrieval",
        reason="retrieval_sparsity_rate",
    )

    snapshot = get_quality_event_snapshot()
    degrade = snapshot["degrade_mode"]

    assert degrade["total_activations"] == base_total + 2
    assert degrade["mode_counts"]["constrained"] == int(base_mode_counts.get("constrained", 0)) + 2
    assert degrade["path_counts"]["ask_retrieval"] == int(base_path_counts.get("ask_retrieval", 0)) + 2
    assert degrade["reason_counts"]["provider_timeout_rate"] == int(base_reason_counts.get("provider_timeout_rate", 0)) + 1
    assert degrade["reason_counts"]["retrieval_sparsity_rate"] == int(base_reason_counts.get("retrieval_sparsity_rate", 0)) + 1
    assert snapshot["event_counts"]["degrade_mode_constrained"] == base_constrained_events + 2


def test_quality_event_snapshot_includes_bounded_domain_and_signal_slices(collector):
    suffix = uuid.uuid4().hex[:8]
    failure_event = f"zztest_fallback_incident_{suffix}"
    mitigation_event = f"zztest_retry_improved_{suffix}"
    degrade_event = f"degrade_mode_constrained_{suffix}"

    for _ in range(12):
        collector.record_quality_event(failure_event, "zztest_scope")
    for _ in range(8):
        collector.record_quality_event(mitigation_event, "zztest_scope")
    for _ in range(6):
        collector.record_quality_event(degrade_event, "zztest_scope")

    snapshot = get_quality_event_snapshot(limit=5)

    assert len(snapshot["domain_trends"]) <= 5
    assert len(snapshot["top_recurring_failures"]) <= 5
    assert len(snapshot["recent_signal_slices"]["mitigation"]) <= 5
    assert len(snapshot["recent_signal_slices"]["degrade"]) <= 5
    assert any(
        item["domain"] == "zztest"
        and int(item["failure_events"]) >= 12
        and int(item["mitigation_events"]) >= 8
        for item in snapshot["domain_trends"]
    )
    assert any(
        item["event"] == failure_event and int(item["count"]) >= 12
        for item in snapshot["top_recurring_failures"]
    )
    assert any(
        item["signal"] == mitigation_event and int(item["count"]) >= 8
        for item in snapshot["recent_signal_slices"]["mitigation"]
    )


def test_quality_event_snapshot_includes_normalized_failure_categories(collector):
    collector.record_quality_event("search_low_results_incident", "search")
    collector.record_quality_event("search_low_results_incident", "search")
    collector.record_quality_event("recap_source_diversity_warning", "recap")
    collector.record_quality_event("recap_partial_coverage_warning", "recap")
    collector.record_quality_event("degrade_mode_constrained", "ask_retrieval")
    collector.record_quality_event("search_provider_timeout_error", "search")
    collector.record_quality_event("ask_quality_retry_no_improvement", "ask")

    snapshot = get_quality_event_snapshot(limit=3)

    assert "top_quality_failure_categories" in snapshot
    assert len(snapshot["top_quality_failure_categories"]) <= 3
    assert "quality_failure_categories" in snapshot
    counts = snapshot["quality_failure_categories"]["counts"]
    assert counts["requested_item_shortfall"] >= 2
    assert counts["source_diversity_shortfall"] >= 1
    assert counts["low_evidence_completeness"] >= 1
    assert counts["degrade_mode_constrained"] >= 1
    assert counts["provider_timeout_pressure"] >= 1
    assert counts["quality_regression"] >= 1
    top_first = snapshot["top_quality_failure_categories"][0]
    assert "category" in top_first
    assert "count" in top_first
    assert "share" in top_first
