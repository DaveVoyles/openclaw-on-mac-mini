# OpenClaw Troubleshooting Guide

Quick-reference for diagnosing and fixing common issues.

---

## Table of Contents

1. [Bot Won't Start](#bot-wont-start)
2. [Bot is Online but Not Responding](#bot-online-not-responding)
3. [Gemini API Errors](#gemini-api-errors)
4. [Ollama / Local LLM Issues](#ollama-local-llm-issues)
5. [Docker Socket Errors](#docker-socket-errors)
6. [Slash Commands Not Appearing](#slash-commands-not-appearing)
7. [Rate Limiting](#rate-limiting)
8. [Scheduled Tasks Not Running](#scheduled-tasks-not-running)
9. [Email Skills Failing](#email-skills-failing)
10. [Google Calendar Issues](#google-calendar-issues)
11. [NAS Connection Problems](#nas-connection-problems)
12. [Web Search Not Working](#web-search-not-working)
13. [Memory / QMD Issues](#memory--qmd-issues)
14. [Health Check Failing](#health-check-failing)
15. [Backup & Restore](#backup--restore)

---

## Bot Won't Start

### Symptom: Container exits immediately

```bash
# Check exit code and logs
docker compose logs openclaw --tail 50
```

**Common causes:**

| Cause | Fix |
|-------|-----|
| Missing `.env` file | Copy `.env.example` to `.env` and fill in required values |
| Invalid `DISCORD_TOKEN` | Regenerate token at https://discord.com/developers/applications |
| Missing `GOOGLE_API_KEY` | Get one at https://aistudio.google.com/app/apikey |
| Python dependency error | Rebuild: `docker compose build --no-cache` |
| Port 8765 already in use | Check with `lsof -i :8765` and kill the conflicting process |

### Symptom: "Privileged intent" error

The bot needs the **Message Content** intent enabled:
1. Go to https://discord.com/developers/applications
2. Select your bot → "Bot" tab
3. Enable **Message Content Intent**

---

## Bot Online Not Responding

### Symptom: Bot shows online but ignores commands

1. **Check permissions**: Verify your Discord user ID is in `config/permissions.yaml`
2. **Check guild ID**: Ensure `GUILD_ID` in `.env` matches your Discord server
3. **Check logs for errors**:
   ```bash
   docker compose logs openclaw --tail 100 | grep -i "error\|exception"
   ```
4. **Emergency stop active?** Check if someone triggered `/estop`. Reset with `/estop` again.

### Symptom: Responds to some commands but not others

- Some commands require specific roles configured in `config/permissions.yaml`
- Advanced skills may be missing API keys (check logs for `KeyError` or `not configured`)

---

## Gemini API Errors

### "429 Resource Exhausted"

You've hit the Gemini API rate limit.

```bash
# Check current rate usage via Discord
/spending
```

**Fixes:**
- Wait 60 seconds (per-minute limits reset)
- Reduce `LLM_RPM_LIMIT` in `.env` to stay under your tier
- Enable Ollama fallback: set `LOCAL_LLM_ENABLED=true` in `.env`

### "400 Invalid API Key"

```bash
# Test your key directly
curl -s "https://generativelanguage.googleapis.com/v1/models?key=$GOOGLE_API_KEY" | head -5
```

If it fails, regenerate at https://aistudio.google.com/app/apikey.

### "500 Internal Server Error"

Gemini service outage. Check https://status.cloud.google.com/ and retry later.

---

## Ollama / Local LLM Issues

### Symptom: "Gemma/Ollama not reachable"

1. **Is Ollama running?**
   ```bash
   curl http://localhost:11434/api/tags
   ```
2. **Docker networking**: The bot uses `host.docker.internal:11434`. Verify:
   ```bash
   docker exec openclaw curl -s http://host.docker.internal:11434/api/tags
   ```
3. **Model not pulled?**
   ```bash
   ollama pull gemma3:12b
   ```

### Symptom: Ollama responds but quality is poor

The bot auto-validates Ollama responses and falls back to Gemini when quality is low. This is expected behavior. To force Gemini for everything:

```env
LOCAL_LLM_ENABLED=false
```

---

## Docker Socket Errors

### "Permission denied" on Docker socket

The container needs read-write access to the Docker socket.

```bash
# Check socket permissions
ls -la /Users/davevoyles/.docker/run/docker.sock

# macOS: Docker Desktop socket is usually accessible.
# Linux: Add the user to the docker group or adjust permissions.
```

### "Cannot connect to Docker daemon"

- Docker Desktop must be running
- Verify the socket path in `docker-compose.yml` matches your system

---

## Slash Commands Not Appearing

After deploying, commands may take up to 1 hour to sync globally.

**Force sync:**
1. Restart the bot: `docker compose restart openclaw`
2. Commands sync on startup — check logs for "Synced N commands"

**Still missing?**
- Verify bot has `applications.commands` OAuth2 scope
- Re-invite the bot with the correct permissions URL

---

## Rate Limiting

### Check current status
```
/spending
```

### Tune limits in `.env`
```env
LLM_RPM_LIMIT=60        # Calls per minute
LLM_RPH_LIMIT=500       # Calls per hour
```

### Enable Ollama to reduce Gemini usage
```env
LOCAL_LLM_ENABLED=true
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=gemma3:12b
```

---

## Scheduled Tasks Not Running

1. **Check task list**: Use `/tasks` or check `data/tasks.json`
2. **Check scheduler logs**:
   ```bash
   docker compose logs openclaw | grep -i "scheduler\|sched-"
   ```
3. **Bot restart clears in-memory schedules**: Tasks defined via `/schedule` are persisted, but verify the file exists at the expected path

---

## Email Skills Failing

### Gmail

| Error | Fix |
|-------|-----|
| "Authentication failed" | Use an **App Password**, not your regular password. Generate at https://myaccount.google.com/apppasswords |
| "Less secure apps" | App Passwords bypass this — no need to enable less secure apps |
| "IMAP not enabled" | Enable IMAP in Gmail Settings → Forwarding and POP/IMAP |

### Required `.env` variables
```env
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

---

## Google Calendar Issues

### "OAuth token expired"

Re-run the OAuth setup:
```bash
python scripts/google_oauth_setup.py
```

### "Calendar API not enabled"

1. Go to https://console.cloud.google.com/apis/library
2. Enable "Google Calendar API"

### Required `.env` variables
```env
GOOGLE_CALENDAR_CREDENTIALS_JSON=<base64-encoded credentials>
GOOGLE_CALENDAR_TOKEN_JSON=<base64-encoded token>
```

---

## NAS Connection Problems

### "Connection refused" or timeout

1. **NAS reachable?** `ping <NAS_IP>`
2. **DSM API port open?** Default is 5001 (HTTPS) or 5000 (HTTP)
3. **Firewall?** Ensure the Docker host can reach the NAS IP

### Required `.env` variables
```env
NAS_URL=https://192.168.1.x:5001
NAS_USER=admin
NAS_PASSWORD=<password>
```

---

## Web Search Not Working

### Tavily search
```env
TAVILY_API_KEY=tvly-xxxxx
```
Get a key at https://tavily.com/

### DuckDuckGo fallback
Should work without any API key. If it fails, check:
```bash
# Test the skill directly
python skills/free-web-search/scripts/web_search.py --query "test" --json
```

---

## Memory / QMD Issues

### "Memory file corrupt"

```bash
# Check the file
cat data/memory/spending.json | python -m json.tool

# If corrupt, restore from backup
./scripts/backup_restore.sh restore <latest-backup>
```

### Memory directory permissions

The container writes to `/memory` which maps to `data/memory/`:
```bash
ls -la data/memory/
```

---

## Health Check Failing

### Test manually
```bash
curl -s http://localhost:8765/health | python -m json.tool
```

### Common causes
- Port 8765 not exposed (check `docker-compose.yml`)
- Bot crashed but container is still running — check logs
- Health endpoint not starting due to import errors

---

## Backup & Restore

### Create a backup
```bash
./scripts/backup_restore.sh backup
```

### Restore from backup
```bash
./scripts/backup_restore.sh restore backups/openclaw_backup_20260324_120000.tar.gz
```

### List available backups
```bash
./scripts/backup_restore.sh list
```

### What's backed up
- `config/` — all configuration files
- `data/tasks.json` — Mission Control tasks
- `data/memory/` — QMD, spending, ontology, summaries
- `data/audit/` — audit logs
- `.env` — secrets and API keys

---

## Getting More Help

1. **Check logs first**: `docker compose logs openclaw --tail 200`
2. **Filter for errors**: `docker compose logs openclaw | grep -i "error\|traceback"`
3. **Audit trail**: Check `data/audit/` for recent action logs
4. **Services reference**: See [docs/SERVICES.md](SERVICES.md) for all API keys and services
5. **Architecture**: See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for system diagram
