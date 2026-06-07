"""Regression coverage for the live eval/run telemetry hook.

Covers ``journal_ask_outcome`` (the hook that restores the run journal +
quality-eval pipeline) and ``get_latency_stats`` (the journal-backed latency
percentiles surfaced on the dashboard). Both are additive telemetry that must
never raise into the user-response path.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import error_tracker as mod


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    journal = tmp_path / "error_journal.jsonl"
    monkeypatch.setattr(mod, "JOURNAL_FILE", journal)
    return journal


def _write_entry(journal: Path, **kwargs):
    entry = {
        "ts": kwargs.pop("ts", time.time()),
        "model_used": "gemini",
        "success": True,
        "latency_ms": 500,
    }
    entry.update(kwargs)
    with open(journal, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# journal_ask_outcome
# ---------------------------------------------------------------------------


def test_journal_ask_outcome_writes_entry(isolated_journal):
    mod.journal_ask_outcome(
        question="what time is it?",
        response_text="It is noon.",
        model_used="gemini-2.5-flash",
        success=True,
        latency_ms=1234,
    )
    lines = isolated_journal.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["question"] == "what time is it?"
    assert entry["model_used"] == "gemini-2.5-flash"
    assert entry["success"] is True
    assert entry["latency_ms"] == 1234
    assert entry["response_preview"] == "It is noon."


def test_journal_ask_outcome_maps_final_meta(isolated_journal):
    mod.journal_ask_outcome(
        question="q",
        response_text="a",
        model_used="m",
        final_meta={"routing_notes": ["picked gemini"], "tools_called": ["web_search"]},
    )
    entry = json.loads(isolated_journal.read_text().strip())
    assert entry["routing_notes"] == ["picked gemini"]
    assert entry["tools_called"] == ["web_search"]


def test_journal_ask_outcome_tolerates_bad_final_meta(isolated_journal):
    # Non-dict final_meta must not break journaling.
    mod.journal_ask_outcome(question="q", response_text="a", final_meta="not-a-dict")
    entry = json.loads(isolated_journal.read_text().strip())
    assert entry["routing_notes"] == []
    assert entry["tools_called"] == []


def test_journal_ask_outcome_never_raises(isolated_journal, monkeypatch):
    # If the underlying write fails, the hook must swallow it (telemetry is additive).
    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "record_outcome", _boom)
    # Should not raise.
    mod.journal_ask_outcome(question="q", response_text="a", latency_ms=10)


def test_journal_ask_outcome_feeds_response_time_on_success(isolated_journal):
    with patch("spending.record_response_time") as rrt:
        mod.journal_ask_outcome(
            question="q",
            response_text="a",
            model_used="gemini",
            success=True,
            latency_ms=900,
        )
    rrt.assert_called_once_with(900.0, "gemini")


def test_journal_ask_outcome_skips_response_time_without_latency(isolated_journal):
    with patch("spending.record_response_time") as rrt:
        mod.journal_ask_outcome(question="q", response_text="a", success=True, latency_ms=0)
    rrt.assert_not_called()


def test_journal_ask_outcome_skips_response_time_on_failure(isolated_journal):
    with patch("spending.record_response_time") as rrt:
        mod.journal_ask_outcome(question="q", response_text="", success=False, latency_ms=900)
    rrt.assert_not_called()


# ---------------------------------------------------------------------------
# get_latency_stats
# ---------------------------------------------------------------------------


def test_get_latency_stats_empty(isolated_journal):
    stats = mod.get_latency_stats()
    assert stats["count"] == 0
    assert stats["p50_ms"] == 0
    assert stats["p95_ms"] == 0
    assert stats["p99_ms"] == 0
    assert stats["last_10"] == []
    assert stats["by_model"] == {}


def test_get_latency_stats_computes_percentiles(isolated_journal):
    for ms in (100, 200, 300, 400, 500):
        _write_entry(isolated_journal, latency_ms=ms)
    stats = mod.get_latency_stats()
    assert stats["count"] == 5
    assert stats["avg_ms"] == 300
    assert stats["p50_ms"] == 300
    # Small-n: p95/p99 fall back to max.
    assert stats["p95_ms"] == 500
    assert stats["p99_ms"] == 500


def test_get_latency_stats_ignores_missing_latency(isolated_journal):
    _write_entry(isolated_journal, latency_ms=100)
    _write_entry(isolated_journal, latency_ms=0)
    _write_entry(isolated_journal, latency_ms=None)
    stats = mod.get_latency_stats()
    assert stats["count"] == 1


def test_get_latency_stats_by_model_breakdown(isolated_journal):
    _write_entry(isolated_journal, latency_ms=100, model_used="gemini")
    _write_entry(isolated_journal, latency_ms=300, model_used="gemini")
    _write_entry(isolated_journal, latency_ms=900, model_used="perplexity")
    stats = mod.get_latency_stats()
    assert stats["by_model"]["gemini"]["count"] == 2
    assert stats["by_model"]["gemini"]["avg_ms"] == 200
    assert stats["by_model"]["perplexity"]["count"] == 1


def test_get_latency_stats_last_10(isolated_journal):
    for ms in range(1, 16):  # 15 entries
        _write_entry(isolated_journal, latency_ms=ms * 10)
    stats = mod.get_latency_stats()
    assert len(stats["last_10"]) == 10
    # last_10 preserves chronological (append) order: entries 6..15 -> 60..150
    assert stats["last_10"][0] == 60
    assert stats["last_10"][-1] == 150
