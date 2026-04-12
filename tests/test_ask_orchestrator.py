import pytest

import ask_orchestrator as mod
from ask_orchestrator import (
    apply_repair_budget,
    apply_retrieval_budget,
    normalize_model_preference,
    run_ask_stream,
    select_latency_budget_policy,
)


def test_normalize_model_preference_upgrades_local_when_tools_required():
    model, upgraded = normalize_model_preference(
        "check my calendar tomorrow",
        "local",
        lambda message: "calendar" in message,
    )
    assert model == "gemini"
    assert upgraded is True


def test_normalize_model_preference_keeps_requested_model_when_no_upgrade():
    model, upgraded = normalize_model_preference(
        "hello there",
        "auto",
        lambda _message: False,
    )
    assert model == "auto"
    assert upgraded is False


def test_normalize_model_preference_maps_claude_alias_to_anthropic():
    model, upgraded = normalize_model_preference(
        "hello there",
        "claude",
        lambda _message: False,
    )
    assert model == "anthropic"
    assert upgraded is False


def test_normalize_model_preference_upgrades_copilot_when_tools_required():
    model, upgraded = normalize_model_preference(
        "check my calendar tomorrow",
        "copilot",
        lambda message: "calendar" in message,
    )
    assert model == "gemini"
    assert upgraded is True


@pytest.mark.asyncio
async def test_run_ask_stream_collects_metadata_and_updates_history():
    saved_history: list[list[dict]] = []

    async def fake_stream(**kwargs):
        assert kwargs["user_message"] == "status report"
        assert kwargs["model_preference"] == "gemini"
        assert kwargs["on_tool_call"] == "tool-callback"
        assert kwargs["context_controls"] == {"scope": "cross-channel", "reset_context": True}
        yield "partial", False, {"context_badge": "🌐 Cross-channel"}
        yield "final response", True, {
            "model_used": "models/gemini-2.5-pro",
            "routing_notes": ["Tool shortlist: create_status_report"],
            "explainability_note": "Cross-channel",
            "context_quality": {
                "compression_ratio": 0.41,
                "retained_key_facts_count": 6,
            },
            "updated_history": [{"role": "model", "parts": ["final response"]}],
            "context_badge": "🌐 Cross-channel",
        }

    partial_chunks: list[str] = []
    finalized: list[tuple[str, str]] = []

    result = await run_ask_stream(
        llm_stream=fake_stream,
        user_message="status report",
        history=[],
        user_name="Dave",
        model_preference="gemini",
        channel_id=123,
        thread_id=456,
        user_id="42",
        on_tool_call="tool-callback",
        on_partial_chunk=lambda chunk: _record_chunk(partial_chunks, chunk),
        on_finalized=lambda model, text: finalized.append((model, text)),
        update_history=lambda history: saved_history.append(history),
        context_controls={"scope": "cross-channel", "reset_context": True},
    )

    assert result.response_text == "final response"
    assert result.model_used == "models/gemini-2.5-pro"
    assert result.routing_notes == ["Tool shortlist: create_status_report"]
    assert result.context_badges == ["🌐 Cross-channel"]
    assert result.context_explainability_note == "Cross-channel"
    assert result.context_quality == {
        "compression_ratio": 0.41,
        "retained_key_facts_count": 6,
    }
    assert saved_history == [[{"role": "model", "parts": ["final response"]}]]
    assert partial_chunks == ["partial"]
    assert finalized == [("models/gemini-2.5-pro", "final response")]


async def _record_chunk(chunks: list[str], chunk: str) -> None:
    chunks.append(chunk)


def test_select_latency_budget_policy_defaults_to_normal_mode_without_pressure(monkeypatch):
    activations: list[dict] = []
    monkeypatch.setattr(
        mod,
        "_record_degrade_mode_metric",
        lambda **kwargs: activations.append(kwargs),
    )
    policy = select_latency_budget_policy(
        profile_name="general",
        load_stats={"request_rate_rpm": 10.0, "p95_latency_ms": 150.0, "error_rate": 0.01},
    )
    assert policy["degrade_mode"] == "normal"
    assert policy["degrade_reasons"] == []
    assert policy["retrieval"]["degrade_mode"] == "normal"
    assert activations == []


def test_select_latency_budget_policy_enters_constrained_mode_for_timeout_and_sparsity(monkeypatch):
    activations: list[dict] = []
    monkeypatch.setattr(
        mod,
        "_record_degrade_mode_metric",
        lambda **kwargs: activations.append(kwargs),
    )
    policy = select_latency_budget_policy(
        profile_name="sports",
        load_stats={
            "request_rate_rpm": 15.0,
            "p95_latency_ms": 400.0,
            "error_rate": 0.01,
            "provider_timeout_rate": 0.2,
            "retrieval_sparsity_rate": 0.3,
        },
    )
    assert policy["degrade_mode"] == "constrained"
    assert "provider_timeout_rate" in policy["degrade_reasons"]
    assert "retrieval_sparsity_rate" in policy["degrade_reasons"]
    assert policy["retrieval"]["degrade_mode"] == "constrained"
    assert activations and activations[0]["mode"] == "constrained"


def test_constrained_policy_remains_bounded_for_retrieval_and_repair():
    policy = select_latency_budget_policy(
        profile_name="sports",
        load_stats={
            "request_rate_rpm": 25.0,
            "p95_latency_ms": 300.0,
            "error_rate": 0.02,
            "provider_timeout_rate": 0.25,
            "retrieval_sparsity_rate": 0.4,
        },
    )

    retrieval_budget = apply_retrieval_budget(
        min_results=8,
        max_query_variants=6,
        provider_attempt_cap=6,
        num_results=20,
        policy=policy,
    )
    repair_budget = apply_repair_budget(
        max_attempts=1,
        timeout_seconds=45,
        policy=policy,
    )

    assert 1 <= retrieval_budget["min_results"] <= 8
    assert 1 <= retrieval_budget["max_query_variants"] <= 6
    assert 1 <= retrieval_budget["provider_attempt_cap"] <= 6
    assert 0 <= repair_budget["max_attempts"] <= 1
    assert 8 <= repair_budget["timeout_seconds"] <= 45
