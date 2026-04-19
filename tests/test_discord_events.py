"""
Tests for discord_events.py — pure helper functions that don't require Discord gateway.
"""

import sys
from unittest.mock import AsyncMock, MagicMock

import discord

# Mock all heavy dependencies before importing discord_events.
# Use save/restore (not setdefault) so these stubs don't persist in sys.modules
# after discord_events is imported.  setdefault would permanently corrupt
# quality_helpers / ask_orchestrator for other test files sharing this xdist worker.
_mocks = {
    "approvals": MagicMock(is_emergency_stopped=MagicMock(return_value=False)),
    "ask_orchestrator": MagicMock(),
    "audit": MagicMock(),
    "quality_helpers": MagicMock(),
    "response_actions": MagicMock(),
    "llm": MagicMock(chat_stream=AsyncMock(), is_configured=MagicMock(return_value=True)),
    "memory": MagicMock(store=MagicMock(), get_model_preference=MagicMock(return_value=None)),
}

# Only stub and import discord_events fresh if it hasn't been imported yet.
# If it's already cached (imported by an earlier test file in this worker), reuse
# the cached version — its local bindings are already set and won't change.
if "discord_events" not in sys.modules:
    _saved = {name: sys.modules.pop(name, None) for name in _mocks}
    for name, mock in _mocks.items():
        sys.modules[name] = mock
    import discord_events as de  # noqa: E402

    # Restore originals so other test files in this worker see the real modules.
    for name, original in _saved.items():
        if original is not None:
            sys.modules[name] = original
        else:
            sys.modules.pop(name, None)
else:
    import discord_events as de  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_thread(*, owner_id, parent_id, archived=False, locked=False):
    thread = MagicMock(spec=discord.Thread)
    thread.owner_id = owner_id
    thread.parent_id = parent_id
    thread.archived = archived
    thread.locked = locked
    return thread


def make_guild_channel(channel_id=111, guild_id=222, can_read=True):
    channel = MagicMock()
    channel.id = channel_id
    guild = MagicMock()
    guild.id = guild_id
    channel.guild = guild
    bot_member = MagicMock()
    perms = MagicMock()
    perms.read_messages = can_read
    channel.permissions_for = MagicMock(return_value=perms)
    guild.me = bot_member
    return channel


def make_dm_channel(channel_id=999):
    channel = MagicMock()
    channel.id = channel_id
    channel.guild = None
    return channel


# ---------------------------------------------------------------------------
# _is_user_allowed
# ---------------------------------------------------------------------------


class TestIsUserAllowed:
    def test_empty_allow_list_allows_all(self, monkeypatch):
        monkeypatch.setattr(de, "ALLOWED_USER_IDS", [])
        assert de._is_user_allowed(99999) is True

    def test_non_empty_allows_matching_id(self, monkeypatch):
        monkeypatch.setattr(de, "ALLOWED_USER_IDS", [12345, 67890])
        assert de._is_user_allowed(12345) is True

    def test_non_empty_rejects_non_matching_id(self, monkeypatch):
        monkeypatch.setattr(de, "ALLOWED_USER_IDS", [12345])
        assert de._is_user_allowed(99999) is False

    def test_set_based_allow_list(self, monkeypatch):
        monkeypatch.setattr(de, "ALLOWED_USER_IDS", {11, 22, 33})
        assert de._is_user_allowed(22) is True
        assert de._is_user_allowed(44) is False


# ---------------------------------------------------------------------------
# _should_send_message_content_hint
# ---------------------------------------------------------------------------


class TestShouldSendMessageContentHint:
    def setup_method(self):
        de._MESSAGE_CONTENT_HINT_CACHE.clear()

    def test_true_on_first_call(self):
        channel = make_dm_channel(channel_id=500)
        assert de._should_send_message_content_hint(channel) is True

    def test_false_on_second_call(self):
        channel = make_dm_channel(channel_id=501)
        de._should_send_message_content_hint(channel)
        assert de._should_send_message_content_hint(channel) is False

    def test_true_again_after_cache_cleared(self):
        channel = make_dm_channel(channel_id=502)
        de._should_send_message_content_hint(channel)
        de._MESSAGE_CONTENT_HINT_CACHE.clear()
        assert de._should_send_message_content_hint(channel) is True

    def test_returns_false_if_no_channel_id(self):
        channel = MagicMock()
        del channel.id
        assert de._should_send_message_content_hint(channel) is False

    def test_different_channels_independent(self):
        ch_a = make_dm_channel(channel_id=600)
        ch_b = make_dm_channel(channel_id=601)
        de._should_send_message_content_hint(ch_a)
        # ch_b is new — should still return True
        assert de._should_send_message_content_hint(ch_b) is True


# ---------------------------------------------------------------------------
# _default_ask_thread_cache_key
# ---------------------------------------------------------------------------


class TestDefaultAskThreadCacheKey:
    def test_with_guild_returns_guild_channel_user(self):
        channel = make_guild_channel(channel_id=10, guild_id=20)
        key = de._default_ask_thread_cache_key(channel, 30)
        assert key == (20, 10, 30)

    def test_dm_channel_uses_zero_guild(self):
        channel = make_dm_channel(channel_id=10)
        key = de._default_ask_thread_cache_key(channel, 30)
        assert key == (0, 10, 30)

    def test_key_is_tuple(self):
        channel = make_dm_channel(channel_id=5)
        key = de._default_ask_thread_cache_key(channel, 7)
        assert isinstance(key, tuple)
        assert len(key) == 3


# ---------------------------------------------------------------------------
# _default_ask_thread_user_tag
# ---------------------------------------------------------------------------


