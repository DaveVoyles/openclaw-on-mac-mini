"""Unit tests for vector_store_scope.py — scope resolution and metadata helpers.

test_vector_store_pure.py already covers: _extract_explicit_recall_domains,
_infer_recall_domains, _normalize_scope_id, _combine_scope_where,
_is_legacy_metadata, _allow_fallback_result via the monolithic vector_store module.

This file tests the same functions imported from the refactored module directly,
plus _inject_scope_metadata and _resolve_scope edge cases.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from vector_store_scope import (
    _allow_fallback_result,
    _combine_scope_where,
    _extract_explicit_recall_domains,
    _infer_recall_domains,
    _inject_scope_metadata,
    _is_legacy_metadata,
    _normalize_scope_id,
    _resolve_scope,
)


class TestExtractExplicitRecallDomains:
    def test_vector_store_scope_unit_empty_string_returns_empty(self):
        assert _extract_explicit_recall_domains("") == set()

    def test_vector_store_scope_unit_none_handled(self):
        assert _extract_explicit_recall_domains(None) == set()

    def test_sports_directive(self):
        result = _extract_explicit_recall_domains("use: sports scores today")
        assert "sports" in result

    def test_wwe_directive(self):
        result = _extract_explicit_recall_domains("use: wwe results")
        assert "wwe" in result


class TestInferRecallDomains:
    def test_vector_store_scope_unit_empty_string(self):
        assert _infer_recall_domains("") == set()

    def test_vector_store_scope_unit_none_handled_v2(self):
        assert _infer_recall_domains(None) == set()

    def test_wwe_inferred_from_single_term(self):
        result = _infer_recall_domains("What happened at WrestleMania?")
        assert "wwe" in result

    def test_sports_inferred_from_multiple_terms(self):
        result = _infer_recall_domains("NBA game scores and team standings")
        assert "sports" in result


class TestNormalizeScopeId:
    def test_vector_store_scope_unit_none_returns_none(self):
        assert _normalize_scope_id(None) is None

    def test_int_returns_string(self):
        assert _normalize_scope_id(42) == "42"

    def test_vector_store_scope_unit_empty_string_returns_none(self):
        assert _normalize_scope_id("") is None

    def test_whitespace_returns_none(self):
        assert _normalize_scope_id("   ") is None

    def test_padded_string_stripped(self):
        assert _normalize_scope_id("  hello  ") == "hello"


class TestCombineScopeWhere:
    def test_vector_store_scope_unit_no_scope_no_base_returns_none(self):
        result = _combine_scope_where(None, channel_id=None, thread_id=None)
        assert result is None

    def test_channel_only_filter(self):
        result = _combine_scope_where(None, channel_id="100", thread_id=None)
        assert result == {"channel_id": "100"}

    def test_channel_and_thread_returns_and_clause(self):
        result = _combine_scope_where(None, channel_id="100", thread_id="200")
        assert result is not None
        assert "$and" in result

    def test_vector_store_scope_unit_base_and_channel_combined(self):
        base = {"type": "memory"}
        result = _combine_scope_where(base, channel_id="100", thread_id=None)
        assert result is not None
        assert "$and" in result


class TestInjectScopeMetadata:
    def test_adds_channel_id_when_missing(self):
        meta = _inject_scope_metadata({}, channel_id="99", thread_id=None)
        assert meta.get("channel_id") == "99"

    def test_does_not_overwrite_existing_channel_id(self):
        meta = _inject_scope_metadata({"channel_id": "existing"}, channel_id="99", thread_id=None)
        assert meta["channel_id"] == "existing"

    def test_adds_thread_id_when_missing(self):
        meta = _inject_scope_metadata({}, channel_id="99", thread_id="55")
        assert meta.get("thread_id") == "55"

    def test_empty_metadata_base(self):
        meta = _inject_scope_metadata(None, channel_id="10", thread_id="20")
        assert meta["channel_id"] == "10"
        assert meta["thread_id"] == "20"

    def test_no_scope_metadata_unchanged(self):
        with (
            patch("runtime_state.get_current_channel_id", return_value=None),
            patch("runtime_state.get_current_thread_id", return_value=None),
        ):
            meta = _inject_scope_metadata({"key": "val"})
        assert meta == {"key": "val"}


class TestIsLegacyMetadata:
    def test_empty_dict_is_legacy(self):
        assert _is_legacy_metadata({}) is True

    def test_with_channel_id_not_legacy(self):
        assert _is_legacy_metadata({"channel_id": "123"}) is False

    def test_empty_string_channel_id_is_legacy(self):
        assert _is_legacy_metadata({"channel_id": ""}) is True


class TestAllowFallbackResult:
    def test_matching_channel_no_thread_allowed(self):
        meta = {"channel_id": "100"}
        assert _allow_fallback_result(meta, channel_id="100", thread_id=None) is True

    def test_different_channel_blocked(self):
        meta = {"channel_id": "999"}
        assert _allow_fallback_result(meta, channel_id="100", thread_id=None) is False

    def test_matching_channel_and_thread_allowed(self):
        meta = {"channel_id": "100", "thread_id": "50"}
        assert _allow_fallback_result(meta, channel_id="100", thread_id="50") is True

    def test_vector_store_scope_unit_wrong_thread_blocked(self):
        meta = {"channel_id": "100", "thread_id": "99"}
        assert _allow_fallback_result(meta, channel_id="100", thread_id="50") is False


class TestResolveScope:
    def test_explicit_channel_and_thread_returned_directly(self):
        ch, th = _resolve_scope(channel_id="123", thread_id="456")
        assert ch == "123"
        assert th == "456"

    def test_none_values_trigger_runtime_state_lookup(self):
        with (
            patch("runtime_state.get_current_channel_id", return_value=99),
            patch("runtime_state.get_current_thread_id", return_value=None),
        ):
            ch, th = _resolve_scope(channel_id=None, thread_id=None)
        assert ch == "99"
        assert th is None
