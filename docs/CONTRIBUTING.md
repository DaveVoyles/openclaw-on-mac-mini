# Contributing to OpenClaw

Guidelines for adding features, skills, and commands to the OpenClaw Discord bot.

---

## Getting Started

### 1. Clone & configure

```bash
git clone https://github.com/davevoyles/openclaw.git
cd openclaw
cp .env.example .env
# Fill in required values: DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, ALLOWED_USER_IDS, GOOGLE_API_KEY
```

### 2. Set up a virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-test.txt
```

### 3. Run tests

```bash
python -m pytest tests/ -x -q
```

### 4. Run locally (outside Docker)

```bash
source .venv/bin/activate
python src/bot.py
```

Or via Docker:

```bash
docker compose up -d --build
docker logs openclaw --tail 30 -f
```

---

## Project Structure

```
openclaw/
├── src/                    # All source code
│   ├── bot.py              # Main Discord bot + 38 slash commands
│   ├── llm.py              # Gemini + Ollama hybrid LLM dispatcher
│   ├── agent_loop.py       # Persistent plan management (8 skills)
│   ├── worker_agent.py     # Sub-agent spawning for task delegation
│   ├── mission_control.py  # Kanban task board (5 skills)
│   ├── ontology_skills.py  # Graph memory (7 skills)
│   ├── monitor_skills.py   # URL change detection (4 skills)
│   ├── rss_skills.py       # RSS/Atom feed monitoring (4 skills)
│   ├── research_agent.py   # Multi-step web research
│   ├── gateway.py          # Maton API gateway client
│   ├── calendar_skills.py  # Google Calendar OAuth
│   ├── email_skills.py     # Gmail/Outlook IMAP+SMTP
│   ├── config.py           # Centralized config (YAML + env)
│   ├── utils.py            # Shared utilities
│   └── cogs/               # Discord command groups
│       ├── analytics_cog.py
│       ├── docker_cog.py
│       ├── media_cog.py
│       └── network_cog.py
├── skills/                 # Skill registry + ClawHub bundles
│   ├── __init__.py         # SKILLS dict — central registry
│   ├── advanced_skills.py  # Media, network, Plex, reports
│   └── <bundle>/           # ClawHub skill bundles (13+)
├── tests/                  # pytest test suite
├── config/                 # Configuration files
│   ├── config.yaml         # Main bot config
│   ├── tools.yaml          # 84 Gemini tool declarations
│   ├── permissions.yaml    # Role-based access control
│   └── prompts/system.txt  # LLM system prompt
├── data/                   # Runtime data (Docker volume)
│   ├── plans/              # Agent plan Markdown files
│   ├── tasks.json          # Mission Control tasks
│   ├── memory/             # QMD, ontology, snapshots, RSS
│   └── audit/              # JSONL audit logs
├── scripts/                # Utility scripts
├── templates/              # HTML templates (dashboard)
├── docs/                   # Documentation
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

---

## Adding a New Skill

Skills are async functions that the LLM can invoke via function calling. Follow these steps:

### 1. Create the module

Create a new file in `src/` (e.g., `src/my_feature_skills.py`):

```python
"""My Feature — brief description."""

import aiohttp
from http_session import SessionManager


async def my_skill(param1: str, param2: int = 10) -> str:
    """Do the thing. Returns a formatted result string."""
    async with SessionManager() as session:
        # ... implementation ...
        pass
    return f"✅ Result: {param1} processed with {param2}"


# Export as a skill dict — keys are tool names, values are async callables
MY_FEATURE_SKILLS = {
    "my_skill": my_skill,
}
```

### 2. Register in the skill registry

Edit `skills/__init__.py` and add your skills dict:

```python
from my_feature_skills import MY_FEATURE_SKILLS

# ... in the SKILLS dict assembly section:
SKILLS.update(MY_FEATURE_SKILLS)
```

### 3. Declare the tool for Gemini

Add a tool declaration in `config/tools.yaml`:

```yaml
- name: my_skill
  description: "Do the thing with param1 and optional param2."
  parameters:
    type: object
    properties:
      param1:
        type: string
        description: "The main input"
      param2:
        type: integer
        description: "Optional count (default 10)"
    required:
      - param1
```

### 4. Add permissions (if restricted)

If the skill should be restricted to certain roles, add it to `config/permissions.yaml`.

### 5. Write tests

