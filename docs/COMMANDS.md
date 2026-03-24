# OpenClaw — Discord Slash Commands Reference

All 37 slash commands, organized by phase. Agents adding new commands should check this list first to avoid duplication.

> **Risk levels:** LOW (auto-execute) | MEDIUM (logged) | HIGH (requires button approval) | CRITICAL (requires approval + preview)

---

## Phase 1 — Foundation

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/ping` | Check if OpenClaw is alive | — | LOW |
| `/about` | Show version and system info | — | LOW |
| `/whoami` | Show your Discord identity and permission status | — | LOW |
| `/help` | List all available commands | — | LOW |

---

## Phase 2 — Core Skills

| Command | Description | Parameters | Risk | Approval? |
|---------|-------------|-----------|------|-----------|
| `/containers` | List all running Docker containers (name, status, ports) | — | LOW | — |
| `/status <service>` | Detailed status for one container | `service: str` | LOW | — |
| `/logs <service> [lines]` | Tail recent container logs (default 30 lines) | `service: str`, `lines: int = 30` | LOW | — |
| `/system` | CPU, memory, disk usage via Glances | — | LOW | — |
| `/dockerstats` | Per-container resource usage | — | LOW | — |
| `/restart <service>` | Restart a Docker container | `service: str` | **HIGH** | ✅ Yes |

**`/restart` policy** — Allowed services are declared in `config/permissions.yaml`. Services like `traefik`, `socket-proxy`, `homepage`, and `watchtower` are explicitly denied and cannot be restarted via the bot regardless of approval.

---

## Phase 3 — LLM Integration

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/ask <question>` | Natural language AI query — routed to Ollama (simple) or Gemini (tool-requiring) | `question: str` | MEDIUM |
| `/clear` | Clear your conversation history for this channel | — | LOW |
| `/save <name>` | Save current conversation thread to disk | `name: str` | LOW |
| `/resume <name>` | Resume a previously saved thread | `name: str` | LOW |
| `/threads` | List all your saved conversation threads | — | LOW |
| `/forget <name>` | Delete a saved thread | `name: str` | LOW |

**Routing note** — `/ask` uses hybrid LLM routing. Simple/conversational queries go to local Ollama (`llama3.2:3b`, free, unlimited). Queries that mention containers, logs, services, media, or other tool-requiring topics go to Gemini 2.5 Flash. The response footer shows which model handled the request.

---

## Phase 4 — Security & Approvals

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/pending` | View all pending approval requests | — | LOW |
| `/auditlog [lines]` | View recent audit log entries (default 10, max 25) | `lines: int = 10` | LOW |
| `/estop [action]` | Activate or deactivate emergency stop | `action: str = "stop"` | **CRITICAL** |

**`/estop`** — `action` can be `stop` (default), `resume`, `start`, or `off`. When active, all write operations (`/restart`, `/ask` tool calls) are blocked. Shows a warning in every blocked response.

---

## Phase 5 — Advanced Skills

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/search <query> [type]` | Search Sonarr + Radarr for TV shows or movies | `query: str`, `media_type: str = "all"` | LOW |
| `/queue` | Show active downloads — SABnzbd + qBittorrent | — | LOW |
| `/recent [count]` | Recently added media from Plex (via Tautulli) | `count: int = 10` | LOW |
| `/health` | Check *arr services and download client health | — | LOW |
| `/ports` | Verify all services are listening on expected ports | — | LOW |
| `/report` | Comprehensive system status report | — | LOW |
| `/analyze <service> [lines]` | AI-powered container log analysis | `service: str`, `lines: int = 50` | LOW |
| `/schedule [action] [skill] [hour] [minute] [interval] [task_id]` | Manage recurring scheduled tasks | see below | LOW |
| `/skills` | List all available skill functions | — | LOW |
| `/remember <content> [tags]` | Store a fact in long-term QMD memory | `content: str`, `tags: str = ""` | LOW |
| `/recall <query>` | Search long-term QMD memory | `query: str` | LOW |
| `/mail <to> <subject> <body>` | Send email via AgentMail.to | `to: str`, `subject: str`, `body: str` | MEDIUM |

**`/schedule` actions:** `list` (default), `add` (requires `skill` + either `hour`/`minute` for daily or `interval` in minutes), `remove` (requires `task_id`). Persisted to `data/memory/`.

---

