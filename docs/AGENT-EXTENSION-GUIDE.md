# OpenClaw — Agent Extension Guide
<!-- Updated: 2026-05-21 -->

> **Audience:** AI agents and human contributors who need to *extend* OpenClaw — add a skill, a command, an LLM provider, a dashboard endpoint, a background loop, a plugin, etc.
>
> **Why this exists:** The extension story used to be split across `SKILL_DEVELOPMENT.md`, `PLUGIN_DEVELOPMENT.md`, `PLUGIN_API.md`, `CONTRIBUTING.md`, `AGENT-GUIDE.md`, and `START-HERE.md`, and several of those drifted from the code. This is the single, code-verified entrypoint. It always cites the real file paths to copy from.
>
> **Audit context:** See [`AUDIT-REPORT.md`](AUDIT-REPORT.md) for the gap analysis that motivated this guide.

---

## 0. Before you change anything

1. **Find the working tree.** Source: `~/openclaw/`. Deploy config: `~/docker-stack/openclaw/docker-compose.yml`.
2. **Check CI baseline.** `gh run list --limit 5`. Don't carry pre-existing failures into your change.
3. **Read [`AGENT-GUIDE.md`](AGENT-GUIDE.md)** for the 30-second flow + 10 critical gotchas.
4. **Decide the extension surface** using the table below.

