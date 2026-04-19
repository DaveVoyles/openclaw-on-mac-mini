"""Tests for model_routing_policy.select_summarization_route and its wiring."""

from unittest.mock import AsyncMock, patch

import pytest

from model_routing_policy import SummarizationRouteDecision, select_summarization_route

# ---------------------------------------------------------------------------
# select_summarization_route policy
# ---------------------------------------------------------------------------


class TestSelectSummarizationRoute:
    def test_summarization_route_prefers_copilot_when_available(self):
        result = select_summarization_route(copilot_available=True)
        assert isinstance(result, SummarizationRouteDecision)
        assert result.provider == "copilot"
        assert "Gemini quota" in result.reason

    def test_falls_back_to_gemini_when_copilot_unavailable(self):
        result = select_summarization_route(copilot_available=False)
        assert result.provider == "gemini"
        assert "unavailable" in result.reason.lower()

    def test_summarization_route_returns_frozen_dataclass(self):
        result = select_summarization_route(copilot_available=True)
        with pytest.raises((AttributeError, TypeError)):
            result.provider = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# summarize_conversation wiring (llm/chat.py)
# ---------------------------------------------------------------------------


class TestSummarizeConversationRouting:
    _history = [
        {"role": "user", "parts": ["What's the weather like?"]},
        {"role": "model", "parts": ["It's sunny today."]},
    ]

    @pytest.mark.asyncio
    async def test_uses_copilot_when_available(self):
        from llm.chat import summarize_conversation

        with (
            patch("llm.chat.COPILOT_PROXY_ENABLED", True, create=True),
            patch("model_routing_policy.select_summarization_route") as mock_route,
            patch("llm.providers.chat_openai", AsyncMock(return_value="  Copilot summary.  ")),
        ):
            from model_routing_policy import SummarizationRouteDecision

            mock_route.return_value = SummarizationRouteDecision(provider="copilot", reason="test")
            result = await summarize_conversation(self._history)

        assert result == "Copilot summary."

    @pytest.mark.asyncio
    async def test_summarization_route_falls_back_to_gemini_when_copilot_returns_none(self):
        from unittest.mock import MagicMock

        from llm.chat import summarize_conversation

        fake_response = MagicMock()
        fake_response.text = "Gemini summary."

        with (
            patch("model_routing_policy.select_summarization_route") as mock_route,
            patch("llm.providers.chat_openai", AsyncMock(return_value=None)),
            patch("llm.chat._client") as mock_client,
            patch("llm.chat.asyncio") as mock_asyncio,
        ):
            mock_asyncio.to_thread = AsyncMock(return_value=fake_response)
            from model_routing_policy import SummarizationRouteDecision

            mock_route.return_value = SummarizationRouteDecision(provider="copilot", reason="test")
            result = await summarize_conversation(self._history)

        assert "Gemini summary" in result

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_history(self):
        from llm.chat import summarize_conversation

        result = await summarize_conversation([])
        assert result == ""
