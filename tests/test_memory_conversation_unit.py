"""Unit tests for memory_conversation.py — Conversation and ConversationStore classes."""

import time

from memory_conversation import Conversation, ConversationStore
from memory_helpers import CONTEXT_TTL, MAX_HISTORY_LENGTH

# ---------------------------------------------------------------------------
# Conversation — basic properties
# ---------------------------------------------------------------------------


class TestConversationInit:
    def test_default_user_name(self):
        conv = Conversation()
        assert conv.user_name == "User"

    def test_custom_user_name(self):
        conv = Conversation("Alice")
        assert conv.user_name == "Alice"

    def test_initial_history_is_empty(self):
        conv = Conversation()
        assert conv.history == []

    def test_initial_summarized_is_false(self):
        conv = Conversation()
        assert conv.summarized is False

    def test_not_expired_immediately(self):
        conv = Conversation()
        assert not conv.is_expired

    def test_message_count_zero_on_init(self):
        conv = Conversation()
        assert conv.message_count == 0


# ---------------------------------------------------------------------------
# Conversation — add_user_message
# ---------------------------------------------------------------------------


class TestConversationAddMessage:
    def test_appends_user_message(self):
        conv = Conversation()
        conv.add_user_message("Hello")
        assert conv.history[0] == {"role": "user", "parts": ["Hello"]}

    def test_memory_conversation_unit_updates_last_active(self):
        conv = Conversation()
        before = conv.last_active
        time.sleep(0.01)
        conv.add_user_message("Hi")
        assert conv.last_active > before

    def test_message_count_increments(self):
        conv = Conversation()
        conv.add_user_message("one")
        conv.add_user_message("two")
        assert conv.message_count == 2


# ---------------------------------------------------------------------------
# Conversation — update_from_llm
# ---------------------------------------------------------------------------


class TestConversationUpdateFromLLM:
    def test_replaces_history(self):
        conv = Conversation()
        conv.add_user_message("old")
        new_history = [{"role": "user", "parts": ["new"]}]
        conv.update_from_llm(new_history)
        assert conv.history == new_history

    def test_memory_conversation_unit_updates_last_active_v2(self):
        conv = Conversation()
        before = conv.last_active
        time.sleep(0.01)
        conv.update_from_llm([{"role": "user", "parts": ["hi"]}])
        assert conv.last_active > before


# ---------------------------------------------------------------------------
# Conversation — _trim
# ---------------------------------------------------------------------------


class TestConversationTrim:
    def test_trim_keeps_max_history(self):
        conv = Conversation()
        for i in range(MAX_HISTORY_LENGTH + 5):
            conv.history.append({"role": "user", "parts": [str(i)]})
        conv._trim()
        assert len(conv.history) <= MAX_HISTORY_LENGTH

    def test_no_trim_when_within_limit(self):
        conv = Conversation()
        conv.history = [{"role": "user", "parts": ["hi"]}] * 3
        original = conv.history.copy()
        conv._trim()
        assert conv.history == original


# ---------------------------------------------------------------------------
# Conversation — clear
# ---------------------------------------------------------------------------


class TestConversationClear:
    def test_clears_history(self):
        conv = Conversation()
        conv.add_user_message("hi")
        conv.clear()
        assert conv.history == []

    def test_resets_summarized(self):
        conv = Conversation()
        conv.summarized = True
        conv.clear()
        assert conv.summarized is False

    def test_updates_last_active_on_clear(self):
        conv = Conversation()
        before = conv.last_active
        time.sleep(0.01)
        conv.clear()
        assert conv.last_active >= before


# ---------------------------------------------------------------------------
# Conversation — age_minutes / is_expired
# ---------------------------------------------------------------------------


class TestConversationExpiry:
    def test_age_minutes_is_nonnegative(self):
        conv = Conversation()
        assert conv.age_minutes >= 0

    def test_memory_conversation_unit_is_expired_after_ttl(self):
        conv = Conversation()
        # Simulate an old last_active by subtracting more than CONTEXT_TTL
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 10)
        assert conv.is_expired


# ---------------------------------------------------------------------------
# ConversationStore — in-memory delegation
# ---------------------------------------------------------------------------


class TestConversationStoreInMemory:
    def test_memory_conversation_unit_get_creates_new_conversation(self):
        cs = ConversationStore()
        conv = cs.get(1, 100, "Bob")
        assert conv is not None
        assert isinstance(conv, Conversation)

    def test_memory_conversation_unit_get_returns_same_conversation_for_same_key(self):
        cs = ConversationStore()
        conv1 = cs.get(1, 100, "Bob")
        conv2 = cs.get(1, 100, "Bob")
        assert conv1 is conv2

    def test_get_returns_different_for_different_channel(self):
        cs = ConversationStore()
        conv1 = cs.get(1, 100, "Bob")
        conv2 = cs.get(1, 200, "Bob")
        assert conv1 is not conv2

    def test_clear_user_empties_history(self):
        cs = ConversationStore()
        conv = cs.get(1, 100)
        conv.add_user_message("hello")
        cs.clear_user(1, 100)
        assert cs.get(1, 100).message_count == 0 or cs.get(1, 100).history == []

    def test_clear_all_removes_conversations(self):
        cs = ConversationStore()
        cs.get(1, 100)
        cs.get(2, 200)
        cs.clear_all()
        assert cs.active_count == 0

    def test_memory_conversation_unit_active_count_excludes_expired(self):
        cs = ConversationStore()
        conv = cs.get(1, 100)
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 10)
        assert cs.active_count == 0

    def test_memory_conversation_unit_stats_returns_string(self):
        cs = ConversationStore()
        result = cs.stats()
        assert isinstance(result, str)

    def test_conversations_property_backward_compat(self):
        cs = ConversationStore()
        assert isinstance(cs._conversations, dict)


# ---------------------------------------------------------------------------
# ConversationStore — thread delegation
# ---------------------------------------------------------------------------


class TestConversationStoreThreads:
    def test_save_thread_no_history_returns_error(self):
        cs = ConversationStore()
        result = cs.save_thread(1, 100, "mythread")
        assert "❌" in result

    def test_list_threads_no_threads_returns_message(self):
        cs = ConversationStore()
        result = cs.list_threads(999)
        assert isinstance(result, str)
        assert "No saved threads" in result or "📂" in result

    def test_delete_thread_invalid_name_returns_error(self):
        cs = ConversationStore()
        result = cs.delete_thread(1, "bad name!!")
        assert "❌" in result
