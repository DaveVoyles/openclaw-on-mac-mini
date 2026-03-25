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
- `data/audit/`: Audit logs for security-sensitive actions.
- `data/tasks.json`: Mission Control task data.
