"""
Tests for the unified memory facade (store_memory/recall_memories/forget_memory/memory_stats)
merged into memory.py (Phase 16 consolidation — formerly memory_manager.py).

Uses mocks for all backends so tests run without ChromaDB/disk/LLM.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import memory as memory_module  # noqa: E402


# Bind the facade functions to the `memory_manager` namespace used throughout this file.
# `store` is already taken in memory.py (ConversationStore singleton), so the unified
# facade uses `store_memory`, `recall_memories`, etc.
class _MemoryManagerFacade:
    store = staticmethod(memory_module.store_memory)
    recall = staticmethod(memory_module.recall_memories)
    forget = staticmethod(memory_module.forget_memory)
    stats = staticmethod(memory_module.memory_stats)
    _content_id = staticmethod(memory_module._mem_content_id)
    _unique_id = staticmethod(memory_module._mem_unique_id)

memory_manager = _MemoryManagerFacade()


def _make_mock_vector_store(**overrides):
    """Create a mock vector_store module with sensible defaults."""
    mock = MagicMock()
    mock.MEMORIES_COLLECTION = "memories"
    mock.CONVERSATIONS_COLLECTION = "conversations"
    mock.RESEARCH_COLLECTION = "research"
    mock.add_memory_deduped = AsyncMock(return_value=True)
    mock.add_memory = AsyncMock()
    mock.search_all = AsyncMock(return_value=[])
    mock.delete_document = AsyncMock()
    mock.get_stats = AsyncMock(return_value={})
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


def _make_mock_qmd(**overrides):
    """Create a mock qmd module."""
    mock = MagicMock()
    mock.remember_fact = AsyncMock(return_value="✅ Remembered")
    mock.list_memories = AsyncMock(return_value="Memory is empty.")
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


def _make_mock_rules(**overrides):
    """Create a mock rules_engine module."""
    mock = MagicMock()
    mock.get_relevant_rules = AsyncMock(return_value=[])
    mock.get_all_rules = AsyncMock(return_value=[])
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


def _make_mock_profile(**overrides):
    """Create a mock user_profile module."""
    mock = MagicMock()
    mock.load_profile = MagicMock(return_value={})
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


# ---------------------------------------------------------------------------
# store()
# ---------------------------------------------------------------------------


class TestStore:
    @pytest.mark.asyncio
    async def test_store_with_dedup_succeeds(self):
        mock_vs = _make_mock_vector_store()
        mock_qmd = _make_mock_qmd()
        with patch.dict(sys.modules, {"vector_store": mock_vs, "qmd": mock_qmd}):
            result = await memory_manager.store("The sky is blue", tags=["science"])

        assert result["stored"] is True
        assert result["duplicate"] is False
        assert result["id"].startswith("mem_")
        mock_vs.add_memory_deduped.assert_awaited_once()
        mock_qmd.remember_fact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_detects_duplicate(self):
        mock_vs = _make_mock_vector_store(add_memory_deduped=AsyncMock(return_value=False))
        mock_qmd = _make_mock_qmd()
        with patch.dict(sys.modules, {"vector_store": mock_vs, "qmd": mock_qmd}):
            result = await memory_manager.store("The sky is blue")

        assert result["stored"] is False
        assert result["duplicate"] is True

    @pytest.mark.asyncio
    async def test_store_without_dedup(self):
        mock_vs = _make_mock_vector_store()
        mock_qmd = _make_mock_qmd()
        with patch.dict(sys.modules, {"vector_store": mock_vs, "qmd": mock_qmd}):
            result = await memory_manager.store("fact", dedup=False)

        assert result["stored"] is True
        mock_vs.add_memory.assert_awaited_once()
        mock_qmd.remember_fact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_survives_vector_failure(self):
        mock_vs = _make_mock_vector_store(
            add_memory_deduped=AsyncMock(side_effect=RuntimeError("boom"))
        )
        mock_qmd = _make_mock_qmd()
        with patch.dict(sys.modules, {"vector_store": mock_vs, "qmd": mock_qmd}):
            result = await memory_manager.store("fact")

        assert result["stored"] is False
        mock_qmd.remember_fact.assert_awaited_once()


# ---------------------------------------------------------------------------
# recall()
# ---------------------------------------------------------------------------


class TestRecall:
    @pytest.mark.asyncio
    async def test_recall_merges_vector_and_rules(self):
        mock_vs = _make_mock_vector_store(
            search_all=AsyncMock(return_value=[
                {"text": "fact1", "metadata": {"source": "user"}, "similarity": 0.9,
                 "collection": "memories", "id": "m1"},
            ])
        )
        mock_rules = _make_mock_rules(
            get_relevant_rules=AsyncMock(return_value=["Always use metric units"])
        )
        with patch.dict(sys.modules, {"vector_store": mock_vs, "rules_engine": mock_rules}):
            results = await memory_manager.recall("units")

        assert len(results) == 2
        assert results[0]["similarity"] >= results[1]["similarity"]

    @pytest.mark.asyncio
    async def test_recall_without_rules(self):
        mock_vs = _make_mock_vector_store(
            search_all=AsyncMock(return_value=[
                {"text": "fact1", "metadata": {}, "similarity": 0.9,
                 "collection": "memories", "id": "m1"},
            ])
        )
        with patch.dict(sys.modules, {"vector_store": mock_vs}):
            results = await memory_manager.recall("test", include_rules=False)

        assert len(results) == 1
        assert results[0]["type"] == "memories"

    @pytest.mark.asyncio
    async def test_recall_survives_all_failures(self):
        mock_vs = _make_mock_vector_store(
            search_all=AsyncMock(side_effect=RuntimeError("boom"))
        )
        mock_rules = _make_mock_rules(
            get_relevant_rules=AsyncMock(side_effect=RuntimeError("boom"))
        )
        with patch.dict(sys.modules, {"vector_store": mock_vs, "rules_engine": mock_rules}):
            results = await memory_manager.recall("anything")

        assert results == []

    @pytest.mark.asyncio
    async def test_recall_respects_top_k(self):
        mock_vs = _make_mock_vector_store(
            search_all=AsyncMock(return_value=[
                {"text": f"fact{i}", "metadata": {}, "similarity": 0.9 - i * 0.01,
                 "collection": "memories", "id": f"m{i}"}
                for i in range(10)
            ])
        )
        with patch.dict(sys.modules, {"vector_store": mock_vs}):
            results = await memory_manager.recall("test", top_k=3, include_rules=False)

        assert len(results) == 3


# ---------------------------------------------------------------------------
# forget()
# ---------------------------------------------------------------------------


class TestForget:
    @pytest.mark.asyncio
    async def test_forget_removes_from_collections(self):
        mock_vs = _make_mock_vector_store()
        with patch.dict(sys.modules, {"vector_store": mock_vs}):
            removed = await memory_manager.forget("mem_abc123")

        assert removed is True
        assert mock_vs.delete_document.await_count == 3

    @pytest.mark.asyncio
    async def test_forget_survives_failure(self):
        mock_vs = _make_mock_vector_store(
            delete_document=AsyncMock(side_effect=RuntimeError("boom"))
        )
        with patch.dict(sys.modules, {"vector_store": mock_vs}):
            removed = await memory_manager.forget("nonexistent")

        assert removed is False


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_aggregates_all_backends(self):
        mock_vs = _make_mock_vector_store(
            get_stats=AsyncMock(return_value={"memories": {"count": 10}})
        )
        mock_qmd = _make_mock_qmd(
            list_memories=AsyncMock(return_value="• fact1\n• fact2\n• fact3")
        )
        mock_rules = _make_mock_rules(
            get_all_rules=AsyncMock(return_value=[{"id": "r1"}, {"id": "r2"}])
        )
        mock_profile = _make_mock_profile(
            load_profile=MagicMock(return_value={"preferences": {"tz": "UTC"}})
        )
        with patch.dict(sys.modules, {
            "vector_store": mock_vs,
            "qmd": mock_qmd,
            "rules_engine": mock_rules,
            "user_profile": mock_profile,
        }):
            result = await memory_manager.stats()

        assert result["vector_store"] == {"memories": {"count": 10}}
        assert result["qmd"]["count"] == 3
        assert result["rules"]["count"] == 2
        assert result["profile"]["exists"] is True

    @pytest.mark.asyncio
    async def test_stats_survives_all_failures(self):
        # Inject modules that raise on every attribute access
        broken = MagicMock(side_effect=RuntimeError("boom"))
        with patch.dict(sys.modules, {
            "vector_store": broken,
            "qmd": broken,
            "rules_engine": broken,
            "user_profile": broken,
        }):
            result = await memory_manager.stats()

        assert "vector_store" in result
        assert "qmd" in result


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


class TestIdGeneration:
    def test_memory_manager_content_id_is_deterministic(self):
        id1 = memory_manager._content_id("same content")
        id2 = memory_manager._content_id("same content")
        assert id1 == id2

    def test_memory_manager_content_id_differs_for_different_content(self):
        id1 = memory_manager._content_id("content A")
        id2 = memory_manager._content_id("content B")
        assert id1 != id2

    def test_unique_id_returns_12_chars(self):
        uid = memory_manager._unique_id("test")
        assert len(uid) == 12
