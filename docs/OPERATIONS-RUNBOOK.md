# OpenClaw Operations Runbook

Concise operator playbook for incidents, monitoring interpretation, backup/recovery, and remote operations.

---

## Monitoring Surfaces

| Surface | What to check | Healthy signal | Action when unhealthy |
| --- | --- | --- | --- |
| `/health` | App liveness/readiness | HTTP 200 with healthy/degraded JSON | If down, inspect container state and recent logs immediately. |
| `/metrics` | Prometheus-style counters/gauges | `openclaw_up 1` and fresh uptime values | If missing, treat as dashboard/HTTP listener regression. |
| `/dashboard` | Operator UI | Loads within a few seconds | Use as first-line triage for traces, status, and linked surfaces. |
| Docker healthcheck | Container-level liveness | `healthy` in `docker ps` | Restart or rebuild only after reviewing logs. |
| Discord alert channel | Automated alerts | Low-noise warnings/critical events | Use alert timestamps to anchor incident timelines. |
| Uptime Kuma | External polling of `/health` and `/metrics` | Last check OK within 60s | Confirms whether the issue is local-only or externally visible. |
| MonsterVision API/status | Patreon cookies + download activity | Cookie label `ok`, recent downloads | If stale, follow Patreon playbook below. |

---

## Thresholds & Interpretation

### Platform thresholds

| Signal | Warning | Critical | Source |
| --- | --- | --- | --- |
| Disk usage | `>= 80%` | `>= 90%` | `src/health_checker.py` |
| Memory usage | `>= 80%` | `>= 90%` | `src/health_checker.py` |
| Container health | first unhealthy/exited observation | auto-restart after 2 consecutive unhealthy checks | `src/bg_monitoring.py` |
| Error patterns | 2+ warnings in a 30 min window | any critical pattern | `src/bg_monitoring.py` |
| MonsterVision cookie age | 3-5 days | >5 days | `docs/PATREON_MONITORING.md` |
| MonsterVision inactivity | 48h without downloads | same if persistent with failures | `docs/PATREON_MONITORING.md` |

### How to read degraded vs unhealthy

- **Degraded**: service is up but approaching a hard limit or missing a non-fatal dependency.
- **Unhealthy**: service cannot reliably serve traffic; treat as an active incident.
- **Repeated Discord alerts** after the cooldown window usually mean the auto-heal path did not fully resolve the issue.

---

## Incident Response Playbooks

### A. OpenClaw health endpoint fails

1. Confirm scope:
   ```bash
   curl -sf http://localhost:8765/health
   docker ps --filter name=openclaw
   ```
2. Review recent logs:
   ```bash
   docker compose logs openclaw --tail 100
   ```
3. If the container is stopped or unhealthy, restart once:
   ```bash
   docker compose restart openclaw
   ```
4. Re-check `/health`, `/metrics`, and `/dashboard`.
5. If still failing, rebuild with the existing compose workflow:
   ```bash
   docker compose up -d --build openclaw
   ```
6. If the rebuild fails, escalate with the failing logs and any Trace IDs surfaced in Discord/dashboard.

### B. Dashboard works but Discord bot behavior is degraded

1. Check Discord alert channel for recent warnings.
2. Search logs for exceptions or API failures:
   ```bash
   docker compose logs openclaw --tail 200 | grep -i "error\|exception\|trace"
   ```
3. Validate required external dependencies (Gemini, Ollama, NAS, etc.) using the targeted troubleshooting doc.
4. Restart only the affected dependency before restarting OpenClaw if the fault is downstream.

### C. Container health alerts for media stack

1. Identify whether the alert is for `sonarr`, `radarr`, `lidarr`, `prowlarr`, `sabnzbd`, `qbittorrent`, `tautulli`, or `overseerr`.
2. Confirm whether auto-restart already occurred (OpenClaw retries after 2 consecutive unhealthy checks).
3. Inspect the target service logs directly.
4. If the service is still down, restart it manually from the host/NAS platform and verify its HTTP UI.
5. Document whether the root cause was app-level, Docker-level, or network-level.

### D. Patreon / MonsterVision incident

1. Query status:
   ```bash
   curl -sf http://192.168.1.93:8766/api/status | python3 -m json.tool
   ```
2. If `cookie_status` is not `ok`, refresh cookies using [PATREON_MONITORING.md](PATREON_MONITORING.md).
3. If cookies are fresh but downloads are stalled, inspect container logs:
   ```bash
   docker logs monstervision --tail 100
   ```
4. Restart MonsterVision only after saving fresh cookies if expiration is suspected.
5. Verify downloads resume within the next monitoring cycle.

### E. NAS / backup path incident

1. Verify the host helper path first:
   ```bash
   curl -sf http://192.168.1.93:19501/webapi/query.cgi?api=SYNO.API.Info\&version=1\&method=query\&query=SYNO.API.Auth
   ```
2. Verify SSH reachability:
   ```bash
   ssh -p 24 dave@192.168.1.8 'echo ok'
   ```
3. If proxy is down, restart the host `nas_proxy.py` launch agent or run it manually.
4. If SSH is down, treat backups as failed until connectivity is restored.
5. Run a manual backup after recovery to re-establish a fresh restore point.

---

## Slack Bot Reference

### Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `SLACK_BOT_TOKEN` | Yes | `xoxb-` token — needs `chat:write`, `commands`, `files:read` scopes |
| `SLACK_APP_TOKEN` | Yes | `xapp-` token for Socket Mode — needs `connections:write` scope |
| `OPENCLAW_UPLOAD_KEY` | Yes | Auth key for the `/upload` endpoint (UUID). On Mac Mini: `c926fab5-c06e-453c-b8e6-3c9b0ddf3042` |
| `SLACK_NOTIFY_USER_ID` | Yes | Slack user ID to DM when new files appear in `AI_FILES_DIR` |
| `AI_FILES_DIR` | Yes | Directory polled by the proactive file-alert loop |
| `OPENCLAW_FILE_POLL_INTERVAL` | No | Seconds between file-alert polls (default: `60`) |
| `DIGEST_CHECK_INTERVAL` | No | Seconds between digest-loop checks (default: `3600` / 1 hour) |
| `DIGEST_LOOKBACK_HOURS` | No | Hours of file history shown in each digest (default: `24`) |

### Slack HTTP Endpoints

| Method | URL | Auth | Notes |
| --- | --- | --- | --- |
| `GET` | `http://192.168.1.93:8080/health` | none | Polled by `/status` slash command; returns JSON liveness |
| `POST` | `http://192.168.1.93:8080/upload` | `X-OpenClaw-Key` header | Multipart field `file`; allowed: `.docx .xlsx .pdf .txt .csv`; blocked: `.exe .sh .py .zip .bat` |

### Wave 5 Slash Commands (new in v0.14.0)

| Command | Description | Data stored |
| --- | --- | --- |
| `/digest on\|off\|status` | Per-user daily file digest via DM | `data/digest_prefs.json` |
| `/template list\|<name>` | Lists or DMs a starter template file | `data/templates/` (committed) |
| `/brief` | Shows user's last 5 uploaded files with timestamps | reads file metadata |
| `/mystats` | Per-user stats from `slack_metrics.jsonl` | reads metrics log |

### Excel Formula Intelligence Button

When an `.xlsx` file is uploaded, a **📐 Formulas** button appears alongside Summarize, Chart, etc.
Clicking it sends the spreadsheet to the AI with a prompt that explains every formula in plain English,
flags errors, and suggests simpler alternatives.

### `/status` Slash Command

- Pings `http://192.168.1.93:8080/health` for liveness.
- Reads `data/last_sync.json` (written by `scripts/watch_folder.sh` after each rsync) for last-sync metadata.
- If `last_sync.json` is missing or stale, the sync job has not run recently.

### Container Restart Note

A plain `docker restart` does **not** reload `.env` values. After any `.env` change, force-recreate the container:

```bash
/usr/local/bin/docker-compose up -d --no-deps --force-recreate openclaw
# or from ~/openclaw/ on the Mac Mini:
make ship-server
```

---

## Backup & Recovery Basics

### What is protected

- `config/`
- `data/tasks.json`
- `data/memory/`
- `data/audit/`
- `data/vault/`
- `.env`

### Standard backup workflow

```bash
./scripts/backup_restore.sh backup
./scripts/backup_restore.sh list
```

### Recovery workflow

1. List and inspect available archives.
2. Stop OpenClaw if it is still running.
3. Run:
   ```bash
   ./scripts/backup_restore.sh restore <archive>
   ```
4. The script creates a pre-restore safety backup first.
5. Restart and validate:
   ```bash
   docker compose up -d --build
   ./scripts/health-check.sh
   ```

### Recovery caveats

- Restore is **destructive** to current config/data.
- Backups are local tarballs unless the nightly NAS maintenance copy also succeeded.
- If `.env` was rotated after the backup, reconcile secrets before restarting production traffic.

---

## Remote Operations

### Preferred workflow

1. Connect over **Tailscale** when off-LAN.
2. SSH to the **Mac Mini host** for Docker, logs, and host helper services.
3. Use the dashboard for non-destructive triage whenever possible.
4. SSH to the NAS only for storage, backup, or DSM verification tasks.

### Remote-access guardrails

- Do not expose new inbound ports just for debugging.
- Preserve Ethernet-first networking on the Mac Mini.
- Avoid changing `DOCKER_HOST_IP`, `NAS_URL`, or port mappings during an incident unless you are explicitly fixing a network regression.
- If remote-only access is broken, check Tailscale status before assuming Docker is at fault.

---

## Focused Validation Checklist

After any operator intervention:

1. `docker ps` shows `openclaw` healthy.
2. `curl -sf http://localhost:8765/health` succeeds.
3. `curl -sf http://localhost:8765/metrics | head` returns metrics.
4. Dashboard loads.
5. The alert channel is quiet or shows a recovery message.
6. If storage was involved, verify NAS proxy and/or SSH reachability.

---

## Escalation Notes to Capture

When handing off, capture:

- First observed time and alert source
- Affected surface (`/health`, Discord, NAS proxy, MonsterVision, etc.)
- Commands run
- Whether auto-restart or self-heal already triggered
- Current customer/operator impact

---

**Related docs:** [NETWORK-TOPOLOGY.md](NETWORK-TOPOLOGY.md), [MAINTENANCE.md](MAINTENANCE.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [PATREON_MONITORING.md](PATREON_MONITORING.md)
