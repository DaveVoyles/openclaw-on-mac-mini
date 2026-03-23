"""
Tests for memory.py — Conversation and ConversationStore.

These are pure in-memory data structures; no external dependencies.
"""

import time
import pytest

from memory import (
    Conversation,
    ConversationStore,
    MAX_HISTORY_LENGTH,
    CONTEXT_TTL,
)


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class TestConversation:
    def test_init_defaults(self):
        conv = Conversation("Alice")
        assert conv.history == []
        assert conv.user_name == "Alice"
        assert not conv.is_expired

    def test_init_default_user_name(self):
        conv = Conversation()
        assert conv.user_name == "User"

    def test_add_user_message_appends_entry(self):
        conv = Conversation()
        conv.add_user_message("Hello")
        assert conv.message_count == 1
        assert conv.history[0] == {"role": "user", "parts": ["Hello"]}

    def test_add_user_message_updates_last_active(self):
        conv = Conversation()
        before = conv.last_active
        time.sleep(0.01)
        conv.add_user_message("Hi")
        assert conv.last_active > before

    def test_add_multiple_messages(self):
        conv = Conversation()
        conv.add_user_message("msg1")
        conv.add_user_message("msg2")
        assert conv.message_count == 2

    def test_trim_keeps_last_n_messages(self):
        conv = Conversation()
        for i in range(MAX_HISTORY_LENGTH + 5):
            conv.history.append({"role": "user", "parts": [str(i)]})
        conv._trim()
        assert len(conv.history) == MAX_HISTORY_LENGTH
        # The oldest messages were dropped; last entry should be the most recent
        assert conv.history[-1]["parts"][0] == str(MAX_HISTORY_LENGTH + 4)

    def test_add_many_messages_auto_trims(self):
        conv = Conversation()
        for i in range(MAX_HISTORY_LENGTH + 3):
            conv.add_user_message(f"msg {i}")
        assert conv.message_count == MAX_HISTORY_LENGTH

    def test_update_from_llm_replaces_history(self):
        conv = Conversation()
        conv.add_user_message("Hello")
        new_history = [
            {"role": "user", "parts": ["Hello"]},
            {"role": "model", "parts": ["Hi there!"]},
        ]
        conv.update_from_llm(new_history)
        assert conv.history == new_history
        assert conv.message_count == 2

    def test_update_from_llm_updates_last_active(self):
        conv = Conversation()
        before = conv.last_active
        time.sleep(0.01)
        conv.update_from_llm([{"role": "model", "parts": ["ok"]}])
        assert conv.last_active > before

    def test_is_not_expired_when_fresh(self):
        conv = Conversation()
        assert not conv.is_expired

    def test_is_expired_after_ttl(self):
        conv = Conversation()
        conv.last_active = time.monotonic() - CONTEXT_TTL - 1
        assert conv.is_expired

    def test_is_not_expired_just_before_ttl(self):
        conv = Conversation()
        conv.last_active = time.monotonic() - CONTEXT_TTL + 5
        assert not conv.is_expired

    def test_clear_empties_history(self):
        conv = Conversation()
        conv.add_user_message("test")
        conv.clear()
        assert conv.message_count == 0

    def test_clear_resets_last_active(self):
        conv = Conversation()
        conv.last_active = time.monotonic() - 1000
        conv.clear()
        assert (time.monotonic() - conv.last_active) < 1

    def test_message_count_property(self):
        conv = Conversation()
        assert conv.message_count == 0
        conv.add_user_message("one")
        assert conv.message_count == 1

    def test_age_minutes_roughly_correct(self):
        conv = Conversation()
        conv.last_active = time.monotonic() - 120  # 2 minutes ago
        assert abs(conv.age_minutes - 2.0) < 0.1


# ---------------------------------------------------------------------------
# ConversationStore
# ---------------------------------------------------------------------------


class TestConversationStore:
    def test_get_creates_new_conversation(self):
        store = ConversationStore()
        conv = store.get(1, 100, "Alice")
        assert conv is not None
        assert conv.user_name == "Alice"

    def test_get_returns_same_conversation_for_same_key(self):
        store = ConversationStore()
        conv1 = store.get(1, 100, "Alice")
        conv1.add_user_message("hello")
        conv2 = store.get(1, 100, "Alice")
        assert conv2.message_count == 1  # Same object

    def test_get_is_isolated_per_user(self):
        store = ConversationStore()
        conv1 = store.get(1, 100, "Alice")
        conv2 = store.get(2, 100, "Bob")
        assert conv1 is not conv2

    def test_get_is_isolated_per_channel(self):
        store = ConversationStore()
        conv1 = store.get(1, 100)
        conv2 = store.get(1, 200)
        assert conv1 is not conv2

    def test_get_creates_fresh_conversation_on_expiry(self):
        store = ConversationStore()
        conv1 = store.get(1, 100)
        conv1.add_user_message("old message")
        conv1.last_active = time.monotonic() - CONTEXT_TTL - 1
        conv2 = store.get(1, 100)
        assert conv2.message_count == 0  # New conversation

    def test_clear_user_empties_conversation(self):
        store = ConversationStore()
        conv = store.get(1, 100)
        conv.add_user_message("hello")
        store.clear_user(1, 100)
        assert conv.message_count == 0

    def test_clear_user_nonexistent_does_not_raise(self):
        store = ConversationStore()
        store.clear_user(999, 999)  # Should not raise

    def test_clear_all_removes_all_conversations(self):
        store = ConversationStore()
        store.get(1, 100)
        store.get(2, 200)
        store.clear_all()
        assert store.active_count == 0

    def test_active_count_counts_non_expired(self):
        store = ConversationStore()
        store.get(1, 100)
        store.get(2, 200)
        assert store.active_count == 2

    def test_active_count_excludes_expired(self):
        store = ConversationStore()
        conv1 = store.get(1, 100)
        store.get(2, 200)
        conv1.last_active = time.monotonic() - CONTEXT_TTL - 1
        assert store.active_count == 1

    def test_cleanup_expired_removes_expired_entries(self):
        store = ConversationStore()
        conv = store.get(1, 100)
        conv.last_active = time.monotonic() - CONTEXT_TTL - 1
        store.get(2, 200)  # Active
        store.cleanup_expired()
        assert len(store._conversations) == 1

    def test_cleanup_expired_keeps_active_entries(self):
        store = ConversationStore()
        store.get(1, 100)
        store.get(2, 200)
        store.cleanup_expired()
        assert len(store._conversations) == 2

    def test_stats_returns_formatted_string(self):
        store = ConversationStore()
        store.get(1, 100)
        result = store.stats()
        assert "active" in result
        assert "conversations" in result
        assert "messages" in result
