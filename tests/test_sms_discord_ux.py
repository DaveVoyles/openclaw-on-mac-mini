"""Focused tests for Discord-facing SMS UX command and context-menu flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs import sms_cog as sms_mod
from discord_commands import context_menus as context_mod
from sms_ux import (
    SMSPrefsStore,
    SMSUXError,
    UserSMSPrefs,
    check_sms_verification,
    configure_sms_phone,
    send_configured_sms,
    start_sms_verification,
)


def _mock_interaction(user_id: int = 12345):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=user_id)
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_sms_send_command_shows_confirmation(monkeypatch):
    cog = sms_mod.SMSCog(SimpleNamespace())
    interaction = _mock_interaction()

    await cog.send.callback(cog, interaction, "hello from openclaw")

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], sms_mod.SMSSendConfirmView)
    assert "Confirm SMS Send" in kwargs["embed"].title


@pytest.mark.asyncio
async def test_sms_confirm_button_sends_and_edits(monkeypatch):
    interaction = _mock_interaction()
    monkeypatch.setattr(
        sms_mod,
        "send_configured_sms",
        AsyncMock(return_value=SimpleNamespace(provider="twilio", sid="SM123")),
    )
    view = sms_mod.SMSSendConfirmView(requester_id=12345, body="ship it")
    confirm_button = view.children[0]

    await confirm_button.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    kwargs = interaction.response.edit_message.await_args.kwargs
    assert kwargs["view"] is None
    assert kwargs["embed"].title == "✅ SMS Sent"


@pytest.mark.asyncio
async def test_context_menu_send_to_sms_registers_and_prompts(monkeypatch):
    captured: list = []
    bot = SimpleNamespace(tree=SimpleNamespace(add_command=lambda cmd: captured.append(cmd)))
    context_mod._register_context_menus(bot)
    assert captured, "context menu command should be registered"
    monkeypatch.setattr(context_mod, "_is_allowed", lambda _interaction: True)

    cmd = next(command for command in captured if command.name == "Send to SMS")
    interaction = _mock_interaction()
    message = SimpleNamespace(content="selected message text")

    await cmd.callback(interaction, message)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "📲 Send Selected Message to SMS?"
    assert isinstance(kwargs["view"], sms_mod.SMSSendConfirmView)


def test_build_copy_workflow_payload_strips_markdown_and_formats_bullets():
    raw = (
        "**Project Update**\n"
        "- Fixed `context menu` flow\n"
        "- Added [docs](https://example.com/docs)\n"
        "> ~~old blocker~~ resolved\n"
        "<@12345> confirm in <#98765>\n"
    )

    payload = context_mod._build_copy_workflow_payload(raw)

    assert payload.startswith("Project Update")
    assert "• Fixed context menu flow" in payload
    assert "• Added docs" in payload
    assert "• old blocker resolved" in payload
    assert "@user" in payload
    assert "#channel" in payload
    assert "**" not in payload
    assert "`" not in payload


@pytest.mark.asyncio
async def test_context_menu_copy_workflow_context_returns_ephemeral_copy_block(monkeypatch):
    captured: list = []
    bot = SimpleNamespace(tree=SimpleNamespace(add_command=lambda cmd: captured.append(cmd)))
    context_mod._register_context_menus(bot)
    monkeypatch.setattr(context_mod, "_is_allowed", lambda _interaction: True)

    cmd = next(command for command in captured if command.name == "Copy Workflow Context")
    interaction = _mock_interaction()
    message = SimpleNamespace(
        content="Workflow status\n- Ship v1 today\n- Validate permissions and tests"
    )

    await cmd.callback(interaction, message)

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args.args
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "Copy-ready export" in args[0]
    assert "```text" in args[0]
    assert "• Ship v1 today" in args[0]


@pytest.mark.asyncio
async def test_context_menu_package_copy_safe_returns_bundle(monkeypatch):
    captured: list = []
    bot = SimpleNamespace(tree=SimpleNamespace(add_command=lambda cmd: captured.append(cmd)))
    context_mod._register_context_menus(bot)
    monkeypatch.setattr(context_mod, "_is_allowed", lambda _interaction: True)

    cmd = next(command for command in captured if command.name == "Package Report: Copy-safe")
    interaction = _mock_interaction()
    message = SimpleNamespace(content="Status\n- Ship report packaging\n- Verify mobile chunks")

    await cmd.callback(interaction, message)

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args.args
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "Copy-safe text bundle" in args[0]
    assert "• Ship report packaging" in args[0]


@pytest.mark.asyncio
async def test_context_menu_package_artifact_attaches_text_file(monkeypatch):
    captured: list = []
    bot = SimpleNamespace(tree=SimpleNamespace(add_command=lambda cmd: captured.append(cmd)))
    context_mod._register_context_menus(bot)
    monkeypatch.setattr(context_mod, "_is_allowed", lambda _interaction: True)

    cmd = next(command for command in captured if command.name == "Package Report: Artifact")
    interaction = _mock_interaction()
    message = SimpleNamespace(content="Artifact report text")

    await cmd.callback(interaction, message)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["files"]
    assert kwargs["files"][0].filename == "report-package.txt"


@pytest.mark.asyncio
async def test_context_menu_package_brief_detail_sends_mobile_friendly_text(monkeypatch):
    captured: list = []
    bot = SimpleNamespace(tree=SimpleNamespace(add_command=lambda cmd: captured.append(cmd)))
    context_mod._register_context_menus(bot)
    monkeypatch.setattr(context_mod, "_is_allowed", lambda _interaction: True)

    cmd = next(command for command in captured if command.name == "Package Report: Brief+Detail")
    interaction = _mock_interaction()
    message = SimpleNamespace(content="Summary\n- Point A\n- Point B")

    await cmd.callback(interaction, message)

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args.args
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "Brief+Detail package" in args[0]


@pytest.mark.asyncio
async def test_send_configured_sms_requires_verified_phone(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))
    prefs = UserSMSPrefs(user_id=12345, phone_number="+15551234567", is_verified=False)
    await sms_ux.sms_prefs.update(prefs)

    with pytest.raises(SMSUXError, match="Phone not verified"):
        await send_configured_sms(12345, "hello")


@pytest.mark.asyncio
async def test_send_configured_sms_rate_limit(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))
    now = 1_000_000.0
    prefs = UserSMSPrefs(
        user_id=12345,
        phone_number="+15551234567",
        is_verified=True,
        send_timestamps=[now - 1, now - 2, now - 3, now - 4, now - 5],
    )
    await sms_ux.sms_prefs.update(prefs)
    monkeypatch.setattr(sms_ux.time, "time", lambda: now)
    monkeypatch.setattr(
        sms_ux,
        "build_sms_provider",
        lambda: SimpleNamespace(send_sms=AsyncMock(return_value=SimpleNamespace(provider="twilio", sid="SMX"))),
    )

    with pytest.raises(SMSUXError, match="rate limit reached"):
        await send_configured_sms(12345, "hello")


@pytest.mark.asyncio
async def test_start_sms_verification_requires_configured_phone(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))

    with pytest.raises(SMSUXError, match="No phone configured yet"):
        await start_sms_verification(12345)


@pytest.mark.asyncio
async def test_check_sms_verification_rejects_empty_code(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))
    prefs = UserSMSPrefs(user_id=12345, phone_number="+15551234567", is_verified=False)
    await sms_ux.sms_prefs.update(prefs)

    with pytest.raises(SMSUXError, match="Verification code cannot be empty"):
        await check_sms_verification(12345, "   ")


@pytest.mark.asyncio
async def test_check_sms_verification_keeps_unverified_on_pending_status(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))
    prefs = UserSMSPrefs(user_id=12345, phone_number="+15551234567", is_verified=False)
    await sms_ux.sms_prefs.update(prefs)
    fake_provider = SimpleNamespace(
        check_verification=AsyncMock(return_value=SimpleNamespace(provider="twilio", sid="VE1", status="pending"))
    )
    monkeypatch.setattr(sms_ux, "build_sms_provider", lambda: fake_provider)

    _result, approved = await check_sms_verification(12345, "123456")

    assert approved is False
    stored = sms_ux.sms_prefs.get(12345)
    assert stored.is_verified is False
    assert stored.verification_status == "pending"


@pytest.mark.asyncio
async def test_configure_sms_phone_change_resets_previous_verification_state(monkeypatch, tmp_path):
    import sms_ux

    monkeypatch.setattr(sms_ux, "sms_prefs", SMSPrefsStore(tmp_path / "sms_prefs.json"))
    prefs = UserSMSPrefs(
        user_id=12345,
        phone_number="+15551234567",
        is_verified=True,
        verification_sid="OLD",
        verification_status="approved",
        verification_started_at=123.0,
        verified_at=456.0,
    )
    await sms_ux.sms_prefs.update(prefs)

    updated = await configure_sms_phone(12345, "+15557654321")

    assert updated.is_verified is False
    assert updated.verification_sid == ""
    assert updated.verification_status == ""
    assert updated.verification_started_at == 0.0
    assert updated.verified_at == 0.0


@pytest.mark.asyncio
async def test_sms_test_command_returns_pending_status_for_unapproved_code(monkeypatch):
    cog = sms_mod.SMSCog(SimpleNamespace())
    interaction = _mock_interaction()
    monkeypatch.setattr(
        sms_mod,
        "check_sms_verification",
        AsyncMock(return_value=(SimpleNamespace(provider="twilio", status="pending"), False)),
    )

    await cog.test.callback(cog, interaction, code="123456")

    interaction.response.send_message.assert_awaited_once()
    message = interaction.response.send_message.await_args.args[0]
    assert "Verification status" in message
    assert "pending" in message
