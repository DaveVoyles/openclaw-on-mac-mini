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
from dataclasses import dataclass
from datetime import datetime, timedelta

import psutil
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
)

logger = logging.getLogger(__name__)


def _safe_non_negative_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


def _infer_quality_domain(event_name: str) -> str:
    event = str(event_name or "").strip().lower()
    if not event:
        return "general"
    if event.startswith("ask_feedback_"):
        return "feedback"
    if event.startswith("degrade_mode_"):
        return "degrade"
    if "_" not in event:
        return event
    domain, _ = event.split("_", 1)
    return domain or "general"


def _classify_quality_signal(event_name: str) -> tuple[bool, bool, bool]:
    event = str(event_name or "").strip().lower()
    if not event:
        return False, False, False
    is_mitigation = (
        "improved" in event
        or "accepted" in event
        or ("helpful" in event and "not_helpful" not in event)
        or "recovered" in event
    )
    is_failure = (
        "fallback" in event
        or "incident" in event
        or "warning" in event
        or "failed" in event
        or "degrade" in event
        or "low" in event
        or "not_helpful" in event
        or "suppressed" in event
        or "no_improvement" in event
        or "timeout" in event
        or "error" in event
    )
    is_degrade = (
        "degrade" in event
        or "fallback" in event
        or "incident" in event
        or "warning" in event
        or "failed" in event
        or "low" in event
        or "no_improvement" in event
    )
    return is_failure, is_mitigation, is_degrade


_QUALITY_FAILURE_CATEGORY_LABELS: dict[str, str] = {
    "requested_item_shortfall": "requested-item shortfall",
    "source_diversity_shortfall": "source-diversity shortfall",
    "low_evidence_completeness": "low evidence completeness",
    "degrade_mode_constrained": "degrade-mode constrained",
    "provider_timeout_pressure": "provider-timeout pressure",
    "quality_regression": "quality regression",
    "other": "other",
}


def _normalize_quality_failure_category(event_name: str) -> str:
    event = str(event_name or "").strip().lower().replace(" ", "_")
    if not event:
        return "other"
    if "degrade_mode_constrained" in event:
        return "degrade_mode_constrained"
    if (
        "requested_item" in event
        or "missing_item" in event
        or "insufficient_item" in event
        or "low_results" in event
        or "item_coverage" in event
    ):
        return "requested_item_shortfall"
    if "source_diversity" in event or "single_source" in event or "mono_source" in event or "one_source" in event:
        return "source_diversity_shortfall"
    if (
        "partial_coverage" in event
        or "evidence" in event
        or "citation" in event
        or "grounding" in event
        or "completeness" in event
    ):
        return "low_evidence_completeness"
    if "timeout" in event or "rate_limit" in event:
        return "provider_timeout_pressure"
    if (
        "fallback" in event
        or "failed" in event
        or "error" in event
        or "no_improvement" in event
        or "not_helpful" in event
        or "suppressed" in event
    ):
        return "quality_regression"
    return "other"


