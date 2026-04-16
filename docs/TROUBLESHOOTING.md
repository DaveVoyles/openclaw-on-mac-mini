# OpenClaw Troubleshooting Guide

Quick-reference for diagnosing and fixing common issues. For incident sequencing, monitoring thresholds, and escalation flow, pair this with [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) and [NETWORK-TOPOLOGY.md](NETWORK-TOPOLOGY.md).

If you are still setting up the stack, read the [Deployment & Environment Guide](DEPLOYMENT.md) first for the expected startup flow and verification steps.

---

## Table of Contents

1. [Bot Won't Start](#bot-wont-start)
2. [ModuleNotFoundError on Startup (config\_loader / skills)](#modulenotfounderror-on-startup-config_loader--skills)
3. [Bot is Online but Not Responding](#bot-online-not-responding)
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
16. [Agent Plan Stuck / Interrupted](#agent-plan-stuck--interrupted)
17. [Worker Agent Not Responding](#worker-agent-not-responding)
18. [Skill Not Appearing in /skills](#skill-not-appearing-in-skills)
19. [Ollama Timeout Errors](#ollama-timeout-errors)
20. [RSS Feed Not Updating](#rss-feed-not-updating)
21. [Calendar Auth Failing](#calendar-auth-failing)

---

## Bot Won't Start

### Symptom: Container exits immediately

```bash
# Check exit code and logs
docker compose logs openclaw --tail 50
```

**Common causes:**

| Cause                    | Fix                                                             |
| ------------------------ | --------------------------------------------------------------- |
| Missing `.env` file      | Copy `.env.example` to `.env` and fill in required values       |
| Invalid `DISCORD_TOKEN`  | Regenerate token at https://discord.com/developers/applications |
| Missing `GOOGLE_API_KEY` | Get one at https://aistudio.google.com/app/apikey               |
| Python dependency error  | Rebuild: `docker compose build --no-cache`                      |
| `No module named 'skills'` | Ensure `PYTHONPATH="/app"` is set in Dockerfile ENV; rebuild with `docker compose build --no-cache` |
| Port 8765 already in use | Check with `lsof -i :8765` and kill the conflicting process     |

### Symptom: "Privileged intent" error

The bot needs the **Message Content** intent enabled:

1. Go to https://discord.com/developers/applications
2. Select your bot → "Bot" tab
3. Enable **Message Content Intent**

---

## ModuleNotFoundError on Startup (config\_loader / skills)

### Symptom

The container exits immediately with one of:

```
ModuleNotFoundError: No module named 'config_loader'
ModuleNotFoundError: No module named 'skills'
```

### Root cause: `config_loader`

`config_loader` never existed as a module. This is a naming error. The singleton config object lives in `src/config.py` and is **always** imported as:

```python
from config import cfg
```

If you see `from config_loader import get` or similar anywhere in `src/`, replace it:

```bash
# Find occurrences
grep -rn "config_loader" src/

# Fix: replace with the correct import
from config import cfg
# Usage: cfg.some_setting  (cfg is the singleton — not a function to call)
```

### Root cause: `skills`

When Docker runs `python src/bot.py`, Python adds `/app/src/` to `sys.path` (the directory of the script). The `skills/` package lives at `/app/skills/` — **not** inside `/app/src/` — so `from skills import SKILLS` fails unless `PYTHONPATH` is set.

**Fix:** verify that `PYTHONPATH="/app"` is present in the Dockerfile `ENV` block:

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PATH="/opt/venv/bin:$PATH"
```

With `/app` in `PYTHONPATH`, Python can resolve:
- `/app/src/` (added automatically by the script entrypoint) — for `config`, `llm_tools`, etc.
- `/app/skills/` (added via `PYTHONPATH`) — for the `skills` package

After editing the Dockerfile, rebuild:

```bash
docker compose build --no-cache
docker compose up -d
```

### Quick sanity check

```bash
docker exec openclaw python -c "import skills; import config; print('OK')"
```

Both imports should succeed and print `OK`.

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

### `/ask` timeout or failure with trace ID

New `/ask` timeout/error messages include a short **Trace ID**.

- Copy the trace ID shown in Discord
- Open Dashboard → **Runs** and find the matching trace
- Use that trace when filing `/incident start` or triaging logs

This avoids sharing raw stack traces in Discord while still giving operators a
reliable correlation handle.

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
   ollama pull gemma4:e4b
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
OLLAMA_MODEL=gemma4:e4b
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

| Error                   | Fix                                                                                                       |
| ----------------------- | --------------------------------------------------------------------------------------------------------- |
| "Authentication failed" | Use an **App Password**, not your regular password. Generate at https://myaccount.google.com/apppasswords |
| "Less secure apps"      | App Passwords bypass this — no need to enable less secure apps                                            |
| "IMAP not enabled"      | Enable IMAP in Gmail Settings → Forwarding and POP/IMAP                                                   |

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

---

## Agent Plan Stuck / Interrupted

### Symptom: Plan shows "in-progress" but nothing is happening

1. **Check plan status:**
   ```
   /plans status:in-progress
   /plan-detail <plan_id>
   ```

2. **Check the plan file on disk:**
   ```bash
   ls data/plans/
   cat data/plans/<plan_id>.md
   ```
   Look for steps marked `- [ ]` (pending) or `- [~]` (in-progress). If a step is stuck in-progress, the bot likely restarted mid-execution.

3. **Resume the plan:**
   ```
   /resume-plan <plan_id>
   ```
   This picks up from the last incomplete step.

4. **Cancel a runaway plan:**
   ```
   /cancel-plan <plan_id>
   ```
   This marks the plan as interrupted and resets any in-progress steps to pending.

5. **On startup**: The bot automatically scans `data/plans/` for interrupted plans and sends a notification to `ALERT_CHANNEL_ID`. If you don't see this, check that the channel ID is set correctly in `.env`.

---

## Worker Agent Not Responding

### Symptom: `spawn_worker()` hangs or returns empty results

1. **Check Gemini rate limits:**
   ```
   /spending
   ```
   Worker agents use their own Gemini sessions and consume API quota. If you've hit the rate limit, workers will fail silently.

2. **Check Ollama availability** (if `LOCAL_LLM_ENABLED=true`):
   ```bash
   curl http://localhost:11434/api/tags
   ```
   Workers always use Gemini (not Ollama), but rate limiting from mixed usage can cause issues.

3. **Check logs for worker errors:**
   ```bash
   docker compose logs openclaw | grep -i "worker\|spawn"
   ```

4. **Memory limits**: Each worker spawns a separate Gemini session. Running many workers in parallel can exhaust memory. Monitor with `/system` or `/dockerstats`.

5. **Max rounds**: Workers have a `max_rounds` limit (default varies). If the task is too complex, the worker may exhaust its rounds and return a partial result.

---

## Skill Not Appearing in /skills

### Symptom: You added a new skill but it doesn't show in `/skills`

1. **Check registration in `skills/__init__.py`:**
   ```bash
   grep "my_skill" skills/__init__.py
   ```
   Your skill function must be imported and added to the `SKILLS` dict.

2. **Check for import errors:**
   ```bash
   docker compose logs openclaw | grep -i "import\|module"
   ```
   If the skill module has a syntax error or missing dependency, the entire import chain may fail silently.

3. **Check tool declaration in `config/tools.yaml`:**
   ```bash
   grep "my_skill" config/tools.yaml
   ```
   The skill must have a matching tool declaration for the LLM to call it.

4. **Restart the bot** after adding a new skill:
   ```bash
   docker compose restart openclaw
   ```

5. **Verify the skill is callable:**
   ```python
   # In a Python REPL inside the container
   from skills import SKILLS
   print("my_skill" in SKILLS)  # Should be True
   ```

---

## Ollama Timeout Errors

### Symptom: "Ollama request timed out" or slow responses

1. **Check `OLLAMA_URL` in `.env`:**
   ```env
   OLLAMA_URL=http://host.docker.internal:11434
   ```
   Inside Docker, use `host.docker.internal` (macOS/Windows) or the host's LAN IP (Linux).

2. **Check the model is pulled:**
   ```bash
   ollama list
   # Should show gemma4:e4b or your configured model
   ollama pull gemma4:e4b
   ```

3. **Check memory usage**: Large models need significant RAM. `gemma4:e4b` requires ~10 GB. If the host is low on memory, Ollama may timeout during inference.
   ```bash
   # Check available memory
   vm_stat | head -5    # macOS
   free -h              # Linux
   ```

4. **Increase timeout** (if needed): The timeout is configured in `llm.py`. Default is usually 60 seconds. For slower hardware, you may need to increase it.

5. **Disable Ollama fallback** if it's causing more problems than it solves:
   ```env
   LOCAL_LLM_ENABLED=false
   ```

---

## RSS Feed Not Updating

### Symptom: RSS digest or feed fetch returns stale or no data

1. **Check URL accessibility:**
   ```bash
   curl -sL "https://example.com/feed.xml" | head -20
   ```
   Some feeds require specific User-Agent headers or block bot traffic.

2. **Check saved feeds list:**
   ```
   /ask list my RSS feeds
   ```
   Or check the file directly:
   ```bash
   cat data/memory/rss_feeds.json
   ```

3. **Check scheduler task status**: RSS fetches are typically scheduled. Verify the task is active:
   ```
   /schedule action:list
   ```
   Look for RSS-related tasks. If missing, re-add:
   ```
   /schedule action:add skill:fetch_rss_feed interval:60
   ```

4. **Feed format issues**: The parser supports RSS 2.0 and Atom 1.0. If a feed uses a non-standard format, parsing may fail. Check logs:
   ```bash
   docker compose logs openclaw | grep -i "rss\|feed\|parse"
   ```

5. **Rate limiting by feed provider**: Some feeds limit request frequency. Space out fetch intervals.

---

## Calendar Auth Failing

### Symptom: "OAuth token expired" or "Invalid credentials" for Google Calendar

1. **Check OAuth environment variables:**
   ```bash
   docker exec openclaw env | grep GOOGLE_OAUTH | wc -l
   # Should show 3 lines (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
   ```

2. **Refresh the token**: OAuth refresh tokens can expire if unused for 6 months or if the Google Cloud project's consent screen is in "Testing" mode (tokens expire after 7 days).
   ```bash
   python scripts/google_oauth_setup.py
   ```
   This will open a browser for re-authentication and update the refresh token.

3. **Check Google Calendar API is enabled:**
   - Go to https://console.cloud.google.com/apis/library
   - Search for "Google Calendar API"
   - Ensure it shows "Enabled"

4. **Check consent screen status:**
   - Go to https://console.cloud.google.com/apis/credentials/consent
   - If status is "Testing", tokens expire after 7 days. Move to "Production" for long-lived tokens.

5. **Required `.env` variables:**
   ```env
   GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxx
   GOOGLE_OAUTH_REFRESH_TOKEN=1//xxxxx
   ```

6. **After updating tokens**, recreate the container to reload `.env`:
   ```bash
   docker compose up -d
   ```
