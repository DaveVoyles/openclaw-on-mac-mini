"""Tests for user_profile.py — structured user profile management."""

import copy
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

import user_profile as up

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_path(tmp_path, monkeypatch):
    """Redirect PROFILE_PATH to a temp location and patch atomic_write.

    Also resets DEFAULT_PROFILE each test because load_profile() uses a
    shallow copy ({**DEFAULT_PROFILE}), which lets nested dicts leak between
    tests when mutations happen to a returned profile's sub-objects.
    """
    path = tmp_path / "user_profile.json"
    monkeypatch.setattr(up, "PROFILE_PATH", path)
    monkeypatch.setattr(up, "atomic_write", lambda p, content: p.write_text(content))
    # Deep-copy so each test starts with fresh mutable containers
    monkeypatch.setattr(up, "DEFAULT_PROFILE", copy.deepcopy(up.DEFAULT_PROFILE))
    return path


# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


def test_load_profile_missing_file_returns_default(profile_path):
    result = up.load_profile()
    assert result == up.DEFAULT_PROFILE
    assert result is not up.DEFAULT_PROFILE  # must be a copy


def test_load_profile_existing_file_returns_parsed(profile_path):
    data = {**up.DEFAULT_PROFILE, "working_style": "async"}
    profile_path.write_text(json.dumps(data))
    result = up.load_profile()
    assert result["working_style"] == "async"


def test_load_profile_corrupt_json_returns_default(profile_path):
    profile_path.write_text("not valid json {{{{")
    result = up.load_profile()
    assert result == up.DEFAULT_PROFILE


def test_load_profile_empty_file_returns_default(profile_path):
    profile_path.write_text("")
    result = up.load_profile()
    assert result == up.DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# save_profile
# ---------------------------------------------------------------------------


def test_save_profile_writes_json(profile_path):
    profile = {**up.DEFAULT_PROFILE, "working_style": "solo"}
    up.save_profile(profile)
    saved = json.loads(profile_path.read_text())
    assert saved["working_style"] == "solo"


def test_save_profile_roundtrip(profile_path):
    profile = {**up.DEFAULT_PROFILE, "interests": ["climbing", "coding"]}
    up.save_profile(profile)
    loaded = up.load_profile()
    assert loaded["interests"] == ["climbing", "coding"]


# ---------------------------------------------------------------------------
# update_preference
# ---------------------------------------------------------------------------


def test_update_preference_sets_key(profile_path):
    up.update_preference("timezone", "US/Pacific")
    profile = up.load_profile()
    assert profile["preferences"]["timezone"] == "US/Pacific"


def test_update_preference_overwrites_existing(profile_path):
    up.update_preference("theme", "dark")
    up.update_preference("theme", "light")
    profile = up.load_profile()
    assert profile["preferences"]["theme"] == "light"


# ---------------------------------------------------------------------------
# add_interest
# ---------------------------------------------------------------------------


def test_add_interest_appends_new(profile_path):
    up.add_interest("photography")
    profile = up.load_profile()
    assert "photography" in profile["interests"]


def test_add_interest_no_duplicate(profile_path):
    up.add_interest("hiking")
    up.add_interest("hiking")
    profile = up.load_profile()
    assert profile["interests"].count("hiking") == 1


def test_add_interest_multiple_distinct(profile_path):
    up.add_interest("cooking")
    up.add_interest("gaming")
    profile = up.load_profile()
    assert "cooking" in profile["interests"]
    assert "gaming" in profile["interests"]


# ---------------------------------------------------------------------------
# add_context_note
# ---------------------------------------------------------------------------


def test_add_context_note_appends(profile_path):
    up.add_context_note("Prefers bullet points.")
    profile = up.load_profile()
    assert "Prefers bullet points." in profile["context_notes"]


def test_add_context_note_always_appends(profile_path):
    up.add_context_note("note A")
    up.add_context_note("note A")
    profile = up.load_profile()
    assert profile["context_notes"].count("note A") == 2


# ---------------------------------------------------------------------------
# update_field
# ---------------------------------------------------------------------------


def test_update_field_known_field(profile_path):
    up.update_field("working_style", "pair programming")
    profile = up.load_profile()
    assert profile["working_style"] == "pair programming"


def test_update_field_unknown_field_ignored(profile_path):
    up.update_field("nonexistent_field", "value")
    profile = up.load_profile()
    assert "nonexistent_field" not in profile


