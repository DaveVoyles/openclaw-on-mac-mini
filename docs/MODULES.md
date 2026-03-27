# OpenClaw — Source Modules Reference

Quick reference for all source files. Consult this before exploring the codebase.

## Core Modules (src/\*.py) — 39 files

| File                    | Purpose                                                                  | Key Exports                                                          |
| ----------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `agentmail.py`          | Email integration via AgentMail.to API                                   | `send_agent_mail()`                                                  |
| `analyzer.py`           | AI-powered container log analysis using Gemini                           | `analyze_logs()`                                                     |
| `approvals.py`          | Security & approval workflows with Discord UI                            | `ApprovalStore`, `ApprovalRequest`, `RiskLevel`                      |
| `autonomous_skills.py`  | Task planning via planning-with-files skill                              | `init_planning_files()`                                              |
| `bot.py`                | Main Discord bot — 30 slash commands, event handlers, conversation store | `OpenClawBot`, command handlers                                      |
| `calendar_skills.py`    | Google Calendar API integration (read/create events)                     | `get_calendar_events()`, `create_calendar_event()`                   |
| `cog_helpers.py`        | Shared utilities for cogs (audit_log, service_allowed)                   | `audit_log()`, `is_service_allowed()`                                |
| `config.py`             | Centralized config from YAML + env vars                                  | `cfg` (config object)                                                |
| `constants.py`          | Shared numeric constants (Discord limits, timeouts, sizes)               | `EMBED_DESC_LIMIT`, `MAX_FILE_SIZE`, `PROACTIVE_SCAN_INTERVAL`, etc. |
| `dashboard.py`          | Lightweight HTML dashboard + JSON API on `:8765`                         | `run_dashboard_server()`, API routes                                 |
| `email_skills.py`       | Gmail/Outlook via IMAP/SMTP + App Passwords                              | `send_email()`, `fetch_emails()`, `search_emails()`                  |
| `gateway.py`            | Maton OAuth proxy client for 100+ third-party APIs                       | `gateway_api_call()`, `gateway_create_connection()`                  |
| `git_skills.py`         | Git operations (status, log, diff, commit)                               | `git_status()`, `git_diff()`, `git_log()`                            |
| `http_session.py`       | Shared aiohttp session manager                                           | `SessionManager` class                                               |
| `llm.py`                | Gemini + Ollama hybrid LLM dispatcher with function calling              | `chat()`, `chat_deep()`, `_gemini_chat()`                            |
| `maintenance_skills.py` | 4:00 AM automated maintenance (backups, updates)                         | `run_maintenance()`, `update_skills()`, `backup_to_nas()`            |
| `memory.py`             | Per-user conversation context + named thread persistence                 | `ConversationStore`, `Thread` class                                  |
| `mission_control.py`    | Kanban-style task management (get/update/complete tasks)                 | `get_mission_tasks()`, `update_mission_task()`                       |
| `monitor_skills.py`     | URL content change detection + monitoring                                | `snapshot_url()`, `check_url_for_changes()`                          |
| `nas.py`                | Synology DSM REST API queries (storage, health, alerts)                  | `get_nas_storage_health()`, `get_nas_alerts()`                       |
| `network.py`            | Network status, Tailscale VPN, DNS, speed test                           | `get_network_status()`, `get_tailscale_status()`, `run_speed_test()` |
| `obsidian_writer.py`    | Markdown + YAML frontmatter writer to Obsidian vault                     | `save_to_vault()`, `build_frontmatter()`                             |
| `ontology_skills.py`    | Graph memory via ontology ClawHub script                                 | `add_fact()`, `query_graph()`, `list_entities()`                     |
| `overseerr.py`          | Overseerr media request management API                                   | `get_overseerr_requests()`, `update_request_status()`                |
| `qmd.py`                | Quick Memory Discovery — persistent fact storage + knowledge routing   | `QMDMemory` class with `remember()`, `recall()`, `search()`          |
| `research_agent.py`     | Autonomous multi-step research with synthesis                            | `ResearchAgent` class                                                |
| `rss_skills.py`         | RSS/Atom feed fetching & LLM summarization                               | `fetch_rss_feed()`, `get_rss_digest()`                               |
| `rules_engine.py`       | Correction learning — extracts rules from user corrections               | `detect_correction()`, `extract_rule()`, `add_rule()`, `get_relevant_rules()` |
| `scheduler.py`          | In-memory task scheduler with JSON persistence                           | `Scheduler` class                                                    |
| `spending.py`           | Gemini API cost tracker (input/output tokens vs budget)                  | `SpendingTracker` class with `summary()`, `daily_breakdown()`        |
| `subprocess_utils.py`   | Async subprocess runner with timeout                                     | `run()` — returns (returncode, stdout, stderr)                       |
| `webhook_formatter.py`  | Webhook payload formatters (Sonarr, Radarr, Plex, qBittorrent)           | `FORMATTERS` dict, `format_arr()`, `format_plex()`                   |
| `thread_store.py`       | SQLite-backed persistent thread/message storage (WAL mode)               | `ThreadStore` class with `create_thread()`, `search_threads()`       |
| `user_profile.py`       | Structured user profile — preferences, interests, working style          | `load_profile()`, `learn_from_message()`, `get_profile_prompt()`     |
| `vector_store.py`       | ChromaDB semantic memory — memories, conversations, research collections | `VectorStore` class with `search()`, `add_memory()`, `recall()`      |
| `worker_agent.py`       | Sub-agent spawning for task delegation                                   | `spawn_worker()`                                                     |
| `json_utils.py`         | JSON validation, repair, and extraction for robust tool result parsing   | `validate_json()`, `repair_json()`, `extract_json()`                 |
| `ollama_tools.py`       | Ollama native tool calling protocol for local Gemma model                | `ollama_chat_with_tools()`, `OLLAMA_TOOL_DECLARATIONS`               |
| `model_router.py`       | Multi-model query classification and routing (Gemini/GPT-4o/Claude/Gemma) | `classify_query()`, `route_to_model()`, `MODEL_CONFIGS`            |
| `fact_extractor.py`     | Automatic fact extraction from conversations for long-term memory        | `extract_facts()`, `should_store()`, `deduplicate()`                 |

