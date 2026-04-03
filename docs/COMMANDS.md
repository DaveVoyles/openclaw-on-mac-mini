# OpenClaw — Discord Slash Commands Reference

All 69 slash commands across `bot.py` and 6 cogs, organized by category.

> **Risk levels:** LOW (auto-execute) | MEDIUM (logged) | HIGH (requires button approval) | CRITICAL (requires approval + preview)
>
> **Auth Required** = uses `@require_auth` decorator or explicit `is_allowed()` check.

---

## Core

General-purpose commands for identity, help, and conversation management.

| Command          | Description                                               | Parameters                                     | Auth | Risk   | File     |
| ---------------- | --------------------------------------------------------- | ---------------------------------------------- | ---- | ------ | -------- |
| `/ping`          | Check if OpenClaw is alive (latency + uptime)             | —                                              | ✅   | LOW    | `bot.py` |
| `/about`         | Show OpenClaw version and system info                     | —                                              | ✅   | LOW    | `bot.py` |
| `/whoami`        | Show your Discord identity and permission level           | —                                              | ✅   | LOW    | `bot.py` |
| `/help`          | List available OpenClaw commands                          | —                                              | ✅   | LOW    | `bot.py` |
| `/ask`           | AI-powered natural language query (auto-routes to Gemini/GPT-4o/Claude/Ollama); auto-creates a Discord thread after 3+ exchanges — follow-up messages work without `/ask` inside the thread | `question: str`, `attachment: file (optional)`, `model: auto\|local\|gemini\|openai\|anthropic (optional)` | —    | MEDIUM | `bot.py` |
| `/clear`         | Clear your conversation history                           | —                                              | ✅   | LOW    | `bot.py` |
| `/model show`    | Show your current model routing preference                | —                                              | —    | LOW    | `bot.py` |
| `/model set`     | Set your default model routing preference                 | `preference: auto\|local\|gemini\|openai\|anthropic` | —    | LOW    | `bot.py` |
| `/save`          | Save current conversation as a named thread               | `name: str`                                    | ✅   | LOW    | `bot.py` |
| `/resume`        | Resume a previously saved conversation thread             | `name: str`                                    | ✅   | LOW    | `bot.py` |
| `/threads`       | List all your saved conversation threads                  | —                                              | ✅   | LOW    | `bot.py` |
| `/forget`        | Delete a saved conversation thread                        | `name: str`                                    | ✅   | LOW    | `bot.py` |
| `/skills`        | List all available skill functions                        | —                                              | ✅   | LOW    | `bot.py` |
| `/remember`      | Store a fact in long-term QMD memory                      | `content: str`, `tags: str (optional)`         | ✅   | LOW    | `bot.py` |
| `/recall`        | Search long-term QMD memory                               | `query: str`                                   | ✅   | LOW    | `bot.py` |

**`/ask` routing** — By default, queries are auto-routed: code queries → Claude (via Copilot proxy), creative writing → GPT-4o (via Copilot proxy), tool-requiring queries → Gemini 2.5 Flash, simple/conversational queries → Ollama (`gemma4:e4b`, free). Auto-RAG injects top-5 relevant memories, user profile, and active rules before every call. You can override routing per-message with the `model:` parameter, or set a sticky default with `/model set`. The response footer shows which model handled the request.

| Model choice | Icon | Behavior |
|---|---|---|
| `auto` | 🔄 | Smart routing — classifies query and picks the best model |
| `local` | 🏠 | Always use Gemma/Ollama (auto-upgrades to Gemini if tools are needed) |
| `gemini` | ☁️ | Always use Gemini cloud (best quality, uses API quota) |
| `openai` | 🧠 | Route to GPT-4o via Copilot proxy (creative writing, general knowledge) |
| `anthropic` | 🔬 | Route to Claude Sonnet 4.5 via Copilot proxy (code review, reasoning) |

---

## Docker & System

Container management, system monitoring, and infrastructure diagnostics.

