"""
Tests for permissions.py — user allow-list and service permission checks.
"""

from unittest.mock import AsyncMock, MagicMock

import discord

import permissions as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# is_allowed
# ---------------------------------------------------------------------------


class TestIsAllowed:
    def test_allowed_user(self, monkeypatch):
        monkeypatch.setattr(mod, "ALLOWED_USER_IDS", [111, 222])
        assert mod.is_allowed(_fake_interaction(111)) is True

    def test_disallowed_user(self, monkeypatch):
        monkeypatch.setattr(mod, "ALLOWED_USER_IDS", [111, 222])
        assert mod.is_allowed(_fake_interaction(999)) is False

    def test_empty_allow_list_permits_everyone(self, monkeypatch):
        monkeypatch.setattr(mod, "ALLOWED_USER_IDS", [])
        assert mod.is_allowed(_fake_interaction(999)) is True


# ---------------------------------------------------------------------------
# require_auth decorator
# ---------------------------------------------------------------------------


class TestRequireAuth:
    async def test_authorized_user_executes(self, monkeypatch):
        monkeypatch.setattr(mod, "ALLOWED_USER_IDS", [111])
        called = {}

        @mod.require_auth
        async def handler(interaction):
            called["ok"] = True

        interaction = _fake_interaction(111)
        await handler(interaction)
        assert called.get("ok") is True

    async def test_unauthorized_user_blocked(self, monkeypatch):
        monkeypatch.setattr(mod, "ALLOWED_USER_IDS", [111])
        called = {}

        @mod.require_auth
        async def handler(interaction):
            called["ok"] = True

        interaction = _fake_interaction(999)
        await handler(interaction)
        assert "ok" not in called
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "not authorized" in msg.lower()

    async def test_decorator_preserves_function_name(self):
        @mod.require_auth
        async def my_command(interaction):
            pass

        assert my_command.__name__ == "my_command"


# ---------------------------------------------------------------------------
# is_service_allowed
# ---------------------------------------------------------------------------


class TestIsServiceAllowed:
    def test_no_permissions_file_allows_all(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_permissions_cache", None)
        monkeypatch.setattr(mod, "_permissions_mtime", 0.0)
        assert mod.is_service_allowed("search", "google") is True

    def test_denied_service(self, monkeypatch, tmp_path):
        perms_file = tmp_path / "permissions.yaml"
        perms_file.write_text("commands:\n  search:\n    denied_services:\n      - bing\n")
        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_permissions_cache", None)
        monkeypatch.setattr(mod, "_permissions_mtime", 0.0)
        assert mod.is_service_allowed("search", "bing") is False
        assert mod.is_service_allowed("search", "google") is True

    def test_allowed_services_whitelist(self, monkeypatch, tmp_path):
        perms_file = tmp_path / "permissions.yaml"
        perms_file.write_text("commands:\n  search:\n    allowed_services:\n      - google\n")
        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_permissions_cache", None)
        monkeypatch.setattr(mod, "_permissions_mtime", 0.0)
        assert mod.is_service_allowed("search", "google") is True
        assert mod.is_service_allowed("search", "bing") is False

    def test_caches_permissions(self, monkeypatch, tmp_path):
        perms_file = tmp_path / "permissions.yaml"
        perms_file.write_text("commands: {}\n")
        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "_permissions_cache", None)
        monkeypatch.setattr(mod, "_permissions_mtime", 0.0)

        mod.is_service_allowed("x", "y")
        first_cache = mod._permissions_cache

        mod.is_service_allowed("x", "y")
        assert mod._permissions_cache is first_cache  # same object, not reloaded
