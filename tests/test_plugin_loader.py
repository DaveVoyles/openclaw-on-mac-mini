"""
Tests for plugin loader.
"""

from pathlib import Path

import pytest
import yaml

from plugin_system import PluginLoader, PluginMetadata


class TestPluginLoader:
    """Test PluginLoader class."""

    @pytest.mark.asyncio
    async def test_discover_plugins(self, tmp_path):
        """Test plugin discovery."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        # Create valid plugin
        plugin1 = plugins_dir / "plugin1"
        plugin1.mkdir()
        (plugin1 / "plugin.yaml").write_text("name: plugin1\nversion: 1.0.0\nauthor: test")

        # Create another valid plugin
        plugin2 = plugins_dir / "plugin2"
        plugin2.mkdir()
        (plugin2 / "plugin.yaml").write_text("name: plugin2\nversion: 1.0.0\nauthor: test")

        # Create invalid directory (no manifest)
        (plugins_dir / "invalid").mkdir()

        # Create hidden directory (should be skipped)
        (plugins_dir / ".hidden").mkdir()

        loader = PluginLoader(
            plugins_dir=plugins_dir,
            data_dir=tmp_path / "data",
            skills_registry={},
        )

        discovered = await loader.discover_plugins()
        names = [p.name for p in discovered]

        assert "plugin1" in names
        assert "plugin2" in names
        assert "invalid" not in names
        assert ".hidden" not in names

    def test_load_manifest_valid(self, tmp_path):
        """Test loading valid manifest."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()

        manifest_data = {
            "name": "test-plugin",
            "version": "1.0.0",
            "author": "test@example.com",
            "description": "Test plugin",
            "dependencies": ["aiohttp>=3.8.0"],
            "permissions": ["network", "storage"],
            "min_openclaw_version": "0.1.0",
        }

        with open(plugin_dir / "plugin.yaml", "w") as f:
            yaml.dump(manifest_data, f)

        loader = PluginLoader(
            plugins_dir=tmp_path,
            data_dir=tmp_path / "data",
            skills_registry={},
        )

        metadata = loader.load_manifest(plugin_dir)

        assert metadata is not None
        assert metadata.name == "test-plugin"
        assert metadata.version == "1.0.0"
        assert metadata.author == "test@example.com"
        assert "aiohttp" in metadata.dependencies[0]

    def test_load_manifest_missing_required(self, tmp_path):
        """Test loading manifest with missing required fields."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()

        # Missing 'author'
        manifest_data = {
            "name": "test-plugin",
            "version": "1.0.0",
        }

        with open(plugin_dir / "plugin.yaml", "w") as f:
            yaml.dump(manifest_data, f)

        loader = PluginLoader(
            plugins_dir=tmp_path,
            data_dir=tmp_path / "data",
            skills_registry={},
        )

        metadata = loader.load_manifest(plugin_dir)
        assert metadata is None

    def test_load_manifest_invalid_yaml(self, tmp_path):
        """Test loading invalid YAML."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()

        (plugin_dir / "plugin.yaml").write_text("invalid: yaml: content:")

        loader = PluginLoader(
            plugins_dir=tmp_path,
            data_dir=tmp_path / "data",
            skills_registry={},
        )

        metadata = loader.load_manifest(plugin_dir)
        assert metadata is None

    def test_validate_dependencies(self):
        """Test dependency validation."""
        loader = PluginLoader(
            plugins_dir=Path("."),
            data_dir=Path("."),
            skills_registry={},
        )

        # No dependencies
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            author="test",
            dependencies=[],
        )
        valid, _ = loader.validate_dependencies(metadata)
        assert valid

        # Valid dependency (pytest should be installed)
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            author="test",
            dependencies=["pytest>=7.0.0"],
        )
        valid, _ = loader.validate_dependencies(metadata)
        assert valid

        # Invalid dependency
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            author="test",
            dependencies=["nonexistent_package_xyz>=1.0.0"],
        )
        valid, error = loader.validate_dependencies(metadata)
        assert not valid
        assert "nonexistent_package_xyz" in error

    def test_validate_version_current_version_within_range(self):
        """Test version validation using the project version fallback."""
        loader = PluginLoader(
            plugins_dir=Path("."),
            data_dir=Path("."),
            skills_registry={},
        )

        current_version, source = loader._get_openclaw_version()
        assert current_version is not None
        assert source == "config/config.yaml"

        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            author="test",
            min_openclaw_version=str(current_version),
            max_openclaw_version=str(current_version),
        )

        valid, _ = loader.validate_version(metadata)
        assert valid

    def test_validate_version_rejects_below_minimum(self):
        """Test version validation rejects plugins that require a newer OpenClaw."""
        loader = PluginLoader(
            plugins_dir=Path("."),
            data_dir=Path("."),
            skills_registry={},
            config={"version": "0.6.0"},
        )

        metadata = PluginMetadata(
            name="too-new-plugin",
            version="1.0.0",
            author="test",
            min_openclaw_version="0.6.1",
        )

        valid, error = loader.validate_version(metadata)

        assert not valid
        assert "requires OpenClaw >= 0.6.1" in error
        assert "current version is 0.6.0" in error

    def test_validate_version_rejects_above_maximum(self):
        """Test version validation rejects plugins that only support older OpenClaw versions."""
        loader = PluginLoader(
            plugins_dir=Path("."),
            data_dir=Path("."),
            skills_registry={},
            config={"version": "0.6.0"},
        )

        metadata = PluginMetadata(
            name="legacy-plugin",
            version="1.0.0",
            author="test",
            min_openclaw_version="0.1.0",
            max_openclaw_version="0.5.9",
        )

        valid, error = loader.validate_version(metadata)

        assert not valid
        assert "supports OpenClaw <= 0.5.9" in error
        assert "current version is 0.6.0" in error

    @pytest.mark.parametrize(
        ("config", "metadata", "expected_error"),
        [
            (
                {"version": "0.6.beta"},
                PluginMetadata(name="bad-current", version="1.0.0", author="test"),
                "Invalid current OpenClaw version",
            ),
            (
                {"version": "0.6.0"},
                PluginMetadata(
                    name="bad-min",
                    version="1.0.0",
                    author="test",
                    min_openclaw_version="0..1",
                ),
                "Invalid plugin bad-min min_openclaw_version",
            ),
            (
                {"version": "0.6.0"},
                PluginMetadata(
                    name="bad-max",
                    version="1.0.0",
                    author="test",
                    max_openclaw_version="1.0.0-beta",
                ),
                "Invalid plugin bad-max max_openclaw_version",
            ),
        ],
    )
    def test_validate_version_handles_malformed_versions_cleanly(self, config, metadata, expected_error):
        """Test version validation reports malformed versions without crashing."""
        loader = PluginLoader(
            plugins_dir=Path("."),
            data_dir=Path("."),
            skills_registry={},
            config=config,
        )

        valid, error = loader.validate_version(metadata)

        assert not valid
        assert expected_error in error

    @pytest.mark.asyncio
    async def test_load_plugin_success(self, valid_plugin_dir):
        """Test successfully loading a plugin."""
        loader = PluginLoader(
            plugins_dir=valid_plugin_dir.parent,
            data_dir=valid_plugin_dir.parent / "data",
            skills_registry={},
        )

        plugin = await loader.load_plugin(valid_plugin_dir)

        assert plugin is not None
        assert plugin.metadata is not None
        assert plugin.metadata.name == "test-plugin"
        assert plugin.is_loaded()

    @pytest.mark.asyncio
    async def test_load_plugin_no_main(self, tmp_path):
        """Test loading plugin without main.py."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()

        # Create valid manifest but no main.py
        manifest = {
            "name": "test-plugin",
            "version": "1.0.0",
            "author": "test@example.com",
        }
        with open(plugin_dir / "plugin.yaml", "w") as f:
            yaml.dump(manifest, f)

        loader = PluginLoader(
            plugins_dir=tmp_path,
            data_dir=tmp_path / "data",
            skills_registry={},
        )

        plugin = await loader.load_plugin(plugin_dir)
        assert plugin is None

    @pytest.mark.asyncio
    async def test_unload_plugin(self, loaded_plugin):
        """Test unloading a plugin."""
        plugin, loader = loaded_plugin

        assert plugin.is_loaded()

        success = await loader.unload_plugin(plugin)

        assert success
        assert not plugin.is_loaded()

    @pytest.mark.asyncio
    async def test_reload_plugin(self, loaded_plugin):
        """Test reloading a plugin."""
        plugin, loader = loaded_plugin

        new_plugin = await loader.reload_plugin(plugin)

        assert new_plugin is not None
        assert new_plugin.is_loaded()
        assert new_plugin.metadata.name == plugin.metadata.name


@pytest.fixture
def valid_plugin_dir(tmp_path):
    """Create a valid plugin directory for testing."""
    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()

    # Create manifest
    manifest = {
        "name": "test-plugin",
        "version": "1.0.0",
        "author": "test@example.com",
        "description": "Test plugin",
        "dependencies": [],
        "permissions": ["storage"],
    }
    with open(plugin_dir / "plugin.yaml", "w") as f:
        yaml.dump(manifest, f)

    # Create main.py
    main_content = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI

class TestPlugin(Plugin):
    async def on_load(self):
        self.api.register_skill("test_skill", self.test_skill)
        self._loaded = True

    async def test_skill(self):
        return "test result"
"""
    (plugin_dir / "main.py").write_text(main_content)

    return plugin_dir


@pytest.fixture
async def loaded_plugin(valid_plugin_dir):
    """Create and load a plugin for testing."""
    loader = PluginLoader(
        plugins_dir=valid_plugin_dir.parent,
        data_dir=valid_plugin_dir.parent / "data",
        skills_registry={},
    )

    plugin = await loader.load_plugin(valid_plugin_dir)
    return plugin, loader
