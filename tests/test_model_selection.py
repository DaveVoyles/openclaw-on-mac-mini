"""Tests for hybrid model selection (model_preference routing)."""

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

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        # No file on disk → should return config default
        from config import cfg
        assert memory.get_model_preference(12345) == cfg.default_model_preference

    def test_set_and_get_preference(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "gemini")
        assert "✅" in result
        assert "Gemini" in result
        assert memory.get_model_preference(12345) == "gemini"

    def test_set_preference_local(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "local")
        assert "✅" in result
        assert "Local" in result
        assert memory.get_model_preference(12345) == "local"

    def test_set_preference_auto(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        # Set to gemini first, then back to auto
        memory.set_model_preference(12345, "gemini")
        result = memory.set_model_preference(12345, "auto")
        assert "✅" in result
        assert memory.get_model_preference(12345) == "auto"

    def test_set_invalid_preference(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "gpt4")
        assert "❌" in result
        assert "Invalid" in result

    def test_preference_persists_to_disk(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        memory.set_model_preference(99, "local")
        # Read raw file
        prefs_file = tmp_path / "prefs" / "99.json"
        assert prefs_file.exists()
        data = json.loads(prefs_file.read_text())
        assert data["model_preference"] == "local"

    def test_preference_case_insensitive(self, tmp_path, monkeypatch):
        import memory

        monkeypatch.setattr(memory, "_PREFS_DIR", tmp_path / "prefs")
        result = memory.set_model_preference(12345, "GEMINI")
        assert "✅" in result
        assert memory.get_model_preference(12345) == "gemini"


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

        mock_model = MagicMock()
        with (
            patch("model_router.COPILOT_PROXY_ENABLED", False),
            patch.object(chat_module, "_rate_limiter") as mock_rl,
            patch.object(chat_module, "_get_model", new_callable=AsyncMock, return_value=mock_model),
            patch.object(chat_module, "_gemini_chat", new_callable=AsyncMock, return_value=("Gemini response", [], "gemini-2.5-flash")),
        ):
            mock_rl.check.return_value = True
            text, hist, model = await llm.chat("hello", model_preference="auto")
            assert text == "Gemini response"
            # Ollama should NOT be called in auto mode
            assert not hasattr(llm._try_local_model, "assert_called")


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
