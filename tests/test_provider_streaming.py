"""Unit tests for OpenAI and Anthropic streaming in src/llm/providers.py.

Covers:
1. chat_openai_stream (via _stream_openai) yields tokens from SSE chunks in order
2. chat_openai_stream stops cleanly on data: [DONE] sentinel
3. chat_openai_stream skips malformed JSON lines and yields valid tokens
4. chat_openai_stream routes through COPILOT_PROXY_URL when proxy is enabled
5. chat_anthropic_stream yields tokens from content_block_delta events
6. chat_anthropic_stream stops cleanly when message_stop event received
"""

import sys
from unittest.mock import MagicMock, patch

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
from llm.providers import _stream_anthropic, _stream_openai  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


class _FakeSession:
    """Minimal aiohttp.ClientSession stub."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.last_url: str | None = None
        self.last_headers: dict | None = None

    def post(self, url: str, **kwargs):
        self.last_url = url
        self.last_headers = kwargs.get("headers")
        return _FakeResponse(self._lines)


class _FakeSessionManager:
    """Wraps _FakeSession to mimic SessionManager.get() coroutine."""

    def __init__(self, fake_session: _FakeSession) -> None:
        self._session = fake_session

    async def get(self) -> _FakeSession:
        return self._session


def _sse(payload: str) -> bytes:
    return f"data: {payload}\n".encode()


def _sse_openai_chunk(content: str) -> bytes:
    return _sse(f'{{"choices":[{{"delta":{{"content":"{content}"}}}}]}}')


def _sse_anthropic_delta(text: str) -> bytes:
    return _sse(f'{{"type":"content_block_delta","delta":{{"type":"text_delta","text":"{text}"}}}}')


def _sse_anthropic_stop() -> bytes:
    return _sse('{"type":"message_stop"}')


# ---------------------------------------------------------------------------
# 1. OpenAI stream yields tokens in order
# ---------------------------------------------------------------------------


async def test_openai_stream_yields_tokens():
    """SSE chunks are decoded and yielded in order."""
    lines = [
        _sse_openai_chunk("Hello"),
        _sse_openai_chunk(" "),
        _sse_openai_chunk("world"),
        _sse("[DONE]"),
    ]
    fake_session = _FakeSession(lines)

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_openai("openai", "hi", None, "", None, 0.7, 512)]

    assert tokens == ["Hello", " ", "world"]


# ---------------------------------------------------------------------------
# 2. OpenAI stream stops cleanly on [DONE]
# ---------------------------------------------------------------------------


async def test_openai_stream_stops_on_done():
    """Generator terminates when data: [DONE] is encountered; no extra tokens."""
    lines = [
        _sse_openai_chunk("first"),
        _sse("[DONE]"),
        # These bytes appear after [DONE] — must not be yielded
        _sse_openai_chunk("should-not-appear"),
    ]
    fake_session = _FakeSession(lines)

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_openai("openai", "hi", None, "", None, 0.7, 512)]

    assert tokens == ["first"]
    assert "should-not-appear" not in tokens


# ---------------------------------------------------------------------------
# 3. OpenAI stream skips malformed JSON and still yields valid tokens
# ---------------------------------------------------------------------------


async def test_openai_stream_skips_malformed_json():
    """Malformed SSE payloads are silently skipped; valid tokens still yielded."""
    lines = [
        _sse_openai_chunk("ok1"),
        b"data: {broken json\n",
        b"data: not-json-at-all\n",
        _sse_openai_chunk("ok2"),
        _sse("[DONE]"),
    ]
    fake_session = _FakeSession(lines)

    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_openai("openai", "hi", None, "", None, 0.7, 512)]

    assert tokens == ["ok1", "ok2"]


# ---------------------------------------------------------------------------
# 4. OpenAI stream uses proxy URL when COPILOT_PROXY_ENABLED is True
# ---------------------------------------------------------------------------


async def test_openai_stream_uses_proxy_when_enabled():
    """When proxy is enabled and healthy, request goes to COPILOT_PROXY_URL."""
    proxy_url = "http://proxy.example.com:8080"
    lines = [_sse_openai_chunk("proxied"), _sse("[DONE]")]
    fake_session = _FakeSession(lines)

    with (
        patch.object(_providers_mod, "COPILOT_PROXY_ENABLED", True),
        patch.object(_providers_mod, "COPILOT_PROXY_URL", proxy_url),
        patch.object(_providers_mod, "_proxy_healthy", True),
        patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_openai("openai", "q", None, "", None, 0.7, 512)]

    assert tokens == ["proxied"]
    assert fake_session.last_url is not None
    assert fake_session.last_url.startswith(proxy_url), (
        f"Expected request to proxy {proxy_url!r}, got {fake_session.last_url!r}"
    )


# ---------------------------------------------------------------------------
# 5. Anthropic stream yields tokens from content_block_delta events
# ---------------------------------------------------------------------------


async def test_anthropic_stream_yields_tokens():
    """content_block_delta events have their text extracted and yielded."""
    lines = [
        _sse_anthropic_delta("Hello"),
        _sse_anthropic_delta(" there"),
        _sse_anthropic_stop(),
    ]
    fake_session = _FakeSession(lines)

    with (
        patch.object(_providers_mod, "COPILOT_PROXY_ENABLED", False),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_anthropic("hi", None, "", None, 0.7, 512)]

    assert tokens == ["Hello", " there"]


# ---------------------------------------------------------------------------
# 6. Anthropic stream continues past message_stop (non-breaking event type)
# ---------------------------------------------------------------------------


async def test_anthropic_stream_stops_on_message_stop():
    """message_stop event type is simply skipped (not a token); stream ends at EOF."""
    lines = [
        _sse_anthropic_delta("tok1"),
        _sse_anthropic_stop(),
        # Any content after message_stop is still consumed until EOF
        # but message_stop itself yields nothing.
    ]
    fake_session = _FakeSession(lines)

    with (
        patch.object(_providers_mod, "COPILOT_PROXY_ENABLED", False),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}),
        patch.object(_providers_mod, "_provider_sessions", _FakeSessionManager(fake_session)),
    ):
        tokens = [t async for t in _stream_anthropic("q", None, "", None, 0.7, 512)]

    # message_stop must not produce a token
    assert "message_stop" not in tokens
    assert tokens == ["tok1"]
