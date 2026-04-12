"""Unit tests for Ollama streaming token capture in src/llm/providers.py.

Covers:
1. chat_ollama_stream yields all content tokens in order
2. chat_ollama_stream captures done-chunk token counts into _last_usage
3. chat_ollama_stream skips empty content strings
4. chat_ollama_stream recovers from malformed JSON lines
5. call_provider_stream("ollama") resets _last_usage before yielding chunks
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
from llm.providers import (  # noqa: E402
    call_provider_stream,
    chat_ollama_stream,
    reset_circuit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sse_lines(*chunks: dict) -> list[bytes]:
    """Encode a sequence of dicts as newline-delimited JSON bytes."""
    return [json.dumps(c).encode() + b"\n" for c in chunks]


class _FakeContent:
    """Async iterator over a list of byte lines."""

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
# 1. chat_ollama_stream yields all content tokens in order
# ---------------------------------------------------------------------------


async def test_ollama_stream_yields_tokens():
    """Multi-chunk SSE stream: all content tokens are yielded in order."""
    chunks = [
        {"message": {"content": "Hello"}, "done": False},
        {"message": {"content": " world"}, "done": False},
        {"message": {"content": "!"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 3},
    ]
    lines = _make_sse_lines(*chunks)

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        tokens = [t async for t in chat_ollama_stream("hi", [], "")]

    assert tokens == ["Hello", " world", "!"]


# ---------------------------------------------------------------------------
# 2. Final done chunk updates _last_usage
# ---------------------------------------------------------------------------


async def test_ollama_stream_captures_done_token_counts():
    """done=true chunk with prompt_eval_count/eval_count updates _last_usage."""
    chunks = [
        {"message": {"content": "tok"}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "prompt_eval_count": 10,
            "eval_count": 5,
        },
    ]
    lines = _make_sse_lines(*chunks)

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        _ = [t async for t in chat_ollama_stream("q", [], "")]

    assert _providers_mod._last_usage == {"input_tokens": 10, "output_tokens": 5, "retry_count": 0}


# ---------------------------------------------------------------------------
# 3. Empty content strings are not yielded
# ---------------------------------------------------------------------------


async def test_ollama_stream_handles_empty_chunks():
    """Chunks with empty content string must not appear in yielded tokens."""
    chunks = [
        {"message": {"content": ""}, "done": False},
        {"message": {"content": "real"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 1, "eval_count": 1},
    ]
    lines = _make_sse_lines(*chunks)

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        tokens = [t async for t in chat_ollama_stream("x", [], "")]

    assert tokens == ["real"]
    assert "" not in tokens


# ---------------------------------------------------------------------------
# 4. Malformed JSON lines are skipped; valid tokens still yielded
# ---------------------------------------------------------------------------


async def test_ollama_stream_handles_json_decode_error():
    """Malformed JSON mixed in does not raise; valid tokens are still yielded."""
    valid_chunk = {"message": {"content": "good"}, "done": False}
    done_chunk = {"message": {"content": ""}, "done": True, "prompt_eval_count": 2, "eval_count": 1}
    lines = [
        json.dumps(valid_chunk).encode() + b"\n",
        b"not-valid-json\n",
        b"{broken\n",
        json.dumps(done_chunk).encode() + b"\n",
    ]

    import aiohttp

    with patch.object(aiohttp, "ClientSession", return_value=_FakeSession(lines)):
        tokens = [t async for t in chat_ollama_stream("q", [], "")]

    assert tokens == ["good"]


# ---------------------------------------------------------------------------
# 5. call_provider_stream resets _last_usage before streaming begins
# ---------------------------------------------------------------------------


async def test_call_provider_stream_ollama_resets_last_usage():
    """_last_usage is zeroed at the start of call_provider_stream("ollama", ...)."""
    # Pre-populate with stale values to confirm reset
    _providers_mod._last_usage.update(input_tokens=99, output_tokens=88)

    observed: list[dict] = []

    async def _fake_stream(prompt, history, system, model):
        # Capture _last_usage at the moment the generator starts
        observed.append(dict(_providers_mod._last_usage))
        yield "tok"
        _providers_mod._last_usage.update(input_tokens=3, output_tokens=2)

    with patch.object(_providers_mod, "chat_ollama_stream", side_effect=_fake_stream):
        _ = [c async for c in call_provider_stream("ollama", "hello")]

    assert observed, "generator was never started"
    assert observed[0] == {"input_tokens": 0, "output_tokens": 0, "retry_count": 0}, (
        f"_last_usage was not reset before streaming; got {observed[0]}"
    )
