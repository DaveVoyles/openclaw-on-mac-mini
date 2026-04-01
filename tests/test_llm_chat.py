"""
Tests for llm.py chat-related functionality.

Covers: system prompt loading, _needs_tools heuristic, _get_model init,
chat() routing (Ollama vs Gemini), function-call loop, _extract_history,
and _execute_function_call caching.

All Gemini / Ollama network calls are mocked — nothing hits real APIs.
"""

import sys
import time
from unittest.mock import MagicMock

import pytest

# Ensure google.genai is stubbed before importing llm
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

import llm  # noqa: E402
import llm_client  # noqa: E402

# ---------------------------------------------------------------------------
# System prompt loading
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_default_prompt_when_file_missing(self, tmp_path, monkeypatch):
        """When the prompt file doesn't exist, fall back to the hardcoded default."""
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        llm._system_prompt_cache = None
        llm._system_prompt_mtime = 0.0
        prompt = llm._load_system_prompt()
        assert "OpenClaw" in prompt

    def test_loads_prompt_from_file(self, tmp_path, monkeypatch):
        """When a system.txt file exists, its content is returned."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        system_file = prompts_dir / "system.txt"
        system_file.write_text("You are TestBot.")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        llm._system_prompt_cache = None
        llm._system_prompt_mtime = 0.0
        assert llm._load_system_prompt() == "You are TestBot."

    def test_cache_invalidation_on_mtime_change(self, tmp_path, monkeypatch):
        """Prompt is reloaded when the file mtime changes."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        system_file = prompts_dir / "system.txt"
        system_file.write_text("v1")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        llm._system_prompt_cache = None
        llm._system_prompt_mtime = 0.0
        assert llm._load_system_prompt() == "v1"

        # Modify the file
        system_file.write_text("v2")
        # Touch to ensure mtime changes
        import os
        os.utime(system_file, (time.time() + 1, time.time() + 1))
        assert llm._load_system_prompt() == "v2"


# ---------------------------------------------------------------------------
# _needs_tools heuristic
# ---------------------------------------------------------------------------

class TestNeedsTools:
    @pytest.mark.parametrize("query", [
        "restart sonarr container",
        "show me the container logs",
        "check the health status",
        "search the web for news",
        "what's the weather forecast?",
        "is plex up?",
        "send an email to dave",
        "create a calendar event",
        "https://example.com",
        "check zillow listings",
    ])
    def test_tool_queries_detected(self, query):
        assert llm._needs_tools(query) is True

    @pytest.mark.parametrize("query", [
        "hi there",
        "tell me a joke",
        "what is the meaning of life?",
        "explain quantum physics",
    ])
    def test_conversational_queries_not_flagged(self, query):
        assert llm._needs_tools(query) is False


# ---------------------------------------------------------------------------
# _extract_history
# ---------------------------------------------------------------------------

class TestExtractHistory:
    def test_extracts_text_parts(self):
        """History with plain text parts should be serialized correctly."""
        mock_session = MagicMock()
        part = MagicMock()
        part.text = "Hello world"
        part.function_call = MagicMock()
        part.function_call.name = ""

        content = MagicMock()
        content.role = "model"
        content.parts = [part]
        mock_session.get_history.return_value = [content]

        result = llm._extract_history(mock_session)
        assert len(result) == 1
        assert result[0]["role"] == "model"
        assert "Hello world" in result[0]["parts"]

    def test_extracts_function_call_parts(self):
        """Function call parts should be serialized as [Called name]."""
        mock_session = MagicMock()
        part = MagicMock()
        part.text = ""
        part.function_call = MagicMock()
        part.function_call.name = "get_docker_stats"
        part.function_call.args = {}

        content = MagicMock()
        content.role = "model"
        content.parts = [part]
        mock_session.get_history.return_value = [content]

        result = llm._extract_history(mock_session)
        assert any("[Called get_docker_stats]" in p for p in result[0]["parts"])


# ---------------------------------------------------------------------------
# is_configured / get_rate_info
# ---------------------------------------------------------------------------

class TestConfigHelpers:
    def test_is_configured_with_api_key(self, monkeypatch):
        monkeypatch.setattr(llm, "GOOGLE_API_KEY", "test-key")
        assert llm.is_configured() is True

    def test_is_configured_with_local_llm(self, monkeypatch):
        monkeypatch.setattr(llm, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(llm, "LOCAL_LLM_ENABLED", True)
        assert llm.is_configured() is True

    def test_not_configured_when_nothing_set(self, monkeypatch):
        monkeypatch.setattr(llm, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(llm, "LOCAL_LLM_ENABLED", False)
        assert llm.is_configured() is False

    def test_get_rate_info_format(self):
        info = llm.get_rate_info()
        assert "/min" in info
        assert "/hr" in info


# ---------------------------------------------------------------------------
# Tool declarations integrity
# ---------------------------------------------------------------------------

class TestToolDeclarations:
    def test_tool_declarations_are_nonempty(self):
        assert len(llm._TOOL_DECLARATIONS) > 0

    def test_all_tool_declarations_have_name(self):
        for decl in llm._TOOL_DECLARATIONS:
            assert "name" in decl, f"Missing 'name' in tool declaration: {decl}"
            assert decl["name"], "Empty name in tool declaration"

    def test_all_tool_declarations_have_description(self):
        for decl in llm._TOOL_DECLARATIONS:
            assert "description" in decl, f"Missing 'description' in {decl.get('name', '?')}"

    def test_no_duplicate_tool_names(self):
        names = [d["name"] for d in llm._TOOL_DECLARATIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"

    def test_all_parameters_have_type(self):
        """Every parameter in every tool must have a type field."""
        for decl in llm._TOOL_DECLARATIONS:
            params = decl.get("parameters", {})
            props = params.get("properties", {})
            for pname, pdef in props.items():
                assert "type" in pdef, f"Tool {decl['name']}.{pname} missing type"


# ---------------------------------------------------------------------------
# chat() — Gemini routing and rate limit handling
# ---------------------------------------------------------------------------

class TestChatRouting:
    @pytest.mark.asyncio
    async def test_chat_returns_rate_limit_message_when_exhausted(self, monkeypatch):
        """When rate limit is exhausted, chat returns a warning message."""
        monkeypatch.setattr(llm, "LOCAL_LLM_ENABLED", False)
        # Exhaust the rate limiter
        rl = llm.RateLimiter(per_minute=1, per_hour=1)
        rl.record()
        monkeypatch.setattr(llm, "_rate_limiter", rl)

        text, history, model = await llm.chat("hello")
        assert "Rate limit" in text

    @pytest.mark.asyncio
    async def test_chat_returns_tuple_of_three(self, monkeypatch):
        """chat() always returns (text, history, model_used)."""
        monkeypatch.setattr(llm, "LOCAL_LLM_ENABLED", False)
        rl = llm.RateLimiter(per_minute=1, per_hour=1)
        rl.record()
        monkeypatch.setattr(llm, "_rate_limiter", rl)
        result = await llm.chat("test")
        assert isinstance(result, tuple)
        assert len(result) == 3
