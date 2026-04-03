"""Tests for dream_cycle.py — classification, tagging, reporting, and health helpers.

Complements test_dream_cycle.py which covers importance scoring, reachability,
and index I/O.
"""

import datetime

import pytest

import dream_cycle as mod


# ---------------------------------------------------------------------------
# _classify_category
# ---------------------------------------------------------------------------


class TestClassifyCategory:
    @pytest.mark.parametrize("text, expected", [
        ("The openclaw bot is running on Discord", "identity"),
        ("Dave prefers dark mode and EST timezone", "user"),
        ("MonsterVision project repo on GitHub", "projects"),
        ("Decided to migrate from SQLite to Postgres", "decisions"),
        ("Lesson learned: never skip backups on Friday", "lessons"),
        ("Docker container on NAS server via SSH", "environment"),
        ("Our strategy and roadmap for Q3", "strategy"),
        ("Open thread: pending review", "threads"),
        ("Team member contact info", "people"),
        ("Revenue and cost analysis", "business"),
        ("Something completely unrelated", "general"),
    ])
    def test_keyword_classification(self, text, expected):
        assert mod._classify_category(text, {}) == expected


# ---------------------------------------------------------------------------
# _classify_type
# ---------------------------------------------------------------------------


class TestClassifyType:
    def test_preference_from_meta(self):
        assert mod._classify_type("anything", {"type": "preference"}) == "preference"

    def test_user_profile_from_meta(self):
        assert mod._classify_type("anything", {"type": "user_profile"}) == "preference"

    def test_decision_from_text(self):
        assert mod._classify_type("We decided to use Redis", {}) == "decision"

    def test_lesson_from_text(self):
        assert mod._classify_type("Lesson learned: check backups", {}) == "lesson"

    def test_thread_from_text(self):
        assert mod._classify_type("Open todo: fix the parser", {}) == "thread"

    def test_default_fact(self):
        assert mod._classify_type("The sky is blue", {}) == "fact"


# ---------------------------------------------------------------------------
# _extract_tags
# ---------------------------------------------------------------------------


class TestExtractTags:
    def test_tags_from_text_keywords(self):
        tags = mod._extract_tags("Set up docker container with plex on NAS", {})
        assert "docker" in tags
        assert "plex" in tags
        assert "nas" in tags

    def test_tags_from_meta_string(self):
        tags = mod._extract_tags("no keywords", {"tags": "alpha, beta"})
        assert "alpha" in tags
        assert "beta" in tags

    def test_tags_from_meta_list(self):
        tags = mod._extract_tags("no keywords", {"tags": ["x", "y"]})
        assert "x" in tags
        assert "y" in tags

    def test_empty_text_and_meta(self):
        assert mod._extract_tags("nothing special", {}) == []


# ---------------------------------------------------------------------------
# _is_procedural
# ---------------------------------------------------------------------------


class TestIsProcedural:
    @pytest.mark.parametrize("text", [
        "How to restart the server",
        "Steps: first do X then Y",
        "Workflow: build, test, deploy",
        "Always do a backup first, then migrate",
        "Run the command docker restart",
    ])
    def test_procedural_detected(self, text):
        assert mod._is_procedural(text) is True

    def test_non_procedural(self):
        assert mod._is_procedural("The server runs Linux") is False


# ---------------------------------------------------------------------------
# _find_by_source_id / _next_id
# ---------------------------------------------------------------------------


class TestIndexHelpers:
    def test_find_by_source_id_hit(self):
        index = {"entries": [{"id": "mem_001", "sourceId": "abc"}]}
        assert mod._find_by_source_id(index, "abc")["id"] == "mem_001"

    def test_find_by_source_id_miss(self):
        index = {"entries": [{"id": "mem_001", "sourceId": "abc"}]}
        assert mod._find_by_source_id(index, "xyz") is None

    def test_next_id_empty(self):
        assert mod._next_id({"entries": []}) == "mem_001"

    def test_next_id_increments(self):
        index = {"entries": [{"id": "mem_005"}, {"id": "mem_003"}]}
        assert mod._next_id(index) == "mem_006"


# ---------------------------------------------------------------------------
# _fallback_insights
# ---------------------------------------------------------------------------


class TestFallbackInsights:
    def test_high_activity_insight(self):
        index = {"entries": []}
        changes = {"added": 10, "skipped": 1, "updated": 0, "archived": 0}
        insights = mod._fallback_insights(index, changes)
        assert any("activity" in i.lower() or "10" in i for i in insights)

    def test_stabilizing_insight(self):
        index = {"entries": []}
        changes = {"added": 2, "skipped": 5, "updated": 0, "archived": 0}
        insights = mod._fallback_insights(index, changes)
        assert any("stabiliz" in i.lower() for i in insights)

    def test_gap_insight(self):
        index = {"entries": [{"category": "identity"}]}
        changes = {"added": 0, "skipped": 0, "updated": 0, "archived": 0}
        insights = mod._fallback_insights(index, changes)
        assert any("gap" in i.lower() for i in insights)

    def test_max_three_insights(self):
        index = {"entries": []}
        changes = {"added": 0, "skipped": 0, "updated": 0, "archived": 0}
        assert len(mod._fallback_insights(index, changes)) <= 3


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_dream_seconds(self):
        assert mod.MAX_DREAM_SECONDS > 0
        assert mod.MAX_DREAM_SECONDS <= 600

    def test_categories_non_empty(self):
        assert len(mod.CATEGORIES) >= 5
        assert "identity" in mod.CATEGORIES
        assert "lessons" in mod.CATEGORIES
