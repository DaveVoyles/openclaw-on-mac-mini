# OpenClaw Command Reference

> Source of truth: `src/dashboard/helpers.py::_raw_command_groups()`
>
> This file is generated from runtime command metadata used by the dashboard and guide command finder.

Total documented commands: **134**

## Quick Start

- `/help` — Browse commands by category
- `/ask` — Use plain English and let OpenClaw route tools
- `/research` — Run deep multi-source research
- `/schedule` — Create and manage automations
- `/incident start` — Kick off guided incident triage
- `/recap weekly` — Summarize a channel/thread quickly

## 🏛️ Foundation

| Command | Description |
| --- | --- |
| `/ping` | Check if bot is alive |
| `/about` | Version and system info |
| `/whoami` | Your Discord identity & permissions |
| `/help` | List all commands |

## 🐳 Docker & System

| Command | Description |
| --- | --- |
| `/containers` | List running containers |
| `/status <service>` | Container detail + resources |
| `/logs <service> [lines]` | View container logs |
| `/system` | CPU, memory, disk usage |
| `/dockerstats` | Per-container resource usage |
| `/restart <service>` | Restart a container (requires approval) |

## 🤖 AI & LLM

| Command | Description |
| --- | --- |
| `/ask <question> [model] [scope] [reset_context] [anchor]` | AI-powered query — auto-routes to Gemini (tools) or Ollama (chat). Context controls are first-class slash options: scope (current/cross-channel/prior-report), reset_context, and anchor override ('none' disables anchor). Legacy inline flags (e.g. --cross-channel, --reset-context, --anchor, --no-anchor) still work. |
| `/model show` | Show your current LLM routing preference and Ollama status. |
| `/model set <preference>` | Set your default LLM routing: auto (smart), local (Gemma), gemini (cloud), openai (GPT-4o), or anthropic (Claude). Alias accepted: claude → anthropic. |
| `/research <query> [deep:true]` | Deep multi-step research — Discord thread, planned sub-queries, 4-tier search (Perplexity → Tavily → DDG → Bing Lite), source ranking, cross-referencing, confidence levels, synthesized report with methodology section |
| `/weather [location]` | Current conditions + 3-day forecast for any location (default: WEATHER_DEFAULT_LOCATION env var) |
| `/clear` | Clear active conversation history |
| `/save <name>` | Save current conversation as a named thread (persisted to disk) |
| `/resume <name>` | Resume a previously saved conversation thread |
| `/threads` | List all your saved conversation threads |
| `/forget <name>` | Delete a saved conversation thread |
| `/analyze <service> [lines]` | AI log analysis |

## 🗓️ Recaps & Watch Guides

| Command | Description |
| --- | --- |
| `/recap weekly [days] [style]` | Summarize the current Discord channel or thread with highlights, action items, or a compact table. Optional save-to-vault and Monday scheduling. |
| `/sports upcoming [query]` | Create a sports watch guide with matchups, ET kickoff times, and where-to-watch details from live web research. Optional save-to-vault and Monday scheduling. |
| `Create recap from thread` | Right-click a Discord message or thread to generate a recap without typing a slash command. |

## 🎬 Media & Downloads

| Command | Description |
| --- | --- |
| `/search <query> [type]` | Search Sonarr/Radarr catalogs |
| `/queue` | Active downloads (SABnzbd + qBit) |
| `/recent [count]` | Recently added Plex media |
| `/health` | Check *arr + download client health |
| `/ports` | Service port connectivity check |
| `/report` | Comprehensive status report |

## 🚨 Incident Operations

| Command | Description |
| --- | --- |
| `/incident start <title> <severity> [details] [services]` | Create an incident and post Copilot triage summary + recommended actions in the incident thread. |
| `/incident create <title> <severity> [details]` | Create a manual incident room entry without Copilot triage. |
| `/incident status <id> [state] [note]` | Check or update incident state (open/investigating/monitoring). |
| `/incident list [state] [limit]` | List recent incidents (active/all/open/investigating/monitoring/resolved). |
| `/incident timeline [id] [limit]` | Show timeline events for an incident; defaults to current incident thread when possible. |
| `/incident resolve <id> <summary> [action_items] [notes]` | Resolve an incident and capture postmortem notes/actions. |

## 🧠 Memory & Automation

