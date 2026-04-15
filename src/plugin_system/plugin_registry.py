"""
Plugin Registry - Central management of installed plugins.

Handles:
- Plugin installation and removal
- Enabling/disabling plugins
- Conflict resolution
- Plugin state persistence
- Query and listing
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .plugin_base import Plugin, PluginMetadata
from .plugin_loader import PluginLoader

log = logging.getLogger("openclaw.plugin_system.registry")


class PluginRegistry:
    """
    Central registry for managing installed plugins.

    Maintains plugin state, handles installation/removal,
    and provides query interface.
    """

    def __init__(
        self,
        plugins_dir: Path,
        data_dir: Path,
        skills_registry: dict[str, Any],
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize the plugin registry.

        Args:
            plugins_dir: Directory containing plugins
            data_dir: Directory for plugin data
            skills_registry: Global skills registry
            config: Bot configuration
        """
        self.plugins_dir = Path(plugins_dir)
        self.data_dir = Path(data_dir)
        self.state_file = self.data_dir / "plugin_state.json"

        self.loader = PluginLoader(
            plugins_dir=plugins_dir,
            data_dir=data_dir / "plugins",
            skills_registry=skills_registry,
            config=config,
        )

        self._plugins: dict[str, Plugin] = {}
        self._disabled_plugins: set[str] = set()

        # Load state
        self._load_state()

    def _load_state(self) -> None:
        """Load plugin state from disk."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file) as f:
                state = json.load(f)
                self._disabled_plugins = set(state.get("disabled", []))
            log.debug("Loaded plugin state")
        except Exception as e:
            log.error(f"Failed to load plugin state: {e}")

    def _save_state(self) -> None:
        """Save plugin state to disk."""
        try:
            state = {
                "disabled": list(self._disabled_plugins),
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
            log.debug("Saved plugin state")
        except Exception as e:
            log.error(f"Failed to save plugin state: {e}")

    async def load_all_plugins(self) -> dict[str, bool]:
        """
        Discover and load all plugins.

        Returns:
            Dictionary mapping plugin names to load success status
        """
        results = {}
        plugin_dirs = await self.loader.discover_plugins()

        for plugin_dir in plugin_dirs:
            metadata = self.loader.load_manifest(plugin_dir)
            plugin_name = metadata.name if metadata else plugin_dir.name

            if not metadata:
                results[plugin_name] = False
                continue

            # Skip disabled plugins
            if metadata.name in self._disabled_plugins or plugin_dir.name in self._disabled_plugins:
                log.info(f"Skipping disabled plugin: {plugin_name}")
                results[plugin_name] = False
                continue

            plugin, conflict = await self._load_plugin_with_conflict_checks(
                plugin_dir,
                metadata=metadata,
            )
            if plugin and plugin.metadata:
                self._plugins[plugin.metadata.name] = plugin
                results[plugin.metadata.name] = True
            else:
                if conflict:
                    log.warning(f"Skipping plugin {plugin_name}: {conflict}")
                results[plugin_name] = False

        return results

    async def install_plugin(self, plugin_dir: Path) -> tuple[bool, str]:
        """
        Install a plugin from a directory.

        Args:
            plugin_dir: Path to plugin directory

        Returns:
            Tuple of (success, message)
        """
        # Validate manifest
        metadata = self.loader.load_manifest(plugin_dir)
        if not metadata:
            return False, "Invalid or missing plugin.yaml"

        plugin, conflict = await self._load_plugin_with_conflict_checks(
            plugin_dir,
            metadata=metadata,
        )
        if not plugin or conflict:
            return False, conflict or "Failed to load plugin"

        self._plugins[metadata.name] = plugin
        self._save_state()

        return True, f"Installed {metadata.name} v{metadata.version}"

    async def uninstall_plugin(self, plugin_name: str) -> tuple[bool, str]:
        """
        Uninstall a plugin.

        Args:
            plugin_name: Name of plugin to uninstall

        Returns:
            Tuple of (success, message)
        """
        if plugin_name not in self._plugins:
            return False, f"Plugin {plugin_name} not found"

        plugin = self._plugins[plugin_name]

        # Unload plugin
        success = await self.loader.unload_plugin(plugin)
        if not success:
            return False, "Failed to unload plugin"

        del self._plugins[plugin_name]
        self._disabled_plugins.discard(plugin_name)
        self._save_state()

        return True, f"Uninstalled {plugin_name}"

    async def enable_plugin(self, plugin_name: str) -> tuple[bool, str]:
        """
        Enable a disabled plugin.

        Args:
            plugin_name: Name of plugin to enable

        Returns:
            Tuple of (success, message)
        """
        if plugin_name not in self._disabled_plugins:
            if plugin_name in self._plugins:
                return False, f"Plugin {plugin_name} already enabled"
            return False, f"Plugin {plugin_name} not found"

        # Load plugin
        plugin_dir = self.plugins_dir / plugin_name
        if not plugin_dir.exists():
            return False, f"Plugin directory not found: {plugin_dir}"

        metadata = self.loader.load_manifest(plugin_dir)
        if not metadata:
            return False, "Invalid or missing plugin.yaml"

        plugin, conflict = await self._load_plugin_with_conflict_checks(
            plugin_dir,
            metadata=metadata,
            exclude_plugin=metadata.name,
        )
        if not plugin or conflict:
            return False, conflict or "Failed to load plugin"

        self._plugins[plugin.metadata.name] = plugin
        self._disabled_plugins.remove(plugin_name)
        self._save_state()

        await plugin.on_enable()

        return True, f"Enabled {plugin_name}"

    async def disable_plugin(self, plugin_name: str) -> tuple[bool, str]:
        """
        Disable a plugin without uninstalling.

        Args:
            plugin_name: Name of plugin to disable

        Returns:
            Tuple of (success, message)
        """
        if plugin_name not in self._plugins:
            return False, f"Plugin {plugin_name} not found"

        plugin = self._plugins[plugin_name]

        await plugin.on_disable()

        # Unload plugin
        success = await self.loader.unload_plugin(plugin)
        if not success:
            return False, "Failed to unload plugin"

        del self._plugins[plugin_name]
        self._disabled_plugins.add(plugin_name)
        self._save_state()

        return True, f"Disabled {plugin_name}"

    async def reload_plugin(self, plugin_name: str) -> tuple[bool, str]:
        """
        Reload a plugin (hot-reload).

        Args:
            plugin_name: Name of plugin to reload

        Returns:
            Tuple of (success, message)
        """
        if plugin_name not in self._plugins:
            return False, f"Plugin {plugin_name} not found"

        old_plugin = self._plugins[plugin_name]

        new_plugin = await self.loader.reload_plugin(old_plugin)
        if not new_plugin:
            del self._plugins[plugin_name]
            return False, "Failed to reload plugin"

        conflict = self._check_conflicts(
            new_plugin.metadata,
            commands=new_plugin.api.get_registered_commands(),
            exclude_plugin=plugin_name,
        )
        if conflict:
            del self._plugins[plugin_name]
            return False, await self._rollback_failed_plugin_load(new_plugin, conflict)

        self._plugins[plugin_name] = new_plugin

        return True, f"Reloaded {plugin_name}"

    def get_plugin(self, plugin_name: str) -> Plugin | None:
        """
        Get a plugin by name.

        Args:
            plugin_name: Plugin name

        Returns:
            Plugin instance or None
        """
        return self._plugins.get(plugin_name)

    def list_plugins(self) -> list[PluginMetadata]:
        """
        List all loaded plugins.

        Returns:
            List of plugin metadata
        """
        metadata_list = []
        for plugin in self._plugins.values():
            if plugin.metadata:
                metadata_list.append(plugin.metadata)
        return metadata_list

    def list_disabled_plugins(self) -> list[str]:
        """
        List disabled plugins.

        Returns:
            List of disabled plugin names
        """
        return list(self._disabled_plugins)

    def get_plugin_info(self, plugin_name: str) -> dict[str, Any] | None:
        """
        Get detailed information about a plugin.

        Args:
            plugin_name: Plugin name

        Returns:
            Plugin info dictionary or None
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin or not plugin.metadata:
            return None

        return {
            "metadata": plugin.metadata.to_dict(),
            "loaded": plugin.is_loaded(),
            "enabled": plugin.is_enabled(),
            "skills": plugin.api.get_registered_skills(),
            "commands": plugin.api.get_registered_commands(),
        }

    async def _load_plugin_with_conflict_checks(
        self,
        plugin_dir: Path,
        metadata: PluginMetadata | None = None,
        *,
        exclude_plugin: str | None = None,
    ) -> tuple[Plugin | None, str | None]:
        """
        Load a plugin and roll it back if post-load conflict checks fail.

        Args:
            plugin_dir: Directory containing the plugin
            metadata: Pre-loaded manifest metadata, if already available
            exclude_plugin: Plugin name to ignore during conflict checks

        Returns:
            Tuple of (plugin, error_message)
        """
        metadata = metadata or self.loader.load_manifest(plugin_dir)
        if not metadata:
            return None, "Invalid or missing plugin.yaml"

        conflict = self._check_conflicts(metadata, exclude_plugin=exclude_plugin)
        if conflict:
            return None, conflict

        plugin = await self.loader.load_plugin(plugin_dir)
        if not plugin or not plugin.metadata:
            return None, "Failed to load plugin"

        conflict = self._check_conflicts(
            plugin.metadata,
            commands=plugin.api.get_registered_commands(),
            exclude_plugin=exclude_plugin,
        )
        if conflict:
            return None, await self._rollback_failed_plugin_load(plugin, conflict)

        return plugin, None

    async def _rollback_failed_plugin_load(self, plugin: Plugin, error: str) -> str:
        """
        Unload a plugin after a post-load validation failure.

        Args:
            plugin: Loaded plugin that must be rolled back
            error: Original validation error

        Returns:
            Final error message for callers
        """
        if await self.loader.unload_plugin(plugin):
            return error

        if plugin.metadata:
            for skill_name in plugin.api.get_registered_skills():
                plugin.api.unregister_skill(skill_name)

            plugin._loaded = False
            sys.modules.pop(f"plugin.{plugin.metadata.name}", None)

        return f"{error}. The plugin was rejected and force-cleaned after unload failed."

    def _check_conflicts(
        self,
        metadata: PluginMetadata,
        commands: list[dict[str, Any]] | None = None,
        *,
        exclude_plugin: str | None = None,
    ) -> str | None:
        """
        Check for conflicts with existing plugins.

        Args:
            metadata: Plugin metadata to check
            commands: Registered commands to validate post-load
            exclude_plugin: Plugin name to ignore during comparison

        Returns:
            Error message if conflict found, None otherwise
        """
        # Check for name conflicts
        if metadata.name != exclude_plugin and metadata.name in self._plugins:
            return f"Plugin with name '{metadata.name}' already exists"

        if metadata.name != exclude_plugin and metadata.name in self._disabled_plugins:
            return f"Plugin with name '{metadata.name}' is already installed but disabled"

        if not commands:
            return None

        existing_commands: dict[str, str] = {}
        for plugin_name, plugin in self._plugins.items():
            if plugin_name == exclude_plugin:
                continue

            for command in plugin.api.get_registered_commands():
                raw_name = command.get("name")
                if not isinstance(raw_name, str):
                    continue

                command_name = raw_name.strip().casefold()
                if command_name:
                    existing_commands[command_name] = plugin_name

        seen_commands: dict[str, str] = {}
        for command in commands:
            raw_name = command.get("name")
            if not isinstance(raw_name, str):
                continue

            display_name = raw_name.strip()
            if not display_name:
                continue

            command_name = display_name.casefold()
            if command_name in seen_commands:
                return (
                    f"Plugin '{metadata.name}' registers duplicate command '/{display_name}'"
                )

            if command_name in existing_commands:
                owner = existing_commands[command_name]
                return (
                    f"Command '/{display_name}' from plugin '{metadata.name}' "
                    f"conflicts with plugin '{owner}'"
                )

            seen_commands[command_name] = display_name

        # Skill conflicts are handled by PluginAPI: each skill is registered with a
        # "<plugin>." prefix in the global registry, so cross-plugin skill name
        # collisions are only possible when plugin names themselves conflict.

        # Check plugin version compatibility
        compat_warnings = _check_plugin_version_compat(metadata.name, metadata.to_dict())
        for warn in compat_warnings:
            log.warning(warn)

        return None


