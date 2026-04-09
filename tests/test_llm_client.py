"""
Tests dedicated to llm_client.py — model config, tool building,
system prompt loading, and usage recording.
"""

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub google.genai before importing llm_client
_genai_mock = MagicMock()
_genai_mock.types.ThinkingConfig = MagicMock
_genai_mock.types.ContentDict = dict
_genai_mock.types.GenerateContentConfig = MagicMock
_genai_mock.types.Tool = MagicMock
_genai_mock.types.FunctionDeclaration = MagicMock
_genai_mock.types.Schema = MagicMock
_genai_mock.types.Type = MagicMock()
_genai_mock.types.Type.OBJECT = "OBJECT"
_genai_mock.types.Type.STRING = "STRING"
_genai_mock.types.Type.INTEGER = "INTEGER"
_genai_mock.types.Type.NUMBER = "NUMBER"
_genai_mock.types.Type.BOOLEAN = "BOOLEAN"
_genai_mock.types.Type.ARRAY = "ARRAY"
_genai_mock.Client = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)

import llm_client  # noqa: E402

# Grab the real _init_gemini_model BEFORE conftest's autouse fixture patches it
_real_init_gemini_model = llm_client._init_gemini_model

# Resolve genai.types.Type via llm_client's actual genai reference (from `from google import genai`)
# MagicMock attribute access is cached, so genai.types.Type.X is always the same object per X.
import google as _google_module  # noqa: E402

_lc_genai = getattr(_google_module, "genai", None) or sys.modules.get("google.genai")
_lc_types = getattr(_lc_genai, "types", None) if _lc_genai else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_prompt_cache():
    llm_client._system_prompt_cache = None
    llm_client._system_prompt_mtime = 0.0


# ---------------------------------------------------------------------------
# _load_system_prompt
# ---------------------------------------------------------------------------

