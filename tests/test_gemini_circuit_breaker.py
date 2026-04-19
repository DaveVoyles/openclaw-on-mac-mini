"""
Tests for the Gemini circuit breaker wired into src/llm/chat.py.

Covers:
1. Circuit stays closed on success (record_success resets counter)
2. Circuit opens after max_failures consecutive failures
3. When circuit is open, _gemini_chat raises immediately (no API call made)
4. Circuit resets (half-open) after cooldown expires
"""

import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports (same pattern as test_llm_chat.py)
# ---------------------------------------------------------------------------
_genai_mock = MagicMock()
_genai_mock.types.ThinkingConfig = MagicMock()
_genai_mock.types.ContentDict = dict
_genai_mock.types.GenerateContentConfig = MagicMock()
_genai_mock.types.Tool = MagicMock()
_genai_mock.types.FunctionDeclaration = MagicMock()
_genai_mock.types.Schema = MagicMock()
_genai_mock.types.Type = MagicMock()
_genai_mock.types.Part = MagicMock()
_genai_mock.types.FunctionResponse = MagicMock()
_genai_mock.types.Content = MagicMock()
_genai_mock.types.Blob = MagicMock()
_genai_mock.Client = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)


from tool_health import CircuitBreaker  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Circuit stays closed on success
# ---------------------------------------------------------------------------
class TestCircuitStaysClosedOnSuccess:
    def test_repeated_successes_keep_circuit_closed(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=60)
        for _ in range(10):
            cb.record_success("gemini")
        assert not cb.is_open("gemini")

    def test_failure_then_success_resets_counter(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=60)
        cb.record_failure("gemini")
        cb.record_failure("gemini")
        cb.record_success("gemini")  # resets counter
        cb.record_failure("gemini")  # only 1 failure now
        cb.record_failure("gemini")  # 2 failures — still under threshold
        assert not cb.is_open("gemini")


# ---------------------------------------------------------------------------
# 2. Circuit opens after N consecutive failures
# ---------------------------------------------------------------------------
class TestCircuitOpensAfterNFailures:
    def test_opens_exactly_at_threshold(self):
        cb = CircuitBreaker(max_failures=5, cooldown_seconds=60)
        for i in range(4):
            cb.record_failure("gemini")
            assert not cb.is_open("gemini"), f"Should be closed after {i + 1} failures"
        cb.record_failure("gemini")
        assert cb.is_open("gemini")

    def test_status_reflects_open_state(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=60)
        for _ in range(3):
            cb.record_failure("gemini")
        status = cb.status()
        assert status["gemini"]["is_open"] is True
        assert status["gemini"]["failures"] == 3


# ---------------------------------------------------------------------------
# 3. _gemini_chat raises immediately when circuit is open (no API call)
# ---------------------------------------------------------------------------
class TestGeminiChatCircuitOpen:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_circuit_open(self):
        """When circuit is open, _gemini_chat raises RuntimeError immediately."""
        chat_mod = sys.modules["llm.chat"]

        mock_cb = MagicMock()
        mock_cb.is_open.return_value = True

        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.0-flash"
        mock_model.config = MagicMock()

        mock_rate_limiter = AsyncMock()
        mock_rate_limiter.wait_for_capacity = AsyncMock(return_value=True)

        with (
            patch.object(chat_mod, "_gemini_circuit", mock_cb),
            patch.object(chat_mod, "_rate_limiter", mock_rate_limiter),
        ):
            with pytest.raises(RuntimeError, match="circuit breaker is open"):
                await chat_mod._gemini_chat("hello", [], mock_model)

        # Rate limiter must NOT be consulted when circuit is open
        mock_rate_limiter.wait_for_capacity.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_api_call_when_circuit_open(self):
        """Gemini client send_message is never invoked when circuit is open."""
        chat_mod = sys.modules["llm.chat"]

        mock_cb = MagicMock()
        mock_cb.is_open.return_value = True

        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.0-flash"
        mock_model.config = MagicMock()

        mock_client = MagicMock()

        with patch.object(chat_mod, "_gemini_circuit", mock_cb), patch.object(chat_mod, "_client", mock_client):
            with pytest.raises(RuntimeError):
                await chat_mod._gemini_chat("hello", [], mock_model)

        mock_client.chats.create.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Circuit resets (half-open) after cooldown expires
# ---------------------------------------------------------------------------
class TestCircuitResetsAfterCooldown:
    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=0.05)
        for _ in range(3):
            cb.record_failure("gemini")
        assert cb.is_open("gemini")

        time.sleep(0.1)
        # After cooldown, circuit goes half-open (allows one retry)
        assert not cb.is_open("gemini")

    def test_recording_success_after_half_open_closes_circuit(self):
        cb = CircuitBreaker(max_failures=2, cooldown_seconds=0.05)
        cb.record_failure("gemini")
        cb.record_failure("gemini")
        assert cb.is_open("gemini")

        time.sleep(0.1)
        assert not cb.is_open("gemini")  # half-open

        cb.record_success("gemini")
        assert not cb.is_open("gemini")  # fully closed
