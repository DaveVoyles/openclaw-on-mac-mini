# OpenClaw — Architecture

<!-- Updated: 2026-05-21 -->

OpenClaw is a Slack-first personal AI assistant running in Docker on a Mac Mini M4. This doc gives the 30-second mental model. For extending the system, see [`AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md). For the day-to-day "what is what", see [`AGENT-GUIDE.md`](AGENT-GUIDE.md).

> **Heads up (May 2026):** Discord and the browser dashboard were removed. The historical Discord-era `ARCHITECTURE.md` and `MODULES.md` were replaced by this shorter, accurate version. Older revisions still exist in git history if you need them.

---

## Topology

```
                ┌──────────────────────────────────────────┐
                │            Mac Mini M4 (host)            │
                │  192.168.1.93                            │
                │                                          │
                │  ┌────────────────────────────────────┐  │
                │  │ openclaw container (Docker)        │  │
                │  │   src/slack_bot.py  (entrypoint)   │  │
                │  │   skills/* + src/*  (registry)     │  │
                │  │   /health on :8765                 │  │
                │  └─────────────┬──────────────────────┘  │
                │                │ stdio + named pipes     │
                │  ┌─────────────▼──────────────────────┐  │
                │  │ host_bridge.py  (host-side proc)   │  │
                │  │   spawns `gh copilot` CLI sessions │  │
                │  └────────────────────────────────────┘  │
                │                                          │
                │  Plex (native)   OrbStack   ChromaDB     │
                └──────────────────┬───────────────────────┘
                                   │
                          NFS / SMB │ mounts
                                   │
                ┌──────────────────▼───────────────────────┐
                │      Synology DS920+   192.168.1.8       │
                │      Storage · Traefik · Backups         │
                └──────────────────────────────────────────┘

                       ▲                       ▲
                       │ Socket Mode           │ HTTPS health probe
                       │                       │
                  Slack workspace          Uptime Kuma
```

## Process layout

| Process | Where | What |
| --- | --- | --- |
| `openclaw` container | Mac Mini Docker | Slack Bolt bot, skill registry, LLM router, scheduler, health server |
| `host_bridge.py` | Mac Mini host (outside Docker) | Spawns `gh copilot` CLI sessions on the host on demand from Slack |
| Slack workspace | Slack cloud | UI; talks to bot via Socket Mode (no inbound webhook needed) |
| Synology NAS | 192.168.1.8 | NFS-mounted media, Traefik reverse proxy, off-host backups |

## Source layout

```
src/
├── slack_bot.py            # entrypoint: Slack Bolt app, /health server, scheduler loops
├── llm/                    # LLM provider + chat orchestration
│   ├── chat.py             # main chat() driver
│   └── providers.py        # Gemini · Copilot · OpenAI · Ollama · circuit breaker
├── ask_executor.py         # standalone agent-ask flow used by /chat
├── ask_orchestrator.py     # tool-call loop driver
├── agent_loop.py           # autonomous worker-agent loop (spawn_worker)
├── host_bridge.py          # host-side `gh copilot` session manager
├── nas.py                  # NAS file ops, backup status
├── dropbox_sync.py         # /dropbox slash command + watch loop
├── gateway.py              # outbound HTTP gateway w/ caching + retry
├── vector_store_*.py       # ChromaDB-backed memory
├── openclaw_cli*.py        # local CLI surface (~12 modules, separate from container)
├── builders/  utils/  plugin_system/   # supporting packages
└── ~140 other modules for individual capabilities

skills/
├── __init__.py             # central SKILLS dict (182 entries)
├── advanced_skills.py · browser_skills.py · digest_skills.py
├── finance_skills.py · health_skills.py · media_skills.py
├── news_skills.py · ocr_skill.py · patreon_skills.py
└── ~27 more domain-specific skill modules

config/
└── tools.yaml              # LLM-facing tool manifest (subset of SKILLS exposed to the model)
```

Counts (May 2026): ~150 `.py` files in `src/`, 36 modules in `skills/`, 182 entries in `SKILLS`.

## Request lifecycle (`/chat <message>`)

1. Slack sends the slash command over Socket Mode → `src/slack_bot.py`.
2. `slack_bot.py` validates the user, posts an ephemeral ack, and calls `ask_executor.execute_agent_ask(...)`.
3. `ask_orchestrator` builds the prompt, calls `llm.chat()`, and runs the tool-call loop until the model stops requesting tools.
4. Tool calls dispatch into `SKILLS[tool_name]`; the most common tools are listed in `config/tools.yaml`.
5. The final answer is posted back to Slack as a thread reply (or DM).

## Background loops

Registered in `src/slack_bot.py` at startup. Each one is supervised and restarts on error.

| Loop | Purpose |
| --- | --- |
| `_proactive_file_alert_loop` | Watch Slack file uploads, DM user when summarizable |
| `_dropbox_watch_loop` | Poll Dropbox folder, alert on new files |
| `_digest_loop` | Hourly digest scheduler |
| `_health_loop` | Self-ping `/health` for liveness telemetry |

## Storage

| Path on host | Purpose | Persisted across container restart? |
| --- | --- | --- |
| `data/chromadb/` | Vector store (gitignored) | yes |
| `data/dream/` | Long-term memory + procedures | yes |
| `data/backups/` | Rotating tarballs (gitignored) | yes |
| `data/audit/` | Append-only audit log (gitignored) | yes |
| `secrets/*.env` | Slack/LLM/integration creds (gitignored) | yes |

## What this doc does **not** cover

- LLM routing details → [`LLM-ROUTING.md`](LLM-ROUTING.md)
- Skill authoring → [`AGENT-EXTENSION-GUIDE.md`](AGENT-EXTENSION-GUIDE.md)
- Deployment commands → [`DEPLOYMENT.md`](DEPLOYMENT.md)
- Troubleshooting → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- Operations runbook → [`OPERATIONS-RUNBOOK.md`](OPERATIONS-RUNBOOK.md)
