"""Tests for patreon.py, media.py, utility.py, and code.py discord commands."""

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
import pytest
from discord.ext import commands

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_interaction(user_id: int = 111):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.channel_id = 100
    interaction.channel = MagicMock()
    interaction.guild_id = 999
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


# ===========================================================================
# PATREON TESTS
# ===========================================================================

import discord_commands._helpers as _helpers_mod
import discord_commands.patreon as patreon_mod
from patreon_monitor import PatreonHealthResult, PatreonHealthStatus


def _allow_all():
    """Patch _helpers._is_allowed to allow all users."""
    return patch.object(_helpers_mod, "_is_allowed", return_value=True)


def _make_health(
    status=PatreonHealthStatus.OK,
    message="All good",
    metadata=None,
    issues=None,
    action_items=None,
):
    return PatreonHealthResult(
        status=status,
        message=message,
        timestamp=datetime.now(),
        metadata=metadata or {},
        issues=issues or [],
        action_items=action_items or [],
    )


def _make_patreon_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    patreon_mod._register_patreon_commands(bot)
    return bot


def _get_patreon_cmd(bot):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == "patreon")


# --- /patreon status (default) ---

@pytest.mark.asyncio
async def test_patreon_status_ok(monkeypatch):
    health = _make_health(
        status=PatreonHealthStatus.OK,
        message="All systems operational",
        metadata={"container_status": "running", "api_available": True, "failed_downloads": 0},
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_patreon_status_warning(monkeypatch):
    health = _make_health(
        status=PatreonHealthStatus.WARNING,
        message="Cookie expiring soon",
        metadata={"cookie_age_hours": 60, "api_available": True},
        issues=["Cookies are old"],
        action_items=["Refresh cookies"],
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_patreon_status_critical(monkeypatch):
    health = _make_health(
        status=PatreonHealthStatus.CRITICAL,
        message="Container stopped",
        metadata={"container_status": "stopped", "api_available": False},
        issues=["Container is stopped"],
        action_items=["1. Start the container", "2. Check logs"],
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_patreon_status_exception(monkeypatch):
    checker = MagicMock()
    checker.check_health = AsyncMock(side_effect=RuntimeError("Connection failed"))
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.audit_log"):
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    msg = interaction.followup.send.await_args.args[0]
    assert "error" in msg.lower()


@pytest.mark.asyncio
async def test_patreon_status_unknown_status():
    """UNKNOWN status returns ❓ color."""
    health = _make_health(
        status=PatreonHealthStatus.UNKNOWN,
        message="Status unknown",
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_patreon_status_with_recovery_history():
    """Shows last recovery attempt if history exists."""
    health = _make_health(status=PatreonHealthStatus.OK, message="OK")
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    from patreon_recovery import RecoveryAction, RecoveryResult
    recent = RecoveryResult(
        action=RecoveryAction.RESTART_CONTAINER,
        success=True,
        message="Container restarted",
        timestamp=datetime.now(),
    )
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = [recent]
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_patreon_status_cookie_fresh():
    """Cookie < 48h old shows 🟢."""
    health = _make_health(
        status=PatreonHealthStatus.OK,
        metadata={"cookie_age_hours": 10},
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_patreon_status_cookie_expired():
    """Cookie > 72h old shows 🔴 expired."""
    health = _make_health(
        status=PatreonHealthStatus.WARNING,
        metadata={"cookie_age_hours": 80, "failed_downloads": 3},
    )
    checker = MagicMock()
    checker.check_health = AsyncMock(return_value=health)
    with _allow_all(), \
         patch("discord_commands.patreon.get_patreon_checker", return_value=checker), \
         patch("discord_commands.patreon.get_recovery_manager") as mgr_patch, \
         patch("discord_commands.patreon.audit_log"):
        mgr_patch.return_value.get_recovery_history.return_value = []
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "status")
    interaction.followup.send.assert_awaited_once()


# --- /patreon refresh ---

@pytest.mark.asyncio
async def test_patreon_refresh_cookies():
    with _allow_all(), patch("discord_commands.patreon.audit_log"):
        bot = _make_patreon_bot()
        interaction = _make_interaction()
        await _get_patreon_cmd(bot).callback(interaction, "refresh")
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


# --- _create_cookie_refresh_embed (standalone) ---

def test_create_cookie_refresh_embed():
    embed = patreon_mod._create_cookie_refresh_embed()
    assert isinstance(embed, discord.Embed)
    assert "cookie" in embed.title.lower()


# --- _create_status_embed coverage ---

def test_create_status_embed_ok():
    health = _make_health(status=PatreonHealthStatus.OK, message="OK")
    embed = patreon_mod._create_status_embed(health)
    assert isinstance(embed, discord.Embed)


def test_create_status_embed_with_all_metadata():
    health = _make_health(
        status=PatreonHealthStatus.CRITICAL,
        message="Bad",
        metadata={
            "container_status": "stopped",
            "api_available": False,
            "cookie_age_hours": 90,
            "failed_downloads": 5,
        },
        issues=["Container stopped", "API unreachable"],
        action_items=["Quick fix", "1. Step one", "2. Step two"],
    )
    embed = patreon_mod._create_status_embed(health)
    assert isinstance(embed, discord.Embed)


# ===========================================================================
# MEDIA TESTS
# ===========================================================================

import discord_commands.media as media_mod


def _make_media_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    briefing_fn = AsyncMock()
    media_mod._register_media_commands(bot, briefing_fn)
    return bot, briefing_fn


def _get_media_cmd(bot, name):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == name)


def _make_attachment(filename="test.png", content_type="image/png",
                     size=1024, url="http://example.com/test.png"):
    att = MagicMock(spec=discord.Attachment)
    att.filename = filename
    att.content_type = content_type
    att.size = size
    att.url = url
    return att


# --- /analyze-image ---

@pytest.mark.asyncio
async def test_analyze_image_unsupported_mime():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    image = _make_attachment(content_type="application/pdf")
    with patch("permissions.is_allowed", return_value=True):
        await _get_media_cmd(bot, "analyze-image").callback(interaction, image)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "unsupported" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_image_too_large():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    image = _make_attachment(size=25 * 1024 * 1024)  # 25 MB > 20 MB max
    with patch("permissions.is_allowed", return_value=True):
        await _get_media_cmd(bot, "analyze-image").callback(interaction, image)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "too large" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_image_download_error():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    image = _make_attachment(content_type="image/png", size=1024)

    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_resp.read = AsyncMock(return_value=b"")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)):
        await _get_media_cmd(bot, "analyze-image").callback(interaction, image)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "404" in msg or "could not download" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_image_network_exception():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    image = _make_attachment(content_type="image/jpeg", size=500)

    mock_session = MagicMock()
    mock_session.get.side_effect = aiohttp.ClientError("network fail")

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)):
        await _get_media_cmd(bot, "analyze-image").callback(interaction, image)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "failed to fetch" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_image_success():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    image = _make_attachment(content_type="image/png", size=1024)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"fake_image_bytes")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)), \
         patch("discord_commands.media.llm_analyze_image", AsyncMock(return_value="A test image")), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "analyze-image").callback(
            interaction, image, "What is in this image?"
        )
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


