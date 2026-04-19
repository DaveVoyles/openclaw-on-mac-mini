"""
Tests for plugin API.
"""

from pathlib import Path

import pytest

from plugin_system import PluginAPI


class TestPluginAPISkills:
    """Test skill registration in PluginAPI."""

    def test_register_skill_basic(self, plugin_api):
        """Test basic skill registration."""

        async def my_skill(param: str) -> str:
            return f"Result: {param}"

        plugin_api.register_skill("my_skill", my_skill)

        skills = plugin_api.get_registered_skills()
        assert "test-plugin.my_skill" in skills

    def test_register_skill_with_description(self, plugin_api):
        """Test skill registration with description."""

        async def documented_skill() -> str:
            return "result"

        plugin_api.register_skill(
            "documented_skill",
            documented_skill,
            description="A well-documented skill",
            category="Test Skills",
        )

        # Verify skill is registered
        assert "test-plugin.documented_skill" in plugin_api.get_registered_skills()

    def test_register_duplicate_skill_raises_error(self, plugin_api):
        """Test that registering duplicate skill raises ValueError."""

        async def skill():
            pass

        plugin_api.register_skill("duplicate", skill)

        with pytest.raises(ValueError, match="already registered"):
            plugin_api.register_skill("duplicate", skill)

    def test_register_bound_method_with_description(self, plugin_api):
        """Bound methods should register even when docstring metadata is added."""

        class SkillContainer:
            async def bound_skill(self) -> str:
                return "ok"

        container = SkillContainer()

        plugin_api.register_skill(
            "bound_skill",
            container.bound_skill,
            description="Bound method skill",
        )

        assert "test-plugin.bound_skill" in plugin_api.get_registered_skills()

    def test_unregister_skill(self, plugin_api):
        """Test skill unregistration."""

        async def skill():
            pass

        # Register
        plugin_api.register_skill("temp_skill", skill)
        assert "test-plugin.temp_skill" in plugin_api.get_registered_skills()

        # Unregister with short name
        plugin_api.unregister_skill("temp_skill")
        assert "test-plugin.temp_skill" not in plugin_api.get_registered_skills()

    def test_unregister_skill_full_name(self, plugin_api):
        """Test unregistering skill with full name."""

        async def skill():
            pass

        plugin_api.register_skill("temp_skill", skill)

        # Unregister with full name
        plugin_api.unregister_skill("test-plugin.temp_skill")
        assert "test-plugin.temp_skill" not in plugin_api.get_registered_skills()


class TestPluginAPICommands:
    """Test command registration in PluginAPI."""

    def test_register_command_basic(self, plugin_api):
        """Test basic command registration."""

        async def cmd_handler(interaction):
            pass

        plugin_api.register_command("test", cmd_handler, "Test command")

        commands = plugin_api.get_registered_commands()
        assert len(commands) == 1
        assert commands[0]["name"] == "test"
        assert commands[0]["description"] == "Test command"

    def test_register_command_with_options(self, plugin_api):
        """Test command registration with options."""

        async def cmd_handler(interaction, param1: str, param2: int):
            pass

        plugin_api.register_command(
            name="complex",
            callback=cmd_handler,
            description="Complex command",
            options=[
                {"name": "param1", "type": "string", "required": True},
                {"name": "param2", "type": "integer", "required": False},
            ],
        )

        commands = plugin_api.get_registered_commands()
        assert len(commands) == 1
        assert len(commands[0]["options"]) == 2

    def test_multiple_commands(self, plugin_api):
        """Test registering multiple commands."""

        async def handler1(interaction):
            pass

        async def handler2(interaction):
            pass

        plugin_api.register_command("cmd1", handler1)
        plugin_api.register_command("cmd2", handler2)

        commands = plugin_api.get_registered_commands()
        assert len(commands) == 2
        names = [cmd["name"] for cmd in commands]
        assert "cmd1" in names
        assert "cmd2" in names


class TestPluginAPIConfig:
    """Test configuration access in PluginAPI."""

    def test_get_config_simple(self):
        """Test getting simple config value."""
        config = {"key1": "value1", "key2": 42}

        api = PluginAPI(
            plugin_name="test",
            data_dir=Path("/tmp/test"),
            skills_registry={},
            config=config,
        )

        assert api.get_config("key1") == "value1"
        assert api.get_config("key2") == 42

    def test_get_config_nested(self):
        """Test getting nested config value with dot notation."""
        config = {
            "database": {
                "host": "localhost",
                "port": 5432,
                "credentials": {"user": "admin", "password": "secret"},
            }
        }

        api = PluginAPI(
            plugin_name="test",
            data_dir=Path("/tmp/test"),
            skills_registry={},
            config=config,
        )

        assert api.get_config("database.host") == "localhost"
        assert api.get_config("database.port") == 5432
        assert api.get_config("database.credentials.user") == "admin"

    def test_get_config_default(self, plugin_api):
        """Test config default value."""
        assert plugin_api.get_config("nonexistent", "default") == "default"
        assert plugin_api.get_config("missing.nested.key", 123) == 123

    def test_get_config_none_handling(self, plugin_api):
        """Test handling of None values in config path."""
        # Accessing nested key when parent is None
        assert plugin_api.get_config("nonexistent.nested", "default") == "default"


