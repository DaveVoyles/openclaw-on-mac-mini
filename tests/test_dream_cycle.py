"""
Tests for dream_cycle.py — importance scoring, reachability, and index I/O.
"""

import datetime
import json

import pytest

import dream_cycle as mod

# ---------------------------------------------------------------------------
# _compute_importance
# ---------------------------------------------------------------------------


class TestComputeImportance:
    """Test the importance scoring formula."""

    def test_decision_type_high_base(self):
        entry = {"type": "decision", "created": datetime.date.today().isoformat(), "referenceCount": 1}
        score = mod._compute_importance(entry, datetime.date.today())
        assert 0.7 <= score <= 1.0  # base=0.8, recency=1.0, boost=1.0

    def test_fact_type_lower_base(self):
        entry = {"type": "fact", "created": datetime.date.today().isoformat(), "referenceCount": 1}
        score = mod._compute_importance(entry, datetime.date.today())
        assert 0.4 <= score <= 0.6

    def test_recency_decay(self):
        today = datetime.date(2025, 6, 1)
        recent = {"type": "fact", "lastReferenced": "2025-06-01", "referenceCount": 1}
        old = {"type": "fact", "lastReferenced": "2025-01-01", "referenceCount": 1}
        assert mod._compute_importance(recent, today) > mod._compute_importance(old, today)

    def test_very_old_entry_minimum_recency(self):
        today = datetime.date(2025, 6, 1)
        ancient = {"type": "fact", "lastReferenced": "2024-01-01", "referenceCount": 1}
        score = mod._compute_importance(ancient, today)
        assert score > 0  # recency floors at 0.1
        assert score <= 1.0

    def test_high_reference_count_boosts(self):
        today = datetime.date.today()
        few = {"type": "fact", "created": today.isoformat(), "referenceCount": 1}
        many = {"type": "fact", "created": today.isoformat(), "referenceCount": 64}
        assert mod._compute_importance(many, today) > mod._compute_importance(few, today)

    def test_unknown_type_defaults(self):
        entry = {"type": "unknown_thing", "created": datetime.date.today().isoformat(), "referenceCount": 1}
        score = mod._compute_importance(entry, datetime.date.today())
        assert 0.4 <= score <= 0.6  # default base = 0.5

    def test_score_clamped_to_unit_range(self):
        entry = {"type": "decision", "created": datetime.date.today().isoformat(), "referenceCount": 10000}
        score = mod._compute_importance(entry, datetime.date.today())
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _compute_reachability
# ---------------------------------------------------------------------------


class TestComputeReachability:
    def test_empty_entries(self):
        assert mod._compute_reachability([]) == 1.0

    def test_single_entry(self):
        assert mod._compute_reachability([{"id": "a", "relations": []}]) == 1.0

    def test_fully_connected(self):
        entries = [
            {"id": "a", "relations": ["b"]},
            {"id": "b", "relations": ["c"]},
            {"id": "c", "relations": []},
        ]
        assert mod._compute_reachability(entries) == 1.0  # 1 component

    def test_disconnected_graph(self):
        entries = [
            {"id": "a", "relations": []},
            {"id": "b", "relations": []},
            {"id": "c", "relations": []},
        ]
        score = mod._compute_reachability(entries)
        assert score == pytest.approx(1 / 3, abs=0.01)

    def test_partial_connectivity(self):
        entries = [
            {"id": "a", "relations": ["b"]},
            {"id": "b", "relations": []},
            {"id": "c", "relations": []},
        ]
        score = mod._compute_reachability(entries)
        assert score == pytest.approx(0.5, abs=0.01)  # 2 components

    def test_relation_to_nonexistent_id_ignored(self):
        entries = [
            {"id": "a", "relations": ["z"]},  # z doesn't exist
            {"id": "b", "relations": []},
        ]
        score = mod._compute_reachability(entries)
        assert score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# _load_index / _save_index
# ---------------------------------------------------------------------------


class TestIndexIO:
    def test_load_missing_returns_default(self, tmp_path):
        path = tmp_path / "index.json"
        idx = mod._load_index(path)
        assert idx["version"] == "3.0"
        assert idx["entries"] == []

    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "index.json"
        idx = mod._load_index(path)
        idx["entries"].append({"id": "e1", "text": "hello"})
        mod._save_index(path, idx)

        loaded = mod._load_index(path)
        assert len(loaded["entries"]) == 1
        assert loaded["entries"][0]["id"] == "e1"

    def test_load_corrupt_json_returns_default(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text("{bad json!!")
        idx = mod._load_index(path)
        assert idx["entries"] == []
        assert (tmp_path / "index.json.bak").exists()

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "index.json"
        mod._save_index(path, {"version": "3.0", "entries": []})
        assert path.exists()

    def test_save_creates_backup(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text('{"old": true}')
        mod._save_index(path, {"version": "3.0", "entries": []})
        bak = path.with_suffix(".json.bak")
        assert bak.exists()
        assert json.loads(bak.read_text()) == {"old": True}
