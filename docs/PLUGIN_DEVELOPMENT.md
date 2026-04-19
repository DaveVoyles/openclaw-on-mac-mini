# Plugin Development Guide
<!-- Updated: 2026-04-18 -->


A comprehensive guide to developing plugins for OpenClaw.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Plugin Structure](#plugin-structure)
3. [Creating Your First Plugin](#creating-your-first-plugin)
4. [Plugin Manifest](#plugin-manifest)
5. [Registering Skills](#registering-skills)
6. [Discord Commands](#discord-commands)
7. [Storage and Configuration](#storage-and-configuration)
8. [Event System](#event-system)
9. [Testing](#testing)
10. [Best Practices](#best-practices)
11. [Security Considerations](#security-considerations)
12. [Publishing](#publishing)

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- OpenClaw installation
- Basic understanding of Python async/await

### Quick Start

Use the plugin generator to create a new plugin:

```bash
python scripts/create_plugin.py
```

Follow the interactive prompts to scaffold your plugin.

---

## Plugin Structure

A plugin consists of these essential files:

```
my-plugin/
├── plugin.yaml      # Plugin manifest (required)
├── main.py          # Plugin implementation (required)
├── README.md        # Documentation (recommended)
└── test_plugin.py   # Tests (recommended)
```

### Minimal Example

**plugin.yaml:**
```yaml
name: my-plugin
version: 1.0.0
author: you@example.com
description: My awesome plugin
dependencies: []
permissions:
  - storage
min_openclaw_version: 0.1.0
```

**main.py:**
```python
from plugin_system import Plugin, PluginAPI

class MyPlugin(Plugin):
    def __init__(self, api: PluginAPI):
        super().__init__(api)

    async def on_load(self) -> None:
        self.api.log("Plugin loaded!")
        self.api.register_skill(
            name="hello",
            function=self.hello,
            description="Say hello"
        )

    async def hello(self, name: str = "World") -> str:
        return f"Hello, {name}!"
```

---

## Creating Your First Plugin

### Step 1: Generate Plugin Scaffold

```bash
python scripts/create_plugin.py
```

Answer the prompts:
- **Plugin name:** `my-awesome-plugin`
- **Version:** `1.0.0`
- **Author:** `your@email.com`
- **Description:** `Does something awesome`
- **Dependencies:** (leave empty or specify)
- **Permissions:** `storage, network`

### Step 2: Implement Your Plugin

Edit `plugins/my-awesome-plugin/main.py`:

```python
"""
My Awesome Plugin
"""

import sys
from pathlib import Path
import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI


class MyAwesomePlugin(Plugin):
    """Does something awesome."""

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.session = None

    async def on_load(self) -> None:
        """Initialize plugin."""
        self.api.log("My Awesome Plugin loading...")

        # Set up resources
        self.session = aiohttp.ClientSession()

        # Register skills
        self.api.register_skill(
            name="fetch_data",
            function=self.fetch_data,
            description="Fetch data from an API",
            category="Data Skills"
        )

        self.api.log("My Awesome Plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Clean up."""
        if self.session:
            await self.session.close()

    async def fetch_data(self, url: str) -> str:
        """
        Fetch data from a URL.

        Args:
            url: URL to fetch from

        Returns:
            Response text or error message
        """
        if not self.session:
            return "❌ Session not initialized"

        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return f"✅ Fetched {len(text)} bytes from {url}"
                else:
                    return f"❌ HTTP {resp.status}"
        except Exception as e:
            self.api.log(f"Fetch error: {e}", "error")
            return f"❌ Error: {e}"
```

### Step 3: Test Your Plugin

```bash
pytest plugins/my-awesome-plugin/test_plugin.py
```

### Step 4: Install Plugin

Via Discord command:
```
/plugin install path:plugins/my-awesome-plugin
```

Or programmatically in bot startup code.

---

## Plugin Manifest

The `plugin.yaml` file defines plugin metadata and requirements.

### Required Fields

```yaml
name: my-plugin           # Unique identifier (kebab-case)
version: 1.0.0            # Semantic versioning
author: you@example.com   # Contact email
```

### Optional Fields

```yaml
description: Plugin description
homepage: https://github.com/user/my-plugin
repository: https://github.com/user/my-plugin

# Python package dependencies
dependencies:
  - aiohttp>=3.8.0
  - requests>=2.28.0

# Required permissions
permissions:
  - network   # HTTP requests
  - storage   # Persistent data
  - commands  # Discord commands

# Version constraints
min_openclaw_version: 0.1.0
max_openclaw_version: 2.0.0
```

### Dependency Format

Dependencies follow pip requirement format:

```yaml
dependencies:
  - package_name>=1.0.0     # Minimum version
  - another_pkg==2.1.0      # Exact version
  - third_pkg~=3.0          # Compatible version
```

---

## Registering Skills

Skills are LLM-callable functions exposed to OpenClaw.

### Basic Skill Registration

```python
async def on_load(self):
    self.api.register_skill(
        name="skill_name",
        function=self.skill_function,
        description="What this skill does",
        category="Skill Category"
    )

async def skill_function(self, param: str) -> str:
    """
    Skill implementation.

    Args:
        param: Parameter description

    Returns:
        Result string
    """
    return f"Result: {param}"
```

### Skill Naming

- Skills are automatically prefixed: `plugin-name.skill_name`
- Use descriptive names: `get_weather`, `calculate_age`, `send_email`
- Avoid conflicts with existing skills

### Type Hints

Use type hints for better LLM understanding:

```python
async def search_users(
    self,
    query: str,
    limit: int = 10,
    include_inactive: bool = False
) -> str:
    """Search for users."""
    pass
```

### Docstrings

Write clear docstrings—the LLM reads them:

```python
async def calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """
    Calculate distance between two GPS coordinates.

    Uses the Haversine formula to compute great-circle distance.

    Args:
        lat1: Latitude of first point (decimal degrees)
        lon1: Longitude of first point (decimal degrees)
        lat2: Latitude of second point (decimal degrees)
        lon2: Longitude of second point (decimal degrees)

    Returns:
        Distance in kilometers, formatted as string
    """
    pass
```

---

## Discord Commands

Register Discord slash commands (currently logged, not auto-registered):

```python
async def on_load(self):
    self.api.register_command(
        name="mycommand",
        callback=self.handle_command,
        description="Command description",
        options=[
            {
                "name": "param1",
                "description": "First parameter",
                "type": "string",
                "required": True
            },
            {
                "name": "param2",
                "description": "Second parameter",
                "type": "integer",
                "required": False
            }
        ]
    )

async def handle_command(self, interaction, param1: str, param2: int = 0):
    """Handle the command."""
    await interaction.response.send_message(
        f"Received: {param1}, {param2}"
    )
```

### Option Types

- `"string"` - Text input
- `"integer"` - Whole number
- `"number"` - Decimal number
- `"boolean"` - True/false
- `"user"` - User mention
- `"channel"` - Channel mention
- `"role"` - Role mention

---

## Storage and Configuration

### Plugin Data Storage

Store arbitrary data:

```python
# Store
self.api.store_data("user_count", 42)
self.api.store_data("settings", {"theme": "dark"})

# Retrieve
count = self.api.get_data("user_count", default=0)
settings = self.api.get_data("settings", default={})

# Delete
self.api.delete_data("user_count")
```

### File Storage

Access plugin data directory:

```python
# Get file path
cache_file = self.api.get_data_file("cache.json")

# Read/write
import json
with open(cache_file, "w") as f:
    json.dump(data, f)

with open(cache_file) as f:
    data = json.load(f)
```

### Configuration Access

Read bot configuration:

```python
# Get config value
api_key = self.api.get_config("plugins.my_plugin.api_key")
debug = self.api.get_config("debug", False)

# Nested config (dot notation)
db_host = self.api.get_config("database.host", "localhost")
```

**Configuration in bot config:**
```yaml
plugins:
  my_plugin:
    api_key: "secret_key_here"
    endpoint: "https://api.example.com"
```

---

## Event System

Emit and listen to events for plugin communication.

### Emitting Events

```python
self.api.emit_event("user_action", user_id=123, action="login")
```

### Listening to Events

```python
async def on_load(self):
    self.api.on_event("user_action", self.handle_user_action)

async def handle_user_action(self, **kwargs):
    user_id = kwargs.get("user_id")
    action = kwargs.get("action")
    self.api.log(f"User {user_id} performed {action}")
```

---

## Testing

### Test Structure

```python
import pytest
from main import MyPlugin
from plugin_system import PluginAPI


@pytest.fixture
def plugin_api(tmp_path):
    """Create test API instance."""
    return PluginAPI(
        plugin_name="my-plugin",
        data_dir=tmp_path / "data",
        skills_registry={},
        config={}
    )


@pytest.fixture
async def plugin(plugin_api):
    """Create and load plugin."""
    p = MyPlugin(plugin_api)
    await p.on_load()
    return p


@pytest.mark.asyncio
async def test_plugin_loads(plugin):
    """Test plugin loads successfully."""
    assert plugin.is_loaded()


@pytest.mark.asyncio
async def test_my_skill(plugin):
    """Test skill functionality."""
    result = await plugin.my_skill("test")
    assert "test" in result
```

### Run Tests

```bash
# Single plugin
pytest plugins/my-plugin/test_plugin.py

# All plugin tests
pytest plugins/ -v

# With coverage
pytest plugins/my-plugin/ --cov=plugins/my-plugin
```

---

## Best Practices

### 1. Error Handling

Always handle errors gracefully:

```python
async def risky_operation(self) -> str:
    try:
        result = await self.make_api_call()
        return f"✅ Success: {result}"
    except aiohttp.ClientError as e:
        self.api.log(f"API error: {e}", "error")
        return f"❌ Network error: {e}"
    except Exception as e:
        self.api.log(f"Unexpected error: {e}", "error")
        return f"❌ Error: {e}"
```

### 2. Resource Cleanup

Clean up in `on_unload()`:

```python
async def on_load(self):
    self.session = aiohttp.ClientSession()
    self.db_pool = await create_pool()

async def on_unload(self):
    if self.session:
        await self.session.close()
    if self.db_pool:
        await self.db_pool.close()
```

### 3. Logging

Use appropriate log levels:

```python
self.api.log("Plugin initialized", "info")
self.api.log("Fetching data from API", "debug")
self.api.log("Deprecated feature used", "warning")
self.api.log("Failed to connect", "error")
self.api.log("Critical system failure", "critical")
```

### 4. Configuration Validation

Validate config on load:

```python
async def on_load(self):
    api_key = self.api.get_config("plugins.my_plugin.api_key")
    if not api_key:
        self.api.log("API key not configured!", "error")
        raise ValueError("API key required in config")
```

### 5. Rate Limiting

Respect API rate limits:

```python
import asyncio
from datetime import datetime, timedelta

class RateLimitedPlugin(Plugin):
    def __init__(self, api):
        super().__init__(api)
        self.last_call = None
        self.min_interval = timedelta(seconds=1)

    async def api_call(self):
        if self.last_call:
            elapsed = datetime.now() - self.last_call
            if elapsed < self.min_interval:
                wait = (self.min_interval - elapsed).total_seconds()
                await asyncio.sleep(wait)

        self.last_call = datetime.now()
        # Make API call
```

---

## Security Considerations

### 1. Input Validation

Always validate user input:

```python
async def search_user(self, user_id: int) -> str:
    if not isinstance(user_id, int) or user_id < 0:
        return "❌ Invalid user ID"

    if user_id > 999999999:
        return "❌ User ID out of range"

    # Proceed with search
```

### 2. Secrets Management

Never hardcode secrets:

```python
# ❌ Bad
API_KEY = "secret_key_123"

# ✅ Good
api_key = self.api.get_config("plugins.my_plugin.api_key")
```

### 3. SQL Injection Prevention

Use parameterized queries:

```python
# ❌ Bad
query = f"SELECT * FROM users WHERE id = {user_id}"

# ✅ Good
query = "SELECT * FROM users WHERE id = ?"
cursor.execute(query, (user_id,))
```

### 4. File System Access

Only access plugin data directory:

```python
# ✅ Good
data_file = self.api.get_data_file("cache.json")

# ❌ Bad - accessing arbitrary paths
with open("/etc/passwd") as f:  # Don't do this!
```

---

## Publishing

### 1. Prepare for Release

- [ ] Write comprehensive README.md
- [ ] Add tests (aim for >80% coverage)
- [ ] Update version in plugin.yaml
- [ ] Add LICENSE file
- [ ] Document configuration requirements

### 2. GitHub Repository

Create a repository with this structure:

```
my-plugin/
├── plugin.yaml
├── main.py
├── test_plugin.py
├── README.md
├── LICENSE
└── .gitignore
```

### 3. Installation Instructions

Document how users install your plugin:

```markdown
## Installation

1. Clone into OpenClaw plugins directory:
   ```bash
   cd openclaw/plugins
   git clone https://github.com/user/my-plugin
   ```

2. Install dependencies:
   ```bash
   pip install -r my-plugin/requirements.txt
   ```

3. Configure (if needed):
   Edit `config/openclaw.yaml`:
   ```yaml
   plugins:
     my-plugin:
       api_key: "your_key_here"
   ```

4. Load plugin:
   ```
   /plugin install path:plugins/my-plugin
   ```
```

### 4. Versioning

Follow [Semantic Versioning](https://semver.org/):

- **MAJOR** version for incompatible API changes
- **MINOR** version for new features (backward compatible)
- **PATCH** version for bug fixes

---

## Additional Resources

- [Plugin API Reference](PLUGIN_API.md) - Complete API documentation
- [Example Plugins](../plugins/examples/) - Working examples
- OpenClaw Discord - Get help from the community

---

## Troubleshooting

### Plugin won't load

1. Check plugin.yaml syntax (YAML format)
2. Verify all dependencies installed
3. Check logs: `grep "plugin-name" data/logs/openclaw.log`
4. Validate main.py has Plugin subclass

### Skills not appearing

1. Verify `register_skill()` called in `on_load()`
2. Check skill name doesn't conflict
3. Ensure plugin is enabled: `/plugin list`

### Permission errors

1. Add required permission to plugin.yaml
2. Reload plugin: `/plugin reload name:my-plugin`

---

Happy plugin development! 🚀
