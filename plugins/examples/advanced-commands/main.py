"""
Advanced Commands Plugin - Example Discord command registration.

Demonstrates:
- Discord command registration
- Command options and parameters
- Event handling
- Interactive workflows
"""

import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI


class AdvancedCommandsPlugin(Plugin):
    """Example plugin with Discord commands."""

    def __init__(self, api: PluginAPI):
        super().__init__(api)

    async def on_load(self) -> None:
        """Called when plugin is loaded."""
        self.api.log("Advanced Commands plugin loading...")

        # Register skills
        self.api.register_skill(
            name="get_server_time",
            function=self.get_server_time,
            description="Get current server time",
            category="Utility Skills",
        )

        self.api.register_skill(
            name="calculate_age",
            function=self.calculate_age,
            description="Calculate age from birth year",
            category="Utility Skills",
        )

        # Register Discord commands (logged, not yet auto-registered)
        self.api.register_command(
            name="time",
            callback=self.cmd_time,
            description="Show current server time",
        )

        self.api.register_command(
            name="age",
            callback=self.cmd_age,
            description="Calculate age from birth year",
            options=[
                {
                    "name": "year",
                    "description": "Birth year",
                    "type": "integer",
                    "required": True,
                }
            ],
        )

        self.api.log("Advanced Commands plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        self.api.log("Advanced Commands plugin unloaded")

    # -------------------------------------------------------------------------
    # Skills
    # -------------------------------------------------------------------------

    async def get_server_time(self) -> str:
        """
        Get the current server time.

        Returns:
            Formatted time string
        """
        now = datetime.now()
        return f"⏰ Server time: {now.strftime('%Y-%m-%d %H:%M:%S')}"

    async def calculate_age(self, birth_year: int) -> str:
        """
        Calculate age from birth year.

        Args:
            birth_year: Year of birth

        Returns:
            Age calculation result
        """
        current_year = datetime.now().year

        if birth_year > current_year:
            return f"❌ Birth year {birth_year} is in the future!"

        if birth_year < 1900:
            return f"❌ Birth year {birth_year} seems unrealistic"

        age = current_year - birth_year
        return f"📅 Born in {birth_year} → Age: {age} years old"

    # -------------------------------------------------------------------------
    # Discord Commands
    # -------------------------------------------------------------------------

    async def cmd_time(self, interaction) -> None:
        """Handle /time command."""
        result = await self.get_server_time()
        await interaction.response.send_message(result)

    async def cmd_age(self, interaction, year: int) -> None:
        """Handle /age command."""
        result = await self.calculate_age(year)
        await interaction.response.send_message(result)
