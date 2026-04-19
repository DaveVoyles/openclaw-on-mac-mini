"""Unit tests for vector_store.py — re-export hub and backward-compat surface.

Focuses on gaps not already covered by test_vector_store_pure.py and
test_vector_store_*.py siblings. Validates that all documented public names
are importable from the top-level hub and that the re-exported pure helpers
behave correctly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export surface — all documented names must be importable
# ---------------------------------------------------------------------------


class TestVectorStoreReexports:
    """Verify the hub re-exports everything in its module-level __all__-equivalent."""

    def test_client_exports(self):
        from vector_store import (
            add_document,
            delete_document,
            get_stats,
            search,
            search_all,
            search_safe,
        )

        for sym in [add_document, delete_document, get_stats, search, search_all, search_safe]:
            assert callable(sym) or sym is not None

    def test_compaction_exports(self):
        from vector_store import (
            bump_access,
            get_decayed_documents,
            mark_decayed,
        )

        for sym in [bump_access, get_decayed_documents, mark_decayed]:
            assert callable(sym)

    def test_config_constant_exports(self):
        from vector_store import (
            CONVERSATIONS_COLLECTION,
            DEFAULT_TOP_K,
            MEMORIES_COLLECTION,
            RESEARCH_COLLECTION,
            SIMILARITY_THRESHOLD,
        )

        assert isinstance(CONVERSATIONS_COLLECTION, str)
        assert isinstance(MEMORIES_COLLECTION, str)
        assert isinstance(RESEARCH_COLLECTION, str)
        assert isinstance(DEFAULT_TOP_K, int)
        assert DEFAULT_TOP_K > 0
        assert isinstance(SIMILARITY_THRESHOLD, float)
        assert 0.0 <= SIMILARITY_THRESHOLD <= 1.0

    def test_config_function_exports(self):
        from vector_store import (
            _get_embedding_function,
            consume_recall_guard_notes,
        )

        assert callable(_get_embedding_function)
        assert callable(consume_recall_guard_notes)

    def test_memory_exports(self):
        from vector_store import (
            add_conversation_summary,
            add_memory,
            add_memory_deduped,
            add_research_report,
            clear_scoped_memory,
            get_scoped_memory_summary,
            recall,
            recall_for_context,
        )

        for sym in [
            add_conversation_summary,
            add_memory,
            add_memory_deduped,
            add_research_report,
            clear_scoped_memory,
            get_scoped_memory_summary,
            recall,
            recall_for_context,
        ]:
            assert callable(sym)

    def test_scope_exports(self):
        from vector_store import (
            _allow_fallback_result,
            _combine_scope_where,
            _extract_explicit_recall_domains,
            _infer_recall_domains,
            _inject_scope_metadata,
            _is_legacy_metadata,
            _normalize_scope_id,
            _resolve_scope,
        )

        for sym in [
            _allow_fallback_result,
            _combine_scope_where,
            _extract_explicit_recall_domains,
            _infer_recall_domains,
            _inject_scope_metadata,
            _is_legacy_metadata,
            _normalize_scope_id,
            _resolve_scope,
        ]:
            assert callable(sym)

    def test_recall_guard_exports(self):
        from vector_store import (
            _RECALL_DOMAIN_TERMS,
            _RECALL_GUARD_MIN_SIMILARITY,
            _set_recall_guard_notes,
        )

        assert callable(_set_recall_guard_notes)
        assert isinstance(_RECALL_DOMAIN_TERMS, (set, frozenset, list, tuple, dict))
        assert isinstance(_RECALL_GUARD_MIN_SIMILARITY, float)

    def test_time_module_exposed_for_patching(self):
        """vector_store.time must be importable so existing tests can patch it."""
        import vector_store

        assert hasattr(vector_store, "time")


# ---------------------------------------------------------------------------
# Compaction helpers — pure function behaviour
# ---------------------------------------------------------------------------


class TestCompactionHelpers:
    def test_retention_window_seconds_positive(self):
        from vector_store import _retention_window_seconds

        result = _retention_window_seconds("user-123")
        assert result > 0

    def test_retention_window_seconds_returns_int_or_float(self):
        from vector_store import _retention_window_seconds

        result = _retention_window_seconds("channel-xyz")
        assert isinstance(result, (int, float))

    def test_compaction_priority_returns_tuple(self):
        from vector_store import _compaction_priority

        result = _compaction_priority("doc-id-1", {"scope_id": "scope-1"})
        assert isinstance(result, tuple)

    def test_compaction_priority_different_docs(self):
        from vector_store import _compaction_priority

        # Just verify both calls return tuples without error
        r1 = _compaction_priority("doc-small", {"scope_id": "s", "access_count": 1})
        r2 = _compaction_priority("doc-large", {"scope_id": "s", "access_count": 100})
        assert isinstance(r1, tuple)
        assert isinstance(r2, tuple)


# ---------------------------------------------------------------------------
# Scope helpers — pure function behaviour (gaps beyond test_vector_store_pure)
# ---------------------------------------------------------------------------


class TestScopeHelpers:
    def test_normalize_scope_id_strips_whitespace(self):
        from vector_store import _normalize_scope_id

        result = _normalize_scope_id("  user-123  ")
        assert result == result.strip()

    def test_normalize_scope_id_returns_string(self):
        from vector_store import _normalize_scope_id

        result = _normalize_scope_id("guild-42")
        assert isinstance(result, str)

    def test_is_legacy_metadata_true_when_no_channel_or_thread(self):
        from vector_store import _is_legacy_metadata

        # Legacy = no channel_id and no thread_id in metadata
        meta = {"user_id": "u1"}
        assert _is_legacy_metadata(meta) is True

    def test_is_legacy_metadata_false_when_channel_id_present(self):
        from vector_store import _is_legacy_metadata

        meta = {"channel_id": "c1"}
        assert _is_legacy_metadata(meta) is False

    def test_is_legacy_metadata_false_when_thread_id_present(self):
        from vector_store import _is_legacy_metadata

        meta = {"thread_id": "t1"}
        assert _is_legacy_metadata(meta) is False

    def test_combine_scope_where_returns_dict_or_none(self):
        from vector_store import _combine_scope_where

        result = _combine_scope_where({"scope_id": "u1"}, channel_id="c1", thread_id=None)
        assert result is None or isinstance(result, dict)

    def test_combine_scope_where_none_base(self):
        from vector_store import _combine_scope_where

        result = _combine_scope_where(None, channel_id="c1", thread_id=None)
        assert result is None or isinstance(result, dict)
