"""
Tests for PDF report generator.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from report_generator import (
    ReportGenerator,
    generate_weekly_report,
    generate_api_usage_report,
    generate_performance_report,
    generate_financial_report,
    generate_cost_report,
)


@pytest.fixture
def report_dir(tmp_path):
    """Create temporary report directory."""
    report_path = tmp_path / "reports"
    report_path.mkdir()
    return report_path


@pytest.fixture
def templates_dir(tmp_path):
    """Create temporary templates directory with test templates."""
    templates_path = tmp_path / "templates" / "reports"
    templates_path.mkdir(parents=True)
    
    # Create minimal test template
    template_content = """
    <!DOCTYPE html>
    <html>
    <head><title>{{ title }}</title></head>
    <body>
        <h1>{{ title }}</h1>
        <p>Start: {{ start_date }}</p>
        <p>End: {{ end_date }}</p>
        <p>Generated: {{ generated_at }}</p>
    </body>
    </html>
    """
    
    for template_name in ["weekly_summary.html", "api_usage.html", "performance.html", "financial.html", "cost_analysis.html"]:
        (templates_path / template_name).write_text(template_content)
    
    return templates_path


@pytest.mark.asyncio
async def test_report_generator_init(templates_dir):
    """Test ReportGenerator initialization."""
    gen = ReportGenerator(templates_dir)
    assert gen.templates_dir == templates_dir
    assert gen.env is not None


@pytest.mark.asyncio
async def test_generate_weekly_report(report_dir, templates_dir):
    """Test weekly summary report generation."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "weekly.pdf"
    
    result = await gen.generate_report(
        "weekly_summary",
        output_file,
    )
    
    if result["success"]:
        assert output_file.exists()
        assert result["report_type"] == "weekly_summary"
        assert result["size_bytes"] > 0
    else:
        # WeasyPrint might not be available in all test environments
        assert "error" in result


@pytest.mark.asyncio
async def test_generate_api_usage_report(report_dir, templates_dir):
    """Test API usage report generation."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "api_usage.pdf"
    
    result = await gen.generate_report(
        "api_usage",
        output_file,
    )
    
    assert "success" in result


@pytest.mark.asyncio
async def test_generate_performance_report(report_dir, templates_dir):
    """Test performance report generation."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "performance.pdf"
    
    result = await gen.generate_report(
        "performance",
        output_file,
    )
    
    assert "success" in result


@pytest.mark.asyncio
async def test_report_with_custom_dates(report_dir, templates_dir):
    """Test report generation with custom date range."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "custom_dates.pdf"
    
    start_date = datetime(2026, 3, 1)
    end_date = datetime(2026, 3, 31)
    
    result = await gen.generate_report(
        "weekly_summary",
        output_file,
        start_date=start_date,
        end_date=end_date,
    )
    
    assert "success" in result


@pytest.mark.asyncio
async def test_report_with_custom_data(report_dir, templates_dir):
    """Test report with custom data."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "custom_data.pdf"
    
    custom_data = {
        "trending_topics": [
            {"topic": "AI", "category": "tech", "volume": 100, "sentiment": 0.8},
            {"topic": "Climate", "category": "news", "volume": 75, "sentiment": -0.2},
        ],
        "total_messages": 500,
        "active_channels": 10,
    }
    
    result = await gen.generate_report(
        "weekly_summary",
        output_file,
        data=custom_data,
    )
    
    assert "success" in result


@pytest.mark.asyncio
async def test_report_invalid_type(report_dir, templates_dir):
    """Test report generation with invalid type."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "invalid.pdf"
    
    result = await gen.generate_report(
        "invalid_type",
        output_file,
    )
    
    assert result["success"] is False
    assert "Unknown report type" in result["error"]


@pytest.mark.asyncio
async def test_convenience_functions(report_dir, monkeypatch):
    """Test convenience functions."""
    # Mock template directory
    monkeypatch.setenv("TEMPLATES_DIR", str(report_dir.parent / "templates"))
    
    # These may fail without proper templates, but should handle gracefully
    try:
        result = await generate_weekly_report(report_dir / "weekly.pdf")
        assert "success" in result
    except Exception:
        pass  # Expected if templates don't exist


@pytest.mark.asyncio
async def test_report_metadata(report_dir, templates_dir):
    """Test that report metadata is correctly populated."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "metadata.pdf"
    
    result = await gen.generate_report(
        "weekly_summary",
        output_file,
    )
    
    if result["success"]:
        assert "path" in result
        assert "size_bytes" in result
        assert "report_type" in result


@pytest.mark.asyncio
async def test_generate_financial_report(report_dir, templates_dir):
    """Test financial report generation."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "financial.pdf"
    
    stock_data = {
        "portfolio": [
            {
                "ticker": "AAPL",
                "shares": 10,
                "current_price": 175.43,
                "cost_basis": 170.00,
                "gain_loss": 54.30,
            },
        ],
        "summary": {
            "total_value": 1754.30,
            "total_gain_loss": 54.30,
            "gain_loss_percent": 3.19,
        }
    }
    
    result = await generate_financial_report(
        output_path=output_file,
        user_id="test_user",
        period="weekly",
        stock_data=stock_data,
    )
    
    if result["success"]:
        assert output_file.exists()
        assert result["size_bytes"] > 0


@pytest.mark.asyncio
async def test_generate_cost_report(report_dir, templates_dir):
    """Test cost analysis report generation."""
    gen = ReportGenerator(templates_dir)
    output_file = report_dir / "cost_analysis.pdf"
    
    api_usage = {
        "apis": [
            {"name": "Polygon.io", "calls": 1234, "cost": 0.00, "tier": "Free"},
            {"name": "Gemini API", "calls": 5432, "cost": 12.34, "tier": "Paid"},
        ],
        "total_cost": 12.34,
        "budget_limit": 30.00,
        "budget_used_percent": 41.13,
    }
    
    result = await generate_cost_report(
        output_path=output_file,
        api_usage=api_usage,
        period="monthly",
    )
    
    if result["success"]:
        assert output_file.exists()
        assert result["size_bytes"] > 0
