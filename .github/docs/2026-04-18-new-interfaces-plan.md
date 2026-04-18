# OpenClaw New Interfaces — Implementation Plan

**Date:** 2026-04-18  
**Requested by:** User  
**Status:** Planning

---

## Request & Target Outcome

Add three new ways to interact with OpenClaw:

1. **Open WebUI** — a browser-based chat UI (open-source) that connects to any OpenAI-compatible API
2. **OpenClaw Dashboard v2** — the community `tugcantopaloglu/openclaw-dashboard` Node.js monitoring dashboard
3. **Slack** — a Slack bot that receives messages and forwards them through OpenClaw's LLM stack

All three should integrate cleanly with the existing `192.168.1.93:8765` server, ideally as additional Docker services in `docker-compose.yml`.

---

## Current Architecture Snapshot

| Component | Location | Notes |
|-----------|----------|-------|
| OpenClaw server | Docker, port 8765 | aiohttp on `src/discord_web.py`; also serves dashboard |
| Existing `/api/agent/ask` | POST, non-streaming | Returns JSON: `{response, model, tokens}` |
| Existing `/api/agent/ask/stream` | POST, SSE streaming | Used by CLI |
| OpenClaw Dashboard v1 | `src/dashboard/` | Served at `/` on port 8765 |
| Model routing | `src/ask_orchestrator.py` | `run_ask_stream` is the canonical entry point |

**Key gaps:**
- No OpenAI-compatible `/v1/chat/completions` endpoint → Open WebUI cannot connect
- No Dashboard v2 deployed → separate Node.js service needed
- No Slack bot → needs Slack Bolt SDK + Socket Mode

---

## Wave Plan

### Wave 1 — Parallel Research (S, S, S)

Three independent research lanes to pin down exact requirements before implementation.

| Lane | Fleet | Effort | Scope | Blocked by | Checkpoint |
|------|-------|--------|-------|------------|------------|
| 1 | Han 😉🚀 | S | OpenAI-compat API spec + Open WebUI config needs | — | 5m |
| 2 | Yoda 👽✨ | S | Dashboard v2 data requirements + deployment config | — | 5m |
| 3 | Leia 👑💁‍♀️ | S | Slack Bolt SDK + Socket Mode architecture | — | 5m |

---

### Wave 2 — Parallel Implementation (M, M, L)

| Lane | Fleet | Effort | Scope | Blocked by | Checkpoint |
|------|-------|--------|-------|------------|------------|
| 1 | Han 😉🚀 | M | `/v1/models` + `/v1/chat/completions` endpoints in `discord_web.py` | Wave 1, Lane 1 | 10m |
| 2 | Yoda 👽✨ | M | Dashboard v2 clone + `docker-compose.yml` service entry | Wave 1, Lane 2 | 10m |
| 3 | Leia 👑💁‍♀️ | L | `src/slack_bot.py` — Bolt SDK, socket mode, message routing, env vars | Wave 1, Lane 3 | 15m |

---

### Wave 3 — Integration + Validation (M, M, M)

| Lane | Fleet | Effort | Scope | Blocked by | Checkpoint |
|------|-------|--------|-------|------------|------------|
| 1 | Han 😉🚀 | M | Open WebUI Docker service in `docker-compose.yml`, CORS config, end-to-end test | Wave 2, Lane 1 | 10m |
| 2 | Yoda 👽✨ | M | Dashboard v2 smoke test: health, sessions, memory pages loading | Wave 2, Lane 2 | 10m |
| 3 | Leia 👑💁‍♀️ | M | Slack end-to-end: send message → Slack → OpenClaw → response in thread | Wave 2, Lane 3 | 10m |

---

## Implementation Details

### 1. Open WebUI

