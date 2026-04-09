"""Tests for vector_store_memory.py — high-level memory operations.

All ChromaDB I/O is mocked at the vector_store_client boundary so these tests
run without a real ChromaDB instance (which is incompatible with Python 3.14).
The functions in vector_store_memory use lazy imports (inside function bodies),
so we patch the source modules directly (vector_store_client, etc.).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vector_store_memory as vsm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    doc_id: str,
    text: str,
    similarity: float = 0.9,
    collection: str = "memories",
) -> dict:
    return {
        "id": doc_id,
        "text": text,
        "metadata": {"type": "fact"},
        "similarity": similarity,
        "collection": collection,
    }


# ---------------------------------------------------------------------------
# 1. Basic add and search
# ---------------------------------------------------------------------------

class TestBasicAddAndSearch:
    """add_memory + recall should store and retrieve a document."""

    @pytest.mark.asyncio
    async def test_add_then_recall_returns_content(self):
        added_texts: list[str] = []

        async def fake_add(col, *, doc_id, text, metadata=None,
                           channel_id=None, thread_id=None):
            added_texts.append(text)

        async def fake_search_all(query, top_k=5, **kwargs):
            return [_make_result("mem_1", "cats are great", similarity=0.95)]

        with (
            patch("vector_store_client.add_document", side_effect=fake_add),
            patch("vector_store_client.search_all", side_effect=fake_search_all),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
        ):
            await vsm.add_memory("1", "cats are great", channel_id=111)
            result = await vsm.recall("cats", top_k=1, channel_id=111)

        assert "cats are great" in result
        assert added_texts == ["cats are great"]

    @pytest.mark.asyncio
    async def test_recall_formats_collection_and_similarity(self):
        row = _make_result("mem_x", "hello world", similarity=0.80, collection="memories")

        async def fake_search_all(query, top_k=5, **kwargs):
            return [row]

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            result = await vsm.recall("hello")

        assert "Memories" in result or "memories" in result.lower()
        assert "80%" in result


# ---------------------------------------------------------------------------
# 2. Similarity ranking
# ---------------------------------------------------------------------------

class TestSimilarityRanking:
    """The most relevant document should appear first in recall output."""

    @pytest.mark.asyncio
    async def test_most_similar_result_appears_first(self):
        rows = [
            _make_result("mem_cat", "cats are great",    similarity=0.92),
            _make_result("mem_dog", "dogs are great",    similarity=0.85),
            _make_result("mem_py",  "Python programming", similarity=0.30),
        ]

        async def fake_search_all(query, top_k=5, **kwargs):
            return rows  # search_all returns pre-ranked results

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            result = await vsm.recall("feline pets", top_k=3)

        lines = result.strip().splitlines()
        assert len(lines) == 3
        assert "cats are great" in lines[0]    # highest similarity first
        assert "Python programming" in lines[2]  # lowest similarity last


# ---------------------------------------------------------------------------
# 3. Empty collection — no crash, returns empty
# ---------------------------------------------------------------------------

class TestEmptyCollection:
    @pytest.mark.asyncio
    async def test_recall_on_empty_store_returns_empty_string(self):
        async def fake_search_all(query, top_k=5, **kwargs):
            return []

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            result = await vsm.recall("anything")

        assert result == ""

    @pytest.mark.asyncio
    async def test_recall_for_context_on_empty_store_returns_empty_string(self):
        async def fake_search_all(query, top_k=5, **kwargs):
            return []

        cfg_mock = MagicMock()
        cfg_mock.auto_recall_top_k = 5

        with (
            patch("vector_store_client.search_all", side_effect=fake_search_all),
            patch("config.cfg", cfg_mock),
        ):
            result = await vsm.recall_for_context("anything")

        assert result == ""


# ---------------------------------------------------------------------------
# 4. Duplicate handling via add_memory_deduped
# ---------------------------------------------------------------------------

class TestDuplicateHandling:
    @pytest.mark.asyncio
    async def test_duplicate_skips_store_and_returns_false(self):
        """When a near-duplicate exists, add_memory_deduped returns False."""
        existing = _make_result("mem_existing", "cats are great", similarity=0.95)

        async def fake_search(col, query, top_k=1, **kwargs):
            return [existing]

        bump_called_with: list = []

        async def fake_bump(col, ids):
            bump_called_with.extend(ids)

        with (
            patch("vector_store_client.search", side_effect=fake_search),
            patch("vector_store_compaction.bump_access", side_effect=fake_bump),
        ):
            stored = await vsm.add_memory_deduped(
                "new_id", "cats are great", dedup_threshold=0.90, channel_id=111
            )

        assert stored is False
        assert "mem_existing" in bump_called_with

    @pytest.mark.asyncio
    async def test_unique_document_is_stored_and_returns_true(self):
        """When no near-duplicate exists, add_memory_deduped stores and returns True."""
        async def fake_search(col, query, top_k=1, **kwargs):
            return []  # no existing similar doc

        upserted: list[str] = []

        async def fake_add(col, *, doc_id, text, metadata=None,
                           channel_id=None, thread_id=None):
            upserted.append(doc_id)

        with (
            patch("vector_store_client.search", side_effect=fake_search),
            patch("vector_store_client.add_document", side_effect=fake_add),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
        ):
            stored = await vsm.add_memory_deduped(
                "brand_new", "something entirely new", dedup_threshold=0.90
            )

        assert stored is True
        assert "mem_brand_new" in upserted

    @pytest.mark.asyncio
    async def test_adding_same_id_twice_via_add_memory_does_not_raise(self):
        """add_memory is an upsert — adding the same ID twice must not crash."""
        call_count = 0

        async def fake_add(col, *, doc_id, text, metadata=None,
                           channel_id=None, thread_id=None):
            nonlocal call_count
            call_count += 1

        with (
            patch("vector_store_client.add_document", side_effect=fake_add),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
        ):
            await vsm.add_memory("dup", "same content")
            await vsm.add_memory("dup", "same content")

        assert call_count == 2  # both calls went through without error


# ---------------------------------------------------------------------------
# 5. Concurrency — multiple parallel recalls
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_recalls_all_succeed(self):
        """asyncio.gather over several recall() calls should all return results."""
        call_log: list[str] = []

        async def fake_search_all(query, top_k=5, **kwargs):
            call_log.append(query)
            return [_make_result(f"id_{query}", f"result for {query}")]

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            queries = ["alpha", "beta", "gamma", "delta", "epsilon"]
            results = await asyncio.gather(*[vsm.recall(q) for q in queries])

        assert len(results) == 5
        assert all(r != "" for r in results)
        assert set(call_log) == set(queries)

    @pytest.mark.asyncio
    async def test_concurrent_add_memories_do_not_raise(self):
        """Concurrent add_memory calls must not raise even under shared state."""
        async def fake_add(col, *, doc_id, text, metadata=None,
                           channel_id=None, thread_id=None):
            await asyncio.sleep(0)  # yield to event loop

        with (
            patch("vector_store_client.add_document", side_effect=fake_add),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
        ):
            await asyncio.gather(
                *[vsm.add_memory(str(i), f"fact {i}") for i in range(10)]
            )


# ---------------------------------------------------------------------------
# 6. Max results — top_k cap
# ---------------------------------------------------------------------------

class TestMaxResults:
    @pytest.mark.asyncio
    async def test_top_k_caps_number_of_results(self):
        """recall() output lines should not exceed top_k."""
        all_docs = [
            _make_result(f"mem_{i}", f"document number {i}", similarity=0.9 - i * 0.01)
            for i in range(10)
        ]

        async def fake_search_all(query, top_k=5, **kwargs):
            return all_docs[:top_k]  # honour the top_k as real search_all does

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            result = await vsm.recall("test query", top_k=2)

        lines = [ln for ln in result.strip().splitlines() if ln]
        assert len(lines) <= 2

    @pytest.mark.asyncio
    async def test_top_k_one_returns_single_result(self):
        rows = [_make_result("mem_a", "only result")]

        async def fake_search_all(query, top_k=5, **kwargs):
            return rows[:top_k]

        with patch("vector_store_client.search_all", side_effect=fake_search_all):
            result = await vsm.recall("something", top_k=1)

        lines = [ln for ln in result.strip().splitlines() if ln]
        assert len(lines) == 1
        assert "only result" in lines[0]
