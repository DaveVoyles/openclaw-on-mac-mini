"""Extended tests for src/error_tracker.py — get_recent_outcomes, get_error_stats, check_error_patterns, get_past_incidents."""
import json
import time
from pathlib import Path

import pytest

import error_tracker as mod


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    journal = tmp_path / "error_journal.jsonl"
    monkeypatch.setattr(mod, "JOURNAL_FILE", journal)
    incidents_file = tmp_path / "incidents.json"
    monkeypatch.setattr(mod, "INCIDENTS_FILE", incidents_file)
    return journal


def _write_outcomes(journal_path: Path, outcomes: list[dict]):
    """Helper to write test outcomes directly to the journal."""
    with open(journal_path, "w") as f:
        for o in outcomes:
            o.setdefault("ts", time.time())
            f.write(json.dumps(o, default=str) + "\n")


class TestGetRecentOutcomes:
    def test_error_tracker_coverage_returns_empty_when_no_file(self):
        assert mod.get_recent_outcomes() == []

    def test_returns_entries_within_window(self, isolated_journal):
        mod.record_outcome(user_id=1, question="test", model_used="gpt", success=True)
        result = mod.get_recent_outcomes(hours=24)
        assert len(result) == 1

    def test_excludes_old_entries(self, isolated_journal):
        old_entry = {
            "ts": time.time() - (48 * 3600),  # 48 hours ago
            "user_id": 1, "question": "old", "model_used": "gpt",
            "success": True, "error": "", "latency_ms": 0,
            "routing_notes": [], "tools_called": [], "reflected": False,
        }
        with open(isolated_journal, "w") as f:
            f.write(json.dumps(old_entry) + "\n")
        result = mod.get_recent_outcomes(hours=1)
        assert result == []

    def test_error_tracker_coverage_limits_results(self, isolated_journal):
        for i in range(10):
            mod.record_outcome(user_id=i, question=f"q{i}", model_used="gpt", success=True)
        result = mod.get_recent_outcomes(limit=5)
        assert len(result) <= 5

    def test_skips_malformed_lines(self, isolated_journal):
        mod.record_outcome(user_id=1, question="good", model_used="gpt", success=True)
        with open(isolated_journal, "a") as f:
            f.write("{ invalid json }\n")
        result = mod.get_recent_outcomes(hours=24)
        assert len(result) >= 1  # valid entry still returned


class TestGetErrorStats:
    def test_empty_returns_zero_stats(self):
        stats = mod.get_error_stats()
        assert stats["total"] == 0
        assert stats["success_rate"] == 1.0

    def test_all_success(self, isolated_journal):
        for _ in range(3):
            mod.record_outcome(user_id=1, question="q", model_used="gpt", success=True)
        stats = mod.get_error_stats()
        assert stats["total"] == 3
        assert stats["successes"] == 3
        assert stats["failures"] == 0
        assert stats["success_rate"] == 1.0

    def test_mixed_success_and_failure(self, isolated_journal):
        mod.record_outcome(user_id=1, question="q1", model_used="gpt", success=True)
        mod.record_outcome(user_id=1, question="q2", model_used="gpt", success=False, error_msg="boom")
        stats = mod.get_error_stats()
        assert stats["total"] == 2
        assert stats["failures"] == 1
        assert stats["success_rate"] == pytest.approx(0.5)

    def test_model_breakdown(self, isolated_journal):
        mod.record_outcome(user_id=1, question="q", model_used="gemini", success=True)
        mod.record_outcome(user_id=1, question="q", model_used="ollama", success=False, error_msg="err")
        stats = mod.get_error_stats()
        assert "gemini" in stats["model_breakdown"]
        assert "ollama" in stats["model_breakdown"]
        assert stats["model_breakdown"]["ollama"]["failures"] == 1

    def test_error_tracker_coverage_avg_latency(self, isolated_journal):
        mod.record_outcome(user_id=1, question="q", model_used="gpt", success=True, latency_ms=1000)
        mod.record_outcome(user_id=1, question="q", model_used="gpt", success=True, latency_ms=3000)
        stats = mod.get_error_stats()
        assert stats["avg_latency_ms"] == 2000

    def test_recent_errors_limited_to_5(self, isolated_journal):
        for i in range(10):
            mod.record_outcome(user_id=1, question=f"q{i}", model_used="gpt", success=False, error_msg=f"err{i}")
        stats = mod.get_error_stats()
        assert len(stats["recent_errors"]) <= 5


