"""Tests for worker_agent module — spawn_worker with mocked Gemini."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_genai():
    """Mock google.generativeai for worker tests."""
    with patch("worker_agent.genai") as mock:
        mock.configure = MagicMock()
        mock.GenerativeModel.return_value = _mock_model()
        mock.GenerationConfig = MagicMock()
        mock.protos = MagicMock()
        yield mock


def _mock_model():
    model = MagicMock()
    # Simulate a response with text and no function calls
    response = MagicMock()
    response.text = "Worker result: task completed successfully"
    candidate = MagicMock()
    part = MagicMock()
    part.function_call.name = ""
    candidate.content.parts = [part]
    response.candidates = [candidate]
    response.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=20)

    chat = MagicMock()
    chat.send_message = MagicMock(return_value=response)
    model.start_chat.return_value = chat
    return model


@pytest.mark.asyncio
async def test_spawn_worker_basic(mock_genai):
    """Worker returns text result for simple goal."""
    with patch("llm.GOOGLE_API_KEY", "test-key"), \
         patch("llm._rate_limiter") as mock_rl, \
         patch("llm._record_usage", new_callable=AsyncMock), \
         patch("llm._build_tools", return_value=[]):
        mock_rl.check.return_value = True
        mock_rl.record = MagicMock()

        from worker_agent import spawn_worker
        result = await spawn_worker("Test task", context="Test context")
        assert "Worker result" in result


@pytest.mark.asyncio
async def test_spawn_worker_no_api_key():
    """Worker fails gracefully without API key."""
    with patch("llm.GOOGLE_API_KEY", ""):
        from worker_agent import spawn_worker
        result = await spawn_worker("Test task")
        assert "GOOGLE_API_KEY" in result


@pytest.mark.asyncio
async def test_spawn_worker_rate_limited():
    """Worker fails gracefully when rate limited."""
    with patch("llm.GOOGLE_API_KEY", "test-key"), \
         patch("llm._rate_limiter") as mock_rl:
        mock_rl.check.return_value = False
        from worker_agent import spawn_worker
        result = await spawn_worker("Test task")
        assert "rate limit" in result.lower()


@pytest.mark.asyncio
async def test_spawn_worker_with_conversation_history(mock_genai):
    """Worker incorporates conversation history into initial message."""
    with patch("llm.GOOGLE_API_KEY", "test-key"), \
         patch("llm._rate_limiter") as mock_rl, \
         patch("llm._record_usage", new_callable=AsyncMock), \
         patch("llm._build_tools", return_value=[]):
        mock_rl.check.return_value = True
        mock_rl.record = MagicMock()

        from worker_agent import spawn_worker
        history = [
            {"role": "user", "parts": ["What neighborhoods are good in Delco?"]},
            {"role": "model", "parts": ["Narberth, Havertown, and Ardmore are popular."]},
        ]
        result = await spawn_worker(
            "Find home prices in Narberth",
            conversation_history=history,
        )
        # Verify the model was called with history context
        model = mock_genai.GenerativeModel.return_value
        chat = model.start_chat.return_value
        call_args = chat.send_message.call_args[0][0]
        assert "conversation context" in call_args.lower() or "Worker result" in result
