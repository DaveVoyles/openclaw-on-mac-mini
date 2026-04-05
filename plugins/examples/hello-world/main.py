"""
Hello World Plugin - Minimal example plugin.

Demonstrates:
- Basic plugin structure
- Skill registration
- Simple data storage
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI


class HelloWorldPlugin(Plugin):
    """A simple hello world plugin."""

    def __init__(self, api: PluginAPI):
        super().__init__(api)

    async def on_load(self) -> None:
        """Called when plugin is loaded."""
        self.api.log("Hello World plugin loading...")

        # Register skills
        self.api.register_skill(
            name="say_hello",
            function=self.say_hello,
            description="Say hello to someone",
            category="Example Skills",
        )

        self.api.register_skill(
            name="count_hellos",
            function=self.count_hellos,
            description="Count how many times we've said hello",
            category="Example Skills",
        )

        # Initialize counter
        if self.api.get_data("hello_count") is None:
            self.api.store_data("hello_count", 0)

        self.api.log("Hello World plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        self.api.log("Goodbye from Hello World plugin!")

    async def say_hello(self, name: str = "World") -> str:
        """
        Say hello to someone.

        Args:
            name: Name to greet (default: World)

        Returns:
            Greeting message
        """
        # Increment counter
        count = self.api.get_data("hello_count", 0)
        count += 1
        self.api.store_data("hello_count", count)

        return f"Hello, {name}! 👋 (Hello #{count})"

    async def count_hellos(self) -> str:
        """
        Get the total number of hellos said.

        Returns:
            Count message
        """
        count = self.api.get_data("hello_count", 0)
        return f"I've said hello {count} times!"
