"""
Custom API Plugin - Example of integrating external APIs.

Demonstrates:
- HTTP API calls
- Configuration management
- Error handling
- Data caching
"""

import sys
from pathlib import Path

import aiohttp

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI


class CustomAPIPlugin(Plugin):
    """Example plugin for API integration."""

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.session: aiohttp.ClientSession | None = None

    async def on_load(self) -> None:
        """Called when plugin is loaded."""
        self.api.log("Custom API plugin loading...")

        # Create HTTP session
        self.session = aiohttp.ClientSession()

        # Register skills
        self.api.register_skill(
            name="get_random_fact",
            function=self.get_random_fact,
            description="Get a random interesting fact",
            category="API Skills",
        )

        self.api.register_skill(
            name="get_cat_fact",
            function=self.get_cat_fact,
            description="Get a random cat fact",
            category="API Skills",
        )

        self.api.log("Custom API plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        # Clean up HTTP session
        if self.session:
            await self.session.close()

        self.api.log("Custom API plugin unloaded")

    async def get_random_fact(self) -> str:
        """
        Get a random interesting fact from an API.

        Returns:
            Random fact as string
        """
        if not self.session:
            return "❌ HTTP session not initialized"

        try:
            # Using uselessfacts.jsph.pl API (no auth required)
            url = "https://uselessfacts.jsph.pl/random.json?language=en"

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fact = data.get("text", "No fact found")
                    return f"💡 Random Fact: {fact}"
                else:
                    return f"❌ API returned status {resp.status}"

        except aiohttp.ClientError as e:
            self.api.log(f"API request failed: {e}", "error")
            return f"❌ Failed to fetch fact: {e}"
        except Exception as e:
            self.api.log(f"Unexpected error: {e}", "error")
            return f"❌ Error: {e}"

    async def get_cat_fact(self) -> str:
        """
        Get a random cat fact.

        Returns:
            Cat fact as string
        """
        if not self.session:
            return "❌ HTTP session not initialized"

        try:
            # Using catfact.ninja API
            url = "https://catfact.ninja/fact"

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fact = data.get("fact", "No fact found")
                    length = data.get("length", 0)
                    return f"🐱 Cat Fact ({length} chars): {fact}"
                else:
                    return f"❌ API returned status {resp.status}"

        except aiohttp.ClientError as e:
            self.api.log(f"API request failed: {e}", "error")
            return f"❌ Failed to fetch cat fact: {e}"
        except Exception as e:
            self.api.log(f"Unexpected error: {e}", "error")
            return f"❌ Error: {e}"
