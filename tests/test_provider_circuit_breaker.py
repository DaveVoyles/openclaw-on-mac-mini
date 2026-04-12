"""
Unit tests for the half-open circuit breaker in src/llm/providers.py.

Covers:
1. Fresh state — _is_open returns False initially
2. Failure accumulation — N-1 failures keep circuit closed; N failures open it
3. Timeout expiry — after _CB_TIMEOUT passes, _is_open returns False (half-open)
4. Success resets — _record_success clears accumulated failures
5. call_provider() circuit guard — underlying chat fn NOT called when circuit is open
6. reset_circuit(provider) — closes a specific provider's circuit
7. reset_circuit(None) — closes all circuits
8. Per-provider isolation — one provider's circuit doesn't affect another
"""

import sys
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

from llm.providers import (  # noqa: E402
    _CB_THRESHOLD,
    _CB_TIMEOUT,
    _is_open,
    _record_failure,
    _record_success,
    call_provider,
    reset_circuit,
    ProviderResponse,
)


# ---------------------------------------------------------------------------
# Autouse fixture: isolate every test with a clean circuit state
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_circuit():
    """Reset all circuit state before each test."""
    reset_circuit()
    yield
    reset_circuit()


# ---------------------------------------------------------------------------
# 1. Fresh state
# ---------------------------------------------------------------------------
class TestFreshState:
    def test_is_open_returns_false_initially(self):
        assert _is_open("openai") is False

    def test_is_open_returns_false_for_unknown_provider(self):
        assert _is_open("nonexistent_provider") is False


# ---------------------------------------------------------------------------
# 2. Failure accumulation
# ---------------------------------------------------------------------------
class TestFailureAccumulation:
    def test_circuit_stays_closed_before_threshold(self):
        for i in range(_CB_THRESHOLD - 1):
            _record_failure("openai")
            assert _is_open("openai") is False, (
                f"Circuit should be closed after {i + 1} failure(s)"
            )

    def test_circuit_opens_at_threshold(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

    def test_circuit_stays_open_after_threshold(self):
        for _ in range(_CB_THRESHOLD + 5):
            _record_failure("openai")
        assert _is_open("openai") is True


# ---------------------------------------------------------------------------
# 3. Timeout expiry (half-open)
# ---------------------------------------------------------------------------
class TestTimeoutExpiry:
    def test_circuit_closes_after_timeout(self, monkeypatch):
        # Open the circuit
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        # Advance time past CB_TIMEOUT
        import llm.providers as _providers_mod

        original_monotonic = _providers_mod._time.monotonic
        monkeypatch.setattr(
            _providers_mod._time,
            "monotonic",
            lambda: original_monotonic() + _CB_TIMEOUT + 1.0,
        )
        assert _is_open("openai") is False

    def test_circuit_still_open_just_before_timeout(self, monkeypatch):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        import llm.providers as _providers_mod

        original_monotonic = _providers_mod._time.monotonic
        monkeypatch.setattr(
            _providers_mod._time,
            "monotonic",
            lambda: original_monotonic() + _CB_TIMEOUT - 0.5,
        )
        assert _is_open("openai") is True


# ---------------------------------------------------------------------------
# 4. Success resets
# ---------------------------------------------------------------------------
class TestSuccessResets:
    def test_success_clears_failures(self):
        for _ in range(_CB_THRESHOLD - 1):
            _record_failure("openai")
        _record_success("openai")
        # After success, one more failure should not open the circuit
        _record_failure("openai")
        assert _is_open("openai") is False

    def test_success_closes_open_circuit(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True
        _record_success("openai")
        assert _is_open("openai") is False

    def test_success_on_fresh_provider_is_safe(self):
        _record_success("openai")
        assert _is_open("openai") is False


# ---------------------------------------------------------------------------
# 5. call_provider() circuit guard
# ---------------------------------------------------------------------------
class TestCallProviderCircuitGuard:
    @pytest.mark.asyncio
    async def test_call_provider_skips_when_circuit_open(self, monkeypatch):
        # Open the openai circuit
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        mock_chat = AsyncMock(return_value="should not be called")

        import llm.providers as _providers_mod

        monkeypatch.setattr(_providers_mod, "chat_openai", mock_chat)
        # Disable the fallback chain so no other provider is attempted
        monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", [])

        resp = await call_provider(
            "openai", "hello", [], "you are helpful"
        )

        mock_chat.assert_not_called()
        assert resp.text is None
        assert resp.provider == "openai"

    @pytest.mark.asyncio
    async def test_call_provider_returns_none_text_when_open(self, monkeypatch):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        mock_chat = AsyncMock(return_value="response text")

        import llm.providers as _providers_mod

        monkeypatch.setattr(_providers_mod, "chat_openai", mock_chat)
        # Disable the fallback chain so no other provider is attempted
        monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", [])

        resp = await call_provider("openai", "hi", [], "sys")

        assert isinstance(resp, ProviderResponse)
        assert resp.text is None

    @pytest.mark.asyncio
    async def test_call_provider_calls_fn_when_circuit_closed(self, monkeypatch):
        mock_chat = AsyncMock(return_value="hello world")

        import llm.providers as _providers_mod

        monkeypatch.setattr(_providers_mod, "chat_openai", mock_chat)

        resp = await call_provider("openai", "hi", [], "sys")

        mock_chat.assert_called_once()
        assert resp.text == "hello world"


# ---------------------------------------------------------------------------
# 6. reset_circuit(provider) — closes a specific circuit
# ---------------------------------------------------------------------------
class TestResetCircuitSpecific:
    def test_reset_closes_open_circuit(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        reset_circuit("openai")
        assert _is_open("openai") is False

    def test_reset_is_safe_on_closed_circuit(self):
        reset_circuit("openai")
        assert _is_open("openai") is False

    def test_reset_specific_leaves_other_providers_intact(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
            _record_failure("anthropic")

        reset_circuit("openai")
        assert _is_open("openai") is False
        assert _is_open("anthropic") is True


# ---------------------------------------------------------------------------
# 7. reset_circuit(None) — closes all circuits
# ---------------------------------------------------------------------------
class TestResetCircuitAll:
    def test_reset_none_clears_all_circuits(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
            _record_failure("anthropic")

        assert _is_open("openai") is True
        assert _is_open("anthropic") is True

        reset_circuit(None)

        assert _is_open("openai") is False
        assert _is_open("anthropic") is False

    def test_reset_none_on_empty_state_is_safe(self):
        reset_circuit(None)
        assert _is_open("openai") is False


# ---------------------------------------------------------------------------
# 8. Per-provider isolation
# ---------------------------------------------------------------------------
class TestPerProviderIsolation:
    def test_openai_circuit_does_not_affect_anthropic(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        assert _is_open("openai") is True
        assert _is_open("anthropic") is False

    def test_anthropic_circuit_does_not_affect_openai(self):
        for _ in range(_CB_THRESHOLD):
            _record_failure("anthropic")

        assert _is_open("anthropic") is True
        assert _is_open("openai") is False

    def test_each_provider_tracks_failures_independently(self):
        for _ in range(_CB_THRESHOLD - 1):
            _record_failure("openai")
        for _ in range(_CB_THRESHOLD - 1):
            _record_failure("anthropic")

        # Neither should be open yet
        assert _is_open("openai") is False
        assert _is_open("anthropic") is False

        # Push only openai over the threshold
        _record_failure("openai")
        assert _is_open("openai") is True
        assert _is_open("anthropic") is False
