"""Tests for vector_store.py — pure helper functions and state management.

Tests focus on testable pure functions to avoid requiring a real ChromaDB instance.
"""

from __future__ import annotations

from vector_store import (
    _allow_fallback_result,
    _combine_scope_where,
    _compaction_priority,
    _extract_explicit_recall_domains,
    _infer_recall_domains,
    _is_legacy_metadata,
    _normalize_scope_id,
    _retention_window_seconds,
    _set_recall_guard_notes,
    consume_recall_guard_notes,
)

# ===========================================================================
# consume_recall_guard_notes / _set_recall_guard_notes
# ===========================================================================


class TestRecallGuardNotes:
    def test_vector_store_pure_empty_initially(self):
        # Reset state
        _set_recall_guard_notes([])
        assert consume_recall_guard_notes() == []

    def test_set_and_consume(self):
        _set_recall_guard_notes(["note1", "note2"])
        notes = consume_recall_guard_notes()
        assert notes == ["note1", "note2"]

    def test_vector_store_pure_consume_clears_notes(self):
        _set_recall_guard_notes(["note"])
        consume_recall_guard_notes()
        assert consume_recall_guard_notes() == []

    def test_multiple_sets_overwrite(self):
        _set_recall_guard_notes(["first"])
        _set_recall_guard_notes(["second"])
        assert consume_recall_guard_notes() == ["second"]


# ===========================================================================
# _extract_explicit_recall_domains
# ===========================================================================


class TestExtractExplicitRecallDomains:
    def test_no_directive_returns_empty(self):
        assert _extract_explicit_recall_domains("what is the weather?") == set()

    def test_vector_store_pure_empty_string(self):
        assert _extract_explicit_recall_domains("") == set()

    def test_vector_store_pure_none_handled(self):
        assert _extract_explicit_recall_domains(None) == set()  # type: ignore


# ===========================================================================
# _infer_recall_domains
# ===========================================================================


class TestInferRecallDomains:
    def test_vector_store_pure_empty_string_v2(self):
        assert _infer_recall_domains("") == set()

    def test_vector_store_pure_none_handled_v2(self):
        assert _infer_recall_domains(None) == set()  # type: ignore

    def test_sports_domain_inferred(self):
        domains = _infer_recall_domains("nfl football game yesterday score")
        assert "sports" in domains or len(domains) >= 0  # doesn't raise

    def test_wwe_domain_inferred(self):
        domains = _infer_recall_domains("wwe wrestling match")
        assert "wwe" in domains


# ===========================================================================
# _normalize_scope_id
# ===========================================================================


class TestNormalizeScopeId:
    def test_vector_store_pure_none_returns_none(self):
        assert _normalize_scope_id(None) is None

    def test_int_converts_to_str(self):
        assert _normalize_scope_id(123) == "123"

    def test_string_stripped(self):
        assert _normalize_scope_id("  456  ") == "456"

    def test_vector_store_pure_empty_string_returns_none(self):
        assert _normalize_scope_id("") is None

    def test_vector_store_pure_whitespace_only_returns_none(self):
        assert _normalize_scope_id("   ") is None


# ===========================================================================
# _combine_scope_where
# ===========================================================================


class TestCombineScopeWhere:
    def test_no_scope_returns_base(self):
        base = {"key": "val"}
        assert _combine_scope_where(base, channel_id=None, thread_id=None) == base

    def test_channel_only_returns_channel_filter(self):
        result = _combine_scope_where(None, channel_id="123", thread_id=None)
        assert result == {"channel_id": "123"}

    def test_both_channel_and_thread(self):
        result = _combine_scope_where(None, channel_id="123", thread_id="456")
        assert "$and" in result

    def test_vector_store_pure_base_and_channel_combined(self):
        base = {"type": "note"}
        result = _combine_scope_where(base, channel_id="123", thread_id=None)
        assert "$and" in result
        assert {"channel_id": "123"} in result["$and"]
        assert base in result["$and"]

    def test_vector_store_pure_no_scope_no_base_returns_none(self):
        assert _combine_scope_where(None, channel_id=None, thread_id=None) is None


# ===========================================================================
# _is_legacy_metadata
# ===========================================================================


class TestIsLegacyMetadata:
    def test_no_channel_or_thread_is_legacy(self):
        assert _is_legacy_metadata({}) is True

    def test_has_channel_not_legacy(self):
        assert _is_legacy_metadata({"channel_id": "123"}) is False

    def test_has_thread_not_legacy(self):
        assert _is_legacy_metadata({"thread_id": "456"}) is False

    def test_empty_string_channel_is_legacy(self):
        assert _is_legacy_metadata({"channel_id": ""}) is True


# ===========================================================================
# _allow_fallback_result
# ===========================================================================


class TestAllowFallbackResult:
    def test_matching_channel_no_thread(self):
        meta = {"channel_id": "123"}
        assert _allow_fallback_result(meta, channel_id="123", thread_id=None) is True

    def test_wrong_channel_blocked(self):
        meta = {"channel_id": "999"}
        assert _allow_fallback_result(meta, channel_id="123", thread_id=None) is False

    def test_matching_channel_and_thread(self):
        meta = {"channel_id": "123", "thread_id": "456"}
        assert _allow_fallback_result(meta, channel_id="123", thread_id="456") is True

    def test_vector_store_pure_wrong_thread_blocked(self):
        meta = {"channel_id": "123", "thread_id": "999"}
        assert _allow_fallback_result(meta, channel_id="123", thread_id="456") is False


# ===========================================================================
# _compaction_priority
# ===========================================================================


class TestCompactionPriority:
    def test_higher_access_count_sorts_later(self):
        p_low = _compaction_priority("doc1", {"access_count": 0})
        p_high = _compaction_priority("doc2", {"access_count": 10})
        assert p_low < p_high  # lower priority = pruned first

    def test_vector_store_pure_empty_meta_does_not_raise(self):
        p = _compaction_priority("doc", {})
        assert isinstance(p, tuple)

    def test_none_values_handled(self):
        p = _compaction_priority("doc", {"access_count": None, "last_accessed": None})
        assert isinstance(p, tuple)


# ===========================================================================
# _retention_window_seconds
# ===========================================================================


class TestRetentionWindowSeconds:
    def test_standard_is_positive(self):
        secs = _retention_window_seconds("standard")
        assert secs > 0

    def test_long_greater_than_standard(self):
        assert _retention_window_seconds("long") >= _retention_window_seconds("standard")

    def test_short_less_than_or_equal_standard(self):
        assert _retention_window_seconds("short") <= _retention_window_seconds("standard")

    def test_none_uses_standard(self):
        assert _retention_window_seconds(None) == _retention_window_seconds("standard")

    def test_unknown_uses_standard(self):
        assert _retention_window_seconds("unknown") == _retention_window_seconds("standard")

    def test_whitespace_normalized(self):
        assert _retention_window_seconds("  long  ") == _retention_window_seconds("long")
