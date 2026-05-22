# OpenClaw Skills Catalog
<!-- Updated: 2026-05-21 -->

Quick-reference for all 113 LLM-facing tools declared in `config/tools.yaml`. These are the callable skills available to the AI agent. Each entry maps to a Python function registered in `skills/__init__.py` or a module imported by `src/slack_bot.py`.

**For agents:** Use this table to discover existing skills before writing a new one. If a skill already covers your use case, call it instead of duplicating logic. To add a new skill, see [`docs/AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md).

---

### Docker & Containers

| Tool | Description |
| ---- | ----------- |
| `list_containers` | List all running Docker containers with name, status, and ports. |
| `get_container_status` | Get detailed status, resource usage, and port mapping for a specific Docker container. |
| `get_container_logs` | Retrieve the last N lines of logs from a Docker container. |
| `restart_container` | Restart a Docker container. Use when a service is unhealthy or needs a fresh start. |
| `stop_container` | Stop a running Docker container gracefully. |
| `pause_container` | Pause a running Docker container (freezes processes without stopping). |
| `unpause_container` | Unpause a paused Docker container (resumes frozen processes). |
| `get_docker_stats` | Get CPU, memory, and network usage for all running containers. |
| `get_system_stats` | Get Mac Mini system resource usage (CPU, memory, disk). |
| `get_uptime` | Get system uptime of the Mac Mini. |
| `get_compose_config` | Read the Docker Compose configuration file and return port mappings, volume mounts, and env vars. |
| `check_service_ports` | Check if all key services are listening on their expected ports. |
| `create_status_report` | Generate a comprehensive system status report covering all services, downloads, and Plex. |
| `analyze_logs` | Analyze container logs using AI to identify errors, warnings, and suggest fixes. |
| `ping_host` | Ping a hostname or IP to check connectivity and latency. |

---

### Media & Downloads

| Tool | Description |
| ---- | ----------- |
| `check_arr_health` | Check health status of all *arr services (Sonarr, Radarr, Lidarr, Prowlarr). |
| `check_download_clients` | Check connectivity of download clients (SABnzbd and qBittorrent). |
| `check_plex_status` | Check Plex server status and version via Tautulli. |
| `get_plex_activity` | Get real-time Plex activity: who is currently watching, what content, playback progress. |
| `get_recent_additions` | Get recently added media from Plex (via Tautulli). |
| `search_media` | Search for TV shows or movies across Sonarr and Radarr catalogs. |
| `add_to_sonarr` | Add a TV show to Sonarr for automatic downloading. Searches by title if no tvdb_id provided. |
| `add_to_radarr` | Add a movie to Radarr for automatic downloading. Searches by title if no tmdb_id provided. |
| `get_download_queue` | Get active downloads from SABnzbd (Usenet) and qBittorrent (torrents). |
| `get_pending_requests` | List all pending media requests awaiting approval in Overseerr. |
| `approve_request` | Approve a pending Overseerr media request by its numeric ID. |
| `deny_request` | Decline a pending Overseerr media request by its numeric ID. |
| `get_request_stats` | Get a summary count of all Overseerr media requests by status. |

---

### Storage & NAS

| Tool | Description |
| ---- | ----------- |
| `get_nas_storage_health` | Get Synology NAS storage volume and disk health status. |
| `get_backup_status` | Get Synology Hyper Backup task status and last run time. |
| `get_nas_alerts` | Get Synology DSM system health alerts (fans, temperature, power, disk warnings). |
| `get_disk_smart_status` | Get SMART health status for all physical disks in the Synology NAS. |
| `nas_list_folder` | List the contents of a folder on the Synology NAS. |
| `nas_create_folder` | Create a new folder on the Synology NAS FileStation. |
| `nas_write_file` | Write a text or markdown file directly to the Synology NAS. |
| `nas_search_files` | Search recursively across Synology NAS shares for files and folders. |

---

### System & Network

| Tool | Description |
| ---- | ----------- |
| `get_network_status` | Check LAN, internet, DNS, and Tailscale VPN connectivity and latency. |
| `get_tailscale_status` | Show Tailscale VPN status and this device's Tailscale IP address. |
| `run_speed_test` | Run a brief network speed test and return measured download and upload speeds. |

---

### Memory & Knowledge

| Tool | Description |
| ---- | ----------- |
| `remember_fact` | Store a fact or piece of information in long-term memory (QMD). |
| `recall_fact` | Search long-term memory (QMD) for a specific fact or information. |
| `list_memories` | List all facts stored in long-term memory (QMD). |
| `dream_now` | Run a cognitive dream cycle to consolidate memories, score importance, and prune stale entries. |
| `get_memory_health` | Return current memory health score and metrics. |

---

### Email & Calendar

| Tool | Description |
| ---- | ----------- |
| `send_agent_mail` | Send an automated e-mail message to a single recipient. |
| `read_inbox` | Read the most recent emails from a Gmail or Outlook inbox. |
| `search_emails` | Search for emails by keyword in the Gmail or Outlook inbox. |
| `send_email` | Send an email via Gmail or Outlook. |
| `get_upcoming_events` | Get Google Calendar events scheduled in the next N days. |
| `create_calendar_event` | Create a Google Calendar event with a title, start/end time, and description. |
| `get_todays_events` | Get all Google Calendar events scheduled for today. |

---

### Web Search & Browsing

| Tool | Description |
| ---- | ----------- |
| `search_web` | Search the live web using a 5-tier cascade (Perplexity AI → Tavily → DDG → Bing Lite → DuckDuckGo). |
| `get_weather` | Get current weather conditions and a 3-day forecast for any location. |
| `browse_url` | Fetch and read the content of a specific web page or URL. |
| `firecrawl_scrape` | Scrape a URL via Firecrawl API and return clean markdown content. |
| `serper_search` | Search Google via Serper API — returns actual Google SERP results. |
| `webfetch_md` | Smartly scrape and fetch any URL, converting it into clean markdown. |
| `compare_sources` | Browse multiple URLs in parallel and synthesize a comparison report. |
| `compare_search_providers` | Compare answers from multiple search providers for the same query. |

---

### URL Monitoring

| Tool | Description |
| ---- | ----------- |
| `snapshot_url` | Take a baseline snapshot of a URL for change monitoring. |
| `check_url_for_changes` | Compare the current content of a URL against its stored snapshot. |
| `list_monitored_urls` | List all URLs currently being monitored for content changes. |
| `remove_url_monitor` | Stop monitoring a URL and remove its snapshot record. |

---

### RSS Feeds

| Tool | Description |
| ---- | ----------- |
| `fetch_rss_feed` | Fetch recent items from any RSS or Atom feed URL. |
| `search_rss` | Fetch a feed and filter items matching a keyword or phrase. |
| `get_rss_digest` | Fetch multiple RSS/Atom feeds in parallel and synthesize a digest. |
| `list_rss_feeds` | List all RSS/Atom feed URLs that have been fetched or saved. |

---

### Git & Code

| Tool | Description |
| ---- | ----------- |
| `git_status` | Check the project's Git status, showing which files are staged, modified, or untracked. |
| `git_log` | View recent commit history for the project codebase. |
| `git_diff` | Compare code changes between current state and previous commits. |
| `git_commit` | Commit all current changes with a brief descriptive message. |
| `execute_python_code` | Execute Python code in a sandboxed environment for calculations or data processing. |

---

### Scheduling & Automation

| Tool | Description |
| ---- | ----------- |
| `create_scheduled_task` | Create a recurring scheduled task (skill call or research query). |
| `cancel_scheduled_task` | Cancel a scheduled task by its task ID. |
| `list_scheduled_tasks` | List all active scheduled tasks with IDs, actions, and schedules. |
| `run_scheduled_research` | Run a research query autonomously (for scheduled/background use). |
| `schedule_research_report` | Schedule a recurring research report on a topic. |

---

### Planning & Goals

| Tool | Description |
| ---- | ----------- |
| `create_plan` | Create a new task plan with a goal and ordered steps. |
| `update_plan_step` | Update a step's status in an active plan. |
| `read_plan` | Read the current state of a plan including all step statuses. |
| `list_plans` | List plans filtered by status. |
| `adjust_plan` | Add, remove, or reorder steps in an active plan. |
| `cancel_plan` | Cancel an active plan, marking it as interrupted. |
| `resume_plan` | Resume an interrupted plan from where it left off. |
| `execute_plan` | Execute all pending steps of a plan autonomously. |
| `decompose_goal` | Break a complex goal into concrete Mission Control tasks using AI reasoning. |
| `init_planning_files` | Initialize Manus-style file-based planning (task_plan.md, findings.md). |
| `update_plan_status` | Log progress or update status of a phase in the current plan file. |

---

### Mission Control

| Tool | Description |
| ---- | ----------- |
| `get_mission_tasks` | List Mission Control Kanban tasks. Optionally filter by status or label. |
| `get_task_detail` | Get full details for a specific Mission Control task including comments. |
| `update_task_status` | Update a Mission Control task's status. |
| `complete_task` | Mark a Mission Control task as complete with a summary. |
| `add_task_comment` | Add a comment or progress update to a Mission Control task. |
| `spawn_worker` | Spawn a focused AI sub-agent to accomplish a specific goal autonomously. |

---

### Reporting & Recaps

| Tool | Description |
| ---- | ----------- |
| `get_available_templates` | Get list of all available recap templates with names and descriptions. |
| `generate_recap_from_template` | Generate a topic-specific weekly recap using a predefined template. |
| `generate_channel_recap_report` | Summarize recent Slack channel or thread activity into highlights. |
| `generate_sports_watch_report` | Create a structured sports watch guide with upcoming matchups and streaming info. |
| `generate_box_office_report` | Create a weekly box-office financial report with new-release rankings. |

---

### Finance & Spending

| Tool | Description |
| ---- | ----------- |
| `get_spending` | Get current Gemini API spending summary including total cost and per-model breakdown. |
| `get_daily_spending` | Get daily spending breakdown for the last N days. |
| `check_patreon_health` | Check Patreon download automation health and cookie expiry. |

---

### Ontology & Graph

| Tool | Description |
| ---- | ----------- |
| `ontology_create_entity` | Create a structured ontology entity in the local graph memory store. |
| `ontology_get_entity` | Fetch a single ontology entity by ID. |
| `ontology_query` | Query ontology entities by type and property filters. |
| `ontology_update_entity` | Update properties on an existing ontology entity. |
| `ontology_relate` | Create a typed relation between two ontology entities. |
| `ontology_get_related` | Get entities related to a given ontology entity. |
| `ontology_validate` | Validate the ontology graph against the local schema and report issues. |

---

### Cloud Files

| Tool | Description |
| ---- | ----------- |
| `create_google_doc` | Create a Google Doc with a title and text content. |
| `create_onedrive_file` | Save a text or markdown file to OneDrive. |

---

### Gateway & OAuth

| Tool | Description |
| ---- | ----------- |
| `gateway_request` | Call any third-party API (Slack, GitHub, Google Sheets, Notion) via a Maton OAuth connection. |
| `gateway_list_connections` | List all active Maton OAuth connections. |
| `gateway_create_connection` | Create a new Maton OAuth connection for a third-party app. |

---

## Maintenance

This catalog is generated from `config/tools.yaml`. When you add a new skill:

1. Add the Python function to `skills/__init__.py` (or a new module)
2. Register the function in the `SKILLS` dict in `skills/__init__.py`
3. Add a tool declaration to `config/tools.yaml` (name + description + parameters)
4. Update this file with a one-liner in the appropriate section

See [`docs/AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md) for the full step-by-step recipe.
