"""
Plugin Loader - Dynamic loading and unloading of plugins.

Handles:
- Plugin discovery and validation
- Dynamic module loading
- Hot-reload support
- Dependency resolution
- Version compatibility checking
"""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from permissions import PermissionLevel, set_plugin_permission
from .plugin_api import PluginAPI
from .plugin_base import Plugin, PluginMetadata

log = logging.getLogger("openclaw.plugin_system.loader")


class PluginLoader:
    """
    Loads and manages plugin lifecycle.

    Responsible for discovering, loading, and unloading plugins
    with proper error handling and validation.
    """

    def __init__(
        self,
        plugins_dir: Path,
        data_dir: Path,
        skills_registry: dict[str, Any],
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize the plugin loader.

        Args:
            plugins_dir: Directory containing plugins
            data_dir: Base directory for plugin data
            skills_registry: Global skills registry
            config: Bot configuration
        """
        self.plugins_dir = Path(plugins_dir)
        self.data_dir = Path(data_dir)
        self.skills_registry = skills_registry
        self.config = config or {}

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    async def discover_plugins(self) -> list[Path]:
        """
        Discover available plugins in the plugins directory.

        Returns:
            List of plugin directories containing valid plugin.yaml
        """
        plugin_dirs = []

        for item in self.plugins_dir.iterdir():
            if not item.is_dir():
                continue

            # Skip hidden directories and examples
            if item.name.startswith(".") or item.name == "__pycache__":
                continue

            manifest_path = item / "plugin.yaml"
            if manifest_path.exists():
                plugin_dirs.append(item)
                log.debug(f"Discovered plugin: {item.name}")

        return plugin_dirs

    def load_manifest(self, plugin_dir: Path) -> PluginMetadata | None:
        """
        Load and validate plugin manifest.

        Args:
            plugin_dir: Plugin directory path

        Returns:
            PluginMetadata if valid, None otherwise
        """
        manifest_path = plugin_dir / "plugin.yaml"

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict):
                log.error(f"Invalid manifest in {plugin_dir.name}: not a dictionary")
                return None

            # Required fields
            required = ["name", "version", "author"]
            for field in required:
                if field not in data:
                    log.error(f"Missing required field '{field}' in {plugin_dir.name}/plugin.yaml")
                    return None

            metadata = PluginMetadata(
                name=data["name"],
                version=data["version"],
                author=data["author"],
                description=data.get("description", ""),
                dependencies=data.get("dependencies", []),
                permissions=data.get("permissions", []),
                permission_level=data.get("permission_level", "MEMBER"),
                min_openclaw_version=data.get("min_openclaw_version", "0.1.0"),
                max_openclaw_version=data.get("max_openclaw_version"),
                homepage=data.get("homepage"),
                repository=data.get("repository"),
            )

            log.debug(f"Loaded manifest for {metadata.name} v{metadata.version}")
            return metadata

        except yaml.YAMLError as e:
            log.error(f"Failed to parse manifest in {plugin_dir.name}: {e}")
            return None
        except Exception as e:
            log.error(f"Error loading manifest from {plugin_dir.name}: {e}")
            return None

    def validate_dependencies(self, metadata: PluginMetadata) -> tuple[bool, str]:
        """
        Validate plugin dependencies.

        Args:
            metadata: Plugin metadata

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not metadata.dependencies:
            return True, ""

        missing = []
        for dep in metadata.dependencies:
            # Parse dependency (format: "package>=version")
            parts = dep.split(">=")
            package = parts[0].strip()

            try:
                importlib.import_module(package)
            except ImportError:
                missing.append(package)

        if missing:
            return False, f"Missing dependencies: {', '.join(missing)}"

        return True, ""

    def validate_version(self, metadata: PluginMetadata) -> tuple[bool, str]:
        """
        Validate OpenClaw version compatibility.

        Args:
            metadata: Plugin metadata

        Returns:
            Tuple of (is_valid, error_message)
        """
        # TODO: Implement actual version comparison
        # For now, accept all versions
        return True, ""

    async def load_plugin(self, plugin_dir: Path) -> Plugin | None:
        """
        Load a plugin from directory.

        Args:
            plugin_dir: Path to plugin directory

        Returns:
            Plugin instance if successful, None otherwise
        """
        # Load and validate manifest
        metadata = self.load_manifest(plugin_dir)
        if not metadata:
            return None

        # Validate dependencies
        deps_valid, deps_error = self.validate_dependencies(metadata)
        if not deps_valid:
            log.error(f"Plugin {metadata.name}: {deps_error}")
            return None

        # Validate version
        version_valid, version_error = self.validate_version(metadata)
        if not version_valid:
            log.error(f"Plugin {metadata.name}: {version_error}")
            return None

        # Find main plugin file
        main_file = plugin_dir / "main.py"
        if not main_file.exists():
            log.error(f"Plugin {metadata.name}: main.py not found")
            return None

        try:
            # Load plugin module
            spec = importlib.util.spec_from_file_location(
                f"plugin.{metadata.name}",
                main_file,
            )
            if not spec or not spec.loader:
                log.error(f"Failed to create spec for {metadata.name}")
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # Find Plugin class
            plugin_class = None
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, Plugin) and obj is not Plugin:
                    plugin_class = obj
                    break

            if not plugin_class:
                log.error(f"No Plugin subclass found in {metadata.name}/main.py")
                return None

            # Create plugin data directory
            plugin_data_dir = self.data_dir / metadata.name
            plugin_data_dir.mkdir(parents=True, exist_ok=True)

            # Create plugin API
            api = PluginAPI(
                plugin_name=metadata.name,
                data_dir=plugin_data_dir,
                skills_registry=self.skills_registry,
                config=self.config,
            )

            # Instantiate plugin
            plugin = plugin_class(api)
            plugin.metadata = metadata

            # Call on_load hook
            await plugin.on_load()
            plugin._loaded = True

            # Register Discord invocation permission level from manifest
            try:
                level = PermissionLevel[metadata.permission_level.upper()]
            except KeyError:
                log.warning(
                    f"Plugin {metadata.name}: unknown permission_level "
                    f"'{metadata.permission_level}', defaulting to MEMBER"
                )
                level = PermissionLevel.MEMBER
            set_plugin_permission(metadata.name, level)

            log.info(f"✓ Loaded plugin: {metadata.name} v{metadata.version}")
            return plugin

        except Exception as e:
            log.error(f"Failed to load plugin {metadata.name}: {e}", exc_info=True)
            return None

    async def unload_plugin(self, plugin: Plugin) -> bool:
        """
        Unload a plugin.

        Args:
            plugin: Plugin instance to unload

        Returns:
            True if successful, False otherwise
        """
        if not plugin.metadata:
            return False

        try:
            # Call on_unload hook
            await plugin.on_unload()

            # Unregister all skills
            for skill_name in plugin.api.get_registered_skills():
                plugin.api.unregister_skill(skill_name)

            plugin._loaded = False

            # Remove module from sys.modules
            module_name = f"plugin.{plugin.metadata.name}"
            if module_name in sys.modules:
                del sys.modules[module_name]

            log.info(f"✓ Unloaded plugin: {plugin.metadata.name}")
            return True

        except Exception as e:
            log.error(f"Failed to unload plugin {plugin.metadata.name}: {e}", exc_info=True)
            return False

    async def reload_plugin(self, plugin: Plugin) -> Plugin | None:
        """
        Reload a plugin (unload then load).

        Args:
            plugin: Plugin instance to reload

        Returns:
            New plugin instance if successful, None otherwise
        """
        if not plugin.metadata:
            return None

        plugin_dir = self.plugins_dir / plugin.metadata.name

        # Unload current version
        await self.unload_plugin(plugin)

        # Load new version
        return await self.load_plugin(plugin_dir)