class TestCheckErrorPatterns:
    def test_no_patterns_when_no_data(self):
        patterns = mod.check_error_patterns()
        assert patterns == []

    def test_high_failure_rate_detected(self, isolated_journal):
        # 4 failures out of 5 total = 80% failure rate
        for i in range(4):
            mod.record_outcome(user_id=1, question=f"q{i}", model_used="gpt", success=False, error_msg="err")
        mod.record_outcome(user_id=1, question="ok", model_used="gpt", success=True)
        patterns = mod.check_error_patterns(window_minutes=60)
        types = [p["type"] for p in patterns]
        assert "high_failure_rate" in types

    def test_repeated_error_detected(self, isolated_journal):
        for _ in range(4):
            mod.record_outcome(user_id=1, question="q", model_used="gpt", success=False, error_msg="Connection refused")
        patterns = mod.check_error_patterns(window_minutes=60)
        types = [p["type"] for p in patterns]
        assert "repeated_error" in types

    def test_high_latency_detected(self, isolated_journal):
        for _ in range(5):
            mod.record_outcome(user_id=1, question="q", model_used="gpt", success=True, latency_ms=20000)
        patterns = mod.check_error_patterns(window_minutes=60)
        types = [p["type"] for p in patterns]
        assert "high_latency" in types

    def test_no_false_positive_with_few_entries(self, isolated_journal):
        # Only 2 entries — not enough for pattern detection
        mod.record_outcome(user_id=1, question="q1", model_used="gpt", success=False, error_msg="err")
        mod.record_outcome(user_id=1, question="q2", model_used="gpt", success=False, error_msg="err2")
        patterns = mod.check_error_patterns(window_minutes=60)
        # High failure rate needs total >= 3
        types = [p["type"] for p in patterns]
        assert "high_failure_rate" not in types


class TestGetPastIncidents:
    def test_error_tracker_coverage_returns_empty_when_no_file_v2(self):
        result = mod.get_past_incidents()
        assert result == []

    def test_loads_incidents(self, isolated_journal, monkeypatch):
        incidents = [
            {
                "ts": time.time(),
                "patterns": [{"type": "high_failure_rate"}],
                "diagnosis": {"cause": "overload"},
                "fix": {"action": "restart", "success": True},
            }
        ]
        mod.INCIDENTS_FILE.write_text(json.dumps(incidents))
        result = mod.get_past_incidents()
        assert len(result) == 1

    def test_filters_by_pattern_type(self, isolated_journal):
        incidents = [
            {
                "ts": time.time(),
                "patterns": [{"type": "high_failure_rate"}],
                "diagnosis": {},
                "fix": {},
            },
            {
                "ts": time.time(),
                "patterns": [{"type": "repeated_error"}],
                "diagnosis": {},
                "fix": {},
            },
        ]
        mod.INCIDENTS_FILE.write_text(json.dumps(incidents))
        result = mod.get_past_incidents(pattern_type="high_failure_rate")
        assert len(result) == 1
        assert result[0]["patterns"][0]["type"] == "high_failure_rate"

    def test_error_tracker_coverage_limits_results_v2(self, isolated_journal):
        incidents = [
            {"ts": time.time(), "patterns": [{"type": "t"}], "diagnosis": {}, "fix": {}}
            for _ in range(10)
        ]
        mod.INCIDENTS_FILE.write_text(json.dumps(incidents))
        result = mod.get_past_incidents(limit=3)
        assert len(result) == 3

    def test_handles_bad_json_file(self, isolated_journal):
        mod.INCIDENTS_FILE.write_text("not valid json")
        result = mod.get_past_incidents()
        assert result == []

# --- Merged from test_error_tracker.py ---
"""Tests for trace persistence in error_tracker outcomes."""

import json

import error_tracker as mod


def test_record_outcome_persists_explicit_trace_id(tmp_path, monkeypatch):
    journal = tmp_path / "error_journal.jsonl"
    monkeypatch.setattr(mod, "JOURNAL_FILE", journal)

    mod.record_outcome(
        user_id=1,
        question="q",
        model_used="gemini",
        success=False,
        error_msg="boom",
        trace_id="trace-explicit-1",
    )

    line = journal.read_text().strip()
    payload = json.loads(line)
    assert payload["trace_id"] == "trace-explicit-1"


def test_record_outcome_uses_active_trace_when_missing(tmp_path, monkeypatch):
    journal = tmp_path / "error_journal.jsonl"
    monkeypatch.setattr(mod, "JOURNAL_FILE", journal)
    monkeypatch.setattr(mod, "get_trace_id", lambda: "trace-from-context")

    mod.record_outcome(
        user_id=2,
        question="q2",
        model_used="gemini",
        success=True,
    )

    line = journal.read_text().strip()
    payload = json.loads(line)
    assert payload["trace_id"] == "trace-from-context"

