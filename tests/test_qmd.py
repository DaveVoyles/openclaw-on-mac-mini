"""
Tests for qmd.py — QMDMemory and async skill wrappers.

File I/O is redirected to a temporary directory via patching.
"""

from unittest.mock import patch

import pytest

import qmd as qmd_module
from qmd import QMDMemory, list_memories, recall_fact, remember_fact

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem(tmp_path):
    """Fresh QMDMemory instance backed by a temp file."""
    temp_file = tmp_path / "qmd.json"
    with patch.object(qmd_module, "MEMORY_FILE", temp_file):
        instance = QMDMemory()
        yield instance


# ---------------------------------------------------------------------------
# QMDMemory — add / search / list_all
# ---------------------------------------------------------------------------


class TestQMDMemoryAdd:
    async def test_add_creates_entry(self, mem):
        await mem.add("Plex server is running on port 32400")
        assert len(mem._memory) == 1

    async def test_add_stores_content(self, mem):
        content = "SABnzbd API key was reset"
        await mem.add(content)
        assert mem._memory[0]["content"] == content

    async def test_add_stores_tags(self, mem):
        await mem.add("disk usage is high", tags=["disk", "alert"])
        assert "disk" in mem._memory[0]["tags"]
        assert "alert" in mem._memory[0]["tags"]

    async def test_add_stores_timestamp(self, mem):
        await mem.add("test fact")
        assert "ts" in mem._memory[0]
        assert len(mem._memory[0]["ts"]) > 0

    async def test_add_empty_tags_defaults_to_empty_list(self, mem):
        await mem.add("no tags here")
        assert mem._memory[0]["tags"] == []

    async def test_add_multiple_entries(self, mem):
        await mem.add("fact one")
        await mem.add("fact two")
        assert len(mem._memory) == 2


class TestQMDMemorySearch:
    async def test_search_finds_by_content_substring(self, mem):
        await mem.add("Plex is running on port 32400")
        result = await mem.search("Plex")
        assert "Plex is running" in result

    async def test_search_is_case_insensitive(self, mem):
        await mem.add("PLEX server info")
        result = await mem.search("plex")
        assert "PLEX server info" in result

    async def test_search_finds_by_tag(self, mem):
        await mem.add("high memory usage", tags=["performance", "memory"])
        result = await mem.search("performance")
        assert "high memory usage" in result

    async def test_search_tag_is_case_insensitive(self, mem):
        await mem.add("disk alert", tags=["DISK"])
        result = await mem.search("disk")
        assert "disk alert" in result

    async def test_search_returns_no_match_message(self, mem):
        await mem.add("something completely unrelated")
        result = await mem.search("xyz-no-match")
        assert "No matching memories" in result

    async def test_search_on_empty_memory(self, mem):
        result = await mem.search("anything")
        assert "No matching memories" in result

    async def test_search_returns_at_most_10_results(self, mem):
        for i in range(15):
            await mem.add(f"matching fact {i}")
        result = await mem.search("matching")
        lines = [ln for ln in result.split("\n") if ln.strip().startswith("•")]
        assert len(lines) <= 10

    async def test_search_bullet_format(self, mem):
        await mem.add("the quick brown fox")
        result = await mem.search("quick")
        assert result.startswith("•")


class TestQMDMemoryListAll:
    async def test_list_all_empty_returns_message(self, mem):
        result = await mem.list_all()
        assert "empty" in result.lower() or "Memory is empty" in result

    async def test_list_all_shows_all_entries(self, mem):
        await mem.add("fact one")
        await mem.add("fact two")
        result = await mem.list_all()
        assert "fact one" in result
        assert "fact two" in result

    async def test_list_all_includes_date(self, mem):
        await mem.add("some fact")
        result = await mem.list_all()
        # Each line shows [YYYY-MM-DD]
        assert "[" in result and "]" in result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestQMDPersistence:
    async def test_data_survives_across_instances(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            m1 = QMDMemory()
            await m1.add("persistent fact", tags=["test"])

        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            m2 = QMDMemory()
            result = await m2.search("persistent")
            assert "persistent fact" in result

    def test_qmd_corrupted_file_falls_back_to_empty(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        temp_file.write_text("[invalid json{{")
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            m = QMDMemory()
            assert m._memory == []  # Graceful fallback


# ---------------------------------------------------------------------------
# Async skill wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAsyncSkills:
    async def test_remember_fact_returns_confirmation(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            # Reset the global store to use the temp file
            qmd_module.qmd_store = QMDMemory()
            result = await remember_fact("test content", tags="tag1,tag2")
            assert "Remembered" in result
            assert "test content" in result

    async def test_remember_fact_actually_stores(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            qmd_module.qmd_store = QMDMemory()
            await remember_fact("stored fact", tags="")
            result = await recall_fact("stored fact")
            assert "stored fact" in result

    async def test_remember_fact_parses_comma_separated_tags(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            qmd_module.qmd_store = QMDMemory()
            await remember_fact("tagged content", tags="server, media, plex")
            result = await recall_fact("server")
            assert "tagged content" in result

    async def test_recall_fact_no_match_returns_message(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            qmd_module.qmd_store = QMDMemory()
            result = await recall_fact("nothing here xyz")
            assert "No matching" in result

    async def test_list_memories_empty_returns_message(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            qmd_module.qmd_store = QMDMemory()
            result = await list_memories()
            assert "empty" in result.lower() or "Memory is empty" in result

    async def test_list_memories_returns_all(self, tmp_path):
        temp_file = tmp_path / "qmd.json"
        with patch.object(qmd_module, "MEMORY_FILE", temp_file):
            qmd_module.qmd_store = QMDMemory()
            await remember_fact("alpha", tags="")
            await remember_fact("beta", tags="")
            result = await list_memories()
            assert "alpha" in result
            assert "beta" in result
