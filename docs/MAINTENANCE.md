# OpenClaw — Maintenance & Operations Guide
<!-- Updated: 2026-04-18 -->


This document outlines common maintenance tasks, troubleshooting steps, and operational procedures for the OpenClaw system on the Mac Mini. For operator-first incident handling, also see [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) and [NETWORK-TOPOLOGY.md](NETWORK-TOPOLOGY.md).

For first-time setup, environment requirements, and deployment verification, start with the [Deployment & Environment Guide](DEPLOYMENT.md).

## 🔄 System Startup & Reliability

### Docker Container Restart Policy

To ensure that OpenClaw and its associated services are always available after a system reboot or Docker daemon restart, we use the `always` restart policy in `docker-compose.yml`.

- **Current Policy**: `restart: always`
- **Location**: [docker-compose.yml](../docker-compose.yml)

### Delayed Proton VPN Startup (macOS)

On the Mac Mini host, Proton VPN may fail to connect if it launches before an active internet connection is established. To mitigate this, a custom delay mechanism is used.

#### Components:

1.  **Delay Script**: [`scripts/delay_proton_launch.sh`](../scripts/delay_proton_launch.sh)
    - Waits 30 seconds after login.
    - Pings `8.8.8.8` (Google DNS) to verify connectivity.
    - Launches the Proton VPN application only after a successful ping.
2.  **LaunchAgent**: [`scripts/com.user.delayprotonlaunch.plist`](../scripts/com.user.delayprotonlaunch.plist)
    - Installed at `~/Library/LaunchAgents/com.user.delayprotonlaunch.plist`.
    - Triggers the delay script automatically on user login.

#### Manual Management:

If you need to reload or stop this delay mechanism:

```bash
# Unload/Disable
launchctl unload ~/Library/LaunchAgents/com.user.delayprotonlaunch.plist

# Load/Enable
launchctl load ~/Library/LaunchAgents/com.user.delayprotonlaunch.plist
```

## 🌐 Mac Mini Network Configuration

The Mac Mini **must** use its built-in Ethernet port (en0, 192.168.1.93) for all network traffic. WiFi and USB ethernet adapters have been disabled to prevent connection instability with SSH, Plex, and Docker services.

### Why This Matters

Having multiple interfaces on the same subnet (e.g. WiFi at 192.168.1.173 and Ethernet at 192.168.1.93) causes macOS to flap between them. When WiFi momentarily drops, all TCP connections (SSH, Plex streams, Docker container networking) break.

### Current Configuration (as of 2026-03-24)

| Service                | Device | Status                   | IP           |
| ---------------------- | ------ | ------------------------ | ------------ |
| Ethernet               | en0    | **Enabled, #1 priority** | 192.168.1.93 |
| Wi-Fi                  | en1    | Disabled                 | —            |
| USB 10/100/1000 LAN    | en8    | Disabled                 | —            |
| USB 10/100/1G/2.5G LAN | en10   | Disabled                 | —            |
| Subosen DL6350         | en9    | Disabled                 | —            |
| ProtonVPN              | utun\* | Enabled                  | 10.2.0.2     |
| Tailscale              | utun\* | Enabled                  | varies       |

### Verify Network Is Correct

```bash
# Should show only "utun5 en0" — no en1 (WiFi)
scutil --nwi | grep "Network interfaces"

# Ethernet should be #1, Wi-Fi should have (*) asterisk = disabled
networksetup -listnetworkserviceorder

# en1 should say "status: inactive"
ifconfig en1 | grep status
```

### If WiFi Gets Re-enabled After an OS Update

```bash
sudo networksetup -setnetworkserviceenabled "Wi-Fi" off
sudo networksetup -setairportpower en1 off
sudo ifconfig en1 down
```

### If Network Service Order Gets Reset

```bash
sudo networksetup -ordernetworkservices \
  "Ethernet" \
  "Thunderbolt Bridge" \
  "Wi-Fi" \
  "USB 10/100/1G/2.5G LAN" \
  "USB 10/100/1000 LAN" \
  "Subosen DL6350" \
  "ProtonVPN" \
  "Tailscale"
```

## 🛠️ Common Commands

### Manage OpenClaw Services

```bash
# Restart the bot
docker compose restart openclaw

# View logs
docker compose logs -f openclaw

# Rebuild and start
docker compose up -d --build openclaw
```

### Health Monitoring

OpenClaw exposes a health check endpoint on port `8765`.

```bash
curl http://localhost:8765/health
```

## 📁 Data Persistence

All persistent data is stored in the `data/` directory and volume-mounted into the container:

- `data/logs/`: Application logs.
- `data/memory/`: LLM conversation context and memory.
- `data/memory/spending.json`: Gemini API cost tracking (input/output tokens, daily totals).
- `data/memory/ontology/`: Structured graph memory (`graph.jsonl` + `schema.yaml`).
- `data/audit/`: Audit logs for security-sensitive actions (JSONL, one file per day).
- `data/tasks.json`: Mission Control task data.
- `data/vault/`: Obsidian vault — research reports, bookmarks, notes, analytics as `.md` files with YAML frontmatter.
  - Subfolders: `Research/`, `Bookmarks/`, `Notes/`, `Analytics/`

## 🐳 Docker Volume Mounts

| Host Path                   | Container Path         | Mode | Purpose                                       |
| --------------------------- | ---------------------- | ---- | --------------------------------------------- |
| `./config`                  | `/config`              | `ro` | YAML config, tools, prompts                   |
| `./data/logs`               | `/logs`                | `rw` | Application logs                              |
| `./data/memory`             | `/memory`              | `rw` | Conversation context, QMD, ontology, spending |
| `./data/audit`              | `/audit`               | `rw` | Security audit trail (JSONL)                  |
| `./data/tasks.json`         | `/app/data/tasks.json` | `rw` | Mission Control tasks                         |
| `./data/vault`              | `/vault`               | `rw` | Obsidian vault (research, bookmarks, notes)   |
| `/tmp/openclaw`             | `/tmp`                 | `rw` | Temp files (required by `read_only: true`)    |
| `~/.docker/run/docker.sock` | `/var/run/docker.sock` | `rw` | Docker API access                             |

