"""Integration tests for the provider failover chain in src/llm/providers.py.

Covers:
1. Success on first try — no fallback attempted
2. Fallback triggered when primary returns None
3. All providers fail — null ProviderResponse returned without raising
4. Warning logged when fallback is triggered
5. Concurrent call_provider() calls return their own ProviderResponse (no cross-contamination)
"""

import asyncio
import logging
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports (same pattern as other test files)
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

import llm.providers as _providers_mod  # noqa: E402
from llm.providers import (  # noqa: E402
    ProviderResponse,
    call_provider,
    reset_circuit,
)

# ---------------------------------------------------------------------------
# Autouse fixture: clean circuit state and reset cumulative tokens each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Reset circuit breakers and cumulative token counters before each test."""
    reset_circuit()
    monkeypatch.setitem(_providers_mod._cumulative_tokens, "input", 0)
    monkeypatch.setitem(_providers_mod._cumulative_tokens, "output", 0)
    yield
    reset_circuit()


# ---------------------------------------------------------------------------
# 1. Success on first try
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_provider_success_first_try(monkeypatch):
    """call_provider returns the ProviderResponse from _call_one without fallback."""
    expected = ProviderResponse(
        text="hello from copilot",
        provider="copilot",
        model="gpt-4o",
        latency_ms=12.0,
        input_tokens=10,
        output_tokens=5,
    )
    call_log: list[str] = []

    async def _mock_call_one(provider, *args, **kwargs):
        call_log.append(provider)
        if provider == "copilot":
            return expected
        return None

    monkeypatch.setattr(_providers_mod, "_call_one", _mock_call_one)
    # Use a chain that only has copilot so no fallback is reachable
    monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", ["copilot"])

    result = await call_provider("copilot", "test msg", [], "sys")

    assert result.text == "hello from copilot"
    assert result.provider == "copilot"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    # _call_one should only have been called once (the primary)
    assert call_log == ["copilot"]


# ---------------------------------------------------------------------------
# 2. Fallback triggered when primary returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_provider_falls_back_on_none(monkeypatch):
    """call_provider tries the next provider when the primary returns None."""
    ollama_resp = ProviderResponse(
        text="from ollama",
        provider="ollama",
        model="gemma3:4b",
        latency_ms=55.0,
        input_tokens=8,
        output_tokens=3,
    )

    async def _mock_call_one(provider, *args, **kwargs):
        if provider == "copilot":
            return None
        if provider == "ollama":
            return ollama_resp
        return None

    monkeypatch.setattr(_providers_mod, "_call_one", _mock_call_one)
    monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", ["copilot", "ollama"])

    result = await call_provider("copilot", "test", [], "sys")

    assert result.text == "from ollama"
    assert result.provider == "ollama"


# ---------------------------------------------------------------------------
# 3. All providers fail — null ProviderResponse returned, no exception raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_provider_all_fail_returns_null_response(monkeypatch):
    """call_provider returns ProviderResponse(text=None) when every provider fails."""

    async def _mock_call_one(provider, *args, **kwargs):
        return None

    monkeypatch.setattr(_providers_mod, "_call_one", _mock_call_one)
    monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", ["copilot", "ollama"])

    result = await call_provider("copilot", "test", [], "sys")

    assert isinstance(result, ProviderResponse)
    assert result.text is None
    assert result.provider == "copilot"


# ---------------------------------------------------------------------------
# 4. Warning logged when fallback is triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_provider_fallback_logs_warning(monkeypatch, caplog):
    """A warning is logged when the primary provider fails and fallback activates."""
    fallback_resp = ProviderResponse(
        text="fallback text",
        provider="ollama",
        model="gemma3:4b",
        latency_ms=30.0,
    )

    async def _mock_call_one(provider, *args, **kwargs):
        if provider == "copilot":
            return None
        return fallback_resp

    monkeypatch.setattr(_providers_mod, "_call_one", _mock_call_one)
    monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", ["copilot", "ollama"])

    with caplog.at_level(logging.WARNING, logger="openclaw.llm.providers"):
        await call_provider("copilot", "test", [], "sys")

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "copilot" in msg and ("fallback" in msg.lower() or "None" in msg)
        for msg in warning_messages
    ), f"Expected fallback warning, got: {warning_messages}"


# ---------------------------------------------------------------------------
# 5. Concurrent calls — each coroutine gets its own ProviderResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_isolation(monkeypatch):
    """Two concurrent call_provider() calls return correct per-call ProviderResponses."""
    resp_a = ProviderResponse(
        text="response A",
        provider="copilot",
        model="gpt-4o",
        latency_ms=10.0,
        input_tokens=100,
        output_tokens=50,
    )
    resp_b = ProviderResponse(
        text="response B",
        provider="anthropic",
        model="claude-sonnet-4.5",
        latency_ms=20.0,
        input_tokens=200,
        output_tokens=80,
    )

    async def _mock_call_one(provider, *args, **kwargs):
        await asyncio.sleep(0.01)  # Allow the event loop to interleave coroutines
        if provider == "copilot":
            return resp_a
        if provider == "anthropic":
            return resp_b
        return None

    monkeypatch.setattr(_providers_mod, "_call_one", _mock_call_one)
    # Each provider is its own chain entry; no cross-contamination via fallback
    monkeypatch.setattr(_providers_mod, "PROVIDER_FALLBACK_CHAIN", [])

    result_a, result_b = await asyncio.gather(
        call_provider("copilot", "msg A", [], "sys"),
        call_provider("anthropic", "msg B", [], "sys"),
    )

    assert result_a.text == "response A"
    assert result_a.provider == "copilot"
    assert result_a.input_tokens == 100
    assert result_a.output_tokens == 50

    assert result_b.text == "response B"
    assert result_b.provider == "anthropic"
    assert result_b.input_tokens == 200
    assert result_b.output_tokens == 80