## Phase 6 — Remote Access & Monitoring

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/network` | Full network report: LAN, internet, DNS, Tailscale, OpenClaw health | — | LOW |
| `/tailscale` | Tailscale VPN status and device IP | — | LOW |
| `/speedtest` | Cloudflare download speed + DNS latency measurement | — | LOW |
| `/spending [breakdown]` | View Gemini API spend against budget | `breakdown: bool = false` | LOW |

---

## Phase 8 — Web, Browsing & Vision

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/websearch <query> [results]` | Live web search via Tavily (falls back to DuckDuckGo) | `query: str`, `results: int = 5` | LOW |
| `/browse <url> [question]` | Fetch and read a web page; optional Q&A | `url: str`, `question: str = ""` | LOW |
| `/analyze-image <image> [question]` | Analyze an uploaded image with Gemini vision | `image: attachment`, `question: str = ""` | LOW |
| `/analyze-file <file> [question]` | Analyze a document/file (PDF, TXT, JSON…) with Gemini | `file: attachment`, `question: str = ""` | LOW |

---

## Phase 9 — Mission Control (Kanban Task Board)

| Command | Description | Parameters | Risk |
|---------|-------------|-----------|------|
| `/tasks [status]` | View tasks from the Kanban board; filter by status | `status: str = ""` (all\|backlog\|in_progress\|done) | LOW |

**Mission Control** uses the ClawHub `mission-control` skill. Tasks are stored locally in `data/tasks.json` and synced to the public GitHub Pages dashboard at https://davevoyles.github.io/openclaw-dashboard/. The `/ask` command can also create, update, and complete tasks via natural language (LLM routes keywords like _task_, _kanban_, _backlog_, _in progress_, _todo_ to these skills).

**Skill functions available via `/ask`:**
- `get_mission_tasks(status)` — list tasks, optionally filtered by status
- `get_task_detail(task_id)` — show full detail for one task
- `update_task_status(task_id, new_status)` — move a task between columns
- `complete_task(task_id, summary)` — mark done and record a completion note
- `add_task_comment(task_id, comment)` — add a comment to a task

---

## Phase 10 — Ontology & Long-Term Memory (Graph-Based)

OpenClaw uses a structured graph-based memory system to track entities (people, projects, tools) and their relations. This is powered by the `ontology` skill and is primarily accessed via natural language in `/ask`.

**Skill functions available via `/ask`:**
- `ontology_create_entity(type, name, properties)` — add a new typed node to the graph
- `ontology_get_entity(name)` — retrieve full JSON details and relations for an entity
- `ontology_update_entity(name, properties)` — update or add metadata to an existing entity
- `ontology_query(query, type)` — search for entities by name or type
- `ontology_relate(source, relation, target)` — create a typed link (e.g., "Project A" *blocks* "Task B")
- `ontology_get_related(name, relation, direction)` — traverse the graph to find linked entities
- `ontology_validate()` — check the graph against the `schema.yaml` constraints

**Usage tip:** You can ask Gemini things like *"Link the 'API Gateway' project to the 'Slack Integration' task"* or *"Tell me everything you know about Dave"* and it will use these tools to consult the structured memory.

---

## Phase 11 — Self-Management & Enhanced Browsing (Git + Scrapers)

OpenClaw can now manage its own project structure and perform more robust web scraping using the `webfetch-md` and `git-essentials` skills.

**Skill functions available via `/ask`:**
| Tool | Description |
|------|-------------|
| `webfetch_md` | Smartly scrape any URL and convert main content to clean Markdown (strips navbars/ads). |
| `git_status` | Check project repository status for local code changes. |
| `git_log` | View recent code change history (commit log). |
| `git_diff` | Compare code changes or view uncommitted changes. |
| `git_commit` | Commit all current changes with a brief summary message. |
| `init_planning_files` | Initialize task_plan.md, findings.md, progress.md for complex tasks. |
| `update_plan_status` | Log progress or update status of a phase in planning files. |

---

## Phase 12 — Autonomous Agent Operations

Enhanced self-driving capabilities using the `planning-with-files` and `autonomous-loop` skills.

**Planning With Files:**
This pattern allows the agent to maintain "working memory on disk." Use `init_planning_files` at the start of any complex task.

**Autonomous Loop:**
When configured, the agent can work continuously in the background. Stop/Resume is handled via control files:
- `touch autonomous-loop.stop` — Pause current loops
- `rm autonomous-loop.stop` — Resume loops

---

## Adding a New Command

1. Declare the function in [bot.py](../bot.py) with `@bot.tree.command(name=..., description=...)`
2. Call `is_allowed(interaction)` at the top of every command handler — no exceptions
3. Check `is_emergency_stopped()` for any write/mutating command
4. If the command is HIGH/CRITICAL risk, use `approval_store.create()` + `ApprovalView` (see `/restart` as the template)
5. Call `audit_log(interaction.user, "<action>", detail=..., result=...)` at every outcome branch
6. Assign the risk level in `config/permissions.yaml`
7. Add it to this document
