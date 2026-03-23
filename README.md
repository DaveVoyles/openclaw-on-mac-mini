# OpenClaw 🤖

Autonomous AI agent for home automation and system management, accessible via Discord.

Runs on a **Mac Mini M4 Pro** managing a 20+ container Docker infrastructure alongside a Synology NAS.

| | |
|---|---|
| **Host** | Mac Mini M4 Pro (192.168.1.93) |
| **Port** | 8765 (health endpoint) |
| **Interface** | Discord slash commands |
| **LLM** | Google Gemini 2.0 Flash (paid tier) |
| **Status** | Phase 4 — Security & Approvals |

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

**Phase 4 — Security & Approvals** ✅
- `/restart` now requires button-click approval before executing
- Discord button UI — ✅ Approve / ❌ Deny with 5-minute timeout
- `/pending` — view pending approval requests
- `/auditlog [lines]` — view recent audit trail entries
- `/estop` — emergency stop to halt all write actions instantly
- `/estop resume` — resume normal operations after emergency stop
- Risk classification system (LOW/MEDIUM/HIGH/CRITICAL)
- Emergency stop blocks `/ask` and `/restart` when active

**Planned**
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
| `/pending` | List pending approval requests | 4 |
| `/auditlog [lines]` | View recent audit log entries | 4 |
| `/estop` | Emergency stop — halt all write actions | 4 |
| `/estop resume` | Resume bot after emergency stop | 4 |

## Architecture

```
~/openclaw/
├── bot.py                 # Main Discord bot (commands + routing)
├── skills.py              # Docker & system monitoring skills
├── llm.py                 # Gemini LLM integration + function calling
├── memory.py              # Per-user conversation memory
├── approvals.py           # Approval workflow engine + Discord button UI
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
- **Approval workflow**: `/restart` requires button-click confirmation before executing
- **Emergency stop**: `/estop` immediately halts all write actions and LLM queries
- **Risk classification**: Commands categorized LOW→CRITICAL with escalating controls
- **Policy enforcement**: `permissions.yaml` blocks restarts of critical infrastructure (traefik, socket-proxy, homepage, watchtower)

## Roadmap

- [x] **Phase 1**: Foundation — Discord bot with basic commands
- [x] **Phase 2**: Core Skills — Docker management, system monitoring
- [x] **Phase 3**: LLM Integration — Gemini-powered AI responses + function calling
- [x] **Phase 4**: Security & Approvals — Button-based approval UI, emergency stop, audit viewer
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

---

## Manual Setup Checklist

Things you need to do by hand before OpenClaw is fully operational. Complete these whenever you're ready.

- [ ] **Create Discord Bot** — [discord.com/developers/applications](https://discord.com/developers/applications)
  - New Application → name it "OpenClaw"
  - Bot tab → Reset Token → copy token
  - Enable **Message Content Intent**
  - OAuth2 → URL Generator: scopes `bot` + `applications.commands`, permissions: Send Messages, Embed Links, Use Slash Commands
  - Open generated URL to invite bot to your server
- [ ] **Fill in `~/openclaw/.env`** with:
  - `DISCORD_BOT_TOKEN` — from the bot you just created
  - `DISCORD_GUILD_ID` — right-click your Discord server → Copy Server ID
  - `ALLOWED_USER_IDS` — right-click your Discord profile → Copy User ID
  - `GOOGLE_API_KEY` — from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (paid Gemini tier)
- [ ] **First deploy**: `cd ~/openclaw && docker compose up -d --build`
- [ ] **Verify**: type `/ping` in Discord, check `curl http://localhost:8765/health`
- [ ] **Test `/ask`**: try "how's sonarr doing?" to confirm Gemini + function calling works
