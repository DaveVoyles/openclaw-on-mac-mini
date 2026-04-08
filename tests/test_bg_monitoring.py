"""Tests for bg_monitoring.py — error monitor, container health, cookie check, resource monitor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bg_monitoring


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(channel=None):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.is_closed = MagicMock(return_value=False)
    bot.wait_until_ready = AsyncMock()
    return bot


def _make_channel():
    ch = MagicMock()
    ch.send = AsyncMock()
    return ch


def _reset_module_state():
    """Reset module-level mutable state to avoid test bleed."""
    bg_monitoring._container_prev_state.clear()
    bg_monitoring._container_unhealthy_count.clear()
    bg_monitoring._cookie_alert_sent = False


# ---------------------------------------------------------------------------
# error_monitor_loop
# ---------------------------------------------------------------------------


class TestErrorMonitorLoop:
    @pytest.mark.asyncio
    async def test_loop_runs_and_checks_patterns(self, monkeypatch):
        """Loop makes one error pattern check then stops."""
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        mock_check = MagicMock(return_value=[])
        mock_error_tracker = MagicMock(check_error_patterns=mock_check)

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.dict("sys.modules", {"error_tracker": mock_error_tracker}):
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.error_monitor_loop(_make_bot())

        mock_check.assert_called()

    @pytest.mark.asyncio
    async def test_critical_patterns_trigger_alert(self, monkeypatch):
        """Critical error patterns cause _post_error_alert to be called."""
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        patterns = [{"severity": "critical", "type": "db_error", "detail": "Connection refused"}]
        mock_check = MagicMock(return_value=patterns)
        mock_error_tracker = MagicMock(
            check_error_patterns=mock_check,
            diagnose_error_pattern=AsyncMock(return_value={"cause": "db down", "confidence": 0.9, "explanation": "x"}),
            execute_fix=AsyncMock(return_value={"success": False, "action_taken": "none", "detail": ""}),
            get_recent_outcomes=MagicMock(return_value=[]),
            record_incident=AsyncMock(),
        )

        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.dict("sys.modules", {"error_tracker": mock_error_tracker}), \
             patch.object(bg_monitoring, "_post_error_alert", new_callable=AsyncMock) as mock_post:
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.error_monitor_loop(bot)

        mock_post.assert_awaited()

    @pytest.mark.asyncio
    async def test_two_patterns_below_critical_still_triggers_alert(self, monkeypatch):
        """2 or more patterns (even non-critical) trigger an alert."""
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        patterns = [
            {"severity": "warning", "type": "err_a", "detail": "A"},
            {"severity": "warning", "type": "err_b", "detail": "B"},
        ]
        mock_check = MagicMock(return_value=patterns)
        mock_error_tracker = MagicMock(
            check_error_patterns=mock_check,
            diagnose_error_pattern=AsyncMock(return_value={"cause": "x", "confidence": 0.5, "explanation": "y"}),
            execute_fix=AsyncMock(return_value={"success": False, "action_taken": "none", "detail": ""}),
            get_recent_outcomes=MagicMock(return_value=[]),
            record_incident=AsyncMock(),
        )
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.dict("sys.modules", {"error_tracker": mock_error_tracker}), \
             patch.object(bg_monitoring, "_post_error_alert", new_callable=AsyncMock) as mock_post:
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.error_monitor_loop(bot)

        mock_post.assert_awaited()

    @pytest.mark.asyncio
    async def test_one_non_critical_pattern_no_alert(self, monkeypatch):
        """Single non-critical pattern does not trigger alert."""
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        patterns = [{"severity": "warning", "type": "err_a", "detail": "A"}]
        mock_check = MagicMock(return_value=patterns)
        mock_error_tracker = MagicMock(check_error_patterns=mock_check)

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.dict("sys.modules", {"error_tracker": mock_error_tracker}), \
             patch.object(bg_monitoring, "_post_error_alert", new_callable=AsyncMock) as mock_post:
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.error_monitor_loop(_make_bot())

        mock_post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_exception_handled(self):
        """Exception during check_error_patterns is caught."""
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        mock_error_tracker = MagicMock(check_error_patterns=MagicMock(side_effect=Exception("db gone")))

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.dict("sys.modules", {"error_tracker": mock_error_tracker}):
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.error_monitor_loop(_make_bot())


# ---------------------------------------------------------------------------
# _post_error_alert
# ---------------------------------------------------------------------------


class TestPostErrorAlert:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        await bg_monitoring._post_error_alert(bot, [{"severity": "critical", "type": "x", "detail": "y"}])
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(channel=None)
        await bg_monitoring._post_error_alert(bot, [{"severity": "critical", "type": "x", "detail": "y"}])
        # No send attempted
        bot.get_channel.assert_called_once_with(123)

    @pytest.mark.asyncio
    async def test_posts_embed_for_critical(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)
        patterns = [{"severity": "critical", "type": "db_error", "detail": "Connection refused"}]
        with patch("bg_monitoring.audit_log"):
            await bg_monitoring._post_error_alert(bot, patterns)
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "Error Pattern" in embed.title

    @pytest.mark.asyncio
    async def test_posts_orange_embed_for_non_critical(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)
        patterns = [{"severity": "warning", "type": "slow_query", "detail": "Query took 10s"}]
        with patch("bg_monitoring.audit_log"):
            await bg_monitoring._post_error_alert(bot, patterns)
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_http_exception_caught(self, monkeypatch):
        import discord
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limit"))
        bot = _make_bot(channel)
        with patch("bg_monitoring.audit_log"):
            # Should not raise
            await bg_monitoring._post_error_alert(bot, [{"severity": "critical", "type": "x", "detail": "y"}])


# ---------------------------------------------------------------------------
# container_health_loop
# ---------------------------------------------------------------------------


class TestContainerHealthLoop:
    @pytest.mark.asyncio
    async def test_loop_calls_checks(self, monkeypatch):
        """Loop calls _check_container_health and _check_monstervision_cookies."""
        _reset_module_state()
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.object(bg_monitoring, "_check_container_health", new_callable=AsyncMock) as mock_ch, \
             patch.object(bg_monitoring, "_check_monstervision_cookies", new_callable=AsyncMock) as mock_mv:
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.container_health_loop(_make_bot())

        mock_ch.assert_awaited()
        mock_mv.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_check_is_caught(self):
        """If a check raises, the loop continues."""
        _reset_module_state()
        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        async def boom(bot):
            raise RuntimeError("docker not found")

        with patch("bg_monitoring.asyncio.sleep", side_effect=mock_sleep), \
             patch.object(bg_monitoring, "_check_container_health", side_effect=boom), \
             patch.object(bg_monitoring, "_check_monstervision_cookies", new_callable=AsyncMock):
            with pytest.raises(asyncio.CancelledError):
                await bg_monitoring.container_health_loop(_make_bot())


# ---------------------------------------------------------------------------
# _check_container_health
# ---------------------------------------------------------------------------


class TestCheckContainerHealth:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        await bg_monitoring._check_container_health(bot)
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_docker_ps_failure_returns_early(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(_make_channel())

        mock_run = AsyncMock(return_value=(1, "", "docker not running"))
        mock_subprocess = MagicMock(run=mock_run)

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess}):
            await bg_monitoring._check_container_health(bot)

        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_healthy_container_no_alert(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        docker_output = "sonarr\tUp 2 hours (healthy)"
        mock_run = AsyncMock(return_value=(0, docker_output, ""))
        mock_subprocess = MagicMock(run=mock_run)

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess,
                                         "health_history": MagicMock(record=MagicMock())}):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unhealthy_container_triggers_alert(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        docker_output = "sonarr\tUp 2 hours (unhealthy)"
        mock_run = AsyncMock(return_value=(0, docker_output, ""))
        mock_subprocess = MagicMock(run=mock_run)

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess,
                                         "health_history": MagicMock(record=MagicMock())}), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exited_container_triggers_alert(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        docker_output = "radarr\tExited (1) 5 minutes ago"
        mock_run = AsyncMock(return_value=(0, docker_output, ""))
        mock_subprocess = MagicMock(run=mock_run)

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess,
                                         "health_history": MagicMock(record=MagicMock())}), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_restart_after_threshold(self, monkeypatch):
        """Container in safe list is auto-restarted after N consecutive unhealthy checks."""
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(bg_monitoring, "_AUTO_RESTART_THRESHOLD", 1)

        channel = _make_channel()
        bot = _make_bot(channel)

        docker_output = "sonarr\tUp (unhealthy)"
        mock_run = AsyncMock(return_value=(0, docker_output, ""))
        mock_subprocess = MagicMock(run=mock_run)
        mock_restart = AsyncMock(return_value="restarted")

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess,
                                         "health_history": MagicMock(record=MagicMock())}), \
             patch("bg_monitoring.restart_container", mock_restart), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        mock_restart.assert_awaited_once_with("sonarr")

    @pytest.mark.asyncio
    async def test_channel_not_found_no_send(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(channel=None)  # channel not found

        docker_output = "sonarr\tUp (unhealthy)"
        mock_run = AsyncMock(return_value=(0, docker_output, ""))
        mock_subprocess = MagicMock(run=mock_run)

        with patch.dict("sys.modules", {"subprocess_utils": mock_subprocess,
                                         "health_history": MagicMock(record=MagicMock())}):
            await bg_monitoring._check_container_health(bot)

        # No error, just no send


# ---------------------------------------------------------------------------
# _check_monstervision_cookies
# ---------------------------------------------------------------------------


class TestCheckMonstervisionCookies:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        await bg_monitoring._check_monstervision_cookies(bot)
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_ok_cookie_status_no_alert(self, monkeypatch):
        """When API reports cookies OK, no alert is sent."""
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        import aiohttp

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"cookie_status": {"label": "ok"}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(return_value=mock_session)

        mock_cfg = MagicMock()
        mock_cfg.docker_host_ip = "192.168.1.1"
        mock_cfg.monstervision_port = 8080

        with patch.dict("sys.modules", {
            "aiohttp": aiohttp,
            "config": MagicMock(cfg=mock_cfg),
        }), patch.object(bg_monitoring, "_bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_cookies_sends_alert(self, monkeypatch):
        """When docker logs show expired cookies and no alert sent yet, alert is sent."""
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(bg_monitoring, "_cookie_alert_sent", False)
        channel = _make_channel()
        bot = _make_bot(channel)

        import aiohttp

        # Simulate API call failure so we fall through to log scraping
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(return_value=mock_session)

        mock_run = AsyncMock(return_value=(0, "cookies have expired and cannot be used", ""))
        mock_subprocess = MagicMock(run=mock_run)

        mock_cfg = MagicMock()
        mock_cfg.docker_host_ip = "192.168.1.1"
        mock_cfg.monstervision_port = 8080

        with patch.dict("sys.modules", {
            "aiohttp": aiohttp,
            "config": MagicMock(cfg=mock_cfg),
            "subprocess_utils": mock_subprocess,
        }), patch.object(bg_monitoring, "_bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(bot)

        channel.send.assert_awaited_once()
        assert bg_monitoring._cookie_alert_sent is True

    @pytest.mark.asyncio
    async def test_alert_not_repeated(self, monkeypatch):
        """If alert already sent, do not send again."""
        _reset_module_state()
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._cookie_alert_sent = True
        channel = _make_channel()
        bot = _make_bot(channel)

        import aiohttp

        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(return_value=mock_session)

        mock_run = AsyncMock(return_value=(0, "cookies have expired", ""))
        mock_subprocess = MagicMock(run=mock_run)

        mock_cfg = MagicMock()
        mock_cfg.docker_host_ip = "192.168.1.1"
        mock_cfg.monstervision_port = 8080

        with patch.dict("sys.modules", {
            "aiohttp": aiohttp,
            "config": MagicMock(cfg=mock_cfg),
            "subprocess_utils": mock_subprocess,
        }), patch.object(bg_monitoring, "_bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(bot)

        channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# resource_monitor_loop
# ---------------------------------------------------------------------------


class TestResourceMonitorLoop:
    @pytest.mark.asyncio
    async def test_violations_trigger_alert(self, monkeypatch):
        """Resource violations send an embed to the alert channel."""
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)

        channel = _make_channel()
        bot = _make_bot(channel)
        bot.is_closed = MagicMock(side_effect=[False, False, True])

        threshold = MagicMock(container="sonarr", cpu_percent=80, memory_percent=90, cooldown_seconds=300)
        stats = {"cpu": 95.0, "memory": 92.0}
        violations = [(threshold, stats)]

        mock_monitor = MagicMock()
        mock_monitor.check_all = AsyncMock(return_value=violations)
        mock_resource_monitor = MagicMock(resource_monitor=mock_monitor)

        with patch.dict("sys.modules", {"resource_monitor": mock_resource_monitor}), \
             patch("bg_monitoring.asyncio.sleep", new_callable=AsyncMock), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring.resource_monitor_loop(bot)

        channel.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_violations_no_alert(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)
        bot.is_closed = MagicMock(side_effect=[False, True])

        mock_monitor = MagicMock()
        mock_monitor.check_all = AsyncMock(return_value=[])
        mock_resource_monitor = MagicMock(resource_monitor=mock_monitor)

        with patch.dict("sys.modules", {"resource_monitor": mock_resource_monitor}), \
             patch("bg_monitoring.asyncio.sleep", new_callable=AsyncMock):
            await bg_monitoring.resource_monitor_loop(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exception_in_check_handled(self, monkeypatch):
        """Exceptions in resource check are caught and loop continues."""
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(_make_channel())
        bot.is_closed = MagicMock(side_effect=[False, True])

        mock_monitor = MagicMock()
        mock_monitor.check_all = AsyncMock(side_effect=Exception("stats unavailable"))
        mock_resource_monitor = MagicMock(resource_monitor=mock_monitor)

        with patch.dict("sys.modules", {"resource_monitor": mock_resource_monitor}), \
             patch("bg_monitoring.asyncio.sleep", new_callable=AsyncMock):
            await bg_monitoring.resource_monitor_loop(bot)

    @pytest.mark.asyncio
    async def test_no_channel_no_send(self, monkeypatch):
        """If channel not found, no embed is sent."""
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(channel=None)
        bot.is_closed = MagicMock(side_effect=[False, True])

        threshold = MagicMock(container="sonarr", cpu_percent=80, memory_percent=90, cooldown_seconds=300)
        violations = [(threshold, {"cpu": 95.0, "memory": 92.0})]

        mock_monitor = MagicMock()
        mock_monitor.check_all = AsyncMock(return_value=violations)
        mock_resource_monitor = MagicMock(resource_monitor=mock_monitor)

        with patch.dict("sys.modules", {"resource_monitor": mock_resource_monitor}), \
             patch("bg_monitoring.asyncio.sleep", new_callable=AsyncMock):
            await bg_monitoring.resource_monitor_loop(bot)