## Cog Modules (src/cogs/\*.py) — 4 cogs, 18 commands

| File               | Commands                                                                 | Purpose                             |
| ------------------ | ------------------------------------------------------------------------ | ----------------------------------- |
| `analytics_cog.py` | `/spending`, `/auditlog`, `/audit-summary`                               | Budget tracking and audit trail     |
| `docker_cog.py`    | `/containers`, `/status`, `/logs`, `/system`, `/dockerstats`, `/restart` | Docker container management         |
| `media_cog.py`     | `/search`, `/queue`, `/recent`, `/health`, `/nowplaying`, `/watch`       | \*arr stack + Plex media management |
| `network_cog.py`   | `/network`, `/tailscale`, `/speedtest`                                   | Network diagnostics and VPN status  |

## Configuration Files (config/)

| File                 | Purpose                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `config.yaml`        | Main bot config (LLM models, security settings, routing keywords, Docker stack refs, channel roles, vault settings) |
| `permissions.yaml`   | Role-based access control + per-service restart permissions                                                         |
| `tools.yaml`         | 84 Gemini function-calling tool declarations (externalized from llm.py)                                             |
| `prompts/system.txt` | System prompt for conversational LLM mode                                                                           |

## Skills Directory (skills/)

13 ClawHub skill bundles installed. See `docs/SERVICES.md` for the full table with versions and API keys.

## Test Files (tests/)

| File                       | Tests                                                                           |
| -------------------------- | ------------------------------------------------------------------------------- |
| `conftest.py`              | Shared fixtures: `mock_llm`, `mock_discord_interaction`, `_clear_module_caches` |
| `test_advanced_skills.py`  | Media, network, report generation skills                                        |
| `test_agentmail.py`        | AgentMail API integration                                                       |
| `test_analyzer.py`         | Log analysis engine                                                             |
| `test_approvals.py`        | Approval workflow system                                                        |
| `test_llm_chat.py`         | LLM chat + function calling                                                     |
| `test_llm_ratelimiter.py`  | Rate limiter logic                                                              |
| `test_memory.py`           | Conversation store + thread persistence                                         |
| `test_mission_control.py`  | Kanban task management                                                          |
| `test_monitor_skills.py`   | URL monitoring skills                                                           |
| `test_qmd.py`              | Quick Memory Discovery                                                          |
| `test_real_estate.py`      | Real estate search skills                                                       |
| `test_scheduler.py`        | Task scheduler                                                                  |
| `test_spending.py`         | Cost tracking                                                                   |
| `test_subprocess_utils.py` | Subprocess runner                                                               |

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
**Dependencies:** `skills.advanced_skills` (search_web, browse_url), `obsidian_writer`, `nas`, `gateway`, `llm.chat_deep`.

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

Last updated: July 2025