# --- /analyze-file ---

@pytest.mark.asyncio
async def test_analyze_file_too_large():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="big.txt", content_type="text/plain", size=25 * 1024 * 1024)
    with patch("permissions.is_allowed", return_value=True):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    msg = interaction.response.send_message.await_args.args[0]
    assert "too large" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_file_download_error():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="doc.txt", content_type="text/plain", size=100)

    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.read = AsyncMock(return_value=b"")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    msg = interaction.followup.send.await_args.args[0]
    assert "500" in msg or "could not download" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_file_text_success():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="notes.txt", content_type="text/plain", size=100)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"Hello world text content")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)), \
         patch("discord_commands.media.llm_analyze_document", AsyncMock(return_value="Summary")), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_analyze_file_text_truncated():
    """Files larger than DOCUMENT_MAX_CHARS get truncated."""
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="big.txt", content_type="text/plain", size=100)

    long_content = b"x" * 60_000

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=long_content)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)), \
         patch("discord_commands.media.llm_analyze_document", AsyncMock(return_value="Summary")), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs
    embed = kwargs["embed"]
    assert "truncated" in embed.footer.text


@pytest.mark.asyncio
async def test_analyze_file_network_exception():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="doc.txt", content_type="text/plain", size=100)

    mock_session = MagicMock()
    mock_session.get.side_effect = aiohttp.ClientError("network fail")

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    msg = interaction.followup.send.await_args.args[0]
    assert "failed to download" in msg.lower()


@pytest.mark.asyncio
async def test_analyze_file_pdf_no_pypdf():
    """If pypdf not installed, shows install message."""
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    f = _make_attachment(filename="doc.pdf", content_type="application/pdf", size=100)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read = AsyncMock(return_value=b"%PDF fake content")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_cm

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("No module named 'pypdf'")
        return real_import(name, *args, **kwargs)

    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media._get_http_session", AsyncMock(return_value=mock_session)), \
         patch("builtins.__import__", side_effect=mock_import):
        await _get_media_cmd(bot, "analyze-file").callback(interaction, f)
    msg = interaction.followup.send.await_args.args[0]
    assert "pypdf" in msg.lower() or "not installed" in msg.lower()


# --- /briefing ---

@pytest.mark.asyncio
async def test_briefing_llm_not_configured():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.llm_is_configured", return_value=False):
        await _get_media_cmd(bot, "briefing").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "llm not configured" in msg.lower() or "⚠️" in msg


