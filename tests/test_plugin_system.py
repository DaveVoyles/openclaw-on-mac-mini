"""
Tests for plugin system core components.
"""

from pathlib import Path

import pytest

from plugin_system import Plugin, PluginAPI, PluginMetadata


class TestPluginMetadata:
    """Test PluginMetadata dataclass."""

    def test_metadata_creation(self):
        """Test creating plugin metadata."""
        metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            author="test@example.com",
            description="Test plugin",
            dependencies=["aiohttp>=3.8.0"],
            permissions=["network", "storage"],
        )

        assert metadata.name == "test-plugin"
        assert metadata.version == "1.0.0"
        assert metadata.author == "test@example.com"
        assert "aiohttp" in metadata.dependencies[0]
        assert "network" in metadata.permissions

    def test_metadata_to_dict(self):
        """Test converting metadata to dictionary."""
        metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            author="test@example.com",
        )

        data = metadata.to_dict()
        assert isinstance(data, dict)
        assert data["name"] == "test-plugin"
        assert data["version"] == "1.0.0"
        assert data["author"] == "test@example.com"

    def test_metadata_defaults(self):
        """Test metadata default values."""
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            author="test@example.com",
        )

        assert metadata.description == ""
        assert metadata.dependencies == []
        assert metadata.permissions == []
        assert metadata.min_openclaw_version == "0.1.0"
        assert metadata.max_openclaw_version is None


class TestPluginBase:
    """Test Plugin base class."""

    def test_plugin_initialization(self, plugin_api):
        """Test plugin initialization."""

        class TestPlugin(Plugin):
            async def on_load(self):
                pass

        plugin = TestPlugin(plugin_api)
        assert plugin.api == plugin_api
        assert not plugin.is_loaded()
        assert plugin.is_enabled()

    def test_plugin_repr(self, plugin_api):
        """Test plugin string representation."""

        class TestPlugin(Plugin):
            async def on_load(self):
                pass

        plugin = TestPlugin(plugin_api)
        assert "TestPlugin" in repr(plugin)

        # With metadata
        plugin.metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            author="test@example.com",
        )
        assert "test-plugin" in repr(plugin)
        assert "1.0.0" in repr(plugin)

    @pytest.mark.asyncio
    async def test_plugin_enable_disable(self, plugin_api):
        """Test plugin enable/disable."""

        class TestPlugin(Plugin):
            async def on_load(self):
                pass

        plugin = TestPlugin(plugin_api)
        assert plugin.is_enabled()

        await plugin.on_disable()
        assert not plugin.is_enabled()

        await plugin.on_enable()
        assert plugin.is_enabled()

    def test_plugin_must_implement_on_load(self, plugin_api):
        """Test that plugins must implement on_load."""

        with pytest.raises(TypeError, match="abstract"):
            # Cannot instantiate abstract class without on_load
            class IncompletePlugin(Plugin):
                pass

            IncompletePlugin(plugin_api)


class TestPluginAPI:
    """Test PluginAPI class."""

    def test_api_initialization(self, tmp_path):
        """Test API initialization."""
        api = PluginAPI(
            plugin_name="test-plugin",
            data_dir=tmp_path / "data",
            skills_registry={},
            config={"key": "value"},
        )

        assert api.plugin_name == "test-plugin"
        assert api.data_dir.exists()
        assert api.get_config("key") == "value"

    def test_skill_registration(self, plugin_api):
        """Test skill registration."""

        async def test_skill(param: str) -> str:
            return f"Result: {param}"

        # Register skill
        plugin_api.register_skill(
            name="test_skill",
            function=test_skill,
            description="Test skill",
        )

        # Verify registration
        assert "test-plugin.test_skill" in plugin_api.get_registered_skills()
        assert "test-plugin.test_skill" in plugin_api._skills_registry

    def test_skill_registration_conflict(self, plugin_api):
        """Test skill name conflict detection."""

        async def test_skill():
            pass

        # Register once
        plugin_api.register_skill("test_skill", test_skill)

        # Try to register again
        with pytest.raises(ValueError, match="already registered"):
            plugin_api.register_skill("test_skill", test_skill)

    def test_skill_unregistration(self, plugin_api):
        """Test skill unregistration."""

        async def test_skill():
            pass

        # Register and unregister
        plugin_api.register_skill("test_skill", test_skill)
        assert len(plugin_api.get_registered_skills()) == 1

        plugin_api.unregister_skill("test_skill")
        assert len(plugin_api.get_registered_skills()) == 0

    def test_command_registration(self, plugin_api):
        """Test command registration."""

        async def test_command(interaction):
            pass

        plugin_api.register_command(
            name="test",
            callback=test_command,
            description="Test command",
            options=[{"name": "param", "type": "string"}],
        )

        commands = plugin_api.get_registered_commands()
        assert len(commands) == 1
        assert commands[0]["name"] == "test"
        assert commands[0]["plugin"] == "test-plugin"

    def test_config_access(self, tmp_path):
        """Test configuration access."""
        config = {
            "key1": "value1",
            "nested": {
                "key2": "value2",
            },
        }

        api = PluginAPI(
            plugin_name="test",
            data_dir=tmp_path,
            skills_registry={},
            config=config,
        )

        assert api.get_config("key1") == "value1"
        assert api.get_config("nested.key2") == "value2"
        assert api.get_config("nonexistent", "default") == "default"

    def test_data_storage(self, plugin_api):
        """Test plugin data storage."""
        # Store data
        plugin_api.store_data("count", 42)
        plugin_api.store_data("settings", {"theme": "dark"})

        # Retrieve data
        assert plugin_api.get_data("count") == 42
        assert plugin_api.get_data("settings")["theme"] == "dark"
        assert plugin_api.get_data("nonexistent", "default") == "default"

        # Delete data
        plugin_api.delete_data("count")
        assert plugin_api.get_data("count") is None

    def test_data_file_access(self, plugin_api):
        """Test data file path access."""
        file_path = plugin_api.get_data_file("test.json")

        assert file_path.parent == plugin_api.data_dir
        assert file_path.name == "test.json"
        assert isinstance(file_path, Path)

    def test_logging(self, plugin_api, caplog):
        """Test logging functionality."""
        plugin_api.log("Test message", "info")
        plugin_api.log("Debug message", "debug")
        plugin_api.log("Warning message", "warning")

        # Logger should be named after plugin
        assert "test-plugin" in plugin_api.logger.name

    def test_has_permission(self, plugin_api):
        """Test permission checking."""
        # Empty manifest permissions should not silently grant capabilities.
        assert not plugin_api.has_permission("network")
        assert not plugin_api.has_permission("storage")
        assert not plugin_api.has_permission("any_permission")


@pytest.fixture
def plugin_api(tmp_path):
    """Create a PluginAPI instance for testing."""
    return PluginAPI(
        plugin_name="test-plugin",
        data_dir=tmp_path / "plugin_data",
        skills_registry={},
        config={"debug": True},
    )


@pytest.fixture
def test_plugin_dir(tmp_path):
    """Create a test plugin directory."""
    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()

    # Create manifest
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text("""
name: test-plugin
version: 1.0.0
author: test@example.com
description: Test plugin
dependencies: []
permissions:
  - storage
min_openclaw_version: 0.1.0
""")

    # Create main.py
    main = plugin_dir / "main.py"
    main.write_text("""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI

class TestPlugin(Plugin):
    async def on_load(self):
        self.api.register_skill("test_skill", self.test_skill)

    async def test_skill(self):
        return "test result"
""")

    return plugin_dir
