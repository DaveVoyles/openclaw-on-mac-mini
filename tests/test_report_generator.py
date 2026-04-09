"""Tests for PDF report generator."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest


def _load_report_generator():
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from report_generator import ReportGenerator

    return ReportGenerator


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")


@pytest.mark.asyncio
async def test_generate_weekly_report(tmp_path):
    ReportGenerator = _load_report_generator()
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    gen = ReportGenerator()
    output_file = report_dir / "weekly.pdf"

    result = await gen.generate_report("weekly_summary", output_file)

    assert result["success"]
    assert output_file.exists()


@pytest.mark.asyncio
async def test_invalid_report_type(tmp_path):
    ReportGenerator = _load_report_generator()
    gen = ReportGenerator()
    result = await gen.generate_report("invalid", tmp_path / "test.pdf")
    assert not result["success"]


@pytest.mark.asyncio
async def test_gather_api_usage_uses_local_metrics_files(tmp_path, monkeypatch):
    ReportGenerator = _load_report_generator()
    import error_tracker
    import spending

    spending_file = tmp_path / "spending.json"
    spending_file.write_text(json.dumps({
        "daily": {
            "2026-04-01": {"calls": 2, "cost_usd": 0.12},
            "2026-04-02": {"calls": 1, "cost_usd": 0.08},
        },
        "perplexity": {
            "daily": {
                "2026-04-01": {"calls": 4, "cost_usd": 0.02},
            },
        },
        "firecrawl": {
            "daily": {
                "2026-04-02": {"calls": 2, "pages": 5, "cost_usd": 0.03},
            },
        },
    }))

    journal_file = tmp_path / "error_journal.jsonl"
    _write_jsonl(journal_file, [
        {
            "ts": datetime(2026, 4, 1, 8, 0, 0).timestamp(),
            "success": True,
            "model_used": "models/gemini-2.0-flash",
            "latency_ms": 1200,
            "tools_called": ["search_web"],
            "routing_notes": [],
            "error": "",
        },
        {
            "ts": datetime(2026, 4, 1, 10, 0, 0).timestamp(),
            "success": False,
            "model_used": "sonar-pro",
            "latency_ms": 3500,
            "tools_called": ["search_web", "browse_web"],
            "routing_notes": ["rate limit backoff"],
            "error": "429 Too Many Requests",
        },
        {
            "ts": datetime(2026, 4, 2, 9, 0, 0).timestamp(),
            "success": True,
            "model_used": "ollama/qwen2.5",
            "latency_ms": 900,
            "tools_called": [],
            "routing_notes": [],
            "error": "",
        },
    ])

    monkeypatch.setattr(spending, "SPENDING_FILE", spending_file)
    monkeypatch.setattr(error_tracker, "JOURNAL_FILE", journal_file)

    gen = ReportGenerator()
    data = await gen._gather_api_usage(
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 4, 2, 23, 59, 59),
        {},
    )

    assert data["total_requests"] == 10
    assert data["total_cost"] == pytest.approx(0.25)
    assert data["error_rate"] == pytest.approx(33.3)
    assert data["rate_limit_hits"] == 1
    assert data["requests_by_api"]["gemini"]["requests"] == 3
    assert data["requests_by_api"]["gemini"]["cost"] == pytest.approx(0.20)
    assert data["requests_by_api"]["perplexity"]["requests"] == 4
    assert data["requests_by_api"]["perplexity"]["errors"] == 1
    assert data["requests_by_api"]["firecrawl"]["requests"] == 2
    assert data["requests_by_api"]["ollama"]["requests"] == 1
    assert data["top_endpoints"][0]["name"] == "search_web"
    assert data["top_endpoints"][0]["requests"] == 2


@pytest.mark.asyncio
async def test_gather_performance_uses_audit_and_error_journal(tmp_path, monkeypatch):
    ReportGenerator = _load_report_generator()
    import error_tracker

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    _write_jsonl(audit_dir / "2026-04-01.jsonl", [
        {
            "ts": "2026-04-01T08:00:00+00:00",
            "action": "ask",
            "result": "success",
        },
        {
            "ts": "2026-04-01T09:00:00+00:00",
            "action": "search",
            "result": "failed",
        },
        {
            "ts": "2026-04-01T10:00:00+00:00",
            "action": "ask",
            "result": "success",
        },
    ])

    journal_file = tmp_path / "error_journal.jsonl"
    _write_jsonl(journal_file, [
        {
            "ts": datetime(2026, 4, 1, 8, 0, 0).timestamp(),
            "success": True,
            "latency_ms": 100,
            "tools_called": ["search_web"],
            "model_used": "models/gemini-2.0-flash",
        },
        {
            "ts": datetime(2026, 4, 1, 9, 0, 0).timestamp(),
            "success": False,
            "latency_ms": 300,
            "tools_called": ["search_web", "browse_web"],
            "model_used": "models/gemini-2.0-flash",
        },
        {
            "ts": datetime(2026, 4, 1, 10, 0, 0).timestamp(),
            "success": True,
            "latency_ms": 600,
            "tools_called": ["browse_web"],
            "model_used": "models/gemini-2.0-flash",
        },
    ])

    monkeypatch.setattr(error_tracker, "JOURNAL_FILE", journal_file)
    monkeypatch.setenv("AUDIT_DIR", str(audit_dir))
    monkeypatch.setattr(
        ReportGenerator,
        "_load_live_performance_metrics",
        lambda self, start, end: {
            "avg_response_time_ms": 0,
            "total_commands": 0,
            "commands_by_type": {},
            "error_count": 0,
            "slowest_endpoints": [],
            "uptime_seconds": 43200,
        },
    )

    gen = ReportGenerator()
    data = await gen._gather_performance(
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 4, 2, 0, 0, 0),
        {},
    )

    assert data["uptime_percentage"] == pytest.approx(50.0)
    assert data["avg_response_time_ms"] == 333
    assert data["total_commands"] == 3
    assert data["commands_by_type"] == {"ask": 2, "search": 1}
    assert data["error_count"] == 1
    assert data["slowest_endpoints"][0] == {
        "name": "browse_web",
        "requests": 2,
        "errors": 1,
        "avg_latency_ms": 450,
        "time_ms": 450,
    }


@pytest.mark.asyncio
async def test_gather_reports_gracefully_handles_missing_metrics(tmp_path, monkeypatch):
    ReportGenerator = _load_report_generator()
    import error_tracker
    import spending

    monkeypatch.setattr(spending, "SPENDING_FILE", tmp_path / "missing-spending.json")
    monkeypatch.setattr(error_tracker, "JOURNAL_FILE", tmp_path / "missing-error-journal.jsonl")
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "missing-audit"))
    monkeypatch.setattr(
        ReportGenerator,
        "_load_live_performance_metrics",
        lambda self, start, end: {
            "avg_response_time_ms": 0,
            "total_commands": 0,
            "commands_by_type": {},
            "error_count": 0,
            "slowest_endpoints": [],
            "uptime_seconds": 0.0,
        },
    )

    gen = ReportGenerator()
    start = datetime(2026, 4, 1, 0, 0, 0)
    end = datetime(2026, 4, 2, 0, 0, 0)

    api_data = await gen._gather_api_usage(start, end, {})
    perf_data = await gen._gather_performance(start, end, {})

    assert api_data["total_requests"] == 0
    assert api_data["requests_by_api"] == {}
    assert api_data["rate_limit_hits"] == 0
    assert perf_data["uptime_percentage"] == 0.0
    assert perf_data["total_commands"] == 0
    assert perf_data["commands_by_type"] == {}
    assert perf_data["slowest_endpoints"] == []
