"""Tests for hybrid model selection (model_preference routing)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# memory.py — per-user preference storage
# ---------------------------------------------------------------------------


class TestModelPreference:
    """Tests for get_model_preference / set_model_preference in memory.py."""

    def test_default_preference_is_auto(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        # No file on disk → should return config default
        from config import cfg
        assert memory.get_model_preference(12345) == cfg.default_model_preference

    def test_set_and_get_preference(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "gemini")
        assert "✅" in result
        assert "Gemini" in result
        assert memory.get_model_preference(12345) == "gemini"

    def test_set_preference_local(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "local")
        assert "✅" in result
        assert "Local" in result
        assert memory.get_model_preference(12345) == "local"

    def test_set_preference_auto(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        # Set to gemini first, then back to auto
        memory.set_model_preference(12345, "gemini")
        result = memory.set_model_preference(12345, "auto")
        assert "✅" in result
        assert memory.get_model_preference(12345) == "auto"

    def test_set_invalid_preference(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "gpt4")
        assert "❌" in result
        assert "Invalid" in result

    def test_set_preference_copilot(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "copilot")
        assert "✅" in result
        assert "Copilot" in result
        assert memory.get_model_preference(12345) == "copilot"

    def test_preference_persists_to_disk(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        memory.set_model_preference(99, "local")
        # Read raw file
        prefs_file = tmp_path / "prefs" / "99.json"
        assert prefs_file.exists()
        data = json.loads(prefs_file.read_text())
        assert data["model_preference"] == "local"

    def test_preference_case_insensitive(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "GEMINI")
        assert "✅" in result
        assert memory.get_model_preference(12345) == "gemini"

    def test_set_preference_accepts_claude_alias(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "claude")
        assert "✅" in result
        assert "Anthropic" in result
        assert memory.get_model_preference(12345) == "anthropic"

    def test_set_invalid_preference_includes_did_you_mean(self, tmp_path, monkeypatch):
        import memory
        import memory_preferences
        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        monkeypatch.setattr(memory_preferences, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "gemni")
        assert "❌" in result
        assert "Did you mean `gemini`?" in result


# ---------------------------------------------------------------------------
# llm.py — _try_local_model force param
# ---------------------------------------------------------------------------


class TestTryLocalModelForce:
    """Tests that the force parameter on _try_local_model works correctly."""

    @pytest.mark.asyncio
    async def test_force_skips_needs_tools_check(self):
        """When force=True, _needs_tools() should NOT be consulted."""
        import llm
        from llm import tool_execution

        # A message that would normally trigger _needs_tools
        msg = "restart the plex container"
        assert llm._needs_tools(msg) is True  # sanity check

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(tool_execution, "_ollama_available", new_callable=AsyncMock, return_value=True),
            patch.object(tool_execution, "_chat_ollama", new_callable=AsyncMock, return_value="Done!"),
            patch.object(tool_execution, "_gemma_response_seems_valid", return_value=True),
        ):
            result = await llm._try_local_model(msg, [], force=True)
            assert result == "Done!"

    @pytest.mark.asyncio
    async def test_no_force_respects_needs_tools(self):
        """Without force, _needs_tools messages should return None."""
        import llm

        msg = "restart the plex container"
        with patch.object(llm, "LOCAL_LLM_ENABLED", True):
            result = await llm._try_local_model(msg, [], force=False)
            assert result is None

    @pytest.mark.asyncio
    async def test_force_still_checks_ollama_available(self):
        """Even with force=True, if Ollama is down, should return None."""
        import llm
        from llm import tool_execution

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(tool_execution, "_ollama_available", new_callable=AsyncMock, return_value=False),
        ):
            result = await llm._try_local_model("hello", [], force=True)
            assert result is None


# ---------------------------------------------------------------------------
# llm.py — chat() model_preference routing
# ---------------------------------------------------------------------------


class TestChatModelPreference:
    """Tests that chat() correctly routes based on model_preference."""

    @pytest.mark.asyncio
    async def test_chat_local_preference_success(self):
        """model_preference='local' should force Ollama path."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(chat_module, "_ollama_available", new_callable=AsyncMock, return_value=True),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value="Hello from Gemma!"),
        ):
            text, hist, model = await llm.chat("hello", model_preference="local")
            assert text == "Hello from Gemma!"
            assert model == llm.OLLAMA_MODEL
            # Verify force=True was passed
            chat_module._try_local_model.assert_called_once_with("hello", [], force=True)

    @pytest.mark.asyncio
    async def test_chat_local_preference_ollama_down(self):
        """model_preference='local' with Ollama down should return error."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(chat_module, "_ollama_available", new_callable=AsyncMock, return_value=False),
        ):
            text, hist, model = await llm.chat("hello", model_preference="local")
            assert "not reachable" in text
            assert model == "none"

    @pytest.mark.asyncio
    async def test_chat_local_preference_disabled(self):
        """model_preference='local' with LOCAL_LLM_ENABLED=False should return error."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with patch.object(chat_module, "LOCAL_LLM_ENABLED", False):
            text, hist, model = await llm.chat("hello", model_preference="local")
            assert "disabled" in text
            assert model == "none"

    @pytest.mark.asyncio
    async def test_chat_gemini_preference_skips_ollama(self):
        """model_preference='gemini' should go straight to Gemini."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        mock_model = MagicMock()
        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_get_model", new_callable=AsyncMock, return_value=mock_model),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini says hi", [], "gemini-2.5-flash")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock) as mock_local,
        ):
            mock_rl.check.return_value = True
            text, hist, model = await llm.chat("hello", model_preference="gemini")
            assert text == "Gemini says hi"
            assert model == "gemini-2.5-flash"
            # _try_local_model should NOT have been called
            mock_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_gemini_preference_no_api_key(self):
        """model_preference='gemini' without API key should return error."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with patch.object(chat_module, "GOOGLE_API_KEY", ""):
            text, hist, model = await llm.chat("hello", model_preference="gemini")
            assert "not configured" in text
            assert model == "none"

    @pytest.mark.asyncio
    async def test_chat_auto_preference_tries_copilot_first(self):
        """model_preference='auto' should try Copilot proxy then fall through to Gemini."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Copilot response"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            text, hist, model = await llm.chat("hello", model_preference="auto")
            assert text.startswith("Copilot response")
            assert model.startswith("copilot/")

    @pytest.mark.asyncio
    async def test_chat_auto_retries_second_copilot_candidate_before_gemini(self):
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]

        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, side_effect=[None, "Claude fallback response"]),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            text, _hist, model = await llm.chat("hello", model_preference="auto")

        assert text.startswith("Claude fallback response")
        assert model.startswith("copilot/")

    @pytest.mark.asyncio
    async def test_chat_auto_respects_gemini_first_routing_profile(self):
        import sys

        import llm
        import model_routing_policy

        chat_module = sys.modules["llm.chat"]

        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch.object(model_routing_policy.cfg, "routing_profile", "gemini-first"),
            patch("model_router.chat_openai", new_callable=AsyncMock) as mock_openai,
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            text, _hist, model = await llm.chat("hello", model_preference="auto")

        assert text == "Gemini response"
        assert model == "gemini-2.5-flash"
        mock_openai.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_auto_balanced_profile_uses_ollama_for_simple_chat(self):
        import sys

        import llm
        import model_routing_policy

        chat_module = sys.modules["llm.chat"]

        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.is_ollama_alive", new_callable=AsyncMock, return_value=True),
            patch.object(model_routing_policy.cfg, "routing_profile", "balanced"),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value="Hello from Gemma!"),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock) as mock_gemini,
        ):
            text, _hist, model = await llm.chat("hello", model_preference="auto")

        assert text == "Hello from Gemma!"
        assert model == llm.OLLAMA_MODEL
        mock_gemini.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_auto_rejects_copilot_placeholder_and_falls_through_to_gemini(self):
        """Placeholder Copilot replies should not be returned as final answers in auto mode."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        mock_model = MagicMock()
        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="One moment while I look that up."),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            text, hist, model = await llm.chat("hello", model_preference="auto")
            assert text == "Gemini response"
            assert model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_chat_copilot_preference_uses_proxy(self):
        import llm

        with (
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Copilot direct response"),
        ):
            text, _hist, model = await llm.chat("hello", model_preference="copilot")

        assert text.startswith("Copilot direct response")
        assert model.startswith("copilot/")


