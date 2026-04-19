"""
Plugin API - Interface for plugins to interact with OpenClaw.

Provides a safe, versioned API for plugins to:
- Register skills and commands
- Access configuration
- Store persistent data
- Emit and listen to events
- Log messages
"""

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class PluginAPI:
    """
    API interface provided to plugins for interacting with OpenClaw.

    This class provides a stable interface that plugins can depend on,
    isolating them from internal OpenClaw implementation changes.
    """

    def __init__(
        self,
        plugin_name: str,
        data_dir: Path,
        skills_registry: dict[str, Any],
        config: dict[str, Any] | None = None,
        event_emitter: Any | None = None,
        allowed_permissions: Iterable[str] | str | None = None,
    ):
        """
        Initialize the PluginAPI.

        Args:
            plugin_name: Name of the plugin using this API
            data_dir: Directory for plugin data storage
            skills_registry: Global skills registry
            config: Bot configuration (read-only)
            event_emitter: Event system for plugin hooks
            allowed_permissions: Capability permissions granted to the plugin.
                The loader passes manifest permissions here; host config may
                extend them via plugins.<plugin_name>.allowed_permissions.
        """
        self.plugin_name = plugin_name
        self.data_dir = data_dir
        self._skills_registry = skills_registry
        self._config = config or {}
        self._event_emitter = event_emitter
        self._allowed_permissions = self._resolve_allowed_permissions(allowed_permissions)
        self._plugin_skills: dict[str, Callable] = {}
        self._plugin_commands: list[dict[str, Any]] = []
        self._storage: dict[str, Any] = {}
        self.logger = logging.getLogger(f"openclaw.plugin.{plugin_name}")

        # Create plugin data directory
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Skill Registration
    # -------------------------------------------------------------------------

    def register_skill(
        self,
        name: str,
        function: Callable,
        description: str = "",
        category: str = "Plugin Skills",
    ) -> None:
        """
        Register a new skill with OpenClaw.

        Args:
            name: Unique skill name (will be prefixed with plugin name)
            function: Async function implementing the skill
            description: Human-readable description
            category: Skill category for organization

        Raises:
            ValueError: If skill name already exists
        """
        full_name = f"{self.plugin_name}.{name}"

        if full_name in self._skills_registry:
            raise ValueError(f"Skill '{full_name}' already registered")

        # Bound methods expose a read-only __doc__ attribute, so set the
        # underlying function docstring when available.
        doc_target = getattr(function, "__func__", function)
        if description and not getattr(doc_target, "__doc__", None):
            try:
                doc_target.__doc__ = description
            except (AttributeError, TypeError):
                self.logger.debug(
                    "Could not attach docstring metadata to skill '%s'",
                    full_name,
                )

        self._skills_registry[full_name] = function
        self._plugin_skills[full_name] = function

        self.logger.info(f"Registered skill: {full_name}")

    def unregister_skill(self, name: str) -> None:
        """
        Unregister a skill.

        Args:
            name: Skill name (with or without plugin prefix)
        """
        full_name = name if "." in name else f"{self.plugin_name}.{name}"

        if full_name in self._skills_registry:
            del self._skills_registry[full_name]
            self._plugin_skills.pop(full_name, None)
            self.logger.info(f"Unregistered skill: {full_name}")

    def get_registered_skills(self) -> list[str]:
        """Get list of skills registered by this plugin."""
        return list(self._plugin_skills.keys())

    # -------------------------------------------------------------------------
    # Command Registration
    # -------------------------------------------------------------------------

    def register_command(
        self,
        name: str,
        callback: Callable,
        description: str = "",
        options: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Register a Discord slash command.

        Args:
            name: Command name
            callback: Async function to handle command
            description: Command description
            options: Command options/parameters

        Note:
            Commands are currently logged but not automatically registered
            with Discord. Full Discord integration coming in future version.
        """
        command = {
            "name": name,
            "callback": callback,
            "description": description,
            "options": options or [],
            "plugin": self.plugin_name,
        }
        self._plugin_commands.append(command)
        self.logger.info(f"Registered command: /{name}")

    def get_registered_commands(self) -> list[dict[str, Any]]:
        """Get list of commands registered by this plugin."""
        return self._plugin_commands.copy()

    # -------------------------------------------------------------------------
    # Configuration Access
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_permissions(permissions: Iterable[str] | str | None) -> set[str]:
        """Normalize permission values into a lowercase set."""
        if permissions is None:
            return set()

        if isinstance(permissions, str):
            permissions = [permissions]

        return {
            permission.strip().lower()
            for permission in permissions
            if isinstance(permission, str) and permission.strip()
        }

    def _resolve_config_value(self, key: str) -> tuple[bool, Any]:
        """Resolve a dotted config path from dict- or attribute-based config."""
        value: Any = self._config

        for part in key.split("."):
            if isinstance(value, dict):
                if part not in value:
                    return False, None
                value = value[part]
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                return False, None

        return True, value

    def _resolve_allowed_permissions(
        self,
        allowed_permissions: Iterable[str] | str | None,
    ) -> set[str]:
        """Build the effective permission set from manifest and config."""
        configured_permissions = self.get_config(
            f"plugins.{self.plugin_name}.allowed_permissions",
            [],
        )
        return self._normalize_permissions(allowed_permissions) | self._normalize_permissions(
            configured_permissions
        )

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value from bot config.

        Args:
            key: Configuration key (supports dot notation)
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        found, value = self._resolve_config_value(key)
        if not found or value is None:
            return default
        return value

    # -------------------------------------------------------------------------
    # Plugin Storage
    # -------------------------------------------------------------------------

    def store_data(self, key: str, value: Any) -> None:
        """
        Store data in plugin's persistent storage.

        Args:
            key: Storage key
            value: Value to store (must be JSON serializable)
        """
        self._storage[key] = value
        self.logger.debug(f"Stored data: {key}")

    def get_data(self, key: str, default: Any = None) -> Any:
        """
        Retrieve data from plugin storage.

        Args:
            key: Storage key
            default: Default value if key not found

        Returns:
            Stored value or default
        """
        return self._storage.get(key, default)

    def delete_data(self, key: str) -> None:
        """
        Delete data from plugin storage.

        Args:
            key: Storage key
        """
        self._storage.pop(key, None)
        self.logger.debug(f"Deleted data: {key}")

    def get_data_file(self, filename: str) -> Path:
        """
        Get path to a file in plugin's data directory.

        Args:
            filename: Name of the file

        Returns:
            Path object for the file
        """
        return self.data_dir / filename

    # -------------------------------------------------------------------------
    # Event System
    # -------------------------------------------------------------------------

    def emit_event(self, event: str, **kwargs: Any) -> None:
        """
        Emit an event that other plugins can listen to.

        Args:
            event: Event name
            **kwargs: Event data
        """
        if self._event_emitter:
            self._event_emitter.emit(event, plugin=self.plugin_name, **kwargs)
        self.logger.debug(f"Emitted event: {event}")

    def on_event(self, event: str, callback: Callable) -> None:
        """
        Register a callback for an event.

        Args:
            event: Event name to listen for
            callback: Function to call when event occurs
        """
        if self._event_emitter:
            self._event_emitter.on(event, callback)
        self.logger.debug(f"Registered event listener: {event}")

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    def log(self, message: str, level: str = "info") -> None:
        """
        Log a message.

        Args:
            message: Message to log
            level: Log level (debug, info, warning, error, critical)
        """
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        log_func(message)

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def get_version(self) -> str:
        """Get OpenClaw version."""
        return self.get_config("version", "unknown")

    def has_permission(self, permission: str) -> bool:
        """
        Check if plugin has a specific permission.

        Args:
            permission: Permission name (e.g., 'network', 'storage')

        Returns:
            True if permission granted

        Note:
            An empty manifest permissions list grants no sandbox capabilities.
            Hosts may explicitly extend capabilities with
            plugins.<plugin_name>.allowed_permissions in config.
        """
        normalized_permission = permission.strip().lower()
        if not normalized_permission:
            return False
        return normalized_permission in self._allowed_permissions
