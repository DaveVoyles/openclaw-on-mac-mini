# Patreon/MonsterVision Monitoring Guide

**User-facing guide for automated Patreon cookie monitoring and management.** Operators should also use [OPERATIONS-RUNBOOK.md](OPERATIONS-RUNBOOK.md) for incident flow and [NETWORK-TOPOLOGY.md](NETWORK-TOPOLOGY.md) for service reachability.

---

## Overview

OpenClaw includes automated health monitoring for the MonsterVision Patreon downloader. The system continuously monitors cookie freshness, download activity, and container health—alerting you proactively when action is needed.

**Key Features:**
- 🔄 **Automated Health Checks** — Every 30 minutes
- 🍪 **Cookie Freshness Tracking** — Alerts before expiration
- 📊 **Download Progress Monitoring** — Real-time stats
- 🚨 **Discord Alerts** — Proactive notifications
- 🔧 **Auto-Recovery** — Container restarts and retry logic
- 📱 **Dashboard Integration** — Live status widgets

---

## Cookie Lifespan & Management

### How Long Do Cookies Last?

Patreon session cookies typically expire after **3-7 days**. The monitoring system tracks cookie age and sends alerts based on these thresholds:

| Status | Cookie Age | Description |
|--------|-----------|-------------|
| 🟢 **OK** | < 3 days | Fresh cookies, downloads active |
| 🟡 **WARNING** | 3-5 days | Cookies aging, refresh recommended |
| 🔴 **CRITICAL** | > 5 days | Cookies expired, refresh required |

### When to Refresh

You'll receive a Discord alert when:
- Cookies reach 3 days old (WARNING)
- Cookies reach 5 days old (CRITICAL)
- No downloads detected in 48 hours
- Container becomes unreachable

### How to Refresh Cookies

Run `/patreon refresh-cookies` to get step-by-step instructions, or follow this workflow:

#### Step-by-Step Instructions