| Command | Description |
| --- | --- |
| `/remember <fact> [tags]` | Store a fact in long-term memory |
| `/recall <query>` | Search long-term memory |
| `/schedule` | Manage scheduled tasks (CRUD via slash command) |
| `/skills` | List all LLM-callable skills |
| `/briefing` | On-demand morning briefing (weather + health + calendar) |
| `/audit-summary` | Analytics on today's audit log |
| `/nowplaying` | Live Plex active streams |
| `/dream` | Run cognitive dream cycle (memory consolidation) |
| `/memory-health` | Show memory health score and 5 metrics |
| `/memory-export` | Export memory bundle |

## 🌐 Network & Monitoring

| Command | Description |
| --- | --- |
| `/network` | LAN, internet, DNS connectivity |
| `/tailscale` | Tailscale VPN status |
| `/speedtest` | Network speed test |
| `/spending [breakdown]` | Gemini API cost tracking |

## Security & Admin

| Command | Description |
| --- | --- |
| `/pending` | Pending approval requests |
| `/auditlog [lines]` | View audit trail |
| `/estop [stop\|resume]` | Emergency stop all actions |
| `/mail <to> <subject> <body>` | Send email via AgentMail |

## 📋 Copy/Paste Workflow

| Command | Description |
| --- | --- |
| `/recap copy-latest` | Copy-ready export of your latest OpenClaw response in the current channel/thread |
| `/recap copy-thread [days] [style]` | Generate and export a copy-ready recap for the current channel/thread |
| `Context menu: Copy Workflow Context` | Right-click any message to export a mobile-friendly copy block |

## Document Review & Interview

| Command | Description |
| --- | --- |
| `/review text [mode]` | Paste text for structured critique (writing/technical/quick) |
| `/review file [mode]` | Upload DOCX/PDF/TXT/etc for structured critique |
| `/interview <goal>` | Sequential Q&A modals → personalized output |

## Calendar & Email

| Command | Description |
| --- | --- |
| `/calendar today` | List today's Google Calendar events |
| `/calendar upcoming [days]` | Next N days of events |
| `/calendar add <title> <when>` | Create event |
| `/email inbox [count]` | Show recent emails (ephemeral) |
| `/email search <query>` | Search inbox |
| `/email read <id>` | Read full email |
| `/email send <to> <subject> <body>` | Send email (requires approval) |

## Journal & GitHub

| Command | Description |
| --- | --- |
| `/journal write [entry]` | Save today's journal entry to vault |
| `/journal read [date]` | Read past entry |
| `/journal streak` | Streak counter |
| `/journal prompt` | AI writing prompt |
| `/github prs [repo]` | List open pull requests |
| `/github issues [repo]` | List open issues |
| `/github watch <repo>` | Subscribe to activity DMs |

## 🎨 Image Generation

| Command | Description |
| --- | --- |
| `/imagine generate <prompt> [size] [negative]` | Generate image via Stable Diffusion txt2img |
| `/imagine status` | Check SD online status and list models |

## 🌐 DNS Management

| Command | Description |
| --- | --- |
| `/dns status` | AdGuard Home status and filtering toggle |
| `/dns stats` | Query/block counts and top domains |
| `/dns block <domain>` | Block domain via DNS rewrite |
| `/dns allow <domain>` | Unblock a domain |
| `/dns blocked` | List all manually blocked domains |

## 📝 Notion

| Command | Description |
| --- | --- |
| `/notion search <query>` | Search Notion pages and databases |
| `/notion page <title> <content>` | Create a new Notion page |
| `/notion todo <item>` | Add item to Notion todo database |

## 📄 Google Docs

| Command | Description |
| --- | --- |
| `/gdoc save <title> <content>` | Create a new Google Doc |
| `/gdoc list` | List recent Google Docs |

## 🖥️ System Performance

| Command | Description |
| --- | --- |
| `/perf` | CPU, memory, disk, load average via Glances |

## 📱 Push Notifications

| Command | Description |
| --- | --- |
| `/ntfy send <message> [title] [priority]` | Send phone push notification via ntfy |
| `/ntfy test` | Send test notification to verify setup |

## 📲 SMS One-Tap

