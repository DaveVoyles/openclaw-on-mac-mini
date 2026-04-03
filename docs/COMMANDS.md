# OpenClaw — Discord Slash Commands Reference

All 126 slash commands across `bot.py` and 24 cogs, organized by category.

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

**`/ask` follow-up UX (Pass 9)** — Every `/ask` response now includes 2 LLM-generated follow-up question buttons (grey, secondary style) and a **🔁 Go Deeper** button that re-runs the original question with a detailed-explanation prefix. Clicking a follow-up button generates a full new response with its own follow-up buttons, so the chain continues naturally.

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

> **💾 Save to NAS:** After `/doc create` or `/sheet create`, the response includes a **Save to NAS** button that rsyncs the generated file to the Synology NAS.

---

## Notes & Vault

Quick note-taking and full-text search across the Obsidian vault.

| Command                      | Description                                              | Parameters                                                    | Auth | Risk | File           |
| ---------------------------- | -------------------------------------------------------- | ------------------------------------------------------------- | ---- | ---- | -------------- |
| `/note create`               | Create a Markdown note and save to the Obsidian vault    | `title: str`, `content: str`, `tags: str (optional)`          | ✅   | LOW  | `note_cog.py`  |
| `/note list`                 | Browse recent vault notes, optionally filtered by type   | `type: research\|bookmark\|note\|analytics (optional)`        | ✅   | LOW  | `note_cog.py`  |
| `/note view`                 | Read a vault note's full content                         | `filename: str`                                               | ✅   | LOW  | `note_cog.py`  |
| `/note search`               | Full-text search across all vault notes                  | `query: str`                                                  | ✅   | LOW  | `note_cog.py`  |

**Examples:**
```
/note create title:"Meeting Notes" content:"Discussed migration timeline" tags:"meeting,planning"
/note list type:research
/note view filename:"2026-03-25-docker-patterns.md"
/note search query:"Sonarr upgrade"
```

**Implementation:** `src/cogs/note_cog.py` (Discord commands) + `src/obsidian_writer.py` (vault I/O).

---

## Research

| Command          | Description                                              | Parameters                                                            | Auth | Risk | File     |
| ---------------- | -------------------------------------------------------- | --------------------------------------------------------------------- | ---- | ---- | -------- |
| `/research`      | Autonomous multi-step research with synthesis            | `query: str`, `deep: bool (optional)`                                 | ✅   | LOW  | `bot.py` |

> **📎 Save to Vault:** After research completes, the report thread includes a **Save to Vault** button that writes the full report as a Markdown file to `data/vault/Research/`.

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

## Personal Assistant

Reminders, tasks, translation, polls, habits, expenses, and daily digests.

| Command                    | Description                                                    | Parameters                                                       | Auth | Risk | File              |
| -------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------- | ---- | ---- | ----------------- |
| `/remind set`              | Set a personal reminder (e.g. "in 30m take a break")           | `when: str`, `message: str`                                      | —    | LOW  | `reminder_cog.py` |
| `/remind list`             | List your active reminders                                     | —                                                                | —    | LOW  | `reminder_cog.py` |
| `/remind cancel`           | Cancel a pending reminder                                      | `reminder_id: str`                                               | —    | LOW  | `reminder_cog.py` |
| `/timer`                   | Start a countdown timer (DMs you when done)                    | `duration: str`                                                  | —    | LOW  | `reminder_cog.py` |
| `/todo add`                | Add a task with optional priority                              | `task: str`, `priority: low\|medium\|high (optional)`            | —    | LOW  | `todo_cog.py`     |
| `/todo list`               | List your tasks (filterable by status/priority)                | `status: str (optional)`, `priority: str (optional)`             | —    | LOW  | `todo_cog.py`     |
| `/todo done`               | Mark a task as completed                                       | `task_id: str`                                                   | —    | LOW  | `todo_cog.py`     |
| `/todo delete`             | Delete a task                                                  | `task_id: str`                                                   | —    | LOW  | `todo_cog.py`     |
| `/translate`               | Translate text to another language (Gemini-powered)            | `text: str`, `language: str`                                     | —    | LOW  | `translate_cog.py`|
| `/poll`                    | Create a reaction-based poll with auto-tally                   | `question: str`, `options: str` (comma-separated)                | —    | LOW  | `poll_cog.py`     |
| `/habit add`               | Start tracking a new daily habit                               | `name: str`                                                      | —    | LOW  | `habit_cog.py`    |
| `/habit checkin`           | Check in for today on a habit                                  | `name: str`                                                      | —    | LOW  | `habit_cog.py`    |
| `/habit streak`            | Show your current streak for a habit                           | `name: str`                                                      | —    | LOW  | `habit_cog.py`    |
| `/habit list`              | List all tracked habits with streaks                           | —                                                                | —    | LOW  | `habit_cog.py`    |
| `/habit delete`            | Stop tracking a habit                                          | `name: str`                                                      | —    | LOW  | `habit_cog.py`    |
| `/expense add`             | Log an expense with category and amount                        | `amount: float`, `category: str`, `note: str (optional)`         | —    | LOW  | `expense_cog.py`  |
| `/expense list`            | List recent expenses (filterable by category)                  | `category: str (optional)`, `days: int (optional)`               | —    | LOW  | `expense_cog.py`  |
| `/expense summary`         | Spending summary by category (weekly/monthly)                  | `period: week\|month (optional)`                                 | —    | LOW  | `expense_cog.py`  |
| `/expense delete`          | Delete an expense entry                                        | `expense_id: str`                                                | —    | LOW  | `expense_cog.py`  |

