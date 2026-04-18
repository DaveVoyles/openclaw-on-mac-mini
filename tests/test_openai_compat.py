"""
Tests for OpenAI-compatible /v1/ endpoints in discord_web.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ---------------------------------------------------------------------------
# Helpers to build a minimal aiohttp app with just the /v1/ routes
# ---------------------------------------------------------------------------

def _make_app():
    """Build a minimal aiohttp app with just the /v1/ handlers."""
    from discord_web import _v1_chat_completions_handler, _v1_models_handler

    app = web.Application()
    app.router.add_get("/v1/models", _v1_models_handler)
    app.router.add_post("/v1/chat/completions", _v1_chat_completions_handler)
    return app


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(_make_app())


# ---------------------------------------------------------------------------
# /v1/models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_v1_models_structure(client):
    resp = await client.get("/v1/models")
    assert resp.status == 200
    data = await resp.json()
    assert data["object"] == "list"
    ids = {m["id"] for m in data["data"]}
    assert ids == {
        "openclaw-auto",
        "openclaw-gemini",
        "openclaw-copilot",
        "openclaw-openai",
        "openclaw-anthropic",
    }


@pytest.mark.asyncio
async def test_v1_models_fields(client):
    resp = await client.get("/v1/models")
    data = await resp.json()
    for model in data["data"]:
        assert model["object"] == "model"
        assert model["owned_by"] == "openclaw"
        assert isinstance(model["created"], int)


# ---------------------------------------------------------------------------
# /v1/chat/completions — non-streaming
# ---------------------------------------------------------------------------

_FAKE_RESULT = {"response": "Hello from OpenClaw!", "model": "gemini-2.0", "tokens": 42}

_PATCH_TARGET = "dashboard.api_handlers._execute_agent_ask"


@pytest.mark.asyncio
async def test_chat_completions_basic(client):
    with patch(
        _PATCH_TARGET,
        new=AsyncMock(return_value=_FAKE_RESULT),
    ):
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "openclaw-auto",
                "messages": [{"role": "user", "content": "Say hello"}],
                "stream": False,
            },
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello from OpenClaw!"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 42
    assert data["model"] == "gemini-2.0"
    assert data["id"].startswith("chatcmpl-")


@pytest.mark.asyncio
async def test_chat_completions_model_mapping_auto(client):
    """openclaw-auto maps to model_pref='auto'."""
    captured: list[str] = []

    async def _fake_execute(*, prompt, model_pref, history, user_name, **kwargs):
        captured.append(model_pref)
        return _FAKE_RESULT

    with patch("dashboard.api_handlers._execute_agent_ask", new=_fake_execute):
        await client.post(
            "/v1/chat/completions",
            json={"model": "openclaw-auto", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert captured[0] == "auto"


@pytest.mark.asyncio
async def test_chat_completions_model_mapping_gemini(client):
    captured: list[str] = []

    async def _fake_execute(*, prompt, model_pref, history, user_name, **kwargs):
        captured.append(model_pref)
        return _FAKE_RESULT

    with patch("dashboard.api_handlers._execute_agent_ask", new=_fake_execute):
        await client.post(
            "/v1/chat/completions",
            json={"model": "openclaw-gemini", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert captured[0] == "gemini"


@pytest.mark.asyncio
async def test_chat_completions_unknown_model_maps_to_auto(client):
    captured: list[str] = []

    async def _fake_execute(*, prompt, model_pref, history, user_name, **kwargs):
        captured.append(model_pref)
        return _FAKE_RESULT

    with patch("dashboard.api_handlers._execute_agent_ask", new=_fake_execute):
        await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4-turbo", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert captured[0] == "auto"


@pytest.mark.asyncio
async def test_chat_completions_missing_messages(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "openclaw-auto", "messages": []},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_chat_completions_missing_prompt_content(client):
    """Last message has empty content → 400."""
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "openclaw-auto", "messages": [{"role": "user", "content": "   "}]},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_chat_completions_history_extraction(client):
    """All messages except the last are passed as history."""
    captured_history: list = []
    captured_prompt: list = []

    async def _fake_execute(*, prompt, model_pref, history, user_name, **kwargs):
        captured_prompt.append(prompt)
        captured_history.extend(history)
        return _FAKE_RESULT

    with patch("dashboard.api_handlers._execute_agent_ask", new=_fake_execute):
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "openclaw-auto",
                "messages": [
                    {"role": "user", "content": "First message"},
                    {"role": "assistant", "content": "First reply"},
                    {"role": "user", "content": "Final question"},
                ],
            },
        )

    assert captured_prompt[0] == "Final question"
    assert len(captured_history) == 2
    assert captured_history[0]["content"] == "First message"
    assert captured_history[1]["content"] == "First reply"


@pytest.mark.asyncio
async def test_chat_completions_invalid_json(client):
    resp = await client.post(
        "/v1/chat/completions",
        data=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
