"""Targeted tests for PluginLoader → PluginAPI permission wiring."""

import pytest
import yaml

from plugin_system import PluginLoader


@pytest.mark.asyncio
async def test_loader_passes_manifest_permissions_to_plugin_api(tmp_path):
    """Manifest permissions should become real PluginAPI capabilities."""
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "test-plugin"
    plugin_dir.mkdir(parents=True)

    with open(plugin_dir / "plugin.yaml", "w") as manifest_file:
        yaml.dump(
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "author": "test@example.com",
                "permissions": ["storage"],
            },
            manifest_file,
        )

    (plugin_dir / "main.py").write_text(
        """
from plugin_system import Plugin


class TestPlugin(Plugin):
    async def on_load(self):
        return None
"""
    )

    loader = PluginLoader(
        plugins_dir=plugins_dir,
        data_dir=tmp_path / "data",
        skills_registry={},
        config={"version": "0.6.0"},
    )

    plugin = await loader.load_plugin(plugin_dir)

    assert plugin is not None
    assert plugin.api.has_permission("storage")
    assert not plugin.api.has_permission("network")