**Evening Digest** — automated 9 PM daily summary posted to `ALERT_CHANNEL_ID`. Covers today's reminders fired, tasks completed, habits checked in, expenses logged, and upcoming items. Complements the morning briefing. Configure the hour with `EVENING_DIGEST_HOUR` (default: `21`).

---

## Calendar & Email

Google Calendar event management and email read/send via Gmail or Outlook.

Requires: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN` (calendar); `GMAIL_USER`, `GMAIL_APP_PASSWORD` and/or `OUTLOOK_USER`, `OUTLOOK_APP_PASSWORD` (email).

| Command                                               | Description                                            | Parameters                                                              | Auth | Risk   | File               |
| ----------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------- | ---- | ------ | ------------------ |
| `/calendar today`                                     | List today's Google Calendar events                    | —                                                                       | —    | LOW    | `calendar_cog.py`  |
| `/calendar upcoming`                                  | Next N days of events (default 7)                      | `days: int (optional)`                                                  | —    | LOW    | `calendar_cog.py`  |
| `/calendar add <title> <when>`                        | Create a new calendar event                            | `title: str`, `when: str`, `description: str (optional)`, `location: str (optional)` | ✅ | MEDIUM | `calendar_cog.py` |
| `/calendar delete <event_id>`                         | Delete a calendar event                                | `event_id: str`                                                         | ✅   | HIGH   | `calendar_cog.py`  |
| `/email inbox`                                        | Show recent emails (default 10, ephemeral)             | `count: int (optional)`, `provider: str (optional)`                     | —    | LOW    | `email_cog.py`     |
| `/email read <id>`                                    | Read full email body (ephemeral)                       | `id: str`, `provider: str (optional)`                                   | —    | LOW    | `email_cog.py`     |
| `/email search <query>`                               | Search inbox (ephemeral)                               | `query: str`, `provider: str (optional)`                                | —    | LOW    | `email_cog.py`     |
| `/email send <to> <subject> <body>`                   | Send an email                                          | `to: str`, `subject: str`, `body: str`, `provider: str (optional)`      | ✅   | HIGH   | `email_cog.py`     |

---

## Journal

Vault-integrated daily journaling with AI writing prompts. Entries saved to `/vault/Journal/` as `Journal - YYYY-MM-DD.md` with Obsidian frontmatter.

| Command           | Description                                                      | Parameters                | Auth | Risk | File              |
| ----------------- | ---------------------------------------------------------------- | ------------------------- | ---- | ---- | ----------------- |
| `/journal write`  | Save today's journal entry (opens modal if no entry provided)    | `entry: str (optional)`   | —    | LOW  | `journal_cog.py`  |
| `/journal read`   | Read a past journal entry (default today)                        | `date: str (optional)`    | —    | LOW  | `journal_cog.py`  |
| `/journal streak` | Show consecutive days journaled                                  | —                         | —    | LOW  | `journal_cog.py`  |
| `/journal prompt` | Get an AI writing prompt from Gemini                             | —                         | —    | LOW  | `journal_cog.py`  |

---

## GitHub

PR and issue monitoring with background DM alerts. Polls watched repos every 30 minutes.

Requires: `GITHUB_TOKEN`, `GITHUB_DEFAULT_REPOS` (comma-separated).

| Command                  | Description                                              | Parameters                            | Auth | Risk   | File             |
| ------------------------ | -------------------------------------------------------- | ------------------------------------- | ---- | ------ | ---------------- |
| `/github prs`            | List open PRs for a repo                                 | `repo: str (optional)`                | —    | LOW    | `github_cog.py`  |
| `/github issues`         | List open issues for a repo                              | `repo: str (optional)`, `label: str (optional)` | — | LOW | `github_cog.py` |
| `/github watch <repo>`   | Subscribe to PR/issue activity DMs                       | `repo: str`                           | ✅   | MEDIUM | `github_cog.py`  |
| `/github unwatch <repo>` | Unsubscribe from repo activity DMs                       | `repo: str`                           | —    | LOW    | `github_cog.py`  |

---

## Document Review & Interview

Structured AI critique for text and files, plus an interactive interview mode for personalized output generation.

### Review

| Command            | Description                                                                    | Parameters                               | Auth | Risk | File              |
| ------------------ | ------------------------------------------------------------------------------ | ---------------------------------------- | ---- | ---- | ----------------- |
| `/review text`     | Opens a modal to paste text; returns structured AI critique as an embed        | `mode: writing\|technical\|quick (optional)` | —    | LOW  | `review_cog.py`   |
| `/review file`     | Upload a document for structured critique (DOCX, PDF, TXT, XLSX, MD, PY, JSON, CSV) | `mode: writing\|technical\|quick (optional)` | —    | LOW  | `review_cog.py`   |

**Mode options:**
- `writing` — clarity, tone, and structure
- `technical` — completeness, accuracy, and readability
- `quick` — 3-bullet summary

Output is an embed with **Strengths / Areas to Improve / Specific Suggestions** sections plus a **💾 Save Review to Vault** button that saves to `/vault/Reviews/` as Obsidian Markdown.

**Implementation:** `src/cogs/review_cog.py`

---

### Interview

| Command               | Description                                                                                     | Parameters    | Auth | Risk | File                |
| --------------------- | ----------------------------------------------------------------------------------------------- | ------------- | ---- | ---- | ------------------- |
| `/interview <goal>`   | Bot asks 3–5 clarifying questions via sequential Discord modals, then synthesizes tailored output | `goal: str`   | —    | LOW  | `interview_cog.py`  |

**Example goals:** `"write my bio"`, `"plan my week"`, `"draft a cover letter"`, `"help me decide X"`

Output is an embed summarising the synthesised result plus a **💾 Save to Vault** button. Each question modal has a 10-minute timeout.

**Implementation:** `src/cogs/interview_cog.py`

---

## 🎨 Image Generation

Generate images via a local Stable Diffusion instance.

Requires: `SD_URL` (Stable Diffusion API base URL, e.g. `http://192.168.1.93:7860`).

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/imagine generate <prompt>` | Generate an image via Stable Diffusion txt2img | `prompt: str`, `size: 512\|768\|1024 (optional)`, `negative: str (optional)` | ✅ | MEDIUM | `imagine_cog.py` |
| `/imagine status` | Check if Stable Diffusion is online and list available models | — | ✅ | LOW | `imagine_cog.py` |

---

## 🌐 DNS Management

Manage DNS filtering via AdGuard Home.

Requires: `ADGUARD_URL`, `ADGUARD_USER`, `ADGUARD_PASSWORD`.

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/dns status` | Show AdGuard Home status and filtering enabled/disabled | — | ✅ | LOW | `dns_cog.py` |
| `/dns stats` | Show query counts, block counts, and top domains | — | ✅ | LOW | `dns_cog.py` |
| `/dns block <domain>` | Block a domain via DNS rewrite | `domain: str` | ✅ | HIGH | `dns_cog.py` |
| `/dns allow <domain>` | Unblock a previously blocked domain | `domain: str` | ✅ | HIGH | `dns_cog.py` |
| `/dns blocked` | List all manually blocked domains | — | ✅ | LOW | `dns_cog.py` |