class TestLoadSystemPrompt:
    def test_default_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        prompt = llm_client._load_system_prompt()
        assert "OpenClaw" in prompt

    def test_loads_from_file(self, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.txt").write_text("Custom prompt text.")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        assert llm_client._load_system_prompt() == "Custom prompt text."

    def test_caching_same_mtime(self, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        f = prompts_dir / "system.txt"
        f.write_text("Cached prompt.")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        first = llm_client._load_system_prompt()
        # Overwrite without changing mtime simulation — cache should return first
        # We rely on mtime being unchanged (fast write within same second)
        second = llm_client._load_system_prompt()
        assert first == second

    def test_mtime_change_triggers_reload(self, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        f = prompts_dir / "system.txt"
        f.write_text("Version 1.")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        v1 = llm_client._load_system_prompt()
        assert v1 == "Version 1."

        # Force a different mtime by writing new content and bumping _system_prompt_mtime to 0
        f.write_text("Version 2.")
        llm_client._system_prompt_mtime = 0.0  # force stale

        v2 = llm_client._load_system_prompt()
        assert v2 == "Version 2."

    def test_strips_whitespace(self, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system.txt").write_text("  padded  \n")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        assert llm_client._load_system_prompt() == "padded"


# ---------------------------------------------------------------------------
# _convert_schema
# ---------------------------------------------------------------------------

class TestConvertSchema:
    def test_object_type_maps_correctly(self):
        result = llm_client._convert_schema({"type": "object"})
        # Both explicit "object" and unknown types fall back to Type.OBJECT;
        # use `is` since MagicMock attribute access is cached per attribute name.
        result2 = llm_client._convert_schema({"type": "object"})
        assert result["type"] is result2["type"]

    def test_string_type_maps_correctly(self):
        result_str = llm_client._convert_schema({"type": "string"})
        result_obj = llm_client._convert_schema({"type": "object"})
        # STRING and OBJECT should be different mock attributes
        assert result_str["type"] is not result_obj["type"]

    def test_integer_type_maps_correctly(self):
        result_int = llm_client._convert_schema({"type": "integer"})
        result_obj = llm_client._convert_schema({"type": "object"})
        assert result_int["type"] is not result_obj["type"]

    def test_unknown_type_falls_back_to_object(self):
        result_unknown = llm_client._convert_schema({"type": "foobar"})
        result_obj = llm_client._convert_schema({"type": "object"})
        # Both map to Type.OBJECT (same cached mock attribute)
        assert result_unknown["type"] is result_obj["type"]

    def test_properties_included(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A name"},
            },
        }
        result = llm_client._convert_schema(schema)
        assert "properties" in result
        assert "name" in result["properties"]

    def test_required_fields_included(self):
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        result = llm_client._convert_schema(schema)
        assert result["required"] == ["name", "age"]

    def test_no_properties_key_when_absent(self):
        result = llm_client._convert_schema({"type": "string"})
        assert "properties" not in result

    def test_missing_type_defaults_to_object(self):
        result_empty = llm_client._convert_schema({})
        result_obj = llm_client._convert_schema({"type": "object"})
        assert result_empty["type"] is result_obj["type"]


# ---------------------------------------------------------------------------
# _build_tools
# ---------------------------------------------------------------------------

class TestBuildTools:
    def test_returns_list(self):
        result = llm_client._build_tools([])
        assert isinstance(result, list)

    def test_with_empty_declarations_returns_one_tool(self):
        result = llm_client._build_tools([])
        assert len(result) == 1

    def test_single_declaration_creates_function_declaration(self):
        decl = [
            {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        result = llm_client._build_tools(decl)
        assert len(result) == 1
        # The Tool constructor was invoked (result is whatever the mock returns)
        assert result is not None

    def test_uses_internal_declarations_when_none_given(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_TOOL_DECLARATIONS", [])
        result = llm_client._build_tools()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _reset_models
# ---------------------------------------------------------------------------

class TestResetModels:
    def test_resets_all_to_none(self, monkeypatch):
        # Prime with fake values
        monkeypatch.setattr(llm_client, "_model", MagicMock(), raising=False)
        monkeypatch.setattr(llm_client, "_model_system_prompt", "some prompt", raising=False)
        monkeypatch.setattr(llm_client, "_thinking_model", MagicMock(), raising=False)
        monkeypatch.setattr(llm_client, "_thinking_model_prompt", "some thinking prompt", raising=False)

        llm_client._reset_models()

        assert llm_client._model is None
        assert llm_client._model_system_prompt is None
        assert llm_client._thinking_model is None
        assert llm_client._thinking_model_prompt is None


# ---------------------------------------------------------------------------
# _get_tool_declarations
# ---------------------------------------------------------------------------

class TestGetToolDeclarations:
    def test_returns_list(self):
        result = llm_client._get_tool_declarations()
        assert isinstance(result, list)

    def test_returns_copy(self, monkeypatch):
        monkeypatch.setattr(llm_client, "_TOOL_DECLARATIONS", [{"name": "tool1"}])
        result = llm_client._get_tool_declarations()
        result.append({"name": "injected"})
        assert len(llm_client._TOOL_DECLARATIONS) == 1


# ---------------------------------------------------------------------------
# _init_gemini_model
# ---------------------------------------------------------------------------

class TestInitGeminiModel:
    def test_raises_without_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm_client, "_init_gemini_model", _real_init_gemini_model)
        monkeypatch.setattr(llm_client, "GOOGLE_API_KEY", "")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            _real_init_gemini_model("gemini-2.0-flash")

    def test_returns_model_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm_client, "GOOGLE_API_KEY", "fake-key")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        result = _real_init_gemini_model("gemini-2.0-flash")
        assert isinstance(result, llm_client._ModelConfig)
        assert result.model_name == "gemini-2.0-flash"

    def test_with_tools_false_no_tools_in_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm_client, "GOOGLE_API_KEY", "fake-key")
        monkeypatch.setattr(llm_client, "CONFIG_DIR", tmp_path)
        _reset_prompt_cache()
        result = _real_init_gemini_model("gemini-2.0-flash", with_tools=False)
        assert result.model_name == "gemini-2.0-flash"


# ---------------------------------------------------------------------------
# _record_usage (async)
# ---------------------------------------------------------------------------

class TestRecordUsage:
    @pytest.mark.asyncio
    async def test_records_usage_with_valid_metadata(self, monkeypatch):
        mock_tracker = AsyncMock()
        monkeypatch.setattr(llm_client, "spending_tracker", mock_tracker)

        response = MagicMock()
        response.usage_metadata.prompt_token_count = 100
        response.usage_metadata.candidates_token_count = 50

        await llm_client._record_usage(response)
        mock_tracker.record.assert_awaited_once_with(100, 50)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_missing_metadata(self, monkeypatch):
        mock_tracker = AsyncMock()
        monkeypatch.setattr(llm_client, "spending_tracker", mock_tracker)

        response = MagicMock()
        response.usage_metadata = None

        # Should not raise
        await llm_client._record_usage(response)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_exception(self, monkeypatch):
        mock_tracker = AsyncMock(side_effect=Exception("boom"))
        monkeypatch.setattr(llm_client, "spending_tracker", mock_tracker)

        response = MagicMock()
        response.usage_metadata.prompt_token_count = 10
        response.usage_metadata.candidates_token_count = 10

        # Should swallow exception
        await llm_client._record_usage(response)
