"""
test_chat_stream_routing_unit.py — Characterization tests for chat_stream() routing logic.

Tests pin the routing branch decisions as a safety net for the planned decomposition
refactor of the 515-line function.  All network / LLM provider calls are patched away.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub google.genai (and friends) *before* any llm.* import
# ---------------------------------------------------------------------------
_genai_mock = MagicMock()
_genai_mock.types.ThinkingConfig = MagicMock()
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

from llm.chat import chat_stream  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPLY = "Here is the answer."
_HISTORY: list[dict] = []


async def _collect(gen) -> list[tuple]:
    """Drain an async generator into a list."""
    chunks: list[tuple] = []
    async for item in gen:
        chunks.append(item)
    return chunks


def _make_trim_history_mock(reply_history=None):
    """_trim_history(history, model_hint, context_quality) → trimmed history."""

    async def _impl(history, *, model_hint="gemini", context_quality=None):
        return reply_history if reply_history is not None else history

    return _impl


def _make_recall_mock(recalled=None):
    """_auto_recall_context(...) → recalled_context string or None."""

    async def _impl(*args, **kwargs):
        return recalled

    return _impl


def _make_gemini_chat_mock(reply=_REPLY):
    """_gemini_chat(msg, history, model, ...) → (reply, updated_history, model_name)."""

    async def _impl(model_message, history, model, **kwargs):
        updated = list(history) + [
            {"role": "user", "parts": [model_message]},
            {"role": "model", "parts": [reply]},
        ]
        return reply, updated, "gemini-2.0-flash"

    return _impl


def _make_select_model_mock():
    """_select_model_for_message(...) → (mock_model, route_info)."""

    async def _impl(message, *, tool_declarations=None, label="LLM"):
        model = MagicMock()
        model.model_name = "gemini-2.0-flash"
        return model, {}

    return _impl


# Common patch targets
_TRIM = "llm.chat._trim_history"
_RECALL = "llm.chat._auto_recall_context"
_RATE = "llm.chat._rate_limiter"
_SEL = "llm.chat._select_model_for_message"
_GEMINI = "llm.chat._gemini_chat"
_LOCAL_LLM_ENABLED = "llm.chat.LOCAL_LLM_ENABLED"
_OLLAMA_AVAIL = "llm.chat._ollama_available"
_TRY_LOCAL = "llm.chat._try_local_model"


def _base_patches():
    """Return a dict of patch targets → mock values for the happy-path Gemini route."""
    rate_mock = MagicMock()
    rate_mock.check.return_value = True
    return {
        _TRIM: _make_trim_history_mock(),
        _RECALL: _make_recall_mock(None),
        _RATE: rate_mock,
        _SEL: _make_select_model_mock(),
        _GEMINI: _make_gemini_chat_mock(),
    }


# ---------------------------------------------------------------------------
# Class 1: Basic chunk-yielding behaviour
# ---------------------------------------------------------------------------


class TestChatStreamBasicYielding:
    """chat_stream() yields correct chunk structure on the Gemini fallback path."""

    @pytest.mark.asyncio
    async def test_yields_at_least_one_chunk(self):
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("What is 2 + 2?", history=[], model_preference="gemini"))
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_final_chunk_has_done_true(self):
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("Tell me a joke.", history=[], model_preference="gemini"))
        _, is_final, _ = chunks[-1]
        assert is_final is True

    @pytest.mark.asyncio
    async def test_chunk_tuple_has_three_elements(self):
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("Hello!", history=[], model_preference="gemini"))
        for chunk in chunks:
            assert len(chunk) == 3, f"Expected 3-tuple, got {chunk!r}"

    @pytest.mark.asyncio
    async def test_final_chunk_metadata_contains_model_used(self):
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("Hi", history=[], model_preference="gemini"))
        _, _, meta = chunks[-1]
        assert "model_used" in meta

    @pytest.mark.asyncio
    async def test_final_chunk_metadata_contains_updated_history(self):
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("Hi", history=[], model_preference="gemini"))
        _, _, meta = chunks[-1]
        assert "updated_history" in meta


# ---------------------------------------------------------------------------
# Class 2: Routing-path selection
# ---------------------------------------------------------------------------


class TestChatStreamRoutingPaths:
    """Each branch in chat_stream() is exercised independently."""

    @pytest.mark.asyncio
    async def test_web_search_route_taken_when_prefer_search(self):
        """model_preference='auto' + select_web_search_route returning prefer_search=True
        must yield with model_used='perplexity-direct' and not call _gemini_chat."""
        web_route = SimpleNamespace(prefer_search=True, reason="real-time query")

        mrp_mock = MagicMock()
        mrp_mock.select_web_search_route.return_value = web_route

        skills_mock = MagicMock()
        skills_mock.generate_web_search_report = AsyncMock(return_value="Web result text")

        patches = _base_patches()
        gemini_mock = AsyncMock()

        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=gemini_mock),
            patch.dict(
                sys.modules,
                {
                    "model_routing_policy": mrp_mock,
                    "skills.reporting_skills": skills_mock,
                },
            ),
        ):
            chunks = await _collect(chat_stream("Latest stock price?", history=[], model_preference="auto"))

        _, _, meta = chunks[-1]
        assert meta.get("model_used") == "perplexity-direct"
        gemini_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_search_route_skipped_when_prefer_search_false(self):
        """When select_web_search_route returns prefer_search=False, we fall through
        to the Gemini path, not the perplexity path."""
        web_route = SimpleNamespace(prefer_search=False, reason="simple query")
        coding_route = SimpleNamespace(matches=False, reason="not coding")

        mrp_mock = MagicMock()
        mrp_mock.select_web_search_route.return_value = web_route
        mrp_mock.select_coding_route.return_value = coding_route

        providers_mock = MagicMock()
        providers_mock.COPILOT_PROXY_ENABLED = False

        model_router_mock = MagicMock()
        model_router_mock.classify_query.return_value = SimpleNamespace(
            model_type="none", reason="no match", model=None
        )
        model_router_mock.is_ollama_alive = AsyncMock(return_value=False)

        patches = _base_patches()

        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
            patch.dict(
                sys.modules,
                {
                    "model_routing_policy": mrp_mock,
                    "llm.providers": providers_mock,
                    "model_router": model_router_mock,
                },
            ),
        ):
            chunks = await _collect(chat_stream("What is 2+2?", history=[], model_preference="auto"))

        _, _, meta = chunks[-1]
        assert meta.get("model_used") != "perplexity-direct"
        assert meta.get("model_used") == "gemini-2.0-flash"

    @pytest.mark.asyncio
    async def test_forced_openai_route_calls_chat_openai(self):
        """model_preference='openai' must call chat_openai, not _gemini_chat."""
        openai_reply = "OpenAI answer"

        providers_mock = MagicMock()
        providers_mock.chat_openai = AsyncMock(return_value=openai_reply)
        providers_mock.chat_anthropic = AsyncMock(return_value=None)

        patches = _base_patches()
        gemini_mock = AsyncMock()

        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=gemini_mock),
            patch.dict(sys.modules, {"llm.providers": providers_mock}),
        ):
            chunks = await _collect(chat_stream("Hi", history=[], model_preference="openai"))

        providers_mock.chat_openai.assert_called_once()
        gemini_mock.assert_not_called()
        _, _, meta = chunks[-1]
        assert "openai" in (meta.get("model_used") or "").lower()

    @pytest.mark.asyncio
    async def test_forced_anthropic_route_calls_chat_anthropic(self):
        """model_preference='anthropic' must call chat_anthropic, not _gemini_chat."""
        anthropic_reply = "Anthropic answer"

        providers_mock = MagicMock()
        providers_mock.chat_openai = AsyncMock(return_value=None)
        providers_mock.chat_anthropic = AsyncMock(return_value=anthropic_reply)

        patches = _base_patches()
        gemini_mock = AsyncMock()

        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=gemini_mock),
            patch.dict(sys.modules, {"llm.providers": providers_mock}),
        ):
            chunks = await _collect(chat_stream("Hi", history=[], model_preference="anthropic"))

        providers_mock.chat_anthropic.assert_called_once()
        gemini_mock.assert_not_called()
        _, _, meta = chunks[-1]
        assert "anthropic" in (meta.get("model_used") or "").lower()

    @pytest.mark.asyncio
    async def test_forced_local_disabled_yields_warning(self):
        """model_preference='local' with LOCAL_LLM_ENABLED=False yields a warning chunk."""
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_LOCAL_LLM_ENABLED, False),
        ):
            chunks = await _collect(chat_stream("Run local", history=[], model_preference="local"))

        text, is_final, _ = chunks[-1]
        assert is_final is True
        assert "Local LLM" in text or "disabled" in text.lower()

    @pytest.mark.asyncio
    async def test_forced_local_ollama_unreachable_yields_warning(self):
        """model_preference='local' with Ollama unreachable yields a specific warning."""
        ollama_unavail = AsyncMock(return_value=False)
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_LOCAL_LLM_ENABLED, True),
            patch(_OLLAMA_AVAIL, ollama_unavail),
        ):
            chunks = await _collect(chat_stream("Run local", history=[], model_preference="local"))

        text, is_final, _ = chunks[-1]
        assert is_final is True
        assert "Ollama" in text or "not reachable" in text.lower() or "local" in text.lower()

    @pytest.mark.asyncio
    async def test_gemini_missing_api_key_yields_warning(self):
        """model_preference='gemini' with no GOOGLE_API_KEY yields an error chunk."""
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch("llm.chat.GOOGLE_API_KEY", ""),
        ):
            chunks = await _collect(chat_stream("Hi", history=[], model_preference="gemini"))

        text, is_final, _ = chunks[-1]
        assert is_final is True
        assert "Gemini" in text or "GOOGLE_API_KEY" in text or "key" in text.lower()


# ---------------------------------------------------------------------------
# Class 3: History handling
# ---------------------------------------------------------------------------


class TestChatStreamHistoryHandling:
    """History is trimmed and forwarded; None is treated as empty."""

    @pytest.mark.asyncio
    async def test_none_history_treated_as_empty(self):
        """Passing history=None must not raise; function should yield normally."""
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            chunks = await _collect(chat_stream("Hello", history=None, model_preference="gemini"))
        assert len(chunks) >= 1
        _, is_final, _ = chunks[-1]
        assert is_final is True

    @pytest.mark.asyncio
    async def test_history_passed_to_trim(self):
        """_trim_history is called with the provided history list."""
        initial_history = [{"role": "user", "parts": ["Earlier message"]}]
        captured: list = []

        async def _capturing_trim(history, *, model_hint="gemini", context_quality=None):
            captured.append(list(history))
            return history

        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=_capturing_trim),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
        ):
            await _collect(chat_stream("Follow-up question", history=initial_history, model_preference="gemini"))

        assert len(captured) == 1
        assert captured[0] == initial_history

    @pytest.mark.asyncio
    async def test_updated_history_appended_in_final_chunk(self):
        """The updated_history in the final chunk is longer than the initial history."""
        initial = [{"role": "user", "parts": ["prior message"]}]
        patches = _base_patches()
        with (
            patch(_TRIM, side_effect=patches[_TRIM]),
            patch(_RECALL, side_effect=patches[_RECALL]),
            patch(_RATE, patches[_RATE]),
            patch(_SEL, side_effect=patches[_SEL]),
            patch(_GEMINI, side_effect=patches[_GEMINI]),
            patch("llm.chat.GOOGLE_API_KEY", "fake-key-for-test"),
        ):
            chunks = await _collect(chat_stream("New question", history=initial, model_preference="gemini"))

        _, _, meta = chunks[-1]
        updated = meta.get("updated_history", [])
        assert len(updated) > len(initial)
