"""Unit tests for openclaw_cli_health.py."""
from __future__ import annotations

import json
from unittest.mock import patch

import openclaw_cli_health as mod
from openclaw_cli_health import HealthResponse

# ---------------------------------------------------------------------------
# HealthResponse dataclass
# ---------------------------------------------------------------------------

def test_health_response_defaults():
    hr = HealthResponse(payload={"status": "ok"}, raw_text="ok")
    assert hr.status == ""
    assert hr.healthy is None


def test_health_response_fields():
    hr = HealthResponse(payload={"status": "ok"}, raw_text="raw", status="ok", healthy=True)
    assert hr.status == "ok"
    assert hr.healthy is True
    assert hr.payload == {"status": "ok"}
    assert hr.raw_text == "raw"


def test_health_response_unhealthy():
    hr = HealthResponse(payload="degraded", raw_text="degraded", status="warn", healthy=False)
    assert hr.healthy is False


# ---------------------------------------------------------------------------
# print_health — JSON output mode
# ---------------------------------------------------------------------------

def test_print_health_json_dict(capsys):
    hr = HealthResponse(payload={"status": "ok", "uptime_seconds": 42}, raw_text="", healthy=True)
    mod.print_health(hr, output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "ok"
    assert data["uptime_seconds"] == 42


def test_print_health_json_string(capsys):
    hr = HealthResponse(payload="simple string health", raw_text="", status="ok")
    mod.print_health(hr, output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["health"] == "simple string health"


# ---------------------------------------------------------------------------
# print_health — plain text mode (non-TTY, non-rich)
# ---------------------------------------------------------------------------

def _print_health_plain(hr: HealthResponse) -> str:
    with patch.object(mod, "_IS_TTY", False), \
         patch.object(mod, "_RICH_AVAILABLE", False), \
         patch("builtins.print") as mock_print:
        mod.print_health(hr, output_json=False)
        calls = [str(c) for c in mock_print.call_args_list]
        return "\n".join(calls)


def test_print_health_plain_healthy(capsys):
    hr = HealthResponse(payload={"status": "ok"}, raw_text="", status="ok", healthy=True)
    with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
        mod.print_health(hr, output_json=False)
    out = capsys.readouterr().out
    assert "OK" in out or "OpenClaw" in out


def test_print_health_plain_unhealthy(capsys):
    hr = HealthResponse(payload={"status": "warn"}, raw_text="", status="warn", healthy=False)
    with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
        mod.print_health(hr, output_json=False)
    out = capsys.readouterr().out
    assert "WARN" in out or "OpenClaw" in out


def test_print_health_plain_dict_payload_prints_fields(capsys):
    payload = {"uptime_seconds": 300, "bot_user": "mybot#1234", "guilds": 5}
    hr = HealthResponse(payload=payload, raw_text="", status="ok", healthy=True)
    with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
        mod.print_health(hr, output_json=False)
    out = capsys.readouterr().out
    assert "300s" in out
    assert "mybot#1234" in out


def test_print_health_plain_dict_with_checks(capsys):
    payload = {"checks": {"db": "ok", "cache": "miss"}}
    hr = HealthResponse(payload=payload, raw_text="", status="ok", healthy=True)
    with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
        mod.print_health(hr, output_json=False)
    out = capsys.readouterr().out
    assert "db" in out
    assert "cache" in out


def test_print_health_plain_string_payload(capsys):
    hr = HealthResponse(payload="Service running fine", raw_text="", status="ok", healthy=True)
    with patch.object(mod, "_IS_TTY", False), patch.object(mod, "_RICH_AVAILABLE", False):
        mod.print_health(hr, output_json=False)
    out = capsys.readouterr().out
    assert "Service running fine" in out


# ---------------------------------------------------------------------------
# _clean_sources_for_display
# ---------------------------------------------------------------------------

def test_clean_sources_bare_urls():
    sources = "https://example.com\nhttps://another.org/page"
    result = mod._clean_sources_for_display(sources)
    urls = [url for _, url in result]
    assert "https://example.com" in urls
    assert "https://another.org/page" in urls


def test_clean_sources_markdown_links():
    sources = "[Example](https://example.com)\n[Another](https://another.org)"
    result = mod._clean_sources_for_display(sources)
    assert len(result) == 2
    texts = [text for text, _ in result]
    assert "Example" in texts
    assert "Another" in texts


def test_clean_sources_numbered_bullets_stripped():
    sources = "1. https://first.com\n2. https://second.com"
    result = mod._clean_sources_for_display(sources)
    urls = [url for _, url in result]
    assert "https://first.com" in urls
    assert "https://second.com" in urls


def test_clean_sources_dash_bullets_stripped():
    sources = "- https://one.com\n* https://two.com"
    result = mod._clean_sources_for_display(sources)
    assert len(result) == 2


def test_openclaw_cli_health_unit_clean_sources_deduplicates():
    sources = "https://example.com\nhttps://example.com"
    result = mod._clean_sources_for_display(sources)
    assert len(result) == 1


def test_clean_sources_empty_input():
    result = mod._clean_sources_for_display("")
    assert result == []


def test_clean_sources_no_valid_urls():
    result = mod._clean_sources_for_display("Just some text\nNo links here")
    assert result == []


# ---------------------------------------------------------------------------
# _operator_snapshot_lines
# ---------------------------------------------------------------------------

def test_operator_snapshot_lines_always_has_visibility():
    lines = mod._operator_snapshot_lines({})
    assert len(lines) >= 1
    assert any("visibility" in line for line in lines)


def test_operator_snapshot_lines_with_readiness():
    snapshot = {"readiness_label": "handoff-ready", "readiness_detail": "plan complete", "readiness_status": "info"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("readiness" in line for line in lines)
    assert any("handoff-ready" in line for line in lines)


def test_operator_snapshot_lines_with_watch_summary():
    snapshot = {"watch_summary": "3 polls completed"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("watch" in line for line in lines)
    assert any("3 polls" in line for line in lines)


def test_operator_snapshot_lines_with_latest_output():
    snapshot = {"latest_output": "report.md (1.2 KB)"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("output" in line for line in lines)
    assert any("report.md" in line for line in lines)


def test_operator_snapshot_lines_with_latest_decision():
    snapshot = {"latest_decision": "Chose Redis for caching due to latency requirements"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("decision" in line for line in lines)


def test_operator_snapshot_lines_omits_empty_fields():
    snapshot = {}
    lines = mod._operator_snapshot_lines(snapshot)
    # Only visibility line should appear (no optional fields)
    assert len(lines) == 1


def test_operator_snapshot_lines_with_control():
    snapshot = {"control": "auto"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("control" in line for line in lines)


def test_operator_snapshot_lines_with_handoff():
    snapshot = {"latest_handoff": "handoff-2024-01-01"}
    lines = mod._operator_snapshot_lines(snapshot)
    assert any("handoff" in line for line in lines)
