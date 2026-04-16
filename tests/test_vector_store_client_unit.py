"""Unit tests for vector_store_client.py — CRUD operations with mocked ChromaDB.

test_vector_store_search.py covers search_safe and scoped isolation scenarios.
This file covers: add_document, delete_document, get_stats, search_all edge cases,
_get_client, _get_collection with mocked chromadb.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from vector_store_client import (
    add_document,
    delete_document,
    get_stats,
    search_all,
    search_safe,
)


def _make_mock_collection(count: int = 0, query_results=None):
    col = MagicMock()
    col.count.return_value = count
    if query_results is not None:
        col.query.return_value = query_results
    return col


class TestAddDocument:
    @pytest.mark.asyncio
    async def test_empty_text_skipped(self):
        with patch("vector_store_client._get_collection") as mock_col:
            await add_document("memories", "doc1", "   ")
        mock_col.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_document_to_collection(self):
        mock_col = _make_mock_collection()
        with (
            patch("vector_store_client._get_collection", return_value=mock_col),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
            patch("vector_store_client._inject_scope_metadata", return_value={
                "added_at": 0.0, "access_count": 0, "last_accessed": 0.0
            }),
            patch("vector_store_client._normalize_scope_id", return_value=None),
        ):
            await add_document("memories", "doc1", "Hello world")

        mock_col.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_metadata_added_at_set(self):
        captured = {}

        def mock_upsert(**kwargs):
            captured.update(kwargs)

        mock_col = _make_mock_collection()
        mock_col.upsert.side_effect = mock_upsert

        with (
            patch("vector_store_client._get_collection", return_value=mock_col),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
            patch("vector_store_client._inject_scope_metadata", return_value={}),
            patch("vector_store_client._normalize_scope_id", return_value=None),
        ):
            await add_document("memories", "doc1", "test text")

        meta = captured.get("metadatas", [{}])[0]
        assert "added_at" in meta

    @pytest.mark.asyncio
    async def test_text_truncated_to_8000_chars(self):
        captured = {}

        def mock_upsert(**kwargs):
            captured.update(kwargs)

        mock_col = _make_mock_collection()
        mock_col.upsert.side_effect = mock_upsert

        long_text = "x" * 10_000

        with (
            patch("vector_store_client._get_collection", return_value=mock_col),
            patch("vector_store_compaction._compact_scope_if_needed", new=AsyncMock()),
            patch("vector_store_client._inject_scope_metadata", return_value={}),
            patch("vector_store_client._normalize_scope_id", return_value=None),
        ):
            await add_document("memories", "doc1", long_text)

        stored_text = captured["documents"][0]
        assert len(stored_text) <= 8000


class TestDeleteDocument:
    @pytest.mark.asyncio
    async def test_calls_delete_on_collection(self):
        mock_col = _make_mock_collection()

        with patch("vector_store_client._get_collection", return_value=mock_col):
            await delete_document("memories", "doc99")

        mock_col.delete.assert_called_once_with(ids=["doc99"])


class TestGetStats:
    @pytest.mark.asyncio
    async def test_returns_dict_with_collection_keys(self):
        mock_col = _make_mock_collection(count=5)

        with (
            patch("vector_store_client._get_client"),
            patch("vector_store_client._get_collection", return_value=mock_col),
        ):
            stats = await get_stats()

        assert "memories" in stats
        assert "conversations" in stats
        assert "research" in stats

    @pytest.mark.asyncio
    async def test_count_value_in_stats(self):
        mock_col = _make_mock_collection(count=42)

        with (
            patch("vector_store_client._get_client"),
            patch("vector_store_client._get_collection", return_value=mock_col),
        ):
            stats = await get_stats()

        for key in stats:
            assert stats[key]["count"] == 42


class TestSearchSafe:
    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        with patch("vector_store_client.search", new=AsyncMock(side_effect=RuntimeError("down"))):
            result = await search_safe("memories", "query")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_results_on_success(self):
        expected = [{"id": "1", "text": "hello", "metadata": {}, "distance": 0.1}]
        with patch("vector_store_client.search", new=AsyncMock(return_value=expected)):
            result = await search_safe("memories", "query")
        assert result == expected

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        result = await search_safe("memories", "")
        assert result == []


class TestSearchAll:
    @pytest.mark.asyncio
    async def test_merges_results_from_all_collections(self):
        result_a = [{"id": "a1", "text": "t", "metadata": {}, "distance": 0.1, "similarity": 0.9}]
        result_b = [{"id": "b1", "text": "t", "metadata": {}, "distance": 0.2, "similarity": 0.8}]

        async def fake_search(col, query, **kwargs):
            if col == "memories":
                return result_a
            if col == "conversations":
                return result_b
            return []

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await search_all("test query", top_k=10)

        ids = [r["id"] for r in results]
        assert "a1" in ids
        assert "b1" in ids

    @pytest.mark.asyncio
    async def test_collection_field_added_to_results(self):
        result = [{"id": "m1", "text": "t", "metadata": {}, "distance": 0.1}]

        async def fake_search(col, query, **kwargs):
            if col == "memories":
                return result
            return []

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await search_all("test query")

        mem_results = [r for r in results if r.get("collection") == "memories"]
        assert len(mem_results) == 1

    @pytest.mark.asyncio
    async def test_handles_collection_exception_gracefully(self):
        async def fake_search(col, query, **kwargs):
            if col == "memories":
                raise RuntimeError("down")
            return []

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await search_all("query")

        # Should not raise; returns results from non-failing collections
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        # search returns [] for empty query; search_all should tolerate that
        with patch("vector_store_client.search", new=AsyncMock(return_value=[])):
            results = await search_all("")
        assert results == []
