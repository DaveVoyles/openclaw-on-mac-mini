"""Tests for vector_store.py — search_safe wrapper and search logic."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vector_store as mod
from runtime_state import request_context


class TestSearchSafe:
    @pytest.mark.asyncio
    async def test_returns_results_on_success(self):
        fake_results = [{"id": "doc1", "text": "hello", "distance": 0.1}]
        with patch("vector_store_client.search", new_callable=AsyncMock, return_value=fake_results):
            result = await mod.search_safe("memories", "test query")
        assert result == fake_results

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        with patch("vector_store_client.search", new_callable=AsyncMock, side_effect=RuntimeError("ChromaDB down")):
            result = await mod.search_safe("memories", "test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        with patch("vector_store_client.search", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            result = await mod.search_safe("memories", "query")
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_top_k(self):
        mock_search = AsyncMock(return_value=[])
        with patch("vector_store_client.search", mock_search):
            await mod.search_safe("conversations", "q", top_k=10)
        mock_search.assert_called_once_with("conversations", "q", 10)

    @pytest.mark.asyncio
    async def test_passes_kwargs(self):
        mock_search = AsyncMock(return_value=[])
        with patch("vector_store_client.search", mock_search):
            await mod.search_safe("research", "q", threshold=0.8)
        mock_search.assert_called_once_with("research", "q", 5, threshold=0.8)


class TestSearchSafeEdgeCases:
    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self):
        with patch("vector_store_client.search", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
            result = await mod.search_safe("memories", "anything")
        assert result == []

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self):
        with patch("vector_store_client.search", new_callable=AsyncMock, side_effect=ImportError("no chromadb")):
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

    def get(self, where=None, include=None, ids=None):
        return {"ids": [], "metadatas": [], "documents": []}

    def delete(self, ids=None):
        pass


class _CompactionCollection:
    def __init__(self, ids, metadatas):
        self.ids = list(ids)
        self.metadatas = list(metadatas)
        self.deleted_ids = []

    def count(self):
        return len(self.ids)

    def get(self, where=None, include=None, ids=None):
        if ids is not None:
            filtered = [(doc_id, meta) for doc_id, meta in zip(self.ids, self.metadatas) if doc_id in ids]
            return {
                "ids": [row[0] for row in filtered],
                "metadatas": [row[1] for row in filtered],
            }
        return {
            "ids": list(self.ids),
            "metadatas": list(self.metadatas),
            "documents": ["" for _ in self.ids],
        }

    def delete(self, ids):
        self.deleted_ids.extend(ids)
        keep = [(doc_id, meta) for doc_id, meta in zip(self.ids, self.metadatas) if doc_id not in ids]
        self.ids = [row[0] for row in keep]
        self.metadatas = [row[1] for row in keep]

    def upsert(self, ids, documents, metadatas):
        for doc_id, meta in zip(ids, metadatas):
            if doc_id in self.ids:
                idx = self.ids.index(doc_id)
                self.metadatas[idx] = meta
            else:
                self.ids.append(doc_id)
                self.metadatas.append(meta)


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
        with patch("vector_store_client._get_collection", return_value=fake):
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

        with patch("vector_store_client._get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=1, track_access=False)

        assert len(results) == 1
        where = fake.query_calls[0]["where"]
        assert {"channel_id": "10"} in where["$and"]
        assert {"thread_id": "20"} in where["$and"]

    @pytest.mark.asyncio
    async def test_search_fallback_blocks_legacy_and_cross_scope_results(self):
        fake = _FakeCollection([
            _chroma_result([]),
            _chroma_result([
                {"id": "same", "text": "same channel", "metadata": {"channel_id": "10", "thread_id": "20"}},
                {"id": "legacy", "text": "legacy entry", "metadata": {"source": "old"}},
                {"id": "other", "text": "other channel", "metadata": {"channel_id": "99", "thread_id": "20"}},
            ]),
        ])

        with patch("vector_store_client._get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=5, track_access=False)

        assert len(fake.query_calls) == 2
        ids = [item["id"] for item in results]
        assert "same" in ids
        assert "legacy" not in ids
        assert "other" not in ids

    @pytest.mark.asyncio
    async def test_search_fallback_channel_scope_excludes_other_and_legacy_channels(self):
        fake = _FakeCollection([
            _chroma_result([]),
            _chroma_result([
                {"id": "same-channel", "text": "same channel", "metadata": {"channel_id": "10"}},
                {"id": "legacy", "text": "legacy entry", "metadata": {"source": "old"}},
                {"id": "other-channel", "text": "other channel", "metadata": {"channel_id": "88"}},
            ]),
        ])

        with patch("vector_store_client._get_collection", return_value=fake):
            with request_context(channel_id=10):
                results = await mod.search(mod.MEMORIES_COLLECTION, "hello", top_k=5, track_access=False)

        assert len(fake.query_calls) == 2
        ids = [item["id"] for item in results]
        assert "same-channel" in ids
        assert "legacy" not in ids
        assert "other-channel" not in ids

    @pytest.mark.asyncio
    async def test_search_without_context_does_not_reuse_previous_scope(self):
        fake = _FakeCollection([_chroma_result([]), _chroma_result([])])

        with patch("vector_store_client._get_collection", return_value=fake):
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

        with patch("vector_store_client._get_collection", return_value=fake):
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

    @pytest.mark.asyncio
    async def test_search_cross_channel_opt_in_disables_scope_filter(self):
        fake = _FakeCollection([
            _chroma_result([
                {"id": "other", "text": "other channel", "metadata": {"channel_id": "99", "thread_id": "88"}},
            ]),
        ])

        with patch("vector_store_client._get_collection", return_value=fake):
            with request_context(channel_id=10, thread_id=20):
                results = await mod.search(
                    mod.MEMORIES_COLLECTION,
                    "hello",
                    top_k=1,
                    track_access=False,
                    cross_channel=True,
                )

        assert len(results) == 1
        assert "where" not in fake.query_calls[0]

    @pytest.mark.asyncio
    async def test_recall_for_context_cross_channel_opt_in_passed_to_search_all(self):
        mock_search_all = AsyncMock(return_value=[])
        with patch("vector_store_client.search_all", mock_search_all):
            await mod.recall_for_context(
                "hello",
                channel_id=10,
                thread_id=20,
                cross_channel=True,
            )
        assert mock_search_all.await_count == 1
        args, kwargs = mock_search_all.await_args
        assert args == ("hello",)
        assert kwargs["channel_id"] == 10
        assert kwargs["thread_id"] == 20
        assert kwargs["cross_channel"] is True
        assert kwargs["where"] is None

    @pytest.mark.asyncio
    async def test_recall_for_context_anchor_id_sets_where_filter(self):
        mock_search_all = AsyncMock(return_value=[])
        with patch("vector_store_client.search_all", mock_search_all):
            await mod.recall_for_context(
                "hello",
                channel_id=10,
                thread_id=20,
                anchor_id="report_42",
            )
        assert mock_search_all.await_count == 2
        _, first_kwargs = mock_search_all.await_args_list[0]
        assert first_kwargs["where"] == {"anchor_id": "report_42"}

    @pytest.mark.asyncio
    async def test_recall_for_context_suppresses_out_of_scope_wwe_context_without_opt_in(self):
        mock_search_all = AsyncMock(
            return_value=[
                {
                    "collection": mod.MEMORIES_COLLECTION,
                    "text": "WWE RAW and SmackDown recap with pay-per-view notes",
                    "similarity": 0.96,
                },
            ]
        )
        with patch("vector_store_client.search_all", mock_search_all):
            text = await mod.recall_for_context(
                "Summarize deployment blockers from this week",
                channel_id=10,
                thread_id=20,
                cross_channel=False,
            )

        assert text == ""
        notes = mod.consume_recall_guard_notes()
        assert any("out-of-scope sports/WWE" in note for note in notes)

    @pytest.mark.asyncio
    async def test_recall_for_context_keeps_out_of_scope_wwe_context_with_cross_channel_opt_in(self):
        mock_search_all = AsyncMock(
            return_value=[
                {
                    "collection": mod.MEMORIES_COLLECTION,
                    "text": "WWE RAW and SmackDown recap with pay-per-view notes",
                    "similarity": 0.96,
                },
            ]
        )
        with patch("vector_store_client.search_all", mock_search_all):
            text = await mod.recall_for_context(
                "Summarize deployment blockers from this week",
                channel_id=10,
                thread_id=20,
                cross_channel=True,
            )

        assert "[Your Memory]" in text
        assert "WWE RAW and SmackDown recap" in text

    @pytest.mark.asyncio
    async def test_recall_for_context_keeps_wwe_context_with_explicit_pack_directive(self):
        mock_search_all = AsyncMock(
            return_value=[
                {
                    "collection": mod.MEMORIES_COLLECTION,
                    "text": "WWE RAW and SmackDown recap with pay-per-view notes",
                    "similarity": 0.96,
                },
            ]
        )
        with patch("vector_store_client.search_all", mock_search_all):
            text = await mod.recall_for_context(
                "use:wwe summarize deployment blockers from this week",
                channel_id=10,
                thread_id=20,
                cross_channel=False,
            )

        assert "[Your Memory]" in text
        assert "WWE RAW and SmackDown recap" in text
        assert mod.consume_recall_guard_notes() == []

    @pytest.mark.asyncio
    async def test_recall_for_context_suppresses_low_similarity_candidates(self):
        mock_search_all = AsyncMock(
            return_value=[
                {
                    "collection": mod.MEMORIES_COLLECTION,
                    "text": "Mostly unrelated reminder",
                    "similarity": 0.5,
                },
            ]
        )
        with patch("vector_store_client.search_all", mock_search_all):
            text = await mod.recall_for_context("What changed in my project today?")

        assert text == ""
        notes = mod.consume_recall_guard_notes()
        assert any("low-relevance" in note for note in notes)


class TestMemoryLifecycleCompaction:
    @pytest.mark.asyncio
    async def test_add_document_triggers_scope_compaction(self):
        fake = _FakeCollection([])
        mock_compact = AsyncMock(return_value=None)
        with (
            patch("vector_store_client._get_collection", return_value=fake),
            patch("vector_store_compaction._compact_scope_if_needed", mock_compact),
        ):
            with request_context(channel_id=111, thread_id=222):
                await mod.add_document(
                    mod.MEMORIES_COLLECTION,
                    doc_id="doc1",
                    text="remember this",
                    metadata={"source": "test"},
                )
        mock_compact.assert_awaited_once_with(
            collection_name=mod.MEMORIES_COLLECTION,
            channel_id="111",
            thread_id="222",
        )

    @pytest.mark.asyncio
    async def test_compaction_prunes_least_relevant_then_oldest(self):
        now = 1_700_000_000.0
        fake = _CompactionCollection(
            ids=["doc_a", "doc_b", "doc_c", "doc_d"],
            metadatas=[
                {"channel_id": "10", "thread_id": "20", "access_count": 0, "last_accessed": 0.0, "added_at": now - 1000},
                {"channel_id": "10", "thread_id": "20", "access_count": 0, "last_accessed": 10.0, "added_at": now - 900},
                {"channel_id": "10", "thread_id": "20", "access_count": 1, "last_accessed": 0.0, "added_at": now - 800},
                {"channel_id": "10", "thread_id": "20", "access_count": 2, "last_accessed": 0.0, "added_at": now - 700},
            ],
        )
        runtime_state_mock = MagicMock(
            get_memory_lifecycle_policy=MagicMock(
                return_value={"retention_class": "standard", "memory_budget_items": 2}
            ),
            record_memory_compaction_event=MagicMock(),
        )
        audit_mock = MagicMock(audit_log=MagicMock())
        with (
            patch("vector_store_client._get_collection", return_value=fake),
            patch("vector_store.time.time", return_value=now),
            patch.dict("sys.modules", {"runtime_state": runtime_state_mock, "audit": audit_mock}),
        ):
            event = await mod._compact_scope_if_needed(
                collection_name=mod.MEMORIES_COLLECTION,
                channel_id="10",
                thread_id="20",
            )

        assert event is not None
        assert event["pruned_count"] == 2
        assert fake.deleted_ids == ["doc_a", "doc_b"]

    @pytest.mark.asyncio
    async def test_scoped_memory_summary_exposes_policy_and_compactions(self):
        fake = _CompactionCollection(ids=[], metadatas=[])
        runtime_state_mock = MagicMock(
            get_scoped_recall_alerts=MagicMock(return_value=[]),
            get_memory_lifecycle_policy=MagicMock(
                return_value={"retention_class": "long", "memory_budget_items": 400}
            ),
            get_memory_compaction_events=MagicMock(
                return_value=[{"collection": "memories", "pruned_count": 10}]
            ),
        )
        with (
            patch("vector_store_client._get_collection", return_value=fake),
            patch.dict("sys.modules", {"runtime_state": runtime_state_mock}),
        ):
            payload = await mod.get_scoped_memory_summary(channel_id="123", thread_id="456")
        assert payload["memory_policy"]["retention_class"] == "long"
        assert payload["compaction"]["count"] == 1