| Command | Description |
| --- | --- |
| `/sms config <phone> [send_verification]` | Save phone number for one-tap SMS; can trigger verification send |
| `/sms test [code]` | Start verification or submit code from SMS |
| `/sms status` | Show masked phone, verification state, and remaining send budget |
| `/sms send <message>` | Confirmation-based SMS send to configured phone |
| `Context menu: Send to SMS` | Right-click a Discord message and forward it via SMS with confirmation |

## 🎬 Movie & TV

| Command | Description |
| --- | --- |
| `/media movie <title>` | Look up a movie with poster and ratings |
| `/media tv <title>` | Look up a TV show with season/episode info |
| `/media search <query>` | Search movies and TV via OMDb |

## 🐛 Error Monitoring

| Command | Description |
| --- | --- |
| `/sentry issues [project]` | List unresolved Sentry issues |
| `/sentry projects` | List Sentry org projects |
| `/sentry resolve <issue_id>` | Resolve a Sentry issue |
| `/sentry stats [project]` | Hourly error rate stats |

## Third-Party API Gateway (via /ask)

| Command | Description |
| --- | --- |
| `gateway_request` | Call any of 100+ APIs (Slack, GitHub, Notion, HubSpot, Stripe…) via Maton managed OAuth. Invoked by /ask. |
| `gateway_list_connections` | List active Maton OAuth connections (optionally filter by app). Invoked by /ask. |
| `gateway_create_connection` | Create a new Maton OAuth connection for an app and return the authorization URL. Invoked by /ask. |

## Knowledge Graph & Ontology (via /ask)

| Command | Description |
| --- | --- |
| `ontology_create_entity` | Create a new typed entity (Person, Project, Task, etc.) in graph memory. |
| `ontology_get_entity` | Retrieve all details and relations for a specific entity name. |
| `ontology_relate` | Create a typed link between two entities (e.g., 'blocks', 'manages'). |
| `ontology_query` | Search the knowledge graph for entities by name or type. |

## Self-Management & Autonomy (via /ask)

| Command | Description |
| --- | --- |
| `spawn_worker` | Spawn a focused AI sub-agent to accomplish a specific goal autonomously using its own tool loop. |
| `create_scheduled_task` | Create a recurring scheduled task (LLM-controlled). Supports cron expressions, prompt jobs, or interval-based. |
| `cancel_scheduled_task` | Cancel a scheduled task by ID. |
| `list_scheduled_tasks` | List all active scheduled tasks with cron expressions, run counts, and next run times. |
| `webfetch_md` | Smartly scrape any URL and convert main content to clean Markdown. |
| `git_status` | Check project repository status for code changes. |
| `git_log` | View recent code change history (commit log). |
| `git_diff` | Compare code changes or view uncommitted changes. |
| `git_commit` | Commit all current changes with a brief summary message. |
| `init_planning_files` | Initialize task_plan.md, findings.md, progress.md for complex tasks. |
| `update_plan_status` | Log progress or update status of a phase in planning files. |

## 📝 Notes & Vault

| Command | Description |
| --- | --- |
| `/note create` | Create a note in the Obsidian vault |
| `/note list` | Browse recent vault notes |
| `/note view` | View a vault note's content |
| `/note search` | Search vault notes by content |

## 📋 Agent Loop & Plans

| Command | Description |
| --- | --- |
| `/plans [status]` | List active/recent agent plans. Filter: all, in-progress, completed, interrupted. |
| `/plan-detail <plan_id>` | Show full details of a specific plan (steps, status, outputs). |
| `/resume-plan <plan_id>` | Resume an interrupted plan from where it left off. |
| `/cancel-plan <plan_id>` | Cancel an active plan (marks interrupted, resets in-progress steps). |
| `create_plan` | (via /ask) Create a new task plan with a goal and ordered steps. Returns plan_id. |
| `update_plan_step` | (via /ask) Update a step's status (done/failed/skipped) with output summary. |
| `read_plan` | (via /ask) Read the current state of a plan including all step statuses. |
| `list_plans` | (via /ask) List plans filtered by status. |
| `adjust_plan` | (via /ask) Add, remove, or reorder steps in an active plan. |
| `cancel_plan` | (via /ask) Cancel an active plan and mark it interrupted. |
| `resume_plan` | (via /ask) Resume an interrupted plan from where it left off. |

---

_Generated from runtime metadata to prevent command-doc drift._
