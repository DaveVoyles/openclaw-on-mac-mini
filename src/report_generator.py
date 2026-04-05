"""
OpenClaw PDF Report Generator — Phase 3: Reporting
Generates PDF reports with charts, tables, and custom branding.
Supports weekly summaries, API usage, and performance reports.
"""

import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

log = logging.getLogger("openclaw.report_generator")

ReportType = Literal["weekly_summary", "api_usage", "performance", "custom"]


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

            # Generate PDF
            HTML(string=html_content, base_url=str(self.templates_dir)).write_pdf(output_path)

            log.info(f"✅ Generated {report_type} report: {output_path}")
            return {
                "success": True,
                "path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "report_type": report_type,
            }

        except Exception as e:
            log.error(f"Report generation failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _gather_weekly_summary(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for weekly summary report."""
        import sqlite3
        import os

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
            conn = sqlite3.connect(str(db_path))
            try:
                # Get trending topics
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

                # Get message count
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

        # TODO: Integrate with actual API tracking when available
        data.update(custom_data)
        return data

    async def _gather_performance(
        self, start_date: datetime, end_date: datetime, custom_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Gather data for performance report."""
        data = {
            "title": "Performance Report",
            "uptime_percentage": 99.9,
            "avg_response_time_ms": 0,
            "total_commands": 0,
            "commands_by_type": {},
            "error_count": 0,
            "slowest_endpoints": [],
        }

        # TODO: Integrate with actual performance metrics
        data.update(custom_data)
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
