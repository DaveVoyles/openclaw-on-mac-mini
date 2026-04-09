"""
Tests for plugin registry conflict detection and rollback behavior.
"""

from unittest.mock import AsyncMock

import pytest

from plugin_system import PluginRegistry


def _write_plugin(
    plugin_dir,
    *,
    plugin_name: str,
    skills: list[str] | None = None,
    commands: list[str] | None = None,
) -> None:
    skills = skills or []
    commands = commands or []

    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(f"""
name: {plugin_name}
version: 1.0.0
author: test@example.com
description: Test plugin
dependencies: []
permissions:
  - storage
""")

    skill_registrations = "\n".join(
        f"""        self.api.register_skill(
            name="{skill}",
            function=self.{skill},
        )"""
        for skill in skills
    )
    command_registrations = "\n".join(
        f"""        self.api.register_command(
            name="{command}",
            callback=self.cmd_{command},
            description="Command {command}",
        )"""
        for command in commands
    )
    skill_methods = "\n\n".join(
        f"""    async def {skill}(self):
        return "{skill}"
"""
        for skill in skills
    )
    command_methods = "\n\n".join(
        f"""    async def cmd_{command}(self, interaction):
        return None
"""
        for command in commands
    )

    (plugin_dir / "main.py").write_text(f"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from plugin_system import Plugin


class TestPlugin(Plugin):
    async def on_load(self):
{skill_registrations or '        pass'}
{command_registrations}

{skill_methods}
{command_methods}
""")


@pytest.fixture
def registry(tmp_path):
    skills_registry = {}
    return (
        PluginRegistry(
            plugins_dir=tmp_path / "plugins",
            data_dir=tmp_path / "data",
            skills_registry=skills_registry,
            config={"version": "1.0.0"},
        ),
        skills_registry,
    )


class TestPluginRegistryConflicts:
    @pytest.mark.asyncio
    async def test_install_plugin_rejects_command_collision_and_rolls_back(
        self,
        tmp_path,
        registry,
    ):
        plugin_registry, skills_registry = registry
        first_plugin = tmp_path / "plugin-one"
        second_plugin = tmp_path / "plugin-two"

        _write_plugin(
            first_plugin,
            plugin_name="plugin-one",
            skills=["skill_one"],
            commands=["shared"],
        )
        _write_plugin(
            second_plugin,
            plugin_name="plugin-two",
            skills=["skill_two"],
            commands=["shared"],
        )

        success, _ = await plugin_registry.install_plugin(first_plugin)
        assert success

        success, message = await plugin_registry.install_plugin(second_plugin)

        assert not success
        assert "Command '/shared'" in message
        assert "plugin-one" in message
        assert plugin_registry.get_plugin("plugin-two") is None
        assert "plugin-one.skill_one" in skills_registry
        assert "plugin-two.skill_two" not in skills_registry
        assert [plugin.name for plugin in plugin_registry.list_plugins()] == ["plugin-one"]

    @pytest.mark.asyncio
    async def test_install_plugin_allows_distinct_commands(self, tmp_path, registry):
        plugin_registry, skills_registry = registry
        first_plugin = tmp_path / "plugin-one"
        second_plugin = tmp_path / "plugin-two"

        _write_plugin(
            first_plugin,
            plugin_name="plugin-one",
            skills=["skill_one"],
            commands=["alpha"],
        )
        _write_plugin(
            second_plugin,
            plugin_name="plugin-two",
            skills=["skill_two"],
            commands=["beta"],
        )

        first_result = await plugin_registry.install_plugin(first_plugin)
        second_result = await plugin_registry.install_plugin(second_plugin)

        assert first_result == (True, "Installed plugin-one v1.0.0")
        assert second_result == (True, "Installed plugin-two v1.0.0")
        assert "plugin-one.skill_one" in skills_registry
        assert "plugin-two.skill_two" in skills_registry
        assert {plugin.name for plugin in plugin_registry.list_plugins()} == {
            "plugin-one",
            "plugin-two",
        }

    @pytest.mark.asyncio
    async def test_load_all_plugins_skips_conflicting_commands(self, registry, monkeypatch):
        plugin_registry, skills_registry = registry
        first_plugin = plugin_registry.plugins_dir / "plugin-one"
        second_plugin = plugin_registry.plugins_dir / "plugin-two"

        _write_plugin(
            first_plugin,
            plugin_name="plugin-one",
            skills=["skill_one"],
            commands=["shared"],
        )
        _write_plugin(
            second_plugin,
            plugin_name="plugin-two",
            skills=["skill_two"],
            commands=["shared"],
        )

        monkeypatch.setattr(
            plugin_registry.loader,
            "discover_plugins",
            AsyncMock(return_value=[first_plugin, second_plugin]),
        )

        results = await plugin_registry.load_all_plugins()

        assert results == {"plugin-one": True, "plugin-two": False}
        assert plugin_registry.get_plugin("plugin-one") is not None
        assert plugin_registry.get_plugin("plugin-two") is None
        assert "plugin-one.skill_one" in skills_registry
        assert "plugin-two.skill_two" not in skills_registry
