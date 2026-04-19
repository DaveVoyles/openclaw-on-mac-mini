"""Unit tests for memory_session.py — session summaries and handover."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import memory_session as ms_module
from memory_session import (
    _load_last_summary,
    _summarize_and_store,
    _summary_path,
    create_session_handover,
    load_last_handover,
)

# ---------------------------------------------------------------------------
# _summary_path
# ---------------------------------------------------------------------------

class TestSummaryPath:
    def test_returns_path_under_summaries_dir(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        path = _summary_path(42)
        assert "42" in path.name
        assert path.suffix == ".json"

    def test_creates_summaries_dir(self, tmp_path, monkeypatch):
        summaries = tmp_path / "new_summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        _summary_path(1)
        assert summaries.exists()


# ---------------------------------------------------------------------------
# _load_last_summary
# ---------------------------------------------------------------------------

class TestLoadLastSummary:
    def test_memory_session_unit_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        assert _load_last_summary(99) == ""

    def test_returns_summary_from_file(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        payload = {"summary": "Discussed deployment pipeline"}
        (summaries / "1_last_session.json").write_text(json.dumps(payload))
        result = _load_last_summary(1)
        assert result == "Discussed deployment pipeline"

    def test_memory_session_unit_returns_empty_on_corrupt_file(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        (summaries / "1_last_session.json").write_text("{invalid json")
        assert _load_last_summary(1) == ""

    def test_returns_empty_when_summary_key_missing(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        summaries.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        (summaries / "1_last_session.json").write_text(json.dumps({"other": "data"}))
        assert _load_last_summary(1) == ""


# ---------------------------------------------------------------------------
# _summarize_and_store
# ---------------------------------------------------------------------------

class TestSummarizeAndStore:
    @pytest.mark.asyncio
    async def test_empty_summary_from_llm_writes_nothing(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        llm_mock = MagicMock()
        llm_mock.summarize_conversation = AsyncMock(return_value="")
        with patch("memory_session._atomic_write") as mock_aw, \
             patch.dict("sys.modules", {"llm": llm_mock}):
            await _summarize_and_store(1, "Alice", [{"role": "user", "parts": ["hi"]}])
        mock_aw.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_summary_is_written(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        llm_mock = MagicMock()
        llm_mock.summarize_conversation = AsyncMock(return_value="Session recap here")
        qmd_mock = MagicMock()
        qmd_mock.remember_fact = AsyncMock()
        vs_mock = MagicMock()
        vs_mock.add_conversation_summary = AsyncMock()
        with patch("memory_session._atomic_write") as mock_aw, \
             patch.dict("sys.modules", {"llm": llm_mock, "qmd": qmd_mock, "vector_store": vs_mock}):
            await _summarize_and_store(1, "Alice", [{"role": "user", "parts": ["hi"]}])
        mock_aw.assert_called_once()
        written_data = json.loads(mock_aw.call_args[0][1])
        assert written_data["summary"] == "Session recap here"
        assert written_data["user_id"] == 1

    @pytest.mark.asyncio
    async def test_qmd_failure_does_not_raise(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        llm_mock = MagicMock()
        llm_mock.summarize_conversation = AsyncMock(return_value="Summary")
        qmd_mock = MagicMock()
        qmd_mock.remember_fact = AsyncMock(side_effect=RuntimeError("qmd fail"))
        vs_mock = MagicMock()
        vs_mock.add_conversation_summary = AsyncMock()
        with patch("memory_session._atomic_write"), \
             patch.dict("sys.modules", {"llm": llm_mock, "qmd": qmd_mock, "vector_store": vs_mock}):
            await _summarize_and_store(1, "Alice", [{"role": "user", "parts": ["hi"]}])

    @pytest.mark.asyncio
    async def test_llm_failure_does_not_raise(self, tmp_path, monkeypatch):
        summaries = tmp_path / "summaries"
        monkeypatch.setattr(ms_module, "SUMMARIES_DIR", summaries)
        llm_mock = MagicMock()
        llm_mock.summarize_conversation = AsyncMock(side_effect=RuntimeError("llm down"))
        with patch.dict("sys.modules", {"llm": llm_mock}):
            await _summarize_and_store(1, "Alice", [{"role": "user", "parts": ["hi"]}])


# ---------------------------------------------------------------------------
# create_session_handover
# ---------------------------------------------------------------------------

class TestCreateSessionHandover:
    @pytest.mark.asyncio
    async def test_returns_none_when_history_too_short(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", tmp_path / "handovers")
        result = await create_session_handover(1, "Alice", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_llm_says_no_handover(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", tmp_path / "handovers")
        llm_mock = MagicMock()
        llm_mock.chat = AsyncMock(return_value=("No handover needed.", [], "test"))
        history = [{"role": "user", "parts": ["hi"]}] * 5
        with patch.dict("sys.modules", {"llm": llm_mock}):
            result = await create_session_handover(1, "Alice", history)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_handover_is_returned_and_saved(self, tmp_path, monkeypatch):
        handover_dir = tmp_path / "handovers"
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", handover_dir)
        llm_mock = MagicMock()
        llm_mock.chat = AsyncMock(return_value=("Decision: deploy tomorrow.", [], "test"))
        vs_mock = MagicMock()
        vs_mock.CONVERSATIONS_COLLECTION = "conversations"
        vs_mock.add_document = AsyncMock()
        history = [{"role": "user", "parts": ["hi"]}] * 5
        with patch("memory_session._atomic_write") as mock_aw, \
             patch.dict("sys.modules", {"llm": llm_mock, "vector_store": vs_mock}):
            result = await create_session_handover(1, "Alice", history)
        assert result == "Decision: deploy tomorrow."
        mock_aw.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", tmp_path / "handovers")
        llm_mock = MagicMock()
        llm_mock.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        history = [{"role": "user", "parts": ["hi"]}] * 5
        with patch.dict("sys.modules", {"llm": llm_mock}):
            result = await create_session_handover(1, "Alice", history)
        assert result is None


# ---------------------------------------------------------------------------
# load_last_handover
# ---------------------------------------------------------------------------

class TestLoadLastHandover:
    def test_memory_session_unit_returns_empty_when_no_file_v2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", tmp_path / "handovers")
        assert load_last_handover(99) == ""

    def test_returns_handover_from_file(self, tmp_path, monkeypatch):
        handover_dir = tmp_path / "handovers"
        handover_dir.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", handover_dir)
        payload = {"handover": "Next: deploy the fix"}
        (handover_dir / "1_last_handover.json").write_text(json.dumps(payload))
        assert load_last_handover(1) == "Next: deploy the fix"

    def test_memory_session_unit_returns_empty_on_corrupt_file_v2(self, tmp_path, monkeypatch):
        handover_dir = tmp_path / "handovers"
        handover_dir.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", handover_dir)
        (handover_dir / "1_last_handover.json").write_text("{bad json")
        assert load_last_handover(1) == ""

    def test_returns_empty_when_handover_key_missing(self, tmp_path, monkeypatch):
        handover_dir = tmp_path / "handovers"
        handover_dir.mkdir(parents=True)
        monkeypatch.setattr(ms_module, "HANDOVER_DIR", handover_dir)
        (handover_dir / "1_last_handover.json").write_text(json.dumps({"other": "key"}))
        assert load_last_handover(1) == ""
