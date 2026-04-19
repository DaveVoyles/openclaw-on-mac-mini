"""Tests for digest_skills.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.digest_skills import (
    DIGEST_SKILLS,
    add_digest_stock,
    add_digest_team,
    add_digest_topic,
    configure_digest,
    get_digest_config,
    get_my_digest,
    preview_digest,
    remove_digest_stock,
    remove_digest_team,
    remove_digest_topic,
    update_digest_preferences,
)


@pytest.fixture
def mock_digest_manager():
    """Create a mock DigestManager."""
    with patch("digest_manager.get_digest_manager") as mock_get:
        manager = MagicMock()
        mock_get.return_value = manager
        yield manager


@pytest.fixture
def mock_user_id():
    """Mock the current user ID."""
    with patch("skills.digest_skills.get_current_user_id") as mock_get:
        mock_get.return_value = "test_user_123"
        yield mock_get


class TestConfigureDigest:
    """Test configure_digest skill."""

    @pytest.mark.asyncio
    async def test_configure_digest_success(self, mock_digest_manager, mock_user_id):
        """Test successful digest configuration."""
        mock_digest_manager.get_preferences.return_value = {
            "topics": ["AI", "space"],
            "stocks": ["TSLA"],
            "teams": [],
            "keywords": [],
            "exclude": [],
            "schedule": "daily",
            "delivery_time": "08:00",
            "timezone": "UTC",
            "format": "concise",
            "max_items": 10,
        }

        prefs = {
            "topics": ["AI", "space"],
            "stocks": ["TSLA"],
            "schedule": "daily",
        }

        result = await configure_digest(prefs)

        assert "✅" in result
        assert "configured successfully" in result
        assert "AI" in result
        assert "TSLA" in result
        mock_digest_manager.save_preferences.assert_called_once_with("test_user_123", prefs)

    @pytest.mark.asyncio
    async def test_configure_digest_no_user_id(self, mock_digest_manager):
        """Test configure_digest with no user ID."""
        with patch("skills.digest_skills.get_current_user_id", return_value=None):
            result = await configure_digest({"topics": ["AI"]})

            assert "❌" in result
            assert "Could not determine" in result

    @pytest.mark.asyncio
    async def test_configure_digest_error_handling(self, mock_user_id):
        """Test error handling in configure_digest."""
        with patch("digest_manager.get_digest_manager") as mock_get:
            mock_get.side_effect = Exception("Test error")

            result = await configure_digest({"topics": ["AI"]})

            assert "❌" in result
            assert "Failed to configure" in result


class TestGetMyDigest:
    """Test get_my_digest skill."""

    @pytest.mark.asyncio
    async def test_get_my_digest_success(self, mock_digest_manager, mock_user_id):
        """Test successful digest generation."""

        # Make the mock coroutine awaitable
        async def mock_generate():
            return "📰 YOUR DIGEST\nTest content"

        mock_digest_manager.generate_digest = AsyncMock(return_value="📰 YOUR DIGEST\nTest content")

        result = await get_my_digest()

        assert "📰 YOUR DIGEST" in result
        assert "Test content" in result
        mock_digest_manager.generate_digest.assert_called_once_with("test_user_123", preview=False)

    @pytest.mark.asyncio
    async def test_get_my_digest_no_user_id(self):
        """Test get_my_digest with no user ID."""
        with patch("skills.digest_skills.get_current_user_id", return_value=None):
            result = await get_my_digest()

            assert "❌" in result
            assert "Could not determine" in result


class TestUpdateDigestPreferences:
    """Test update_digest_preferences skill."""

    @pytest.mark.asyncio
    async def test_update_preference_success(self, mock_digest_manager, mock_user_id):
        """Test successful preference update."""
        result = await update_digest_preferences("schedule", "weekly")

        assert "✅" in result
        assert "schedule" in result
        assert "weekly" in result
        mock_digest_manager.update_preference.assert_called_once_with("test_user_123", "schedule", "weekly")

    @pytest.mark.asyncio
    async def test_update_preference_no_user_id(self):
        """Test update with no user ID."""
        with patch("skills.digest_skills.get_current_user_id", return_value=None):
            result = await update_digest_preferences("schedule", "weekly")

            assert "❌" in result


class TestPreviewDigest:
    """Test preview_digest skill."""

    @pytest.mark.asyncio
    async def test_preview_digest_success(self, mock_digest_manager, mock_user_id):
        """Test successful digest preview."""
        mock_digest_manager.generate_digest = AsyncMock(return_value="🔍 PREVIEW\nTest preview")

        result = await preview_digest()

        assert "PREVIEW" in result
        mock_digest_manager.generate_digest.assert_called_once_with("test_user_123", preview=True)


class TestAddDigestTopic:
    """Test add_digest_topic skill."""

    @pytest.mark.asyncio
    async def test_add_topic_success(self, mock_digest_manager, mock_user_id):
        """Test adding a topic."""
        mock_digest_manager.get_preferences.return_value = {"topics": ["AI", "robotics"]}

        result = await add_digest_topic("robotics")

        assert "✅" in result
        assert "robotics" in result
        assert "2 topic(s)" in result
        mock_digest_manager.add_to_list.assert_called_once_with("test_user_123", "topics", "robotics")

    @pytest.mark.asyncio
    async def test_add_topic_no_user_id(self):
        """Test adding topic with no user ID."""
        with patch("skills.digest_skills.get_current_user_id", return_value=None):
            result = await add_digest_topic("AI")

            assert "❌" in result


class TestAddDigestStock:
    """Test add_digest_stock skill."""

    @pytest.mark.asyncio
    async def test_add_stock_success(self, mock_digest_manager, mock_user_id):
        """Test adding a stock."""
        mock_digest_manager.get_preferences.return_value = {"stocks": ["TSLA", "NVDA"]}

        result = await add_digest_stock("tsla")

        assert "✅" in result
        assert "TSLA" in result  # Should be uppercase
        assert "2 stock(s)" in result
        mock_digest_manager.add_to_list.assert_called_once_with("test_user_123", "stocks", "TSLA")

    @pytest.mark.asyncio
    async def test_add_stock_uppercase_conversion(self, mock_digest_manager, mock_user_id):
        """Test that stock ticker is converted to uppercase."""
        mock_digest_manager.get_preferences.return_value = {"stocks": ["AAPL"]}

        await add_digest_stock("aapl")

        mock_digest_manager.add_to_list.assert_called_with("test_user_123", "stocks", "AAPL")


class TestAddDigestTeam:
    """Test add_digest_team skill."""

    @pytest.mark.asyncio
    async def test_add_team_success(self, mock_digest_manager, mock_user_id):
        """Test adding a team."""
        mock_digest_manager.get_preferences.return_value = {"teams": ["Lakers"]}

        result = await add_digest_team("Lakers")

        assert "✅" in result
        assert "Lakers" in result
        assert "1 team(s)" in result
        mock_digest_manager.add_to_list.assert_called_once_with("test_user_123", "teams", "Lakers")


class TestRemoveDigestTopic:
    """Test remove_digest_topic skill."""

    @pytest.mark.asyncio
    async def test_remove_topic_success(self, mock_digest_manager, mock_user_id):
        """Test removing a topic."""
        result = await remove_digest_topic("AI")

        assert "✅" in result
        assert "Removed" in result
        assert "AI" in result
        mock_digest_manager.remove_from_list.assert_called_once_with("test_user_123", "topics", "AI")


class TestRemoveDigestStock:
    """Test remove_digest_stock skill."""

    @pytest.mark.asyncio
    async def test_remove_stock_success(self, mock_digest_manager, mock_user_id):
        """Test removing a stock."""
        result = await remove_digest_stock("tsla")

        assert "✅" in result
        assert "TSLA" in result  # Should be uppercase
        mock_digest_manager.remove_from_list.assert_called_once_with("test_user_123", "stocks", "TSLA")


class TestRemoveDigestTeam:
    """Test remove_digest_team skill."""

    @pytest.mark.asyncio
    async def test_remove_team_success(self, mock_digest_manager, mock_user_id):
        """Test removing a team."""
        result = await remove_digest_team("Lakers")

        assert "✅" in result
        assert "Lakers" in result
        mock_digest_manager.remove_from_list.assert_called_once_with("test_user_123", "teams", "Lakers")


class TestGetDigestConfig:
    """Test get_digest_config skill."""

    @pytest.mark.asyncio
    async def test_get_config_full(self, mock_digest_manager, mock_user_id):
        """Test getting full configuration."""
        mock_digest_manager.get_preferences.return_value = {
            "topics": ["AI", "space", "robotics"],
            "stocks": ["TSLA", "NVDA"],
            "teams": ["Lakers"],
            "keywords": ["OpenAI"],
            "exclude": ["gossip"],
            "schedule": "daily",
            "delivery_time": "08:00",
            "timezone": "UTC",
            "format": "concise",
            "max_items": 10,
            "enabled": True,
        }

        result = await get_digest_config()

        assert "Your Digest Configuration" in result
        assert "AI" in result
        assert "TSLA" in result
        assert "Lakers" in result
        assert "OpenAI" in result
        assert "gossip" in result
        assert "daily" in result
        assert "Enabled" in result

    @pytest.mark.asyncio
    async def test_get_config_empty(self, mock_digest_manager, mock_user_id):
        """Test getting config with no preferences."""
        mock_digest_manager.get_preferences.return_value = {
            "topics": [],
            "stocks": [],
            "teams": [],
            "keywords": [],
            "exclude": [],
            "schedule": "daily",
            "delivery_time": "08:00",
            "timezone": "UTC",
            "format": "concise",
            "max_items": 10,
            "enabled": True,
        }

        result = await get_digest_config()

        assert "None configured" in result
        assert "Enabled" in result

    @pytest.mark.asyncio
    async def test_get_config_disabled(self, mock_digest_manager, mock_user_id):
        """Test getting config when disabled."""
        mock_digest_manager.get_preferences.return_value = {
            "topics": ["AI"],
            "stocks": [],
            "teams": [],
            "keywords": [],
            "exclude": [],
            "schedule": "daily",
            "delivery_time": "08:00",
            "timezone": "UTC",
            "format": "concise",
            "max_items": 10,
            "enabled": False,
        }

        result = await get_digest_config()

        assert "Disabled" in result

    @pytest.mark.asyncio
    async def test_get_config_weekly(self, mock_digest_manager, mock_user_id):
        """Test getting config with weekly schedule."""
        mock_digest_manager.get_preferences.return_value = {
            "topics": ["AI"],
            "stocks": [],
            "teams": [],
            "keywords": [],
            "exclude": [],
            "schedule": "weekly",
            "delivery_time": "09:00",
            "delivery_day": "Monday",
            "timezone": "America/New_York",
            "format": "detailed",
            "max_items": 15,
            "enabled": True,
        }

        result = await get_digest_config()

        assert "weekly" in result
        assert "Monday" in result
        assert "09:00" in result
        assert "detailed" in result
        assert "15" in result


def test_digest_skills_registry():
    """Test that DIGEST_SKILLS is properly structured."""
    assert isinstance(DIGEST_SKILLS, dict)

    expected_skills = [
        "configure_digest",
        "get_my_digest",
        "update_digest_preferences",
        "preview_digest",
        "add_digest_topic",
        "add_digest_stock",
        "add_digest_team",
        "remove_digest_topic",
        "remove_digest_stock",
        "remove_digest_team",
        "get_digest_config",
    ]

    for skill in expected_skills:
        assert skill in DIGEST_SKILLS
        assert callable(DIGEST_SKILLS[skill])