class TestDefaultAskThreadUserTag:
    def test_formats_user_id(self):
        assert de._default_ask_thread_user_tag(12345) == "u12345"

    def test_zero_user_id(self):
        assert de._default_ask_thread_user_tag(0) == "u0"


# ---------------------------------------------------------------------------
# _build_default_ask_thread_name
# ---------------------------------------------------------------------------


class TestBuildDefaultAskThreadName:
    def test_includes_emoji(self):
        name = de._build_default_ask_thread_name("Hello?", 1)
        assert "💬" in name

    def test_includes_question_snippet(self):
        name = de._build_default_ask_thread_name("What is the weather?", 1)
        assert "What is the weather?" in name

    def test_includes_user_tag(self):
        name = de._build_default_ask_thread_name("Hello", 42)
        assert "u42" in name

    def test_empty_question_uses_conversation(self):
        name = de._build_default_ask_thread_name("", 1)
        assert "conversation" in name

    def test_long_question_is_truncated(self):
        long_q = "A" * 200
        name = de._build_default_ask_thread_name(long_q, 1)
        assert len(name) <= 100

    def test_exactly_50_char_snippet_gets_ellipsis(self):
        q = "B" * 50
        name = de._build_default_ask_thread_name(q, 1)
        assert "…" in name

    def test_whitespace_normalised(self):
        name = de._build_default_ask_thread_name("  hi   there  ", 1)
        assert "hi there" in name


# ---------------------------------------------------------------------------
# _is_reusable_bot_thread
# ---------------------------------------------------------------------------


class TestIsReusableBotThread:
    BOT_ID = 777
    CHANNEL_ID = 888

    def _patch_bot(self, monkeypatch):
        bot = MagicMock()
        bot.user.id = self.BOT_ID
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=bot))

    def test_non_thread_returns_false(self, monkeypatch):
        self._patch_bot(monkeypatch)
        assert de._is_reusable_bot_thread(MagicMock(), parent_channel_id=self.CHANNEL_ID) is False

    def test_archived_thread_returns_false(self, monkeypatch):
        self._patch_bot(monkeypatch)
        t = make_thread(owner_id=self.BOT_ID, parent_id=self.CHANNEL_ID, archived=True)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is False

    def test_locked_thread_returns_false(self, monkeypatch):
        self._patch_bot(monkeypatch)
        t = make_thread(owner_id=self.BOT_ID, parent_id=self.CHANNEL_ID, locked=True)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is False

    def test_owner_id_mismatch_returns_false(self, monkeypatch):
        self._patch_bot(monkeypatch)
        t = make_thread(owner_id=999, parent_id=self.CHANNEL_ID)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is False

    def test_parent_id_mismatch_returns_false(self, monkeypatch):
        self._patch_bot(monkeypatch)
        t = make_thread(owner_id=self.BOT_ID, parent_id=12345)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is False

    def test_valid_thread_returns_true(self, monkeypatch):
        self._patch_bot(monkeypatch)
        t = make_thread(owner_id=self.BOT_ID, parent_id=self.CHANNEL_ID)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is True

    def test_no_bot_returns_false(self, monkeypatch):
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=None))
        t = make_thread(owner_id=self.BOT_ID, parent_id=self.CHANNEL_ID)
        assert de._is_reusable_bot_thread(t, parent_channel_id=self.CHANNEL_ID) is False


# ---------------------------------------------------------------------------
# _pick_most_recent_thread
# ---------------------------------------------------------------------------


class TestPickMostRecentThread:
    def _make_thread_with_msg_id(self, tid, last_msg_id):
        t = MagicMock(spec=discord.Thread)
        t.id = tid
        t.last_message_id = last_msg_id
        return t

    def test_returns_thread_with_highest_last_message_id(self):
        t1 = self._make_thread_with_msg_id(1, 100)
        t2 = self._make_thread_with_msg_id(2, 999)
        t3 = self._make_thread_with_msg_id(3, 500)
        result = de._pick_most_recent_thread([t1, t2, t3])
        assert result is t2

    def test_handles_none_last_message_id(self):
        t1 = self._make_thread_with_msg_id(10, None)
        t2 = self._make_thread_with_msg_id(20, 500)
        result = de._pick_most_recent_thread([t1, t2])
        assert result is t2

    def test_single_thread(self):
        t = self._make_thread_with_msg_id(1, 42)
        assert de._pick_most_recent_thread([t]) is t


# ---------------------------------------------------------------------------
# _bot_can_read_channel
# ---------------------------------------------------------------------------


class TestBotCanReadChannel:
    def test_dm_channel_returns_true(self, monkeypatch):
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=None))
        channel = make_dm_channel()
        assert de._bot_can_read_channel(channel) is True

    def test_guild_channel_with_read_returns_true(self, monkeypatch):
        bot = MagicMock()
        bot.user.id = 1
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=bot))
        channel = make_guild_channel(can_read=True)
        assert de._bot_can_read_channel(channel) is True

    def test_guild_channel_without_read_returns_false(self, monkeypatch):
        bot = MagicMock()
        bot.user.id = 1
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=bot))
        channel = make_guild_channel(can_read=False)
        assert de._bot_can_read_channel(channel) is False

    def test_no_bot_member_returns_true(self, monkeypatch):
        # guild.me = None and no bot user — should return True (optimistic)
        monkeypatch.setattr(de, "get_bot", MagicMock(return_value=None))
        channel = MagicMock()
        channel.guild = MagicMock()
        channel.guild.me = None
        perms = MagicMock()
        perms.read_messages = True
        channel.permissions_for = MagicMock(return_value=perms)
        assert de._bot_can_read_channel(channel) is True
