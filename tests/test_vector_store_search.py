"""Tests for vector_store.py — search_safe wrapper and search logic."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import vector_store as mod
from runtime_state import request_context


class TestSearchSafe:
    @pytest.mark.asyncio
    async def test_returns_results_on_success(self):
        fake_results = [{"id": "doc1", "text": "hello", "distance": 0.1}]
        with patch.object(mod, "search", new_callable=AsyncMock, return_value=fake_results):
            result = await mod.search_safe("memories", "test query")
        assert result == fake_results

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        with patch.object(mod, "search", new_callable=AsyncMock, side_effect=RuntimeError("ChromaDB down")):
            result = await mod.search_safe("memories", "test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        with patch.object(mod, "search", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            result = await mod.search_safe("memories", "query")
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_top_k(self):
        mock_search = AsyncMock(return_value=[])
        with patch.object(mod, "search", mock_search):
            await mod.search_safe("conversations", "q", top_k=10)
        mock_search.assert_called_once_with("conversations", "q", 10)

    @pytest.mark.asyncio
    async def test_passes_kwargs(self):
        mock_search = AsyncMock(return_value=[])
        with patch.object(mod, "search", mock_search):
            await mod.search_safe("research", "q", threshold=0.8)
        mock_search.assert_called_once_with("research", "q", 5, threshold=0.8)


class TestSearchSafeEdgeCases:
    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self):
        with patch.object(mod, "search", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
            result = await mod.search_safe("memories", "anything")
        assert result == []

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self):
        with patch.object(mod, "search", new_callable=AsyncMock, side_effect=ImportError("no chromadb")):
            result = await mod.search_safe("memories", "anything")
        assert result == []


class _FakeCollection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.query_calls = []
        self.upsert_calls = []

    def count(self):
        return 50

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)


def _chroma_result(items):
    return {
        "ids": [[item["id"] for item in items]],
        "documents": [[item["text"] for item in items]],
        "metadatas": [[item.get("metadata", {}) for item in items]],
        "distances": [[item.get("distance", 0.1) for item in items]],
    }


class TestChannelScopedIsolation:
    @pytest.mark.asyncio
    async def test_add_document_persists_channel_and_thread_metadata(self):
        fake = _FakeCollection([])
        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=111, thread_id=222):
                await mod.add_document(
                    mod.MEMORIES_COLLECTION,
                    doc_id="doc1",
                    text="remember this",
                    metadata={"source": "test"},
                )

        metadata = fake.upsert_calls[0]["metadatas"][0]
        assert metadata["channel_id"] == "111"
        assert metadata["thread_id"] == "222"

    @pytest.mark.asyncio
    async def test_search_applies_channel_thread_scope_where(self):
        fake = _FakeCollection([
            _chroma_result([
                {"id": "doc1", "text": "same thread", "metadata": {"channel_id": "10", "thread_id": "20"}},
            ]),
        ])

        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=1, track_access=False)

        assert len(results) == 1
        where = fake.query_calls[0]["where"]
        assert {"channel_id": "10"} in where["$and"]
        assert {"thread_id": "20"} in where["$and"]

    @pytest.mark.asyncio
    async def test_search_fallback_keeps_same_channel_and_legacy_only(self):
        fake = _FakeCollection([
            _chroma_result([]),
            _chroma_result([
                {"id": "same", "text": "same channel", "metadata": {"channel_id": "10", "thread_id": "20"}},
                {"id": "legacy", "text": "legacy entry", "metadata": {"source": "old"}},
                {"id": "other", "text": "other channel", "metadata": {"channel_id": "99", "thread_id": "20"}},
            ]),
        ])

        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=5, track_access=False)

        assert len(fake.query_calls) == 2
        ids = [item["id"] for item in results]
        assert "same" in ids
        assert "legacy" in ids
        assert "other" not in ids

    @pytest.mark.asyncio
    async def test_search_fallback_channel_scope_excludes_other_channels(self):
        fake = _FakeCollection([
            _chroma_result([]),
            _chroma_result([
                {"id": "same-channel", "text": "same channel", "metadata": {"channel_id": "10"}},
                {"id": "legacy", "text": "legacy entry", "metadata": {"source": "old"}},
                {"id": "other-channel", "text": "other channel", "metadata": {"channel_id": "88"}},
            ]),
        ])

        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=10):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=5, track_access=False)

        assert len(fake.query_calls) == 2
        ids = [item["id"] for item in results]
        assert "same-channel" in ids
        assert "legacy" in ids
        assert "other-channel" not in ids

    @pytest.mark.asyncio
    async def test_search_without_context_does_not_reuse_previous_scope(self):
        fake = _FakeCollection([_chroma_result([]), _chroma_result([])])

        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                await mod.search(
                    mod.MEMORIES_COLLECTION,
                    "hello",
                    top_k=1,
                    track_access=False,
                    enable_scope_fallback=False,
                )
            await mod.search(
                mod.MEMORIES_COLLECTION,
                "hello",
                top_k=1,
                track_access=False,
                enable_scope_fallback=False,
            )

        assert "where" in fake.query_calls[0]
        assert "where" not in fake.query_calls[1]

    @pytest.mark.asyncio
    async def test_search_skips_fallback_when_disabled(self):
        fake = _FakeCollection([_chroma_result([])])

        with patch.object(mod, "_get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(
                    mod.MEMORIES_COLLECTION,
                    "hello",
                    top_k=5,
                    track_access=False,
                    enable_scope_fallback=False,
                )

        assert results == []
        assert len(fake.query_calls) == 1
