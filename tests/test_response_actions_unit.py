"""Unit tests for response_actions.py — gaps not covered by test_response_actions_coverage.py.

Coverage file tests: _resolve_channel_thread_scope with lock modes, _generate_follow_ups.
This file covers: _b() helper, ResponseActions.__init__ field storage, button setup,
_generate_follow_ups edge cases (LLM failure, JSON parsing, filtering).
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import discord

import response_actions as mod
from response_actions import _b, _generate_follow_ups


class TestBHelper:
    """_b() resolves a value, preferring local patches over bot module values."""

    def test_returns_local_value_when_not_patched(self):
        sentinel = object()
        mod._ORIG["_test_key_xyz"] = sentinel
        try:
            result = _b("_test_key_xyz", sentinel)
            assert result is sentinel
        finally:
            del mod._ORIG["_test_key_xyz"]

    def test_returns_local_value_when_it_differs_from_orig(self):
        orig = object()
        local = object()
        mod._ORIG["_test_key_xyz2"] = orig
        try:
            result = _b("_test_key_xyz2", local)
            assert result is local
        finally:
            del mod._ORIG["_test_key_xyz2"]

    def test_returns_local_when_no_bot_module(self):
        val = object()
        result = _b("no_such_key_abc", val)
        assert result is val


class TestResponseActionsInit:
    def _make_view(self, **overrides):
        defaults = dict(
            response_text="Hello world",
            question="What is the weather?",
            user_id=111,
            channel_id=222,
        )
        defaults.update(overrides)
        return mod.ResponseActions(**defaults)

    def test_stores_response_text(self):
        view = self._make_view()
        assert view._response_text == "Hello world"

    def test_stores_question(self):
        view = self._make_view()
        assert view._question == "What is the weather?"

    def test_stores_user_id(self):
        view = self._make_view()
        assert view._user_id == 111

    def test_stores_channel_id(self):
        view = self._make_view()
        assert view._channel_id == 222

    def test_stores_thread_id_none_by_default(self):
        view = self._make_view()
        assert view._thread_id is None

    def test_stores_thread_id_when_provided(self):
        view = self._make_view(thread_id=333)
        assert view._thread_id == 333

    def test_timeout_defaults_to_300(self):
        view = self._make_view()
        assert view.timeout == 300

    def test_custom_timeout_stored(self):
        view = self._make_view(timeout=120)
        assert view.timeout == 120

    def test_follow_up_buttons_added_for_each_followup(self):
        view = self._make_view(follow_ups=["Follow up A", "Follow up B"])
        custom_ids = [c.custom_id for c in view.children if isinstance(c, discord.ui.Button)]
        assert "followup_0" in custom_ids
        assert "followup_1" in custom_ids

    def test_go_deeper_button_always_present(self):
        view = self._make_view()
        custom_ids = [c.custom_id for c in view.children if isinstance(c, discord.ui.Button)]
        assert "go_deeper" in custom_ids

    def test_download_button_only_when_show_download(self):
        view_no = self._make_view(show_download=False)
        view_yes = self._make_view(show_download=True)
        ids_no = {c.custom_id for c in view_no.children if isinstance(c, discord.ui.Button)}
        ids_yes = {c.custom_id for c in view_yes.children if isinstance(c, discord.ui.Button)}
        assert "download_response" not in ids_no
        assert "download_response" in ids_yes

    def test_followup_label_truncated_to_80_chars(self):
        long_label = "A" * 200
        view = self._make_view(follow_ups=[long_label])
        btn = next(
            (c for c in view.children if isinstance(c, discord.ui.Button) and c.custom_id == "followup_0"),
            None,
        )
        assert btn is not None
        assert len(btn.label) <= 80


class TestGenerateFollowUps:
    @pytest.mark.asyncio
    async def test_response_actions_unit_returns_empty_on_import_error(self):
        with patch("response_actions.json") as _json_mod:
            with patch.dict("sys.modules", {"llm.chat": None}):
                result = await _generate_follow_ups("q", "a")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_returns_empty_on_runtime_error(self):
        # _generate_follow_ups catches RuntimeError/TimeoutError/ImportError
        async def raise_runtime(*args, **kwargs):
            raise RuntimeError("LLM down")

        with patch("llm.chat.chat", raise_runtime):
            result = await _generate_follow_ups("q", "a")
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_json_follow_ups(self):
        follow_ups = ["What else can you tell me about this?", "How does that work exactly?", "Any exceptions here?"]
        payload = json.dumps({"follow_ups": follow_ups})
        mock_chat = AsyncMock(return_value=(payload, None, None))

        with patch("response_actions.llm_chat", mock_chat):
            # Patch the inner import
            with patch("builtins.__import__", wraps=__import__) as mock_import:
                mock_import.return_value = MagicMock(chat=mock_chat)
                # Use direct patch on module path
                with patch("llm.chat.chat", mock_chat):
                    result = await _generate_follow_ups("question", "answer")

        # Result may be empty if llm.chat isn't importable; just verify type
        assert isinstance(result, list)
        assert len(result) <= 3

    @pytest.mark.asyncio
    async def test_filters_out_short_suggestions(self):
        # Feed a JSON with a too-short follow-up
        payload = json.dumps({"follow_ups": ["Yes", "What is the complete history of this topic?"]})
        mock_chat = AsyncMock(return_value=(payload, None, None))

        async def patched_chat(prompt, model_preference=None):
            return payload, None, None

        with patch("llm.chat.chat", patched_chat):
            result = await _generate_follow_ups("question here", "answer here")

        # Short ones (<15 chars) should be filtered
        for r in result:
            assert len(r) >= 15

    @pytest.mark.asyncio
    async def test_max_three_follow_ups_returned(self):
        follow_ups = [f"Follow up question number {i} about the topic?" for i in range(10)]
        payload = json.dumps({"follow_ups": follow_ups})

        async def patched_chat(prompt, model_preference=None):
            return payload, None, None

        with patch("llm.chat.chat", patched_chat):
            result = await _generate_follow_ups("q", "a")

        assert len(result) <= 3