| If you want to… | Go to |
|---|---|
| Expose a new Python function to the LLM | [§1 Add a skill](#1-add-a-skill) + [§4 Tool declaration](#4-tool-declaration-and-routing) |
| Add a `/slash` command | [§2 Discord command](#2-add-a-discord-command-cog-or-discord_commands-module) |
| Wire a new LLM provider or model | [§3 LLM provider](#3-add-an-llm-provider-or-model) |
| Add a `/api/...` endpoint or dashboard page | [§5 Dashboard / API](#5-add-a-dashboard-surface-or-api-endpoint) |
| Run something periodically in the background | [§6 Background loop](#6-add-a-background-loop) or [§7 Scheduled job](#7-add-a-scheduled-cron-job) |
| Ship optional capability as a plugin | [§8 Plugin](#8-add-a-plugin) |
| Persist new state | [§9 Persistence](#9-persist-new-data) |

---

## 1. Add a skill

A **skill** is an async Python function the LLM can invoke as a tool. There are two valid homes:

- **`skills/<name>_skills.py`** — the canonical home for domain-grouped skills (media, news, finance, weather, reporting, …). The registry in `skills/__init__.py` aggregates them.
- **`src/<name>_skills.py`** — pre-package legacy home; still used by some older skill families (`email_skills.py`, `git_skills.py`, `calendar_skills.py`, `monitor_skills.py`, etc.).

**Prefer `skills/`** unless you are extending an existing `src/*_skills.py` family.

### Recipe

1. **Write the async function** in the chosen skills file:
   ```python
   # skills/reporting_skills.py
   async def generate_weekly_recap(topic: str) -> str:
       """Generate a weekly recap for the given topic."""
       ...
       return result
   ```

2. **Add it to the module's registry dict** (each skill module exports one):
   ```python
   REPORTING_SKILLS = {
       ...
       "generate_weekly_recap": generate_weekly_recap,
   }
   ```

3. **Register it in `skills/__init__.py`.** The unified `SKILLS` dict starts around line 475 and is extended via `SKILLS.update(REPORTING_SKILLS)` style calls further down (lines ~489–617, ~777–817).

4. **Declare the tool in `config/tools.yaml`** so Gemini can call it. Schema (118 examples already exist):
   ```yaml
   - name: generate_weekly_recap
     description: Generate a weekly recap report for a topic.
     parameters:
       type: object
       properties:
         topic:
           type: string
           description: Topic to recap (e.g. "sports", "finance").
       required: [topic]
     # Optional routing metadata — improves tool shortlisting:
     category: reporting
     keywords: [recap, weekly, summary, digest]
     examples:
       - "weekly recap on sports"
       - "give me this week's finance summary"
   ```

5. **Decide if it should be a direct-return / fast-path** (e.g. Perplexity-style answer that bypasses Gemini synthesis):
   - Add to `_DIRECT_RETURN_MARKERS` in `src/answer_policy.py`.
   - Add a keyword bundle in `src/tool_router.py` (`_INTENT_HINTS`, `_WORKFLOW_BUNDLES`).
   - Add a route selector in `src/model_routing_policy.py`.
   - Wire the fast-path branch in `src/llm/chat.py` (`chat()` and `chat_stream()`).
   - **Skip this for most new skills** — only the realtime providers use fast-paths.

6. **Test.** Tests live in `tests/`. Use `pytest`; xdist runs in parallel by default, override with `--override-ini="addopts="` for a single-process run.

7. **Rebuild + redeploy:**
   ```bash
   cd ~/docker-stack/openclaw && docker compose up -d --build
   ```

### Files involved

| File | Role |
|---|---|
| `skills/<name>_skills.py` | Skill implementation + module-level registry dict |
| `skills/__init__.py` | Unified `SKILLS` registry (the canonical source of truth) |
| `config/tools.yaml` | Gemini function-calling declaration |
| `src/tool_router.py` | (optional) keyword bundle for shortlisting |
| `src/answer_policy.py` | (optional) direct-return marker |
| `src/model_routing_policy.py` | (optional) route selector |
| `src/llm/chat.py` | (optional) fast-path branch in `chat()`/`chat_stream()` |

---

## 2. Add a Discord command (cog or `discord_commands` module)

Both patterns coexist in the codebase. Pick by stickiness:

| Pattern | When to use | Where it lives |
|---|---|---|
| **Cog** | Stateful, grouped feature with multiple related commands or its own background work | `src/cogs/*.py` (40 cogs today) |
| **`discord_commands` module** | Lightweight commands attached to a global registry | `src/discord_commands/<group>.py` (21 modules today) |

Both are loaded from `src/bot.py` at startup; the `discord_commands` package exposes `register_commands(bot)` (`src/discord_commands/__init__.py:43-67`).

### Cog recipe

```python
# src/cogs/my_cog.py
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth  # auth + audit shared helpers
from discord_error import build_error_embed       # uniform error embeds
from discord_progress import ProgressTracker      # long-running interactions

class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="mything", description="Do my thing")
    @require_auth                           # gate by ALLOWED_USER_IDS
    async def mything(self, interaction):
        await interaction.response.defer()
        tracker = ProgressTracker()
        try:
            await tracker.start(interaction, "Working…", steps=2)
            # ... do work ...
            await tracker.update("Finishing…", step=1)
            # ... do work ...
            await tracker.done("Done.")
            audit_log(interaction.user, "/mything", "completed")
        except Exception as e:
            embed = build_error_embed(e, context="/mything")
            await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

The cog is auto-discovered by name — check `src/cogs/__init__.py` for the loader pattern in use (and add a matching entry there if explicit registration is required).

### Conventions every cog must follow

- **Errors** — `build_error_embed(e, context="/cmd")` from `src/discord_error.py`. Always `ephemeral=True`.
- **Progress** — `ProgressTracker` from `src/discord_progress.py` for anything > ~2s.
- **Audit** — `audit_log(user, action, status)` from `src/cog_helpers.py` for state-changing or privileged commands.
- **Permissions** — `require_auth` from `src/cog_helpers.py`; service-level allowlists via `is_service_allowed()`.
- **Alerts (not in cogs, but adjacent)** — never `channel.send()` directly for monitoring. Use `send_severity_alert()` from `src/alert_manager.py` (severity routing: DEBUG/INFO log-only, WARNING → channel, CRITICAL → channel + DM).

---

## 3. Add an LLM provider or model

Routing decisions flow through four files. To wire a new provider end-to-end:

1. **Normalize the input name.** Add to `VALID_MODEL_PREFERENCES` and the alias resolution in `src/model_aliases.py` (around lines 7–38).
2. **Classify queries to it.** Update `classify_query()` in `src/model_router.py` (lines 125–199). Each provider has a small selector (e.g. `copilot_model_for_message()` at 109–122).
3. **Encode the policy.** Add rules in `src/model_routing_policy.py` (consumed via `select_auto_route()` and `select_tool_route()`).
4. **Wire the call path.** In `src/llm/chat.py`, add a fast-path branch in `chat()` / `chat_stream()` if needed. Gemini is the native function-calling path; other providers are typically compat layers without function calling.
5. **(Optional) Direct-return marker.** If the provider returns user-facing answers without needing Gemini synthesis, add a marker in `src/answer_policy.py` (`_DIRECT_RETURN_MARKERS`) and ensure `_normalize_direct_provider_answer()` appends it. `answer_policy.should_return_directly()` reads the marker.
6. **Track spend.** Add a recorder in `src/spending.py` (see `record_copilot()` for a $0 example, `record_perplexity()` for a paid example).

### Critical gotcha

> **Never use `model_preference="gemini"`.** All callers pass `"auto"` or a specific provider name (`"copilot"`, `"perplexity"`, `"ollama"`, …). Zero hardcoded `"gemini"` strings remain — keep it that way.

### Fast-path guard

Fast-paths in `chat()` only fire when `model_preference == "auto"` **AND** `recalled_context` is empty. This prevents hijacking mid-conversation follow-ups that need history.

---

## 4. Tool declaration and routing

A **tool** is the LLM-facing view of a skill. The relationship is:

```
skill (Python)          ↔  config/tools.yaml entry
       ↑                          ↓
skills.SKILLS dict     →  tool_router shortlists by keyword
       ↑                          ↓
       ←──  src/llm_tools.py executes the tool
```

### Schema reference (tools.yaml)

```yaml
- name: <function_name>            # must match the SKILLS[...] key
  description: <one-liner shown to the model>
  parameters:
    type: object
    properties:
      <param>:
        type: string | integer | boolean | array | object
        description: <how the model should set it>
        enum: [...]                # optional
    required: [<param>, ...]
  # Optional routing metadata (improves tool_router shortlisting):
  category: <area>
  aliases: [...]
  keywords: [...]
  examples: ["<natural-language phrasing>", ...]
  domains: [...]
  packs: [...]
  personas: [...]
```

### Execution

- Shortlisting: `src/tool_router.py` matches the user's intent against `_INTENT_HINTS` and `_WORKFLOW_BUNDLES` and returns a reduced tool list to Gemini.
- Execution: `src/llm_tools.py::_execute_function_call()` (lines 73–111) resolves the name against `skills.SKILLS` and awaits it. `_run_tool_loop()` (129–162) drives the multi-round tool loop with a `MAX_TOOL_ROUNDS` cap.

---

## 5. Add a dashboard surface or API endpoint

The dashboard is an aiohttp app hosted out of `src/discord_web.py` and wired up in `src/dashboard/`.

| File | Role |
|---|---|
| `src/dashboard/routes.py` | `setup_dashboard(app, ...)` registers page + API routes (lines 68–170) |
| `src/dashboard/api_handlers.py` | JSON `/api/...` handlers |
| `src/dashboard/html_handlers.py` | HTML page handlers + CLI download endpoints |
| `src/dashboard/helpers.py` | Shared helpers |
| `src/discord_web.py` | aiohttp server, login/logout, session middleware (`_require_session`, `_require_api_action_auth`) |

### Recipe

1. **JSON endpoint:** add `async def <name>_api(request): ...` in `api_handlers.py`. Register it in `routes.py` (`app.router.add_get("/api/<name>", <name>_api)`).
2. **HTML page:** add `async def <name>_page(request): ...` in `html_handlers.py` and register it. Use the shared layout helpers.
3. **State-changing action:** wrap the route through the `action()` helper in `routes.py` (lines 82–93) which enforces `_require_api_action_auth()` — bearer token / `X-OpenClaw-Token` with `hmac.compare_digest`.
4. **Page-level auth:** session middleware in `_require_session()` (`src/discord_web.py:548-562`) redirects unauthenticated requests to `/login`. Credentials come from `OPENCLAW_DASHBOARD_USERNAME` / `OPENCLAW_DASHBOARD_PASSWORD` (set in deploy `.env`, loaded by `src/config.py`).

---

## 6. Add a background loop

> **Important:** `src/discord_background.py` is now a re-export shim. The real loops live in:
>
> | Module | Loops it owns |
> |---|---|
> | `src/bg_briefing.py` | `morning_briefing_loop`, `evening_digest_loop` |
> | `src/bg_monitoring.py` | `error_monitor_loop`, container health monitor, resource monitor |
> | `src/bg_healing.py` | `audit_writer_loop`, `background_cleanup_loop`, `proactive_insight_loop`, self-healing |
> | `src/bg_tasks.py` | supervisor — start/stop/restart/backoff, loop registry |

### Recipe

1. **Implement the loop** as an async coroutine in the appropriate `bg_*.py` module (or a new one):
   ```python
   async def my_loop(bot):
       while True:
           try:
               # ... do work ...
               await asyncio.sleep(INTERVAL_SECS)
           except asyncio.CancelledError:
               raise
           except Exception:
               log.exception("my_loop error")
               await asyncio.sleep(BACKOFF_SECS)
   ```

2. **Register it in the factory map** in `src/bg_tasks.py::_build_background_task_factories()` (lines 112–130). The supervisor wraps each loop with `_run_supervised_background_task()` (175–208) and restarts it via `_handle_background_task_done()` (133–163) with exponential backoff.

3. **Start it.** `start_background_tasks(bot)` (lines 222–243) launches all registered loops at bot startup.

4. **Alert on failure conditions** via `src/alert_manager.send_severity_alert()` — never write to a channel directly.

---

## 7. Add a scheduled (cron) job

Two schedulers exist; choose by need:

| API | When to use | Persistence |
|---|---|---|
| `src/scheduler.py` | Simple daily / interval / cron tasks; lightweight | JSON at `MEMORY_DIR/schedules.json` |
| `src/scheduler_advanced.py` | Triggers, retries, history, conditional events | SQLite |

### Simple scheduler recipe

```python
from scheduler import scheduler

scheduler.register_skills({"my_recap": my_recap_skill})

scheduler.create(
    action="my_recap",
    args={"topic": "sports"},
    cron_expression="0 9 * * MON",   # cron syntax via croniter
)
```

`TaskScheduler._run_loop()` (`src/scheduler.py:30-175`) drives execution. Cron expressions take priority over daily hour/minute.

### Advanced scheduler

Use `src/scheduler_advanced.py` (lines 90–260) when you need retry policy, event triggers, or persistent history.

---

## 8. Add a plugin

Plugins are optional capabilities discovered from `plugins/<name>/plugin.yaml` and loaded by `src/plugin_system/`.

### What works today

- Manifest discovery (`PluginLoader.discover_plugins()` — `plugin_loader.py:64-86`)
- Manifest validation (`plugin_loader.py:88-137`) — required: `name`, `version`, `author`
- Lifecycle: `on_load()` / `on_unload()` (`plugin_loader.py:330-395`)
- **Skill registration** from inside plugins via `self.api.register_skill(name, fn)` (`plugin_api.py:131-145`) — the registered skill is callable just like any other skill
- Enable / disable persisted in `data/plugin_state.json` (`src/plugin_system/plugin_registry.py:24-127`)
- Reload, install, remove (`plugin_registry.py:24-260`)

### What does NOT work today

> **Discord command registration from plugins is logged but not wired.** See `src/plugin_system/plugin_api.py:147-150`. Treat plugins as **skill-only** for production use.

### Recipe (real example: `plugins/examples/<name>/`)

```
plugins/<name>/
├── plugin.yaml      # name, version, author (+ optional: description, homepage, repository, permissions, min_openclaw_version)
└── main.py          # Plugin subclass with on_load / on_unload
```

```python
# plugins/<name>/main.py
from plugin_system import Plugin, PluginAPI

class MyPlugin(Plugin):
    async def on_load(self, api: PluginAPI):
        api.register_skill("my_skill", self._my_skill)

    async def _my_skill(self, query: str) -> str:
        return f"hello {query}"

    async def on_unload(self):
        pass  # skills auto-unregister on unload
```

Discovery happens automatically when the bot starts; enable/disable state persists across restarts.

---

## 9. Persist new data

Pick the store by data shape:

| Store | Use when | Where it lives | Example |
|---|---|---|---|
| **JSON file** | Lightweight runtime state; human-inspectable | `data/*.json` | `data/onboarding_seen.json` (`src/bot.py:168-187`), `MEMORY_DIR/schedules.json` (`src/scheduler.py`) |
| **SQLite** | Threaded, indexed, transactional | `src/thread_store.py` (threads/messages, WAL mode), `src/scheduler_advanced.py` (cron history) | `ThreadStore` in `src/thread_store.py` |
| **ChromaDB** | Semantic search / recall | `src/vector_store*.py` (6 modules — client, config, scope, compaction, memory, hub) | `VectorStore.search()` |
| **Obsidian vault (Markdown)** | Long-form human-readable artifacts | `src/obsidian_writer.py` writes `data/vault/...` | `save_to_vault()` |
| **Audit log** | Append-only audit trail | `src/audit.py::audit_event()` | every cog uses `audit_log` from `cog_helpers.py` |

**Rules:**

- Keep persistence behind the owning module. Don't write JSON/SQLite/Chroma directly from unrelated code.
- If the new store needs health visibility, add a check in `src/discord_web.py` health/metrics surface or in `src/tool_health.py`.
- Anything sensitive (tokens, keys) — never in `data/`. Use `.env` / secrets, loaded via `src/config.py`.

---

## Cross-cutting conventions

| Concern | The one right way |
|---|---|
| Logging | `log = logging.getLogger("openclaw.<area>")` at module top |
| HTTP | Use `src/http_session.SessionManager` — shared aiohttp pool |
| Subprocess | `src/subprocess_utils.run()` (async wrapper, timeout-aware) |
| JSON parsing of LLM output | `src/json_utils.{validate_json, repair_json, extract_json}` |
| Tracing | `src/trace_context` (correlation IDs propagate across loops) |
| Rate limiting | `src/llm_ratelimit.RateLimiter` (sliding window + jittered backoff) |
| Errors in cogs | `src/discord_error.build_error_embed()` |
| Progress in cogs | `src/discord_progress.ProgressTracker` |
| Alerts | `src/alert_manager.send_severity_alert()` |

---

## How to verify before declaring "done"

```bash
# From ~/openclaw
.venv/bin/python -m pytest tests/<your_test_file>.py --override-ini="addopts=" -q
.venv/bin/ruff check src/ tests/
python3 scripts/check_markdown_links.py     # for doc changes

# Rebuild + deploy (only if container behavior changed)
cd ~/docker-stack/openclaw && docker compose up -d --build

# Verify the change is live
curl -s http://192.168.1.93:8765/health | python3 -m json.tool
docker logs openclaw --tail 50
```

For tests, remember xdist is forced by `pyproject.toml` (`-n auto --dist loadfile`) — override with `--override-ini="addopts="` for single-process debugging.

---

## When this guide is wrong

This guide cites real file paths and line ranges, but lines drift. If a citation looks stale:

1. Cross-check the cited file with `grep`/`view`.
2. Update this guide in the same PR as the code change that invalidated it.
3. If the surface itself has changed shape (new package, retired module), add a section here.

See [`AUDIT-REPORT.md`](AUDIT-REPORT.md) for the audit method this guide is based on, and re-run the ground-truth one-liners in that report to refresh the numbers.

---

## See also

- [`AGENT-GUIDE.md`](AGENT-GUIDE.md) — 30-second orientation + critical gotchas
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system diagrams and runtime boundaries
- [`MODULES.md`](MODULES.md) — per-module reference (note: see audit for staleness)
- [`DEPENDENCY_MAP.md`](DEPENDENCY_MAP.md) — CLI submodule dependency rules
- [`TESTING.md`](TESTING.md) — how to run tests
- [`DEVELOPMENT.md`](DEVELOPMENT.md) — local setup, CI gate policy