1. **Open Chrome** and navigate to [patreon.com](https://patreon.com)
2. **Log in** to your Patreon account
3. **Install EditThisCookie** extension (if not already installed)
   - [Chrome Web Store Link](https://chrome.google.com/webstore/detail/editthiscookie)
4. **Click the EditThisCookie icon** in your toolbar
5. **Click "Export"** — copies cookies to clipboard as JSON
6. **Save to file:**
   ```bash
   # Paste clipboard contents to file
   echo '<paste-json-here>' > ~/Downloads/cookies.txt
   ```
7. **Copy to container:**
   ```bash
   docker cp ~/Downloads/cookies.txt monstervision:/app/cookies/cookies.txt
   ```
8. **Verify** — Downloads resume automatically within 30 minutes

#### Quick Reference

```bash
# Full workflow in one go (after exporting from EditThisCookie):
pbpaste > ~/Downloads/cookies.txt
docker cp ~/Downloads/cookies.txt monstervision:/app/cookies/cookies.txt
```

---

## Commands Reference

### `/patreon status`

Get comprehensive health diagnostics for the Patreon/MonsterVision system.

**Example Output:**
```
🎭 Patreon/MonsterVision Health Status

Cookie Status: 🟢 Fresh (2.3 days old)
Downloads: 47/120 (73 pending)
Container: ✅ Running
Last Download: 3 hours ago
Auto-Recovery: ✅ Active

Overall Status: 🟢 HEALTHY
```

**What It Shows:**
- Cookie freshness (days since last refresh)
- Download progress (completed/total/pending)
- Container health and uptime
- Last successful download timestamp
- Auto-recovery system status
- Overall health assessment

### `/patreon refresh-cookies`

Display step-by-step cookie refresh instructions directly in Discord.

**Use Case:** Run this command when you receive a cookie expiration alert or when `/patreon status` shows yellow/red cookie status.

---

## Automated Monitoring Features

### Health Checks (Every 30 Minutes)

The monitoring system performs comprehensive health checks:

✅ **Cookie Validation**
- File age tracking
- Expiration prediction
- Format verification

✅ **Download Activity**
- Progress tracking (downloaded/total/pending)
- 48-hour activity window
- Success/failure rate

✅ **Container Health**
- Availability checks
- Restart count monitoring
- Resource usage

✅ **Alert Triggers**
- Cookie aging thresholds
- Download stagnation detection
- Container crash detection

### Discord Alerts

Alerts are sent to the configured alert channel when issues are detected:

| Severity | Trigger | Action Required |
|----------|---------|-----------------|
| 🟡 **WARNING** | Cookies 3-5 days old | Refresh cookies soon |
| 🟡 **WARNING** | No downloads in 48h | Check container logs |
| 🔴 **CRITICAL** | Cookies > 5 days | Refresh cookies immediately |
| 🔴 **CRITICAL** | Container down | Check Docker status |
| 🔴 **CRITICAL** | Download failures | Investigate errors |

**Alert Deduplication:** The system prevents spam by enforcing a 24-hour cooldown per unique issue.

### Auto-Recovery System

The monitoring system includes intelligent auto-recovery features:

🔄 **Container Restarts**
- Detects container crashes
- Automatic restart attempt
- Discord notification on recovery

🍪 **Cookie Detection**
- Monitors cookie file updates
- Triggers refresh on detection
- Verifies download resumption

⏮️ **Download Retry**
- Retries failed downloads
- Exponential backoff
- Success notification

---

## Dashboard Integration

View real-time Patreon status on the [OpenClaw Dashboard](http://192.168.1.93:8765/dashboard):

**Status Banner:**
- Quick health indicator in the top status bar
- 🟢 Green = Healthy
- 🟡 Yellow = Warning
- 🔴 Red = Critical

**Patreon Widget:**
- Cookie freshness with color coding
- Download progress (downloaded/total, pending count)
- Last download timestamp
- Next health check countdown
- "Refresh Cookies" button with modal instructions

The dashboard auto-refreshes every 60 seconds.

---

## Troubleshooting Guide

### Common Issues

#### ❌ Cookies Not Working After Refresh

**Symptoms:** Downloads don't resume after copying new cookies

**Solutions:**
1. Verify you're **logged into Patreon** before exporting
2. Check cookie file format (should be JSON)
3. Ensure file has correct permissions:
   ```bash
   chmod 644 ~/Downloads/cookies.txt
   ```
4. Verify file was copied to container:
   ```bash
   docker exec monstervision ls -lh /app/cookies/cookies.txt
   ```

#### ❌ Wrong Cookie Format

**Symptoms:** Container logs show "Invalid cookie format"

**Solutions:**
1. Use **EditThisCookie** extension (not other cookie exporters)
2. Click "Export" not "Import"
3. Ensure export format is **JSON** (default)
4. Don't manually edit the exported JSON

#### ❌ Container Keeps Restarting

**Symptoms:** Container crash loop, downloads fail repeatedly

**Solutions:**
1. Check container logs:
   ```bash
   docker logs monstervision --tail 100
   ```
2. Verify disk space:
   ```bash
   df -h
   ```
3. Restart with fresh cookies:
   ```bash
   docker restart monstervision
   # Then copy fresh cookies
   ```

#### ❌ No Downloads Detected

**Symptoms:** Container running, cookies fresh, but no downloads

**Solutions:**
1. Check if there are pending downloads:
   ```bash
   docker exec monstervision ls /app/downloads/pending
   ```
2. Verify Patreon account has active pledges
3. Check container configuration:
   ```bash
   docker exec monstervision env | grep PATREON
   ```

#### ❌ Permission Denied

**Symptoms:** Cannot copy cookies to container

**Solutions:**
1. Verify container is running:
   ```bash
   docker ps | grep monstervision
   ```
2. Check file permissions on local file:
   ```bash
   ls -lh ~/Downloads/cookies.txt
   ```
3. Use absolute paths:
   ```bash
   docker cp /Users/$(whoami)/Downloads/cookies.txt monstervision:/app/cookies/cookies.txt
   ```

---

## FAQ

### How often are cookies checked?

The monitoring system runs comprehensive health checks **every 30 minutes**. Cookie age is tracked continuously.

### Do I need to restart the container after refreshing cookies?

**No.** The MonsterVision container detects cookie file updates automatically. Downloads resume within 30 minutes without manual intervention.

### What happens if I miss a cookie expiration alert?

The system will:
1. Continue sending alerts (respecting 24h cooldown)
2. Mark status as CRITICAL on dashboard
3. Attempt auto-recovery if container is configured for it
4. Pause downloads until cookies are refreshed

### Can I refresh cookies proactively before expiration?

**Yes, recommended!** You can refresh cookies anytime via the dashboard "Refresh Cookies" button or by running `/patreon refresh-cookies` and following the instructions.

### Where are cookies stored?

Cookies are stored at `/app/cookies/cookies.txt` inside the MonsterVision container. The monitoring system tracks the file modification time to determine age.

### How do I disable monitoring alerts?

Edit `.env` file and set:
```bash
PATREON_MONITORING_ENABLED=false
```

Then restart OpenClaw:
```bash
docker restart openclaw
```

### What if I don't have Chrome?

You can use any Chromium-based browser (Edge, Brave, Opera) with the EditThisCookie extension. Firefox users can use "Cookie Quick Manager" extension with similar export functionality.

### Can I automate cookie refresh?

Cookie refresh requires manual login to Patreon (for security), so full automation isn't possible. However, the monitoring system **minimizes manual intervention** by:
- Alerting you proactively before expiration
- Providing one-click access to instructions
- Auto-detecting when you've refreshed cookies
- Resuming downloads automatically

---

## Quick Reference Card

| Task | Command/Action |
|------|----------------|
| Check health | `/patreon status` |
| Get refresh instructions | `/patreon refresh-cookies` |
| View dashboard | http://192.168.1.93:8765/dashboard |
| Check container logs | `docker logs monstervision --tail 50` |
| Copy fresh cookies | `docker cp ~/Downloads/cookies.txt monstervision:/app/cookies/cookies.txt` |
| Restart container | `docker restart monstervision` |
| Check next health check | View dashboard "Next Check" field |

---

## Support

For issues not covered in this guide:

1. Check container logs: `docker logs monstervision --tail 100`
2. View dashboard for real-time status: http://192.168.1.93:8765/dashboard
3. Run `/patreon status` for diagnostics
4. Review `src/patreon_scheduled.py` for the scheduled implementation details

---

**Last Updated:** January 2025  
**Related Docs:** `src/patreon_scheduled.py` (scheduled implementation guide)
