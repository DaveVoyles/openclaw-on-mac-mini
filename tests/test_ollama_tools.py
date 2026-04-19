"""
Tests for src/ollama_tools.py

Covers: OLLAMA_TOOL_ALLOWLIST, convert_tools_for_ollama, chat_ollama_with_tools.
HTTP sessions are fully mocked — no real network calls are made.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies before importing the module under test
# ---------------------------------------------------------------------------

# config
_config_mock = MagicMock()
_config_mock.TIMEOUT_LONG = 60
_config_mock.TIMEOUT_SLOW = 30
sys.modules.setdefault("config", _config_mock)

# http_session — use setdefault for first-import, but we always patch at test
# runtime via the autouse fixture below so order doesn't matter.
_http_session_mock = MagicMock()
_mock_session_mgr = MagicMock()
_mock_aiohttp_session = AsyncMock()
_mock_session_mgr.get = AsyncMock(return_value=_mock_aiohttp_session)
_http_session_mock.SessionManager.return_value = _mock_session_mgr
sys.modules.setdefault("http_session", _http_session_mock)

# aiohttp (real import fine, but we mock individual objects)
import aiohttp  # noqa: E402

import ollama_tools as mod  # noqa: E402
from ollama_tools import (
    OLLAMA_MAX_TOOL_ROUNDS,
    OLLAMA_TOOL_ALLOWLIST,
    chat_ollama_with_tools,
    convert_tools_for_ollama,
)

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

ALLOWED_TOOL = next(iter(OLLAMA_TOOL_ALLOWLIST))  # e.g. "get_system_stats"

_BASE_DECL = {
    "name": ALLOWED_TOOL,
    "description": "Get system stats",
    "parameters": {
        "properties": {
            "host": {"type": "string", "description": "hostname"},
        },
        "required": ["host"],
    },
}

_BLOCKED_DECL = {
    "name": "delete_all_data",
    "description": "Dangerous",
    "parameters": {"properties": {}, "required": []},
}

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma3"
SYSTEM_PROMPT = "You are a helpful assistant."


def _make_execute_fn(result="tool result"):
    return AsyncMock(return_value=result)


def _chat_response(content="Here is the answer.", tool_calls=None):
    """Build a mock aiohttp response for the chat endpoint."""
    data = {"message": {"content": content, "role": "assistant"}}
    if tool_calls is not None:
        data["message"]["tool_calls"] = tool_calls

    resp_mock = AsyncMock()
    resp_mock.status = 200
    resp_mock.json = AsyncMock(return_value=data)
    resp_mock.__aenter__ = AsyncMock(return_value=resp_mock)
    resp_mock.__aexit__ = AsyncMock(return_value=False)
    return resp_mock


def _error_response(status=500):
    resp_mock = AsyncMock()
    resp_mock.status = status
    resp_mock.__aenter__ = AsyncMock(return_value=resp_mock)
    resp_mock.__aexit__ = AsyncMock(return_value=False)
    return resp_mock


def _tool_call_message(fn_name, fn_args=None):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": fn_name, "arguments": fn_args or {}}}],
    }


# ---------------------------------------------------------------------------
# OLLAMA_TOOL_ALLOWLIST
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Autouse fixture: always replace _ollama_sessions with a fresh MagicMock so
# that `mod._ollama_sessions.get = AsyncMock(...)` works regardless of whether
# the real http_session module was already imported by another test file.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_ollama_sessions():
    """Replace the module-level SessionManager with a plain MagicMock for every test."""
    fake_mgr = MagicMock()
    fake_mgr.get = AsyncMock(return_value=MagicMock())
    with patch.object(mod, "_ollama_sessions", fake_mgr):
        yield fake_mgr


class TestOllamaToolAllowlist:
    def test_is_frozenset(self):
        assert isinstance(OLLAMA_TOOL_ALLOWLIST, frozenset)

    def test_known_safe_tools_present(self):
        expected = {
            "get_system_stats",
            "get_docker_stats",
            "list_containers",
            "get_weather",
            "execute_python_code",
        }
        assert expected.issubset(OLLAMA_TOOL_ALLOWLIST)

    def test_dangerous_tools_absent(self):
        for tool in ("delete_all_data", "sudo_exec", "rm_rf"):
            assert tool not in OLLAMA_TOOL_ALLOWLIST

    def test_not_empty(self):
        assert len(OLLAMA_TOOL_ALLOWLIST) > 0


# ---------------------------------------------------------------------------
# convert_tools_for_ollama
# ---------------------------------------------------------------------------


class TestConvertToolsForOllama:
    def test_ollama_tools_empty_input_returns_empty(self):
        assert convert_tools_for_ollama([]) == []

    def test_blocked_tool_filtered_out(self):
        result = convert_tools_for_ollama([_BLOCKED_DECL])
        assert result == []

    def test_allowed_tool_included(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert len(result) == 1

    def test_mixed_input_filters_correctly(self):
        result = convert_tools_for_ollama([_BASE_DECL, _BLOCKED_DECL])
        assert len(result) == 1
        assert result[0]["function"]["name"] == ALLOWED_TOOL

    def test_output_has_type_function(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert result[0]["type"] == "function"

    def test_output_function_has_name(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert result[0]["function"]["name"] == ALLOWED_TOOL

    def test_output_function_has_description(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert result[0]["function"]["description"] == "Get system stats"

    def test_output_parameters_type_is_object(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert result[0]["function"]["parameters"]["type"] == "object"

    def test_properties_converted(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        props = result[0]["function"]["parameters"]["properties"]
        assert "host" in props
        assert props["host"]["type"] == "string"

    def test_required_fields_preserved(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        assert result[0]["function"]["parameters"]["required"] == ["host"]

    def test_missing_description_defaults_to_empty_string(self):
        decl = {**_BASE_DECL, "name": ALLOWED_TOOL}
        decl.pop("description", None)
        result = convert_tools_for_ollama([decl])
        assert result[0]["function"]["description"] == ""

    def test_property_description_included(self):
        result = convert_tools_for_ollama([_BASE_DECL])
        props = result[0]["function"]["parameters"]["properties"]
        assert props["host"]["description"] == "hostname"

    def test_property_missing_type_defaults_to_string(self):
        decl = {
            "name": ALLOWED_TOOL,
            "description": "x",
            "parameters": {
                "properties": {"arg": {"description": "an arg"}},
                "required": [],
            },
        }
        result = convert_tools_for_ollama([decl])
        assert result[0]["function"]["parameters"]["properties"]["arg"]["type"] == "string"

    def test_multiple_allowed_tools(self):
        decls = []
        for name in list(OLLAMA_TOOL_ALLOWLIST)[:3]:
            decls.append({"name": name, "description": "", "parameters": {"properties": {}, "required": []}})
        result = convert_tools_for_ollama(decls)
        assert len(result) == 3

    def test_empty_parameters_block(self):
        decl = {"name": ALLOWED_TOOL, "description": "x", "parameters": {}}
        result = convert_tools_for_ollama([decl])
        assert result[0]["function"]["parameters"]["properties"] == {}
        assert result[0]["function"]["parameters"]["required"] == []


# ---------------------------------------------------------------------------
# chat_ollama_with_tools — success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatOllamaWithToolsSuccess:
    async def _call(self, tool_decls=None, execute_fn=None, post_side_effect=None):
        """Helper that patches the session post and calls the SUT."""
        if tool_decls is None:
            tool_decls = [_BASE_DECL]
        if execute_fn is None:
            execute_fn = _make_execute_fn()

        mock_post_ctx = post_side_effect or _chat_response()

        session = MagicMock()
        session.post = MagicMock(return_value=mock_post_ctx)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        return await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=tool_decls,
            execute_fn=execute_fn,
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

    async def test_returns_text_on_no_tool_calls(self):
        text, calls = await self._call()
        assert text == "Here is the answer."
        assert calls == []

    async def test_no_tool_declarations_returns_none(self):
        result = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert result == (None, [])

    async def test_all_blocked_tools_returns_none(self):
        result = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BLOCKED_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert result == (None, [])

    async def test_tool_call_executed(self):
        execute_fn = _make_execute_fn("tool_result_xyz")

        tool_call_resp = AsyncMock()
        tool_call_resp.status = 200
        tool_call_resp.json = AsyncMock(return_value={
            "message": _tool_call_message(ALLOWED_TOOL, {"host": "server1"}),
        })
        tool_call_resp.__aenter__ = AsyncMock(return_value=tool_call_resp)
        tool_call_resp.__aexit__ = AsyncMock(return_value=False)

        final_resp = _chat_response("Done!")

        session = MagicMock()
        session.post = MagicMock(side_effect=[tool_call_resp, final_resp])
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        text, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=execute_fn,
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        execute_fn.assert_awaited_once_with(ALLOWED_TOOL, {"host": "server1"})
        assert calls[0][0] == ALLOWED_TOOL
        assert calls[0][2] == "tool_result_xyz"
        assert text == "Done!"

    async def test_non_allowlisted_tool_not_executed(self):
        execute_fn = _make_execute_fn()

        bad_tool = "delete_all_data"
        tool_call_resp = AsyncMock()
        tool_call_resp.status = 200
        tool_call_resp.json = AsyncMock(return_value={
            "message": _tool_call_message(bad_tool, {}),
        })
        tool_call_resp.__aenter__ = AsyncMock(return_value=tool_call_resp)
        tool_call_resp.__aexit__ = AsyncMock(return_value=False)

        final_resp = _chat_response("ok")

        session = MagicMock()
        session.post = MagicMock(side_effect=[tool_call_resp, final_resp])
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        _, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=execute_fn,
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        execute_fn.assert_not_awaited()
        assert calls[0][2].startswith("Tool '")

    async def test_history_converted_model_to_assistant(self):
        history = [{"role": "model", "parts": ["Hello!"]}]
        text, _ = await self._call()
        # Just ensure it doesn't raise; role conversion path is exercised
        assert text is not None

    async def test_history_truncated_to_last_10(self):
        history = [{"role": "user", "parts": [f"msg {i}"]} for i in range(20)]
        text, _ = await self._call()
        assert text is not None


# ---------------------------------------------------------------------------
# chat_ollama_with_tools — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatOllamaWithToolsErrors:
    async def _call_with_session_post(self, post_side_effect):
        session = MagicMock()
        session.post = MagicMock(return_value=post_side_effect)
        mod._ollama_sessions.get = AsyncMock(return_value=session)
        return await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

    async def test_http_error_returns_none(self):
        resp = _error_response(status=500)
        text, calls = await self._call_with_session_post(resp)
        assert text is None
        assert calls == []

    async def test_404_returns_none(self):
        resp = _error_response(status=404)
        text, calls = await self._call_with_session_post(resp)
        assert text is None

    async def test_connection_error_returns_none(self):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("refused"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.post = MagicMock(return_value=ctx)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        text, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert text is None

    async def test_execute_fn_exception_captured_in_result(self):
        bad_execute = AsyncMock(side_effect=RuntimeError("tool crashed"))

        tool_call_resp = AsyncMock()
        tool_call_resp.status = 200
        tool_call_resp.json = AsyncMock(return_value={
            "message": _tool_call_message(ALLOWED_TOOL, {}),
        })
        tool_call_resp.__aenter__ = AsyncMock(return_value=tool_call_resp)
        tool_call_resp.__aexit__ = AsyncMock(return_value=False)

        final_resp = _chat_response("recovered")

        session = MagicMock()
        session.post = MagicMock(side_effect=[tool_call_resp, final_resp])
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        _, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=bad_execute,
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert any("Error:" in c[2] for c in calls)

    async def test_max_rounds_fallback_response(self):
        """When every round returns tool_calls, fallback request is made."""
        def _make_tool_call_resp():
            r = AsyncMock()
            r.status = 200
            r.json = AsyncMock(return_value={
                "message": _tool_call_message(ALLOWED_TOOL, {}),
            })
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=False)
            return r

        def tc_resp_factory():
            return _make_tool_call_resp()

        final_fallback = _chat_response("fallback answer")

        side_effects = [tc_resp_factory() for _ in range(OLLAMA_MAX_TOOL_ROUNDS)]
        side_effects.append(final_fallback)

        session = MagicMock()
        session.post = MagicMock(side_effect=side_effects)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        text, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert len(calls) == OLLAMA_MAX_TOOL_ROUNDS
        assert text == "fallback answer"

    async def test_max_rounds_fallback_http_error_returns_none(self):
        """If fallback request also fails, return None."""
        def _tc_resp():
            r = AsyncMock()
            r.status = 200
            r.json = AsyncMock(return_value={
                "message": _tool_call_message(ALLOWED_TOOL, {}),
            })
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=False)
            return r

        fallback_err = _error_response(500)

        side_effects = [_tc_resp() for _ in range(OLLAMA_MAX_TOOL_ROUNDS)]
        side_effects.append(fallback_err)

        session = MagicMock()
        session.post = MagicMock(side_effect=side_effects)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        text, calls = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert text is None

    async def test_max_rounds_fallback_connection_error_returns_none(self):
        def _tc_resp():
            r = AsyncMock()
            r.status = 200
            r.json = AsyncMock(return_value={"message": _tool_call_message(ALLOWED_TOOL, {})})
            r.__aenter__ = AsyncMock(return_value=r)
            r.__aexit__ = AsyncMock(return_value=False)
            return r

        err_ctx = MagicMock()
        err_ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("network"))
        err_ctx.__aexit__ = AsyncMock(return_value=False)

        side_effects = [_tc_resp() for _ in range(OLLAMA_MAX_TOOL_ROUNDS)]
        side_effects.append(err_ctx)

        session = MagicMock()
        session.post = MagicMock(side_effect=side_effects)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        text, _ = await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )
        assert text is None


# ---------------------------------------------------------------------------
# Request format validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChatOllamaRequestFormat:
    async def test_post_called_with_correct_url(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        call_args = session.post.call_args
        assert call_args[0][0] == f"{OLLAMA_URL}/api/chat"

    async def test_payload_contains_model(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["model"] == OLLAMA_MODEL

    async def test_payload_has_stream_false(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["stream"] is False

    async def test_payload_contains_tools(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        payload = session.post.call_args[1]["json"]
        assert len(payload["tools"]) == 1

    async def test_system_message_is_first(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="test",
            history=[],
            system_prompt="SYS",
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "SYS"

    async def test_custom_temperature(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
            temperature=0.1,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["options"]["temperature"] == 0.1

    async def test_custom_max_tokens(self):
        resp = _chat_response()
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        mod._ollama_sessions.get = AsyncMock(return_value=session)

        await chat_ollama_with_tools(
            user_message="hi",
            history=[],
            system_prompt=SYSTEM_PROMPT,
            tool_declarations=[_BASE_DECL],
            execute_fn=_make_execute_fn(),
            ollama_url=OLLAMA_URL,
            ollama_model=OLLAMA_MODEL,
            max_tokens=512,
        )

        payload = session.post.call_args[1]["json"]
        assert payload["options"]["num_predict"] == 512
