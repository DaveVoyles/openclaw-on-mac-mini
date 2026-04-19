"""Tests for patreon_scheduled.py and openclaw_types.py."""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# openclaw_types tests — pure TypedDicts and type aliases
# ---------------------------------------------------------------------------
from openclaw_types import (
    JSON,
    APIResponse,
    ConversationMessage,
    MessageContext,
    NewsArticle,
    SkillResult,
)


class TestOpenclawTypes:
    """Verify TypedDicts accept expected field shapes."""

    def test_message_context_minimal(self):
        ctx: MessageContext = {"user_id": "123"}
        assert ctx["user_id"] == "123"

    def test_patreon_scheduled_types_message_context_full(self):
        ctx: MessageContext = {
            "user_id": "123",
            "channel_id": "456",
            "guild_id": "789",
            "message_id": "999",
            "is_dm": False,
            "author_name": "Dave",
        }
        assert ctx["author_name"] == "Dave"
        assert ctx["is_dm"] is False

    def test_conversation_message_required_fields(self):
        msg: ConversationMessage = {"role": "user", "content": "hello"}
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_patreon_scheduled_types_skill_result_success(self):
        result: SkillResult = {"success": True, "data": {"key": "val"}, "message": "ok"}
        assert result["success"] is True

    def test_skill_result_failure(self):
        result: SkillResult = {"success": False, "error": "something broke"}
        assert result["success"] is False
        assert "broke" in result["error"]

    def test_api_response_structure(self):
        resp: APIResponse = {"status": "ok", "data": {}, "error": None}
        assert resp["status"] == "ok"

    def test_patreon_scheduled_types_news_article_minimal(self):
        article: NewsArticle = {"title": "Big news", "url": "https://example.com"}
        assert article["title"] == "Big news"

    def test_json_type_alias_is_dict(self):
        # JSON is just a type alias — verify it accepts a real dict
        data: JSON = {"a": 1, "b": [1, 2, 3]}
        assert data["a"] == 1

    def test_all_typeddicts_importable(self):
        import openclaw_types as ot

        for name in ["MessageContext", "ConversationMessage", "SkillResult", "APIResponse", "NewsArticle"]:
            assert hasattr(ot, name)


# ---------------------------------------------------------------------------
# patreon_scheduled tests
# ---------------------------------------------------------------------------

# Mock config before importing patreon_scheduled without polluting other module imports.
_patreon_mocks = {
    "config": MagicMock(cfg=MagicMock(alert_channel_id=None, ollama_url="http://localhost:11434")),
}
with patch.dict(sys.modules, _patreon_mocks):
    sys.modules.pop("patreon_scheduled", None)
    ps = importlib.import_module("patreon_scheduled")
sys.modules["patreon_scheduled"] = ps


@pytest.fixture(autouse=True)
def reset_discord_client():
    """Reset module-level discord client between tests."""
    ps._discord_client = None
    yield
    ps._discord_client = None


class TestSetDiscordClient:
    def test_set_discord_client_stores_value(self):
        mock_client = MagicMock()
        ps.set_discord_client(mock_client)
        assert ps._discord_client is mock_client

    def test_set_discord_client_none(self):
        ps.set_discord_client(None)
        assert ps._discord_client is None

    def test_set_discord_client_overrides_previous(self):
        ps.set_discord_client(MagicMock())
        new_client = MagicMock()
        ps.set_discord_client(new_client)
        assert ps._discord_client is new_client


