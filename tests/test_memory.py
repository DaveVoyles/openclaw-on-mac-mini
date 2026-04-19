"""
Tests for memory.py — Conversation and ConversationStore.

These are pure in-memory data structures; no external dependencies.
"""

import time

from memory import (
    CONTEXT_TTL,
    MAX_HISTORY_LENGTH,
    Conversation,
    ConversationStore,
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

    def test_memory_is_expired_after_ttl(self):
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
    def test_memory_get_creates_new_conversation(self):
        store = ConversationStore()
        conv = store.get(1, 100, "Alice")
        assert conv is not None
        assert conv.user_name == "Alice"

    def test_memory_get_returns_same_conversation_for_same_key(self):
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

    def test_memory_active_count_counts_non_expired(self):
        store = ConversationStore()
        store.get(1, 100)
        store.get(2, 200)
        assert store.active_count == 2

    def test_memory_active_count_excludes_expired(self):
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


# ===========================================================================
# _normalize_text / _message_text (pure helpers)
# ===========================================================================


import memory as _mem_mod
from memory import (
    _build_salience_summary,
    _extract_key_topics,
    _message_salience_score,
    _message_text,
    _normalize_text,
    _relative_age,
    get_model_preference,
    set_model_preference,
)


class TestNormalizeText:
    def test_strips_and_collapses(self):
        assert _normalize_text("  hello   world  ") == "hello world"

    def test_memory_none_returns_empty(self):
        assert _normalize_text(None) == ""  # type: ignore

    def test_memory_empty_string(self):
        assert _normalize_text("") == ""


class TestMessageText:
    def test_memory_joins_string_parts(self):
        assert _message_text({"parts": ["hello", " world"]}) == "hello world"

    def test_ignores_non_strings(self):
        assert _message_text({"parts": ["a", 99, None, "b"]}) == "a b"

    def test_missing_parts_key(self):
        assert _message_text({}) == ""


class TestMessageSalienceScore:
    def test_empty_returns_zero(self):
        assert _message_salience_score({"parts": []}, 0) == 0

    def test_question_adds_bonus(self):
        msg = {"role": "user", "parts": ["What is the plan?"]}
        assert _message_salience_score(msg, 0) >= 2

    def test_model_role_adds_one(self):
        msg = {"role": "model", "parts": ["Short."]}
        assert _message_salience_score(msg, 0) >= 1

    def test_memory_long_text_adds_bonus(self):
        long = "word " * 40
        assert _message_salience_score({"parts": [long]}, 0) >= 1

    def test_index_increases_score(self):
        msg = {"parts": ["some text"]}
        assert _message_salience_score(msg, 5) >= _message_salience_score(msg, 0)


class TestExtractKeyTopics:
    def test_most_frequent_word_returned(self):
        msgs = [{"parts": ["python python python code"]}, {"parts": ["python code"]}]
        assert "python" in _extract_key_topics(msgs, limit=5)

    def test_memory_stopwords_excluded(self):
        msgs = [{"parts": ["the the and the is"]}]
        topics = _extract_key_topics(msgs)
        assert "the" not in topics and "and" not in topics

    def test_empty_messages(self):
        assert _extract_key_topics([]) == []

    def test_memory_limit_respected(self):
        msgs = [{"parts": ["alpha beta gamma delta epsilon zeta"]}]
        assert len(_extract_key_topics(msgs, limit=2)) <= 2


class TestBuildSalienceSummary:
    def test_empty_returns_empty_string_and_dict(self):
        text, meta = _build_salience_summary([])
        assert text == "" and meta == {}

    def test_has_compressed_header(self):
        msgs = [
            {"role": "user", "parts": ["What is the database schema?"]},
            {"role": "model", "parts": ["Users and products tables."]},
        ]
        text, meta = _build_salience_summary(msgs)
        assert "Compressed Thread Context" in text

    def test_meta_has_expected_keys(self):
        msgs = [{"role": "user", "parts": ["Hello there question?"]}]
        _, meta = _build_salience_summary(msgs)
        for key in ("compression_applied", "drift_risk", "topic_retention_ratio"):
            assert key in meta


class TestRelativeAge:
    def test_under_60s(self):
        assert _relative_age(30) == "just now"

    def test_memory_minutes(self):
        assert "m ago" in _relative_age(120)

    def test_memory_hours(self):
        assert "h ago" in _relative_age(7200)

    def test_memory_days(self):
        assert "d ago" in _relative_age(86400 * 3)


class TestModelPreference:
    def test_default_is_auto(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "_PREFS_DIR", tmp_path)
        assert get_model_preference(9999) == "auto"

    def test_set_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "_PREFS_DIR", tmp_path)
        set_model_preference(9999, "gemini")
        assert get_model_preference(9999) == "gemini"

    def test_invalid_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "_PREFS_DIR", tmp_path)
        result = set_model_preference(9999, "badmodel")
        assert "❌" in result or "invalid" in result.lower()


class TestConversationStoreThreads:
    def test_save_invalid_name_rejected(self):
        from memory import ConversationStore
        store = ConversationStore()
        assert "❌" in store.save_thread(1, 100, "bad name!")

    def test_save_no_active_conv(self):
        from memory import ConversationStore
        store = ConversationStore()
        assert "❌" in store.save_thread(1, 100, "ok-name")

    def test_save_load_delete(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "THREADS_DIR", tmp_path)
        from memory import Conversation, ConversationStore
        store = ConversationStore()
        conv = Conversation("Alice")
        conv.history = [{"role": "user", "parts": ["hi"]}]
        store._conversations[(1, 100)] = conv

        assert "✅" in store.save_thread(1, 100, "mythread")
        store2 = ConversationStore()
        assert "✅" in store2.load_thread(1, 100, "mythread")
        assert "🗑️" in store2.delete_thread(1, "mythread")

    def test_memory_list_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "THREADS_DIR", tmp_path)
        from memory import ConversationStore
        result = ConversationStore().list_threads(1)
        assert "No saved threads" in result

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mem_mod, "THREADS_DIR", tmp_path)
        from memory import ConversationStore
        assert "❌" in ConversationStore().load_thread(1, 100, "ghost")
