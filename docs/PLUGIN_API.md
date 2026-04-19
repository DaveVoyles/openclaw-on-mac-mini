# Plugin API Reference
<!-- Updated: 2026-04-18 -->


The OpenClaw Plugin API provides a stable interface for plugins to interact with the bot core.

## PluginAPI Class

The `PluginAPI` class is provided to each plugin during initialization and offers the following capabilities:

### Initialization

```python
from plugin_system import Plugin, PluginAPI

class MyPlugin(Plugin):
    def __init__(self, api: PluginAPI):
        super().__init__(api)
        # self.api is now available
```

### Skill Registration

#### `register_skill(name, function, description="", category="Plugin Skills")`

Register a new LLM-callable skill.

**Parameters:**
- `name` (str): Skill name (will be prefixed with plugin name)
- `function` (Callable): Async function implementing the skill
- `description` (str, optional): Human-readable description
- `category` (str, optional): Skill category for organization

**Example:**
```python
async def on_load(self):
    self.api.register_skill(
        name="greet_user",
        function=self.greet_user,
        description="Greet a user by name",
        category="Social Skills"
    )

async def greet_user(self, name: str) -> str:
    return f"Hello, {name}!"
```

**Raises:**
- `ValueError`: If skill name already exists

#### `unregister_skill(name)`

Remove a registered skill.

**Parameters:**
- `name` (str): Skill name (with or without plugin prefix)

#### `get_registered_skills()`

Get list of skills registered by this plugin.

**Returns:**
- `list[str]`: List of skill names

---

### Command Registration

#### `register_command(name, callback, description="", options=None)`

Register a Discord slash command.

**Parameters:**
- `name` (str): Command name
- `callback` (Callable): Async function to handle command
- `description` (str, optional): Command description
- `options` (list[dict], optional): Command options/parameters

**Example:**
```python
async def on_load(self):
    self.api.register_command(
        name="hello",
        callback=self.cmd_hello,
        description="Say hello",
        options=[
            {
                "name": "name",
                "description": "Your name",
                "type": "string",
                "required": True
            }
        ]
    )

async def cmd_hello(self, interaction, name: str):
    await interaction.response.send_message(f"Hello, {name}!")
```

**Note:** Commands are currently logged but not automatically registered with Discord. Full integration coming in a future version.

#### `get_registered_commands()`

Get list of commands registered by this plugin.

**Returns:**
- `list[dict]`: List of command definitions

---

### Configuration Access

#### `get_config(key, default=None)`

Get configuration value from bot config.

**Parameters:**
- `key` (str): Configuration key (supports dot notation, e.g., "discord.token")
- `default` (Any, optional): Default value if key not found

**Returns:**
- `Any`: Configuration value or default

**Example:**
```python
api_key = self.api.get_config("plugins.my_plugin.api_key", "default_key")
debug_mode = self.api.get_config("debug", False)
```

---

### Plugin Storage

#### `store_data(key, value)`

Store data in plugin's persistent storage.

**Parameters:**
- `key` (str): Storage key
- `value` (Any): Value to store (must be JSON serializable)

**Example:**
```python
self.api.store_data("user_count", 42)
self.api.store_data("settings", {"theme": "dark", "lang": "en"})
```

#### `get_data(key, default=None)`

Retrieve data from plugin storage.

**Parameters:**
- `key` (str): Storage key
- `default` (Any, optional): Default value if key not found

**Returns:**
- `Any`: Stored value or default

**Example:**
```python
count = self.api.get_data("user_count", 0)
settings = self.api.get_data("settings", {})
```

#### `delete_data(key)`

Delete data from plugin storage.

**Parameters:**
- `key` (str): Storage key

#### `get_data_file(filename)`

Get path to a file in plugin's data directory.

**Parameters:**
- `filename` (str): Name of the file

**Returns:**
- `Path`: Path object for the file

**Example:**
```python
cache_file = self.api.get_data_file("cache.json")
with open(cache_file, "w") as f:
    json.dump(data, f)
```

---

### Event System

#### `emit_event(event, **kwargs)`

Emit an event that other plugins can listen to.

**Parameters:**
- `event` (str): Event name
- `**kwargs`: Event data

**Example:**
```python
self.api.emit_event("user_joined", user_id=123, username="alice")
```

#### `on_event(event, callback)`

Register a callback for an event.

**Parameters:**
- `event` (str): Event name to listen for
- `callback` (Callable): Function to call when event occurs

