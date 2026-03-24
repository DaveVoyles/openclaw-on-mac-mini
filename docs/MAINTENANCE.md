# OpenClaw — Maintenance & Operations Guide

This document outlines common maintenance tasks, troubleshooting steps, and operational procedures for the OpenClaw system on the Mac Mini.

## 🔄 System Startup & Reliability

### Docker Container Restart Policy
To ensure that OpenClaw and its associated services are always available after a system reboot or Docker daemon restart, we use the `always` restart policy in `docker-compose.yml`.

- **Current Policy**: `restart: always`
- **Location**: [docker-compose.yml](../docker-compose.yml)

### Delayed Proton VPN Startup (macOS)
On the Mac Mini host, Proton VPN may fail to connect if it launches before an active internet connection is established. To mitigate this, a custom delay mechanism is used.

#### Components:
1.  **Delay Script**: [`scripts/delay_proton_launch.sh`](../scripts/delay_proton_launch.sh)
    *   Waits 30 seconds after login.
    *   Pings `8.8.8.8` (Google DNS) to verify connectivity.
    *   Launches the Proton VPN application only after a successful ping.
2.  **LaunchAgent**: [`scripts/com.user.delayprotonlaunch.plist`](../scripts/com.user.delayprotonlaunch.plist)
    *   Installed at `~/Library/LaunchAgents/com.user.delayprotonlaunch.plist`.
    *   Triggers the delay script automatically on user login.

#### Manual Management:
If you need to reload or stop this delay mechanism:
```bash
# Unload/Disable
launchctl unload ~/Library/LaunchAgents/com.user.delayprotonlaunch.plist

# Load/Enable
launchctl load ~/Library/LaunchAgents/com.user.delayprotonlaunch.plist
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
- `data/audit/`: Audit logs for security-sensitive actions.
- `data/tasks.json`: Mission Control task data.
