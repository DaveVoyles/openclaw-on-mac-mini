# OpenClaw 🤖

Autonomous AI agent for home automation and system management, accessible via Discord.

Runs on a **Mac Mini M4 Pro** managing a 20+ container Docker infrastructure alongside a Synology NAS.

| | |
|---|---|
| **Host** | Mac Mini M4 Pro (192.168.1.93) |
| **Port** | 8765 (health endpoint) |
| **Interface** | Discord slash commands |
| **LLM** | Google Gemini 2.0 Flash (paid tier) |
| **Status** | Phase 3 — LLM Integration |

## Features

**Phase 1 — Foundation** ✅
- Discord bot with `/ping`, `/about`, `/whoami`, `/help`
- Health check HTTP endpoint (`/health`)
- Audit logging (JSONL)
- Security-hardened Docker container

**Phase 2 — Core Skills** ✅
- `/containers` — list all running Docker containers
- `/status <service>` — detailed container status
- `/logs <service>` — tail recent container logs
- `/system` — CPU, memory, disk usage via Glances
- `/restart <service>` — restart a container (approval required)

**Phase 3 — LLM Integration** ✅
- `/ask <question>` — AI-powered natural language queries via Gemini 2.0 Flash
- Function calling — LLM autonomously invokes skills (container status, logs, system stats)
- Conversation memory — multi-turn context per user/channel (30 min TTL)
- `/clear` — reset conversation history
- Rate limiting — 60 RPM / 500 RPH with graceful degradation

**Planned**
- Phase 4: Approval workflows with Discord buttons
- Phase 5: Media automation (Sonarr/Radarr/Plex queries)
- Phase 6: Remote access via Tailscale + Traefik
- Phase 7: Production hardening

---

## Quick Start

### 1. Create Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it "OpenClaw"
3. Navigate to **Bot** tab → click **Reset Token** → copy the token
4. Enable these Privileged Gateway Intents:
   - **Message Content Intent**
5. Navigate to **OAuth2** → **URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: Send Messages, Embed Links, Use Slash Commands
6. Open the generated URL in your browser to invite the bot to your server

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `DISCORD_BOT_TOKEN` — from step 1
- `DISCORD_GUILD_ID` — right-click your Discord server → Copy Server ID
- `ALLOWED_USER_IDS` — right-click your profile → Copy User ID

### 3. Deploy

```bash
cd ~/openclaw
docker compose up -d --build
```

### 4. Verify

```bash
# Check container health
docker ps --filter name=openclaw

# Check health endpoint
curl http://localhost:8765/health

# Check logs
docker logs openclaw --tail 20
```

Then type `/ping` in your Discord server.

---

## Commands

| Command | Description | Phase |
|---------|-------------|-------|
| `/ping` | Check if OpenClaw is alive (latency + uptime) | 1 |
| `/about` | Show version and system info | 1 |
| `/whoami` | Show your identity and permissions | 1 |
| `/help` | List available commands | 1 |
| `/containers` | List all running Docker containers | 2 |
| `/status <service>` | Detailed status for a specific container | 2 |
| `/logs <service>` | Tail last 30 lines of container logs | 2 |
| `/system` | System resource usage (CPU, RAM, disk) | 2 |
| `/restart <service>` | Restart a container (requires approval) | 2 |
| `/ask <question>` | AI-powered natural language query (Gemini) | 3 |
| `/clear` | Clear your conversation history | 3 |

## Architecture

```
~/openclaw/
├── bot.py                 # Main Discord bot (commands + routing)
├── skills.py              # Docker & system monitoring skills
├── llm.py                 # Gemini LLM integration + function calling
├── memory.py              # Per-user conversation memory
├── docker-compose.yml     # Container orchestration
├── Dockerfile             # Image build
├── .env                   # Secrets (not committed)
├── .env.example           # Template
├── config/
│   ├── config.yaml        # Main configuration
│   ├── permissions.yaml   # Risk levels and access control
│   ├── skills/
│   │   └── enabled.yaml   # Which skills are active
│   └── prompts/
│       └── system.txt     # LLM system prompt (Phase 3)
├── data/
│   ├── logs/              # Application logs
│   ├── memory/            # Agent memory (Phase 3)
│   └── audit/             # Audit trail (JSONL)
├── docs/
│   └── IMPLEMENTATION-PLAN.md  # Full 7-phase plan
└── scripts/
    └── health-check.sh    # Health monitoring
```

## Security

- Container runs with `read_only: true`, `cap_drop: ALL`, `no-new-privileges`
- Only whitelisted Discord user IDs can execute commands
- All actions logged to `data/audit/YYYY-MM-DD.jsonl`
- Resource limits: 2 GB RAM, 2 CPU cores
- Health endpoint on port 8765

## Roadmap

- [x] **Phase 1**: Foundation — Discord bot with basic commands
- [x] **Phase 2**: Core Skills — Docker management, system monitoring
- [x] **Phase 3**: LLM Integration — Gemini-powered AI responses + function calling
- [ ] **Phase 4**: Security & Approvals — Approval workflows, audit logging
- [ ] **Phase 5**: Advanced Skills — Media automation, scheduled tasks
- [ ] **Phase 6**: Remote Access — Traefik routing, Uptime Kuma
- [ ] **Phase 7**: Polish — Documentation, testing, production hardening

See [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) for the detailed plan.

## Maintenance

```bash
# Restart
cd ~/openclaw && docker compose restart

# View logs
docker logs openclaw -f --tail 50

# Rebuild after code changes
docker compose up -d --build

# Stop
docker compose down
```

## Related Documentation

- [Implementation Plan](docs/IMPLEMENTATION-PLAN.md) — Full 7-phase roadmap
- [Docker Stack](https://github.com/DaveVoyles/docker-on-mac-mini) — The infrastructure OpenClaw manages
