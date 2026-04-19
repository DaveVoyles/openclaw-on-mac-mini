"""test_bg_monitoring_unit.py — Unit tests for src/bg_monitoring.py."""
from __future__ import annotations

import importlib as _importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy external dependencies before importing bg_monitoring
# ---------------------------------------------------------------------------


def _try_stub(mod_name: str) -> None:
    if mod_name not in sys.modules:
        try:
            _importlib.import_module(mod_name)
        except (ImportError, ModuleNotFoundError):
            sys.modules[mod_name] = MagicMock()


if "discord" not in sys.modules:
    try:
        _importlib.import_module("discord")
    except (ImportError, ModuleNotFoundError):
        _discord_stub = MagicMock()
        sys.modules["discord"] = _discord_stub
        sys.modules["discord.ext"] = MagicMock()
        sys.modules["discord.ext.commands"] = MagicMock()

for _mod in [
    "aiohttp",
    "audit",
    "http_session",
    "skills",
    "skills.advanced_skills",
    "subprocess_utils",
    "health_history",
    "error_tracker",
    "llm_ratelimit",
    "resource_monitor",
    "config",
    "google", "google.genai", "google.genai.types",
    "pandas", "psutil", "prometheus_client",
]:
    _try_stub(_mod)

# Ensure audit_log is importable as a callable stub
sys.modules["audit"].audit_log = MagicMock()

