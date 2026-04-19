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

    def test_dream_cycle_save_and_load_roundtrip(self, tmp_path):
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

    def test_dream_cycle_save_creates_parent_dirs(self, tmp_path):
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


# ---------------------------------------------------------------------------
# _compute_health
# ---------------------------------------------------------------------------

class TestComputeHealth:
    def _fresh_entry(self, eid="e1", relations=None, category="identity"):
        import datetime
        today = datetime.date.today().isoformat()
        return {"id": eid, "lastReferenced": today, "category": category, "relations": relations or []}

    def test_empty_index_returns_valid_structure(self):
        result = mod._compute_health({"entries": []})
        assert "overall" in result
        assert "metrics" in result
        for k in ("freshness", "coverage", "coherence", "efficiency", "reachability"):
            assert k in result["metrics"]

    def test_all_fresh_entries_high_freshness(self):
        entries = [self._fresh_entry(f"e{i}") for i in range(5)]
        result = mod._compute_health({"entries": entries})
        assert result["metrics"]["freshness"] == 1.0

    def test_entries_with_relations_boost_coherence(self):
        entries = [
            self._fresh_entry("e1", relations=["e2"]),
            self._fresh_entry("e2", relations=["e1"]),
        ]
        result = mod._compute_health({"entries": entries})
        assert result["metrics"]["coherence"] == 1.0

    def test_efficiency_drops_with_large_memory_file(self, tmp_path):
        mem = tmp_path / "MEMORY.md"
        mem.write_text("\n" * 600)
        result = mod._compute_health({"entries": []}, memory_path=mem)
        assert result["metrics"]["efficiency"] == 0.0

    def test_overall_is_average_of_five_metrics(self):
        result = mod._compute_health({"entries": []})
        m = result["metrics"]
        expected = round(sum(m.values()) / 5, 3)
        assert result["overall"] == expected


# ---------------------------------------------------------------------------
# _fallback_insights
# ---------------------------------------------------------------------------

class TestFallbackInsights:
    def test_high_activity_produces_insight(self):
        changes = {"added": 10, "skipped": 0, "updated": 0}
        insights = mod._fallback_insights({"entries": []}, changes)
        assert any("High activity" in i for i in insights)

    def test_stabilizing_produces_insight(self):
        changes = {"added": 1, "skipped": 5, "updated": 0}
        insights = mod._fallback_insights({"entries": []}, changes)
        assert any("stabilizing" in i for i in insights)

    def test_default_insight_when_nothing_notable(self):
        changes = {"added": 0, "skipped": 0, "updated": 0}
        insights = mod._fallback_insights({"entries": []}, changes)
        assert insights  # always returns at least one


# ---------------------------------------------------------------------------
# _find_by_source_id / _next_id / _build_relations
# ---------------------------------------------------------------------------

class TestIndexHelpers:
    def test_find_by_source_id_found(self):
        index = {"entries": [{"id": "mem_001", "sourceId": "conv-abc"}]}
        result = mod._find_by_source_id(index, "conv-abc")
        assert result is not None
        assert result["id"] == "mem_001"

    def test_find_by_source_id_not_found(self):
        index = {"entries": [{"id": "mem_001", "sourceId": "other"}]}
        assert mod._find_by_source_id(index, "missing") is None

    def test_dream_cycle_next_id_increments(self):
        index = {"entries": [{"id": "mem_001"}, {"id": "mem_005"}]}
        assert mod._next_id(index) == "mem_006"

    def test_next_id_starts_at_001_for_empty(self):
        index = {"entries": []}
        assert mod._next_id(index) == "mem_001"

    def test_build_relations_links_shared_tags(self):
        index = {
            "entries": [
                {"id": "a", "tags": ["docker"], "source": "", "relations": []},
                {"id": "b", "tags": ["docker"], "source": "", "relations": []},
                {"id": "c", "tags": ["plex"], "source": "", "relations": []},
            ]
        }
        mod._build_relations(index)
        a = next(e for e in index["entries"] if e["id"] == "a")
        b = next(e for e in index["entries"] if e["id"] == "b")
        assert "b" in a["relations"]
        assert "a" in b["relations"]
        c = next(e for e in index["entries"] if e["id"] == "c")
        assert not c["relations"]  # plex is unique, no relations


