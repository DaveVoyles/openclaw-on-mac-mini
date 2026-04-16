# OpenClaw Network Topology & Port Inventory

Operator-facing reference for how traffic moves between the Mac Mini host, Dockerized OpenClaw, the Synology NAS, and remote access layers.

---

## Topology at a Glance

| Zone | Node | Role | Default Address / Port | Notes |
| --- | --- | --- | --- | --- |
| LAN host | Mac Mini M4 | Primary OpenClaw host | `192.168.1.93` | OpenClaw publishes the dashboard and health endpoints from here. |
| Container | `openclaw` | Discord bot + dashboard + metrics | `:8765` | Docker publishes `8765:8765` in both compose files. |
| LAN host | MonsterVision | Patreon downloader | `192.168.1.93:8766` | OpenClaw polls its API for cookie/download status. |
| LAN host | Uptime Kuma | External uptime monitor | `192.168.1.93:3001` | Polls `/health` and `/metrics`. |
| LAN host | Tautulli | Plex analytics | `192.168.1.93:8181` | Used for media/server health insights. |
| LAN host | Overseerr | Media request UI | `192.168.1.93:5055` | Downstream service; useful when validating LAN reachability. |
| LAN host | Sonarr / Radarr / Lidarr / Prowlarr | Media automation services | `8989`, `7878`, `8686`, `9696` | Safe auto-restart targets in monitoring logic. |
| Host helper | NAS proxy | Bridges Docker → Synology DSM | `192.168.1.93:19501` | Forwards to Synology DSM because Docker on macOS cannot reliably reach LAN services directly. |
| NAS | Synology DSM | Storage + backup target | `192.168.1.8:5001` (HTTPS) | Also exposes SSH on port `24` for rsync/scp backups. |
| NAS | qBittorrent / SABnzbd | Download clients | `192.168.1.8:8080`, `192.168.1.8:8775` | qBit/SAB run behind gluetun VPN on the NAS. |
| Remote access | Tailscale | Secure remote operations | device-specific | Preferred remote admin path; avoids opening extra inbound ports. |
| WAN edge | Traefik on NAS | External HTTPS ingress | `80/443` | Terminates TLS for services exposed from the NAS side. |

---

## Traffic Paths

### 1. Discord/API ingress to OpenClaw

```text
Discord / external APIs
        |
        v
  Mac Mini host (192.168.1.93)
        |
        v
 Docker port publish 8765:8765
        |
        v
  openclaw container (/health, /metrics, /dashboard)
```

### 2. OpenClaw to Synology DSM

```text
openclaw container
        |
        v
NAS_URL=http://host.docker.internal:19501
        |
        v
Mac Mini NAS proxy (scripts/nas_proxy.py)
        |
        v
Synology DSM https://192.168.1.8:5001
```

Use this path for DSM API calls from inside Docker. If DSM checks fail but the NAS is healthy, validate the proxy first.

### 3. Nightly backup flow

```text
openclaw container
  |  rsync/scp over SSH (port 24)
  v
Synology NAS /volume1/docker/openclaw/backups/<date>/
```

Nightly maintenance backs up config, memory, audit logs, vault contents, tasks, and `.env`.

### 4. Patreon / MonsterVision monitoring flow

```text
openclaw container ---> http://192.168.1.93:8766/api/status
        |                                |
        |                                v
        +---- Discord alert channel <---- MonsterVision status / logs
```

OpenClaw checks MonsterVision roughly every 5 minutes for cookie expiry and stalled downloads.

---

## OpenClaw Port Inventory

| Port | Surface | Owner | Protocol | Why it matters |
| --- | --- | --- | --- | --- |
| `8765` | `/health`, `/metrics`, `/dashboard`, `/api/dashboard` | OpenClaw | HTTP | Primary operator surface and probe target. |
| `19501` | NAS proxy | Mac Mini host helper | HTTP → HTTPS proxy | Required when Dockerized OpenClaw needs DSM API access. |
| `8766` | MonsterVision API | Mac Mini host service | HTTP | Source for Patreon cookie/download monitoring. |
| `3001` | Uptime Kuma | Mac Mini host service | HTTP | Where external uptime checks are configured. |
| `8181` | Tautulli | Mac Mini host service | HTTP | Plex health/usage monitoring. |
| `5055` | Overseerr | Mac Mini host service | HTTP | Media request service; optional validation target. |
| `8989` / `7878` / `8686` / `9696` | Sonarr / Radarr / Lidarr / Prowlarr | Mac Mini host services | HTTP | Monitored Docker/LAN services used by media automation. |
| `8080` / `8775` | qBittorrent / SABnzbd | Synology NAS | HTTP | Download clients behind gluetun VPN. |
| `5001` | Synology DSM | Synology NAS | HTTPS | Storage health, Hyper Backup, and NAS API target. |
| `24` | Synology SSH | Synology NAS | SSH | Required for nightly backup jobs and manual restore verification. |
| `80` / `443` | Traefik | Synology NAS | HTTP/HTTPS | External ingress on NAS-hosted apps. |

---

## Remote Access Expectations

### Preferred access order

1. **Tailscale** for remote administration.
2. **SSH to the Mac Mini host** for Docker and host-helper operations.
3. **Synology DSM / SSH** only when storage or backup validation is required.

### Operator assumptions

- The Mac Mini should remain on **Ethernet `en0` at `192.168.1.93`**.
- Wi-Fi should stay disabled to avoid interface flapping and broken long-lived TCP sessions.
- Dockerized services depend on the host IP staying stable; update `DOCKER_HOST_IP` and related URLs if the LAN address changes.
- Prefer the OpenClaw dashboard and health endpoints over direct container shell access for first-line triage.

---

## Quick Validation Commands

```bash
# OpenClaw operator surface
curl -sf http://192.168.1.93:8765/health | python3 -m json.tool
curl -sf http://192.168.1.93:8765/metrics | head
open http://192.168.1.93:8765/dashboard

# NAS proxy and DSM reachability
curl -sf http://192.168.1.93:19501/webapi/query.cgi?api=SYNO.API.Info\&version=1\&method=query\&query=SYNO.API.Auth
ssh -p 24 dave@192.168.1.8 'echo ok'

# MonsterVision status
curl -sf http://192.168.1.93:8766/api/status | python3 -m json.tool
```

---

## When to Escalate

Escalate beyond routine runbooks when:

- `8765` is unreachable from both localhost and the LAN.
- The NAS proxy is healthy but DSM `:5001` is still unreachable.
- Ethernet `en0` is no longer the primary network interface.
- Tailscale and local SSH are both unavailable, suggesting a host-level outage.

---

**Related docs:** [MAINTENANCE.md](MAINTENANCE.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [PATREON_MONITORING.md](PATREON_MONITORING.md), [SERVICES.md](SERVICES.md)