| Command        | Description                                      | Parameters                         | Auth | Risk     | File             |
| -------------- | ------------------------------------------------ | ---------------------------------- | ---- | -------- | ---------------- |
| `/containers`  | Interactive container management (select menu + action buttons: status, logs, restart) | —                    | —    | LOW      | `docker_cog.py`  |
| `/status`      | Detailed status for a container                  | `service: str`                     | —    | LOW      | `docker_cog.py`  |
| `/logs`        | View recent logs from a container                | `service: str`, `lines: int = 30`  | —    | LOW      | `docker_cog.py`  |
| `/system`      | Show system resource usage (CPU, RAM, disk)      | —                                  | —    | LOW      | `docker_cog.py`  |
| `/dockerstats` | Show resource usage per container                | —                                  | —    | LOW      | `docker_cog.py`  |
| `/restart`     | Restart a Docker container (requires approval)   | `service: str`                     | —    | **HIGH** | `docker_cog.py`  |
| `/ports`       | Verify all services are listening                | —                                  | ✅   | LOW      | `bot.py`         |
| `/report`      | Comprehensive system status report               | —                                  | ✅   | LOW      | `bot.py`         |
| `/analyze`     | AI-powered container log analysis                | `service: str`, `lines: int = 50`  | ✅   | LOW      | `bot.py`         |
| `/schedule`    | Manage recurring scheduled tasks                 | `action`, `skill`, `cron`, `prompt`, `hour`, `minute`, `interval`, `task_id` | — | LOW | `bot.py` |

**`/restart` policy** — Allowed services are declared in `config/permissions.yaml`. Critical services (`traefik`, `socket-proxy`, `homepage`, `watchtower`) are always denied regardless of approval.

**`/schedule` actions:** `list` (default), `add` (requires `skill` + `cron`/`hour`/`minute`/`interval`, or `prompt` + `cron` for prompt jobs), `remove` / `toggle` (requires `task_id`).

**Cron Expressions** (new):
- `"0 7 * * 1,5"` — Monday and Friday at 7 AM
- `"0 9 * * 1-5"` — Weekdays at 9 AM
- `"*/30 * * * *"` — Every 30 minutes
- Format: `minute hour day-of-month month day-of-week`

**Prompt Jobs** (new):
Instead of calling a specific skill, prompt jobs send a natural language instruction to the LLM with full tool access. The LLM can search the web, browse pages, execute code, and post results.

Example: "Search ESPN for D1 lacrosse games this week and create a table"

**Natural Language Creation**: Tell the bot via `/ask` — e.g., "schedule a prompt job every Monday at 7 AM to check lacrosse scores" — and it creates the cron job automatically.

---

## Media & Downloads

Plex, *arr stack, and download client management.

| Command       | Description                                         | Parameters                                              | Auth | Risk | File           |
| ------------- | --------------------------------------------------- | ------------------------------------------------------- | ---- | ---- | -------------- |
| `/search`     | Search Sonarr + Radarr for TV shows or movies       | `query: str`, `media_type: str = "all"`                 | —    | LOW  | `media_cog.py` |
| `/queue`      | Show active downloads (SABnzbd + qBittorrent)       | —                                                       | —    | LOW  | `media_cog.py` |
| `/recent`     | Recently added media from Plex (via Tautulli)       | `count: int = 10`                                       | —    | LOW  | `media_cog.py` |
| `/health`     | Check *arr services and download client health      | —                                                       | —    | LOW  | `media_cog.py` |
| `/nowplaying` | Show what's currently playing on Plex               | —                                                       | —    | LOW  | `media_cog.py` |
| `/watch`      | Create a persistent scheduled alert                 | `condition: str`, `action: str = "list"`, `watch_id: str` | —  | LOW  | `media_cog.py` |

---

## Network

Network diagnostics, VPN status, and connectivity.

| Command      | Description                                           | Parameters | Auth | Risk | File             |
| ------------ | ----------------------------------------------------- | ---------- | ---- | ---- | ---------------- |
| `/network`   | LAN + internet + DNS + Tailscale + health summary     | —          | —    | LOW  | `network_cog.py` |
| `/tailscale` | Tailscale VPN status and device IP                    | —          | —    | LOW  | `network_cog.py` |
| `/speedtest` | Cloudflare download speed + DNS latency               | —          | —    | LOW  | `network_cog.py` |

---

## AI & Research

Web search, browsing, file analysis, image generation, code execution, and autonomous research.