---

## 📝 Notion

Search and create Notion content via Maton automation.

Requires: `MATON_NOTION_SEARCH_URL`, `MATON_NOTION_CREATE_URL`, `MATON_NOTION_TODO_URL`.

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/notion search <query>` | Search Notion pages and databases | `query: str` | ✅ | LOW | `notion_cog.py` |
| `/notion page <title> <content>` | Create a new Notion page | `title: str`, `content: str` | ✅ | MEDIUM | `notion_cog.py` |
| `/notion todo <item>` | Add an item to the Notion todo database | `item: str` | ✅ | MEDIUM | `notion_cog.py` |

---

## 📄 Google Docs

Create and list Google Docs via Maton automation.

Requires: `MATON_GDOC_CREATE_URL`, `MATON_GDOC_LIST_URL`.

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/gdoc save <title> <content>` | Create a new Google Doc with the given content | `title: str`, `content: str` | ✅ | MEDIUM | `gdoc_cog.py` |
| `/gdoc list` | List recent Google Docs | — | ✅ | LOW | `gdoc_cog.py` |

---

## 🖥️ System Performance

Real-time system metrics via Glances.

Requires: `GLANCES_URL` (e.g. `http://192.168.1.93:61208`).

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/perf` | System snapshot — CPU, memory, disk usage, and load average | — | ✅ | LOW | `perf_cog.py` |

---

## 📱 Push Notifications

Send phone push notifications via ntfy.sh or a self-hosted ntfy instance.

Requires: `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` (optional).

Also exports `push_notification()` as a utility for other cogs to send phone alerts.

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/ntfy send <message>` | Send a phone push notification | `message: str`, `title: str (optional)`, `priority: min\|low\|default\|high\|urgent (optional)` | ✅ | MEDIUM | `ntfy_cog.py` |
| `/ntfy test` | Send a test push notification to verify setup | — | ✅ | LOW | `ntfy_cog.py` |

