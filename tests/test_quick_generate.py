"""Tests for llm_client.quick_generate() and the satellite module cleanups."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# quick_generate
# ---------------------------------------------------------------------------

class TestQuickGenerate:
    """Tests for llm_client.quick_generate."""

    @pytest.mark.asyncio
    async def test_returns_stripped_text_from_client(self):
        fake_response = MagicMock()
        fake_response.text = "  Extracted fact.  "
        fake_response.usage_metadata = None

        with (
            patch("llm_client._client") as mock_client,
            patch("llm_client._record_usage", AsyncMock()),
        ):
            mock_client.models.generate_content.return_value = fake_response
            from llm_client import quick_generate
            result = await quick_generate("test prompt")

        assert result == "Extracted fact."

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_client(self):
        with patch("llm_client._client", None):
            from llm_client import quick_generate
            result = await quick_generate("test prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_exception(self):
        with (
            patch("llm_client._client") as mock_client,
        ):
            mock_client.models.generate_content.side_effect = RuntimeError("API down")
            from llm_client import quick_generate
            result = await quick_generate("test prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_custom_max_tokens_and_temperature(self):
        fake_response = MagicMock()
        fake_response.text = "ok"
        fake_response.usage_metadata = None

        with (
            patch("llm_client._client") as mock_client,
            patch("llm_client._record_usage", AsyncMock()),
        ):
            mock_client.models.generate_content.return_value = fake_response
            from llm_client import quick_generate
            result = await quick_generate("hello", max_tokens=42, temperature=0.5)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_records_usage(self):
        fake_response = MagicMock()
        fake_response.text = "done"
        fake_response.usage_metadata = None

        with (
            patch("llm_client._client") as mock_client,
            patch("llm_client._record_usage", AsyncMock()) as mock_record,
        ):
            mock_client.models.generate_content.return_value = fake_response
            from llm_client import quick_generate
            await quick_generate("hello")

        mock_record.assert_awaited_once_with(fake_response)


# ---------------------------------------------------------------------------
# fact_extractor no longer creates its own genai.Client
# ---------------------------------------------------------------------------

class TestFactExtractorNoBareClient:
    def test_no_genai_client_in_source(self):
        import inspect

        import fact_extractor
        src = inspect.getsource(fact_extractor)
        assert "genai.Client(" not in src

    @pytest.mark.asyncio
    async def test_delegates_to_quick_generate(self):
        from fact_extractor import extract_and_store_facts

        with patch("llm_client.quick_generate", AsyncMock(return_value="NONE")):
            result = await extract_and_store_facts(
                "hi there how are you doing today?", "I'm good!", 42
            )
        assert result == []


# ---------------------------------------------------------------------------
# goal_tracker no longer creates its own genai.Client
# ---------------------------------------------------------------------------

class TestGoalTrackerNoBareClient:
    def test_no_genai_client_in_source(self):
        import inspect

        import goal_tracker
        src = inspect.getsource(goal_tracker)
        assert "genai.Client(" not in src

    @pytest.mark.asyncio
    async def test_delegates_to_quick_generate_none(self):
        from goal_tracker import extract_and_store_goal

        with patch("llm_client.quick_generate", AsyncMock(return_value="NONE")):
            result = await extract_and_store_goal("I'm planning to build a robot.", 99)
        assert result is None


# ---------------------------------------------------------------------------
# error_tracker no longer creates its own genai.Client
# ---------------------------------------------------------------------------

class TestErrorTrackerNoBareClient:
    def test_no_genai_client_in_source(self):
        import inspect

        import error_tracker
        src = inspect.getsource(error_tracker)
        assert "genai.Client(" not in src

    @pytest.mark.asyncio
    async def test_returns_default_when_quick_generate_empty(self):
        from error_tracker import diagnose_error_pattern

        with patch("llm_client.quick_generate", AsyncMock(return_value="")):
            result = await diagnose_error_pattern(
                [{"severity": "high", "type": "crash", "detail": "OOM"}]
            )
        assert result["fix_type"] == "manual_required"

