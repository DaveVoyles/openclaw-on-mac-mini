"""Tests for plugin permission system in permissions.py."""

from unittest.mock import MagicMock

import discord

import permissions as mod
from permissions import PermissionLevel, check_permission, get_plugin_permission, set_plugin_permission

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interaction(
    *,
    guild: discord.Guild | None = None,
    user_id: int = 1000,
    is_admin: bool = False,
    role_ids: list[int] | None = None,
) -> MagicMock:
    """Build a mock discord.Interaction."""
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock()
    user.id = user_id
    interaction.user = user
    interaction.guild = guild

    if guild is not None:
        member = MagicMock(spec=discord.Member)
        member.roles = []
        if role_ids:
            for rid in role_ids:
                role = MagicMock()
                role.id = rid
                member.roles.append(role)
        guild_permissions = MagicMock(spec=discord.Permissions)
        guild_permissions.administrator = is_admin
        member.guild_permissions = guild_permissions
        guild.get_member = MagicMock(return_value=member)

    return interaction


def _make_guild() -> MagicMock:
    return MagicMock(spec=discord.Guild)


# ---------------------------------------------------------------------------
# PUBLIC
# ---------------------------------------------------------------------------


class TestPublicLevel:
    def test_public_allows_guild_member(self):
        interaction = _make_interaction(guild=_make_guild())
        assert check_permission(PermissionLevel.PUBLIC, interaction) is True

    def test_public_allows_dm(self):
        interaction = _make_interaction(guild=None)
        assert check_permission(PermissionLevel.PUBLIC, interaction) is True


# ---------------------------------------------------------------------------
# MEMBER
# ---------------------------------------------------------------------------


class TestMemberLevel:
    def test_member_allows_guild_interaction(self):
        interaction = _make_interaction(guild=_make_guild())
        assert check_permission(PermissionLevel.MEMBER, interaction) is True

    def test_member_denies_dm(self):
        interaction = _make_interaction(guild=None)
        assert check_permission(PermissionLevel.MEMBER, interaction) is False


# ---------------------------------------------------------------------------
# TRUSTED
# ---------------------------------------------------------------------------


class TestTrustedLevel:
    ROLE_ID = 555

    def test_trusted_allows_member_with_role(self):
        guild = _make_guild()
        interaction = _make_interaction(guild=guild, role_ids=[self.ROLE_ID])
        assert check_permission(PermissionLevel.TRUSTED, interaction, trusted_role_id=self.ROLE_ID) is True

    def test_trusted_denies_member_without_role(self):
        guild = _make_guild()
        interaction = _make_interaction(guild=guild, role_ids=[999])
        assert check_permission(PermissionLevel.TRUSTED, interaction, trusted_role_id=self.ROLE_ID) is False

    def test_trusted_denies_dm(self):
        interaction = _make_interaction(guild=None)
        assert check_permission(PermissionLevel.TRUSTED, interaction, trusted_role_id=self.ROLE_ID) is False

    def test_trusted_denies_when_no_role_id_configured(self):
        guild = _make_guild()
        interaction = _make_interaction(guild=guild, role_ids=[self.ROLE_ID])
        assert check_permission(PermissionLevel.TRUSTED, interaction, trusted_role_id=None) is False

    def test_trusted_denies_when_member_not_in_guild(self):
        guild = _make_guild()
        guild.get_member = MagicMock(return_value=None)
        interaction = _make_interaction(guild=guild)
        assert check_permission(PermissionLevel.TRUSTED, interaction, trusted_role_id=self.ROLE_ID) is False


# ---------------------------------------------------------------------------
# ADMIN
# ---------------------------------------------------------------------------


class TestAdminLevel:
    def test_admin_allows_administrator(self):
        guild = _make_guild()
        interaction = _make_interaction(guild=guild, is_admin=True)
        assert check_permission(PermissionLevel.ADMIN, interaction) is True

    def test_admin_denies_non_administrator(self):
        guild = _make_guild()
        interaction = _make_interaction(guild=guild, is_admin=False)
        assert check_permission(PermissionLevel.ADMIN, interaction) is False

    def test_admin_denies_dm(self):
        interaction = _make_interaction(guild=None)
        assert check_permission(PermissionLevel.ADMIN, interaction) is False

    def test_admin_denies_when_member_not_found(self):
        guild = _make_guild()
        guild.get_member = MagicMock(return_value=None)
        interaction = _make_interaction(guild=guild)
        assert check_permission(PermissionLevel.ADMIN, interaction) is False


# ---------------------------------------------------------------------------
# OWNER
# ---------------------------------------------------------------------------


class TestOwnerLevel:
    OWNER_ID = 42

    def test_owner_allows_bot_owner(self):
        interaction = _make_interaction(user_id=self.OWNER_ID)
        assert check_permission(PermissionLevel.OWNER, interaction, owner_id=self.OWNER_ID) is True

    def test_owner_denies_non_owner(self):
        interaction = _make_interaction(user_id=999)
        assert check_permission(PermissionLevel.OWNER, interaction, owner_id=self.OWNER_ID) is False

    def test_owner_denies_when_owner_id_not_set(self):
        interaction = _make_interaction(user_id=self.OWNER_ID)
        assert check_permission(PermissionLevel.OWNER, interaction, owner_id=None) is False


# ---------------------------------------------------------------------------
# Per-plugin permission registry
# ---------------------------------------------------------------------------


class TestPluginPermissionRegistry:
    def test_unknown_plugin_returns_default_member_level(self):
        assert get_plugin_permission("nonexistent-plugin-xyz") == PermissionLevel.MEMBER

    def test_set_and_get_plugin_permission(self):
        set_plugin_permission("my-plugin", PermissionLevel.ADMIN)
        assert get_plugin_permission("my-plugin") == PermissionLevel.ADMIN
        # Cleanup
        mod._PLUGIN_PERMISSIONS.pop("my-plugin", None)

    def test_override_replaces_previous_level(self):
        set_plugin_permission("another-plugin", PermissionLevel.PUBLIC)
        set_plugin_permission("another-plugin", PermissionLevel.TRUSTED)
        assert get_plugin_permission("another-plugin") == PermissionLevel.TRUSTED
        mod._PLUGIN_PERMISSIONS.pop("another-plugin", None)

    def test_check_plugin_permission_uses_registered_level(self):
        set_plugin_permission("dm-plugin", PermissionLevel.PUBLIC)
        # Even a DM interaction should be allowed for a PUBLIC plugin
        interaction = _make_interaction(guild=None)
        from permissions import check_plugin_permission
        assert check_plugin_permission("dm-plugin", interaction) is True
        mod._PLUGIN_PERMISSIONS.pop("dm-plugin", None)