---

## 🎬 Movie & TV

Look up movies and TV shows via OMDb/IMDb.

Requires: `OMDB_API_KEY` (free at https://www.omdbapi.com/).

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/movie <title>` | Look up a movie with poster, ratings, and plot | `title: str` | — | LOW | `imdb_cog.py` |
| `/tv <title>` | Look up a TV show with season/episode info and ratings | `title: str` | — | LOW | `imdb_cog.py` |
| `/imdb <query>` | Search both movies and TV shows | `query: str` | — | LOW | `imdb_cog.py` |

---

## 🐛 Error Monitoring

Monitor application errors via Sentry.

Requires: `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_URL` (default `https://sentry.io`).

| Command | Description | Parameters | Auth | Risk | File |
| ------- | ----------- | ---------- | ---- | ---- | ---- |
| `/sentry issues [project]` | List unresolved Sentry issues | `project: str (optional)` | ✅ | LOW | `sentry_cog.py` |
| `/sentry projects` | List all Sentry projects in the org | — | ✅ | LOW | `sentry_cog.py` |
| `/sentry resolve <issue_id>` | Mark a Sentry issue as resolved | `issue_id: str` | ✅ | HIGH | `sentry_cog.py` |
| `/sentry stats [project]` | Show hourly error rate stats | `project: str (optional)` | ✅ | LOW | `sentry_cog.py` |

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
| Personal Assistant   | 19       | `reminder_cog.py`, `todo_cog.py`, `translate_cog.py`, `poll_cog.py`, `habit_cog.py`, `expense_cog.py` |
| Calendar & Email     | 8        | `calendar_cog.py`, `email_cog.py`           |
| Journal              | 4        | `journal_cog.py`                            |
| GitHub               | 4        | `github_cog.py`                             |
| Document Review & Interview | 3   | `review_cog.py`, `interview_cog.py`         |
| Image Generation     | 2        | `imagine_cog.py`                            |
| DNS Management       | 5        | `dns_cog.py`                                |
| Notion               | 3        | `notion_cog.py`                             |
| Google Docs          | 2        | `gdoc_cog.py`                               |
| System Performance   | 1        | `perf_cog.py`                               |
| Push Notifications   | 2        | `ntfy_cog.py`                               |
| Movie & TV           | 3        | `imdb_cog.py`                               |
| Error Monitoring     | 4        | `sentry_cog.py`                             |
| **Total**            | **129**  | + 60 LLM-callable skill functions via `/ask` |

---

## Adding a New Command

1. Declare the function in [bot.py](../src/bot.py) with `@bot.tree.command(name=..., description=...)` or in a cog file under `src/cogs/` with `@app_commands.command(name=..., description=...)`
2. Call `is_allowed(interaction)` at the top of every command handler — no exceptions
3. Check `is_emergency_stopped()` for any write/mutating command
4. If the command is HIGH/CRITICAL risk, use `approval_store.create()` + `ApprovalView` (see `/restart` as the template)
5. Call `audit_log(interaction.user, "<action>", detail=..., result=...)` at every outcome branch
6. Assign the risk level in `config/permissions.yaml`
7. Add it to this document