@pytest.mark.asyncio
async def test_briefing_success():
    bot, briefing_fn = _make_media_bot()
    interaction = _make_interaction()
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.llm_is_configured", return_value=True), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "briefing").callback(interaction)
    interaction.response.defer.assert_awaited_once()
    briefing_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_briefing_edit_response_failure():
    """If edit_original_response fails, it should be caught silently."""
    bot, briefing_fn = _make_media_bot()
    interaction = _make_interaction()
    interaction.edit_original_response = AsyncMock(side_effect=Exception("edit failed"))
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.llm_is_configured", return_value=True), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "briefing").callback(interaction)
    briefing_fn.assert_awaited_once()


# --- /imagine ---

@pytest.mark.asyncio
async def test_imagine_sd_unavailable():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.sd_is_available", AsyncMock(return_value=False)):
        await _get_media_cmd(bot, "imagine").callback(
            interaction, "a cat", "", 512, 512, 20
        )
    interaction.edit_original_response.assert_awaited()
    msg = interaction.edit_original_response.await_args.kwargs.get("content", "")
    assert "stable diffusion" in msg.lower() or "not running" in msg.lower()


@pytest.mark.asyncio
async def test_imagine_generation_failure():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.sd_is_available", AsyncMock(return_value=True)), \
         patch("discord_commands.media.generate_image", AsyncMock(return_value=(None, "timeout"))):
        await _get_media_cmd(bot, "imagine").callback(
            interaction, "a dog", "", 512, 512, 20
        )
    calls = interaction.edit_original_response.await_args_list
    last_call = calls[-1]
    content = last_call.kwargs.get("content", "")
    assert "failed" in content.lower() or "❌" in content


@pytest.mark.asyncio
async def test_imagine_success():
    bot, _ = _make_media_bot()
    interaction = _make_interaction()
    fake_image = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    with patch("permissions.is_allowed", return_value=True), \
         patch("discord_commands.media.sd_is_available", AsyncMock(return_value=True)), \
         patch("discord_commands.media.generate_image", AsyncMock(return_value=(fake_image, "ok"))), \
         patch("discord_commands.media.audit_log"):
        await _get_media_cmd(bot, "imagine").callback(
            interaction, "a beautiful landscape", "ugly", 1024, 768, 30
        )
    calls = interaction.edit_original_response.await_args_list
    last_call = calls[-1]
    assert "embed" in last_call.kwargs


# ===========================================================================
# UTILITY TESTS
# ===========================================================================

import discord_commands.utility as utility_mod


def _make_utility_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.start_time = time.monotonic() - 3661  # 1h 1m 1s ago
    utility_mod._register_utility_commands(bot)
    return bot


def _get_utility_cmd(bot, name):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == name)


# --- /ping ---

@pytest.mark.asyncio
async def test_ping_authorized():
    bot = _make_utility_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "ping").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_ping_unauthorized():
    import discord_commands._helpers as helpers_mod
    bot = _make_utility_bot()
    interaction = _make_interaction(user_id=9999)
    with patch.object(helpers_mod, "ALLOWED_USER_IDS", [111]):
        await _get_utility_cmd(bot, "ping").callback(interaction)
    msg = interaction.response.send_message.await_args.args[0]
    assert "not authorized" in msg.lower()


# --- /about ---

@pytest.mark.asyncio
async def test_about_authorized():
    bot = _make_utility_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "about").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


# --- /whoami ---

@pytest.mark.asyncio
async def test_whoami_authorized():
    bot = _make_utility_bot()
    interaction = _make_interaction(user_id=111)
    with _allow_all(), patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "whoami").callback(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_whoami_not_allowed():
    """Shows not authorized status in embed."""
    bot = _make_utility_bot()
    interaction = _make_interaction(user_id=9999)
    # Pass require_auth but show "Not Authorized" in the whoami body
    with _allow_all(), \
         patch("discord_commands.utility._is_allowed", return_value=False), \
         patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "whoami").callback(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


# --- /help ---

@pytest.mark.asyncio
async def test_help_shows_embed_with_view():
    bot = _make_utility_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "help").callback(interaction)
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs
    assert "view" in kwargs


@pytest.mark.asyncio
async def test_help_select_callback():
    """HelpSelect dropdown triggers category embed on select."""
    bot = _make_utility_bot()
    interaction = _make_interaction()
    with _allow_all(), patch("discord_commands.utility.audit_log"):
        await _get_utility_cmd(bot, "help").callback(interaction)
    view = interaction.response.send_message.await_args.kwargs["view"]
    select = view.children[0]
    # Call callback directly by simulating internal values via _values
    select._values = ["🤖 AI & Chat"]
    select_interaction = _make_interaction()
    await select.callback(select_interaction)
    select_interaction.response.edit_message.assert_awaited_once()
    kwargs = select_interaction.response.edit_message.await_args.kwargs
    assert "embed" in kwargs