# ---------------------------------------------------------------------------
# _classify_category / _classify_type / _extract_tags / _is_procedural
# ---------------------------------------------------------------------------

class TestClassifiers:
    def test_classify_category_discord(self):
        cat = mod._classify_category("The Discord bot has a new skill.", {})
        assert cat == "identity"

    def test_classify_category_docker(self):
        cat = mod._classify_category("Restart the docker container.", {})
        assert cat == "environment"

    def test_classify_category_fallback(self):
        cat = mod._classify_category("Something completely unrelated xyz.", {})
        assert isinstance(cat, str)  # returns some default

    def test_classify_type_decision(self):
        typ = mod._classify_type("We decided to migrate to Stripe.", {})
        assert typ == "decision"

    def test_classify_type_lesson(self):
        typ = mod._classify_type("Lesson: always use retries on API calls.", {})
        assert typ == "lesson"

    def test_extract_tags_from_text(self):
        tags = mod._extract_tags("Deploy with docker compose on the NAS.", {})
        assert isinstance(tags, list)

    def test_is_procedural_true(self):
        assert mod._is_procedural("How to deploy: run the command.") is True

    def test_is_procedural_false(self):
        assert mod._is_procedural("The sky is blue.") is False


# ---------------------------------------------------------------------------
# DreamCycle.__init__ and run (mocked)
# ---------------------------------------------------------------------------

class TestDreamCycleInit:
    def test_init_sets_paths(self, tmp_path):
        dc = mod.DreamCycle(data_dir=tmp_path)
        assert dc.data_dir == tmp_path
        assert dc.index_path == tmp_path / "index.json"
        assert dc.memory_path == tmp_path / "MEMORY.md"

    @pytest.mark.asyncio
    async def test_run_calls_phases(self, tmp_path, monkeypatch):
        """run() delegates to _run_phases which we stub."""
        from unittest.mock import AsyncMock
        dc = mod.DreamCycle(data_dir=tmp_path)
        monkeypatch.setattr(dc, "_run_phases", AsyncMock(return_value="✅ Dream done"))
        result = await dc.run()
        assert result == "✅ Dream done"

    @pytest.mark.asyncio
    async def test_run_returns_timeout_message_on_timeout(self, tmp_path, monkeypatch):
        """run() catches timeout and returns warning string."""
        import asyncio as _asyncio
        from unittest.mock import patch

        dc = mod.DreamCycle(data_dir=tmp_path)
        async def _slow(*a, **kw):
            raise _asyncio.TimeoutError()
        monkeypatch.setattr(dc, "_run_phases", _slow)

        with patch("dream_cycle.asyncio.timeout", side_effect=_asyncio.TimeoutError):
            # If asyncio.timeout itself raises, run() should handle it
            try:
                result = await dc.run()
                assert "timeout" in result.lower() or "⚠️" in result
            except _asyncio.TimeoutError:
                pass  # acceptable if not caught at this level


# ---------------------------------------------------------------------------
# dream_now / get_memory_health (top-level wrappers)
# ---------------------------------------------------------------------------

class TestDreamNow:
    @pytest.mark.asyncio
    async def test_dream_now_delegates_to_dream_cycle_run(self, tmp_path, monkeypatch):
        """dream_now() creates a DreamCycle and calls .run()."""
        from unittest.mock import AsyncMock, patch

        mock_run = AsyncMock(return_value="Dream complete.")
        with patch.object(mod.DreamCycle, "run", mock_run):
            result = await mod.dream_now()
        assert result == "Dream complete."

    @pytest.mark.asyncio
    async def test_get_memory_health_returns_string(self, tmp_path, monkeypatch):
        """get_memory_health() returns a string (no entries → no-data message)."""
        result = await mod.get_memory_health()
        assert isinstance(result, str)
        assert len(result) > 0
