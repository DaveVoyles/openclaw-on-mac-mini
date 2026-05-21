# OpenClaw — Source Modules Reference
<!-- Updated: 2026-05-21 -->


Quick reference for source files. Consult this before exploring the codebase.

> ⚠️ **Maintenance note (2026-05-21):** The per-file tables below are point-in-time snapshots and drift quickly as the codebase grows. The **headline counts** in this section were re-verified on 2026-05-21; the per-file rows further down are partly stale and are flagged in [`AUDIT-REPORT.md`](AUDIT-REPORT.md). For the canonical extension recipes, use [`AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md). For a refreshed package overview, see [`ARCHITECTURE.md`](ARCHITECTURE.md) § "Source layout".

## Verified counts (2026-05-21)

| Surface | Count |
|---|---|
| `src/*.py` files | 180 |
| `src/cogs/*.py` | 40 |
| `src/discord_commands/*.py` (package) | 21 |
| `src/llm/*.py` (package, replaced old `src/llm.py`) | 10 |
| `src/dashboard/*.py` | 4 |
| `src/plugin_system/*.py` | 4 |
| `skills/*.py` | 22 |
| `skills/<bundle>/` (ClawHub bundles with `SKILL.md`) | 12+ |
| `config/tools.yaml` declarations | 118 |
| `src/bot.py` lines | 966 |
| `src/openclaw_cli.py` lines | 6,663 |
| `skills/__init__.py` lines (unified `SKILLS` registry) | 823 |

> **Dead references — do not edit these into new docs:**
> - `src/llm.py` — replaced by `src/llm/` package + `src/llm_client.py`
> - `src/memory_manager.py` — replaced by the `src/memory_*.py` family (9 modules)
> - `src/autonomous_skills.py` — replaced by `skills/autonomous-loop/` bundle + `src/agent_loop.py`
> - `src/discord_commands.py` (single file) — now a **package** at `src/discord_commands/`
> - `src/discord_background.py` is a **re-export shim** — see `src/bg_briefing.py`, `src/bg_monitoring.py`, `src/bg_healing.py`, `src/bg_tasks.py`

---

## Core Modules (src/\*.py) — 180 files

| File                    | Purpose                                                                  | Key Exports                                                          |
| ----------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `agentmail.py`          | Email integration via AgentMail.to API                                   | `send_agent_mail()`                                                  |
| `alert_manager.py`      | Severity-based alert routing with deduplication, snooze, and resolve     | `send_severity_alert()`, `handle_alert_reaction()`, `send_alert_resolved()`, `get_remediation_hint()` |
| `analyzer.py`           | AI-powered container log analysis using Gemini                           | `analyze_logs()`                                                     |
| `approvals.py`          | Security & approval workflows with Discord UI                            | `ApprovalStore`, `ApprovalRequest`, `RiskLevel`                      |
| `audit.py`              | Audit event recording helpers                                            | `audit_event()`                                                      |
| `autonomous_skills.py`  | _Removed._ Replaced by `skills/autonomous-loop/` bundle + `src/agent_loop.py` | (see `agent_loop.py`)                                                |
| `bot.py`                | Core Discord bot — init, auth, `/ask` command (966 lines)                | `OpenClawBot`, `/ask` handler                                        |
| `calendar_skills.py`    | Google Calendar API integration (read/create events)                     | `get_calendar_events()`, `create_calendar_event()`                   |
| `cog_helpers.py`        | Shared utilities for cogs (audit_log, service_allowed)                   | `audit_log()`, `is_service_allowed()`                                |
| `code_sandbox.py`       | Sandboxed Python code execution in ephemeral Docker container            | `execute_python_code()`                                              |
| `config.py`             | Centralized config from YAML + env vars                                  | `cfg` (config object)                                                |
| `constants.py`          | Shared numeric constants (Discord limits, timeouts, sizes)               | `EMBED_DESC_LIMIT`, `MAX_FILE_SIZE`, `PROACTIVE_SCAN_INTERVAL`, etc. |
| `dashboard.py`          | Lightweight HTML dashboard + JSON API on `:8765`                         | `run_dashboard_server()`, API routes                                 |
| `dream_cycle.py`        | Auto-Dream cognitive memory consolidation — collect/consolidate/evaluate (916 lines) | `DreamCycle` class, `run_dream_cycle()`                  |
| `email_skills.py`       | Gmail/Outlook via IMAP/SMTP + App Passwords                              | `send_email()`, `fetch_emails()`, `search_emails()`                  |
| `error_tracker.py`      | Persistent error tracking, pattern analysis, and recurring error detection (444 lines) | `ErrorTracker` class                                    |
| `gateway.py`            | Maton OAuth proxy client for 100+ third-party APIs                       | `gateway_api_call()`, `gateway_create_connection()`                  |
| `git_skills.py`         | Git operations (status, log, diff, commit)                               | `git_status()`, `git_diff()`, `git_log()`                            |
| `goal_tracker.py`       | Auto-tracked goals extracted from conversations (188 lines)              | `GoalTracker` class, `extract_goals()`                               |
| `http_session.py`       | Shared aiohttp session manager                                           | `SessionManager` class                                               |
| `image_gen.py`          | Image generation and analysis utilities (91 lines)                       | `generate_image()`, `analyze_image()`                                |
| `llm.py`                | _Removed._ Replaced by `src/llm/` package: `chat.py` (dispatcher), `context.py`, `providers.py`, `tool_execution.py`, `telemetry.py`, etc. | (see `src/llm/chat.py`) |
| `llm_client.py`         | Gemini client wrapper, model config, system prompt loading (257 lines)   | `get_model()`, `load_system_prompt()`, `MODEL_CONFIG`                |
| `llm_tools.py`          | Tool execution engine, function calling loop (275 lines)                 | `execute_tool_call()`, `run_function_calling_loop()`                 |
| `llm_patterns.py`       | Regex patterns for query classification, hallucination detection (194 lines) | `needs_tools()`, `is_hallucination()`, `TOOL_PATTERNS`           |
| `llm_ratelimit.py`      | Sliding-window rate limiter with jittered backoff (82 lines)             | `RateLimiter` class (per-minute, per-hour)                           |
| `maintenance_skills.py` | 4:00 AM automated maintenance (backups, updates)                         | `run_maintenance()`, `update_skills()`, `backup_to_nas()`            |
| `memory.py`             | Per-user conversation context + named thread persistence                 | `ConversationStore`, `Thread` class                                  |
| `memory_manager.py`     | _Removed._ Replaced by the `src/memory_*.py` family (9 modules — `memory.py`, `memory_consolidation.py`, `memory_decay.py`, etc.) | (see `memory.py`, `memory_consolidation.py`) |
| `mission_control.py`    | Kanban-style task management (get/update/complete tasks)                 | `get_mission_tasks()`, `update_mission_task()`                       |
| `monitor_skills.py`     | URL content change detection + monitoring                                | `snapshot_url()`, `check_url_for_changes()`                          |
| `nas.py`                | Synology DSM REST API queries (storage, health, alerts)                  | `get_nas_storage_health()`, `get_nas_alerts()`                       |
| `network.py`            | Network status, Tailscale VPN, DNS, speed test                           | `get_network_status()`, `get_tailscale_status()`, `run_speed_test()` |
| `obsidian_writer.py`    | Markdown + YAML frontmatter writer to Obsidian vault                     | `save_to_vault()`, `build_frontmatter()`                             |
| `openclaw_cli.py`       | Terminal REPL entry point (~6,663 lines): `run_chat()`, `main()`, shims to extracted handler modules | `main()`, `invoke_openclaw()`, `run_chat()`                          |
| `openclaw_cli_actions.py` | CLI shell execution, risk-aware approvals, and diffable file edits                | `run_shell_command()`, `request_cli_approval()`, `replace_text_in_file()` |
| `openclaw_cli_cli_parser.py` | Extracted CLI argument parser (TD-34). Pure function, no side effects.      | `build_parser()`                                                     |
| `openclaw_cli_help.py`  | Extracted chat-help renderer (TD-34). Generates the `/help` command table from the command registry | `print_chat_help()`                                                  |
| `openclaw_cli_sessions.py` | Local CLI session persistence, workspace context capture, and saved outputs       | `create_session()`, `list_sessions()`, `collect_workspace_context()` |
| `ontology_skills.py`    | Graph memory via ontology ClawHub script                                 | `add_fact()`, `query_graph()`, `list_entities()`                     |
| `overseerr.py`          | Overseerr media request management API                                   | `get_overseerr_requests()`, `update_request_status()`                |
| `permissions.py`        | Role-based permission checks and access control (90 lines)               | `check_permission()`, `is_allowed()`                                 |
| `qmd.py`                | Quick Memory Discovery — persistent fact storage + knowledge routing   | `QMDMemory` class with `remember()`, `recall()`, `search()`          |
| `research_agent.py`     | Autonomous multi-step research with synthesis                            | `ResearchAgent` class                                                |
| `rss_skills.py`         | RSS/Atom feed fetching & LLM summarization                               | `fetch_rss_feed()`, `get_rss_digest()`                               |
| `rules_engine.py`       | Correction learning — extracts rules from user corrections               | `detect_correction()`, `extract_rule()`, `add_rule()`, `get_relevant_rules()` |
| `scheduler.py`          | Cron-based task scheduler with prompt jobs and JSON persistence (`croniter`) | `Scheduler` class, cron expressions, prompt jobs                     |
| `search_provider.py`    | Search provider retry/fallback logic for cascade (91 lines)              | `retry_once()`                                                       |
| `spending.py`           | Gemini API cost tracker (input/output tokens vs budget)                  | `SpendingTracker` class with `summary()`, `daily_breakdown()`        |
| `subprocess_utils.py`   | Async subprocess runner with timeout                                     | `run()` — returns (returncode, stdout, stderr)                       |
| `table_renderer.py`     | Discord-formatted table rendering for embeds (166 lines)                 | `render_table()`, `format_columns()`                                 |
| `webhook_formatter.py`  | Webhook payload formatters (Sonarr, Radarr, Plex, qBittorrent)           | `FORMATTERS` dict, `format_arr()`, `format_plex()`                   |
| `thread_store.py`       | SQLite-backed persistent thread/message storage (WAL mode)               | `ThreadStore` class with `create_thread()`, `search_threads()`       |
| `tool_health.py`        | Tool health monitoring, status tracking, and reporting (180 lines)       | `check_tool_health()`, `get_health_report()`                         |
| `user_profile.py`       | Structured user profile — preferences, interests, working style          | `load_profile()`, `learn_from_message()`, `get_profile_prompt()`     |
| `utils.py`              | Shared utility functions (truncation, formatting) (69 lines)             | `truncate()`, `format_bytes()`                                       |
| `vector_store.py`       | ChromaDB semantic memory — memories, conversations, research collections | `VectorStore` class with `search()`, `add_memory()`, `recall()`      |
| `worker_agent.py`       | Sub-agent spawning for task delegation                                   | `spawn_worker()`                                                     |
| `json_utils.py`         | JSON validation, repair, and extraction for robust tool result parsing   | `validate_json()`, `repair_json()`, `extract_json()`                 |
| `ollama_tools.py`       | Ollama native tool calling protocol for local Gemma model                | `ollama_chat_with_tools()`, `OLLAMA_TOOL_DECLARATIONS`               |
| `model_router.py`       | Multi-model query classification and routing (Gemini/GPT-4o/Claude/Gemma) | `classify_query()`, `route_to_model()`, `MODEL_CONFIGS`            |
| `discord_commands/` (package) | Slash command groups (21 submodules — admin, agent, code, comms, context_menus, conversation, feedback, media, monitoring, patreon, plugins, providers, routing, safety, schedule, skills, system, trends, uptime_kuma, utility) | `register_commands(bot)` in `src/discord_commands/__init__.py` |
| `discord_background.py` | **Re-export shim.** Real loops live in `src/bg_briefing.py`, `src/bg_monitoring.py`, `src/bg_healing.py`, `src/bg_tasks.py` | `start_background_tasks(bot)` (from `bg_tasks`) |
| `discord_error.py`      | Shared Discord error formatting — classifies exceptions and builds uniform error embeds | `build_error_embed()`, `classify_error()`, `ERROR_CATEGORIES`        |
| `discord_progress.py`   | Live-updating Discord progress embeds for long-running cog commands      | `ProgressTracker` class (`start()`, `update()`, `done()`)            |
| `discord_web.py`        | aiohttp health/metrics/smoke/webhook web server + dashboard host + session auth | `create_web_app(bot)`, `setup_dashboard()`, `_require_session()` |
| `fact_extractor.py`     | Automatic fact extraction from conversations for long-term memory        | `extract_facts()`, `should_store()`, `deduplicate()`                 |

### Background loop modules (replaced the old single `discord_background.py`)

| File | Loops it owns |
|---|---|
| `bg_briefing.py`  | `morning_briefing_loop`, `evening_digest_loop` |
| `bg_monitoring.py`| `error_monitor_loop`, container health monitor, resource monitor |
| `bg_healing.py`   | `audit_writer_loop`, `background_cleanup_loop`, `proactive_insight_loop`, self-healing |
| `bg_tasks.py`     | supervisor — start/stop/restart with exponential backoff; loop factory registry |

## Source subpackages (src/)

These directories hold their own focused modules in addition to the top-level `src/*.py` files:

| Package | Files | Purpose |
|---|---|---|
| `src/llm/` | `chat.py`, `context.py`, `context_limits.py`, `provider_plugin.py`, `providers.py`, `response.py`, `startup.py`, `telemetry.py`, `tool_execution.py`, `trace.py` | LLM routing, streaming, tool loop, telemetry. Replaced the old `src/llm.py` monolith. |
| `src/cogs/` | 40 cogs | Discord cog layer (slash commands, stateful features) |
| `src/discord_commands/` | 21 submodules | Standalone slash-command registry, loaded via `register_commands(bot)` |
| `src/dashboard/` | `routes.py`, `api_handlers.py`, `html_handlers.py`, `helpers.py` | Dashboard pages and `/api/...` endpoints; mounted by `src/discord_web.py` |
| `src/plugin_system/` | `plugin_api.py`, `plugin_base.py`, `plugin_loader.py`, `plugin_registry.py` | Dynamic plugin loading + enable/disable persistence |
| `src/api/` | `export.py`, `workflow_api.py` | HTTP API surface for exports and workflow operations |
| `src/exporters/` | `csv_exporter.py`, `json_exporter.py`, `parquet_exporter.py` | Data export formats |
| `src/builders/` | `embed_builder.py` | Embed/UI construction helpers |
| `src/utils/` | (helpers) | Shared utilities |
| `src/templates/` | (templates) | Built-in templates |

## Cog Modules (src/cogs/\*.py) — 40 cogs

> ⚠️ The exhaustive cog inventory (with all 40 cog command lists) is not maintained here by hand. See [`AGENT-EXTENSION-GUIDE.md § 2`](AGENT-EXTENSION-GUIDE.md#2-add-a-discord-command-cog-or-discord_commands-module) for the cog vs `discord_commands/` decision tree, and grep `src/cogs/*.py` for `@app_commands.command` to enumerate the live command set.
>
> The high-traffic cogs are listed below; the full 40-file list is available with `ls src/cogs/*.py`.

| File               | Commands (verified)                                                       | Purpose                             |
| ------------------ | ------------------------------------------------------------------------- | ----------------------------------- |
| `analytics_cog.py` | `/spending`, `/auditlog`, `/audit-summary`                                | Budget tracking and audit trail     |
| `docker_cog.py`    | `/containers`, `/status`, `/logs`, `/system`, `/dockerstats`, `/restart`  | Docker container management         |
| `dream_cog.py`     | `/dream`, `/memory-health`, `/memory-export`                              | Auto-Dream cognitive memory system  |
| `media_cog.py`     | `/search`, `/queue`, `/recent`, `/health`, `/nowplaying`, `/watch`        | \*arr stack + Plex media management |
| `memory_cog.py`    | `/remember`, `/recall`, `/goals`, `/memory-stats`, `/memory-refresh`, `/rules`, `/profile`, `/profile-edit`, `/export-conversations` | Memory, profiles, and learned rules |
| `network_cog.py`   | `/network`, `/tailscale`, `/speedtest`                                    | Network diagnostics and VPN status  |
| `research_cog.py`  | `/websearch`, `/browse`, `/research`, `/research-search`, `/sources`, `/compare` | Web search, browsing, deep research |
| `channel_profile_cog.py` | `/show`, `/recommendations`, `/recommendation-action`, `/set`, `/clear` | Per-channel profile defaults |
| `decision_cog.py`  | `/poll`, `/recent`, `/summary`                                            | Decision polls + summaries |
| `notify_cog.py`    | `/show`, `/mute`, `/unmute`, `/filter`, `/block`, `/unblock`, `/dm`       | Notification preferences |
| `rss_cog.py`       | `/list`, `/fetch`, `/search`, `/digest`                                   | RSS feeds + digests |
| `sms_cog.py`       | `/config`, `/status`, `/send`, `/test`                                    | Discord → SMS UX |
| `poll_cog.py`      | `/poll`                                                                   | Generic polls |
| `reminder_cog.py`  | `/timer`                                                                  | Countdown reminder |
| `perf_cog.py`      | `/perf`                                                                   | System perf |
| `translate_cog.py` | `/translate`                                                              | Translation |
| `interview_cog.py` | `/interview`                                                              | Interview flow |
| _(others)_ | _calendar, context, digest, dns, doc, email, expense, gdoc, github, habit, imagine, imdb, incident, journal, nas, note, notion, ntfy, reports, review, sentry, todo_ | Domain-specific groups — see source for commands |

## Configuration Files (config/)

| File                 | Purpose                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `config.yaml`        | Main bot config (LLM models, security settings, routing keywords, Docker stack refs, channel roles, vault settings) |
| `env_schema.yaml`    | Env var schema + validation metadata                                                                                |
| `permissions.yaml`   | Role-based access control + per-service restart permissions                                                         |
| `tools.yaml`         | **118 Gemini function-calling tool declarations** (externalized from the old `llm.py`)                              |
| `prompts/system.txt` | System prompt for conversational LLM mode                                                                           |

## Skills Directory (skills/)

The `skills/` package contains **22 skill modules** (`skills/*.py`) plus **12+ ClawHub skill bundle directories** (each with its own `SKILL.md`).

### Skill Modules (skills/\*.py) — 22 files

`advanced_skills.py` was split into focused modules for maintainability. Notable modules:

| File                  | Purpose                                                          | Key Exports                                   |
| --------------------- | ---------------------------------------------------------------- | --------------------------------------------- |
| `__init__.py`         | Unified `SKILLS` registry (823 lines)                            | `SKILLS` dict                                 |
| `advanced_skills.py`  | Orchestration glue — re-exports from sub-modules                 | `ADVANCED_SKILLS` dict                        |
| `search_skills.py`    | Web search providers (Perplexity, Firecrawl, Tavily, DDG, Bing) with retry logic | `SEARCH_SKILLS` dict         |
| `media_skills.py`     | \*arr services, Plex, download clients                           | `MEDIA_SKILLS` dict                           |
| `web_skills.py`       | URL browsing, content extraction, multi-source comparison        | `WEB_SKILLS` dict                             |
| `reporting_skills.py` | Recap/report generation (sports, finance, news, weather, entertainment) | `REPORTING_SKILLS` dict              |
| `news_skills.py`, `finance_skills.py`, `sports_skills.py`, `weather_skills.py`, `health_skills.py`, `digest_skills.py`, `trend_skills.py`, `synthesis_skills.py`, `polygon_skills.py`, `trakt_skills.py`, `browser_skills.py`, `patreon_skills.py`, `smart_media_skills.py`, `screenshot_skill.py`, `ocr_skill.py`, `recap_templates.py` | Domain-specific skill bundles | (per-module registry dict) |

### ClawHub Bundles

12+ ClawHub skill bundles installed under `skills/<name>/`, each with its own `SKILL.md` and `plugin/` directory: `autonomous-loop`, `free-web-search`, `git-essentials`, `mission-control`, `multi-search-engine`, `ontology`, `openclaw-tavily-search`, `planning-with-files`, `proactive-agent`, `self-improving`, `skill-vetter`, `weather`, `webfetch-md`.

See `docs/SERVICES.md` for the table with versions and API keys.

## Test Files (tests/)

| File                         | Tests                                                                           |
| ---------------------------- | ------------------------------------------------------------------------------- |
| `conftest.py`                | Shared fixtures: `mock_llm`, `mock_discord_interaction`, `_clear_module_caches` |
| `test_advanced_skills.py`    | Media, network, report generation skills (covers search_skills, media_skills, web_skills) |
| `test_agent_loop.py`         | Agent loop plan management                                                      |
| `test_agentmail.py`          | AgentMail API integration                                                       |
| `test_analyzer.py`           | Log analysis engine                                                             |
| `test_approvals.py`          | Approval workflow system                                                        |
| `test_approvals_extended.py` | Extended approval workflow tests                                                |
| `test_code_sandbox.py`       | Code sandbox execution                                                          |
| `test_config.py`             | Configuration loading                                                           |
| `test_dashboard.py`          | Dashboard rendering                                                             |
| `test_dream_cycle.py`        | Dream cycle memory consolidation                                                |
| `test_email_skills.py`       | Email sending/receiving                                                         |
| `test_gateway.py`            | Maton API gateway                                                               |
| `test_git_skills.py`         | Git operations                                                                  |
| `test_http_session.py`       | HTTP session manager                                                            |
| `test_llm_chat.py`           | LLM chat + function calling                                                     |
| `test_llm_patterns.py`       | LLM query patterns                                                              |
| `test_llm_ratelimiter.py`    | Rate limiter logic                                                              |
| `test_llm_tools.py`          | LLM tool execution                                                              |
| `test_memory.py`             | Conversation store + thread persistence                                         |
| `test_memory_manager.py`     | Memory lifecycle                                                                |
| `test_mission_control.py`    | Kanban task management                                                          |
| `test_model_selection.py`    | Model routing                                                                   |
| `test_monitor_skills.py`     | URL monitoring skills                                                           |
| `test_nas.py`                | NAS integration                                                                 |
| `test_network.py`            | Network diagnostics                                                             |
| `test_openclaw_cli.py`       | Terminal launcher defaults, request building, and chat behavior                 |
| `test_permissions.py`        | Permission checks                                                               |
| `test_qmd.py`                | Quick Memory Discovery                                                          |
| `test_real_estate.py`        | Real estate search skills                                                       |
| `test_research_agent.py`     | Research agent                                                                  |
| `test_rss_skills.py`         | RSS feed skills                                                                 |
| `test_scheduler.py`          | Task scheduler                                                                  |
| `test_search_provider.py`    | Search provider retry                                                           |
| `test_spending.py`           | Cost tracking                                                                   |
| `test_subprocess_utils.py`   | Subprocess runner                                                               |
| `test_tool_health.py`        | Tool health monitoring                                                          |
| `test_utils.py`              | Utility functions                                                               |
| `test_worker_agent.py`       | Worker agent delegation                                                         |

---

## Module Details — Phase 6–12 Additions

Expanded reference for modules added after Phase 5. Each section covers purpose, key functions, and dependencies.

### agent_loop.py — Persistent Plan Management

Observe → think → act engine for multi-step autonomous goals. Plans are stored as Markdown files in `data/plans/` with checkbox-based step tracking and survive bot restarts. On startup, interrupted plans are detected and reported to the alert channel.

**Key Functions:**
- `create_plan(goal, steps_text)` — create a new plan, returns `plan_id`
- `update_plan_step(plan_id, step_num, status, output)` — mark a step done/failed/skipped
- `execute_plan(plan_id)` — run all pending steps autonomously
- `adjust_plan(plan_id, action, step_description, position)` — add/remove/reorder steps
- `read_plan(plan_id)` / `list_plans_skill(status)` — query plan state
- `resume_plan(plan_id)` / `cancel_plan(plan_id)` — lifecycle management
- `plan_to_markdown()` / `plan_from_markdown()` — serialization helpers

**Exports:** `AGENT_LOOP_SKILLS` dict (8 skills) — `Step` and `Plan` classes.
**Dependencies:** None (self-contained, file-system only).

---

### worker_agent.py — Autonomous Task Delegation

Sub-agent spawning for parallel task execution within a single user interaction. Each worker gets its own Gemini session with a focused system prompt and independent tool loop, enabling the bot to delegate sub-tasks (e.g., "research X while I format Y").

**Key Functions:**
- `spawn_worker(goal, context, max_rounds, conversation_history)` — create a focused Gemini session with its own tool loop, returns the result string

**Exports:** `WORKER_SKILLS = {"spawn_worker": spawn_worker}`
**Dependencies:** `llm` (lazy import — `_get_model`, `_execute_function_call`, `_rate_limiter`, `_record_usage`, `_build_tools`, `_load_system_prompt`).

---

### mission_control.py — Kanban Task Management

Kanban-style task board with five columns (backlog → in_progress → review → done → permanent). Tasks are persisted in `data/tasks.json` with mtime-based caching and synced to a public GitHub Pages dashboard via `mc-update.sh`.

**Key Functions:**
- `get_mission_tasks(status)` — list tasks, optionally filtered by column
- `get_task_detail(task_id)` — full task details
- `update_task_status(task_id, new_status)` — move between columns
- `complete_task(task_id, summary)` — mark done with completion note
- `add_task_comment(task_id, comment)` — append a comment
- `_run_mc_script()` — shell out to `mc-update.sh` for mutations
- `_load_tasks()` — load `tasks.json` with file-modification caching

**Exports:** `MISSION_CONTROL_SKILLS` dict (5 skills).
**Dependencies:** None (uses subprocess shell execution).

---

### ontology_skills.py — Structured Graph Memory

Graph-based knowledge store backed by the ClawHub ontology script. Supports entity CRUD, typed relationships, graph traversal queries, and schema validation against `data/memory/ontology/schema.yaml`.

**Key Functions:**
- `ontology_create_entity(entity_type, properties_json, entity_id)` — add a typed node
- `ontology_get_entity(entity_id)` — fetch entity details
- `ontology_query(entity_type, where_json)` — search by type and filters
- `ontology_update_entity(entity_id, properties_json)` — update properties
- `ontology_relate(from_id, relation, to_id, properties_json)` — create a relation edge
- `ontology_get_related(entity_id, relation, direction)` — traverse relations
- `ontology_validate()` — validate graph against schema constraints

**Storage:** `data/memory/ontology/graph.jsonl` + `data/memory/ontology/schema.yaml`.
**Exports:** `ONTOLOGY_SKILLS` dict (7 skills).
**Dependencies:** `subprocess_utils.run`.

---

### monitor_skills.py — URL Change Detection

Proactive URL content monitoring. Takes baseline snapshots of web pages and alerts on content hash changes — useful for tracking job postings, pricing pages, release notes, or any URL that may change.

**Key Functions:**
- `snapshot_url(url, label)` — take a baseline snapshot (SHA-256 hash + timestamp)
- `check_url_for_changes(url)` — compare current content to stored snapshot
- `list_monitored_urls()` — show all monitored URLs and their state
- `remove_url_monitor(url)` — stop monitoring a URL
- `_fetch_text(url)` — fetch and normalize page content
- `_content_hash(text)` — SHA-256 hash (truncated to 16 chars)

**Storage:** `data/memory/url_snapshots.json`.
**Exports:** `MONITOR_SKILLS` dict (4 skills).
**Dependencies:** `aiohttp`, `http_session.SessionManager`.

---

### rss_skills.py — RSS/Atom Feed Monitoring

Fetch, search, and summarize RSS/Atom feeds. Supports keyword filtering and LLM-powered digest generation across multiple feeds. No API key required.

**Key Functions:**
- `fetch_rss_feed(url, limit)` — get recent items from an RSS or Atom feed
- `search_rss(url, query)` — filter feed items by keyword match
- `get_rss_digest(urls_json, topic)` — fetch multiple feeds and generate an LLM summary
- `list_rss_feeds()` — list all saved/watched feed URLs
- `_parse_feed(xml_text, limit)` — parse RSS 2.0 or Atom 1.0 XML

**Storage:** `data/memory/rss_feeds.json`.
**Exports:** `RSS_SKILLS` dict (4 skills).
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `llm.chat` (for digest generation).

---

### research_agent.py — Multi-Step Web Research

Autonomous research engine that decomposes a query into sub-questions, searches the web, browses top sources, and synthesizes a structured report with citations. Results are auto-saved to the Obsidian vault, NAS, and optionally Google Docs.

**Key Class:** `ResearchAgent`
- `run(query, on_progress)` — main entry point; returns a Markdown report
- `_plan_searches(query)` — decompose query into sub-queries via Gemini
- `_perform_searches(sub_queries, post)` — execute searches in parallel
- `_fetch_pages(urls, post)` — browse top URLs (semaphore-limited concurrency)
- `_synthesize(query, data)` — synthesize findings into a structured report
- `_auto_save(query, report, post)` — save to Obsidian vault + NAS + Google Docs

**Exports:** `ResearchAgent` class (used directly, not a skill dict).
**Dependencies:** `skills.search_skills` (search_web), `skills.web_skills` (browse_url), `obsidian_writer`, `nas`, `gateway`, `llm.chat_deep`.

---

### gateway.py — Maton API Gateway

Client for the Maton managed OAuth proxy, providing authenticated access to 100+ third-party SaaS APIs (Slack, Google Workspace, Notion, GitHub, HubSpot, Stripe, etc.) through a single integration point.

**Key Functions:**
- `_http_request(url, method, body, extra_headers, retries)` — authenticated async requests with retry logic
- `_headers(connection_id)` — build auth headers with `MATON_API_KEY`
- Gateway base URL: `https://gateway.maton.ai`; control plane: `https://ctrl.maton.ai/connections`

**Setup:** Requires `MATON_API_KEY` from https://maton.ai/settings. Rate limit: 10 req/s.
**Exports:** `GATEWAY_SKILLS` dict.
**Dependencies:** `aiohttp`, `http_session.SessionManager`.

---

### calendar_skills.py — Google Calendar Integration

Read and create Google Calendar events via REST API with OAuth2 token refresh. Stores access tokens with TTL-based caching — no OAuth library required.

**Key Functions:**
- `_get_access_token()` — exchange refresh token for a short-lived access token (cached)
- Calendar event CRUD operations

**Setup:** Requires `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`. Run `scripts/google_oauth_setup.py` for initial token exchange.
**Exports:** `CALENDAR_SKILLS` dict.
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `utils.truncate`.

---

### email_skills.py — Gmail/Outlook Email

Read and send email via Gmail or Outlook/Microsoft 365 using IMAP + SMTP with App Passwords (no OAuth2 required). Supports both providers with automatic host/port configuration.

**Key Functions:**
- `send_email(provider, to, subject, body)` — send via SMTP
- `fetch_emails(provider, folder, limit)` — read via IMAP
- `search_emails(provider, query)` — search inbox
- `_decode_header(raw)` — decode RFC 2047 email headers
- `_provider_creds(provider)` — resolve (user, password, imap_host, smtp_host, smtp_port)

**Setup:** Requires 2FA + App Password for each provider. Gmail: `GMAIL_USER`, `GMAIL_APP_PASSWORD`. Outlook: `OUTLOOK_USER`, `OUTLOOK_APP_PASSWORD`.
**Exports:** `EMAIL_SKILLS` dict.
**Dependencies:** `imaplib`, `smtplib`, `email` (stdlib), `utils.truncate`.

---

---

## Module Details — Phase 15 Additions

### json_utils.py — Structured Output Utilities

JSON validation, repair, and extraction for robust tool result parsing. Handles malformed LLM outputs by attempting progressive repair strategies (strip markdown fences, fix trailing commas, bracket matching).

**Key Functions:**
- `validate_json(text)` — validate a string as JSON, returns parsed object or None
- `repair_json(text)` — attempt to repair malformed JSON (strip fences, fix commas, match brackets)
- `extract_json(text)` — extract the first JSON object or array from mixed text

**Exports:** `validate_json`, `repair_json`, `extract_json`.
**Dependencies:** `json`, `re` (stdlib only).

---

### ollama_tools.py — Ollama Native Tool Calling

Implements the Ollama tool calling protocol so the local Gemma model can invoke read-only tools natively (system stats, container status, weather, etc.) instead of hallucinating results. Only exposes safe, read-only tools to the local model.

**Key Functions:**
- `ollama_chat_with_tools(prompt, conversation_history, tools)` — chat with tool calling loop
- `OLLAMA_TOOL_DECLARATIONS` — read-only subset of tool declarations formatted for Ollama's API

**Exports:** `ollama_chat_with_tools`, `OLLAMA_TOOL_DECLARATIONS`.
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `json_utils`.

---

### model_router.py — Multi-Model Query Classification & Routing

Classifies incoming queries and routes them to the optimal model backend. Code queries → Claude (via Copilot proxy), creative writing → GPT-4o (via Copilot proxy), tool-requiring queries → Gemini, simple chat → local Gemma.

**Key Functions:**
- `classify_query(query, has_tools)` — classify a query into a category (code, creative, tool, chat)
- `route_to_model(category, user_preference)` — resolve category + user pref to a model backend
- `MODEL_CONFIGS` — dict of model backends with endpoints, context limits, and capabilities

**Exports:** `classify_query`, `route_to_model`, `MODEL_CONFIGS`.
**Dependencies:** `re`, `config`.

---

### fact_extractor.py — Automatic Fact Extraction

Extracts memorable facts from conversations and stores them in long-term memory. Checks for >90% similarity before storing to prevent duplicates. Explicit `/remember` facts get higher confidence than auto-extracted ones.

**Key Functions:**
- `extract_facts(message, response)` — extract notable facts from a conversation exchange
- `should_store(fact, existing_memories)` — check if a fact is novel (>90% similarity threshold)
- `deduplicate(facts)` — remove near-duplicate facts from a batch

**Exports:** `extract_facts`, `should_store`, `deduplicate`.
**Dependencies:** `vector_store`, `qmd`, `llm.chat`.

---

---

## Module Details — Modular Split (March 2026)

`bot.py` was refactored from 3,084 → 1,146 lines by extracting commands, background tasks, and the web server into dedicated modules. `llm.py` extracted its client setup, tool engine, regex patterns, and rate limiter.

### discord_commands.py — Slash Commands

All slash commands except `/ask` (which stays in `bot.py`) are registered here via `register_commands(bot)`. Called once after the bot connects.

**Exports:** `register_commands(bot)`.
**Dependencies:** `bot.py` (bot instance), `cog_helpers`, `skills`, `llm`.

---

### discord_background.py — Background Loop Tasks

Long-running asyncio loops: audit log flush, expired session cleanup, morning briefing, proactive monitoring, error monitor, and container health alerts. All functions accept the bot instance.

**Exports:** `start_background_tasks(bot)`.
**Dependencies:** `bot.py` (bot instance), `memory`, `scheduler`.

---

### discord_web.py — Web / Health Server

aiohttp web application serving `/health`, `/metrics`, `/smoke`, `/dashboard`, and `/webhooks` endpoints. The bot instance is stored in `app["bot"]`.

**Exports:** `create_web_app(bot)`.
**Dependencies:** `bot.py` (bot instance), `dashboard`, `webhook_formatter`.

---

### llm_client.py — Gemini Client Wrapper

Initializes the Gemini client, manages model configuration, and loads the system prompt from `config/prompts/system.txt`.

**Exports:** `get_model()`, `load_system_prompt()`, `MODEL_CONFIG`.
**Dependencies:** `google.genai`, `config`.

---

### llm_tools.py — Tool Execution Engine

Executes function calls returned by Gemini, manages the multi-turn tool-calling loop, and caches tool results.

**Exports:** `execute_tool_call()`, `run_function_calling_loop()`.
**Dependencies:** `skills`, `llm_client`.

---

### llm_patterns.py — Regex Patterns & Validation

Regex-based query classification (`needs_tools()`), hallucination detection, and response validation utilities.

**Exports:** `needs_tools()`, `is_hallucination()`, `TOOL_PATTERNS`.
**Dependencies:** `re` (stdlib).

---

### llm_ratelimit.py — Rate Limiter

Sliding-window rate limiter with per-minute (60 RPM) and per-hour (500 RPH) limits plus jittered backoff.

**Exports:** `RateLimiter` class.
**Dependencies:** `asyncio`, `time` (stdlib).

---

### search_skills.py — Web Search Providers

Search cascade logic extracted from `advanced_skills.py`. Implements the 5-tier web search cascade (Perplexity AI → Firecrawl → Tavily → DuckDuckGo → Bing Lite) with per-provider retry logic via `search_provider.retry_once`.

**Key Functions:**
- `search_web(query, provider, max_results)` — unified search entry point with cascade fallback
- Provider-specific helpers for Perplexity, Firecrawl, Tavily, DuckDuckGo, Bing, and Serper

**Exports:** `SEARCH_SKILLS` dict.
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `search_provider.retry_once`, `config`.

---

### media_skills.py — \*arr & Plex Skills

Media stack skills extracted from `advanced_skills.py`. Covers Sonarr, Radarr, Lidarr, Prowlarr, Plex (via Tautulli), SABnzbd, and qBittorrent.

**Key Functions:**
- `check_arr_health()`, `search_media(query)`, `get_download_queue()`
- `check_download_clients()`, `check_plex_status()`, `get_recent_additions()`

**Exports:** `MEDIA_SKILLS` dict.
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `config`.

---

### web_skills.py — URL Browsing & Extraction

Web page content extraction extracted from `advanced_skills.py`. Three-tier extraction: trafilatura → Jina AI Reader → Playwright headless browser.

**Key Functions:**
- `browse_url(url, question)` — fetch and read a web page; optional Q&A
- `compare_sources(urls_json, question)` — multi-source comparison

**Exports:** `WEB_SKILLS` dict.
**Dependencies:** `aiohttp`, `http_session.SessionManager`, `config`.

---

Last updated: July 2026

---

## Module Details — W1–W14 Discord Improvements (April 2026)

### discord_error.py — Shared Discord Error Formatting

Centralises error handling across all Discord cogs. Classifies exceptions into
known categories (permissions, rate-limit, network, validation, timeout, unknown)
and builds a consistent embed that is safe to send as an ephemeral response.

**Key Exports:**
- `build_error_embed(exc, *, context: str = "") -> discord.Embed` — build a
  colour-coded error embed from any exception; `context` is typically the
  invoking slash command name (e.g. `"/ask"`).
- `classify_error(exc) -> str` — map an exception to an `ERROR_CATEGORIES` key.
- `ERROR_CATEGORIES: dict[str, dict]` — severity + user-facing label per
  exception class.

**Dependencies:** `discord.py`, `config`.

---

### discord_progress.py — Live-Updating Progress Embeds

Provides `ProgressTracker` for Discord cog commands that may take several
seconds. Sends an initial "in progress" embed and edits it in place as work
advances, reducing perceived latency without flooding the channel.

**Key Class:** `ProgressTracker`
- `start(interaction, title, *, steps: int = 0)` — send the initial embed and
  store the message handle.
- `update(message: str, *, step: int = 0)` — edit the live embed with a new
  status line.
- `done(final_message: str)` — mark complete (green embed) and stop further
  edits.

**Dependencies:** `discord.py`.

---

### alert_manager.py — Severity-Based Alert Routing

Routes monitoring alerts by severity, deduplicates repeat alerts within a
configurable window, and tracks snooze/resolve state. Integrates with Discord
reaction buttons for acknowledge/resolve flows.

**Key Functions:**
- `send_severity_alert(severity, title, body, *, service="")` — route alert:
  `DEBUG`/`INFO` → log only; `WARNING` → configured alert channel;
  `CRITICAL` → alert channel + DM to owner.
- `handle_alert_reaction(payload)` — process Discord reaction events for alert
  acknowledge/snooze/resolve.
- `send_alert_resolved(alert_id, summary)` — post a resolution notice to the
  original alert message.
- `get_remediation_hint(error_type)` — return a canned remediation suggestion
  for a known error category.

**Dependencies:** `discord.py`, `config`, `error_tracker`.

---

### openclaw_cli_cli_parser.py — CLI Argument Parser (TD-34)

Pure-function leaf module extracted from `openclaw_cli.py` as part of the TD-34
CLI extraction wave. Contains no module-level side effects and no imports from
`openclaw_cli.py`, making it independently testable.

**Key Export:**
- `build_parser() -> argparse.ArgumentParser` — construct and return the full
  `openclaw` argument parser with all sub-commands and flags.

**Dependencies:** `argparse` (stdlib only).

---

### openclaw_cli_help.py — Chat Help Renderer (TD-34)

Generates the formatted `/help` output table from the live command registry.
Extracted from `openclaw_cli.py` as part of the TD-34 CLI extraction wave.

**Key Export:**
- `print_chat_help(registry, *, is_tty: bool, prefs: dict)` — print the
  grouped command help table, respecting the caller's TTY state and preferences.

**Dependencies:** `openclaw_cli_types` (for `ChatCommandRegistry`), `openclaw_cli_ui_core`.

