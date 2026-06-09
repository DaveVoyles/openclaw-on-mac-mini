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

## Status: COMPLETE (Wave 1 + Wave 2 done, deployed 0b7c91f)

---

# Phase 2: Dashboard Simplification Plan

**Date:** 2026-04-19  
**Goal:** Remove visual clutter and merge redundant cards on the main dashboard homepage.

---

## Findings

### 1. Three stale release-notes cards (~812 lines, ~14% of template)

| Card | Lines | Content |
|---|---|---|
| `💬 Recent Features (v0.11.0)` | 862–1245 | Static grid of feature tiles |
| `🚀 New in v0.12.0: Premium API Integrations` | 1248–1425 | Static grid of feature tiles |
| `🚀 Phase 3 & 4: Advanced Capabilities` | 1428–1673 | Static grid of feature tiles |

All three are **static changelog content** — no live data, no interaction, fully covered by `CHANGELOG.md`. They make the homepage extremely long to scroll through.

**Proposal:** Remove all three. Add a single "📝 See CHANGELOG" link in the homepage header area.

### 2. Duplicate orientation cards ("Access Points" + "Which Interface Should I Use?")

| Card | Lines | Content |
|---|---|---|
| `🌐 Access Points` | 718–758 | Link tiles: Discord, Open WebUI, CLI, Slack |
| `🗺️ Which Interface Should I Use?` | 761–807 | Comparison table: same 5 interfaces |

Both cards cover the same 5 interfaces. The tiles link out; the table explains when to use each. **Proposal:** Merge into one card — tiles on top, comparison table collapsed inside a `<details>`.

### 3. Channel Memory tools (medium priority, distinct workflows)

`🧪 Channel Memory Inspector` and `🧠 Channel Profile Assistant` both take a channel ID input and operate on scoped memory — but serve different purposes (inspect/clear/retrain vs. recommend/apply/revert). These *could* be merged with a `<details>` separator but are lower priority. **Proposal:** Defer unless user wants it.

---

## Wave Plan

### Wave 1 — Remove stale release-notes cards (S)

- Remove card at lines 862–1245 (`v0.11.0 features`)
- Remove card at lines 1248–1425 (`v0.12.0 features`)
- Remove card at lines 1428–1673 (`Phase 3 & 4 features`)
- Add a minimal "See full changelog →" link in the quick-actions strip or below the Ask card

**Risk:** Low. Pure content removal, no live data affected.  
**Savings:** ~812 lines removed.

### Wave 2 — Merge Access Points + Interface Guide (S)

- Combine `🌐 Access Points` (tiles) + `🗺️ Which Interface Should I Use?` (comparison table) into a single card
- Tiles remain always-visible at top; comparison table wrapped in a `<details>` that is collapsed by default
- Remove the second card entirely

**Risk:** Low. Visual-only change.  
**Savings:** ~40 lines, 2 cards → 1.

---

## Files to touch

- `templates/dashboard.html` — remove 3 release-notes cards; merge 2 orientation cards
- No backend changes required; no API changes

---

## Status: PLANNING
