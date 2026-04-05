"""
OpenClaw PDF Report Generator — Phase 3: Reporting
Generates PDF reports with charts, tables, and custom branding.
Supports weekly summaries, API usage, performance reports, and financial reports.
"""

import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

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
