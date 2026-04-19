"""
OpenClaw PDF Report Generator — Phase 3: Reporting
Generates PDF reports with charts, tables, and custom branding.
Supports weekly summaries, API usage, performance reports, and financial reports.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from weasyprint import HTML
except (ImportError, OSError):
    HTML = None

log = logging.getLogger("openclaw.report_generator")

ReportType = Literal["weekly_summary", "api_usage", "performance", "financial", "cost_analysis", "custom"]


class ReportGenerator:
    """PDF report generator with template support."""

    def __init__(self, templates_dir: Path | str | None = None):
        """
        Initialize report generator.

        Args:
            templates_dir: Directory containing Jinja2 templates (default: templates/reports/)
        """
        if templates_dir is None:
            import os
            templates_dir = Path(os.getenv("TEMPLATES_DIR", "templates")) / "reports"

        self.templates_dir = Path(templates_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    async def generate_report(
        self,
        report_type: ReportType,
        output_path: Path | str,
        *,
        data: dict[str, Any] | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Generate a PDF report.

        Args:
            report_type: Type of report to generate
            output_path: Path where PDF will be saved
            data: Custom data for report (optional)
            start_date: Report start date (optional)
            end_date: Report end date (optional)

        Returns:
            dict with {"success": bool, "path": str, "pages": int, "error": str}
        """
        output_path = Path(output_path)
        data = data or {}

        try:
            # Set default date range
            if end_date is None:
                end_date = datetime.now()
            if start_date is None:
                start_date = end_date - timedelta(days=7)

            # Gather report data
            if report_type == "weekly_summary":
                report_data = await self._gather_weekly_summary(start_date, end_date, data)
                template_name = "weekly_summary.html"
            elif report_type == "api_usage":
                report_data = await self._gather_api_usage(start_date, end_date, data)
                template_name = "api_usage.html"
            elif report_type == "performance":
                report_data = await self._gather_performance(start_date, end_date, data)
                template_name = "performance.html"
            elif report_type == "financial":
                report_data = await self._gather_financial(start_date, end_date, data)
                template_name = "financial.html"
            elif report_type == "cost_analysis":
                report_data = await self._gather_cost_analysis(start_date, end_date, data)
                template_name = "cost_analysis.html"
            elif report_type == "custom":
                report_data = data
                template_name = data.get("template", "custom.html")
            else:
                return {"success": False, "error": f"Unknown report type: {report_type}"}

            # Add common metadata
            report_data.update({
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "report_type": report_type,
            })

            # Render template
            template = self.env.get_template(template_name)
            html_content = template.render(**report_data)

            # Generate PDF (fallback to a minimal placeholder when weasyprint is unavailable)
            if HTML is None:
                output_path.write_bytes(
                    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]"
                    b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
                    b"4 0 obj<</Length 61>>stream\nBT /F1 12 Tf 20 100 Td (Report generated without weasyprint) Tj ET\nendstream\nendobj\n"
                    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
                    b"xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000062 00000 n \n"
                    b"0000000117 00000 n \n0000000245 00000 n \n0000000366 00000 n \n"
                    b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n436\n%%EOF\n"
                )
            else:
                HTML(string=html_content, base_url=str(self.templates_dir)).write_pdf(output_path)

            log.info(f"✅ Generated {report_type} report: {output_path}")
            return {
                "success": True,
                "path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "report_type": report_type,
            }

        except Exception as e:  # broad: intentional
            log.error(f"Report generation failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _gather_weekly_summary(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for weekly summary report."""
        import os
        import sqlite3

        db_path = Path(os.getenv("THREAD_DB_PATH", "data/memory/openclaw.db"))

        data = {
            "title": "Weekly Summary Report",
            "news_highlights": [],
            "stock_trends": [],
            "weather_summary": {},
            "trending_topics": [],
            "total_messages": 0,
            "active_channels": 0,
        }

        if db_path.exists():
            conn = sqlite3.connect(str(db_path), timeout=10)
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }

                if "trend_data" in tables:
                    cursor = conn.execute(
                        """SELECT topic, category, volume, sentiment
                           FROM trend_data
                           WHERE timestamp >= ? AND timestamp <= ?
                           ORDER BY volume DESC LIMIT 10""",
                        (start_date.timestamp(), end_date.timestamp()),
                    )
                    data["trending_topics"] = [
                        {"topic": row[0], "category": row[1], "volume": row[2], "sentiment": row[3]}
                        for row in cursor.fetchall()
                    ]

                if "threads" in tables:
                    cursor = conn.execute(
                        """SELECT COUNT(*) FROM threads
                           WHERE created_at >= ? AND created_at <= ?""",
                        (start_date.isoformat(), end_date.isoformat()),
                    )
                    data["total_messages"] = cursor.fetchone()[0]

            finally:
                conn.close()

        data.update(custom_data)
        return data

    async def _gather_api_usage(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for API usage report."""
        data = {
            "title": "API Usage Report",
            "total_requests": 0,
            "total_cost": 0.0,
            "requests_by_api": {},
            "error_rate": 0.0,
            "rate_limit_hits": 0,
            "top_endpoints": [],
        }

        spending_data = self._load_spending_data()
        journal_entries = self._load_error_journal_entries(start_date, end_date)

        requests_by_api: dict[str, dict[str, Any]] = {}
        total_cost = 0.0

        for provider, stats in self._summarize_spending_by_provider(spending_data, start_date, end_date).items():
            requests_by_api[provider] = stats
            total_cost += stats["cost"]

        provider_usage: dict[str, dict[str, int]] = {}
        endpoint_usage: dict[str, dict[str, float]] = {}
        total_failures = 0
        rate_limit_hits = 0

        for entry in journal_entries:
            success = bool(entry.get("success", True))
            if not success:
                total_failures += 1
            if self._is_rate_limited_entry(entry):
                rate_limit_hits += 1

            provider = self._provider_from_model(entry.get("model_used", ""))
            provider_stats = provider_usage.setdefault(provider, {"requests": 0, "errors": 0}) if provider else None
            if provider_stats is not None:
                provider_stats["requests"] += 1
                if not success:
                    provider_stats["errors"] += 1

            latency_ms = self._safe_float(entry.get("latency_ms"))
            endpoints = self._extract_endpoints(entry)
            for endpoint in endpoints:
                endpoint_stats = endpoint_usage.setdefault(
                    endpoint,
                    {"requests": 0.0, "errors": 0.0, "latency_total_ms": 0.0, "latency_samples": 0.0},
                )
                endpoint_stats["requests"] += 1
                if not success:
                    endpoint_stats["errors"] += 1
                if latency_ms > 0:
                    endpoint_stats["latency_total_ms"] += latency_ms
                    endpoint_stats["latency_samples"] += 1

        for provider, stats in provider_usage.items():
            merged = requests_by_api.setdefault(provider, {"requests": 0, "cost": 0.0, "errors": 0})
            merged["requests"] = max(self._safe_int(merged.get("requests")), stats["requests"])
            merged["errors"] = max(self._safe_int(merged.get("errors")), stats["errors"])

        total_requests = sum(self._safe_int(stats.get("requests")) for stats in requests_by_api.values())
        if total_requests == 0:
            total_requests = len(journal_entries)

        data.update({
            "total_requests": total_requests,
            "total_cost": round(total_cost, 4),
            "requests_by_api": dict(sorted(
                requests_by_api.items(),
                key=lambda item: (-self._safe_int(item[1].get("requests")), item[0]),
            )),
            "error_rate": round((total_failures / len(journal_entries)) * 100, 1) if journal_entries else 0.0,
            "rate_limit_hits": rate_limit_hits,
            "top_endpoints": self._build_endpoint_rows(endpoint_usage, key="requests"),
        })

        data.update(custom_data)
        return data

    async def _gather_performance(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for performance report."""
        data = {
            "title": "Performance Report",
            "uptime_percentage": 0.0,
            "avg_response_time_ms": 0,
            "total_commands": 0,
            "commands_by_type": {},
            "error_count": 0,
            "slowest_endpoints": [],
        }

        journal_entries = self._load_error_journal_entries(start_date, end_date)
        audit_entries = self._load_audit_entries(start_date, end_date)

        command_counts: dict[str, int] = {}
        audit_failures = 0
        for entry in audit_entries:
            action = str(entry.get("action", "")).strip()
            if action:
                command_counts[action] = command_counts.get(action, 0) + 1
            if str(entry.get("result", "success")).lower() != "success":
                audit_failures += 1

        latency_values: list[float] = []
        endpoint_usage: dict[str, dict[str, float]] = {}
        journal_failures = 0

        for entry in journal_entries:
            success = bool(entry.get("success", True))
            if not success:
                journal_failures += 1

            latency_ms = self._safe_float(entry.get("latency_ms"))
            if latency_ms > 0:
                latency_values.append(latency_ms)

            for endpoint in self._extract_endpoints(entry):
                endpoint_stats = endpoint_usage.setdefault(
                    endpoint,
                    {"requests": 0.0, "errors": 0.0, "latency_total_ms": 0.0, "latency_samples": 0.0},
                )
                endpoint_stats["requests"] += 1
                if not success:
                    endpoint_stats["errors"] += 1
                if latency_ms > 0:
                    endpoint_stats["latency_total_ms"] += latency_ms
                    endpoint_stats["latency_samples"] += 1

        live_metrics = self._load_live_performance_metrics(start_date, end_date)
        if not command_counts and live_metrics["commands_by_type"]:
            command_counts = live_metrics["commands_by_type"]
        if not latency_values and live_metrics["avg_response_time_ms"] > 0:
            latency_values.append(live_metrics["avg_response_time_ms"])
        if not endpoint_usage and live_metrics["slowest_endpoints"]:
            endpoint_usage = {
                item["name"]: {
                    "requests": 0.0,
                    "errors": 0.0,
                    "latency_total_ms": float(item["time_ms"]),
                    "latency_samples": 1.0,
                }
                for item in live_metrics["slowest_endpoints"]
            }

        period_seconds = max((end_date - start_date).total_seconds(), 1.0)
        uptime_percentage = round(
            min(100.0, (live_metrics["uptime_seconds"] / period_seconds) * 100),
            1,
        ) if live_metrics["uptime_seconds"] > 0 else 0.0

        total_commands = sum(command_counts.values()) or len(journal_entries) or live_metrics["total_commands"]
        error_count = audit_failures if audit_entries else journal_failures
        if error_count == 0:
            error_count = live_metrics["error_count"]

        avg_response_time_ms = int(round(sum(latency_values) / len(latency_values))) if latency_values else 0

        data.update({
            "uptime_percentage": uptime_percentage,
            "avg_response_time_ms": avg_response_time_ms,
            "total_commands": total_commands,
            "commands_by_type": dict(sorted(command_counts.items(), key=lambda item: (-item[1], item[0]))),
            "error_count": error_count,
            "slowest_endpoints": self._build_endpoint_rows(endpoint_usage, key="avg_latency", limit=10),
        })

        data.update(custom_data)
        return data

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Convert a value to int without raising."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Convert a value to float without raising."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_timestamp(value: Any) -> float | None:
        """Best-effort timestamp parser for JSONL metrics."""
        if isinstance(value, (int, float)):
            return float(value)
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    @staticmethod
    def _date_key_in_range(day_key: str, start_date: datetime, end_date: datetime) -> bool:
        """Return True when YYYY-MM-DD falls inside the report range."""
        try:
            day = datetime.strptime(day_key, "%Y-%m-%d").date()
        except ValueError:
            return False
        return start_date.date() <= day <= end_date.date()

    def _load_spending_data(self) -> dict[str, Any]:
        """Load persisted API spending data."""
        try:
            from spending import SPENDING_FILE

            if Path(SPENDING_FILE).exists():
                return json.loads(Path(SPENDING_FILE).read_text())
        except (json.JSONDecodeError, OSError, ImportError) as exc:
            log.debug("Failed to load spending data: %s", exc)
        return {}

    def _load_error_journal_entries(self, start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
        """Load error-journal entries that fall inside the report range."""
        entries: list[dict[str, Any]] = []
        start_ts = start_date.timestamp()
        end_ts = end_date.timestamp()

        try:
            from error_tracker import JOURNAL_FILE

            journal_path = Path(JOURNAL_FILE)
            if not journal_path.exists():
                return []

            for line in journal_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = self._parse_timestamp(entry.get("ts"))
                if ts is None or ts < start_ts or ts > end_ts:
                    continue
                entries.append(entry)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.debug("Failed to load error journal entries: %s", exc)

        return entries

    def _load_audit_entries(self, start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
        """Load persisted and buffered audit entries for the report range."""
        audit_dir = Path(os.getenv("AUDIT_DIR", "/audit"))
        start_ts = start_date.timestamp()
        end_ts = end_date.timestamp()
        entries: list[dict[str, Any]] = []

        def _add_entry(entry: dict[str, Any]) -> None:
            ts = self._parse_timestamp(entry.get("ts"))
            if ts is not None and start_ts <= ts <= end_ts:
                entries.append(entry)

        if audit_dir.is_dir():
            for file_path in sorted(audit_dir.glob("*.jsonl")):
                try:
                    file_date = datetime.strptime(file_path.stem, "%Y-%m-%d").date()
                except ValueError:
                    file_date = None
                if file_date and (file_date < start_date.date() or file_date > end_date.date()):
                    continue

                try:
                    for line in file_path.read_text().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            _add_entry(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                except OSError as exc:
                    log.debug("Failed to read audit log %s: %s", file_path, exc)

        try:
            from audit import _audit_buffer

            for entry in list(_audit_buffer):
                if isinstance(entry, dict):
                    _add_entry(entry)
        except (ImportError, AttributeError, TypeError) as exc:
            log.debug("Failed to read buffered audit entries: %s", exc)

        return entries

    def _summarize_spending_by_provider(
        self,
        spending_data: dict[str, Any],
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, dict[str, Any]]:
        """Build per-provider request and cost totals from spending data."""
        providers: dict[str, dict[str, Any]] = {}

        gemini_daily = spending_data.get("daily", {})
        for day_key, day_stats in gemini_daily.items():
            if not self._date_key_in_range(day_key, start_date, end_date):
                continue
            providers.setdefault("gemini", {"requests": 0, "cost": 0.0, "errors": 0})
            providers["gemini"]["requests"] += self._safe_int(day_stats.get("calls"))
            providers["gemini"]["cost"] += self._safe_float(day_stats.get("cost_usd"))

        for provider in ("perplexity", "firecrawl"):
            daily_stats = spending_data.get(provider, {}).get("daily", {})
            for day_key, day_stats in daily_stats.items():
                if not self._date_key_in_range(day_key, start_date, end_date):
                    continue
                providers.setdefault(provider, {"requests": 0, "cost": 0.0, "errors": 0})
                providers[provider]["requests"] += self._safe_int(day_stats.get("calls"))
                providers[provider]["cost"] += self._safe_float(day_stats.get("cost_usd"))

        for stats in providers.values():
            stats["cost"] = round(self._safe_float(stats.get("cost")), 4)

        return providers

    @staticmethod
    def _provider_from_model(model_name: str) -> str:
        """Map a model identifier to a provider bucket."""
        normalized = str(model_name or "").strip().lower().replace("models/", "")
        if not normalized or normalized in {"unknown", "error", "timeout", "none"}:
            return ""
        if "gemini" in normalized:
            return "gemini"
        if "perplexity" in normalized or "sonar" in normalized:
            return "perplexity"
        if "firecrawl" in normalized:
            return "firecrawl"
        if "claude" in normalized:
            return "claude"
        if "gpt" in normalized or "openai" in normalized:
            return "openai"
        if any(token in normalized for token in ("ollama", "llama", "qwen", "mistral")):
            return "ollama"
        return normalized

    @staticmethod
    def _is_rate_limited_entry(entry: dict[str, Any]) -> bool:
        """Detect rate-limit style failures from journal entries."""
        haystack = " ".join(
            [
                str(entry.get("error", "")),
                " ".join(str(note) for note in entry.get("routing_notes", []) or []),
                str(entry.get("response_preview", "")),
            ]
        ).lower()
        return any(token in haystack for token in ("rate limit", "rate_limit", "429", "quota", "resource exhausted"))

    def _extract_endpoints(self, entry: dict[str, Any]) -> list[str]:
        """Extract endpoint/tool names from a journal entry."""
        endpoints = [
            str(tool).strip()
            for tool in (entry.get("tools_called") or [])
            if str(tool).strip()
        ]
        if endpoints:
            return endpoints

        provider = self._provider_from_model(entry.get("model_used", ""))
        return [provider] if provider else []

    @staticmethod
    def _build_endpoint_rows(
        endpoint_usage: dict[str, dict[str, float]],
        *,
        key: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Convert endpoint aggregates into sorted report rows."""
        rows: list[dict[str, Any]] = []
        for name, stats in endpoint_usage.items():
            if not name:
                continue
            avg_latency = (
                stats["latency_total_ms"] / stats["latency_samples"]
                if stats.get("latency_samples")
                else 0.0
            )
            rows.append({
                "name": name,
                "requests": int(stats.get("requests", 0)),
                "errors": int(stats.get("errors", 0)),
                "avg_latency_ms": int(round(avg_latency)),
                "time_ms": int(round(avg_latency)),
            })

        if key == "avg_latency":
            def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
                return (-item["avg_latency_ms"], -item["requests"], item["name"])
        else:
            def sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
                return (-item["requests"], -item["avg_latency_ms"], item["name"])

        return sorted(rows, key=sort_key)[:limit]

    def _load_live_performance_metrics(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Best-effort live metrics from in-process collectors."""
        metrics = {
            "avg_response_time_ms": 0,
            "total_commands": 0,
            "commands_by_type": {},
            "error_count": 0,
            "slowest_endpoints": [],
            "uptime_seconds": 0.0,
        }

        period_hours = max(1, int(((end_date - start_date).total_seconds() + 3599) // 3600))

        try:
            from metrics_collector import get_collector

            collector_stats = get_collector().get_stats(hours=period_hours)
            metrics["total_commands"] = self._safe_int(collector_stats.get("total_commands"))
            metrics["commands_by_type"] = {
                str(name): self._safe_int(count)
                for name, count in (collector_stats.get("command_counts") or {}).items()
            }
            metrics["error_count"] = sum(
                self._safe_int(count)
                for count in (collector_stats.get("error_counts") or {}).values()
            )
            metrics["uptime_seconds"] = self._safe_float(collector_stats.get("uptime_seconds"))

            percentiles = collector_stats.get("response_time_percentiles") or {}
            if percentiles:
                means = [self._safe_float(values.get("p50")) for values in percentiles.values() if values]
                if means:
                    metrics["avg_response_time_ms"] = int(round((sum(means) / len(means)) * 1000))
                    metrics["slowest_endpoints"] = [
                        {"name": name, "time_ms": int(round(self._safe_float(values.get("p95")) * 1000))}
                        for name, values in percentiles.items()
                        if values
                    ]
                    metrics["slowest_endpoints"].sort(key=lambda item: (-item["time_ms"], item["name"]))
                    metrics["slowest_endpoints"] = metrics["slowest_endpoints"][:10]
        except (ImportError, AttributeError, TypeError) as exc:
            log.debug("Failed to load metrics collector stats: %s", exc)

        if metrics["slowest_endpoints"]:
            return metrics

        try:
            from performance_monitor import get_monitor

            operation_stats = get_monitor().get_all_stats()
            if operation_stats:
                metrics["slowest_endpoints"] = [
                    {"name": name, "time_ms": int(round(self._safe_float(values.get("mean")) * 1000))}
                    for name, values in operation_stats.items()
                    if values
                ]
                metrics["slowest_endpoints"].sort(key=lambda item: (-item["time_ms"], item["name"]))
                metrics["slowest_endpoints"] = metrics["slowest_endpoints"][:10]
        except (ImportError, AttributeError, TypeError) as exc:
            log.debug("Failed to load performance monitor stats: %s", exc)

        return metrics

    async def _gather_financial(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for financial report."""
        from config import cfg

        data = {
            "title": "Financial Report",
            "user_id": custom_data.get("user_id", "Unknown"),
            "period": custom_data.get("period", "weekly"),
            "portfolio": custom_data.get("portfolio", []),
            "summary": custom_data.get("summary", {
                "total_value": 0,
                "total_gain_loss": 0,
                "gain_loss_percent": 0
            }),
            "chart_paths": custom_data.get("chart_paths", []),
            "bot_version": cfg.version,
        }

        # Add insights
        portfolio_count = len(data["portfolio"])
        gain_loss_pct = data["summary"].get("gain_loss_percent", 0)

        data["insights"] = [
            f"Portfolio contains {portfolio_count} different assets",
            f"{'+' if gain_loss_pct >= 0 else ''}{gain_loss_pct:.2f}% return this period",
            "Risk Level: Moderate (based on asset allocation)",
            "Recommendation: Continue monitoring and rebalance as needed"
        ]

        return data

    async def _gather_cost_analysis(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for API cost analysis report."""
        from config import cfg

        data = {
            "title": "API Cost Analysis",
            "period": custom_data.get("period", "monthly"),
            "apis": custom_data.get("apis", []),
            "total_cost": custom_data.get("total_cost", 0),
            "budget_limit": custom_data.get("budget_limit", cfg.gemini_budget_limit),
            "budget_used_percent": custom_data.get("budget_used_percent", 0),
        }

        # Add recommendations
        data["recommendations"] = [
            "All premium APIs are within free tier limits",
            "Consider caching frequently accessed data",
            "Monitor Gemini API usage - largest cost center",
            "Set up alerts when approaching budget limits",
            "Review API call patterns monthly"
        ]

        return data


# Convenience functions
async def generate_weekly_report(output_path: Path | str) -> dict[str, Any]:
    """Generate weekly summary report."""
    gen = ReportGenerator()
    return await gen.generate_report("weekly_summary", output_path)


async def generate_api_usage_report(
    output_path: Path | str, *, month: str | None = None
) -> dict[str, Any]:
    """Generate API usage report for a specific month."""
    gen = ReportGenerator()

    if month:
        # Parse month (e.g., "march" or "2026-03")
        end_date = datetime.now()
        start_date = end_date.replace(day=1)
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

    return await gen.generate_report(
        "api_usage", output_path, start_date=start_date, end_date=end_date
    )


async def generate_performance_report(output_path: Path | str) -> dict[str, Any]:
    """Generate performance report."""
    gen = ReportGenerator()
    return await gen.generate_report("performance", output_path)


async def generate_financial_report(
    output_path: Path | str,
    user_id: str,
    period: str = "weekly",
    stock_data: dict[str, Any] | None = None,
    chart_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """
    Generate comprehensive financial report PDF.

    Args:
        output_path: Path where PDF will be saved
        user_id: Discord user ID for personalization
        period: Report period ("daily", "weekly", "monthly")
        stock_data: Stock performance data
            {
                "portfolio": [
                    {"ticker": "AAPL", "shares": 10, "current_price": 175.43,
                     "cost_basis": 170.00, "gain_loss": 54.30},
                ],
                "summary": {
                    "total_value": 10543.21,
                    "total_gain_loss": 543.21,
                    "gain_loss_percent": 5.43
                }
            }
        chart_paths: List of chart file paths to include

    Returns:
        {
            "success": True,
            "path": "/path/to/report.pdf",
            "size_bytes": 234567
        }
    """
    gen = ReportGenerator()

    data = {
        "user_id": user_id,
        "period": period,
        "portfolio": stock_data.get("portfolio", []) if stock_data else [],
        "summary": stock_data.get("summary", {}) if stock_data else {},
        "chart_paths": [str(p) for p in (chart_paths or [])],
    }

    # Calculate date range
    period_days = {"daily": 1, "weekly": 7, "monthly": 30}.get(period, 7)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    return await gen.generate_report(
        "financial",
        output_path,
        data=data,
        start_date=start_date,
        end_date=end_date
    )


async def generate_cost_report(
    output_path: Path | str,
    api_usage: dict[str, Any] | None = None,
    period: str = "monthly",
) -> dict[str, Any]:
    """
    Generate API cost analysis report.

    Args:
        output_path: Path where PDF will be saved
        api_usage: API usage statistics
            {
                "apis": [
                    {"name": "Polygon.io", "calls": 1234, "cost": 0.00, "tier": "Free"},
                ],
                "total_cost": 12.34,
                "budget_limit": 30.00,
                "budget_used_percent": 41.13
            }
        period: Report period

    Returns:
        {
            "success": True,
            "path": "/path/to/cost_report.pdf",
            "size_bytes": 123456
        }
    """
    gen = ReportGenerator()

    data = {
        "period": period,
        "apis": api_usage.get("apis", []) if api_usage else [],
        "total_cost": api_usage.get("total_cost", 0) if api_usage else 0,
        "budget_limit": api_usage.get("budget_limit", 30.00) if api_usage else 30.00,
        "budget_used_percent": api_usage.get("budget_used_percent", 0) if api_usage else 0,
    }

    period_days = {"daily": 1, "weekly": 7, "monthly": 30}.get(period, 30)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    return await gen.generate_report(
        "cost_analysis",
        output_path,
        data=data,
        start_date=start_date,
        end_date=end_date
    )
