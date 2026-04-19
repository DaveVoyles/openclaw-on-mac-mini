"""Tests for digest_manager.py."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.digest_manager import (
    DEFAULT_DIGEST_PREFERENCES,
    DIGEST_PREFS_DIR,
    DigestManager,
    get_digest_manager,
)


@pytest.fixture
def manager():
    """Create a DigestManager instance."""
    return DigestManager()


@pytest.fixture
def temp_prefs_dir(tmp_path, monkeypatch):
    """Use a temporary directory for preferences."""
    test_dir = tmp_path / "digests"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("src.digest_manager.DIGEST_PREFS_DIR", test_dir)
    return test_dir


class TestDigestManager:
    """Test DigestManager class."""

    def test_digest_manager_init(self, manager):
        """Test DigestManager initialization."""
        assert isinstance(manager, DigestManager)

    def test_get_user_pref_path(self, manager, temp_prefs_dir):
        """Test user preference path generation."""
        path = manager._get_user_pref_path("123456")
        assert path.parent == temp_prefs_dir
        assert path.name == "123456.json"

    def test_get_user_pref_path_sanitizes(self, manager, temp_prefs_dir):
        """Test user ID sanitization in path."""
        path = manager._get_user_pref_path("user@123/test")
        assert "/" not in path.name
        assert "@" not in path.name

    def test_save_preferences(self, manager, temp_prefs_dir):
        """Test saving preferences."""
        user_id = "test_user_123"
        prefs = {
            "topics": ["AI", "space"],
            "stocks": ["TSLA"],
            "schedule": "daily",
        }

        manager.save_preferences(user_id, prefs)

        # Verify file was created
        path = temp_prefs_dir / f"{user_id}.json"
        assert path.exists()

        # Verify content
        saved = json.loads(path.read_text())
        assert saved["user_id"] == user_id
        assert saved["topics"] == ["AI", "space"]
        assert saved["stocks"] == ["TSLA"]
        assert saved["schedule"] == "daily"
        assert "created_at" in saved
        assert "updated_at" in saved

    def test_save_preferences_requires_user_id(self, manager):
        """Test that save_preferences requires user_id."""
        with pytest.raises(ValueError, match="user_id is required"):
            manager.save_preferences("", {"topics": ["AI"]})

    def test_save_preferences_merges_with_defaults(self, manager, temp_prefs_dir):
        """Test that preferences are merged with defaults."""
        user_id = "test_user_456"
        prefs = {"topics": ["AI"]}

        manager.save_preferences(user_id, prefs)

        saved = manager.get_preferences(user_id)
        # Should have default values
        assert saved["format"] == "concise"
        assert saved["max_items"] == 10
        assert saved["enabled"] is True
        # Should have custom value
        assert saved["topics"] == ["AI"]

    def test_get_preferences_returns_defaults_for_new_user(self, manager):
        """Test getting preferences for non-existent user."""
        prefs = manager.get_preferences("nonexistent_user")

        assert prefs["user_id"] == "nonexistent_user"
        assert prefs["topics"] == []
        assert prefs["stocks"] == []
        assert prefs["schedule"] == "daily"

    def test_get_preferences_loads_saved(self, manager, temp_prefs_dir):
        """Test loading saved preferences."""
        user_id = "test_user_789"
        original = {
            "topics": ["climate", "tech"],
            "stocks": ["NVDA", "AAPL"],
            "schedule": "weekly",
        }

        manager.save_preferences(user_id, original)
        loaded = manager.get_preferences(user_id)

        assert loaded["topics"] == ["climate", "tech"]
        assert loaded["stocks"] == ["NVDA", "AAPL"]
        assert loaded["schedule"] == "weekly"

    def test_update_preference(self, manager, temp_prefs_dir):
        """Test updating a single preference."""
        user_id = "test_user_update"
        manager.save_preferences(user_id, {"topics": ["AI"]})

        manager.update_preference(user_id, "schedule", "weekly")

        prefs = manager.get_preferences(user_id)
        assert prefs["schedule"] == "weekly"
        assert prefs["topics"] == ["AI"]  # Other fields unchanged

    def test_add_to_list(self, manager, temp_prefs_dir):
        """Test adding items to list preferences."""
        user_id = "test_user_add"
        manager.save_preferences(user_id, {})

        manager.add_to_list(user_id, "topics", "AI")
        manager.add_to_list(user_id, "topics", "robotics")

        prefs = manager.get_preferences(user_id)
        assert "AI" in prefs["topics"]
        assert "robotics" in prefs["topics"]

    def test_add_to_list_no_duplicates(self, manager, temp_prefs_dir):
        """Test that add_to_list doesn't create duplicates."""
        user_id = "test_user_dup"
        manager.save_preferences(user_id, {"topics": ["AI"]})

        manager.add_to_list(user_id, "topics", "AI")

        prefs = manager.get_preferences(user_id)
        assert prefs["topics"].count("AI") == 1

    def test_add_to_list_invalid_key(self, manager):
        """Test that add_to_list rejects invalid keys."""
        with pytest.raises(ValueError, match="Invalid list preference key"):
            manager.add_to_list("user", "invalid_key", "value")

    def test_remove_from_list(self, manager, temp_prefs_dir):
        """Test removing items from list preferences."""
        user_id = "test_user_remove"
        manager.save_preferences(user_id, {"topics": ["AI", "robotics", "space"]})

        manager.remove_from_list(user_id, "topics", "robotics")

        prefs = manager.get_preferences(user_id)
        assert "AI" in prefs["topics"]
        assert "space" in prefs["topics"]
        assert "robotics" not in prefs["topics"]

    def test_list_all_users(self, manager, temp_prefs_dir):
        """Test listing all users with digest preferences."""
        manager.save_preferences("user1", {})
        manager.save_preferences("user2", {})
        manager.save_preferences("user3", {})

        users = manager.list_all_users()
        assert "user1" in users
        assert "user2" in users
        assert "user3" in users
        assert len(users) >= 3

    def test_calculate_relevance_exact_topic_match(self, manager):
        """Test relevance calculation for exact topic match."""
        content = "AI breakthrough in neural networks"
        topics = ["AI"]
        keywords = []

        score = manager._calculate_relevance(content, topics, keywords)
        assert score >= 1.0

    def test_calculate_relevance_keyword_match(self, manager):
        """Test relevance calculation for keyword match."""
        content = "New Tesla model announced with autopilot features"
        topics = []
        keywords = ["Tesla", "autopilot"]

        score = manager._calculate_relevance(content, topics, keywords)
        assert score >= 0.7

    def test_calculate_relevance_multiple_matches(self, manager):
        """Test relevance calculation with multiple matches."""
        content = "AI research at OpenAI achieves breakthrough"
        topics = ["AI"]
        keywords = ["OpenAI", "breakthrough"]

        score = manager._calculate_relevance(content, topics, keywords)
        assert score >= 2.0  # Topic + 2 keywords

    def test_calculate_relevance_no_match(self, manager):
        """Test base relevance score with no matches."""
        content = "Random unrelated content"
        topics = ["AI"]
        keywords = ["Tesla"]

        score = manager._calculate_relevance(content, topics, keywords)
        assert score == 0.3  # Base score

    @pytest.mark.asyncio
    async def test_generate_digest_no_preferences(self, manager, temp_prefs_dir):
        """Test digest generation with no configured preferences."""
        user_id = "test_user_empty"
        manager.save_preferences(user_id, {})

        digest = await manager.generate_digest(user_id, preview=False)

        assert "haven't configured any digest preferences" in digest
        assert "!configure_digest" in digest

    @pytest.mark.asyncio
    async def test_generate_digest_disabled(self, manager, temp_prefs_dir):
        """Test digest generation when disabled."""
        user_id = "test_user_disabled"
        manager.save_preferences(
            user_id,
            {
                "topics": ["AI"],
                "enabled": False,
            },
        )

        digest = await manager.generate_digest(user_id, preview=False)

        assert "digest is currently disabled" in digest

    @pytest.mark.asyncio
    async def test_generate_digest_disabled_but_preview(self, manager, temp_prefs_dir):
        """Test that preview works even when disabled."""
        user_id = "test_user_preview"
        manager.save_preferences(
            user_id,
            {
                "topics": ["AI"],
                "enabled": False,
            },
        )

        with patch.object(manager, "_generate_news_section", new_callable=AsyncMock) as mock_news:
            mock_news.return_value = "\n🤖 NEWS\n• Test article\n"

            digest = await manager.generate_digest(user_id, preview=True)

            assert "PREVIEW" in digest
            assert "disabled" not in digest.lower()

    @pytest.mark.asyncio
    async def test_generate_digest_with_topics(self, manager, temp_prefs_dir):
        """Test digest generation with topics configured."""
        user_id = "test_user_topics"
        manager.save_preferences(
            user_id,
            {
                "topics": ["AI", "space"],
                "schedule": "daily",
            },
        )

        with patch.object(manager, "_generate_news_section", new_callable=AsyncMock) as mock_news:
            mock_news.return_value = "\n🤖 NEWS & TOPICS (2 articles)\n• AI article\n• Space article\n"

            digest = await manager.generate_digest(user_id, preview=False)

            assert "YOUR DAILY DIGEST" in digest
            assert "NEWS & TOPICS" in digest
            mock_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_digest_with_stocks(self, manager, temp_prefs_dir):
        """Test digest generation with stocks configured."""
        user_id = "test_user_stocks"
        manager.save_preferences(
            user_id,
            {
                "stocks": ["TSLA", "NVDA"],
            },
        )

        with patch.object(manager, "_generate_stocks_section", new_callable=AsyncMock) as mock_stocks:
            mock_stocks.return_value = "\n📈 YOUR STOCKS (2 symbols)\n• TSLA: $245\n• NVDA: $800\n"

            digest = await manager.generate_digest(user_id, preview=False)

            assert "YOUR STOCKS" in digest
            mock_stocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_digest_with_teams(self, manager, temp_prefs_dir):
        """Test digest generation with teams configured."""
        user_id = "test_user_teams"
        manager.save_preferences(
            user_id,
            {
                "teams": ["Lakers", "Patriots"],
            },
        )

        with patch.object(manager, "_generate_sports_section", new_callable=AsyncMock) as mock_sports:
            mock_sports.return_value = "\n🏀 SPORTS UPDATES (2 teams)\n• Lakers won\n• Patriots lost\n"

            digest = await manager.generate_digest(user_id, preview=False)

            assert "SPORTS UPDATES" in digest
            mock_sports.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_digest_preview_flag(self, manager, temp_prefs_dir):
        """Test that preview flag adds preview header."""
        user_id = "test_user_preview2"
        manager.save_preferences(user_id, {"topics": ["AI"]})

        with patch.object(manager, "_generate_news_section", new_callable=AsyncMock) as mock_news:
            mock_news.return_value = ""

            digest = await manager.generate_digest(user_id, preview=True)

            assert "PREVIEW" in digest
            assert "This is a preview" in digest


