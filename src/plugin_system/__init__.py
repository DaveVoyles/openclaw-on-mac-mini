"""
OpenClaw Plugin System - Phase 4: Extensible Architecture

A comprehensive plugin system enabling third-party extensions with:
- Dynamic loading/unloading (hot-reload support)
- Dependency management and version checking
- Sandboxing with resource limits and permissions
- Plugin registry and discovery
- Comprehensive API for skill/command registration
"""

from .plugin_api import PluginAPI
from .plugin_base import Plugin, PluginMetadata
from .plugin_loader import PluginLoader
from .plugin_registry import PluginRegistry

__all__ = [
    "Plugin",
    "PluginMetadata",
    "PluginAPI",
    "PluginLoader",
    "PluginRegistry",
]
