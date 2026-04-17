# OpenClaw Deployment & Environment Guide

Use this guide when you need to go from a fresh clone and `.env` file to a healthy local or production deployment.

---

## What this guide covers

1. [Day-to-day code deploy workflow](#day-to-day-code-deploy-workflow)
2. [Deployment flow](#deployment-flow)
3. [Environment configuration](#environment-configuration)
4. [Local vs production](#local-vs-production)
5. [Deploying locally](#deploying-locally)
6. [Deploying in production](#deploying-in-production)
7. [Verification checklist](#verification-checklist)
8. [Rollback basics](#rollback-basics)

---

## Day-to-day code deploy workflow

> **`git push` alone is never enough. Always follow with a deploy step.**

This repo has two deploy targets:

| Target | Machine | What lives there |
|---|---|---|
| Server | Mac Mini (`192.168.1.93`) | Docker container `openclaw`, `src/` volume-mounted |
| CLI | MacBook (`macbook` SSH alias) | `~/.local/share/openclaw-cli/` — standalone Python install |

### Why the container must be restarted after server-side changes

The container mounts `./src:/app/src:ro` so the files are visible, but **Python caches module imports at startup**. Changed `.py` files on disk are **invisible to the running process** until the container restarts. This is the most common cause of "I pushed a fix but it didn't work" — the container was still running old code.

### Commands (run from Mac Mini)

```bash
make ship          # safe default — pull + restart server + update MacBook CLI
make ship-server   # server only: git pull, write git SHA to src/_git_sha.txt, docker restart openclaw
make ship-cli      # CLI only: SCP openclaw_cli*.py to MacBook via install script
make verify-deploy # confirm: shows CLI build label + /health JSON with git_sha field
```

### Choosing the right target

| Changed file(s) | Command |
|---|---|
| `src/openclaw_cli*.py`, `src/subprocess_utils.py` | `make ship-cli` |
| Any other `src/` file (`model_router.py`, `discord_web.py`, `bot.py`, `llm/`, etc.) | `make ship-server` |
| Both, or unsure | `make ship` |

### Verify the deploy landed

```bash
make verify-deploy
# Shows: CLI build label + /health JSON including "git_sha"
# Cross-check: git rev-parse --short HEAD  (must match git_sha in /health)
```

The `/health` endpoint at `http://192.168.1.93:8765/health` includes `"git_sha"` so you can always confirm which commit the running server has loaded.

---

## Deployment flow

Use this order for first-time setup:

1. Clone the repository
2. Copy `.env.example` to `.env`
3. Fill in the **required** environment variables
4. Review optional integrations and disable anything you are not using yet
5. Start with `docker compose up -d --build` for local development, or `docker compose -f docker-compose.prod.yml up -d --build` for production
6. Verify `/health`, `/dashboard`, and Discord command responsiveness

---

## Environment configuration

### Required for a minimal healthy deployment

These values are required to get the bot online and responding in Discord:

| Variable | Why it matters |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Authenticates the Discord bot |
| `DISCORD_GUILD_ID` | Targets your Discord server for command sync |
| `ALLOWED_USER_IDS` | Allows privileged commands for trusted operators |
| `GOOGLE_API_KEY` | Enables the primary Gemini-backed `/ask` workflow |

### Strongly recommended before exposing the dashboard or webhooks

| Variable | Why it matters |
| --- | --- |
| `DASHBOARD_API_TOKEN` | Protects mutating dashboard/API endpoints |
| `DASHBOARD_API_AUTH_REQUIRED` | Keeps API actions auth-gated |
| `WEBHOOK_SECRET` | Secures webhook-triggered actions |
| `WEBHOOK_REQUIRE_AUTH` | Enforces webhook bearer auth outside local-only testing |

### Optional, based on features you enable

| Category | Variables |
| --- | --- |
| Local LLM | `LOCAL_LLM_ENABLED`, `OLLAMA_URL`, `OLLAMA_MODEL`, `DEFAULT_MODEL_PREFERENCE`, `ROUTING_PROFILE` |
| Notifications / channel routing | `ALERT_CHANNEL_ID`, `DISCORD_CHANNEL_RESEARCH_ID`, `DISCORD_CHANNEL_ANALYTICS_ID`, `DISCORD_CHANNEL_BOOKMARKS_ID`, `DISCORD_CHANNEL_REAL_ESTATE_ID` |
| Backups / NAS | `NAS_HOST`, `NAS_SSH_PORT`, `NAS_SSH_USER`, `NAS_BACKUP_PATH`, `SSH_KEY_PATH`, `SSH_KNOWN_HOSTS` |
| Service integrations | Media, search, productivity, and infrastructure API keys from `.env.example` |

### Practical setup rules

- Start with only the required variables and add integrations in small batches.
- Leave unused optional values blank rather than inventing placeholders.
- Keep `.env` local and out of version control.
- If you change `.env`, recreate or restart the container so the new values are loaded.

See [API setup](API_SETUP.md) for per-service registration steps and [Services](SERVICES.md) for the full integration catalog.

---

## Local vs production

Both compose files run the same application, but they optimize for different goals.

| Area | Local (`docker-compose.yml`) | Production (`docker-compose.prod.yml`) |
| --- | --- | --- |
| Startup command | `docker compose up -d --build` | `docker compose -f docker-compose.prod.yml up -d --build` |
| Restart behavior | `restart: always` | `restart: unless-stopped` plus restart policy |
| Source code | Live-mounts `./src` into `/app/src` for fast iteration | Uses the built runtime image |
| Storage | Direct bind mounts under `./data` | Named volumes backed by `${DATA_DIR:-./data}` |
| Runtime flags | No explicit production flag | `ENVIRONMENT=production`, `DEBUG=false` |
| Temp/cache storage | Host paths `/tmp/openclaw` and `/tmp/openclaw-cache` | Named volumes `openclaw-tmp` and `openclaw-cache` |
| Networking | Default compose network | Dedicated `openclaw-net` bridge network |
| Logging | Smaller JSON log rotation | Larger compressed log rotation |

### Choose local when

- You are iterating on code or config
- You want live source mounts
- You are validating setup on a single machine first

### Choose production when

- You want a hardened runtime container
- You want explicit production flags and isolated named volumes
- You are deploying a durable long-running bot instance

---

## Deploying locally

### 1. Prepare the environment

```bash
cp .env.example .env
```

Edit `.env` and set at least the required variables listed above.

### 2. Build and start

```bash
docker compose up -d --build
```

### 3. Check service state

```bash
docker compose ps
docker compose logs openclaw --tail 100
curl -sf http://localhost:8765/health
```

### 4. Confirm Discord behavior

After the container is healthy:

- wait for command sync on startup
- run `/ping`
- run `/about`
- run a simple `/ask` prompt

---

## Deploying in production

### 1. Prepare persistent directories and secrets

- Keep `config/`, `data/`, and `.env` on durable storage
- Set the security-related tokens in `.env`
- If NAS backups are enabled, make sure the SSH key mounts are valid

### 2. Build and start with the production compose file

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

### 3. Inspect health and logs

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs openclaw --tail 100
curl -sf http://localhost:8765/health
```

### 4. Validate production-specific expectations

- the container should report healthy
- `/dashboard` and `/metrics` should respond
- `ENVIRONMENT=production` should be present in the container environment
- Discord commands should sync and respond normally

Example:

```bash
docker compose -f docker-compose.prod.yml exec openclaw env | grep '^ENVIRONMENT='
```

---

## Verification checklist

Use this checklist after any first deploy or update:

### Container and HTTP checks

```bash
docker compose ps
curl -sf http://localhost:8765/health
curl -sf http://localhost:8765/metrics | head
curl -sf http://localhost:8765/dashboard | head
```

Expected result:

- the `openclaw` service is `Up`
- `/health` returns success JSON
- `/metrics` returns Prometheus text
- `/dashboard` returns HTML

### Application checks

- Startup logs do not show missing env vars, import failures, or auth errors
- Slash commands sync successfully
- A trusted operator can run `/ping` and `/ask`

### Optional integration checks

- If Ollama is enabled, verify `OLLAMA_URL` is reachable from the container
- If backups are enabled, verify SSH key mounts and NAS connectivity
- If dashboard API auth is enabled, test a protected endpoint with the bearer token you configured

For deeper operational procedures, see [Maintenance](MAINTENANCE.md). For failure cases, see [Troubleshooting](TROUBLESHOOTING.md).

---

## Rollback basics

When a deployment is unhealthy, prefer a simple rollback over live debugging in place.

### Before updating

- Keep your previous git SHA or image tag recorded
- Preserve `config/`, `data/`, and `.env`
- If the change is risky, run `./scripts/backup_restore.sh backup` first

### Fast rollback approach

1. Stop the current deployment
2. Restore the last known-good code or image tag
3. Start the stack again
4. Re-run the verification checklist

Example using git:

```bash
git checkout <last-known-good-sha>
docker compose up -d --build
```

Example using the production compose file:

```bash
git checkout <last-known-good-sha>
docker compose -f docker-compose.prod.yml up -d --build
```

### When rollback is preferable

- container fails health checks after an update
- Discord auth or command sync breaks
- required env/config changes were missed
- a new image starts but core `/ask` or dashboard behavior regresses

If rollback does not restore service, move to [Troubleshooting](TROUBLESHOOTING.md) and inspect logs before making additional changes.