Create `tests/test_my_feature_skills.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_my_skill_success():
    with patch("my_feature_skills.SessionManager") as mock_session:
        # ... mock external calls ...
        from my_feature_skills import my_skill
        result = await my_skill("hello", 5)
        assert "hello" in result
```

### 6. Update documentation

- Add the module to `docs/MODULES.md`
- Add any new commands to `docs/COMMANDS.md`

---

## Adding a New Command

Slash commands can be added to `src/bot.py` or as a new cog in `src/cogs/`.

### In bot.py

```python
@bot.tree.command(name="mycommand", description="Do something useful")
@require_auth
async def mycommand_cmd(interaction: discord.Interaction, param: str):
    await interaction.response.defer()
    # Check emergency stop for write operations
    if is_emergency_stopped():
        await interaction.followup.send("⛔ Emergency stop is active.")
        return
    # ... implementation ...
    audit_log(interaction.user, "mycommand", detail=param, result="success")
    await interaction.followup.send(embed=result_embed)
```

### As a new cog

Create `src/cogs/my_cog.py`:

```python
import discord
from discord import app_commands
from discord.ext import commands
from cog_helpers import audit_log


class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mycommand", description="Do something useful")
    async def mycommand_cmd(self, interaction: discord.Interaction, param: str):
        await interaction.response.defer()
        audit_log(interaction.user, "mycommand", detail=param, result="success")
        await interaction.followup.send("Done!")


async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

Then load the cog in `bot.py` by adding it to the cog loading section.

### Checklist

- [ ] Use `@require_auth` or call `is_allowed(interaction)` at the top
- [ ] Check `is_emergency_stopped()` for any write/mutating command
- [ ] Use `ApprovalView` for HIGH/CRITICAL risk operations (see `/restart` as template)
- [ ] Call `audit_log()` at every outcome branch
- [ ] Add to `config/permissions.yaml` if role-restricted
- [ ] Add to `docs/COMMANDS.md`

---

## Code Patterns

Follow these conventions across all modules:

### Async everywhere

All skill functions and command handlers must be `async`. Use `await` for I/O operations.

```python
# ✅ Good
async def my_skill(url: str) -> str:
    async with SessionManager() as session:
        resp = await session.get(url)
        return await resp.text()

# ❌ Bad — blocking I/O in async context
def my_skill(url: str) -> str:
    return requests.get(url).text
```

### Use config.py, not os.getenv

```python
# ✅ Good
from config import cfg
api_key = cfg.google_api_key

# ❌ Bad
import os
api_key = os.getenv("GOOGLE_API_KEY")
```

### Use utils.atomic_write for file writes

Prevents data corruption if the process is interrupted mid-write:

```python
from utils import atomic_write

atomic_write("data/tasks.json", json.dumps(tasks, indent=2))
```

### Use SessionManager for HTTP

Reuses a shared aiohttp session with proper cleanup:

```python
from http_session import SessionManager

async with SessionManager() as session:
    resp = await session.get(url, timeout=30)
```

### Error handling

Return user-friendly error strings from skills (don't raise exceptions):

```python
async def my_skill(param: str) -> str:
    try:
        result = await do_thing(param)
        return f"✅ {result}"
    except Exception as e:
        return f"❌ my_skill failed: {e}"
```

---

## Testing Requirements

- **Framework:** pytest with `asyncio_mode = auto` (configured in `pyproject.toml`)
- **Run tests:** `python -m pytest tests/ -x -q`
- **Mock external APIs:** Never make real HTTP calls in tests. Use `unittest.mock.AsyncMock` and `patch`.
- **Coverage target:** Aim for 80% coverage on new code
- **Test file naming:** `tests/test_<module_name>.py`
- **Shared fixtures:** See `tests/conftest.py` for `mock_llm`, `mock_discord_interaction`, and `_clear_module_caches`

Example test pattern:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_my_skill_returns_formatted_result():
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value="OK")

    with patch("my_module.SessionManager") as mock_sm:
        mock_sm.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        from my_module import my_skill
        result = await my_skill("test")
        assert "✅" in result
```

---

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add RSS digest skill with LLM summarization
fix: handle Gemini 429 rate limit in worker agent
docs: update COMMANDS.md with Phase 12 commands
test: add monitor_skills snapshot tests
refactor: extract plan serialization to helper functions
```

When AI-assisted, include the Co-authored-by trailer:

```
feat: add URL change detection monitoring

Implemented snapshot_url and check_url_for_changes skills
with SHA-256 content hashing and diff-based alerting.

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```
