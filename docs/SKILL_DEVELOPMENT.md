# OpenClaw — Skills System Guide

OpenClaw has **two distinct skill systems** that serve different purposes. Read this carefully before creating anything new.

---

## Two Skill Systems

### 1. Python Skills (`skills/*.py` + `skills/__init__.py`)

These are **async Python functions** that the LLM tool router can call during `/ask` processing. They run server-side inside the Docker container.

- Location: `skills/*.py` files
- Registry: `skills/__init__.py` → `SKILLS` dict
- Tool declarations: `config/tools.yaml`
- Invoked by: `src/llm_tools.py` during the tool-call loop

### 2. SKILL.md Knowledge Documents (`skills/<name>/SKILL.md`)

These are **Markdown reference documents** that agents (humans and AI) read to understand how to use specific tools or services. They do not execute code.

- Location: `skills/<name>/SKILL.md` directories
- Metadata: `skills/<name>/_meta.json` (slug, version, publishedAt)
- Invoked by: Agents reading them as context — they are not loaded automatically

---

## Python Skills — Structure

### Anatomy of a skill function

```python
# skills/my_skills.py

import logging
from typing import Any

log = logging.getLogger("openclaw.my_skills")

async def do_something(arg1: str, arg2: int = 10) -> str:
    """
    One-line summary of what this skill does.

    Args:
        arg1: Description of the first argument
        arg2: Optional second argument (default 10)

    Returns:
        Human-readable result string, or JSON-serializable dict
    """
    try:
        result = await some_api_call(arg1)
        return f"Result: {result}"
    except Exception as exc:
        log.error("do_something failed: %s", exc)
        return f"Error: {exc}"
```

Key rules:
- All skill functions are `async def`
- Return a `str` (preferred) or a JSON-serializable `dict`
- Catch exceptions and return error strings — the tool loop expects a string result
- Use module-level `log = logging.getLogger("openclaw.<module>")` for logging
- Keep skills focused: one responsibility per function

### Registering a Python skill

After writing the function, register it in `skills/__init__.py`:

```python
# skills/__init__.py

# Import your new skill module
from .my_skills import do_something, do_something_else

# Add to SKILLS dict (or use SKILLS.update)
SKILLS.update({
    "do_something": do_something,
    "do_something_else": do_something_else,
})
```

> **Critical:** A function that isn't in the `SKILLS` dict cannot be called by the tool router. This is the most common "my skill isn't working" mistake.

### Adding a tool declaration

Every Python skill that should be callable by Gemini needs a declaration in `config/tools.yaml`:

```yaml
- name: do_something
  description: "One-line description of what this skill does."
  parameters:
    type: object
    properties:
      arg1:
        type: string
        description: "The first argument"
      arg2:
        type: integer
        description: "Optional second argument (default 10)"
    required:
      - arg1
```

The `name` here must exactly match the key in `SKILLS`.

---

## Python Skills — Step-by-Step Creation

1. **Choose or create a skill file.** Group related skills together (e.g., `media_skills.py`, `finance_skills.py`).

2. **Write the async function.** Follow the pattern above. Import any APIs or helpers you need.

3. **Register in `skills/__init__.py`.** Add to `SKILLS` dict.

4. **Add tool declaration to `config/tools.yaml`.** Match the function name exactly.

5. **If this is a direct-return skill** (Perplexity fast-path, bypasses Gemini synthesis):
   - Add marker to `_DIRECT_RETURN_MARKERS` in `src/answer_policy.py`
   - Add keyword bundle in `src/tool_router.py`
   - Add route selector in `src/model_routing_policy.py`
   - Wire fast-path in `src/llm/chat.py`

6. **Write tests** in `tests/`.

7. **Rebuild and deploy:**
   ```bash
   cd ~/docker-stack/openclaw && docker compose up -d --build
   ```

---

## Python Skills — Testing

```python
# tests/test_my_skills.py
import pytest
from skills.my_skills import do_something

@pytest.mark.asyncio
async def test_do_something_success(monkeypatch):
    # Mock the API call
    async def _fake_api(arg):
        return "fake result"
    monkeypatch.setattr("skills.my_skills.some_api_call", _fake_api)

    result = await do_something("hello")
    assert "fake result" in result

@pytest.mark.asyncio
async def test_do_something_error(monkeypatch):
    async def _fail(arg):
        raise ValueError("API down")
    monkeypatch.setattr("skills.my_skills.some_api_call", _fail)

    result = await do_something("hello")
    assert "Error" in result
```

---

## Skill Naming Conventions (Python)

| Rule | Example |
|------|---------|
| Use `snake_case` for function and SKILLS key names | `get_weather`, `list_containers` |
| Group by domain: `<domain>_skills.py` | `weather_skills.py`, `media_skills.py` |
| Use verb-first naming | `get_`, `list_`, `create_`, `run_`, `send_` |
| No abbreviations in public-facing names | `get_container_status` not `get_ctr_stat` |

---

## SKILL.md Documents — Structure

SKILL.md files are knowledge documents for agents. They follow a YAML frontmatter + Markdown body pattern:

```markdown
---
name: my-skill
description: One-line description of what this skill covers.
homepage: https://relevant-api-docs.example.com
metadata: {"clawdbot":{"emoji":"🔧","requires":{"bins":["curl"]}}}
---

# My Skill

Brief intro paragraph.

## Common Use Cases

### Use case 1
```bash
curl -s "https://api.example.com/endpoint"
```

### Use case 2
...
```

Each SKILL.md directory also contains `_meta.json`:

```json
{
  "ownerId": "...",
  "slug": "my-skill",
  "version": "1.0.0",
  "publishedAt": 1234567890000
}
```

---

## SKILL.md — Step-by-Step Creation

1. Create a directory: `skills/my-skill/`
2. Write `skills/my-skill/SKILL.md` following the frontmatter + Markdown format above
3. Create `skills/my-skill/_meta.json` with a slug matching the directory name
4. If the skill requires an optional plugin (code, config): add a `plugin/` subdirectory

SKILL.md documents do **not** need to be registered anywhere. They are read directly by agents as reference material.

---

## Example Walkthrough: `skills/weather/`

The `weather` skill is a SKILL.md document (not a Python skill):

```
skills/weather/
  SKILL.md      ← Markdown reference for wttr.in + Open-Meteo APIs
  _meta.json    ← {slug: "weather", version: "1.0.0", ...}
```

The SKILL.md explains:
- How to query `wttr.in` (primary, no API key)
- How to query Open-Meteo (fallback, JSON, no key)
- Format codes, tips, unit options

Agents read this file to know how to fetch weather data. There is also a separate Python skill `weather_skills.py` that implements the same capability as callable tool functions.

---

## Where Each System Is Used

| System | Used by | When |
|--------|---------|------|
| Python skills (`SKILLS` dict) | LLM tool loop (`src/llm_tools.py`) | During `/ask` tool calls |
| SKILL.md documents | Agents, developers | Reading reference material |
| `config/tools.yaml` | Gemini API (`src/llm/chat.py`) | Declaring available tools to the model |