**Example:**
```python
async def on_load(self):
    self.api.on_event("user_joined", self.handle_user_joined)

async def handle_user_joined(self, **kwargs):
    user_id = kwargs.get("user_id")
    self.api.log(f"User {user_id} joined!")
```

---

### Logging

#### `log(message, level="info")`

Log a message.

**Parameters:**
- `message` (str): Message to log
- `level` (str, optional): Log level (debug, info, warning, error, critical)

**Example:**
```python
self.api.log("Plugin initialized", "info")
self.api.log("Something went wrong", "error")
self.api.log("Debug information", "debug")
```

**Note:** Plugin logs are automatically prefixed with the plugin name.

---

### Utility Methods

#### `get_version()`

Get OpenClaw version.

**Returns:**
- `str`: OpenClaw version string

#### `has_permission(permission)`

Check if plugin has a specific permission.

**Parameters:**
- `permission` (str): Permission name (e.g., 'network', 'storage')

**Returns:**
- `bool`: True if permission granted

**Example:**
```python
if self.api.has_permission("network"):
    await self.make_api_call()
else:
    self.api.log("Network permission denied", "warning")
```

**Note:** Permissions are currently not enforced. This is a placeholder for future sandboxing.

---

## Plugin Lifecycle Hooks

### `on_load()`

Called when the plugin is loaded. Register skills, commands, and set up resources here.

**Must be implemented by all plugins.**

```python
async def on_load(self) -> None:
    self.api.register_skill("my_skill", self.my_skill)
    self.session = aiohttp.ClientSession()
```

### `on_unload()`

Called when the plugin is unloaded. Clean up resources here.

**Optional** (default implementation does nothing).

```python
async def on_unload(self) -> None:
    if self.session:
        await self.session.close()
```

### `on_enable()`

Called when the plugin is enabled after being disabled.

**Optional** (default sets `_enabled = True`).

```python
async def on_enable(self) -> None:
    await super().on_enable()
    self.api.log("Plugin enabled")
```

### `on_disable()`

Called when the plugin is disabled.

**Optional** (default sets `_enabled = False`).

```python
async def on_disable(self) -> None:
    await super().on_disable()
    self.api.log("Plugin disabled")
```

---

## PluginMetadata

Plugin metadata is loaded from `plugin.yaml` and accessible via `self.metadata`.

```python
@dataclass
class PluginMetadata:
    name: str
    version: str
    author: str
    description: str = ""
    dependencies: list[str] = []
    permissions: list[str] = []
    min_openclaw_version: str = "0.1.0"
    max_openclaw_version: str | None = None
    homepage: str | None = None
    repository: str | None = None
```

**Access in plugin:**
```python
if self.metadata:
    self.api.log(f"Running {self.metadata.name} v{self.metadata.version}")
```

---

## Complete Example

```python
"""
Weather Plugin - Fetches weather data from an API.
"""

import aiohttp
from plugin_system import Plugin, PluginAPI


class WeatherPlugin(Plugin):
    """Get weather information."""

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.session: aiohttp.ClientSession | None = None

    async def on_load(self) -> None:
        """Initialize plugin."""
        self.api.log("Weather plugin loading...")

        # Create HTTP session
        self.session = aiohttp.ClientSession()

        # Register skills
        self.api.register_skill(
            name="get_weather",
            function=self.get_weather,
            description="Get current weather for a city",
            category="Weather Skills"
        )

        # Load API key from config
        self.api_key = self.api.get_config("plugins.weather.api_key")
        if not self.api_key:
            self.api.log("Warning: No API key configured", "warning")

        self.api.log("Weather plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Clean up resources."""
        if self.session:
            await self.session.close()

    async def get_weather(self, city: str) -> str:
        """
        Get current weather for a city.

        Args:
            city: City name

        Returns:
            Weather description
        """
        if not self.session:
            return "❌ Plugin not initialized"

        if not self.api_key:
            return "❌ API key not configured"

        try:
            url = f"https://api.weatherapi.com/v1/current.json"
            params = {"key": self.api_key, "q": city}

            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    temp = data["current"]["temp_c"]
                    condition = data["current"]["condition"]["text"]
                    return f"🌡️ {city}: {temp}°C, {condition}"
                else:
                    return f"❌ API error: {resp.status}"

        except Exception as e:
            self.api.log(f"Weather API error: {e}", "error")
            return f"❌ Error: {e}"
```
