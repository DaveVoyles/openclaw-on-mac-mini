"""Unit tests for memory_store_ops.py — store/recall/forget/stats and ID helpers."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory_store_ops import (
    _mem_content_id,
    _mem_unique_id,
    forget_memory,
    memory_stats,
    recall_memories,
    store_memory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vector_store(**overrides):
    mock = MagicMock()
    mock.MEMORIES_COLLECTION = "memories"
    mock.CONVERSATIONS_COLLECTION = "conversations"
    mock.RESEARCH_COLLECTION = "research"
    mock.add_memory_deduped = AsyncMock(return_value=True)
    mock.add_memory = AsyncMock()
    mock.search_all = AsyncMock(return_value=[])
    mock.delete_document = AsyncMock()
    mock.get_stats = AsyncMock(return_value={"memories": 5})
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


def _make_qmd(**overrides):
    mock = MagicMock()
    mock.remember_fact = AsyncMock()
    mock.list_memories = AsyncMock(return_value="Memory is empty.")
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


# ---------------------------------------------------------------------------
# _mem_content_id / _mem_unique_id
# ---------------------------------------------------------------------------

class TestIDHelpers:
    def test_content_id_is_deterministic(self):
        assert _mem_content_id("hello") == _mem_content_id("hello")

    def test_content_id_differs_for_different_content(self):
        assert _mem_content_id("hello") != _mem_content_id("world")

    def test_content_id_length_is_12(self):
        assert len(_mem_content_id("test")) == 12

    def test_unique_id_differs_for_same_content(self):
        """Non-dedup IDs should differ (timestamp suffix makes them unique)."""
        # We patch time.time to return different values to ensure divergence
        import memory_store_ops as mso
        with patch.object(mso, "_time") as mock_time:
            mock_time.time.side_effect = [1000.0, 2000.0]
            id1 = _mem_unique_id("hello")
            id2 = _mem_unique_id("hello")
        assert id1 != id2

    def test_unique_id_length_is_12(self):
        assert len(_mem_unique_id("test")) == 12

    def test_aliases_match_originals(self):
        from memory_store_ops import _content_id, _unique_id
        assert _content_id is _mem_content_id
        assert _unique_id is _mem_unique_id


# ---------------------------------------------------------------------------
# store_memory
# ---------------------------------------------------------------------------

class TestStoreMemory:
    @pytest.mark.asyncio
    async def test_dedup_skips_duplicate(self):
        vs = _make_vector_store(add_memory_deduped=AsyncMock(return_value=False))
        qmd = _make_qmd()
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            result = await store_memory("dupe content", dedup=True)
        assert result["duplicate"] is True
        assert result["stored"] is False

    @pytest.mark.asyncio
    async def test_dedup_stores_new_content(self):
        vs = _make_vector_store(add_memory_deduped=AsyncMock(return_value=True))
        qmd = _make_qmd()
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            result = await store_memory("fresh content", dedup=True)
        assert result["stored"] is True
        assert result["id"].startswith("mem_")

    @pytest.mark.asyncio
    async def test_no_dedup_always_stores(self):
        vs = _make_vector_store()
        qmd = _make_qmd()
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            result = await store_memory("any content", dedup=False)
        assert result["stored"] is True
        vs.add_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_vector_store_failure_still_returns_result(self):
        vs = _make_vector_store(add_memory_deduped=AsyncMock(side_effect=RuntimeError("fail")))
        qmd = _make_qmd()
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            result = await store_memory("content", dedup=True)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_qmd_failure_is_non_fatal(self):
        vs = _make_vector_store(add_memory_deduped=AsyncMock(return_value=True))
        qmd = _make_qmd(remember_fact=AsyncMock(side_effect=RuntimeError("qmd down")))
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            result = await store_memory("content", dedup=True)
        assert result["stored"] is True

    @pytest.mark.asyncio
    async def test_tags_passed_to_vector_store(self):
        vs = _make_vector_store(add_memory_deduped=AsyncMock(return_value=True))
        qmd = _make_qmd()
        tags = ["important", "fact"]
        with patch.dict(sys.modules, {"vector_store": vs, "qmd": qmd}):
            await store_memory("content", tags=tags, dedup=True)
        call_kwargs = vs.add_memory_deduped.call_args.kwargs
        assert call_kwargs["tags"] == tags


# ---------------------------------------------------------------------------
# recall_memories
# ---------------------------------------------------------------------------

class TestRecallMemories:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        vs = _make_vector_store(search_all=AsyncMock(return_value=[]))
        with patch.dict(sys.modules, {"vector_store": vs}):
            results = await recall_memories("test query")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_vector_results_mapped_to_schema(self):
        raw = [{"text": "fact", "metadata": {"source": "user"}, "similarity": 0.9,
                "collection": "memory", "id": "abc"}]
        vs = _make_vector_store(search_all=AsyncMock(return_value=raw))
        with patch.dict(sys.modules, {"vector_store": vs}):
            results = await recall_memories("query", include_rules=False)
        assert results[0]["text"] == "fact"
        assert results[0]["similarity"] == 0.9
        assert results[0]["source"] == "user"

    @pytest.mark.asyncio
    async def test_results_sorted_by_similarity_descending(self):
        raw = [
            {"text": "low", "metadata": {}, "similarity": 0.3, "collection": "m", "id": "1"},
            {"text": "high", "metadata": {}, "similarity": 0.9, "collection": "m", "id": "2"},
        ]
        vs = _make_vector_store(search_all=AsyncMock(return_value=raw))
        with patch.dict(sys.modules, {"vector_store": vs}):
            results = await recall_memories("q", include_rules=False)
        assert results[0]["similarity"] >= results[-1]["similarity"]

    @pytest.mark.asyncio
    async def test_vector_failure_returns_empty_list(self):
        vs = _make_vector_store(search_all=AsyncMock(side_effect=RuntimeError("fail")))
        with patch.dict(sys.modules, {"vector_store": vs}):
            results = await recall_memories("query", include_rules=False)
        assert results == []

    @pytest.mark.asyncio
    async def test_rules_included_when_flag_set(self):
        vs = _make_vector_store(search_all=AsyncMock(return_value=[]))
        rules_mock = MagicMock()
        rules_mock.get_relevant_rules = AsyncMock(return_value=["rule 1"])
        with patch.dict(sys.modules, {"vector_store": vs, "rules_engine": rules_mock}):
            results = await recall_memories("q", include_rules=True)
        assert any(r["type"] == "rule" for r in results)

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self):
        raw = [
            {"text": f"fact{i}", "metadata": {}, "similarity": float(i) / 10,
             "collection": "m", "id": str(i)}
            for i in range(10)
        ]
        vs = _make_vector_store(search_all=AsyncMock(return_value=raw))
        with patch.dict(sys.modules, {"vector_store": vs}):
            results = await recall_memories("q", top_k=3, include_rules=False)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# forget_memory
# ---------------------------------------------------------------------------

class TestForgetMemory:
    @pytest.mark.asyncio
    async def test_returns_true_when_deletion_succeeds(self):
        vs = _make_vector_store()
        with patch.dict(sys.modules, {"vector_store": vs}):
            removed = await forget_memory("mem_abc123")
        assert removed is True
        assert vs.delete_document.call_count == 3  # 3 collections

    @pytest.mark.asyncio
    async def test_returns_false_when_vector_store_fails(self):
        vs = _make_vector_store()
        # Simulate vector_store import failing
        with patch.dict(sys.modules, {"vector_store": None}):
            removed = await forget_memory("mem_abc123")
        assert removed is False

    @pytest.mark.asyncio
    async def test_partial_deletion_failure_still_returns_true(self):
        """If at least one collection deletion succeeds, removed=True."""
        vs = _make_vector_store()
        call_count = 0

        async def _side_effect(collection, mem_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("collection 1 failed")

        vs.delete_document = AsyncMock(side_effect=_side_effect)
        with patch.dict(sys.modules, {"vector_store": vs}):
            removed = await forget_memory("mem_abc")
        assert removed is True


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------

class TestMemoryStats:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self):
        vs = _make_vector_store()
        qmd = _make_qmd()
        rules_mock = MagicMock()
        rules_mock.get_all_rules = AsyncMock(return_value=["rule1", "rule2"])
        profile_mock = MagicMock()
        profile_mock.load_profile = MagicMock(return_value={"name": "Alice"})
        with patch.dict(sys.modules, {
            "vector_store": vs, "qmd": qmd,
            "rules_engine": rules_mock, "user_profile": profile_mock
        }):
            result = await memory_stats()
        for key in ("vector_store", "qmd", "rules", "profile"):
            assert key in result

    @pytest.mark.asyncio
    async def test_rules_count_populated(self):
        vs = _make_vector_store()
        qmd = _make_qmd()
        rules_mock = MagicMock()
        rules_mock.get_all_rules = AsyncMock(return_value=["r1", "r2", "r3"])
        profile_mock = MagicMock()
        profile_mock.load_profile = MagicMock(return_value={})
        with patch.dict(sys.modules, {
            "vector_store": vs, "qmd": qmd,
            "rules_engine": rules_mock, "user_profile": profile_mock,
        }):
            result = await memory_stats()
        assert result["rules"]["count"] == 3

    @pytest.mark.asyncio
    async def test_all_backends_failing_returns_empty_defaults(self):
        with patch.dict(sys.modules, {
            "vector_store": None, "qmd": None,
            "rules_engine": None, "user_profile": None,
        }):
            result = await memory_stats()
        assert result["qmd"]["count"] == 0
        assert result["rules"]["count"] == 0