class TestScheduledPatreonHealthCheck:
    @pytest.mark.asyncio
    async def test_success_path_returns_dict_with_success_true(self):
        mock_health = MagicMock(
            status=MagicMock(value="healthy"),
            message="All good",
            issues=[],
        )
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(return_value=mock_health)

        mock_recovery_mgr = AsyncMock()
        mock_recovery_mgr.attempt_recovery = AsyncMock(return_value=None)

        mock_alert_mgr = AsyncMock()
        mock_alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        with (
            patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker),
            patch("patreon_scheduled.get_recovery_manager", return_value=mock_recovery_mgr),
            patch("patreon_scheduled.get_alert_manager", return_value=mock_alert_mgr),
        ):
            result = await ps.scheduled_patreon_health_check()

        assert result["success"] is True
        assert result["status"] == "healthy"
        assert result["message"] == "All good"
        assert result["issues_count"] == 0
        assert result["recovery_attempted"] is False
        assert result["alert_sent"] is False
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_patreon_scheduled_types_exception_returns_failure_dict(self):
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(side_effect=RuntimeError("network down"))

        with patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker):
            result = await ps.scheduled_patreon_health_check()

        assert result["success"] is False
        assert "network down" in result["error"]
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_recovery_attempted_when_needed(self):
        mock_health = MagicMock(
            status=MagicMock(value="degraded"),
            message="Issues found",
            issues=["issue1"],
        )
        mock_recovery_result = MagicMock(
            action=MagicMock(value="retry"),
            success=True,
        )
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(return_value=mock_health)

        mock_recovery_mgr = AsyncMock()
        mock_recovery_mgr.attempt_recovery = AsyncMock(return_value=mock_recovery_result)

        mock_alert_mgr = AsyncMock()
        mock_alert_mgr.send_alert_if_needed = AsyncMock(return_value=True)

        with (
            patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker),
            patch("patreon_scheduled.get_recovery_manager", return_value=mock_recovery_mgr),
            patch("patreon_scheduled.get_alert_manager", return_value=mock_alert_mgr),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await ps.scheduled_patreon_health_check()

        assert result["recovery_attempted"] is True
        assert result["recovery_success"] is True

    @pytest.mark.asyncio
    async def test_alert_sent_with_discord_client_arg(self):
        mock_health = MagicMock(
            status=MagicMock(value="healthy"),
            message="ok",
            issues=[],
        )
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(return_value=mock_health)

        mock_recovery_mgr = AsyncMock()
        mock_recovery_mgr.attempt_recovery = AsyncMock(return_value=None)

        mock_alert_mgr = AsyncMock()
        mock_alert_mgr.send_alert_if_needed = AsyncMock(return_value=True)

        mock_discord = MagicMock()

        with (
            patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker),
            patch("patreon_scheduled.get_recovery_manager", return_value=mock_recovery_mgr),
            patch("patreon_scheduled.get_alert_manager", return_value=mock_alert_mgr),
        ):
            result = await ps.scheduled_patreon_health_check(discord_client=mock_discord)

        assert result["alert_sent"] is True
        mock_alert_mgr.send_alert_if_needed.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_sent_with_module_discord_client(self):
        """Falls back to module-level _discord_client when none passed as arg."""
        mock_health = MagicMock(
            status=MagicMock(value="healthy"),
            message="ok",
            issues=[],
        )
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(return_value=mock_health)

        mock_recovery_mgr = AsyncMock()
        mock_recovery_mgr.attempt_recovery = AsyncMock(return_value=None)

        mock_alert_mgr = AsyncMock()
        mock_alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        ps.set_discord_client(MagicMock())

        with (
            patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker),
            patch("patreon_scheduled.get_recovery_manager", return_value=mock_recovery_mgr),
            patch("patreon_scheduled.get_alert_manager", return_value=mock_alert_mgr),
        ):
            result = await ps.scheduled_patreon_health_check()

        mock_alert_mgr.send_alert_if_needed.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_when_no_discord_client(self):
        """No alert sent when discord_client arg is None and _discord_client is None."""
        mock_health = MagicMock(
            status=MagicMock(value="healthy"),
            message="ok",
            issues=[],
        )
        mock_checker = AsyncMock()
        mock_checker.check_health = AsyncMock(return_value=mock_health)

        mock_recovery_mgr = AsyncMock()
        mock_recovery_mgr.attempt_recovery = AsyncMock(return_value=None)

        mock_alert_mgr = AsyncMock()
        mock_alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        with (
            patch("patreon_scheduled.get_patreon_checker", return_value=mock_checker),
            patch("patreon_scheduled.get_recovery_manager", return_value=mock_recovery_mgr),
            patch("patreon_scheduled.get_alert_manager", return_value=mock_alert_mgr),
        ):
            result = await ps.scheduled_patreon_health_check()

        mock_alert_mgr.send_alert_if_needed.assert_not_called()
        assert result["alert_sent"] is False

    def test_task_config_structure(self):
        task = ps.PATREON_MONITORING_TASK
        assert task["name"] == "patreon_health_check"
        assert callable(task["function"])
        assert "schedule" in task
        assert task["retry_on_failure"] is True
        assert task["max_retries"] > 0
        assert task["timeout_seconds"] > 0

    def test_task_config_schedule_is_cron(self):
        schedule = ps.PATREON_MONITORING_TASK["schedule"]
        # Should be a valid cron expression
        parts = schedule.split()
        assert len(parts) == 5
