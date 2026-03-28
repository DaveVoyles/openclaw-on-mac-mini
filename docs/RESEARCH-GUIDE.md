# 🔬 Research & Autonomous Features

OpenClaw operates autonomously — conducting multi-step research, monitoring infrastructure, tracking goals, and delivering daily briefings without manual prompting.

---

## 🔍 Research Workflow

### Basic Research

`/research <query>` triggers a multi-step autonomous research workflow in a dedicated Discord thread:

```
/research Find homes in Narberth PA under $450k with fenced yards
```

**Steps:**

1. **🗺️ Query Decomposition** — Gemini breaks the query into 3–5 specific, non-overlapping sub-searches via `chat_deep()`
2. **🔍 Parallel Search** — Up to 4 sub-queries execute concurrently (semaphore-limited to 3 at a time) using `search_web()`:
   - **Tavily** (primary, requires `TAVILY_API_KEY`) — higher quality, AI-enhanced results
   - **DuckDuckGo** (free fallback) — no API key required
3. **🌐 URL Browsing** — Reads top 2 pages per search (`browse_top_n=2`) for full content, with smart URL prioritization:
   - Social media deprioritized (Twitter/X, Facebook, Instagram, Reddit, YouTube)
   - Deduplication across all sub-searches
   - 20-second timeout per page, content capped at 3,000 chars
4. **🧠 Synthesis** — `chat_deep()` (extended thinking mode) synthesizes all sources into a structured report with:
   - Executive summary
   - Key findings (bullet points)
   - Detailed analysis with inline citations
   - Numbered source list
5. **💾 Storage** — report auto-saved to three destinations:
   - **Obsidian vault** (primary, via `obsidian_writer`)
   - **NAS** (`/volume1/documents/research/research_{date}_{slug}.md`)
   - **ChromaDB** `research` collection (for future `/research-search` queries)
   - **Google Docs** (if Maton API gateway is configured)
6. **💡 Follow-ups** — Gemini suggests 2–3 deeper research questions for continued exploration

**Prior research awareness** — before starting, the agent checks ChromaDB for related past research (threshold 0.6). If found, prior findings are injected into the synthesis prompt so the new report builds on — rather than repeats — earlier work.

### Research Search

Search across all your past research reports:

```
/research-search Docker security
```

Returns matching reports from the `research` collection with similarity scores.

---

### 📅 Scheduled Research (Updated)

You can now schedule research with precise timing using cron expressions:

```
/ask Schedule a prompt job with cron "0 7 * * 1,5": search ESPN for Division 1 
men's lacrosse games this week. Post a table to the lacrosse channel.
```

This creates a prompt job that runs every Monday and Friday at 7 AM with full 
tool access (web search, page browsing, etc.).

You can also schedule skill-based research with cron expressions:

```
/schedule add skill:run_scheduled_research cron:"0 9 * * 1-5" args:{"query": "Docker security updates"}
```

Or ask the bot naturally:

```
Schedule weekly research on house listings in Philadelphia
```

**How recurring research works:**
1. The scheduler invokes `run_scheduled_research(query)` at the configured interval
2. A full research cycle runs (same 6-step workflow)
3. After completion, the agent checks for prior reports on the same topic (85% similarity threshold)
4. If a prior report exists, a diff annotation is appended: *"This is a recurring research update. Previous report was {timestamp}."*
5. Results are stored and optionally posted to a Discord channel

**Scheduler configuration:**

| Field | Description |
|-------|-------------|
| `cron` | Cron expression for precise scheduling (e.g., `"0 7 * * 1,5"` = Mon+Fri at 7 AM). Uses `croniter`. |
| `prompt` | Natural language instruction for prompt jobs (LLM executes with full tool access) |
| `interval_minutes` | Run every N minutes (e.g., 10080 = weekly). Legacy; prefer `cron`. |
| `hour` + `minute` | Daily cron (e.g., `hour:8 minute:0` = daily at 8 AM). Legacy; prefer `cron`. |
| `alert_only` | Only post to Discord if result contains alert keywords |
| `notify_channel_id` | Discord channel for result notifications |

---

## 🌅 Morning Briefing (Daily)

Every day at **8:00 AM** (configurable via `BRIEFING_HOUR`), OpenClaw posts a briefing to `ALERT_CHANNEL_ID`:

**Data gathered concurrently:**

| Source | Function | Description |
|--------|----------|-------------|
| ☀️ Weather | `get_weather()` | Current conditions and forecast |
| 🏥 System Health | `check_arr_health()` | Sonarr, Radarr, Lidarr, Prowlarr status |
| 📥 Downloads | `get_download_queue()` | Active download queue status |
| 📊 System Stats | `get_system_stats()` | CPU, memory, disk usage |
| 📅 Calendar | `get_upcoming_events(days=1)` | Today's calendar events (8s timeout) |
| 🎯 Active Goals | `format_goals_for_briefing()` | Goals detected from conversations |

Gemini synthesizes all data into a concise, emoji-accented briefing (< 600 words) and posts it as an embed.

**On-demand**: use `/briefing` to trigger a briefing at any time in the current channel.

---

## 🔭 Proactive Monitoring (Every 2 Hours)

After a 2-hour startup delay, OpenClaw scans infrastructure every `PROACTIVE_SCAN_INTERVAL` seconds (default: 7200):

**Scan process:**

