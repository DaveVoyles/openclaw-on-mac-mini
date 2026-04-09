"""Tests for rules_engine.py — correction detection and rule persistence."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import rules_engine as mod
from rules_engine import detect_correction

# ---------------------------------------------------------------------------
# detect_correction — pure function
# ---------------------------------------------------------------------------

class TestDetectCorrection:
    def test_empty_string_returns_false(self):
        assert detect_correction("") is False

    def test_starts_with_no(self):
        assert detect_correction("No, that's not right") is True

    def test_thats_wrong(self):
        assert detect_correction("That's wrong, you should use different data") is True

    def test_actually(self):
        assert detect_correction("Actually, the score was 4-2") is True

    def test_i_told_you(self):
        assert detect_correction("I told you to use markdown tables") is True

    def test_dont_do_that(self):
        assert detect_correction("Don't do that again") is True

    def test_stop_doing(self):
        assert detect_correction("Stop doing that thing with the footer") is True

    def test_remember_that(self):
        assert detect_correction("Remember that I prefer concise answers") is True

    def test_incorrect(self):
        assert detect_correction("This is incorrect data") is True

    def test_you_forgot(self):
        assert detect_correction("You forgot to include the sources") is True

    def test_wrong(self):
        assert detect_correction("The number is wrong") is True

    def test_you_should(self):
        assert detect_correction("You should include timestamps") is True

    def test_i_prefer(self):
        assert detect_correction("I prefer bullet points over tables") is True

    def test_i_said(self):
        assert detect_correction("I said to use the sports pack") is True

    def test_normal_message_returns_false(self):
        assert detect_correction("What's happening in the Premier League?") is False

    def test_casual_question_returns_false(self):
        assert detect_correction("Tell me about the upcoming games") is False

    def test_case_insensitive_no(self):
        assert detect_correction("NO, that is wrong") is True


# ---------------------------------------------------------------------------
# _load_rules / _save_rules (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_rules_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    result = await mod._load_rules()
    assert result == []


@pytest.mark.asyncio
async def test_save_and_load_rules_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    rules = [{"id": "rule_1", "rule": "Always use markdown tables", "source": "test"}]
    await mod._save_rules(rules)
    loaded = await mod._load_rules()
    assert loaded == rules


@pytest.mark.asyncio
async def test_load_rules_handles_corrupt_json(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text("{{corrupt}", encoding="utf-8")
    monkeypatch.setattr(mod, "RULES_FILE", rules_file)
    result = await mod._load_rules()
    assert result == []


# ---------------------------------------------------------------------------
# add_rule (async) — with mocked ChromaDB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_rule_persists_to_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    with patch.dict("sys.modules", {"vector_store": MagicMock(add_document=AsyncMock())}):
        entry = await mod.add_rule("Always include sources", "because user said so")
    assert entry["rule"] == "Always include sources"
    assert "id" in entry
    assert entry["access_count"] == 0

    # Verify file was actually written
    saved = json.loads((tmp_path / "rules.json").read_text())
    assert len(saved) == 1
    assert saved[0]["rule"] == "Always include sources"


@pytest.mark.asyncio
async def test_add_rule_truncates_source(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    with patch.dict("sys.modules", {"vector_store": MagicMock(add_document=AsyncMock())}):
        entry = await mod.add_rule("Some rule", "x" * 1000)
    assert len(entry["source"]) <= 500


@pytest.mark.asyncio
async def test_add_rule_chromadb_failure_ignored(tmp_path, monkeypatch):
    """ChromaDB failure should not prevent rule from being saved to JSON."""
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    failing_store = MagicMock(add_document=AsyncMock(side_effect=Exception("DB down")))
    with patch.dict("sys.modules", {"vector_store": failing_store}):
        entry = await mod.add_rule("Rule text", "source")
    assert entry["rule"] == "Rule text"
    saved = json.loads((tmp_path / "rules.json").read_text())
    assert len(saved) == 1


# ---------------------------------------------------------------------------
# get_all_rules (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_rules_returns_list(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    with patch.dict("sys.modules", {"vector_store": MagicMock(add_document=AsyncMock())}):
        await mod.add_rule("Rule one", "")
        await mod.add_rule("Rule two", "")
    rules = await mod.get_all_rules()
    assert len(rules) == 2
    assert any(r["rule"] == "Rule one" for r in rules)


# ---------------------------------------------------------------------------
# delete_rule (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_rule_removes_from_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    mock_store = MagicMock(add_document=AsyncMock(), delete_document=AsyncMock())
    with patch.dict("sys.modules", {"vector_store": mock_store}):
        entry = await mod.add_rule("Delete me", "src")
        rule_id = entry["id"]
        result = await mod.delete_rule(rule_id)
    assert result is True
    saved = json.loads((tmp_path / "rules.json").read_text())
    assert all(r["id"] != rule_id for r in saved)


@pytest.mark.asyncio
async def test_delete_rule_missing_id_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    result = await mod.delete_rule("nonexistent_id")
    assert result is False


@pytest.mark.asyncio
async def test_delete_rule_chromadb_failure_still_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    failing_store = MagicMock(
        add_document=AsyncMock(),
        delete_document=AsyncMock(side_effect=Exception("DB down")),
    )
    with patch.dict("sys.modules", {"vector_store": failing_store}):
        entry = await mod.add_rule("Rule to delete", "")
        result = await mod.delete_rule(entry["id"])
    assert result is True


# ---------------------------------------------------------------------------
# get_relevant_rules (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_relevant_rules_uses_vector_store(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    mock_results = [{"text": "Always use sources"}, {"text": "Prefer bullet points"}]
    mock_store = MagicMock(search=AsyncMock(return_value=mock_results))
    with patch.dict("sys.modules", {"vector_store": mock_store}):
        rules = await mod.get_relevant_rules("how should I format answers?", top_k=5)
    assert rules == ["Always use sources", "Prefer bullet points"]


@pytest.mark.asyncio
async def test_get_relevant_rules_falls_back_to_json(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "rules.json")
    failing_store = MagicMock(
        add_document=AsyncMock(),
        search=AsyncMock(side_effect=Exception("vector unavailable")),
    )
    with patch.dict("sys.modules", {"vector_store": failing_store}):
        await mod.add_rule("Fallback rule", "")
        rules = await mod.get_relevant_rules("any query", top_k=5)
    assert "Fallback rule" in rules


# ---------------------------------------------------------------------------
# extract_rule (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_rule_calls_llm_and_returns_rule(monkeypatch):
    mock_chat = AsyncMock(return_value=("Always use markdown tables.", [], "gemini"))
    with patch.dict("sys.modules", {"llm": MagicMock(chat=mock_chat)}):
        result = await mod.extract_rule("Use tables please", "Here is a plain list")
    assert result == "Always use markdown tables."


@pytest.mark.asyncio
async def test_extract_rule_returns_empty_on_llm_failure(monkeypatch):
    failing_llm = MagicMock(chat=AsyncMock(side_effect=Exception("LLM down")))
    with patch.dict("sys.modules", {"llm": failing_llm}):
        result = await mod.extract_rule("no no no", "bad answer")
    assert result == ""
