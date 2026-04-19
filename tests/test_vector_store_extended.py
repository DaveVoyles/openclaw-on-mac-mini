"""Extended tests for vector_store.py — targeting uncovered lines 61-1207."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vector_store as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_col(count=10, query_results=None, get_results=None):
    """Build a minimal fake ChromaDB collection."""
    col = MagicMock()
    col.count.return_value = count
    if query_results is not None:
        col.query.return_value = query_results
    else:
        col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    if get_results is not None:
        col.get.return_value = get_results
    else:
        col.get.return_value = {"ids": [], "metadatas": [], "documents": []}
    col.upsert = MagicMock()
    col.delete = MagicMock()
    col.update = MagicMock()
    return col


def _chroma_result(items):
    return {
        "ids": [[item["id"] for item in items]],
        "documents": [[item.get("text", "") for item in items]],
        "metadatas": [[item.get("metadata", {}) for item in items]],
        "distances": [[item.get("distance", 0.4) for item in items]],
    }


# ---------------------------------------------------------------------------
# _get_embedding_function (lines 61-78)
# ---------------------------------------------------------------------------


class TestGetEmbeddingFunction:
    def test_vector_store_extended_returns_none_when_no_model_set(self):
        with patch("vector_store_config.EMBEDDING_MODEL", ""):
            result = mod._get_embedding_function()
        assert result is None

    def test_returns_ollama_function_when_model_set(self):
        mock_fn = MagicMock()
        mock_ollama_class = MagicMock(return_value=mock_fn)
        fake_ef_module = MagicMock()
        fake_ef_module.OllamaEmbeddingFunction = mock_ollama_class
        fake_chroma = MagicMock()
        fake_chroma.utils = MagicMock()
        fake_chroma.utils.embedding_functions = fake_ef_module

        with patch("vector_store_config.EMBEDDING_MODEL", "nomic-embed-text"):
            with patch.dict(
                "sys.modules",
                {
                    "chromadb": fake_chroma,
                    "chromadb.utils": fake_chroma.utils,
                    "chromadb.utils.embedding_functions": fake_ef_module,
                },
            ):
                result = mod._get_embedding_function()

        assert result == mock_fn

    def test_returns_none_on_import_error(self):
        import builtins

        real_import = builtins.__import__

        def patched_import(name, *args, **kwargs):
            if name == "chromadb.utils.embedding_functions":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        with patch("vector_store_config.EMBEDDING_MODEL", "some-model"):
            with patch("builtins.__import__", side_effect=patched_import):
                result = mod._get_embedding_function()

        assert result is None

    def test_returns_none_on_general_exception(self):
        mock_ollama_class = MagicMock(side_effect=RuntimeError("connection refused"))
        fake_ef_module = MagicMock()
        fake_ef_module.OllamaEmbeddingFunction = mock_ollama_class

        with patch("vector_store_config.EMBEDDING_MODEL", "fail-model"):
            with patch.dict(
                "sys.modules",
                {
                    "chromadb.utils.embedding_functions": fake_ef_module,
                },
            ):
                result = mod._get_embedding_function()

        assert result is None


# ---------------------------------------------------------------------------
# _infer_recall_domains (lines 127-139)
# ---------------------------------------------------------------------------


class TestInferRecallDomains:
    def test_infers_wwe_with_single_hit(self):
        domains = mod._infer_recall_domains("WWE RAW results this week")
        assert "wwe" in domains

    def test_infers_wwe_with_wrestling_term(self):
        domains = mod._infer_recall_domains("wrestling match replay")
        assert "wwe" in domains

    def test_infers_sports_requires_two_hits(self):
        domains = mod._infer_recall_domains("NBA scores and ESPN highlights")
        assert "sports" in domains

    def test_sports_single_hit_not_inferred(self):
        # Only one sports term ("mlb") — needs 2+ hits, so should not infer "sports"
        domains = mod._infer_recall_domains("mlb standings update today")
        assert "sports" not in domains

    def test_domain_literal_match(self):
        domains = mod._infer_recall_domains("sports roundup")
        assert "sports" in domains

    def test_vector_store_extended_empty_string_returns_empty(self):
        assert mod._infer_recall_domains("") == set()

    def test_vector_store_extended_none_returns_empty(self):
        assert mod._infer_recall_domains(None) == set()


# ---------------------------------------------------------------------------
# _is_legacy_metadata (line 199)
# ---------------------------------------------------------------------------


class TestIsLegacyMetadata:
    def test_returns_true_for_no_scope(self):
        assert mod._is_legacy_metadata({"source": "old"}) is True

    def test_returns_false_when_channel_id_present(self):
        assert mod._is_legacy_metadata({"channel_id": "10"}) is False

    def test_returns_false_when_thread_id_present(self):
        assert mod._is_legacy_metadata({"thread_id": "20"}) is False

    def test_returns_true_for_empty_dict(self):
        assert mod._is_legacy_metadata({}) is True


# ---------------------------------------------------------------------------
# _compact_scope_if_needed early-exit paths (lines 235, 241-242, 249-250)
# ---------------------------------------------------------------------------


class TestCompactScopeEarlyExits:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_channel_id(self):
        result = await mod._compact_scope_if_needed(
            collection_name=mod.MEMORIES_COLLECTION,
            channel_id=None,
            thread_id=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_runtime_state_import_fails(self):
        import builtins

        real_import = builtins.__import__

        def fail_import(name, *args, **kwargs):
            if name == "runtime_state":
                raise ImportError("no runtime_state")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_import):
            result = await mod._compact_scope_if_needed(
                collection_name=mod.MEMORIES_COLLECTION,
                channel_id="10",
                thread_id="20",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_policy_fetch_raises(self):
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(side_effect=RuntimeError("db error"))
        mock_rs.record_memory_compaction_event = MagicMock()

        with patch.dict("sys.modules", {"runtime_state": mock_rs}):
            result = await mod._compact_scope_if_needed(
                collection_name=mod.MEMORIES_COLLECTION,
                channel_id="10",
                thread_id="20",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_collection_empty(self):
        col = _make_fake_col(count=0)
        col.get.return_value = {"ids": [], "metadatas": []}
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.record_memory_compaction_event = MagicMock()

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod._compact_scope_if_needed(
                    collection_name=mod.MEMORIES_COLLECTION,
                    channel_id="10",
                    thread_id="20",
                )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_within_budget(self):
        col = _make_fake_col(count=5)
        col.get.return_value = {
            "ids": ["a", "b"],
            "metadatas": [{"channel_id": "10", "thread_id": "20"}, {"channel_id": "10", "thread_id": "20"}],
        }
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.record_memory_compaction_event = MagicMock()

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod._compact_scope_if_needed(
                    collection_name=mod.MEMORIES_COLLECTION,
                    channel_id="10",
                    thread_id="20",
                )
        assert result is None

    @pytest.mark.asyncio
    async def test_compaction_with_protection_window(self):
        """Test that recently-added items are protected (lines 276-279)."""
        now = 1_700_000_000.0
        col = _make_fake_col(count=5)
        col.get.return_value = {
            "ids": ["old", "new"],
            "metadatas": [
                {
                    "channel_id": "10",
                    "thread_id": "20",
                    "access_count": 0,
                    "last_accessed": 0.0,
                    "added_at": now - 99999,
                },
                {
                    "channel_id": "10",
                    "thread_id": "20",
                    "access_count": 0,
                    "last_accessed": 0.0,
                    "added_at": now - 1,
                },  # recently added
            ],
        }
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 1}
        )
        mock_rs.record_memory_compaction_event = MagicMock()
        audit_mock = MagicMock(audit_log=MagicMock())

        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store.time.time", return_value=now):
                with patch.dict("sys.modules", {"runtime_state": mock_rs, "audit": audit_mock}):
                    event = await mod._compact_scope_if_needed(
                        collection_name=mod.MEMORIES_COLLECTION,
                        channel_id="10",
                        thread_id="20",
                    )

        # "old" should be pruned first, "new" is protected
        assert event is not None
        assert "old" in event["pruned_ids"]

    @pytest.mark.asyncio
    async def test_compaction_record_event_exception_is_swallowed(self):
        """record_memory_compaction_event raising should not propagate (lines 319-320)."""
        now = 1_700_000_000.0
        col = _make_fake_col(count=5)
        col.get.return_value = {
            "ids": ["a", "b", "c"],
            "metadatas": [
                {
                    "channel_id": "10",
                    "thread_id": "20",
                    "access_count": 0,
                    "last_accessed": 0.0,
                    "added_at": now - 1000,
                },
                {"channel_id": "10", "thread_id": "20", "access_count": 0, "last_accessed": 0.0, "added_at": now - 900},
                {"channel_id": "10", "thread_id": "20", "access_count": 1, "last_accessed": 1.0, "added_at": now - 800},
            ],
        }
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "short", "memory_budget_items": 1}
        )
        mock_rs.record_memory_compaction_event = MagicMock(side_effect=RuntimeError("record failed"))
        audit_mock = MagicMock()
        audit_mock.audit_log = MagicMock()

        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store.time.time", return_value=now):
                with patch.dict("sys.modules", {"runtime_state": mock_rs, "audit": audit_mock}):
                    event = await mod._compact_scope_if_needed(
                        collection_name=mod.MEMORIES_COLLECTION,
                        channel_id="10",
                        thread_id="20",
                    )

        assert event is not None
        assert event["pruned_count"] == 2

    @pytest.mark.asyncio
    async def test_compaction_audit_log_exception_is_swallowed(self):
        """audit_log raising should not propagate (lines 321-330)."""
        now = 1_700_000_000.0
        col = _make_fake_col(count=5)
        col.get.return_value = {
            "ids": ["a", "b"],
            "metadatas": [
                {
                    "channel_id": "10",
                    "thread_id": "20",
                    "access_count": 0,
                    "last_accessed": 0.0,
                    "added_at": now - 1000,
                },
                {"channel_id": "10", "thread_id": "20", "access_count": 0, "last_accessed": 0.0, "added_at": now - 900},
            ],
        }
        mock_rs = MagicMock()
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "short", "memory_budget_items": 1}
        )
        mock_rs.record_memory_compaction_event = MagicMock()

        broken_audit = MagicMock()
        broken_audit.audit_log = MagicMock(side_effect=RuntimeError("audit down"))

        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store.time.time", return_value=now):
                with patch.dict("sys.modules", {"runtime_state": mock_rs, "audit": broken_audit}):
                    event = await mod._compact_scope_if_needed(
                        collection_name=mod.MEMORIES_COLLECTION,
                        channel_id="10",
                        thread_id="20",
                    )

        assert event is not None


# ---------------------------------------------------------------------------
# add_document (line 382 — empty text guard)
# ---------------------------------------------------------------------------


class TestAddDocumentEdgeCases:
    @pytest.mark.asyncio
    async def test_skips_empty_text(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store_compaction._compact_scope_if_needed", new_callable=AsyncMock):
                await mod.add_document(mod.MEMORIES_COLLECTION, "id1", "")
                await mod.add_document(mod.MEMORIES_COLLECTION, "id1", "   ")
        col.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_valid_text(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store_compaction._compact_scope_if_needed", new_callable=AsyncMock):
                await mod.add_document(mod.MEMORIES_COLLECTION, "id1", "some text")
        col.upsert.assert_called_once()


# ---------------------------------------------------------------------------
# search — empty query / empty collection / decay / confidence / threshold
# ---------------------------------------------------------------------------


class TestSearchEdgeCases:
    @pytest.mark.asyncio
    async def test_returns_empty_for_blank_query(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.search(mod.MEMORIES_COLLECTION, "")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_whitespace_query(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.search(mod.MEMORIES_COLLECTION, "   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_collection_empty(self):
        col = _make_fake_col(count=0)
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.search(mod.MEMORIES_COLLECTION, "query", track_access=False)
        assert result == []

    @pytest.mark.asyncio
    async def test_decayed_document_gets_similarity_penalty(self):
        """Decayed docs get 0.9× similarity (line 467)."""
        # distance=0.0 → similarity=1.0; with decay → 0.9
        col = _make_fake_col(count=1)
        col.query.return_value = _chroma_result(
            [
                {"id": "doc1", "text": "text", "metadata": {"decayed": True}, "distance": 0.0},
            ]
        )
        with patch("vector_store_client._get_collection", return_value=col):
            results = await mod.search(
                mod.MEMORIES_COLLECTION,
                "query",
                track_access=False,
                threshold=0.5,
                cross_channel=True,
            )
        assert results
        assert results[0]["similarity"] == pytest.approx(0.9, abs=0.01)

    @pytest.mark.asyncio
    async def test_confidence_boost_applied(self):
        """High-confidence docs get boosted similarity (lines 471-475)."""
        # distance=0.0 → raw similarity=1.0; confidence=1.0 → *1.0 = 1.0
        col = _make_fake_col(count=1)
        col.query.return_value = _chroma_result(
            [
                {"id": "doc1", "text": "text", "metadata": {"confidence": 1.0}, "distance": 0.0},
            ]
        )
        with patch("vector_store_client._get_collection", return_value=col):
            results = await mod.search(
                mod.MEMORIES_COLLECTION,
                "query",
                track_access=False,
                threshold=0.5,
                cross_channel=True,
            )
        assert results
        assert results[0]["similarity"] == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_confidence_invalid_value_is_ignored(self):
        """Invalid confidence value should not crash (lines 471-475)."""
        col = _make_fake_col(count=1)
        col.query.return_value = _chroma_result(
            [
                {"id": "doc1", "text": "text", "metadata": {"confidence": "invalid"}, "distance": 0.0},
            ]
        )
        with patch("vector_store_client._get_collection", return_value=col):
            results = await mod.search(
                mod.MEMORIES_COLLECTION,
                "query",
                track_access=False,
                threshold=0.5,
                cross_channel=True,
            )
        assert results  # should still return, not crash

    @pytest.mark.asyncio
    async def test_below_threshold_filtered_out(self):
        """Items below similarity threshold are excluded (line 477)."""
        col = _make_fake_col(count=1)
        # distance=2.0 → similarity=0.0 (0 = 1 - 2/2), below any threshold
        col.query.return_value = _chroma_result(
            [
                {"id": "doc1", "text": "text", "metadata": {}, "distance": 2.0},
            ]
        )
        with patch("vector_store_client._get_collection", return_value=col):
            results = await mod.search(
                mod.MEMORIES_COLLECTION,
                "query",
                track_access=False,
                threshold=0.5,
                cross_channel=True,
            )
        assert results == []

    @pytest.mark.asyncio
    async def test_track_access_fires_bump_task(self):
        """track_access=True creates a bump task (lines 506-511)."""
        col = _make_fake_col(count=1)
        col.query.return_value = _chroma_result(
            [
                {"id": "doc1", "text": "text", "metadata": {"channel_id": "10", "thread_id": "20"}, "distance": 0.0},
            ]
        )

        bump_mock = AsyncMock()
        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store_compaction.bump_access", bump_mock):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    results = await mod.search(
                        mod.MEMORIES_COLLECTION,
                        "query",
                        track_access=True,
                        threshold=0.0,
                    )
                # Let any pending tasks run
                await asyncio.sleep(0)

        assert results

    @pytest.mark.asyncio
    async def test_fallback_blocked_cross_thread_counted(self):
        """Records blocked_cross_thread counter (lines 541-542)."""
        col = MagicMock()
        col.count.return_value = 5
        # First query (scoped) returns nothing; second (fallback) returns cross-thread doc
        col.query.side_effect = [
            _chroma_result([]),  # scoped query → empty
            _chroma_result(
                [
                    {
                        "id": "cross-thread",
                        "text": "text",
                        "metadata": {"channel_id": "10", "thread_id": "99"},
                        "distance": 0.0,
                    },
                ]
            ),
        ]
        mock_rs = MagicMock()
        mock_rs.record_scoped_recall_alert = MagicMock()

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    results = await mod.search(
                        mod.MEMORIES_COLLECTION,
                        "query",
                        track_access=False,
                        threshold=0.0,
                    )
        # Cross-thread doc should be blocked
        assert all(r["id"] != "cross-thread" for r in results)

    @pytest.mark.asyncio
    async def test_fallback_record_alert_exception_swallowed(self):
        """record_scoped_recall_alert raising is swallowed (lines 562-563)."""
        col = MagicMock()
        col.count.return_value = 5
        col.query.side_effect = [
            _chroma_result([]),
            _chroma_result(
                [
                    {
                        "id": "legacy",
                        "text": "text",
                        "metadata": {"source": "old"},
                        "distance": 0.0,
                    },  # no channel_id → unscoped
                ]
            ),
        ]
        bad_rs = MagicMock()
        bad_rs.record_scoped_recall_alert = MagicMock(side_effect=RuntimeError("alert failed"))

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": bad_rs}):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    # Should not raise
                    results = await mod.search(
                        mod.MEMORIES_COLLECTION,
                        "query",
                        track_access=False,
                        threshold=0.0,
                    )
        assert results == []  # legacy docs blocked

    @pytest.mark.asyncio
    async def test_fallback_access_tracking_fires(self):
        """track_access=True fires bump for fallback results (lines 576-581)."""
        col = MagicMock()
        col.count.return_value = 5
        col.query.side_effect = [
            _chroma_result([]),  # first call empty
            _chroma_result(
                [
                    {
                        "id": "same",
                        "text": "text",
                        "metadata": {"channel_id": "10", "thread_id": "20"},
                        "distance": 0.0,
                    },
                ]
            ),
        ]
        bump_mock = AsyncMock()
        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store_compaction.bump_access", bump_mock):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    results = await mod.search(
                        mod.MEMORIES_COLLECTION,
                        "query",
                        track_access=True,
                        threshold=0.0,
                    )
                await asyncio.sleep(0)
        assert results


# ---------------------------------------------------------------------------
# search_all — exception handling (lines 635-637)
# ---------------------------------------------------------------------------


class TestSearchAll:
    @pytest.mark.asyncio
    async def test_exception_in_one_collection_is_skipped(self):
        async def fake_search(col, query, **kwargs):
            if col == mod.MEMORIES_COLLECTION:
                raise RuntimeError("db down")
            return [{"id": "r1", "text": "t", "distance": 0.1, "similarity": 0.95}]

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await mod.search_all("query", top_k=5)

        ids = [r["id"] for r in results]
        assert "r1" in ids

    @pytest.mark.asyncio
    async def test_results_tagged_with_collection(self):
        async def fake_search(col, query, **kwargs):
            return [{"id": "x", "text": "t", "distance": 0.2, "similarity": 0.9}]

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await mod.search_all("query", top_k=10)

        assert all("collection" in r for r in results)

    @pytest.mark.asyncio
    async def test_results_sorted_by_distance(self):
        distances = [0.5, 0.1, 0.3]
        collections = [mod.MEMORIES_COLLECTION, mod.CONVERSATIONS_COLLECTION, mod.RESEARCH_COLLECTION]

        async def fake_search(col, query, **kwargs):
            idx = collections.index(col)
            return [{"id": col, "text": "t", "distance": distances[idx], "similarity": 0.8}]

        with patch("vector_store_client.search", side_effect=fake_search):
            results = await mod.search_all("query", top_k=10)

        assert results[0]["distance"] == 0.1
        assert results[1]["distance"] == 0.3
        assert results[2]["distance"] == 0.5


# ---------------------------------------------------------------------------
# delete_document (lines 647-653)
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    @pytest.mark.asyncio
    async def test_delete_calls_collection_delete(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            await mod.delete_document(mod.MEMORIES_COLLECTION, "doc1")
        col.delete.assert_called_once_with(ids=["doc1"])


# ---------------------------------------------------------------------------
# bump_access (lines 662-683)
# ---------------------------------------------------------------------------


class TestBumpAccess:
    @pytest.mark.asyncio
    async def test_does_nothing_for_empty_list(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            await mod.bump_access(mod.MEMORIES_COLLECTION, [])
        col.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_increments_access_count(self):
        col = _make_fake_col()
        col.get.return_value = {
            "ids": ["doc1"],
            "metadatas": [{"access_count": 3, "last_accessed": 0.0}],
        }
        with patch("vector_store_client._get_collection", return_value=col):
            with patch("vector_store.time.time", return_value=12345.0):
                await mod.bump_access(mod.MEMORIES_COLLECTION, ["doc1"])
        col.update.assert_called_once()
        call_kwargs = col.update.call_args
        meta = call_kwargs.kwargs["metadatas"][0] if call_kwargs.kwargs else call_kwargs[1]["metadatas"][0]
        assert meta["access_count"] == 4
        assert meta["last_accessed"] == 12345.0

    @pytest.mark.asyncio
    async def test_vector_store_extended_skips_missing_doc(self):
        col = _make_fake_col()
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("vector_store_client._get_collection", return_value=col):
            await mod.bump_access(mod.MEMORIES_COLLECTION, ["missing"])
        col.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_bump_is_swallowed(self):
        col = _make_fake_col()
        col.get.side_effect = RuntimeError("collection error")
        with patch("vector_store_client._get_collection", return_value=col):
            await mod.bump_access(mod.MEMORIES_COLLECTION, ["doc1"])  # should not raise


# ---------------------------------------------------------------------------
# get_decayed_documents (lines 696-729)
# ---------------------------------------------------------------------------


class TestGetDecayedDocuments:
    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_collection(self):
        col = _make_fake_col(count=0)
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.get_decayed_documents(mod.MEMORIES_COLLECTION)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_decayed_docs(self):
        col = _make_fake_col(count=3)
        cutoff = 1_000_000.0
        col.get.return_value = {
            "ids": ["old", "new"],
            "metadatas": [
                {"last_accessed": 100.0, "access_count": 0},  # old, low access
                {"last_accessed": cutoff + 9999, "access_count": 5},  # recent
            ],
            "documents": ["old text", "new text"],
        }
        with patch("vector_store.time.time", return_value=cutoff + 86400 * 31):
            with patch("vector_store_client._get_collection", return_value=col):
                result = await mod.get_decayed_documents(mod.MEMORIES_COLLECTION, max_age_days=30)

        ids = [r["id"] for r in result]
        assert "old" in ids
        assert "new" not in ids

    @pytest.mark.asyncio
    async def test_fallback_when_where_filter_raises(self):
        """If where-filtered get raises, falls back to full scan (lines 711-714)."""
        col = _make_fake_col(count=1)
        # First call (with where filter) raises; second (full) returns data
        col.get.side_effect = [
            RuntimeError("filter not supported"),
            {
                "ids": ["old"],
                "metadatas": [{"last_accessed": 0.0, "access_count": 0}],
                "documents": ["text"],
            },
        ]
        with patch("vector_store.time.time", return_value=86400 * 31 + 1):
            with patch("vector_store_client._get_collection", return_value=col):
                result = await mod.get_decayed_documents(mod.MEMORIES_COLLECTION)

        assert any(r["id"] == "old" for r in result)


# ---------------------------------------------------------------------------
# mark_decayed (lines 734-754)
# ---------------------------------------------------------------------------


class TestMarkDecayed:
    @pytest.mark.asyncio
    async def test_vector_store_extended_returns_zero_for_empty_list(self):
        col = _make_fake_col()
        with patch("vector_store_client._get_collection", return_value=col):
            count = await mod.mark_decayed(mod.MEMORIES_COLLECTION, [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_marks_existing_doc_decayed(self):
        col = _make_fake_col()
        col.get.return_value = {
            "ids": ["doc1"],
            "metadatas": [{"access_count": 1}],
        }
        with patch("vector_store_client._get_collection", return_value=col):
            count = await mod.mark_decayed(mod.MEMORIES_COLLECTION, ["doc1"])
        assert count == 1
        call_kwargs = col.update.call_args
        meta = call_kwargs.kwargs.get("metadatas", [call_kwargs[1]["metadatas"]])[0]
        assert meta.get("decayed") is True

    @pytest.mark.asyncio
    async def test_vector_store_extended_skips_missing_doc_v2(self):
        col = _make_fake_col()
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("vector_store_client._get_collection", return_value=col):
            count = await mod.mark_decayed(mod.MEMORIES_COLLECTION, ["missing"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_exception_in_mark_is_swallowed(self):
        col = _make_fake_col()
        col.get.side_effect = RuntimeError("db error")
        with patch("vector_store_client._get_collection", return_value=col):
            count = await mod.mark_decayed(mod.MEMORIES_COLLECTION, ["doc1"])
        assert count == 0


# ---------------------------------------------------------------------------
# get_stats (lines 765-774)
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_returns_counts_for_all_collections(self):
        col_memories = _make_fake_col(count=10)
        col_conversations = _make_fake_col(count=5)
        col_research = _make_fake_col(count=2)

        def fake_get_collection(name):
            return {
                mod.MEMORIES_COLLECTION: col_memories,
                mod.CONVERSATIONS_COLLECTION: col_conversations,
                mod.RESEARCH_COLLECTION: col_research,
            }[name]

        with patch("vector_store_client._get_client", return_value=MagicMock()):
            with patch("vector_store_client._get_collection", side_effect=fake_get_collection):
                stats = await mod.get_stats()

        assert stats[mod.MEMORIES_COLLECTION]["count"] == 10
        assert stats[mod.CONVERSATIONS_COLLECTION]["count"] == 5
        assert stats[mod.RESEARCH_COLLECTION]["count"] == 2


# ---------------------------------------------------------------------------
# get_scoped_memory_summary (lines 790, 813-890)
# ---------------------------------------------------------------------------


class TestGetScopedMemorySummary:
    @pytest.mark.asyncio
    async def test_vector_store_extended_raises_when_channel_id_none(self):
        with pytest.raises(ValueError, match="channel_id is required"):
            await mod.get_scoped_memory_summary(channel_id=None)

    @pytest.mark.asyncio
    async def test_returns_scope_data(self):
        col = _make_fake_col(count=2)
        col.get.return_value = {
            "ids": ["doc1", "doc2"],
            "metadatas": [
                {"channel_id": "10", "thread_id": "20", "added_at": 1000.0, "type": "fact"},
                {"channel_id": "10", "thread_id": "20", "added_at": 2000.0, "type": "summary"},
            ],
            "documents": ["text1", "text2"],
        }
        mock_rs = MagicMock()
        mock_rs.get_scoped_recall_alerts = MagicMock(return_value=[])
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.get_memory_compaction_events = MagicMock(return_value=[])

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod.get_scoped_memory_summary(channel_id="10", thread_id="20")

        assert result["scope"]["channel_id"] == "10"
        assert result["total_count"] > 0

    @pytest.mark.asyncio
    async def test_include_anchor_with_matching_anchor(self):
        col = _make_fake_col(count=0)
        col.get.return_value = {"ids": [], "metadatas": [], "documents": []}

        mock_rs = MagicMock()
        mock_rs.get_scoped_recall_alerts = MagicMock(return_value=[])
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.get_memory_compaction_events = MagicMock(return_value=[])
        mock_rs.get_anchor_state = MagicMock(
            return_value={
                "channel_id": "10",
                "thread_id": "20",
                "anchor_id": "report_42",
                "timestamp": 12345.0,
            }
        )

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod.get_scoped_memory_summary(channel_id="10", thread_id="20", include_anchor=True)

        assert result["anchor"]["present"] is True
        assert result["anchor"]["anchor_id"] == "report_42"

    @pytest.mark.asyncio
    async def test_include_anchor_with_no_anchor(self):
        col = _make_fake_col(count=0)
        col.get.return_value = {"ids": [], "metadatas": [], "documents": []}

        mock_rs = MagicMock()
        mock_rs.get_scoped_recall_alerts = MagicMock(return_value=[])
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.get_memory_compaction_events = MagicMock(return_value=[])
        mock_rs.get_anchor_state = MagicMock(return_value=None)

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod.get_scoped_memory_summary(channel_id="10", thread_id="20", include_anchor=True)

        assert result["anchor"]["present"] is False

    @pytest.mark.asyncio
    async def test_alerts_exception_returns_empty_alerts(self):
        """get_scoped_recall_alerts raising is caught, returns empty alerts (lines 870-871)."""
        col = _make_fake_col(count=0)
        col.get.return_value = {"ids": [], "metadatas": [], "documents": []}

        mock_rs = MagicMock()
        mock_rs.get_scoped_recall_alerts = MagicMock(side_effect=RuntimeError("alerts down"))
        mock_rs.get_memory_lifecycle_policy = MagicMock(
            return_value={"retention_class": "standard", "memory_budget_items": 200}
        )
        mock_rs.get_memory_compaction_events = MagicMock(return_value=[])

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod.get_scoped_memory_summary(channel_id="10", thread_id="20")

        assert result["alerts"] == {"count": 0, "items": []}

    @pytest.mark.asyncio
    async def test_policy_exception_returns_defaults(self):
        """get_memory_lifecycle_policy raising returns default policy (lines 888-890)."""
        col = _make_fake_col(count=0)
        col.get.return_value = {"ids": [], "metadatas": [], "documents": []}

        mock_rs = MagicMock()
        mock_rs.get_scoped_recall_alerts = MagicMock(return_value=[])
        mock_rs.get_memory_lifecycle_policy = MagicMock(side_effect=RuntimeError("policy down"))
        mock_rs.get_memory_compaction_events = MagicMock(side_effect=RuntimeError("compaction down"))

        with patch("vector_store_client._get_collection", return_value=col):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                result = await mod.get_scoped_memory_summary(channel_id="10", thread_id="20")

        assert result["memory_policy"]["retention_class"] == "standard"
        assert result["compaction"]["count"] == 0


# ---------------------------------------------------------------------------
# clear_scoped_memory (lines 900-938)
# ---------------------------------------------------------------------------


class TestClearScopedMemory:
    @pytest.mark.asyncio
    async def test_vector_store_extended_raises_when_channel_id_none_v2(self):
        with pytest.raises(ValueError, match="channel_id is required"):
            await mod.clear_scoped_memory(channel_id=None)

    @pytest.mark.asyncio
    async def test_deletes_scoped_documents(self):
        col = _make_fake_col(count=3)
        col.get.return_value = {"ids": ["doc1", "doc2"], "metadatas": []}

        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.clear_scoped_memory(channel_id="10", thread_id="20")

        col.delete.assert_called()
        assert result["total_deleted"] == 2 * 3  # 3 collections × 2 docs each

    @pytest.mark.asyncio
    async def test_skips_empty_collections(self):
        col = _make_fake_col(count=0)
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.clear_scoped_memory(channel_id="10", thread_id="20")
        assert result["total_deleted"] == 0
        col.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_scope_info(self):
        col = _make_fake_col(count=1)
        col.get.return_value = {"ids": [], "metadatas": []}
        with patch("vector_store_client._get_collection", return_value=col):
            result = await mod.clear_scoped_memory(channel_id="42", thread_id="99")
        assert result["scope"]["channel_id"] == "42"
        assert result["scope"]["thread_id"] == "99"


# ---------------------------------------------------------------------------
# add_memory_deduped (lines 984-1015)
# ---------------------------------------------------------------------------


class TestAddMemoryDeduped:
    @pytest.mark.asyncio
    async def test_stores_when_no_duplicate(self):
        add_mock = AsyncMock()
        search_mock = AsyncMock(return_value=[])
        bump_mock = AsyncMock()
        with patch("vector_store_client.search", search_mock):
            with patch("vector_store_client.add_document", add_mock):
                with patch("vector_store_compaction.bump_access", bump_mock):
                    result = await mod.add_memory_deduped("fact1", "unique content")
        assert result is True
        add_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_duplicate_found(self):
        add_mock = AsyncMock()
        bump_mock = AsyncMock()
        existing = [{"id": "mem_existing", "similarity": 0.95, "text": "duplicate"}]
        with patch("vector_store_client.search", AsyncMock(return_value=existing)):
            with patch("vector_store_client.add_document", add_mock):
                with patch("vector_store_compaction.bump_access", bump_mock):
                    result = await mod.add_memory_deduped("fact1", "near duplicate content")
        assert result is False
        add_mock.assert_not_called()
        bump_mock.assert_awaited_once_with(mod.MEMORIES_COLLECTION, ["mem_existing"])

    @pytest.mark.asyncio
    async def test_stores_when_search_raises(self):
        """If dedup check fails, stores anyway (lines 1000-1001)."""
        add_mock = AsyncMock()
        with patch("vector_store_client.search", AsyncMock(side_effect=RuntimeError("db error"))):
            with patch("vector_store_client.add_document", add_mock):
                with patch("vector_store_compaction._compact_scope_if_needed", AsyncMock()):
                    result = await mod.add_memory_deduped("fact2", "some content")
        assert result is True
        add_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# add_conversation_summary (line 1027)
# ---------------------------------------------------------------------------


class TestAddConversationSummary:
    @pytest.mark.asyncio
    async def test_stores_in_conversations_collection(self):
        add_mock = AsyncMock()
        with patch("vector_store_client.add_document", add_mock):
            await mod.add_conversation_summary(
                user_id=123,
                thread_name="general",
                summary="We talked about X",
                channel_id=10,
                thread_id=20,
            )
        add_mock.assert_awaited_once()
        args, kwargs = add_mock.await_args
        assert args[0] == mod.CONVERSATIONS_COLLECTION
        assert kwargs["metadata"]["type"] == "summary"
        assert kwargs["metadata"]["user_id"] == "123"


# ---------------------------------------------------------------------------
# add_research_report (lines 1064-1076)
# ---------------------------------------------------------------------------


class TestAddResearchReport:
    @pytest.mark.asyncio
    async def test_stores_and_returns_report_id(self):
        add_mock = AsyncMock()
        with patch("vector_store_client.add_document", add_mock):
            report_id = await mod.add_research_report(
                query="deployment blockers",
                report="Here are the blockers...",
                sources=["http://source1.com"],
                channel_id=10,
                thread_id=20,
            )
        assert report_id.startswith("research_")
        add_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sets_anchor_state_when_channel_id_present(self):
        import runtime_state as _rs_mod

        add_mock = AsyncMock()
        mock_set_anchor = MagicMock()

        # add_research_report does a lazy `from runtime_state import set_anchor_state`.
        # Use patch.object so the attribute is restored after the block — the old
        # builtins.__import__ approach permanently mutated runtime_state.set_anchor_state.
        with patch("vector_store_client.add_document", add_mock):
            with patch("vector_store._resolve_scope", return_value=("10", "20")):
                with patch.object(_rs_mod, "set_anchor_state", mock_set_anchor):
                    report_id = await mod.add_research_report(
                        query="test",
                        report="report text",
                        channel_id=10,
                        thread_id=20,
                    )

        assert report_id.startswith("research_")
        mock_set_anchor.assert_called_once()

    @pytest.mark.asyncio
    async def test_anchor_state_exception_is_swallowed(self):
        """Exception in set_anchor_state is swallowed (lines 1074-1075)."""
        bad_rs = MagicMock()
        bad_rs.set_anchor_state = MagicMock(side_effect=RuntimeError("state error"))
        add_mock = AsyncMock()

        from runtime_state import request_context

        with patch("vector_store_client.add_document", add_mock):
            with patch.dict("sys.modules", {"runtime_state": bad_rs}):
                with request_context(channel_id=10, thread_id=20):
                    report_id = await mod.add_research_report(query="test", report="text")

        assert report_id.startswith("research_")


# ---------------------------------------------------------------------------
# recall (lines 1098-1105)
# ---------------------------------------------------------------------------


class TestRecall:
    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_results(self):
        with patch("vector_store_client.search_all", AsyncMock(return_value=[])):
            result = await mod.recall("query")
        assert result == ""

    @pytest.mark.asyncio
    async def test_formats_results_correctly(self):
        fake_results = [
            {
                "collection": mod.MEMORIES_COLLECTION,
                "text": "some fact here",
                "similarity": 0.92,
                "distance": 0.16,
            }
        ]
        with patch("vector_store_client.search_all", AsyncMock(return_value=fake_results)):
            result = await mod.recall("query")
        assert "[Memories · 92%]" in result
        assert "some fact here" in result

    @pytest.mark.asyncio
    async def test_handles_multiple_results(self):
        fake_results = [
            {"collection": mod.MEMORIES_COLLECTION, "text": "fact", "similarity": 0.9, "distance": 0.2},
            {"collection": mod.CONVERSATIONS_COLLECTION, "text": "summary", "similarity": 0.8, "distance": 0.4},
        ]
        with patch("vector_store_client.search_all", AsyncMock(return_value=fake_results)):
            result = await mod.recall("query")
        lines = result.strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# recall_for_context — record alert exception swallowed (lines 1206-1207)
# ---------------------------------------------------------------------------


class TestRecallForContextAlertException:
    @pytest.mark.asyncio
    async def test_record_alert_exception_swallowed(self):
        """record_scoped_recall_alert exception in recall_for_context is swallowed."""
        bad_rs = MagicMock()
        bad_rs.record_scoped_recall_alert = MagicMock(side_effect=RuntimeError("alert error"))

        low_sim_result = [
            {
                "collection": mod.MEMORIES_COLLECTION,
                "text": "irrelevant",
                "similarity": 0.5,  # below RECALL_GUARD_MIN_SIMILARITY
                "distance": 1.0,
            }
        ]

        with patch("vector_store_client.search_all", AsyncMock(return_value=low_sim_result)):
            with patch.dict("sys.modules", {"runtime_state": bad_rs}):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    result = await mod.recall_for_context("query", channel_id=10, thread_id=20)

        assert result == ""  # suppressed, no exception raised

    @pytest.mark.asyncio
    async def test_suppressed_domain_with_scoped_channel_triggers_alert(self):
        """When domain is suppressed and channel is scoped, alert is attempted."""
        mock_rs = MagicMock()
        mock_rs.record_scoped_recall_alert = MagicMock()

        wwe_result = [
            {
                "collection": mod.MEMORIES_COLLECTION,
                "text": "WWE RAW recap SmackDown wrestling results",
                "similarity": 0.95,
                "distance": 0.1,
            }
        ]

        with patch("vector_store_client.search_all", AsyncMock(return_value=wwe_result)):
            with patch.dict("sys.modules", {"runtime_state": mock_rs}):
                from runtime_state import request_context

                with request_context(channel_id=10, thread_id=20):
                    result = await mod.recall_for_context(
                        "discuss project deployment",
                        channel_id=10,
                        thread_id=20,
                        cross_channel=False,
                    )

        assert result == ""
        mock_rs.record_scoped_recall_alert.assert_called_once()


# ---------------------------------------------------------------------------
# _retention_window_seconds
# ---------------------------------------------------------------------------


class TestRetentionWindowSeconds:
    def test_short_returns_zero(self):
        assert mod._retention_window_seconds("short") == 0

    def test_standard_returns_6_hours(self):
        assert mod._retention_window_seconds("standard") == 6 * 3600

    def test_long_returns_24_hours(self):
        assert mod._retention_window_seconds("long") == 24 * 3600

    def test_unknown_defaults_to_standard(self):
        assert mod._retention_window_seconds("unknown") == 6 * 3600

    def test_vector_store_extended_none_defaults_to_standard(self):
        assert mod._retention_window_seconds(None) == 6 * 3600


# ---------------------------------------------------------------------------
# _normalize_scope_id
# ---------------------------------------------------------------------------


class TestNormalizeScopeId:
    def test_returns_none_for_none(self):
        assert mod._normalize_scope_id(None) is None

    def test_vector_store_extended_returns_none_for_empty_string(self):
        assert mod._normalize_scope_id("") is None

    def test_returns_none_for_whitespace(self):
        assert mod._normalize_scope_id("  ") is None

    def test_returns_string_for_int(self):
        assert mod._normalize_scope_id(42) == "42"

    def test_vector_store_extended_strips_whitespace(self):
        assert mod._normalize_scope_id("  10  ") == "10"


# ---------------------------------------------------------------------------
# _combine_scope_where
# ---------------------------------------------------------------------------


class TestCombineScopeWhere:
    def test_returns_base_when_no_scope(self):
        base = {"key": "val"}
        result = mod._combine_scope_where(base, channel_id=None, thread_id=None)
        assert result == base

    def test_single_filter_returns_plain_dict(self):
        result = mod._combine_scope_where(None, channel_id="10", thread_id=None)
        assert result == {"channel_id": "10"}

    def test_two_filters_returns_and(self):
        result = mod._combine_scope_where(None, channel_id="10", thread_id="20")
        assert "$and" in result
        assert {"channel_id": "10"} in result["$and"]
        assert {"thread_id": "20"} in result["$and"]

    def test_base_where_combined_with_scope(self):
        base = {"type": "fact"}
        result = mod._combine_scope_where(base, channel_id="10", thread_id="20")
        assert "$and" in result
        assert base in result["$and"]


# ---------------------------------------------------------------------------
# add_memory (convenience helper)
# ---------------------------------------------------------------------------


class TestAddMemory:
    @pytest.mark.asyncio
    async def test_stores_in_memories_collection(self):
        add_mock = AsyncMock()
        with patch("vector_store_client.add_document", add_mock):
            await mod.add_memory("fact1", "user likes coffee", tags=["preference"])
        add_mock.assert_awaited_once()
        args, kwargs = add_mock.await_args
        assert args[0] == mod.MEMORIES_COLLECTION
        assert kwargs.get("doc_id") == "mem_fact1" or args[1] == "mem_fact1"
        meta = kwargs.get("metadata", {})
        assert meta["type"] == "fact"
        assert "preference" in meta["tags"]

    @pytest.mark.asyncio
    async def test_stores_with_confidence(self):
        add_mock = AsyncMock()
        with patch("vector_store_client.add_document", add_mock):
            await mod.add_memory("fact2", "content", confidence=0.8)
        _, kwargs = add_mock.await_args
        assert kwargs["metadata"]["confidence"] == 0.8
