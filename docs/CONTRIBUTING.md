# Contributing to OpenClaw
<!-- Updated: 2026-04-18 -->


Guidelines for adding features, skills, and commands to OpenClaw.

> **New here?** Start with [`docs/START-HERE.md`](START-HERE.md) for the contributor roadmap, first-contribution walkthrough, architecture orientation, and workflow recipes.
>
> **Planning a future wave or CLI UX follow-up?** Start from the canonical roadmap in [`docs/PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md) before opening a new scoped planning thread.

---

## Choose Your Path

- **Start here / first contribution:** [`docs/START-HERE.md`](START-HERE.md)
- **Local setup + CI gate + daily commands:** [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)
- **Architecture + key files + gotchas:** [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md)
- **Testing and validation:** [`docs/TESTING.md`](TESTING.md)
- **Skill work:** [`docs/SKILL_DEVELOPMENT.md`](SKILL_DEVELOPMENT.md)
- **Plugin work:** [`docs/PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md)

---

## Getting Started

### 1. Clone & configure

```bash
git clone https://github.com/davevoyles/openclaw.git
cd openclaw
cp .env.example .env
# Fill in required values: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, ALLOWED_USER_IDS, GOOGLE_API_KEY
```

### 2. Set up a virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-test.txt
```

### 3. Install pre-commit hooks

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

Hooks run automatically on `git commit`. To run manually:

```bash
pre-commit run --all-files
```

Hooks included: ruff lint + format, trailing whitespace, YAML/TOML/JSON validation, mypy type
checking, bandit security scan, markdown lint, conventional commit messages, and env schema
validation. Local hooks (`mypy`, `validate-env-schema`, `pytest-check`) are skipped in
[pre-commit.ci](https://pre-commit.ci) and only run locally.

### 4. Run tests

```bash
python -m pytest tests/ -x -q
```

> ⛔ **CI Gate:** Always verify CI is passing (or at the known baseline) before starting new work.
> See [`docs/DEVELOPMENT.md` → CI Gate Policy](DEVELOPMENT.md#-ci-gate-policy--read-before-starting-any-wave-of-work) for the full policy and commands.

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
├── src/                    # All source code (~145 modules)
│   ├── slack_bot.py        # Slack Bolt entrypoint (Socket Mode)
│   ├── ask_executor.py     # Routes Slack /chat → orchestrator
│   ├── ask_orchestrator.py # Plans tool calls, runs the LLM loop
│   ├── llm_tools.py        # Tool dispatch: tools.yaml → SKILLS
│   ├── host_bridge.py      # Standalone host process for /copilot CLI
│   ├── config.py           # Centralized config (YAML + env) — import `cfg`
│   ├── utils.py            # Shared utilities (atomic_write, http session, …)
│   ├── llm/                # LLM client wrappers (Gemini + Ollama)
│   ├── builders/           # Slack Block Kit message builders
│   ├── utils/              # Subpackage of focused helpers
│   └── plugin_system/      # Plugin loader and hooks
├── skills/                 # Skill registry + ClawHub bundles  ⚠️ see note below
│   ├── __init__.py         # SKILLS dict — 182 entries
│   └── <bundle>/           # ClawHub skill bundles
├── tests/                  # pytest test suite
├── config/                 # Configuration files
│   ├── config.yaml         # Main config
│   ├── tools.yaml          # 113 LLM-facing tool declarations (subset of SKILLS)
│   ├── permissions.yaml    # Role-based access control
│   └── prompts/system.txt  # LLM system prompt
├── data/                   # Runtime data (Docker volume; gitignored)
├── scripts/                # Utility scripts
├── docs/                   # Documentation
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

> **⚠️ Import path conventions — read before adding code:**
>
> - **`skills/`** lives at the repo root, **not** inside `src/`. In the Docker container it is
>   copied to `/app/skills/`. The Dockerfile sets `PYTHONPATH="/app"` so it is importable as
>   `from skills import SKILLS` from any module. Do **not** move it into `src/`.
>
> - **Config singleton:** always import as `from config import cfg`. The `cfg` object is a
>   module-level singleton — do not call it like a function. Never create or import from a
>   module called `config_loader`; that module does not exist.

---

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

## Adding a New Slash Command

Slack slash commands are handled in `src/slack_bot.py`. The repo currently
exposes a small fixed surface (`/chat`, `/copilot`, `/incident`); most new
capabilities should be added as **skills** (see "Adding a New Skill" above)
and exposed via `config/tools.yaml` rather than as new slash commands.

When a new top-level slash command is genuinely warranted:

```python
# src/slack_bot.py
@app.command("/mycommand")
async def handle_mycommand(ack, body, client):
    await ack()
    user_id = body["user_id"]
    text = body.get("text", "").strip()

    if is_emergency_stopped():
        await client.chat_postEphemeral(
            channel=body["channel_id"],
            user=user_id,
            text="⛔ Emergency stop is active.",
        )
        return

    # ... implementation ...
    audit_log(user_id, "mycommand", detail=text, result="success")
    await client.chat_postMessage(channel=body["channel_id"], text="Done!")
```

Then register the command in the Slack app manifest
(`scripts/register_slack_commands.py` or the Slack app config UI).

### Checklist

- [ ] Acknowledge the command within 3 seconds (`await ack()`)
- [ ] Check `is_emergency_stopped()` for any write/mutating command
- [ ] Call `audit_log()` at every outcome branch
- [ ] Add to `config/permissions.yaml` if role-restricted
- [ ] Register the command in the Slack app manifest
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

---

## Pull Request Process

1. **Create a feature branch:** `git checkout -b feature/your-feature-name`
2. **Make changes**, write tests, update docs
3. **Run the full test suite:** `.venv/bin/python -m pytest tests/ --override-ini="addopts=" -q && .venv/bin/ruff check src/ tests/`
4. **Push and create PR on GitHub**

### PR Guidelines

- Write clear, descriptive PR titles
- Reference related issues (e.g., "Fixes #123")
- Ensure CI passes before requesting review
- Keep PRs focused — one logical change per PR
