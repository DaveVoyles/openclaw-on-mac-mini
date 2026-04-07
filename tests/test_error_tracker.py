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