**What it is:** [Open WebUI](https://github.com/open-webui/open-webui) — a Svelte/Python web chat UI that speaks the OpenAI API.

**What we need to build:**

#### A. `/v1/models` endpoint (new, in `src/discord_web.py`)
```json
GET /v1/models
{
  "object": "list",
  "data": [
    {"id": "openclaw-auto", "object": "model", "owned_by": "openclaw"},
    {"id": "openclaw-gemini", "object": "model", "owned_by": "openclaw"},
    {"id": "openclaw-copilot", "object": "model", "owned_by": "openclaw"}
  ]
}
```

#### B. `/v1/chat/completions` endpoint (new, in `src/discord_web.py`)
- Accepts standard OpenAI body: `{model, messages, stream}`
- Maps `messages[-1].content` → prompt; prior messages → history list
- Maps `model` → `model_pref` (openclaw-auto → "auto", etc.)
- Calls `_execute_agent_ask()` (non-streaming) or the streaming path
- Returns OpenAI-compatible response shape:
  ```json
  {
    "id": "chatcmpl-...",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
    "model": "openclaw-auto",
    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
  }
  ```
- For streaming: returns `text/event-stream` with `data: {...}` chunks

#### C. Open WebUI Docker service (new service in `docker-compose.yml`)
```yaml
open-webui:
  image: ghcr.io/open-webui/open-webui:main
  container_name: open-webui
  ports:
    - "3000:8080"
  environment:
    - OPENAI_API_BASE_URL=http://host.docker.internal:8765/v1
    - OPENAI_API_KEY=openclaw  # dummy key, auth handled by OpenClaw
    - WEBUI_AUTH=false         # disable Open WebUI auth for LAN use
  restart: unless-stopped
```
Open WebUI accessible at `http://192.168.1.93:3000`.

**Risk:** Medium — adds two new routes to `discord_web.py`; no changes to routing logic.

---

### 2. OpenClaw Dashboard v2

**What it is:** [`tugcantopaloglu/openclaw-dashboard`](https://github.com/tugcantopaloglu/openclaw-dashboard) — a Node.js monitoring dashboard with sessions, memory, logs, costs, rate limits, Docker management, and more.

**What it reads from:**
- OpenClaw workspace directory (`WORKSPACE_DIR`) — session files, memory files, logs
- `http://192.168.1.93:8765/health` — health endpoint (already exists)
- `~/.openclaw` (agent data dir, `OPENCLAW_DIR`)
- Optional: Docker socket for container management

**Deployment plan:**

#### A. Clone into `data/dashboard-v2/` (excluded from Docker image, host-only)
```bash
cd /Users/davevoyles/openclaw
git clone https://github.com/tugcantopaloglu/openclaw-dashboard data/dashboard-v2/
```
Or as a Docker service with volume mount.

#### B. New service in `docker-compose.yml`
```yaml
dashboard-v2:
  image: node:18-alpine
  container_name: openclaw-dashboard-v2
  working_dir: /app
  command: node server.js
  ports:
    - "7000:7000"
  environment:
    - DASHBOARD_PORT=7000
    - WORKSPACE_DIR=/workspace
    - OPENCLAW_DIR=/openclaw-data
    - DASHBOARD_ALLOW_HTTP=true
  volumes:
    - ./data/dashboard-v2:/app:ro   # the cloned repo
    - ./data:/workspace:ro          # OpenClaw data dir
    - ./data/memory:/openclaw-data:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
  restart: unless-stopped
```
Dashboard v2 accessible at `http://192.168.1.93:7000`.

**Consideration:** The dashboard reads `OPENCLAW_API_URL` (defaults to `http://localhost:8765`) — set to `http://openclaw:8765` for Docker network resolution.

**Risk:** Low — read-only access to workspace; separate container; no changes to OpenClaw source.

---

### 3. Slack Bot

**What it is:** A Slack bot using [Slack Bolt for Python](https://slack.dev/bolt-python/) in **Socket Mode** (no public URL needed, works on LAN).

**Why Socket Mode:** The Mac Mini is on a private LAN (`192.168.1.93`) with no public URL. Socket Mode uses a persistent WebSocket connection to Slack's servers — no inbound port mapping needed.

**Architecture:**
```
Slack (user DMs bot or mentions @OpenClaw)
  ↓ WebSocket (Socket Mode)
src/slack_bot.py (Bolt App, async)
  ↓ internal call
ask_orchestrator.run_ask_stream(prompt, history)
  ↓
LLM response
  ↓ Slack SDK post_message
Slack thread/DM reply
```

#### A. New file: `src/slack_bot.py`
- Uses `slack_bolt[async]` + `aiohttp` app backend
- Listens for: `app_mention` events, `message` in DMs
- Sends typing indicator while processing
- Posts response back as a thread reply
- Maps Slack thread history → OpenClaw conversation history
- Graceful error handling with user-visible error messages

#### B. Integration with `bot.py` / startup
- Start as an `asyncio` task alongside the existing Discord bot + aiohttp server
- Or as a separate Docker service if token management is easier

#### C. New environment variables (add to `.env.example`)
```
SLACK_BOT_TOKEN=xoxb-...        # Bot token (OAuth)
SLACK_APP_TOKEN=xapp-...        # App-level token (Socket Mode)
SLACK_SIGNING_SECRET=...        # Request verification
SLACK_ENABLED=true              # Feature flag
```

#### D. Dockerfile + requirements.txt
- Add `slack_bolt` and `slack_sdk` to `requirements.txt`

**Risk:** Medium — new background task in the bot process; needs Slack app setup by user.

---

## Required User Actions (Pre-Implementation)

| Interface | User Action Needed |
|-----------|--------------------|
| Open WebUI | None — automatic |
| Dashboard v2 | None — automatic |
| Slack | Create Slack app at api.slack.com, enable Socket Mode, copy `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` to `.env` |

---

## Port Map (after implementation)

| Port | Service |
|------|---------|
| 8765 | OpenClaw server (existing) + `/v1/` OpenAI-compat API |
| 3000 | Open WebUI chat interface |
| 7000 | OpenClaw Dashboard v2 |
| — | Slack (Socket Mode — no inbound port needed) |

---

## Files to Create/Modify

| File | Action | Notes |
|------|--------|-------|
| `src/discord_web.py` | Modify | Add `/v1/models` + `/v1/chat/completions` routes |
| `src/slack_bot.py` | Create | New Slack Bolt async app |
| `docker-compose.yml` | Modify | Add `open-webui` + `dashboard-v2` services |
| `requirements.txt` | Modify | Add `slack_bolt` |
| `.env.example` | Modify | Add Slack env vars |
| `docs/PRODUCT-ROADMAP.md` | Modify | Document new interfaces |
| `data/dashboard-v2/` | Create (git clone) | Dashboard v2 repo clone |
| `tests/test_openai_compat.py` | Create | Tests for new `/v1/` endpoints |

---

## Wave Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| — | — | — | Plan created; awaiting user approval |

---

## Wave 1 Retrospective

_To be filled after Wave 1 completes._

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Open WebUI streaming format mismatch | Medium | Test both streaming and non-streaming; fall back to non-streaming first |
| Dashboard v2 expects different file structure | Medium | Run dashboard locally first to check, adjust WORKSPACE_DIR mount |
| Slack Socket Mode needs always-on connection | Low | Restart policy + health check; daemon thread with auto-reconnect |
| Dashboard v2 writes to workspace (security) | Low | Mount all volumes as `:ro` |