## 🔧 Automated 4:00 AM Maintenance

OpenClaw runs an automated maintenance cycle at **4:00 AM daily** via `maintenance_skills.py`:

1. **Skill update** — `git pull --rebase --autostash` in `/app` to fetch latest skill code
2. **Session restart** — Clears LLM gateway and HTTP sessions to prevent memory leaks
3. **NAS backup** — Full backup of all persistent data to Synology NAS via SSH

### What gets backed up

| Source | Destination on NAS | Method | Contents |
| ------ | ------------------ | ------ | -------- |
| `/config/` | `{BACKUP_PATH}/{date}/config/` | rsync --delete | YAML config, prompts, permissions, tools |
| `/app/data/tasks.json` | `{BACKUP_PATH}/{date}/tasks.json` | scp | Scheduler task definitions |
| `/app/.env` | `{BACKUP_PATH}/{date}/dot-env` (chmod 600) | scp | API keys, secrets (**critical**) |
| `/memory/` | `{BACKUP_PATH}/{date}/memory/` | rsync | QMD knowledge base, conversation threads, spending tracker |
| `/vault/` | `{BACKUP_PATH}/{date}/vault/` | rsync | Obsidian research reports, bookmarks, notes |
| `/audit/` | `{BACKUP_PATH}/{date}/audit/` | rsync | Command audit trail (JSONL) |

**NAS destination:** `/volume1/docker/openclaw/backups/{YYYY-MM-DD}/`

Each source is backed up independently — a failure in one doesn't block the others. The `.env` file is stored as `dot-env` with `chmod 600` to prevent accidental exposure.

### What's NOT backed up (already safe)

| Data | Protected by |
| ---- | ------------ |
| Source code (`src/`, `skills/`, `templates/`) | GitHub repo |
| `config/` (YAML, prompts) | GitHub repo + NAS backup |
| `docker-compose.yml`, `Dockerfile` | GitHub repo |
| `.env.example` (template) | GitHub repo |

### Connection details

Uses **SSH key-based authentication** (no password). The connection uses `BatchMode=yes` which prevents hanging if keys aren't configured — it will fail gracefully with a clear error instead.

### Environment variables

| Env Var           | Default                            | Purpose                     |
| ----------------- | ---------------------------------- | --------------------------- |
| `NAS_HOST`        | `192.168.1.8`                      | Synology NAS IP address     |
| `NAS_SSH_PORT`    | `24`                               | SSH port on NAS             |
| `NAS_SSH_USER`    | `dave`                             | SSH username for backup     |
| `NAS_BACKUP_PATH` | `/volume1/docker/openclaw/backups` | Remote backup directory     |
| `CONFIG_DIR`      | `/config`                          | Config directory to back up |

### Manual backup

For on-demand local backups (includes `.env`):

```bash
./scripts/backup_restore.sh backup    # Creates timestamped tar.gz
./scripts/backup_restore.sh list      # List available backups
./scripts/backup_restore.sh restore <file>  # Restore from backup
```

### Disabling maintenance

Remove or comment out the 4 AM scheduler entry. The schedule is registered in `bot.py` during startup.

## 🏷️ Channel-Role Architecture

OpenClaw supports **per-channel prompt overrides** that adjust the bot's personality and behavior based on which Discord channel the message arrives in.

| Channel        | Purpose           | Prompt behavior                                                       |
| -------------- | ----------------- | --------------------------------------------------------------------- |
| `#research`    | Focused research  | Prioritize accuracy, cite sources, structured reports                 |
| `#analytics`   | Data analysis     | Metrics-driven, tables, ranked lists, concise                         |
| `#bookmarks`   | Knowledge capture | Brief confirmations, organize saved content                           |
| `#real-estate` | Property research | Listings, market analysis, comps, tax records, school ratings, tables |

**Configuration:** Set channel IDs in `.env` and customize prompts in `config/config.yaml` under `channels.roles`.

```bash
# .env
DISCORD_CHANNEL_RESEARCH_ID=1234567890
DISCORD_CHANNEL_ANALYTICS_ID=1234567891
DISCORD_CHANNEL_BOOKMARKS_ID=1234567892
DISCORD_CHANNEL_REAL_ESTATE_ID=1486358540246319135
```

## 🌐 Dashboard & API Endpoints

OpenClaw serves a lightweight web dashboard alongside its health endpoint on port `8765`:

| Endpoint         | Purpose                                                             |
| ---------------- | ------------------------------------------------------------------- |
| `/health`        | JSON health check (uptime, version)                                 |
| `/metrics`       | Prometheus metrics (`openclaw_up`, `openclaw_uptime_seconds`, etc.) |
| `/dashboard`     | HTML dashboard with system status, command reference, and guide     |
| `/api/dashboard` | JSON API for dashboard data                                         |

## 🧪 CI/CD Pipeline

The project uses GitHub Actions for continuous integration:

- **Workflow**: `.github/workflows/tests.yml`
- **Lint**: `ruff check src/ tests/` — runs before tests
- **Tests**: `pytest tests/ --cov=src --cov-report=xml --timeout=30`
- **Coverage**: XML report uploaded as artifact
- **Plugins**: `pytest-cov` (coverage), `pytest-timeout` (30s per-test timeout), `pytest-asyncio`