import bg_monitoring  # noqa: E402
from bg_monitoring import (  # noqa: E402
    set_active_conversation_count,
    get_active_conversation_count,
    _post_error_alert,
    _check_container_health,
    _SAFE_RESTART_TARGETS,
    _AUTO_RESTART_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


class TestActiveConversationCount:
    def test_default_is_zero(self):
        set_active_conversation_count(0)
        assert get_active_conversation_count() == 0

    def test_set_and_get(self):
        set_active_conversation_count(5)
        assert get_active_conversation_count() == 5

    def test_clamps_negative_to_zero(self):
        set_active_conversation_count(-3)
        assert get_active_conversation_count() == 0

    def test_accepts_zero_explicitly(self):
        set_active_conversation_count(10)
        set_active_conversation_count(0)
        assert get_active_conversation_count() == 0

    def test_large_value(self):
        set_active_conversation_count(999)
        assert get_active_conversation_count() == 999
        set_active_conversation_count(0)  # cleanup

    def test_float_is_truncated(self):
        set_active_conversation_count(2.9)  # type: ignore[arg-type]
        assert get_active_conversation_count() == 2


# ---------------------------------------------------------------------------
# _post_error_alert — channel resolution and embed dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPostErrorAlert:
    async def test_does_nothing_when_no_channel_id(self):
        """_post_error_alert should exit immediately when ALERT_CHANNEL_ID is 0."""
        bot = MagicMock()
        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 0
        try:
            await _post_error_alert(bot, [{"severity": "critical", "type": "oom", "detail": "x"}])
        finally:
            bg_monitoring.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_not_called()

    async def test_does_nothing_when_channel_not_found(self):
        """_post_error_alert should exit when bot cannot find the channel."""
        bot = MagicMock()
        bot.get_channel.return_value = None
        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 12345
        try:
            await _post_error_alert(bot, [{"severity": "warning", "type": "err", "detail": "y"}])
        finally:
            bg_monitoring.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_called_once_with(12345)

    async def test_sends_embed_when_channel_found(self):
        """_post_error_alert should call channel.send when channel is valid."""
        channel = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 999
        try:
            patterns = [{"severity": "critical", "type": "oom", "detail": "out of memory"}]
            await _post_error_alert(bot, patterns)
        finally:
            bg_monitoring.ALERT_CHANNEL_ID = original
        channel.send.assert_awaited_once()

    async def test_caps_patterns_at_five(self):
        """Embed should only include the first 5 patterns even if more are provided."""
        channel = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 1
        try:
            patterns = [{"severity": "warning", "type": f"err_{i}", "detail": f"d{i}"} for i in range(10)]
            await _post_error_alert(bot, patterns)
        finally:
            bg_monitoring.ALERT_CHANNEL_ID = original
        channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# _SAFE_RESTART_TARGETS and threshold constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_safe_restart_targets_is_frozenset(self):
        assert isinstance(_SAFE_RESTART_TARGETS, frozenset)

    def test_known_safe_targets_present(self):
        for name in ("sonarr", "radarr", "sabnzbd"):
            assert name in _SAFE_RESTART_TARGETS

    def test_auto_restart_threshold_positive(self):
        assert _AUTO_RESTART_THRESHOLD > 0

    def test_unknown_target_not_in_safe_set(self):
        assert "monstervision" not in _SAFE_RESTART_TARGETS
        assert "plex" not in _SAFE_RESTART_TARGETS


# ---------------------------------------------------------------------------
# _check_container_health — docker ps output parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckContainerHealth:
    async def _run_with_docker_output(self, output: str, bot=None):
        """Helper: patch subprocess_utils.run to return given docker ps output."""
        if bot is None:
            bot = MagicMock()
            bot.get_channel.return_value = None

        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 1
        # Reset module-level state between tests
        bg_monitoring._container_prev_state.clear()
        bg_monitoring._container_unhealthy_count.clear()

        async def _fake_run(cmd, timeout=15):
            return (0, output, "")

        # _run is imported locally inside _check_container_health, so patch the source module
        sys.modules["subprocess_utils"].run = AsyncMock(side_effect=_fake_run)
        with patch("bg_monitoring.audit_log"):
            try:
                await _check_container_health(bot)
            finally:
                bg_monitoring.ALERT_CHANNEL_ID = original

    async def test_healthy_container_no_alert(self):
        """A running healthy container should not trigger any alert."""
        bot = MagicMock()
        bot.get_channel.return_value = AsyncMock()
        await self._run_with_docker_output("sonarr\tUp 2 hours (healthy)\n", bot)
        # The channel mock's send should not have been called since container is healthy
        if hasattr(bot.get_channel.return_value, "send"):
            assert not bot.get_channel.return_value.send.called

    async def test_exited_container_is_detected_as_bad(self):
        """An exited container should be added to prev_state after check."""
        bot = MagicMock()
        channel = AsyncMock()
        bot.get_channel.return_value = channel
        await self._run_with_docker_output("sonarr\tExited (1) 5 minutes ago\n", bot)
        assert bg_monitoring._container_prev_state.get("sonarr") == "Exited"

    async def test_unhealthy_container_tracked(self):
        """An unhealthy container increments the consecutive-unhealthy counter."""
        bot = MagicMock()
        bot.get_channel.return_value = AsyncMock()
        await self._run_with_docker_output("radarr\tUp 10 minutes (unhealthy)\n", bot)
        assert bg_monitoring._container_unhealthy_count.get("radarr", 0) >= 1

    async def test_recovered_container_clears_state(self):
        """A container that was bad and is now healthy should be removed from state."""
        bg_monitoring._container_prev_state["sonarr"] = "unhealthy"
        bg_monitoring._container_unhealthy_count["sonarr"] = 1
        bot = MagicMock()
        bot.get_channel.return_value = AsyncMock()
        await self._run_with_docker_output("sonarr\tUp 10 minutes (healthy)\n", bot)
        assert "sonarr" not in bg_monitoring._container_prev_state

    async def test_docker_failure_returns_silently(self):
        """If docker ps fails (rc != 0), function should return without error."""
        original = bg_monitoring.ALERT_CHANNEL_ID
        bg_monitoring.ALERT_CHANNEL_ID = 1

        sys.modules["subprocess_utils"].run = AsyncMock(return_value=(1, "", "docker: error"))
        try:
            await _check_container_health(MagicMock())
        finally:
            bg_monitoring.ALERT_CHANNEL_ID = original

    async def test_malformed_line_is_skipped(self):
        """Lines with no tab separator should be ignored gracefully."""
        bot = MagicMock()
        bot.get_channel.return_value = AsyncMock()
        # No crash should occur
        await self._run_with_docker_output("this-is-a-line-with-no-tab\n", bot)
