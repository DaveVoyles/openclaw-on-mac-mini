import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_genai_mock = MagicMock()
_genai_mock.types.GenerateContentConfig = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _genai_mock)
sys.modules.setdefault("google.genai.types", _genai_mock.types)

import llm.context as ctx  # noqa: E402


def test_extract_cross_channel_opt_in_strips_marker():
    cleaned, enabled = ctx._extract_cross_channel_opt_in("Find this --cross-channel")
    assert enabled is True
    assert cleaned == "Find this"


@pytest.mark.asyncio
async def test_auto_recall_context_passes_cross_channel_flag(monkeypatch):
    captured = {}

    async def _recall_for_context(query, **kwargs):
        captured["query"] = query
        captured["kwargs"] = kwargs
        return ""

    monkeypatch.setattr(ctx.cfg, "auto_recall_enabled", True)
    monkeypatch.setitem(
        sys.modules,
        "vector_store",
        SimpleNamespace(recall_for_context=_recall_for_context),
    )
    monkeypatch.setitem(
        sys.modules,
        "user_profile",
        SimpleNamespace(get_profile_prompt=lambda: ""),
    )

    async def _rules(*args, **kwargs):
        return []

    monkeypatch.setitem(
        sys.modules,
        "rules_engine",
        SimpleNamespace(get_relevant_rules=_rules),
    )

    monkeypatch.setattr("runtime_state.get_current_channel_id", lambda: 111)
    monkeypatch.setattr("runtime_state.get_current_thread_id", lambda: 222)

    await ctx._auto_recall_context("hello", cross_channel=True)

    assert captured["query"] == "hello"
    assert captured["kwargs"]["cross_channel"] is True
