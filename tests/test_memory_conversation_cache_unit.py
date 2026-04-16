"""Unit tests for memory_conversation_cache.py — ConversationCache class."""

import time
from unittest.mock import MagicMock, patch

import pytest

from memory_conversation import Conversation
from memory_conversation_cache import ConversationCache
from memory_helpers import CONTEXT_TTL, MIN_MESSAGES_TO_SUMMARIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache_with_conv(user_id=1, channel_id=100, user_name="TestUser"):
    """Return (cache, conv) with one pre-populated conversation."""
    cache = ConversationCache()
    with patch("memory_session._load_last_summary", return_value=""), \
         patch("memory_session.load_last_handover", return_value=""):
        conv = cache.get(user_id, channel_id, user_name)
    return cache, conv


# ---------------------------------------------------------------------------
# get — creates new conversation
# ---------------------------------------------------------------------------

class TestConversationCacheGet:
    def test_creates_new_conversation_when_absent(self):
        with patch("memory_session._load_last_summary", return_value=""), \
             patch("memory_session.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv = cache.get(1, 100, "Alice")
        assert conv is not None
        assert isinstance(conv, Conversation)

    def test_returns_same_instance_on_repeated_call(self):
        with patch("memory_session._load_last_summary", return_value=""), \
             patch("memory_session.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv1 = cache.get(1, 100, "Alice")
            conv2 = cache.get(1, 100, "Alice")
        assert conv1 is conv2

    def test_different_channel_produces_different_conversation(self):
        with patch("memory_session._load_last_summary", return_value=""), \
             patch("memory_session.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv1 = cache.get(1, 100)
            conv2 = cache.get(1, 200)
        assert conv1 is not conv2

    def test_recall_injected_when_summary_exists(self):
        with patch("memory_conversation_cache._load_last_summary", return_value="We discussed X"), \
             patch("memory_conversation_cache.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv = cache.get(1, 100)
        assert any("Recall from last session" in str(m) for m in conv.history)

    def test_handover_injected_when_handover_exists(self):
        with patch("memory_conversation_cache._load_last_summary", return_value=""), \
             patch("memory_conversation_cache.load_last_handover", return_value="Pending: fix bug"):
            cache = ConversationCache()
            conv = cache.get(1, 100)
        assert any("handover" in str(m).lower() for m in conv.history)

    def test_no_injection_when_both_empty(self):
        with patch("memory_session._load_last_summary", return_value=""), \
             patch("memory_session.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv = cache.get(1, 100)
        assert conv.history == []

    def test_expired_conversation_replaced_with_new(self):
        with patch("memory_session._load_last_summary", return_value=""), \
             patch("memory_session.load_last_handover", return_value=""):
            cache = ConversationCache()
            conv1 = cache.get(1, 100)
            # Force expiry
            conv1.last_active = time.monotonic() - (CONTEXT_TTL + 100)
            conv2 = cache.get(1, 100)
        assert conv1 is not conv2


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------

class TestConversationCacheSet:
    def test_set_stores_conversation(self):
        cache = ConversationCache()
        conv = Conversation("Bob")
        cache.set(1, 100, conv)
        assert cache._conversations[(1, 100)] is conv


# ---------------------------------------------------------------------------
# clear_user
# ---------------------------------------------------------------------------

class TestConversationCacheClearUser:
    def test_clear_user_resets_history(self):
        cache, conv = _make_cache_with_conv()
        conv.add_user_message("hello")
        cache.clear_user(1, 100)
        assert conv.history == []

    def test_clear_user_noop_when_absent(self):
        cache = ConversationCache()
        cache.clear_user(999, 999)  # Should not raise


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------

class TestConversationCacheClearAll:
    def test_clear_all_empties_dict(self):
        cache, _ = _make_cache_with_conv()
        cache.clear_all()
        assert cache._conversations == {}


# ---------------------------------------------------------------------------
# active_count
# ---------------------------------------------------------------------------

class TestConversationCacheActiveCount:
    def test_active_count_counts_non_expired(self):
        cache, conv = _make_cache_with_conv()
        assert cache.active_count == 1

    def test_active_count_excludes_expired(self):
        cache, conv = _make_cache_with_conv()
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 100)
        assert cache.active_count == 0

    def test_active_count_zero_when_empty(self):
        cache = ConversationCache()
        assert cache.active_count == 0


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

class TestConversationCacheStats:
    def test_stats_returns_string(self):
        cache, _ = _make_cache_with_conv()
        result = cache.stats()
        assert isinstance(result, str)

    def test_stats_contains_counts(self):
        cache, conv = _make_cache_with_conv()
        conv.add_user_message("hello")
        result = cache.stats()
        assert "1" in result


# ---------------------------------------------------------------------------
# cleanup_expired
# ---------------------------------------------------------------------------

class TestConversationCacheCleanupExpired:
    def test_expired_conversations_removed(self):
        cache, conv = _make_cache_with_conv()
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 100)
        cache.cleanup_expired()
        assert (1, 100) not in cache._conversations

    def test_active_conversations_retained(self):
        cache, conv = _make_cache_with_conv()
        conv.add_user_message("still active")
        cache.cleanup_expired()
        assert (1, 100) in cache._conversations

    def test_summarization_skipped_when_too_few_messages(self):
        cache, conv = _make_cache_with_conv()
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 100)
        # Only 0 messages — below MIN_MESSAGES_TO_SUMMARIZE
        with patch("memory_conversation_cache._summarize_and_store") as mock_sum:
            cache.cleanup_expired()
        mock_sum.assert_not_called()

    def test_no_loop_does_not_raise(self):
        """cleanup_expired must not raise if there's no running event loop."""
        cache, conv = _make_cache_with_conv()
        conv.last_active = time.monotonic() - (CONTEXT_TTL + 100)
        for _ in range(MIN_MESSAGES_TO_SUMMARIZE):
            conv.add_user_message("msg")
        # Should not raise even without an event loop
        cache.cleanup_expired()