| Command          | Description                                              | Parameters                                                            | Auth | Risk | File     |
| ---------------- | -------------------------------------------------------- | --------------------------------------------------------------------- | ---- | ---- | -------- |
| `/websearch`     | Live web search via Tavily (DuckDuckGo fallback)         | `query: str`, `results: int = 5`                                      | ✅   | LOW  | `bot.py` |
| `/browse`        | Fetch and read a web page; optional Q&A                  | `url: str`, `question: str (optional)`                                | ✅   | LOW  | `bot.py` |
| `/analyze-image` | Analyze an uploaded image with Gemini vision             | `image: attachment`, `question: str (optional)`                       | —    | LOW  | `bot.py` |
| `/analyze-file`  | Analyze a document (PDF, TXT, JSON…) with Gemini        | `file: attachment`, `question: str (optional)`                        | —    | LOW  | `bot.py` |
| `/research`      | Autonomous multi-step research with synthesis            | `query: str`                                                          | ✅   | LOW  | `bot.py` |
| `/bookmark`      | Save a URL or note to the Obsidian vault                 | `url: str (optional)`, `note: str (optional)`, `tags: str (optional)` | ✅   | LOW  | `bot.py` |
| `/imagine`       | Generate an image using local Stable Diffusion           | `prompt: str`, `negative`, `width`, `height`, `steps`                 | ✅   | LOW  | `bot.py` |
| `/run-code`      | Execute Python code in a sandboxed container             | `code: str` (max 10,000 chars)                                        | ✅   | LOW  | `bot.py` |
| `/weather`       | Get current weather and forecast                         | `location: str (optional)`, `units: str (optional)`                   | ✅   | LOW  | `bot.py` |
| `/briefing`      | On-demand morning briefing (weather, health, downloads)  | —                                                                     | ✅   | LOW  | `bot.py` |

---

## Document Editing

Word (.docx) and Excel (.xlsx) document creation, reading, and AI-assisted editing.

| Command                      | Description                                              | Parameters                                    | Auth | Risk   | File          |
| ---------------------------- | -------------------------------------------------------- | --------------------------------------------- | ---- | ------ | ------------- |
| `/doc read`                  | Extract and display Word document content                | `attachment: .docx file`                       | —    | LOW    | `doc_cog.py`  |
| `/doc edit <instructions>`   | AI-assisted Word document editing                        | `attachment: .docx file`, `instructions: str`  | —    | LOW    | `doc_cog.py`  |
| `/doc create <description>`  | Generate a new Word document from natural language       | `description: str`                             | —    | LOW    | `doc_cog.py`  |
| `/sheet read`                | Display Excel spreadsheet as a formatted table           | `attachment: .xlsx file`                       | —    | LOW    | `doc_cog.py`  |
| `/sheet edit <instructions>` | AI-assisted Excel spreadsheet editing                    | `attachment: .xlsx file`, `instructions: str`  | —    | LOW    | `doc_cog.py`  |
| `/sheet create <description>`| Generate a new Excel spreadsheet from description        | `description: str`                             | —    | LOW    | `doc_cog.py`  |

**Examples:**
```
/doc read                          → attach a .docx file to see its content
/doc edit "fix all typos"          → attach a .docx file + describe changes
/doc create "weekly status report template with headers for accomplishments, blockers, and next steps"
/sheet read                        → attach a .xlsx file to see it as a table
/sheet edit "add a Total row"      → attach a .xlsx file + describe changes
/sheet create "budget tracker with columns: date, category, amount, notes"
```

**Dependencies:** `python-docx`, `openpyxl` (in `requirements.txt`).
**Implementation:** `src/document_skills.py` (skill logic) + `src/cogs/doc_cog.py` (Discord commands).

---

## Security & Approvals

Audit trail, emergency controls, and approval workflows.

| Command          | Description                                        | Parameters             | Auth | Risk         | File               |
| ---------------- | -------------------------------------------------- | ---------------------- | ---- | ------------ | ------------------ |
| `/pending`       | View all pending approval requests                 | —                      | ✅   | LOW          | `bot.py`           |
| `/estop`         | Emergency stop — halt all write actions            | `action: str = "stop"` | ✅   | **CRITICAL** | `bot.py`           |
| `/auditlog`      | View recent audit log entries (max 25)             | `lines: int = 10`      | —    | LOW          | `analytics_cog.py` |
| `/audit-summary` | Analytics summary of today's audit log             | —                      | —    | LOW          | `analytics_cog.py` |
| `/spending`      | View Gemini API spending and budget status         | `breakdown: bool = false` | —  | LOW          | `analytics_cog.py` |
| `/diff`          | Show uncommitted git changes in the OpenClaw repo  | —                      | ✅   | LOW          | `bot.py`           |
| `/mail`          | Send email via AgentMail.to                        | `to`, `subject`, `body` | ✅  | MEDIUM       | `bot.py`           |

**`/estop`** — `action` can be `stop` (default), `resume`, `start`, or `off`. When active, all write operations are blocked.

---

## Planning & Tasks

Kanban task management and persistent multi-step agent plans.

