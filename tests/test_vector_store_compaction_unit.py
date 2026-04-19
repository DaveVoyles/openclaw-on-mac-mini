"""Unit tests for vector_store_compaction.py — priority, retention, decay, access.

test_vector_store_pure.py already covers _compaction_priority and
_retention_window_seconds via the old monolithic vector_store module.
This file imports those functions from the refactored module directly
and adds coverage for get_decayed_documents, mark_decayed, and bump_access.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from vector_store_compaction import (
    _compaction_priority,
    _retention_window_seconds,
    bump_access,
    get_decayed_documents,
    mark_decayed,
)


class TestCompactionPriority:
    def test_higher_access_count_ranks_later(self):
        low = _compaction_priority("a", {"access_count": 1})
        high = _compaction_priority("b", {"access_count": 10})
        assert low < high

    def test_vector_store_compaction_unit_empty_meta_does_not_raise(self):
        result = _compaction_priority("doc1", {})
        assert isinstance(result, tuple)

    def test_none_values_coerced(self):
        result = _compaction_priority("doc", {"access_count": None, "last_accessed": None})
        assert isinstance(result, tuple)

    def test_tuple_first_element_is_access_count(self):
        result = _compaction_priority("doc", {"access_count": 7})
        assert result[0] == 7

    def test_doc_id_is_last_element_for_stable_sort(self):
        result = _compaction_priority("zz", {"access_count": 0})
        assert result[-1] == "zz"


class TestRetentionWindowSeconds:
    def test_short_is_zero(self):
        assert _retention_window_seconds("short") == 0

    def test_standard_is_six_hours(self):
        assert _retention_window_seconds("standard") == 6 * 3600

    def test_long_is_24_hours(self):
        assert _retention_window_seconds("long") == 24 * 3600

    def test_vector_store_compaction_unit_none_defaults_to_standard(self):
        assert _retention_window_seconds(None) == _retention_window_seconds("standard")

    def test_unknown_class_defaults_to_standard(self):
        assert _retention_window_seconds("unknown") == _retention_window_seconds("standard")

    def test_vector_store_compaction_unit_case_insensitive(self):
        assert _retention_window_seconds("SHORT") == 0
        assert _retention_window_seconds("Long") == 24 * 3600


class TestGetDecayedDocuments:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_collection_empty(self):
        mock_col = MagicMock()
        mock_col.count.return_value = 0

        # _get_collection is lazily imported from vector_store_client inside the function
        with patch("vector_store_client._get_collection", return_value=mock_col):
            result = await get_decayed_documents("memories")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_only_decayed_docs(self):
        import time as _time
        old_time = _time.time() - (31 * 86400)  # older than 30 days

        mock_col = MagicMock()
        mock_col.count.return_value = 2
        mock_col.get.return_value = {
            "ids": ["doc1", "doc2"],
            "metadatas": [
                {"last_accessed": old_time, "access_count": 0},
                {"last_accessed": _time.time(), "access_count": 5},
            ],
            "documents": ["text1", "text2"],
        }

        with patch("vector_store_client._get_collection", return_value=mock_col):
            result = await get_decayed_documents("memories")

        # Only doc1 should be in the result (old + low access_count)
        assert len(result) == 1
        assert result[0]["id"] == "doc1"

    @pytest.mark.asyncio
    async def test_empty_results_handled(self):
        mock_col = MagicMock()
        mock_col.count.return_value = 1
        mock_col.get.return_value = {"ids": [], "metadatas": []}

        with patch("vector_store_client._get_collection", return_value=mock_col):
            result = await get_decayed_documents("memories")

        assert result == []


class TestMarkDecayed:
    @pytest.mark.asyncio
    async def test_vector_store_compaction_unit_returns_zero_for_empty_list(self):
        result = await mark_decayed("memories", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_update_on_collection(self):
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["doc1"],
            "metadatas": [{"type": "memory"}],
        }

        with patch("vector_store_client._get_collection", return_value=mock_col):
            count = await mark_decayed("memories", ["doc1"])

        mock_col.update.assert_called_once()
        assert count == 1

    @pytest.mark.asyncio
    async def test_returns_count_of_marked_docs(self):
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["a", "b"],
            "metadatas": [{}, {}],
        }

        with patch("vector_store_client._get_collection", return_value=mock_col):
            count = await mark_decayed("memories", ["a", "b"])

        assert count == 2


class TestBumpAccess:
    @pytest.mark.asyncio
    async def test_noop_for_empty_ids(self):
        mock_col = MagicMock()
        with patch("vector_store_client._get_collection", return_value=mock_col):
            await bump_access("memories", [])
        mock_col.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_access_count_and_timestamp(self):
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["doc1"],
            "metadatas": [{"access_count": 3, "last_accessed": 0.0}],
        }

        with patch("vector_store_client._get_collection", return_value=mock_col):
            await bump_access("memories", ["doc1"])

        mock_col.update.assert_called_once()
        updated_meta = mock_col.update.call_args[1]["metadatas"][0]
        assert updated_meta["access_count"] == 4
        assert updated_meta["last_accessed"] > 0