# ===========================================================================
# CODE TESTS
# ===========================================================================

import discord_commands.code as code_mod


def _make_code_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    code_mod._register_code_commands(bot)
    return bot


def _get_code_cmd(bot, name):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == name)


# --- /diff ---

@pytest.mark.asyncio
async def test_diff_success():
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all(), \
         patch("discord_commands.code.git_status", AsyncMock(return_value="M file.py")), \
         patch("discord_commands.code.git_diff", AsyncMock(return_value="-old\n+new")), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "diff").callback(interaction)
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_diff_empty():
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all(), \
         patch("discord_commands.code.git_status", AsyncMock(return_value="")), \
         patch("discord_commands.code.git_diff", AsyncMock(return_value="")), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "diff").callback(interaction)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_diff_unauthorized():
    import discord_commands._helpers as helpers_mod
    bot = _make_code_bot()
    interaction = _make_interaction(user_id=9999)
    with patch.object(helpers_mod, "ALLOWED_USER_IDS", [111]):
        await _get_code_cmd(bot, "diff").callback(interaction)
    msg = interaction.response.send_message.await_args.args[0]
    assert "not authorized" in msg.lower()


# --- /run-code ---

@pytest.mark.asyncio
async def test_run_code_empty():
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all():
        await _get_code_cmd(bot, "run-code").callback(interaction, "")
    interaction.edit_original_response.assert_awaited()
    content = interaction.edit_original_response.await_args.kwargs.get("content", "")
    assert "no code" in content.lower()


@pytest.mark.asyncio
async def test_run_code_too_long():
    bot = _make_code_bot()
    interaction = _make_interaction()
    code = "x" * 10_001
    with _allow_all():
        await _get_code_cmd(bot, "run-code").callback(interaction, code)
    content = interaction.edit_original_response.await_args.kwargs.get("content", "")
    assert "too long" in content.lower()


@pytest.mark.asyncio
async def test_run_code_success():
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all(), \
         patch("discord_commands.code.sandbox_run_code",
               AsyncMock(return_value=("Hello, World!\n", "", 0))), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "run-code").callback(
            interaction, "print('Hello, World!')"
        )
    calls = interaction.edit_original_response.await_args_list
    last = calls[-1].kwargs
    assert "embed" in last
    assert last["embed"].color.value == discord.Color.green().value


@pytest.mark.asyncio
async def test_run_code_failure():
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all(), \
         patch("discord_commands.code.sandbox_run_code",
               AsyncMock(return_value=("", "NameError: x", 1))), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "run-code").callback(interaction, "print(x)")
    calls = interaction.edit_original_response.await_args_list
    last = calls[-1].kwargs
    assert "embed" in last
    assert last["embed"].color.value == discord.Color.red().value


@pytest.mark.asyncio
async def test_run_code_no_output():
    """Empty stdout and stderr shows '*(no output)*'."""
    bot = _make_code_bot()
    interaction = _make_interaction()
    with _allow_all(), \
         patch("discord_commands.code.sandbox_run_code",
               AsyncMock(return_value=("", "", 0))), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "run-code").callback(interaction, "pass")
    calls = interaction.edit_original_response.await_args_list
    last = calls[-1].kwargs
    assert "embed" in last
    assert "no output" in last["embed"].description


@pytest.mark.asyncio
async def test_run_code_strips_markdown_fence():
    """Code wrapped in ```python...``` gets stripped."""
    bot = _make_code_bot()
    interaction = _make_interaction()
    code_input = "```python\nprint('hi')\n```"
    captured = {}

    async def fake_run(code):
        captured["code"] = code
        return ("hi\n", "", 0)

    with _allow_all(), \
         patch("discord_commands.code.sandbox_run_code", side_effect=fake_run), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "run-code").callback(interaction, code_input)
    assert "```" not in captured.get("code", "```still here```")


@pytest.mark.asyncio
async def test_run_code_large_output_attaches_file():
    """If stdout > OUTPUT_MAX_CHARS, attaches a file."""
    bot = _make_code_bot()
    interaction = _make_interaction()
    from constants import OUTPUT_MAX_CHARS
    large_output = "x" * (OUTPUT_MAX_CHARS + 100)
    with _allow_all(), \
         patch("discord_commands.code.sandbox_run_code",
               AsyncMock(return_value=(large_output, "", 0))), \
         patch("discord_commands.code.audit_log"):
        await _get_code_cmd(bot, "run-code").callback(
            interaction, "print('x' * 10000)"
        )
    calls = interaction.edit_original_response.await_args_list
    last = calls[-1].kwargs
    assert "attachments" in last