class TestPluginAPIStorage:
    """Test data storage in PluginAPI."""

    def test_store_and_retrieve_data(self, plugin_api):
        """Test storing and retrieving data."""
        plugin_api.store_data("count", 42)
        plugin_api.store_data("name", "test")

        assert plugin_api.get_data("count") == 42
        assert plugin_api.get_data("name") == "test"

    def test_store_complex_data(self, plugin_api):
        """Test storing complex data structures."""
        data = {
            "list": [1, 2, 3],
            "nested": {"key": "value"},
            "number": 3.14,
        }

        plugin_api.store_data("complex", data)
        retrieved = plugin_api.get_data("complex")

        assert retrieved["list"] == [1, 2, 3]
        assert retrieved["nested"]["key"] == "value"
        assert retrieved["number"] == 3.14

    def test_get_data_default(self, plugin_api):
        """Test getting data with default value."""
        assert plugin_api.get_data("nonexistent", "default") == "default"
        assert plugin_api.get_data("missing", []) == []

    def test_delete_data(self, plugin_api):
        """Test deleting data."""
        plugin_api.store_data("temp", "value")
        assert plugin_api.get_data("temp") == "value"

        plugin_api.delete_data("temp")
        assert plugin_api.get_data("temp") is None

    def test_delete_nonexistent_data(self, plugin_api):
        """Test deleting nonexistent data doesn't raise error."""
        plugin_api.delete_data("nonexistent")  # Should not raise

    def test_get_data_file(self, plugin_api):
        """Test getting data file path."""
        file_path = plugin_api.get_data_file("cache.json")

        assert isinstance(file_path, Path)
        assert file_path.name == "cache.json"
        assert file_path.parent == plugin_api.data_dir

    def test_data_directory_creation(self, tmp_path):
        """Test that data directory is created."""
        data_dir = tmp_path / "plugin_data" / "test"

        PluginAPI(
            plugin_name="test",
            data_dir=data_dir,
            skills_registry={},
        )

        assert data_dir.exists()
        assert data_dir.is_dir()


class TestPluginAPIEvents:
    """Test event system in PluginAPI."""

    def test_emit_event_no_emitter(self, plugin_api):
        """Test emitting event when no emitter configured."""
        # Should not raise error
        plugin_api.emit_event("test_event", data="value")

    def test_on_event_no_emitter(self, plugin_api):
        """Test registering event listener when no emitter configured."""

        async def handler(**kwargs):
            pass

        # Should not raise error
        plugin_api.on_event("test_event", handler)


class TestPluginAPILogging:
    """Test logging in PluginAPI."""

    def test_log_info(self, plugin_api, caplog):
        """Test info logging."""
        plugin_api.log("Test message", "info")
        # Logger exists and is named correctly
        assert "test-plugin" in plugin_api.logger.name

    def test_log_levels(self, plugin_api, caplog):
        """Test different log levels."""
        plugin_api.log("Debug", "debug")
        plugin_api.log("Info", "info")
        plugin_api.log("Warning", "warning")
        plugin_api.log("Error", "error")
        plugin_api.log("Critical", "critical")

        # Should not raise errors

    def test_log_invalid_level(self, plugin_api):
        """Test logging with invalid level falls back to info."""
        plugin_api.log("Message", "invalid_level")
        # Should not raise, defaults to info


class TestPluginAPIUtilities:
    """Test utility methods in PluginAPI."""

    def test_get_version(self):
        """Test getting OpenClaw version."""
        config = {"version": "1.2.3"}

        api = PluginAPI(
            plugin_name="test",
            data_dir=Path("/tmp/test"),
            skills_registry={},
            config=config,
        )

        assert api.get_version() == "1.2.3"

    def test_get_version_default(self, plugin_api):
        """Test getting version with no version in config."""
        version = plugin_api.get_version()
        assert version == "unknown"

    def test_plugin_api_has_permission(self, tmp_path):
        """Test declared permissions are granted."""
        api = PluginAPI(
            plugin_name="test-plugin",
            data_dir=tmp_path / "data",
            skills_registry={},
            allowed_permissions=["network", "storage", "commands"],
        )

        assert api.has_permission("network")
        assert api.has_permission("storage")
        assert api.has_permission("commands")
        assert not api.has_permission("any_permission")

    def test_has_permission_denies_missing_permission(self, tmp_path):
        """Test undeclared permissions are denied."""
        api = PluginAPI(
            plugin_name="test-plugin",
            data_dir=tmp_path / "data",
            skills_registry={},
            allowed_permissions=["network"],
        )

        assert api.has_permission("network")
        assert not api.has_permission("storage")

    def test_has_permission_empty_permissions_default_to_none(self, plugin_api):
        """Empty permission lists should not silently grant capabilities."""
        assert not plugin_api.has_permission("network")

    def test_has_permission_unknown_permission_requires_explicit_grant(self, tmp_path):
        """Unknown capabilities stay denied until the host explicitly grants them."""
        denied_api = PluginAPI(
            plugin_name="test-plugin",
            data_dir=tmp_path / "denied-data",
            skills_registry={},
        )
        assert not denied_api.has_permission("custom-capability")

        api = PluginAPI(
            plugin_name="test-plugin",
            data_dir=tmp_path / "data",
            skills_registry={},
            config={
                "plugins": {
                    "test-plugin": {
                        "allowed_permissions": ["custom-capability"],
                    }
                }
            },
        )

        assert api.has_permission("custom-capability")
        assert not api.has_permission("network")


@pytest.fixture
def plugin_api(tmp_path):
    """Create a PluginAPI instance for testing."""
    return PluginAPI(
        plugin_name="test-plugin",
        data_dir=tmp_path / "data",
        skills_registry={},
        config={"debug": True},
    )
