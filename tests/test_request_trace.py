"""Unit tests for Wave 9 RequestTrace dataclass and trace footer helpers."""
from __future__ import annotations

import importlib

from llm.trace import RequestTrace

# llm.chat is a lazy-exported function in llm/__init__.py, so the submodule
# must be loaded via importlib to avoid resolving it as the chat() callable.
_chat_mod = importlib.import_module("llm.chat")
_build_trace_footer = _chat_mod._build_trace_footer
_apply_trace_footer = _chat_mod._apply_trace_footer

# ── Trace dataclass tests ──────────────────────────────────────────────────


def test_request_trace_defaults():
    trace = RequestTrace()
    assert trace.model_used == ""
    assert trace.provider == ""
    assert trace.skills_invoked == []
    assert trace.routing_reason == ""
    assert trace.latency_ms == 0.0
    assert trace.mini_model_used is False


def test_request_trace_skills_invoked_is_independent():
    t1 = RequestTrace()
    t2 = RequestTrace()
    t1.skills_invoked.append("search_web")
    assert t2.skills_invoked == [], "Two instances must not share the same list"


def test_request_trace_mutation():
    trace = RequestTrace()
    trace.skills_invoked.append("weather_report")
    trace.mini_model_used = True
    assert "weather_report" in trace.skills_invoked
    assert trace.mini_model_used is True


# ── _build_trace_footer tests ──────────────────────────────────────────────


def test_footer_empty_when_debug_level_0():
    trace = RequestTrace(model_used="gpt-4o", provider="copilot")
    assert _build_trace_footer(trace, 0) == ""


def test_footer_empty_when_trace_is_none():
    assert _build_trace_footer(None, 1) == ""


def test_footer_shows_model_at_level_1():
    trace = RequestTrace(model_used="gpt-4o", provider="copilot")
    footer = _build_trace_footer(trace, 1)
    assert "gpt-4o" in footer
    assert "copilot" in footer


def test_footer_shows_mini_model_lightning():
    trace = RequestTrace(model_used="gpt-4o-mini", provider="openai", mini_model_used=True)
    footer = _build_trace_footer(trace, 1)
    assert "⚡" in footer


def test_footer_shows_skills_at_level_2():
    trace = RequestTrace(
        model_used="gpt-4o",
        skills_invoked=["search_web", "weather_report"],
    )
    footer = _build_trace_footer(trace, 2)
    assert "search_web" in footer
    assert "weather_report" in footer


def test_footer_shows_latency_at_level_2():
    trace = RequestTrace(model_used="gpt-4o", latency_ms=842.0)
    footer = _build_trace_footer(trace, 2)
    assert "842" in footer


def test_footer_no_skills_at_level_1():
    trace = RequestTrace(model_used="gpt-4o", skills_invoked=["search_web", "weather_report"])
    footer = _build_trace_footer(trace, 1)
    assert "search_web" not in footer
    assert "weather_report" not in footer


# ── _apply_trace_footer integration tests ─────────────────────────────────


def test_apply_footer_appends_to_text(monkeypatch):
    monkeypatch.setenv("SHOW_ROUTING_DEBUG", "1")
    trace = RequestTrace(model_used="gpt-4o", provider="copilot")
    result = _apply_trace_footer("Hello", trace)
    assert result.startswith("Hello")
    assert "gpt-4o" in result
    assert "_via" in result or "via" in result


def test_apply_footer_noop_when_debug_disabled(monkeypatch):
    monkeypatch.setenv("SHOW_ROUTING_DEBUG", "0")
    trace = RequestTrace(model_used="gpt-4o", provider="copilot")
    result = _apply_trace_footer("Hello", trace)
    assert result == "Hello"
