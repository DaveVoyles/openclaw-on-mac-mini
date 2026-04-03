"""Tests for vector_store.py — search_safe wrapper and search logic."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import vector_store as mod


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
