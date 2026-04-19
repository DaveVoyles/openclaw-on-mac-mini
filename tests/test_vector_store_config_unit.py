"""Unit tests for vector_store_config.py — constants, recall-guard state, embedding function.

vector_store_pure.py already tests: _set_recall_guard_notes, consume_recall_guard_notes
via the old monolithic vector_store module.  This file tests the same functions
imported directly from the refactored vector_store_config module, plus constant
values, and the embedding function factory behaviour.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import vector_store_config as mod
from vector_store_config import (
    CONVERSATIONS_COLLECTION,
    DEFAULT_TOP_K,
    MEMORIES_COLLECTION,
    RESEARCH_COLLECTION,
    SIMILARITY_THRESHOLD,
    _set_recall_guard_notes,
    consume_recall_guard_notes,
)


class TestConstants:
    def test_default_top_k_is_5(self):
        assert DEFAULT_TOP_K == 5

    def test_vector_store_config_unit_similarity_threshold_in_range(self):
        assert 0.0 < SIMILARITY_THRESHOLD < 1.0

    def test_collection_names_are_strings(self):
        assert isinstance(MEMORIES_COLLECTION, str)
        assert isinstance(CONVERSATIONS_COLLECTION, str)
        assert isinstance(RESEARCH_COLLECTION, str)

    def test_collection_names_distinct(self):
        names = {MEMORIES_COLLECTION, CONVERSATIONS_COLLECTION, RESEARCH_COLLECTION}
        assert len(names) == 3


class TestRecallGuardNotes:
    def setup_method(self):
        _set_recall_guard_notes([])

    def test_consume_returns_empty_initially(self):
        _set_recall_guard_notes([])
        assert consume_recall_guard_notes() == []

    def test_set_then_consume_returns_notes(self):
        _set_recall_guard_notes(["note1", "note2"])
        notes = consume_recall_guard_notes()
        assert notes == ["note1", "note2"]

    def test_vector_store_config_unit_consume_clears_notes(self):
        _set_recall_guard_notes(["something"])
        consume_recall_guard_notes()
        assert consume_recall_guard_notes() == []

    def test_set_overwrites_previous(self):
        _set_recall_guard_notes(["old"])
        _set_recall_guard_notes(["new"])
        assert consume_recall_guard_notes() == ["new"]

    def test_consume_returns_copy_not_reference(self):
        _set_recall_guard_notes(["a", "b"])
        result = consume_recall_guard_notes()
        result.append("extra")
        assert consume_recall_guard_notes() == []


class TestGetEmbeddingFunction:
    def test_vector_store_config_unit_returns_none_when_no_model_set(self, monkeypatch):
        monkeypatch.setattr(mod, "EMBEDDING_MODEL", "")
        fn = mod._get_embedding_function()
        assert fn is None

    def test_handles_import_error_gracefully(self, monkeypatch):
        monkeypatch.setattr(mod, "EMBEDDING_MODEL", "some-model")
        with patch("vector_store_config.OllamaEmbeddingFunction", side_effect=ImportError, create=True):
            # Should not raise; falls back to None
            fn = mod._get_embedding_function()
        assert fn is None or callable(fn)

    def test_handles_exception_gracefully(self, monkeypatch):
        monkeypatch.setattr(mod, "EMBEDDING_MODEL", "bad-model")
        # Simulate OllamaEmbeddingFunction raising an exception; should return None
        fake_ef_mod = MagicMock()
        fake_ef_mod.OllamaEmbeddingFunction = MagicMock(side_effect=RuntimeError("connection refused"))
        with patch.dict("sys.modules", {"chromadb.utils.embedding_functions": fake_ef_mod}):
            fn = mod._get_embedding_function()
        assert fn is None
