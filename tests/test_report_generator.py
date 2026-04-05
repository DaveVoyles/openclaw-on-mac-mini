"""Tests for PDF report generator."""
import pytest

@pytest.mark.asyncio
async def test_generate_weekly_report(tmp_path):
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from report_generator import ReportGenerator
    
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    gen = ReportGenerator()
    output_file = report_dir / "weekly.pdf"
    
    result = await gen.generate_report("weekly_summary", output_file)
    
    assert result["success"]
    assert output_file.exists()


@pytest.mark.asyncio
async def test_invalid_report_type(tmp_path):
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from report_generator import ReportGenerator
    
    gen = ReportGenerator()
    result = await gen.generate_report("invalid", tmp_path / "test.pdf")
    assert not result["success"]