# ---------------------------------------------------------------------------
# llm.py — chat_stream() model_preference routing
# ---------------------------------------------------------------------------


class TestChatStreamModelPreference:
    """Tests that chat_stream() respects model_preference."""

    @pytest.mark.asyncio
    async def test_stream_local_preference_success(self):
        """model_preference='local' in chat_stream should yield from Ollama."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(chat_module, "_ollama_available", new_callable=AsyncMock, return_value=True),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value="Gemma streaming!"),
        ):
            chunks = []
            async for text, is_final, meta in llm.chat_stream("hi", model_preference="local"):
                chunks.append((text, is_final, meta))
            assert len(chunks) == 1
            assert chunks[0][0] == "Gemma streaming!"
            assert chunks[0][1] is True
            assert chunks[0][2]["model_used"] == llm.OLLAMA_MODEL

    @pytest.mark.asyncio
    async def test_stream_local_preference_ollama_down(self):
        """model_preference='local' with Ollama down should yield error."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        with (
            patch.object(llm, "LOCAL_LLM_ENABLED", True),
            patch.object(chat_module, "_ollama_available", new_callable=AsyncMock, return_value=False),
        ):
            chunks = []
            async for text, is_final, meta in llm.chat_stream("hi", model_preference="local"):
                chunks.append((text, is_final, meta))
            assert len(chunks) == 1
            assert "not reachable" in chunks[0][0]

    @pytest.mark.asyncio
    async def test_stream_gemini_preference_skips_local(self):
        """model_preference='gemini' in chat_stream should skip local path."""
        import sys

        import llm

        chat_module = sys.modules['llm.chat']

        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_get_model", new_callable=AsyncMock, return_value=mock_model),
            patch.object(chat_module, "_needs_tools", return_value=True),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini answer", [], "gemini-2.5-flash")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock) as mock_local,
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("hi", model_preference="gemini"):
                chunks.append((text, is_final, meta))
            # Local model should NOT be called
            mock_local.assert_not_called()
            assert any("Gemini answer" in c[0] for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_invalid_retry_escalates_with_web_results(self):
        """Placeholder responses should escalate instead of leaking to callers."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"
        search_fn = AsyncMock(return_value="Overnight events: NAS healthy, queue empty.")

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(
                chat_module,
                "_gemini_chat",
                new_callable=AsyncMock,
                side_effect=[
                    ("One moment while I look that up.", [], "gemini-2.5-flash"),
                    ("Let me check that for you.", [], "gemini-2.5-flash"),
                    ("Overnight summary: NAS healthy and the queue is empty.", [], "gemini-2.5-flash"),
                ],
            ),
            patch.object(chat_module, "SKILLS", {"search_web": search_fn}),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0] == "Overnight summary: NAS healthy and the queue is empty."
        assert "Retry response remained placeholder" in chunks[-1][2]["routing_notes"]
        search_fn.assert_awaited_once_with("what happened overnight?")

    @pytest.mark.asyncio
    async def test_stream_auto_openai_placeholder_falls_through_to_gemini(self):
        """Auto-routed OpenAI placeholder replies should not be yielded as final output."""
        import sys
        from types import SimpleNamespace

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch("model_router.classify_query", return_value=SimpleNamespace(model_type="openai")),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="One moment while I look that up."),
            patch("model_router.is_ollama_alive", new_callable=AsyncMock, return_value=True),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("show me today's games", model_preference="auto"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0] == "Gemini response"
        assert chunks[-1][2]["model_used"] == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_stream_auto_non_tool_prefers_copilot_when_available(self):
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Copilot stream response"),
            patch("model_router.is_ollama_alive", new_callable=AsyncMock, return_value=True),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("hello there", model_preference="auto"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0].startswith("Copilot stream response")
        assert chunks[-1][2]["model_used"].startswith("copilot/")

    @pytest.mark.asyncio
    async def test_stream_auto_retries_second_copilot_candidate_before_gemini(self):
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, side_effect=[None, "Claude stream fallback"]),
            patch("model_router.is_ollama_alive", new_callable=AsyncMock, return_value=True),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("hello there", model_preference="auto"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0].startswith("Claude stream fallback")
        assert chunks[-1][2]["model_used"].startswith("copilot/")

    @pytest.mark.asyncio
    async def test_stream_invalid_retry_without_search_returns_fallback(self):
        """If retry stays invalid and no search tool exists, return a clear fallback."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(
                chat_module,
                "_gemini_chat",
                new_callable=AsyncMock,
                side_effect=[
                    ("One moment while I look that up.", [], "gemini-2.5-flash"),
                    ("Let me check that for you.", [], "gemini-2.5-flash"),
                ],
            ),
            patch.object(chat_module, "SKILLS", {}),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert "couldn't complete that live lookup cleanly" in chunks[-1][0]
        assert "Returned explicit fallback after invalid retry" in chunks[-1][2]["routing_notes"]

    @pytest.mark.asyncio
    async def test_stream_primary_gemini_failure_returns_graceful_message(self):
        """Primary Gemini failures should not bubble up as exceptions to callers."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert "Gemini is temporarily unavailable" in chunks[-1][0]
        assert chunks[-1][2]["model_used"] == "unavailable"
        assert "Gemini unavailable (primary)" in chunks[-1][2]["routing_notes"]

    @pytest.mark.asyncio
    async def test_stream_retry_gemini_failure_returns_graceful_message(self):
        """Retry Gemini failures should not bubble up as exceptions to callers."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(
                chat_module,
                "_gemini_chat",
                new_callable=AsyncMock,
                side_effect=[
                    ("One moment while I look that up.", [], "gemini-2.5-flash"),
                    RuntimeError("Gemini circuit breaker is open"),
                ],
            ),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert "Gemini is temporarily unavailable" in chunks[-1][0]
        assert chunks[-1][2]["model_used"] == "unavailable"
        assert "Gemini unavailable (retry)" in chunks[-1][2]["routing_notes"]

    @pytest.mark.asyncio
    async def test_stream_primary_gemini_failure_uses_copilot_after_local_fails(self):
        """Gemini failures should fall through to Copilot when local recovery is unavailable."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Recovered through Copilot."),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0].startswith("Recovered through Copilot.")
        assert chunks[-1][2]["model_used"].startswith("copilot/")
        assert "Gemini unavailable → Copilot proxy (primary)" in chunks[-1][2]["routing_notes"]

    @pytest.mark.asyncio
    async def test_stream_primary_gemini_failure_uses_direct_sports_skill_when_routed(self):
        """Sports asks should recover through the direct sports skill when Gemini is unavailable."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
            patch("model_router.COPILOT_PROXY_ENABLED", False),
            patch.object(chat_module, "_get_tool_declarations", return_value=[{"name": "generate_sports_watch_report"}]),
            patch.object(chat_module, "route_tool_declarations", return_value=([{"name": "generate_sports_watch_report"}], {})),
            patch("skills.reporting_skills.generate_sports_watch_report", new_callable=AsyncMock, return_value="Direct sports answer"),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream(
                "Show me the schedule for the men's division 1 college lacrosse games today",
                model_preference="gemini",
            ):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0] == "Direct sports answer"
        assert chunks[-1][2]["model_used"] == "direct-sports-skill"
        assert "Gemini unavailable → direct sports skill (primary)" in chunks[-1][2]["routing_notes"]

    @pytest.mark.asyncio
    async def test_chat_auto_gemini_failure_uses_copilot_after_local_fails(self):
        """chat() should also recover through Copilot when Gemini is unavailable."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Recovered through Copilot."),
        ):
            mock_rl.check.return_value = True
            text, _hist, model = await llm.chat("what happened overnight?", model_preference="auto")

        assert text.startswith("Recovered through Copilot.")
        assert model.startswith("copilot/")

    @pytest.mark.asyncio
    async def test_stream_primary_gemini_failure_uses_copilot_after_local_timeout(self):
        """A slow local recovery should time out and still fall through to Copilot."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"

        async def local_timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError()

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, side_effect=local_timeout),
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value="Recovered through Copilot."),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream("what happened overnight?", model_preference="gemini"):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0].startswith("Recovered through Copilot.")
        assert chunks[-1][2]["model_used"].startswith("copilot/")

    @pytest.mark.asyncio
    async def test_stream_primary_gemini_failure_accepts_copilot_realtime_limitation_reply(self):
        """Copilot recovery replies should not be filtered with Gemma's hallucination regex."""
        import sys

        import llm

        chat_module = sys.modules["llm.chat"]
        mock_model = MagicMock()
        mock_model.model_name = "gemini-2.5-flash"
        fallback_reply = "I don't have access to real-time sports schedules in this fallback mode."

        with (
            patch.object(chat_module, "GOOGLE_API_KEY", "test-key"),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_trim_history", new_callable=AsyncMock, side_effect=lambda history, **_: history),
            patch.object(chat_module, "_auto_recall_context", new_callable=AsyncMock, return_value=""),
            patch.object(chat_module, "_select_model_for_message", new_callable=AsyncMock, return_value=(mock_model, {})),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, side_effect=RuntimeError("Gemini circuit breaker is open")),
            patch.object(chat_module, "_try_local_model", new_callable=AsyncMock, return_value=None),
            patch("model_router.COPILOT_PROXY_ENABLED", True),
            patch("model_router.chat_openai", new_callable=AsyncMock, return_value=fallback_reply),
        ):
            mock_rl.check.return_value = True
            chunks = []
            async for text, is_final, meta in llm.chat_stream(
                "Show me the schedule for the men's division 1 college lacrosse games today",
                model_preference="gemini",
            ):
                chunks.append((text, is_final, meta))

        assert chunks[-1][1] is True
        assert chunks[-1][0].startswith(fallback_reply)
        assert chunks[-1][2]["model_used"].startswith("copilot/")
        assert "Gemini unavailable → Copilot proxy (primary)" in chunks[-1][2]["routing_notes"]


# ---------------------------------------------------------------------------
# Guardrail: local + tools → auto-upgrade
# ---------------------------------------------------------------------------


class TestGuardrails:
    """Test that guardrails auto-upgrade from local to gemini when tools needed."""

    def test_needs_tools_triggers_upgrade(self):
        """_needs_tools returning True for a tool query should be detectable."""
        import llm

        assert llm._needs_tools("restart the plex container") is True
        assert llm._needs_tools("what is the weather in philly") is True
        assert llm._needs_tools("tell me a joke") is False
        assert llm._needs_tools("explain quantum computing") is False
