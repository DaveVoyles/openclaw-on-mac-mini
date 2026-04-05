#!/usr/bin/env python3
"""
Plugin Generator - Create new OpenClaw plugins from templates.

Usage:
    python scripts/create_plugin.py

Interactive prompts guide you through plugin creation.
"""

import os
import sys
from pathlib import Path


def main():
    """Interactive plugin generator."""
    print("🔌 OpenClaw Plugin Generator\n")

    # Gather plugin information
    name = input("Plugin name (kebab-case, e.g., my-awesome-plugin): ").strip()
    if not name:
        print("❌ Plugin name required")
        return

    # Validate name
    if not all(c.isalnum() or c in "-_" for c in name):
        print("❌ Plugin name can only contain letters, numbers, hyphens, and underscores")
        return

    version = input("Version [1.0.0]: ").strip() or "1.0.0"
    author = input("Author (e.g., you@example.com): ").strip()
    if not author:
        print("❌ Author required")
        return

    description = input("Description: ").strip()
    homepage = input("Homepage URL (optional): ").strip()

    print("\n📦 Dependencies (comma-separated, e.g., aiohttp>=3.8.0)")
    deps_input = input("Dependencies [none]: ").strip()
    dependencies = [d.strip() for d in deps_input.split(",") if d.strip()]

    print("\n🔒 Permissions (comma-separated: network, storage, commands)")
    perms_input = input("Permissions [storage]: ").strip() or "storage"
    permissions = [p.strip() for p in perms_input.split(",") if p.strip()]

    # Create plugin directory
    repo_root = Path(__file__).parent.parent
    plugin_dir = repo_root / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Generate plugin.yaml
    yaml_content = f"""name: {name}
version: {version}
author: {author}
description: {description}
dependencies:
{chr(10).join(f"  - {dep}" for dep in dependencies) if dependencies else "  []"}
permissions:
{chr(10).join(f"  - {perm}" for perm in permissions)}
min_openclaw_version: 0.1.0
"""
    if homepage:
        yaml_content += f"homepage: {homepage}\n"

    with open(plugin_dir / "plugin.yaml", "w") as f:
        f.write(yaml_content)

    # Generate main.py
    class_name = "".join(word.capitalize() for word in name.replace("-", "_").split("_")) + "Plugin"

    main_content = f'''"""
{name.replace("-", " ").title()} Plugin

{description}
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from plugin_system import Plugin, PluginAPI


class {class_name}(Plugin):
    """{description}"""

    def __init__(self, api: PluginAPI):
        super().__init__(api)

    async def on_load(self) -> None:
        """Called when plugin is loaded."""
        self.api.log("{name} plugin loading...")

        # Register your skills here
        self.api.register_skill(
            name="example_skill",
            function=self.example_skill,
            description="An example skill",
            category="Custom Skills",
        )

        self.api.log("{name} plugin loaded!", "info")

    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        self.api.log("{name} plugin unloaded")

    # -------------------------------------------------------------------------
    # Skills
    # -------------------------------------------------------------------------

    async def example_skill(self, param: str = "default") -> str:
        """
        An example skill implementation.

        Args:
            param: Example parameter

        Returns:
            Result string
        """
        return f"Hello from {{param}}!"
'''

    with open(plugin_dir / "main.py", "w") as f:
        f.write(main_content)

    # Generate README.md
    readme_content = f"""# {name.replace('-', ' ').title()}

{description}

## Installation

1. Copy this directory to `plugins/{name}`
2. Restart OpenClaw or use `/plugin install {name}`

## Usage

This plugin provides the following skills:

- `{name}.example_skill` - An example skill

## Configuration

No additional configuration required.

## License

[Add your license here]

## Author

{author}
"""

    with open(plugin_dir / "README.md", "w") as f:
        f.write(readme_content)

    # Generate test file
    test_content = f'''"""
Tests for {name} plugin.
"""

import pytest
import sys
from pathlib import Path

# Add plugin to path
sys.path.insert(0, str(Path(__file__).parent))

from main import {class_name}
from plugin_system import PluginAPI


@pytest.fixture
def plugin_api(tmp_path):
    """Create a mock PluginAPI for testing."""
    return PluginAPI(
        plugin_name="{name}",
        data_dir=tmp_path / "data",
        skills_registry={{}},
        config={{}},
    )


@pytest.fixture
async def plugin(plugin_api):
    """Create plugin instance."""
    p = {class_name}(plugin_api)
    await p.on_load()
    return p


@pytest.mark.asyncio
async def test_plugin_loads(plugin):
    """Test that plugin loads successfully."""
    assert plugin.is_loaded()


@pytest.mark.asyncio
async def test_example_skill(plugin):
    """Test example skill."""
    result = await plugin.example_skill("test")
    assert "test" in result
'''

    with open(plugin_dir / "test_plugin.py", "w") as f:
        f.write(test_content)

    print(f"\n✅ Plugin '{name}' created successfully!")
    print(f"📁 Location: {plugin_dir}")
    print(f"\n📝 Next steps:")
    print(f"1. Edit {plugin_dir / 'main.py'} to implement your plugin")
    print(f"2. Test your plugin: pytest {plugin_dir / 'test_plugin.py'}")
    print(f"3. Install: /plugin install {name}")
    print(f"\n📚 See docs/PLUGIN_DEVELOPMENT.md for more information")


if __name__ == "__main__":
    main()
