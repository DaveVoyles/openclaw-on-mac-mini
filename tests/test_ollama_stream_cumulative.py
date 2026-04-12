"""Tests for call_provider_stream() cumulative token accumulation.

Covers:
1. _cumulative_tokens increases by prompt+eval counts after a completed stream
2. _tokens_by_provider["ollama"] is populated after streaming
3. _cumulative_tokens unchanged when stream raises an error
4. Two sequential streams accumulate correctly in _cumulative_tokens
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub google.genai before any llm imports
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
from llm.providers import call_provider_stream, reset_circuit  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_lines(*chunks: dict) -> list[bytes]:
    return [json.dumps(c).encode() + b"\n" for c in chunks]


class _FakeContent:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.content = _FakeContent(lines)

    def raise_for_status(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _FakePost:
    def __init__(self, lines: list[bytes]) -> None:
        self._resp = _FakeResponse(lines)

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *_):
        pass


class _FakeSession:
    def __init__(self, lines: list[bytes]) -> None:
        self._post = _FakePost(lines)

    def post(self, *args, **kwargs):
        return self._post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _ErrorContent:
    """Async iterator that raises after the first item."""

    def __init__(self, first_line: bytes) -> None:
        self._first = first_line
        self._raised = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._raised:
            self._raised = True
            return self._first
        raise RuntimeError("simulated stream error")


class _ErrorResponse:
    def __init__(self, first_line: bytes) -> None:
        self.content = _ErrorContent(first_line)

    def raise_for_status(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class _ErrorPost:
    def __init__(self, first_line: bytes) -> None:
        self._resp = _ErrorResponse(first_line)

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *_):
        pass


class _ErrorSession:
    def __init__(self, first_line: bytes) -> None:
        self._post = _ErrorPost(first_line)

    def post(self, *args, **kwargs):
        return self._post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Autouse fixture: clean state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state():
    reset_circuit()
    _providers_mod._last_usage.update(input_tokens=0, output_tokens=0)
    _providers_mod._cumulative_tokens.update(input=0, output=0)
    _providers_mod._tokens_by_provider.clear()
    yield
    reset_circuit()


# ---------------------------------------------------------------------------
# 1. _cumulative_tokens updated after stream completes
# ---------------------------------------------------------------------------


async def test_cumulative_tokens_updated_after_stream():
    """After streaming completes with done=true, _cumulative_tokens reflects the token counts."""
    chunks = [
        {"message": {"content": "hello"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
        },
    ]
    lines = _make_sse_lines(*chunks)

    import aiohttp

    before_input = _providers_mod._cumulative_tokens["input"]
    before_output = _providers_mod._cumulative_tokens["output"]

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        _ = [t async for t in call_provider_stream("ollama", "hi")]

    assert _providers_mod._cumulative_tokens["input"] == before_input + 10
    assert _providers_mod._cumulative_tokens["output"] == before_output + 5


# ---------------------------------------------------------------------------
# 2. _tokens_by_provider tracks per-provider counts
# ---------------------------------------------------------------------------


async def test_tokens_by_provider_tracks_per_provider():
    """_tokens_by_provider["ollama"] is populated with input/output token counts after stream."""
    chunks = [
        {"message": {"content": "word"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 7,
            "eval_count": 3,
        },
    ]
    lines = _make_sse_lines(*chunks)

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        _ = [t async for t in call_provider_stream("ollama", "test")]

    assert "ollama" in _providers_mod._tokens_by_provider
    assert _providers_mod._tokens_by_provider["ollama"]["input"] == 7
    assert _providers_mod._tokens_by_provider["ollama"]["output"] == 3


# ---------------------------------------------------------------------------
# 3. _cumulative_tokens unchanged on stream failure
# ---------------------------------------------------------------------------


async def test_cumulative_tokens_not_updated_on_failure():
    """When the stream raises mid-way, _cumulative_tokens stays at its pre-call value."""
    first_line = json.dumps({"message": {"content": "partial"}, "done": False}).encode() + b"\n"

    import aiohttp

    before_input = _providers_mod._cumulative_tokens["input"]
    before_output = _providers_mod._cumulative_tokens["output"]

    with patch.object(aiohttp, "ClientSession", return_value=_ErrorSession(first_line)):
        _ = [t async for t in call_provider_stream("ollama", "boom")]

    assert _providers_mod._cumulative_tokens["input"] == before_input
    assert _providers_mod._cumulative_tokens["output"] == before_output


# ---------------------------------------------------------------------------
# 4. Multiple sequential streams accumulate in _cumulative_tokens
# ---------------------------------------------------------------------------


async def test_multiple_streams_accumulate():
    """Two sequential streams each add their token counts; totals are the sum of both."""

    def _lines(prompt_tokens: int, eval_tokens: int) -> list[bytes]:
        return _make_sse_lines(
            {"message": {"content": "tok"}, "done": False},
            {
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": prompt_tokens,
                "eval_count": eval_tokens,
            },
        )

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(_lines(10, 5))):
        _ = [t async for t in call_provider_stream("ollama", "first")]

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(_lines(8, 4))):
        _ = [t async for t in call_provider_stream("ollama", "second")]

    assert _providers_mod._cumulative_tokens["input"] == 18
    assert _providers_mod._cumulative_tokens["output"] == 9