def test_update_field_communication_style(profile_path):
    up.update_field("communication_style", "concise")
    profile = up.load_profile()
    assert profile["communication_style"] == "concise"


# ---------------------------------------------------------------------------
# get_profile_prompt
# ---------------------------------------------------------------------------


def test_get_profile_prompt_empty_profile_returns_empty(profile_path):
    result = up.get_profile_prompt()
    assert result == ""


def test_get_profile_prompt_with_preferences(profile_path):
    up.update_preference("timezone", "UTC")
    result = up.get_profile_prompt()
    assert "[User Profile]" in result
    assert "timezone=UTC" in result


def test_get_profile_prompt_with_interests(profile_path):
    up.add_interest("Python")
    result = up.get_profile_prompt()
    assert "Interests:" in result
    assert "Python" in result


def test_get_profile_prompt_with_tools(profile_path):
    up.update_field("tools", ["vim", "tmux"])
    result = up.get_profile_prompt()
    assert "Tools:" in result
    assert "vim" in result


def test_get_profile_prompt_with_working_style(profile_path):
    up.update_field("working_style", "deep focus sessions")
    result = up.get_profile_prompt()
    assert "Working style:" in result
    assert "deep focus sessions" in result


def test_get_profile_prompt_includes_context_notes(profile_path):
    up.add_context_note("Works remotely")
    result = up.get_profile_prompt()
    assert "Context notes:" in result
    assert "Works remotely" in result


def test_get_profile_prompt_format_starts_with_header(profile_path):
    up.update_preference("lang", "en")
    result = up.get_profile_prompt()
    assert result.startswith("[User Profile]")
    assert result.endswith("\n")


# ---------------------------------------------------------------------------
# learn_from_message (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learn_from_message_updates_preferences(profile_path):
    monkeypatch_save = {}

    def fake_save(p):
        monkeypatch_save.update(p)

    original_save = up.save_profile
    up.save_profile = fake_save

    async def fake_chat(prompt, **kwargs):
        return ('{"preferences": {"timezone": "UTC"}}', [], "test-model")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    try:
        with patch.dict(sys.modules, {"llm": fake_llm}):
            result = await up.learn_from_message("I am in UTC", "ok")
        assert any("timezone" in item for item in result)
    finally:
        up.save_profile = original_save


@pytest.mark.asyncio
async def test_learn_from_message_returns_empty_on_empty_json(profile_path):
    async def fake_chat(prompt, **kwargs):
        return ("{}", [], "test-model")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    with patch.dict(sys.modules, {"llm": fake_llm}):
        result = await up.learn_from_message("nothing personal", "ok")
    assert result == []


@pytest.mark.asyncio
async def test_learn_from_message_silent_on_error(profile_path):
    async def fake_chat(prompt, **kwargs):
        raise RuntimeError("LLM exploded")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    with patch.dict(sys.modules, {"llm": fake_llm}):
        result = await up.learn_from_message("crash test", "ok")
    assert result == []


@pytest.mark.asyncio
async def test_learn_from_message_learns_interests(profile_path):
    async def fake_chat(prompt, **kwargs):
        return ('{"interests": ["cycling", "baking"]}', [], "test-model")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    with patch.dict(sys.modules, {"llm": fake_llm, "vector_store": MagicMock()}):
        result = await up.learn_from_message("I love cycling and baking", "cool")
    assert any("cycling" in item for item in result)
    assert any("baking" in item for item in result)


@pytest.mark.asyncio
async def test_learn_from_message_learns_tools(profile_path):
    async def fake_chat(prompt, **kwargs):
        return ('{"tools": ["neovim", "zsh"]}', [], "test-model")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    with patch.dict(sys.modules, {"llm": fake_llm, "vector_store": MagicMock()}):
        result = await up.learn_from_message("I use neovim and zsh", "nice")
    assert any("neovim" in item for item in result)


@pytest.mark.asyncio
async def test_learn_from_message_strips_markdown_fences(profile_path):
    async def fake_chat(prompt, **kwargs):
        return ('```json\n{"preferences": {"lang": "en"}}\n```', [], "test-model")

    fake_llm = MagicMock()
    fake_llm.chat = fake_chat

    with patch.dict(sys.modules, {"llm": fake_llm, "vector_store": MagicMock()}):
        result = await up.learn_from_message("I speak English", "great")
    assert any("lang" in item for item in result)
