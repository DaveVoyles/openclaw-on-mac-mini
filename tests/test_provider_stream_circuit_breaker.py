"""
Unit tests for the circuit breaker in call_provider_stream() (src/llm/providers.py).

Covers:
1. Open circuit yields nothing — circuit open → empty chunk list
2. Failure recording — _stream_openai raises → _is_open eventually True
3. Success resets — clean stream → _record_success clears failure count
4. Half-open after timeout — monkeypatched time past _CB_TIMEOUT → closed
5. Per-provider isolation — openai open doesn't affect anthropic stream
6. Chunks yielded on success — mock yields 3 chunks → all 3 received
"""

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports (same pattern as existing CB tests)
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
    call_provider_stream,
    reset_circuit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect(provider: str, **kwargs) -> list[str]:
    """Drain call_provider_stream into a list."""
    chunks: list[str] = []
    async for chunk in call_provider_stream(provider, "hi", **kwargs):
        chunks.append(chunk)
    return chunks


async def mock_stream_ok(*args, **kwargs):
    """Async generator that yields three chunks successfully."""
    for chunk in ["hello", " ", "world"]:
        yield chunk


async def mock_stream_raise(*args, **kwargs):
    """Async generator that raises on first iteration."""
    raise RuntimeError("simulated stream error")
    yield  # make it a generator


# ---------------------------------------------------------------------------
# Autouse fixture: isolate every test with a clean circuit state
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_circuit():
    reset_circuit()
    yield
    reset_circuit()


# ---------------------------------------------------------------------------
# 1. Open circuit yields nothing
# ---------------------------------------------------------------------------
class TestOpenCircuitYieldsNothing:
    @pytest.mark.asyncio
    async def test_open_circuit_returns_empty(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        # Open the circuit manually
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        chunks = await _collect("openai")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_underlying_fn_not_called_when_open(self, monkeypatch):
        import llm.providers as _p

        called = []

        async def spy_stream(*args, **kwargs):
            called.append(True)
            for chunk in ["x"]:
                yield chunk

        monkeypatch.setattr(_p, "_stream_openai", spy_stream)

        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        await _collect("openai")
        assert called == [], "Underlying stream fn should not be called when circuit is open"


# ---------------------------------------------------------------------------
# 2. Failure recording — exceptions open the circuit after N calls
# ---------------------------------------------------------------------------
class TestFailureRecording:
    @pytest.mark.asyncio
    async def test_exception_records_failure(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_raise)

        assert _is_open("openai") is False
        # One failure shouldn't open the circuit (threshold > 1)
        await _collect("openai")
        if _CB_THRESHOLD > 1:
            assert _is_open("openai") is False

    @pytest.mark.asyncio
    async def test_n_exceptions_open_circuit(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_raise)

        for _ in range(_CB_THRESHOLD):
            await _collect("openai")

        assert _is_open("openai") is True

    @pytest.mark.asyncio
    async def test_circuit_open_after_threshold_yields_nothing(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_raise)

        for _ in range(_CB_THRESHOLD):
            await _collect("openai")

        assert _is_open("openai") is True

        # Subsequent call with a working stream should still yield nothing
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)
        chunks = await _collect("openai")
        assert chunks == []


# ---------------------------------------------------------------------------
# 3. Success resets — clean stream clears failure count
# ---------------------------------------------------------------------------
class TestSuccessResets:
    @pytest.mark.asyncio
    async def test_clean_stream_records_success(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        # Accumulate failures just below threshold
        for _ in range(_CB_THRESHOLD - 1):
            _record_failure("openai")

        # Successful stream should reset
        await _collect("openai")
        assert _is_open("openai") is False

        # One more failure after reset shouldn't open the circuit
        _record_failure("openai")
        assert _is_open("openai") is False

    @pytest.mark.asyncio
    async def test_success_does_not_open_circuit(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        await _collect("openai")
        assert _is_open("openai") is False

    @pytest.mark.asyncio
    async def test_success_after_failures_clears_count(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_raise)

        # Accumulate failures but stay below threshold
        for _ in range(_CB_THRESHOLD - 1):
            await _collect("openai")

        # Switch to a good stream
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)
        await _collect("openai")

        # Should now tolerate _CB_THRESHOLD - 1 more failures without opening
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_raise)
        for _ in range(_CB_THRESHOLD - 1):
            await _collect("openai")
        assert _is_open("openai") is False


