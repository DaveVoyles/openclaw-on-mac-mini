# OpenClaw Contributor Start Here

Use this page as the contributor entrypoint. It tells you **what to read first**, **where to make your first change**, and **which workflow doc to follow next**.

> **Planning future work?** Use [`docs/PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md) as the canonical roadmap. The next CLI UX wave should be started there, then expanded in scoped docs only if needed.

---

## Recommended reading order

1. **This page** — orient yourself and choose a contribution path.
2. [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) — local setup, CI gate, and day-to-day commands.
3. [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md) — 30-second architecture, critical gotchas, and key files.
4. One focused workflow guide based on the change you are making:
   - skills → [`docs/SKILL_DEVELOPMENT.md`](SKILL_DEVELOPMENT.md)
   - plugins → [`docs/PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md)
   - tests and validation → [`docs/TESTING.md`](TESTING.md)
   - CLI usage and UX behavior → [`docs/CLI_QUICKSTART.md`](CLI_QUICKSTART.md)

---

## Contributor roadmap

### Step 1: Get a working local setup

Follow [`docs/DEVELOPMENT.md`](DEVELOPMENT.md) to:
- create `.venv`
- install runtime and test dependencies
- run the CLI locally
- learn the CI gate rule before starting a new wave of work

### Step 2: Learn the three main surfaces

| Surface | What it owns | Start reading here |
| --- | --- | --- |
| Discord bot + server | Slash commands, `/ask`, tools, scheduling, dashboard APIs | [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md), [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) |
| CLI | Terminal client, routing, rendering, sessions, macros | [`docs/DEVELOPMENT.md`](DEVELOPMENT.md), [`docs/CLI_QUICKSTART.md`](CLI_QUICKSTART.md) |
| Skills and extensions | Python skills, SKILL.md docs, plugins | [`docs/SKILL_DEVELOPMENT.md`](SKILL_DEVELOPMENT.md), [`docs/PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md) |

### Step 3: Pick a contribution shape

- **Docs-first / first PR:** improve contributor docs, examples, or validation notes.
- **CLI change:** update `src/openclaw_cli*.py`, then run focused CLI tests.
- **Skill change:** update a skill, register it, declare it in `config/tools.yaml`, and add tests.
- **Plugin change:** update plugin code and plugin-focused tests.
- **Bot/server change:** trace the request flow first so you know whether the edit belongs in routing, tool execution, or a cog.

### Step 4: Validate only what you touched

Use [`docs/TESTING.md`](TESTING.md) and the commands in [`docs/DEVELOPMENT.md`](DEVELOPMENT.md). For contributor docs, run the markdown link checker and any existing doc checks instead of unrelated builds.

---

## First contribution walkthrough

A good first contribution is a **small docs fix, focused test improvement, or isolated CLI/skill change**.

1. **Check the baseline first.**
   ```bash
   gh run list --limit 5
   ```
2. **Create a branch.**
   ```bash
   git checkout -b docs/your-topic
   ```
3. **Pick one contained task.** Good examples:
   - clarify a confusing doc
   - add or tighten a focused test
   - fix a small CLI/help/rendering issue
   - add a small skill improvement with matching docs/tests
4. **Read the nearest guide before editing.**
   - CLI work → `docs/DEVELOPMENT.md` + `docs/CLI_QUICKSTART.md`
   - skill work → `docs/SKILL_DEVELOPMENT.md`
   - plugin work → `docs/PLUGIN_DEVELOPMENT.md`
5. **Make the smallest coherent change.** Update docs when behavior or workflow changes.
6. **Run focused validation.** Examples:
   ```bash
   python3 scripts/check_markdown_links.py
   make test-cli
   make lint
   ```
7. **Open a PR with context.** Include what changed, how you verified it, and any known baseline failures you did not introduce.

---

## Architecture for contributors in 90 seconds

### If you are changing the Discord or server experience

The common request path is:

```
Discord user → src/bot.py → src/ask_orchestrator.py → src/llm/chat.py
                                               ↓
                                    src/tool_router.py
                                               ↓
                                     src/llm_tools.py
                                               ↓
                                          skills/*.py
```

Use this to decide whether your edit belongs in:
- a command or interaction layer (`src/bot.py`, `src/discord_commands/`)
- model/tool routing (`src/llm/chat.py`, `src/tool_router.py`)
- tool execution (`src/llm_tools.py`)
- a capability implementation (`skills/*.py`, `src/*_skills.py`)

### If you are changing the CLI

Start with these modules:
- `src/openclaw_cli.py` — main REPL and command dispatch glue
- `src/openclaw_cli_router.py` — route decisions
- `src/openclaw_cli_render.py` — output formatting
- `src/openclaw_cli_sessions.py` — session persistence and history

For **future CLI UX wave planning**, check [`docs/PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md) first and treat any scoped UX doc as supporting detail, not the primary roadmap.

### If you are unsure where to start

Read [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md) first, then inspect the nearest module-specific doc.

---

## Common contributor recipes

### Docs-only change
- Update the canonical doc and any entrypoint that should point to it.
- Run:
  ```bash
  python3 scripts/check_markdown_links.py
  ```

### Add or update a Python skill
- Implement the async function.
- Register it in `skills/__init__.py`.
- Add the tool declaration in `config/tools.yaml`.
- Add or update tests.
- Read: [`docs/SKILL_DEVELOPMENT.md`](SKILL_DEVELOPMENT.md).

### Update CLI behavior
- Make the code change in the smallest relevant `src/openclaw_cli*.py` module.
- Run focused CLI tests first, then broader validation if needed.
- Read: [`docs/DEVELOPMENT.md`](DEVELOPMENT.md), [`docs/TESTING.md`](TESTING.md), [`docs/CLI_QUICKSTART.md`](CLI_QUICKSTART.md).

### Update plugin behavior
- Keep the change scoped to the plugin and its manifest/tests.
- Read: [`docs/PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md).

---

## Where to go next

- Want setup and commands? → [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)
- Want architecture and gotchas? → [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md)
- Want validation guidance? → [`docs/TESTING.md`](TESTING.md)
- Want skill-specific implementation steps? → [`docs/SKILL_DEVELOPMENT.md`](SKILL_DEVELOPMENT.md)
- Want plugin-specific implementation steps? → [`docs/PLUGIN_DEVELOPMENT.md`](PLUGIN_DEVELOPMENT.md)