def test_get_digest_manager_singleton():
    """Test that get_digest_manager returns a singleton."""
    manager1 = get_digest_manager()
    manager2 = get_digest_manager()

    assert manager1 is manager2


def test_default_digest_preferences_structure():
    """Test that default preferences have all required fields."""
    assert "user_id" in DEFAULT_DIGEST_PREFERENCES
    assert "topics" in DEFAULT_DIGEST_PREFERENCES
    assert "stocks" in DEFAULT_DIGEST_PREFERENCES
    assert "teams" in DEFAULT_DIGEST_PREFERENCES
    assert "keywords" in DEFAULT_DIGEST_PREFERENCES
    assert "exclude" in DEFAULT_DIGEST_PREFERENCES
    assert "schedule" in DEFAULT_DIGEST_PREFERENCES
    assert "delivery_time" in DEFAULT_DIGEST_PREFERENCES
    assert "format" in DEFAULT_DIGEST_PREFERENCES
    assert "max_items" in DEFAULT_DIGEST_PREFERENCES
    assert "enabled" in DEFAULT_DIGEST_PREFERENCES


def test_digest_prefs_dir_creation():
    """Test that digest preferences directory can be created."""
    # The module should attempt to create the directory, but may fail in test env
    # This test just ensures the path is defined correctly
    assert str(DIGEST_PREFS_DIR) == "/memory/preferences/digests"