| Command        | Description                                                         | Parameters                                               | Auth | Risk   | File     |
| -------------- | ------------------------------------------------------------------- | -------------------------------------------------------- | ---- | ------ | -------- |
| `/tasks`       | View Mission Control task board                                     | `status: str` (all\|backlog\|in_progress\|review\|done)  | ✅   | LOW    | `bot.py` |
| `/plans`       | List active/recent agent plans                                      | `status`: all \| in-progress \| completed \| interrupted | ✅   | LOW    | `bot.py` |
| `/plan-detail` | Show full details of a specific plan (steps, status, outputs)       | `plan_id: str`                                           | ✅   | LOW    | `bot.py` |
| `/resume-plan` | Resume an interrupted plan from where it left off                   | `plan_id: str`                                           | ✅   | LOW    | `bot.py` |
| `/cancel-plan` | Cancel an active plan (marks interrupted, resets in-progress steps) | `plan_id: str`                                           | ✅   | MEDIUM | `bot.py` |

### Mission Control

Tasks stored in `data/tasks.json`, synced to [GitHub Pages dashboard](https://davevoyles.github.io/openclaw-dashboard/). LLM-callable tools: `get_mission_tasks`, `get_task_detail`, `update_task_status`, `complete_task`, `add_task_comment`.

### Agent Plans

Plans stored as Markdown in `data/plans/` and survive restarts. On startup, interrupted plans are detected and reported to `ALERT_CHANNEL_ID`.

LLM-callable tools: `create_plan`, `update_plan_step`, `read_plan`, `list_plans`, `adjust_plan`, `cancel_plan`, `resume_plan`.

### Ontology (Graph Memory)

Structured graph memory via `/ask` — entities, relations, queries, validation. Stored in `data/memory/ontology/graph.jsonl`.

LLM-callable tools: `ontology_create_entity`, `ontology_get_entity`, `ontology_update_entity`, `ontology_query`, `ontology_relate`, `ontology_get_related`, `ontology_validate`.

---

## Notifications

Per-user alert preferences — mute, filter, block, and DM controls.

| Command               | Description                                    | Parameters                     | Auth | Risk | File            |
| --------------------- | ---------------------------------------------- | ------------------------------ | ---- | ---- | --------------- |
| `/notify show`        | Show your notification preferences             | —                              | —    | LOW  | `notify_cog.py` |
| `/notify mute`        | Mute alerts for a duration (e.g., 30m, 2h, 8h) | `duration: str`               | —    | LOW  | `notify_cog.py` |
| `/notify unmute`      | Resume alerts immediately                      | —                              | —    | LOW  | `notify_cog.py` |
| `/notify filter`      | Set severity filter                            | `level: all\|warning\|critical` | —    | LOW  | `notify_cog.py` |
| `/notify block`       | Block alerts from a specific service           | `service: str`                 | —    | LOW  | `notify_cog.py` |
| `/notify unblock`     | Unblock a previously blocked service           | `service: str`                 | —    | LOW  | `notify_cog.py` |
| `/notify dm`          | Toggle DM delivery for alerts                  | `enabled: on\|off`             | —    | LOW  | `notify_cog.py` |

---

## Summary

| Category             | Commands | Source Files                                |
| -------------------- | -------- | ------------------------------------------- |
| Core                 | 13       | `bot.py`                                    |
| Docker & System      | 10       | `bot.py`, `docker_cog.py`                   |
| Media & Downloads    | 6        | `media_cog.py`                              |
| Network              | 3        | `network_cog.py`                            |
| AI & Research        | 10       | `bot.py`                                    |
| Document Editing     | 6        | `doc_cog.py`                                |
| Security & Approvals | 7        | `bot.py`, `analytics_cog.py`                |
| Planning & Tasks     | 5        | `bot.py`                                    |
| Notifications        | 7        | `notify_cog.py`                             |
| **Total**            | **69**   | + 60 LLM-callable skill functions via `/ask` |

---

## Adding a New Command

1. Declare the function in [bot.py](../src/bot.py) with `@bot.tree.command(name=..., description=...)` or in a cog file under `src/cogs/` with `@app_commands.command(name=..., description=...)`
2. Call `is_allowed(interaction)` at the top of every command handler — no exceptions
3. Check `is_emergency_stopped()` for any write/mutating command
4. If the command is HIGH/CRITICAL risk, use `approval_store.create()` + `ApprovalView` (see `/restart` as the template)
5. Call `audit_log(interaction.user, "<action>", detail=..., result=...)` at every outcome branch
6. Assign the risk level in `config/permissions.yaml`
7. Add it to this document
