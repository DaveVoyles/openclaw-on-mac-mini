# Dashboard Consolidation Plan

**Date:** 2026-04-19  
**Goal:** Port the two genuinely unique features from `dashboard-v2` into the main dashboard, then remove `dashboard-v2` entirely.

---

## Background

Two dashboards currently exist:

| Dashboard | URL | Tech | Purpose |
|---|---|---|---|
| **Main** | `openclaw.davevoyles.synology.me/dashboard` | Python/aiohttp, templates/dashboard.html | Full ops dashboard (12+ pages, Discord, quality evals, memory, agent chat…) |
| **dashboard-v2** | `openclaw-dashboard.davevoyles.synology.me` | Node.js, data/dashboard-v2/ | Lightweight monitor — designed for npm CLI + tmux deployment, not the current Docker/git model |

### Why dashboard-v2 stats show all zeros
Session files are expected at `~/.openclaw/agents/main/sessions/*.jsonl` — that path doesn't exist because OpenClaw runs as a Docker container, not as an npm CLI. Zero session files → all stats zero.

### Why the buttons are useless
- **"apt update"** → updates packages inside the dashboard container only (not the host)
- **"Update OpenClaw"** → runs `npm update -g openclaw`, which does nothing since OpenClaw isn't an npm package

---

## What dashboard-v2 has vs main dashboard

| Feature | dashboard-v2 | Main dashboard |
|---|---|---|
| Container health status | ✅ | ✅ (overview, read-only dots) |
| System stats (CPU/RAM/disk) | ✅ | ✅ (overview) |
| Sessions viewer | ✅ (broken, 0 data) | ✅ (full CLI session management) |
| Cost tracking | ✅ (broken, 0 data) | ✅ (Gemini spending, model usage) |
| **Docker container actions (stop/start/restart)** | ✅ | ❌ missing |
| **Live log viewer (docker logs per service)** | ✅ | ❌ missing |
| Config editor (edit .env) | ✅ | ❌ — not porting (security risk) |
| Tailscale status | ✅ | ❌ — not porting (minimal value) |
| Auth system (login, MFA) | ✅ | ❌ — not porting (reverse proxy handles this) |
| Lifetime stats | ✅ (shows 0) | ❌ — not porting (broken) |
| "apt update" / "Update OpenClaw" | ✅ | ❌ — not porting (useless) |

**Two features to port:** Docker actions + Log viewer.

---

## Wave Plan

### Wave 1 — Port valuable features (parallel)

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|---|---|---|---|---|---|
| 1 | Han 😉🚀 | M | Backend: add `/api/docker/action` + `/api/docker/logs` routes | — | Pending |
| 2 | Yoda 👽✨ | M | Frontend: add restart buttons + log viewer panel to dashboard.html | Lane 1 complete | Pending |

**Lane 1 (Han) scope:**
- Add `api_docker_action_handler` (POST `/api/docker/action`) — accepts `{action: restart|stop|start, container: name}`, runs docker command via subprocess
- Add `api_docker_logs_handler` (GET `/api/docker/logs?service=X&lines=50`) — runs `docker logs --tail N X 2>&1`
- Register both in `src/dashboard/routes.py`
- Tests: run existing test suite to verify no regressions

**Lane 2 (Yoda) scope:**
- Add restart button to each container item in `renderContainers()` (small button, calls new backend route)
- Add a "Container Logs" panel below the container health grid: dropdown to pick service, "Fetch Logs" button, scrollable pre block
- Keep visual style consistent with existing dashboard (use `var(--surface)`, `var(--border)`, etc.)
- No new pages — add both features to the existing overview page container section

### Wave 2 — Remove dashboard-v2 (solo)

After Wave 1 is deployed and verified:
- Remove `dashboard-v2` service block from `docker-compose.yml`
- Remove `data/dashboard-v2/` directory
- Update `CHANGELOG.md` to note the consolidation
- Update any docs that reference `openclaw-dashboard.davevoyles.synology.me`
- Deploy (`make ship`) + verify main dashboard has the new features
- Stop dashboard-v2 container on Mac Mini

---

## Files to touch

**Wave 1:**
- `src/dashboard/api_handlers.py` — add two handler functions
- `src/dashboard/routes.py` — register two new routes
- `templates/dashboard.html` — add buttons + log panel to container grid section

**Wave 2:**
- `docker-compose.yml` — remove dashboard-v2 block
- `data/dashboard-v2/` — delete entire directory
- `CHANGELOG.md` — note consolidation
- Any docs referencing `openclaw-dashboard.davevoyles.synology.me`

---

## Risk assessment

- **Wave 1**: Low-Medium — adds new routes, no changes to existing logic
- **Wave 2**: Low — removing a service that provides no unique value

---

## Communication Log

| Time | Lane | Fleet | Update |
|---|---|---|---|
| — | — | — | Plan created |

---

## Status: PLANNING