# ---------------------------------------------------------------------------
# 4. Half-open after timeout
# ---------------------------------------------------------------------------
class TestHalfOpenAfterTimeout:
    @pytest.mark.asyncio
    async def test_circuit_closes_after_timeout(self, monkeypatch):
        import llm.providers as _providers_mod

        # Open the circuit
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        # Advance monotonic clock past CB_TIMEOUT
        original = _providers_mod._time.monotonic
        monkeypatch.setattr(
            _providers_mod._time,
            "monotonic",
            lambda: original() + _CB_TIMEOUT + 1.0,
        )
        assert _is_open("openai") is False

    @pytest.mark.asyncio
    async def test_stream_flows_after_timeout(self, monkeypatch):
        import llm.providers as _providers_mod

        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        original = _providers_mod._time.monotonic
        monkeypatch.setattr(
            _providers_mod._time,
            "monotonic",
            lambda: original() + _CB_TIMEOUT + 1.0,
        )
        monkeypatch.setattr(_providers_mod, "_stream_openai", mock_stream_ok)

        chunks = await _collect("openai")
        assert chunks == ["hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_circuit_still_open_before_timeout(self, monkeypatch):
        import llm.providers as _providers_mod

        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")

        original = _providers_mod._time.monotonic
        monkeypatch.setattr(
            _providers_mod._time,
            "monotonic",
            lambda: original() + _CB_TIMEOUT - 1.0,
        )
        assert _is_open("openai") is True


# ---------------------------------------------------------------------------
# 5. Per-provider isolation
# ---------------------------------------------------------------------------
class TestPerProviderIsolation:
    @pytest.mark.asyncio
    async def test_openai_open_does_not_affect_anthropic(self, monkeypatch):
        import llm.providers as _p

        async def mock_stream_anthropic(*args, **kwargs):
            for chunk in ["a", "b", "c"]:
                yield chunk

        monkeypatch.setattr(_p, "_stream_anthropic", mock_stream_anthropic)

        # Open openai circuit
        for _ in range(_CB_THRESHOLD):
            _record_failure("openai")
        assert _is_open("openai") is True

        # Anthropic stream should still work
        chunks = await _collect("anthropic")
        assert chunks == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_anthropic_open_does_not_affect_openai(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        for _ in range(_CB_THRESHOLD):
            _record_failure("anthropic")
        assert _is_open("anthropic") is True

        chunks = await _collect("openai")
        assert chunks == ["hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_providers_track_failures_independently(self, monkeypatch):
        import llm.providers as _p

        async def raise_stream(*args, **kwargs):
            raise RuntimeError("err")
            yield

        monkeypatch.setattr(_p, "_stream_openai", raise_stream)

        # Fail openai to threshold
        for _ in range(_CB_THRESHOLD):
            await _collect("openai")

        # anthropic should remain closed
        assert _is_open("openai") is True
        assert _is_open("anthropic") is False


# ---------------------------------------------------------------------------
# 6. Chunks yielded on success
# ---------------------------------------------------------------------------
class TestChunksYieldedOnSuccess:
    @pytest.mark.asyncio
    async def test_all_three_chunks_received(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        chunks = await _collect("openai")
        assert chunks == ["hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_chunk_count_matches(self, monkeypatch):
        import llm.providers as _p
        monkeypatch.setattr(_p, "_stream_openai", mock_stream_ok)

        chunks = await _collect("openai")
        assert len(chunks) == 3

    @pytest.mark.asyncio
    async def test_chunk_content_preserved(self, monkeypatch):
        import llm.providers as _p

        async def custom_stream(*args, **kwargs):
            for chunk in ["foo", "bar", "baz", "qux"]:
                yield chunk

        monkeypatch.setattr(_p, "_stream_openai", custom_stream)

        chunks = await _collect("openai")
        assert "".join(chunks) == "foobarbazqux"

    @pytest.mark.asyncio
    async def test_empty_stream_records_success_not_failure(self, monkeypatch):
        import llm.providers as _p

        async def empty_stream(*args, **kwargs):
            return
            yield  # noqa: unreachable — makes it a generator

        monkeypatch.setattr(_p, "_stream_openai", empty_stream)

        chunks = await _collect("openai")
        assert chunks == []
        # Circuit should remain closed after a clean (empty) stream
        assert _is_open("openai") is False