1. **Gather signals** — health checks + log snippets run concurrently:
   - `check_arr_health()` — *arr stack status
   - `check_download_clients()` — SABnzbd, qBittorrent
   - `check_plex_status()` — Plex availability
   - Container logs from `sonarr`, `radarr`, `sabnzbd`, `plex` (last 25 lines each, 6s timeout)
2. **Error scan** — regex scans logs for `error`, `warn`, `exception`, `critical`, `failed`
3. **LLM analysis** — if anomalies are found, Gemini evaluates whether the issue is actionable
4. **Self-healing** — the LLM can include `SELF_HEAL: restart_container <name>` directives:
   - ✅ **Safe to restart**: `sonarr`, `radarr`, `lidarr`, `prowlarr`, `sabnzbd`, `qbittorrent`, `tautulli`, `overseerr`
   - ❌ **Never restarted**: `plex`, `postgres`, `openclaw`
5. **Alert posting** — if actionable, posts a 🔭 **Proactive Insight** embed with auto-repair results

If everything is clean, the scan logs "all clear" and posts nothing.

---

## 🎯 Goal Tracking

The goal tracker (`src/goal_tracker.py`) automatically detects user intentions from conversation:

**Detection patterns** — statements containing:
- "I'm looking for…", "trying to…", "want to…", "need to…"
- "planning to…", "hoping to…", "going to…"
- "interested in…", "working on…", "building…", "learning…", "researching…"

**Workflow:**

1. **Detection** — `detect_goal()` matches against intent patterns (min 20 chars)
2. **Extraction** — Gemini extracts a concise goal statement (`temperature=0.1`)
3. **Dedup** — checks for existing goals with > 60% word overlap; if found, bumps `mention_count` instead
4. **Storage** — saved to `/memory/goals.json` with:

```json
{
  "goal": "Find a house in Narberth PA under $450k with a fenced yard",
  "user_id": 123456,
  "created_at": 1700000000,
  "last_mentioned": 1700000000,
  "mention_count": 3,
  "status": "active"
}
```

5. **Briefing integration** — active goals appear in the morning briefing (top 5, with mention counts)
6. **Lifecycle** — goals can be `active`, `completed`, or `dismissed`

| Command | Description |
|---------|-------------|
| `/goals` | View active goals |
| *(auto-detected)* | Goals are extracted from conversation passively |

---

## 📋 Agent Planning

OpenClaw can decompose complex tasks into multi-step plans with parallel execution:

**How it works:**
1. When a complex request is detected (via `/ask` or natural conversation), Gemini creates a plan
2. Plans are stored as Markdown files in `data/plans/` with checkbox-based step tracking
3. Steps can have dependencies and run in parallel (up to `MAX_WORKERS_PER_PLAN`, default 3)
4. Plans survive restarts — interrupted plans are detected on startup and reported to `ALERT_CHANNEL_ID`

**Plan step statuses**: `pending` → `in-progress` → `done` | `failed` | `skipped`

| Command | Description |
|---------|-------------|
| `/plans` | List active and recent plans (filterable: `all`, `in-progress`, `completed`, `interrupted`) |
| `/plan-detail <plan_id>` | Show full plan with step statuses |
| `/resume-plan <plan_id>` | Resume an interrupted plan |
| `/cancel-plan <plan_id>` | Cancel an active plan |

---

## 🪞 Self-Reflection

When `REFLECTION_ENABLED=true` (default), OpenClaw self-evaluates complex responses before sending:

1. After generating a response, a second LLM pass reviews it against the original question
2. If the reflection returns `LGTM`, the original response is sent as-is
3. If the reflection produces an improved version, that version replaces the original
4. This adds latency but catches errors, hallucinations, and incomplete answers

---

## ⚙️ Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `BRIEFING_HOUR` | `8` | Hour (0–23) to post the morning briefing |
| `BRIEFING_MINUTE_WINDOW` | `5` | Briefing fires only within the first N minutes of the hour |
| `BRIEFING_CHECK_INTERVAL` | `60` | Seconds between briefing schedule checks |
| `PROACTIVE_SCAN_INTERVAL` | `7200` | Seconds between proactive scans (default: 2 hours) |
| `PROACTIVE_LOG_LINES` | `25` | Lines fetched per container during proactive scan |
| `REFLECTION_ENABLED` | `true` | Enable self-evaluation of complex responses |
| `TAVILY_API_KEY` | *(empty)* | Tavily API key for premium search (falls back to DuckDuckGo) |
| `ALERT_CHANNEL_ID` | *(required)* | Discord channel for briefings and proactive alerts |
| `PLANS_DIR` | `data/plans` | Directory for plan Markdown files |
| `MAX_ACTIVE_PLANS` | `20` | Maximum concurrent plans |
| `MAX_WORKERS_PER_PLAN` | `3` | Max parallel workers per plan |
| `MAX_WORKERS_GLOBAL` | `10` | Max parallel workers across all plans |
| `PLAN_TIMEOUT` | `600` | Seconds before a plan step times out |

---

## 📁 Key Source Files

| File | Purpose |
|------|---------|
| `src/research_agent.py` | Multi-step research engine (`ResearchAgent` class) |
| `src/scheduler.py` | Scheduled task system (`TaskScheduler` class) |
| `src/goal_tracker.py` | Automatic goal detection and tracking |
| `src/agent_loop.py` | Persistent plan engine with parallel workers |
| `src/bot.py` | Morning briefing, proactive monitoring, slash commands |
| `src/llm.py` | Self-reflection logic (`_reflect_on_response`) |
| `skills/advanced_skills.py` | `search_web()` — Tavily + DuckDuckGo fallback |
