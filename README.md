# OpenClaw

Autonomous AI agent for home automation and system management, accessible via Discord.

**Host**: Mac Mini M4 Pro (192.168.1.93)
**Port**: 8765
**Status**: Phase 1 — Foundation

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

| Command | Description |
|---------|-------------|
| `/ping` | Check if OpenClaw is alive (latency + uptime) |
| `/about` | Show version and system info |
| `/whoami` | Show your Discord identity and permissions |
| `/help` | List available commands |

## Architecture

```
~/openclaw/
├── bot.py                 # Main Discord bot
├── docker-compose.yml     # Container orchestration
├── Dockerfile             # Image build
├── .env                   # Secrets (not committed)
├── .env.example           # Template
├── config/
│   ├── config.yaml        # Main configuration
│   ├── permissions.yaml   # Risk levels and access control
│   └── prompts/
│       └── system.txt     # LLM system prompt (Phase 3)
├── data/
│   ├── logs/              # Application logs
│   ├── memory/            # Agent memory (Phase 3)
│   └── audit/             # Audit trail (JSONL)
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
- [ ] **Phase 2**: Core Skills — Docker management, system monitoring
- [ ] **Phase 3**: LLM Integration — Gemini-powered AI responses
- [ ] **Phase 4**: Security & Approvals — Approval workflows, audit logging
- [ ] **Phase 5**: Advanced Skills — Media automation, scheduled tasks
- [ ] **Phase 6**: Remote Access — Traefik routing, Uptime Kuma
- [ ] **Phase 7**: Polish — Documentation, testing, production hardening

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

- [Implementation Plan](~/docker-stack/docs/OPENCLAW-IMPLEMENTATION-PLAN.md)
- [Docker Stack Services](~/docker-stack/docs/SERVICES.md)
- [Port Reference](~/docker-stack/PORT-REFERENCE.md)