# ---------------------------------------------------------------------------
# Version compatibility helpers (module-level, used by _check_conflicts)
# ---------------------------------------------------------------------------

def _get_host_version_fallback() -> str:
    """Read OpenClaw version from pyproject.toml as fallback."""
    import pathlib
    import tomllib
    try:
        pyproject = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse 'X.Y.Z' into (X, Y, Z) tuple. Strips leading 'v' and pre-release suffix."""
    v = v.lstrip("v").split("-")[0]
    parts = v.split(".")
    return tuple(int(p) for p in parts if p.isdigit())


def _version_satisfies(current: str, minimum: str) -> bool:
    """Return True if current >= minimum."""
    try:
        return _parse_version(current) >= _parse_version(minimum)
    except Exception:
        return True  # if parsing fails, don't block loading


def _version_at_most(current: str, maximum: str) -> bool:
    """Return True if current <= maximum."""
    try:
        return _parse_version(current) <= _parse_version(maximum)
    except Exception:
        return True


def _is_valid_semver(v: str) -> bool:
    """Basic semver validity check."""
    import re
    v = v.lstrip("v")
    return bool(re.match(r'^\d+\.\d+(\.\d+)?(-[\w.]+)?(\+[\w.]+)?$', v))


def _check_plugin_version_compat(plugin_name: str, plugin_meta: dict) -> list[str]:
    """Check plugin version compatibility. Returns list of warning strings (empty = OK)."""
    warnings = []

    # Get current host version
    try:
        from importlib.metadata import version as pkg_version
        host_version = pkg_version("openclaw")
    except Exception:
        host_version = _get_host_version_fallback()

    # Check min version requirement (support multiple field name conventions)
    min_version = (
        plugin_meta.get("min_openclaw_version")
        or plugin_meta.get("min_host_version")
        or plugin_meta.get("requires_openclaw")
    )
    if min_version and not _version_satisfies(host_version, min_version):
        warnings.append(
            f"Plugin '{plugin_name}' requires OpenClaw >= {min_version} "
            f"(running {host_version}). Plugin may not function correctly."
        )

    # Check max version if declared
    max_version = (
        plugin_meta.get("max_openclaw_version")
        or plugin_meta.get("max_host_version")
    )
    if max_version and not _version_at_most(host_version, max_version):
        warnings.append(
            f"Plugin '{plugin_name}' was tested up to OpenClaw {max_version} "
            f"(running {host_version}). Compatibility not guaranteed."
        )

    # Check plugin's own version format validity
    plugin_version = plugin_meta.get("version")
    if plugin_version and not _is_valid_semver(plugin_version):
        warnings.append(
            f"Plugin '{plugin_name}' declares version '{plugin_version}' "
            f"which is not a valid semver string."
        )

    return warnings