def _build_quality_failure_category_summary(
    failure_events: list[dict[str, int | str]],
    *,
    total_failures: int,
    limit: int,
) -> dict[str, object]:
    safe_limit = max(1, min(int(limit or 10), 20))
    counts: dict[str, int] = defaultdict(int)
    examples: dict[str, list[dict[str, int | str]]] = defaultdict(list)
    for entry in failure_events:
        event_name = str(entry.get("event") or "").strip().lower()
        count = _safe_non_negative_int(entry.get("count", 0), default=0)
        if not event_name or count <= 0:
            continue
        category = _normalize_quality_failure_category(event_name)
        counts[category] += count
        sample_bucket = examples[category]
        if len(sample_bucket) < 3:
            sample_bucket.append({"event": event_name, "count": count})
    sorted_counts = sorted(
        counts.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    top_categories: list[dict[str, object]] = []
    for index, (category, count) in enumerate(sorted_counts[:safe_limit], start=1):
        share = round(count / total_failures, 3) if total_failures > 0 else 0.0
        top_categories.append(
            {
                "category": category,
                "label": _QUALITY_FAILURE_CATEGORY_LABELS.get(category, "other"),
                "count": count,
                "share": share,
                "rank": index,
                "examples": examples.get(category, [])[:3],
            }
        )
    return {
        "counts": {name: int(value) for name, value in sorted_counts},
        "top": top_categories,
        "total_classified_failures": int(sum(counts.values())),
        "total_failure_events": int(total_failures),
    }


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

quality_event_counter = Counter(
    "openclaw_quality_events_total",
    "Quality/reliability events emitted by recap/search pipelines",
    ["event", "context"],
    registry=REGISTRY,
)

budget_policy_decision_counter = Counter(
    "openclaw_budget_policy_decisions_total",
    "Latency-quality budget policy decisions applied by ask/search flows",
    ["path", "profile", "load_tier", "decision"],
    registry=REGISTRY,
)

degrade_mode_activation_counter = Counter(
    "openclaw_degrade_mode_activations_total",
    "Deterministic retrieval degrade-mode activations",
    ["mode", "path", "reason"],
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
    error_type: str | None = None


@dataclass
class APIMetrics:
    """Metrics for an API call."""

    provider: str
    method: str
    duration: float
    timestamp: datetime
    success: bool
    error_type: str | None = None


class MetricsCollector:
    """Centralized metrics collection and aggregation."""

    def __init__(self):
        self._command_history: deque = deque(maxlen=10000)
        self._api_history: deque = deque(maxlen=10000)
        self._active_user_set: set = set()
        self._start_time = time.time()
        self._resource_update_task: asyncio.Task | None = None

        # In-memory aggregations
        self._response_times: dict[str, list[float]] = defaultdict(list)
        self._error_counts: dict[str, int] = defaultdict(int)
        self._command_counts: dict[str, int] = defaultdict(int)

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
                recent_users = {m.user for m in self._command_history if m.timestamp > cutoff}
                active_users.set(len(recent_users))

                await asyncio.sleep(10)  # Update every 10 seconds
            except Exception:  # broad: intentional
                await asyncio.sleep(60)

    def record_command(
        self,
        command: str,
        user: str,
        workspace: str,
        duration: float,
        success: bool = True,
        error_type: str | None = None,
    ):
        """Record a command execution."""
        # Update Prometheus metrics
        command_counter.labels(command=command, user=user, workspace=workspace).inc()
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
        error_type: str | None = None,
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

    def record_quality_event(self, event: str, context: str = "general"):
        """Record a quality/reliability event."""
        event_label = (event or "unknown").strip().lower().replace(" ", "_")
        context_label = (context or "general").strip().lower().replace(" ", "_")
        quality_event_counter.labels(event=event_label, context=context_label).inc()

    def record_budget_policy_decision(
        self,
        *,
        path: str,
        profile: str,
        load_tier: str,
        decision: str,
    ) -> None:
        """Record deterministic latency-quality budget policy application."""
        budget_policy_decision_counter.labels(
            path=(path or "unknown").strip().lower().replace(" ", "_"),
            profile=(profile or "general").strip().lower().replace(" ", "_"),
            load_tier=(load_tier or "unknown").strip().lower().replace(" ", "_"),
            decision=(decision or "failsafe").strip().lower().replace(" ", "_"),
        ).inc()

    def record_degrade_mode_activation(
        self,
        *,
        mode: str,
        path: str,
        reason: str = "unspecified",
    ) -> None:
        """Record deterministic retrieval degrade-mode activation."""
        mode_label = (mode or "normal").strip().lower().replace(" ", "_")
        path_label = (path or "unknown").strip().lower().replace(" ", "_")
        reason_label = (reason or "unspecified").strip().lower().replace(" ", "_")
        degrade_mode_activation_counter.labels(
            mode=mode_label,
            path=path_label,
            reason=reason_label,
        ).inc()
        self.record_quality_event(
            event=f"degrade_mode_{mode_label}",
            context=path_label,
        )

    def get_stats(self, hours: int = 1) -> dict[str, any]:
        """Get aggregated statistics for the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)

        # Filter recent commands
        recent_commands = [m for m in self._command_history if m.timestamp > cutoff]

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

    def get_top_commands(self, limit: int = 10) -> list[tuple]:
        """Get top N most used commands."""
        return sorted(self._command_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    def get_top_users(self, limit: int = 10) -> list[tuple]:
        """Get top N most active users."""
        user_counts = defaultdict(int)
        for cmd in self._command_history:
            user_counts[cmd.user] += 1
        return sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    def get_top_errors(self, limit: int = 10) -> list[tuple]:
        """Get top N most common errors."""
        return sorted(self._error_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

    def export_prometheus(self) -> bytes:
        """Export metrics in Prometheus format."""
        return generate_latest(REGISTRY)

    def get_prometheus_content_type(self) -> str:
        """Get Prometheus content type header."""
        return CONTENT_TYPE_LATEST


def get_quality_event_snapshot(limit: int = 20) -> dict:
    """Return aggregated quality-event counters for dashboard consumers."""
    safe_limit = max(1, min(int(limit or 20), 50))
    event_counts: dict[str, float] = defaultdict(float)
    context_counts: dict[str, float] = defaultdict(float)
    degrade_mode_counts: dict[str, float] = defaultdict(float)
    degrade_path_counts: dict[str, float] = defaultdict(float)
    degrade_reason_counts: dict[str, float] = defaultdict(float)

    for metric in quality_event_counter.collect():
        for sample in metric.samples:
            if sample.name != "openclaw_quality_events_total":
                continue
            labels = sample.labels or {}
            event = str(labels.get("event") or "unknown")
            context = str(labels.get("context") or "general")
            value = float(sample.value or 0.0)
            if value <= 0:
                continue
            event_counts[event] += value
            context_counts[context] += value
    for metric in degrade_mode_activation_counter.collect():
        for sample in metric.samples:
            if sample.name != "openclaw_degrade_mode_activations_total":
                continue
            labels = sample.labels or {}
            mode = str(labels.get("mode") or "normal")
            path = str(labels.get("path") or "unknown")
            reason = str(labels.get("reason") or "unspecified")
            value = float(sample.value or 0.0)
            if value <= 0:
                continue
            degrade_mode_counts[mode] += value
            degrade_path_counts[path] += value
            degrade_reason_counts[reason] += value

    sorted_events = sorted(event_counts.items(), key=lambda item: item[1], reverse=True)
    sorted_contexts = sorted(context_counts.items(), key=lambda item: item[1], reverse=True)
    sorted_degrade_modes = sorted(degrade_mode_counts.items(), key=lambda item: item[1], reverse=True)
    sorted_degrade_paths = sorted(degrade_path_counts.items(), key=lambda item: item[1], reverse=True)
    sorted_degrade_reasons = sorted(degrade_reason_counts.items(), key=lambda item: item[1], reverse=True)
    domain_rollup: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "total_events": 0,
            "failure_events": 0,
            "mitigation_events": 0,
            "degrade_events": 0,
        }
    )
    recurring_failures: list[dict[str, int | str]] = []
    mitigation_signals: list[dict[str, int | str]] = []
    degrade_signals: list[dict[str, int | str]] = []
    for event_name, raw_count in sorted_events:
        count = _safe_non_negative_int(raw_count, default=0)
        if count <= 0:
            continue
        domain = _infer_quality_domain(event_name)
        is_failure, is_mitigation, is_degrade = _classify_quality_signal(event_name)
        domain_rollup[domain]["total_events"] += count
        if is_failure:
            domain_rollup[domain]["failure_events"] += count
            recurring_failures.append({"event": event_name, "count": count, "domain": domain})
        if is_mitigation:
            domain_rollup[domain]["mitigation_events"] += count
            mitigation_signals.append({"signal": event_name, "count": count, "domain": domain})
        if is_degrade:
            domain_rollup[domain]["degrade_events"] += count
            degrade_signals.append({"signal": event_name, "count": count, "domain": domain})
    domain_trends: list[dict[str, int | str]] = []
    for domain, counts in domain_rollup.items():
        failure_events = counts["failure_events"]
        mitigation_events = counts["mitigation_events"]
        if failure_events == 0 and mitigation_events > 0:
            trend = "improving"
        elif failure_events >= max(2, mitigation_events * 2):
            trend = "degrading"
        elif failure_events > mitigation_events:
            trend = "watch"
        else:
            trend = "stable"
        domain_trends.append(
            {
                "domain": domain,
                "total_events": counts["total_events"],
                "failure_events": failure_events,
                "mitigation_events": mitigation_events,
                "degrade_events": counts["degrade_events"],
                "trend": trend,
            }
        )
    domain_trends.sort(
        key=lambda item: (
            _safe_non_negative_int(item.get("total_events", 0), default=0),
            _safe_non_negative_int(item.get("failure_events", 0), default=0),
        ),
        reverse=True,
    )
    recurring_failures.sort(key=lambda item: _safe_non_negative_int(item.get("count", 0), default=0), reverse=True)
    mitigation_signals.sort(key=lambda item: _safe_non_negative_int(item.get("count", 0), default=0), reverse=True)
    degrade_signals.sort(key=lambda item: _safe_non_negative_int(item.get("count", 0), default=0), reverse=True)
    total_failure_events = int(
        sum(_safe_non_negative_int(item.get("count", 0), default=0) for item in recurring_failures)
    )
    quality_failure_categories = _build_quality_failure_category_summary(
        recurring_failures,
        total_failures=total_failure_events,
        limit=safe_limit,
    )
    total_events = int(sum(event_counts.values()))
    total_degrade_activations = int(sum(degrade_mode_counts.values()))
    feedback_helpful = int(event_counts.get("ask_feedback_helpful", 0))
    feedback_not_helpful = int(event_counts.get("ask_feedback_not_helpful", 0))
    feedback_total = feedback_helpful + feedback_not_helpful
    feedback_accepted = int(event_counts.get("ask_feedback_accepted", feedback_total))
    feedback_suppressed = int(event_counts.get("ask_feedback_suppressed", 0))
    feedback_suppressed_dedupe = int(event_counts.get("ask_feedback_suppressed_dedupe", 0))
    feedback_suppressed_rate_limited = int(
        event_counts.get("ask_feedback_suppressed_rate_limited_user", 0)
        + event_counts.get("ask_feedback_suppressed_rate_limited_channel", 0)
    )
    helpful_rate = round(feedback_helpful / feedback_total, 3) if feedback_total > 0 else None

    return {
        "total_events": total_events,
        "event_counts": {name: int(value) for name, value in sorted_events},
        "context_counts": {name: int(value) for name, value in sorted_contexts},
        "top_events": [{"event": name, "count": int(value)} for name, value in sorted_events[:safe_limit]],
        "top_contexts": [{"context": name, "count": int(value)} for name, value in sorted_contexts[:safe_limit]],
        "domain_trends": domain_trends[:safe_limit],
        "top_recurring_failures": recurring_failures[:safe_limit],
        "top_quality_failure_categories": list(quality_failure_categories.get("top", []))[:safe_limit],
        "quality_failure_categories": quality_failure_categories,
        "recent_signal_slices": {
            "mitigation": mitigation_signals[:safe_limit],
            "degrade": degrade_signals[:safe_limit],
        },
        "feedback": {
            "helpful": feedback_helpful,
            "not_helpful": feedback_not_helpful,
            "total": feedback_total,
            "helpful_rate": helpful_rate,
            "accepted": feedback_accepted,
            "suppressed": feedback_suppressed,
            "suppressed_dedupe": feedback_suppressed_dedupe,
            "suppressed_rate_limited": feedback_suppressed_rate_limited,
        },
        "degrade_mode": {
            "total_activations": total_degrade_activations,
            "mode_counts": {name: int(value) for name, value in sorted_degrade_modes},
            "path_counts": {name: int(value) for name, value in sorted_degrade_paths},
            "reason_counts": {name: int(value) for name, value in sorted_degrade_reasons},
            "top_modes": [{"mode": name, "count": int(value)} for name, value in sorted_degrade_modes[:safe_limit]],
            "top_paths": [{"path": name, "count": int(value)} for name, value in sorted_degrade_paths[:safe_limit]],
            "top_reasons": [
                {"reason": name, "count": int(value)} for name, value in sorted_degrade_reasons[:safe_limit]
            ],
        },
    }


# Global metrics collector instance
_collector: MetricsCollector | None = None


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
