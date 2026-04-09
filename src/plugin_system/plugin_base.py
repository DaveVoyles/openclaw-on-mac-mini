"""
Plugin Base Class - Core abstraction for OpenClaw plugins.

All plugins must inherit from the Plugin base class and implement
the required lifecycle hooks.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("openclaw.plugin_system")


@dataclass
class PluginMetadata:
    """Plugin metadata from manifest."""

    name: str
    version: str
    author: str
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    permission_level: str = "MEMBER"  # PermissionLevel name for Discord invocation gate
    min_openclaw_version: str = "0.1.0"
    max_openclaw_version: str | None = None
    homepage: str | None = None
    repository: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "dependencies": self.dependencies,
            "permissions": self.permissions,
            "permission_level": self.permission_level,
            "min_openclaw_version": self.min_openclaw_version,
            "max_openclaw_version": self.max_openclaw_version,
            "homepage": self.homepage,
            "repository": self.repository,
        }


class Plugin(ABC):
    """
    Base class for all OpenClaw plugins.

    Example:
        class MyPlugin(Plugin):
            def __init__(self, api: PluginAPI):
                super().__init__(api)
                self.metadata = PluginMetadata(
                    name="my-plugin",
                    version="1.0.0",
                    author="me@example.com",
                    description="My awesome plugin"
                )

            async def on_load(self):
                # Register skills, commands, etc.
                self.api.register_skill("my_skill", self.my_skill_func)

            async def my_skill_func(self, param: str) -> str:
                return f"Hello {param}!"
    """

    def __init__(self, api: "PluginAPI"):  # noqa: F821
        """
        Initialize the plugin.

        Args:
            api: PluginAPI instance for interacting with OpenClaw
        """
        self.api = api
        self.metadata: PluginMetadata | None = None
        self._loaded = False
        self._enabled = True

    @abstractmethod
    async def on_load(self) -> None:
        """
        Called when the plugin is loaded.
        Register skills, commands, and set up resources here.

        Raises:
            Exception: If plugin fails to load
        """
        pass

    async def on_unload(self) -> None:
        """
        Called when the plugin is unloaded.
        Clean up resources, unregister handlers, etc.
        Default implementation does nothing.
        """
        pass

    async def on_enable(self) -> None:
        """Called when the plugin is enabled after being disabled."""
        self._enabled = True

    async def on_disable(self) -> None:
        """Called when the plugin is disabled."""
        self._enabled = False

    def is_loaded(self) -> bool:
        """Check if plugin is currently loaded."""
        return self._loaded

    def is_enabled(self) -> bool:
        """Check if plugin is currently enabled."""
        return self._enabled

    def __repr__(self) -> str:
        """String representation of plugin."""
        if self.metadata:
            return f"<Plugin {self.metadata.name} v{self.metadata.version}>"
        return f"<Plugin {self.__class__.__name__}>"
