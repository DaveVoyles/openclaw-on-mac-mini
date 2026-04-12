"""Tests for provider-agnostic tool orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tool_orchestration import (
    DirectTextResponse,
    GeminiToolAdapter,
    ToolCallRequest,
    ToolCallResult,
    ToolOrchestrator,
    build_tool_provider_context,
)


class _FakeAdapter:
    def __init__(self, calls=None, latest_query: str = "", next_response=None):
        self._calls = list(calls or [])
        self._latest_query = latest_query
        self._next_response = next_response or {"status": "done"}
        self.sent_messages: list[object] = []

    def extract_tool_calls(self, response):
        return list(getattr(response, "tool_calls", self._calls))

    def latest_user_query(self, session):
        return self._latest_query

    def build_tool_result_message(self, tool_results: list[ToolCallResult]):
        return {"tool_results": tool_results}

    async def send_tool_result_message(self, session, message):
        self.sent_messages.append(message)
        return self._next_response

    def build_direct_text_response(self, text: str):
        return DirectTextResponse(text)

    def extract_final_text(self, response, rounds, session, *, max_rounds: int):
        return str(getattr(response, "text", ""))

    def extract_history(self, session):
        return []


def test_build_tool_provider_context_returns_gemini_context(monkeypatch):
    fake_session = MagicMock()

    def _fake_create_session(self, *, model, history):
        assert model.model_name == "models/gemini-2.5-flash"
        assert history == [{"role": "user", "parts": ["hello"]}]
        return fake_session

    monkeypatch.setattr(GeminiToolAdapter, "create_session", _fake_create_session)

    context = build_tool_provider_context(
        "gemini",
        model=SimpleNamespace(model_name="models/gemini-2.5-flash"),
        history=[{"role": "user", "parts": ["hello"]}],
    )

    assert context.provider == "gemini"
    assert context.model_name == "models/gemini-2.5-flash"
    assert context.session is fake_session
    assert isinstance(context.adapter, GeminiToolAdapter)


def test_merge_direct_final_history_replaces_trailing_model_placeholder():
    adapter = GeminiToolAdapter()

    history = [
        {"role": "user", "parts": ["Show me today's lacrosse slate"]},
        {"role": "model", "parts": ["[Called generate_sports_watch_report]"]},
    ]

    merged = adapter.merge_direct_final_history(
        history,
        "| Matchup |\n| --- |\n| UNC vs Notre Dame |\n\n_via perplexity-direct_",
    )

    assert merged == [
        {"role": "user", "parts": ["Show me today's lacrosse slate"]},
        {
            "role": "model",
            "parts": ["| Matchup |\n| --- |\n| UNC vs Notre Dame |\n\n_via perplexity-direct_"],
        },
    ]


def test_merge_direct_final_history_appends_when_history_does_not_end_with_model():
    adapter = GeminiToolAdapter()

    merged = adapter.merge_direct_final_history(
        [{"role": "user", "parts": ["hello"]}],
        "final answer",
    )

    assert merged == [
        {"role": "user", "parts": ["hello"]},
        {"role": "model", "parts": ["final answer"]},
    ]


def test_merged_direct_final_history_is_reused_for_followup_sessions(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create_session(self, *, model, history):
        captured["model_name"] = model.model_name
        captured["history"] = history
        return MagicMock()

    monkeypatch.setattr(GeminiToolAdapter, "create_session", _fake_create_session)

    adapter = GeminiToolAdapter()
    merged_history = adapter.merge_direct_final_history(
        [
            {"role": "user", "parts": ["Show me today's lacrosse slate"]},
            {"role": "model", "parts": ["[Called generate_sports_watch_report]"]},
        ],
        "final direct sports answer",
    )

    build_tool_provider_context(
        "gemini",
        model=SimpleNamespace(model_name="models/gemini-2.5-flash", config={}),
        history=merged_history,
    )

    assert captured["model_name"] == "models/gemini-2.5-flash"
    assert captured["history"] == [
        {"role": "user", "parts": ["Show me today's lacrosse slate"]},
        {"role": "model", "parts": ["final direct sports answer"]},
    ]


@pytest.mark.asyncio
async def test_tool_orchestrator_executes_through_adapter_contract():
    response = MagicMock()
    response.tool_calls = [ToolCallRequest("lookup_status", {"service": "openclaw"})]
    next_response = MagicMock()
    next_response.tool_calls = []
    adapter = _FakeAdapter(next_response=next_response)
    rate_limiter = MagicMock()
    rate_limiter.check.return_value = True
    execute_tool_call = AsyncMock(return_value="service is healthy")
    record_usage = AsyncMock()

    orchestrator = ToolOrchestrator(
        adapter=adapter,
        execute_tool_call=execute_tool_call,
        rate_limiter=rate_limiter,
        record_usage=record_usage,
        should_return_tool_result_directly=lambda _name, _result: False,
    )

    final_response, rounds = await orchestrator.run(
        session=MagicMock(),
        response=response,
        max_rounds=3,
        parallel=True,
        label="test",
    )

    assert final_response is next_response
    assert rounds == 1
    execute_tool_call.assert_awaited_once_with("lookup_status", {"service": "openclaw"})
    rate_limiter.record.assert_called_once()
    record_usage.assert_awaited_once_with(next_response)
    assert adapter.sent_messages
    tool_result = adapter.sent_messages[0]["tool_results"][0]
    assert tool_result == ToolCallResult(
        name="lookup_status",
        args={"service": "openclaw"},
        result="service is healthy",
    )


@pytest.mark.asyncio
async def test_tool_orchestrator_returns_direct_result_without_provider_rewrite():
    response = MagicMock()
    response.tool_calls = [ToolCallRequest("generate_sports_watch_report", {})]
    adapter = _FakeAdapter()
    rate_limiter = MagicMock()
    execute_tool_call = AsyncMock(
        return_value="| Matchup |\n| --- |\n| UNC vs Notre Dame |\n\n_via perplexity-direct_"
    )

    orchestrator = ToolOrchestrator(
        adapter=adapter,
        execute_tool_call=execute_tool_call,
        rate_limiter=rate_limiter,
        record_usage=AsyncMock(),
        should_return_tool_result_directly=lambda name, result: name == "generate_sports_watch_report" and "_via perplexity-direct_" in result,
    )

    final_response, rounds = await orchestrator.run(
        session=MagicMock(),
        response=response,
        max_rounds=3,
        parallel=True,
        label="test",
    )

    assert isinstance(final_response, DirectTextResponse)
    assert "_via perplexity-direct_" in final_response.text
    assert rounds == 1
    rate_limiter.record.assert_called_once()
    assert adapter.sent_messages == []


@pytest.mark.asyncio
async def test_tool_orchestrator_backfills_sports_query_before_execution():
    response = MagicMock()
    response.tool_calls = [
        ToolCallRequest(
            "generate_sports_watch_report",
            {"sport": "lacrosse", "league": "NCAA Division 1"},
        )
    ]
    adapter = _FakeAdapter(latest_query="Show me the men's division 1 lacrosse games today")
    execute_tool_call = AsyncMock(return_value="sports result")
    rate_limiter = MagicMock()
    rate_limiter.check.return_value = True

    orchestrator = ToolOrchestrator(
        adapter=adapter,
        execute_tool_call=execute_tool_call,
        rate_limiter=rate_limiter,
        record_usage=AsyncMock(),
        should_return_tool_result_directly=lambda _name, _result: False,
    )

    await orchestrator.run(
        session=MagicMock(),
        response=response,
        max_rounds=3,
        parallel=True,
        label="test",
    )

    execute_tool_call.assert_awaited_once_with(
        "generate_sports_watch_report",
        {
            "sport": "lacrosse",
            "league": "NCAA Division 1",
            "query": "Show me the men's division 1 lacrosse games today",
        },
    )
