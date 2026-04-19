"""
Extended tests for src/error_tracker.py — covering previously uncovered functions:
get_recent_outcomes, get_error_stats, check_error_patterns, execute_fix,
get_past_incidents, record_incident.
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import error_tracker as mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    journal = tmp_path / "error_journal.jsonl"
    monkeypatch.setattr(mod, "JOURNAL_FILE", journal)
    return journal


@pytest.fixture(autouse=True)
def isolated_incidents(tmp_path, monkeypatch):
    incidents_file = tmp_path / "incidents.json"
    monkeypatch.setattr(mod, "INCIDENTS_FILE", incidents_file)
    return incidents_file


def _write_entry(journal: Path, **kwargs):
    """Write a journal entry with sensible defaults."""
    entry = {
        "ts": kwargs.pop("ts", time.time()),
        "trace_id": "",
        "user_id": 1,
        "question": "test question",
        "model_used": "gemini",
        "success": True,
        "error": "",
        "latency_ms": 500,
        "routing_notes": [],
        "tools_called": [],
        "reflected": False,
        "scope_mode": None,
        "lock_mode": None,
        "anchor_id": None,
        "anchor_age": None,
        "profile_values": {},
        "response_preview": "",
        "explainability": {},
    }
    entry.update(kwargs)
    with open(journal, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------


def test_record_outcome_writes_to_journal(isolated_journal):
    mod.record_outcome(user_id=1, question="hello", model_used="gemini", success=True)
    lines = isolated_journal.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["user_id"] == 1
    assert entry["question"] == "hello"
    assert entry["success"] is True


def test_record_outcome_truncates_long_question(isolated_journal):
    mod.record_outcome(question="x" * 500)
    entry = json.loads(isolated_journal.read_text().strip())
    assert len(entry["question"]) <= 200


def test_record_outcome_truncates_long_error(isolated_journal):
    mod.record_outcome(error_msg="e" * 1000)
    entry = json.loads(isolated_journal.read_text().strip())
    assert len(entry["error"]) <= 500


def test_record_outcome_truncates_response_preview(isolated_journal):
    mod.record_outcome(response_preview="r" * 5000)
    entry = json.loads(isolated_journal.read_text().strip())
    assert len(entry["response_preview"]) <= 2000


def test_record_outcome_defaults(isolated_journal):
    mod.record_outcome()
    entry = json.loads(isolated_journal.read_text().strip())
    assert entry["user_id"] == 0
    assert entry["model_used"] == "unknown"
    assert entry["routing_notes"] == []
    assert entry["tools_called"] == []
    assert entry["profile_values"] == {}
    assert entry["explainability"] == {}


def test_record_outcome_all_fields(isolated_journal):
    mod.record_outcome(
        user_id=7,
        question="What time is it?",
        model_used="gemini-pro",
        success=False,
        error_msg="timeout",
        latency_ms=3000,
        routing_notes=["used fallback"],
        tools_called=["clock"],
        reflected=True,
        scope_mode="global",
        lock_mode="strict",
        anchor_id="abc123",
        anchor_age=42.5,
        profile_values={"pref": "dark"},
        response_preview="partial...",
        explainability={"reason": "fast"},
        trace_id="trace-xyz",
    )
    entry = json.loads(isolated_journal.read_text().strip())
    assert entry["user_id"] == 7
    assert entry["success"] is False
    assert entry["error"] == "timeout"
    assert entry["latency_ms"] == 3000
    assert entry["routing_notes"] == ["used fallback"]
    assert entry["reflected"] is True
    assert entry["trace_id"] == "trace-xyz"


def test_record_outcome_replaces_no_trace_id(isolated_journal, monkeypatch):
    monkeypatch.setattr(mod, "get_trace_id", lambda: "no-trace")
    mod.record_outcome(trace_id="no-trace")
    entry = json.loads(isolated_journal.read_text().strip())
    assert entry["trace_id"] == ""


def test_record_outcome_handles_write_failure(monkeypatch):
    """Should not raise even when journal write fails."""
    monkeypatch.setattr(mod, "JOURNAL_FILE", Path("/no/such/path/journal.jsonl"))
    # Should not raise
    mod.record_outcome(user_id=1, question="q")


# ---------------------------------------------------------------------------
# get_recent_outcomes
# ---------------------------------------------------------------------------


def test_get_recent_outcomes_empty_file(isolated_journal):
    result = mod.get_recent_outcomes()
    assert result == []


def test_get_recent_outcomes_file_missing(monkeypatch):
    monkeypatch.setattr(mod, "JOURNAL_FILE", Path("/no/such/file.jsonl"))
    result = mod.get_recent_outcomes()
    assert result == []


def test_get_recent_outcomes_returns_recent(isolated_journal):
    _write_entry(isolated_journal, question="recent")
    result = mod.get_recent_outcomes(hours=1)
    assert len(result) == 1
    assert result[0]["question"] == "recent"


def test_get_recent_outcomes_filters_old(isolated_journal):
    old_ts = time.time() - (48 * 3600)
    _write_entry(isolated_journal, ts=old_ts, question="old")
    _write_entry(isolated_journal, question="new")
    result = mod.get_recent_outcomes(hours=24)
    assert len(result) == 1
    assert result[0]["question"] == "new"


def test_get_recent_outcomes_respects_limit(isolated_journal):
    for i in range(20):
        _write_entry(isolated_journal, question=f"q{i}")
    result = mod.get_recent_outcomes(limit=5)
    assert len(result) == 5


def test_get_recent_outcomes_skips_blank_lines(isolated_journal):
    with open(isolated_journal, "w") as f:
        f.write("\n")
        f.write(json.dumps({"ts": time.time(), "question": "valid", "success": True}) + "\n")
        f.write("   \n")
    result = mod.get_recent_outcomes()
    assert len(result) == 1


def test_get_recent_outcomes_skips_invalid_json(isolated_journal):
    with open(isolated_journal, "w") as f:
        f.write("not json\n")
        f.write(json.dumps({"ts": time.time(), "question": "ok", "success": True}) + "\n")
    result = mod.get_recent_outcomes()
    assert len(result) == 1
    assert result[0]["question"] == "ok"


# ---------------------------------------------------------------------------
# get_error_stats
# ---------------------------------------------------------------------------


def test_get_error_stats_empty():
    stats = mod.get_error_stats()
    assert stats["total"] == 0
    assert stats["success_rate"] == 1.0
    assert stats["recent_errors"] == []
    assert stats["model_breakdown"] == {}


def test_get_error_stats_all_success(isolated_journal):
    for _ in range(5):
        _write_entry(isolated_journal, success=True, latency_ms=1000)
    stats = mod.get_error_stats()
    assert stats["total"] == 5
    assert stats["successes"] == 5
    assert stats["failures"] == 0
    assert stats["success_rate"] == 1.0
    assert stats["avg_latency_ms"] == 1000


def test_get_error_stats_mixed(isolated_journal):
    for _ in range(3):
        _write_entry(isolated_journal, success=True, model_used="gemini", latency_ms=200)
    for _ in range(2):
        _write_entry(isolated_journal, success=False, model_used="ollama", error="boom", latency_ms=800)
    stats = mod.get_error_stats()
    assert stats["total"] == 5
    assert stats["successes"] == 3
    assert stats["failures"] == 2
    assert stats["success_rate"] == pytest.approx(0.6)
    assert "gemini" in stats["model_breakdown"]
    assert "ollama" in stats["model_breakdown"]
    assert stats["model_breakdown"]["ollama"]["failures"] == 2


def test_get_error_stats_recent_errors_capped_at_5(isolated_journal):
    for i in range(10):
        _write_entry(isolated_journal, success=False, error=f"err{i}")
    stats = mod.get_error_stats()
    assert len(stats["recent_errors"]) == 5


def test_get_error_stats_avg_latency(isolated_journal):
    _write_entry(isolated_journal, latency_ms=1000)
    _write_entry(isolated_journal, latency_ms=3000)
    stats = mod.get_error_stats()
    assert stats["avg_latency_ms"] == 2000


def test_get_error_stats_model_breakdown(isolated_journal):
    _write_entry(isolated_journal, model_used="gemini", success=True)
    _write_entry(isolated_journal, model_used="gemini", success=False)
    _write_entry(isolated_journal, model_used="ollama", success=True)
    stats = mod.get_error_stats()
    assert stats["model_breakdown"]["gemini"]["total"] == 2
    assert stats["model_breakdown"]["gemini"]["failures"] == 1
    assert stats["model_breakdown"]["ollama"]["total"] == 1
    assert stats["model_breakdown"]["ollama"]["failures"] == 0


# ---------------------------------------------------------------------------
# check_error_patterns
# ---------------------------------------------------------------------------


def test_check_error_patterns_empty():
    patterns = mod.check_error_patterns()
    assert patterns == []


def test_check_error_patterns_high_failure_rate_warning(isolated_journal):
    # 40% failure → warning
    for _ in range(6):
        _write_entry(isolated_journal, success=True)
    for _ in range(4):
        _write_entry(isolated_journal, success=False, error="something broke")
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "high_failure_rate" in types
    hfr = next(p for p in patterns if p["type"] == "high_failure_rate")
    assert hfr["severity"] == "warning"


def test_check_error_patterns_high_failure_rate_critical(isolated_journal):
    # >50% failure → critical
    for _ in range(2):
        _write_entry(isolated_journal, success=True)
    for _ in range(8):
        _write_entry(isolated_journal, success=False, error="critical error")
    patterns = mod.check_error_patterns(window_minutes=60)
    hfr = next((p for p in patterns if p["type"] == "high_failure_rate"), None)
    assert hfr is not None
    assert hfr["severity"] == "critical"


def test_check_error_patterns_no_trigger_below_threshold(isolated_journal):
    # Only 2 failures — needs >=3 total
    for _ in range(2):
        _write_entry(isolated_journal, success=False, error="minor")
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "high_failure_rate" not in types


def test_check_error_patterns_repeated_error(isolated_journal):
    for _ in range(4):
        _write_entry(isolated_journal, success=False, error="Connection refused by upstream")
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "repeated_error" in types
    rep = next(p for p in patterns if p["type"] == "repeated_error")
    assert rep["count"] == 4


def test_check_error_patterns_repeated_error_not_triggered_below_3(isolated_journal):
    for _ in range(2):
        _write_entry(isolated_journal, success=False, error="Unique error message abc")
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "repeated_error" not in types


def test_check_error_patterns_ollama_timeout_streak(isolated_journal):
    for _ in range(5):
        _write_entry(
            isolated_journal,
            success=False,
            routing_notes=["Ollama timed out after 30s"],
        )
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "ollama_timeout_streak" in types


def test_check_error_patterns_ollama_not_triggered_below_3(isolated_journal):
    for _ in range(2):
        _write_entry(
            isolated_journal,
            success=False,
            routing_notes=["Ollama timed out"],
        )
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "ollama_timeout_streak" not in types


def test_check_error_patterns_high_latency(isolated_journal):
    for _ in range(5):
        _write_entry(isolated_journal, latency_ms=20000)
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "high_latency" in types


def test_check_error_patterns_no_high_latency_below_threshold(isolated_journal):
    for _ in range(5):
        _write_entry(isolated_journal, latency_ms=1000)
    patterns = mod.check_error_patterns(window_minutes=60)
    types = [p["type"] for p in patterns]
    assert "high_latency" not in types


def test_check_error_patterns_model_failures_warning(isolated_journal):
    # 4 failures out of 5 for same model (80%) → warning (rate not > 0.8)
    _write_entry(isolated_journal, model_used="bad-model", success=True)
    for _ in range(4):
        _write_entry(isolated_journal, model_used="bad-model", success=False, error="model broke")
    patterns = mod.check_error_patterns(window_minutes=60)
    model_fail = next((p for p in patterns if p["type"] == "model_failures"), None)
    assert model_fail is not None
    assert model_fail["severity"] == "warning"


def test_check_error_patterns_model_failures_critical(isolated_journal):
    # 5/5 failures (100%) → critical (rate > 0.8)
    for _ in range(5):
        _write_entry(isolated_journal, model_used="bad-model", success=False, error="model broke")
    patterns = mod.check_error_patterns(window_minutes=60)
    model_fail = next((p for p in patterns if p["type"] == "model_failures"), None)
    assert model_fail is not None
    assert model_fail["severity"] == "critical"


# ---------------------------------------------------------------------------
# execute_fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_fix_low_confidence_skips():
    diag = {"fix_type": "restart_service", "fix_target": "sonarr", "confidence": 0.4}
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "skipped"
    assert result["success"] is False


@pytest.mark.asyncio
async def test_execute_fix_switch_model():
    diag = {"fix_type": "switch_model", "fix_target": "ollama", "confidence": 0.9}
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "switch_model:ollama"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_fix_switch_model_empty_target():
    diag = {"fix_type": "switch_model", "fix_target": "", "confidence": 0.9}
    result = await mod.execute_fix(diag)
    assert "gemini" in result["action_taken"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_fix_increase_timeout():
    diag = {"fix_type": "increase_timeout", "fix_target": "ollama", "confidence": 0.8}
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "increase_timeout"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_fix_manual_required():
    diag = {
        "fix_type": "manual_required",
        "fix_target": "",
        "confidence": 0.9,
        "explanation": "Human must intervene",
    }
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "manual_required"
    assert result["success"] is False
    assert "Human must intervene" in result["detail"]


@pytest.mark.asyncio
async def test_execute_fix_none_type():
    diag = {"fix_type": "none", "fix_target": "", "confidence": 0.9, "explanation": "all good"}
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "manual_required"


@pytest.mark.asyncio
async def test_execute_fix_restart_service_safe_target():
    async def fake_restart(name):
        return f"Restarted {name}"

    with patch.dict("sys.modules", {"skills": MagicMock(restart_container=AsyncMock(return_value="Restarted sonarr"))}):
        diag = {"fix_type": "restart_service", "fix_target": "sonarr", "confidence": 0.9}
        result = await mod.execute_fix(diag)
        assert result["action_taken"] == "restart_service:sonarr"
        assert result["success"] is True


@pytest.mark.asyncio
async def test_execute_fix_restart_service_unsafe_target():
    diag = {"fix_type": "restart_service", "fix_target": "nginx", "confidence": 0.9}
    result = await mod.execute_fix(diag)
    assert result["action_taken"] == "skipped"
    assert result["success"] is False
    assert "not in safe restart list" in result["detail"]


@pytest.mark.asyncio
async def test_execute_fix_clear_circuit_breaker_tool_not_found():
    mock_cb = MagicMock()
    mock_cb._tools = {}
    with patch.dict("sys.modules", {"tool_health": MagicMock(circuit_breaker=mock_cb)}):
        diag = {"fix_type": "clear_circuit_breaker", "fix_target": "missing-tool", "confidence": 0.9}
        result = await mod.execute_fix(diag)
        assert result["success"] is False
        assert "not found" in result["detail"]


@pytest.mark.asyncio
async def test_execute_fix_clear_circuit_breaker_success():
    mock_tool = MagicMock()
    mock_tool.failures = 5
    mock_tool.last_failure = 999
    mock_cb = MagicMock()
    mock_cb._tools = {"sonarr": mock_tool}
    with patch.dict("sys.modules", {"tool_health": MagicMock(circuit_breaker=mock_cb)}):
        diag = {"fix_type": "clear_circuit_breaker", "fix_target": "sonarr", "confidence": 0.9}
        result = await mod.execute_fix(diag)
        assert result["success"] is True
        assert mock_tool.failures == 0


# ---------------------------------------------------------------------------
# get_past_incidents
# ---------------------------------------------------------------------------


def test_get_past_incidents_no_file(isolated_incidents):
    isolated_incidents.unlink(missing_ok=True)
    result = mod.get_past_incidents()
    assert result == []


def test_get_past_incidents_empty_list(isolated_incidents):
    isolated_incidents.write_text("[]")
    result = mod.get_past_incidents()
    assert result == []


def test_get_past_incidents_returns_all(isolated_incidents):
    incidents = [
        {"ts": time.time(), "patterns": [{"type": "high_failure_rate"}], "diagnosis": {}, "fix": {}},
        {"ts": time.time(), "patterns": [{"type": "repeated_error"}], "diagnosis": {}, "fix": {}},
    ]
    isolated_incidents.write_text(json.dumps(incidents))
    result = mod.get_past_incidents()
    assert len(result) == 2


def test_get_past_incidents_filter_by_type(isolated_incidents):
    incidents = [
        {"ts": time.time(), "patterns": [{"type": "high_failure_rate"}], "diagnosis": {}, "fix": {}},
        {"ts": time.time(), "patterns": [{"type": "repeated_error"}], "diagnosis": {}, "fix": {}},
        {"ts": time.time(), "patterns": [{"type": "high_failure_rate"}], "diagnosis": {}, "fix": {}},
    ]
    isolated_incidents.write_text(json.dumps(incidents))
    result = mod.get_past_incidents(pattern_type="high_failure_rate")
    assert len(result) == 2
    assert all(any(p["type"] == "high_failure_rate" for p in i["patterns"]) for i in result)


def test_get_past_incidents_respects_limit(isolated_incidents):
    incidents = [{"ts": i, "patterns": [{"type": "high_failure_rate"}], "diagnosis": {}, "fix": {}} for i in range(10)]
    isolated_incidents.write_text(json.dumps(incidents))
    result = mod.get_past_incidents(limit=3)
    assert len(result) == 3


def test_get_past_incidents_handles_bad_json(isolated_incidents):
    isolated_incidents.write_text("not valid json {{")
    result = mod.get_past_incidents()
    assert result == []


# ---------------------------------------------------------------------------
# record_incident
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_incident_creates_file(isolated_incidents):
    patterns = [{"type": "high_failure_rate", "severity": "critical", "detail": "5/5", "count": 5}]
    diagnosis = {"cause": "overload", "fix_type": "manual_required", "fix_target": "", "confidence": 0.5}
    fix_result = {"action_taken": "manual_required", "success": False, "detail": "needs human"}
    await mod.record_incident(patterns, diagnosis, fix_result)
    assert isolated_incidents.exists()
    data = json.loads(isolated_incidents.read_text())
    assert len(data) == 1
    assert data[0]["diagnosis"]["cause"] == "overload"


@pytest.mark.asyncio
async def test_record_incident_appends(isolated_incidents):
    patterns = [{"type": "high_failure_rate", "severity": "warning", "detail": "4/5", "count": 4}]
    diag = {"cause": "test", "fix_type": "none", "fix_target": "", "confidence": 0.3}
    fix = {"action_taken": "none", "success": False, "detail": ""}
    await mod.record_incident(patterns, diag, fix)
    await mod.record_incident(patterns, diag, fix)
    data = json.loads(isolated_incidents.read_text())
    assert len(data) == 2


@pytest.mark.asyncio
async def test_record_incident_caps_at_100(isolated_incidents):
    # Seed 100 existing incidents
    existing = [{"ts": i, "patterns": [], "diagnosis": {}, "fix": {}} for i in range(100)]
    isolated_incidents.write_text(json.dumps(existing))
    patterns = [{"type": "repeated_error", "severity": "warning", "detail": "x", "count": 3}]
    diag = {"cause": "test", "fix_type": "none", "fix_target": "", "confidence": 0.3}
    fix = {"action_taken": "none", "success": False, "detail": ""}
    await mod.record_incident(patterns, diag, fix)
    data = json.loads(isolated_incidents.read_text())
    assert len(data) == 100  # Capped at 100


@pytest.mark.asyncio
async def test_record_incident_successful_fix_tries_rule(isolated_incidents, monkeypatch):
    rules_added = []

    async def fake_add_rule(text, source_message=""):
        rules_added.append(text)

    fake_rules = MagicMock()
    fake_rules.add_rule = fake_add_rule

    with patch.dict("sys.modules", {"rules_engine": fake_rules}):
        patterns = [{"type": "high_failure_rate", "severity": "critical", "detail": "5/5", "count": 5}]
        diag = {
            "cause": "overload",
            "fix_type": "restart_service",
            "fix_target": "sonarr",
            "confidence": 0.9,
            "explanation": "Service was down",
        }
        fix = {"action_taken": "restart_service:sonarr", "success": True, "detail": "Restarted"}
        await mod.record_incident(patterns, diag, fix)

    assert len(rules_added) == 1
    assert "high_failure_rate" in rules_added[0]


@pytest.mark.asyncio
async def test_record_incident_no_rule_on_failure(isolated_incidents, monkeypatch):
    rules_added = []

    async def fake_add_rule(text, source_message=""):
        rules_added.append(text)

    fake_rules = MagicMock()
    fake_rules.add_rule = fake_add_rule

    with patch.dict("sys.modules", {"rules_engine": fake_rules}):
        patterns = [{"type": "high_failure_rate", "severity": "critical", "detail": "5/5", "count": 5}]
        diag = {
            "cause": "overload",
            "fix_type": "manual_required",
            "fix_target": "",
            "confidence": 0.5,
            "explanation": "No fix available",
        }
        fix = {"action_taken": "manual_required", "success": False, "detail": "needs human"}
        await mod.record_incident(patterns, diag, fix)

    assert rules_added == []
