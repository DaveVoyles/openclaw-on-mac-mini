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
| Add a `/slash` command | [§2 Slack slash command](#2-add-a-slack-slash-command) |
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

## 2. Add a Slack slash command

> **Discord was removed in May 2026.** The previous "cog" / `discord_commands` patterns are gone. All user-facing commands now live in `src/slack_bot.py`. Recipe §10 below covers the lighter-weight `/host <subcommand>` pattern; use this section for genuinely new top-level commands.

### Recipe

1. **Define the handler** in `src/slack_bot.py`:

   ```python
   @app.command("/mything")
   async def _handle_mything(ack, body, say):
       await ack()
       user_id = body.get("user_id", "")
       if user_id not in _incident_allowed_user_ids():
           await say(":no_entry: Not authorized.")
           return
       text = (body.get("text") or "").strip()
       try:
           # ... do work ...
           await say(f":white_check_mark: Done: {text}")
       except Exception as exc:
           log.exception("/mything failed")
           await say(f":x: {exc}")
   ```

2. **Register the command in Slack** by editing the `MANIFEST` constant in `scripts/update_slack_manifest.py` (add a `command`, `description`, `usage_hint`, `should_escape` block under `features.slash_commands`).

3. **Push the manifest** to Slack:

   ```bash
   make slack-manifest-push    # rotates SLACK_CONFIG_TOKEN automatically
   make slack-manifest-check   # confirm in-sync
   ```

4. **Write a test** in `tests/test_slack_*.py` mirroring existing patterns.

5. **Deploy:** `cd ~/openclaw && git rev-parse --short HEAD > src/_git_sha.txt && cd ~/docker-stack/openclaw && docker compose up -d --build openclaw`.

### Conventions

- **Auth gate:** call `_incident_allowed_user_ids()` (or your own allowlist helper) at the top of every privileged handler.
- **Long-running work:** spawn an async task and post incremental updates back to the thread. See `start_session()` in `src/host_bridge.py` for the canonical streaming pattern.
- **Errors:** post user-visible messages with `:x:` prefix; log the exception with `log.exception(...)` so it's captured in `docker logs openclaw`.
- **Audit:** add an entry via `audit_event(...)` from `src/audit.py` for state-changing or privileged commands.

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

The dashboard is an aiohttp app in `src/dashboard/`. With Discord removed, the slack bot's health server (`src/slack_bot.py::_start_health_server()`) is what currently runs on port 8765. The full dashboard wire-up (login/session middleware, etc.) lived in the deleted `src/discord_web.py`; if you need it, lift the routes from git history (commit `ae0004d` had them) into a new Slack-bot-hosted aiohttp app.

| File | Role |
|---|---|
| `src/dashboard/routes.py` | `setup_dashboard(app, ...)` registers page + API routes (lines 68–170) |
| `src/dashboard/api_handlers.py` | JSON `/api/...` handlers |
| `src/dashboard/html_handlers.py` | HTML page handlers + CLI download endpoints |
| `src/dashboard/helpers.py` | Shared helpers |
| `src/slack_bot.py::_start_health_server` | Minimal aiohttp server hosting `/health` on :8765 — extend here to add more routes |

### Recipe

1. **JSON endpoint:** add `async def <name>_api(request): ...` in `api_handlers.py`. Wire it into `_start_health_server()` in `slack_bot.py` with `app.router.add_get(...)`.
2. **HTML page:** same pattern; render via `html_handlers.py`.
3. **State-changing action:** wrap with a bearer-token guard (`OPENCLAW_API_TOKEN`). The previous `_require_api_action_auth()` helper was in `discord_web.py`; reimplement in a Slack-owned module if you need it.

> **Tech debt:** the full dashboard surface is currently degraded post-Discord-removal. Only `/health` runs out of the box. Restoring `/dashboard`, `/api/*`, login, etc. is a future cleanup task.

---

## 6. Add a background loop

The real loops live in:

| Module | Loops it owns |
|---|---|
| `src/bg_briefing.py` | `morning_briefing_loop`, `evening_digest_loop` |
| `src/bg_monitoring.py` | `error_monitor_loop`, container health monitor, resource monitor |
| `src/bg_healing.py` | `audit_writer_loop`, `background_cleanup_loop`, `proactive_insight_loop`, self-healing |
| `src/bg_tasks.py` | supervisor — start/stop/restart/backoff, loop registry |

> **Note:** `src/discord_background.py` was deleted in May 2026 along with the Discord bot. The supervisor in `bg_tasks.py` is still present but no longer wired to a Discord client; loops that posted to Discord channels are dormant. Migrating these to Slack output (via `slack_sdk` client from `slack_bot.py`) is a future task.

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
| **JSON file** | Lightweight runtime state; human-inspectable | `data/*.json` | `MEMORY_DIR/schedules.json` (`src/scheduler.py`) |
| **SQLite** | Threaded, indexed, transactional | `src/thread_store.py` (threads/messages, WAL mode), `src/scheduler_advanced.py` (cron history) | `ThreadStore` in `src/thread_store.py` |
| **ChromaDB** | Semantic search / recall | `src/vector_store*.py` (6 modules — client, config, scope, compaction, memory, hub) | `VectorStore.search()` |
| **Obsidian vault (Markdown)** | Long-form human-readable artifacts | `src/obsidian_writer.py` writes `data/vault/...` | `save_to_vault()` |
| **Audit log** | Append-only audit trail | `src/audit.py::audit_event()` | privileged Slack handlers in `src/slack_bot.py` |

**Rules:**

- Keep persistence behind the owning module. Don't write JSON/SQLite/Chroma directly from unrelated code.
- If the new store needs health visibility, extend `src/slack_bot.py::_start_health_server()` `/health` payload or `src/tool_health.py`.
- Anything sensitive (tokens, keys) — never in `data/`. Use `.env` / secrets, loaded via `src/config.py`.

---

## 10. Add a `/host` quick-action shortcut

`/host <subcommand> [args]` wraps vetted Copilot prompts so a phone user can dispatch the most common operations without typing a freeform prompt. All shortcuts route through the `/copilot` session machinery in `src/host_bridge.py` — they inherit threaded replies, owner checks, the per-user concurrency cap, and the idle timeout.

### Recipe

1. **Edit the registry** in `src/host_bridge_shortcuts.py`:

   ```python
   SHORTCUTS["uptime"] = Shortcut(
       name="uptime",
       description="Show host uptime + load average",
       prompt_template="Run `uptime` on this Mac Mini and explain load.",
       usage="/host uptime",
   )
   ```

2. **Args?** Set `requires_arg=True` and add a `{placeholder}` to `prompt_template`. Then extend `_format_prompt()` with a branch that binds the placeholder — see how `logs`, `restart`, and `git` already do it.

3. **Validate user input.** `_safe()` strips shell metachars; never trust positional args verbatim. Clamp numerics like `logs` does (`max(1, min(n, 5000))`).

4. **Add a test** in `tests/test_host_bridge_shortcuts.py`:

   ```python
   def test_uptime(self) -> None:
       r = resolve("uptime")
       assert isinstance(r, ResolvedShortcut)
       assert "uptime" in r.prompt
   ```

5. **Update the help text test** (`test_all_expected_shortcuts_present`) — the set must include your new shortcut.

6. **No Slack manifest change needed.** `/host` is one registered slash command; subcommands are dispatched in-process by `resolve()`.

### Why this exists

Phone users want one-tap operations. Typing `/copilot diagnose why plex can't find files on disk and check if NAS mounts dropped` from a phone is painful; `/host plex-fix` is two seconds. Shortcuts are the "speed-dial" layer on top of the Phase 3 host bridge.

### Files involved

- `src/host_bridge_shortcuts.py` — pure data registry + `shlex` resolver (no I/O, no Slack imports — unit-testable)
- `src/slack_bot.py` — `@app.command("/host")` handler resolves the shortcut and calls the same `start_session()` flow as `/copilot`
- `tests/test_host_bridge_shortcuts.py` — happy path, error paths, arg validation, scrubbing

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
| Slack message errors | `:x: <msg>` prefix + `log.exception(...)`; ephemeral if appropriate |
| Long-running Slack work | Async task + incremental thread updates (see `start_session()` in `src/host_bridge.py`) |
| Alerts | `src/alert_manager.send_severity_alert()` (Discord output paths dormant post-removal; logging still works) |

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
