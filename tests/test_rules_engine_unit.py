"""Unit tests for rules_engine.py — gaps not covered by test_rules_engine_coverage.py.

Coverage file already tests: detect_correction, _load_rules, _save_rules,
add_rule, get_all_rules, delete_rule, get_relevant_rules, extract_rule.

This file covers: RULES_FILE constant, RULE_SIMILARITY_THRESHOLD, edge cases
in detect_correction patterns, and async rule lifecycle interactions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import rules_engine as mod
from rules_engine import (
    RULE_SIMILARITY_THRESHOLD,
    RULES_FILE,
    detect_correction,
)


class TestConstants:
    def test_rules_file_is_path(self):
        assert isinstance(RULES_FILE, Path)

    def test_similarity_threshold_in_range(self):
        assert 0.0 < RULE_SIMILARITY_THRESHOLD < 1.0

    def test_similarity_threshold_value(self):
        assert RULE_SIMILARITY_THRESHOLD == 0.6


class TestDetectCorrectionEdgeCases:
    def test_whitespace_only_returns_false(self):
        assert detect_correction("   ") is False

    def test_i_said_triggers(self):
        assert detect_correction("I said to use JSON not XML") is True

    def test_normal_greeting_returns_false(self):
        assert detect_correction("Hello, how are you?") is False

    def test_question_returns_false(self):
        assert detect_correction("What is the weather like?") is False

    def test_case_insensitive_actually(self):
        assert detect_correction("ACTUALLY that was wrong") is True

    def test_case_insensitive_incorrect(self):
        assert detect_correction("That answer is INCORRECT") is True

    def test_partial_no_match_not_triggered(self):
        # "no" embedded in "knowledge" should not trigger
        assert detect_correction("I have knowledge of that topic") is False

    def test_stop_doing_triggers(self):
        assert detect_correction("Please stop doing that formatting") is True

    def test_you_should_in_middle(self):
        assert detect_correction("Next time you should format it differently") is True


@pytest.mark.asyncio
async def test_load_rules_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "RULES_FILE", tmp_path / "nonexistent.json")
    result = await mod._load_rules()
    assert result == []


@pytest.mark.asyncio
async def test_save_and_reload_rules(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    monkeypatch.setattr(mod, "RULES_FILE", rules_file)
    rules = [{"id": "r1", "rule": "Use markdown tables", "source": ""}]
    await mod._save_rules(rules)
    loaded = await mod._load_rules()
    assert loaded == rules


@pytest.mark.asyncio
async def test_get_all_rules_returns_list(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps([{"id": "x", "rule": "test rule", "source": ""}]))
    monkeypatch.setattr(mod, "RULES_FILE", rules_file)
    result = await mod.get_all_rules()
    assert isinstance(result, list)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_delete_rule_returns_false_when_not_found(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps([]))
    monkeypatch.setattr(mod, "RULES_FILE", rules_file)

    # search and delete are locally imported in rules_engine; patch at source
    with patch("vector_store.search_safe", new=AsyncMock(return_value=[])):
        result = await mod.delete_rule("nonexistent_id")
    assert result is False


@pytest.mark.asyncio
async def test_add_rule_returns_dict_with_required_keys(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps([]))
    monkeypatch.setattr(mod, "RULES_FILE", rules_file)

    # add_document and search are locally imported inside add_rule
    with (
        patch("vector_store.add_document", new=AsyncMock()),
        patch("vector_store.search", new=AsyncMock(return_value=[])),
    ):
        result = await mod.add_rule("Always use markdown tables", source_message="test")

    assert "id" in result
    assert "rule" in result
    assert result["rule"] == "Always use markdown tables"
