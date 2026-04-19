# OpenClaw HTTP API Reference
<!-- Updated: 2026-04-18 -->

This document describes the HTTP API exposed by the OpenClaw bot server.

The web server is assembled in `src/discord_web.py` (`start_health_server`) and augmented by:
- `src/dashboard/routes.py` — dashboard UI and dashboard API endpoints
- `src/api/export.py` — data export and backup endpoints
- `src/api/workflow_api.py` — workflow management endpoints

The server listens on a single port (default **8765**, configurable via `HEALTH_PORT`).

---

## Auth tiers

| Tier | Description |
|------|-------------|
| 🟢 **Public** | No auth required. Intended for load balancers and uptime monitors. |
| 🔒 **Localhost only** | Restricted to `127.0.0.1` / `::1` by `_require_internal()`. Returns `403 Forbidden` for all other callers. |
| 🔑 **Bearer token** | Requires `Authorization: Bearer <token>` header. Token is compared to `DASHBOARD_API_TOKEN`. Controlled by `DASHBOARD_API_AUTH_REQUIRED`. |
| 🔐 **Webhook HMAC** | Optional; active when `WEBHOOK_REQUIRE_AUTH=true`. Requires `Authorization: Bearer <WEBHOOK_SECRET>`. |
| 🗝️ **Export API key** | Requires `Authorization: Bearer <EXPORT_API_KEY>`. Rate-limited to 10 requests/hour per key. |

---

## Health endpoints

### `GET /health`
- **Auth:** 🟢 Public
- **Description:** Liveness + readiness probe. Checks DB and vector store connectivity.
- **Response:** `200 OK`
  ```json
  {
    "status": "healthy",
    "uptime_seconds": 3600.5,
    "bot_user": "OpenClaw#1234",
    "guilds": 3,
    "python": "3.12.0",
    "discord_py": "2.3.2",
    "git_sha": "abc1234",
    "checks": {
      "db": "ok",
      "vector_store": "ok"
    },
    "ts": 1700000000.0
  }
  ```
- **Notes:** `status` is `"healthy"` when DB is reachable, `"degraded"` otherwise. `vector_store` may be `"unavailable"` without affecting the overall status code.

---

### `GET /health/llm`
- **Auth:** 🟢 Public
- **Description:** LLM provider availability, token usage, and circuit-breaker state.
- **Response:** `200 OK` (or `503` if no provider is reachable)
  ```json
  {
    "status": "ok",
    "proxy_healthy": true,
    "checks": {
      "ollama": "ok",
      "gemini": "ok",
      "copilot_proxy": "ok"
    },
    "token_usage": {},
    "circuit_state": {
      "anthropic": {"open": false},
      "copilot":   {"open": false},
      "ollama":    {"open": false},
      "openai":    {"open": false}
    }
  }
  ```

---

### `GET /health/llm/circuit`
- **Auth:** 🟢 Public
- **Description:** Lightweight circuit-breaker state snapshot — no LLM calls made.
- **Response:** `200 OK`
  ```json
  {
    "anthropic": {"open": false},
    "copilot":   {"open": false},
    "ollama":    {"open": false},
    "openai":    {"open": false}
  }
  ```

---

### `POST /health/llm/reset`
- **Auth:** 🔑 Bearer token (`DASHBOARD_API_TOKEN`)
- **Description:** Reset circuit-breaker state for one or all LLM providers.
- **Query params:**
  - `provider` (optional) — name of a single provider to reset (e.g., `?provider=ollama`). Omit to reset all.
- **Response:** `200 OK`
  ```json
  {
    "reset": ["anthropic", "copilot", "ollama", "openai"],
    "circuit_state": {
      "anthropic": {"open": false},
      "copilot":   {"open": false},
      "ollama":    {"open": false},
      "openai":    {"open": false}
    }
  }
  ```

---

### `GET /health/memory`
- **Auth:** 🟢 Public
- **Description:** Memory subsystem health — ChromaDB, QMD file, threads SQLite.
- **Response:** `200 OK` (or `503` when ChromaDB is down)
  ```json
  {
    "status": "ok",
    "checks": {
      "chromadb":   "ok",
      "qmd":        "ok",
      "threads_db": "ok"
    }
  }
  ```
- **Notes:** `status` is `"ok"` when ChromaDB is reachable, `"degraded"` otherwise. `qmd` is `"missing"` if the QMD file is absent.

---

### `GET /health/services`
- **Auth:** 🟢 Public
- **Description:** External service connectivity — Docker socket, NAS, scheduler.
- **Response:** `200 OK` (or `503` if any service is `"down"`)
  ```json
  {
    "status": "ok",
    "checks": {
      "docker":    "ok",
      "nas":       "ok",
      "scheduler": "ok (5 tasks)"
    }
  }
  ```
- **Notes:** `nas` is `"unconfigured"` when `NAS_HOST` env var is not set. `docker` is `"unavailable"` when `/var/run/docker.sock` is absent.

---

## Metrics & smoke test

### `GET /metrics`
- **Auth:** 🔒 Localhost only
- **Description:** Prometheus-format metrics for Grafana / Uptime Kuma scraping. Includes collector metrics followed by basic bot gauges.
- **Response:** `200 OK` — `text/plain; version=0.0.4; charset=utf-8`
  ```
  # HELP openclaw_up Whether the bot is running (1=up)
  # TYPE openclaw_up gauge
  openclaw_up 1

  # HELP openclaw_uptime_seconds Seconds since bot started
  # TYPE openclaw_uptime_seconds counter
  openclaw_uptime_seconds 3600.0

  # HELP openclaw_guilds Number of Discord guilds connected to
  # TYPE openclaw_guilds gauge
  openclaw_guilds 3

  # HELP openclaw_latency_ms Discord gateway latency in milliseconds
  # TYPE openclaw_latency_ms gauge
  openclaw_latency_ms 45.2
  ```

---

### `GET /smoke`
- **Auth:** 🔒 Localhost only
- **Description:** Live subsystem smoke test — makes real external API calls. Checks Gemini API, Ollama, ChromaDB, SQLite, config, and skill registry.
- **Response:** `200 OK` (all pass) or `503` (any failure)
  ```json
  {
    "status": "pass",
    "checks": {
      "gemini_api":     {"status": "pass", "latency_ms": 312},
      "ollama":         {"status": "skipped", "reason": "LOCAL_LLM_ENABLED=false"},
      "chromadb":       {"status": "pass", "latency_ms": 5},
      "memory_sqlite":  {"status": "pass", "threads": 42},
      "config":         {"status": "pass"},
      "skill_registry": {"status": "pass", "skill_count": 18}
    },
    "timestamp": "2026-04-18T12:00:00Z"
  }
  ```

---

## Webhook ingestion

### `POST /webhook/{source}`
- **Auth:** 🔐 Webhook HMAC (when `WEBHOOK_REQUIRE_AUTH=true`; open when `false`)
- **Description:** Receive inbound webhooks from Sonarr, Radarr, Plex, qBittorrent, and other services. Formats a human-readable embed and posts it to `ALERT_CHANNEL_ID`. Error-bearing payloads trigger an async LLM analysis.
- **Path params:** `source` — lowercase name of the upstream service (e.g., `sonarr`, `plex`)
- **Request body:** Arbitrary JSON from the upstream service
- **Response:** `200 OK`
  ```json
  {"ok": true}
  ```
- **Errors:**
  - `401` — missing or invalid bearer token (when auth is enabled)
  - `503` — `WEBHOOK_REQUIRE_AUTH=true` but `WEBHOOK_SECRET` is not set

---

## Management actions

### `POST /api/trigger-scan`
- **Auth:** 🔑 Bearer token (`DASHBOARD_API_TOKEN`)
- **Description:** Immediately schedule a proactive insight scan in the background (timeout: 300 s).
- **Request body:** None
- **Response:** `200 OK`
  ```json
  {"status": "scan triggered"}
  ```

---

## CLI self-update

### `GET /cli-update/{filename}`
- **Auth:** 🟢 Public
- **Description:** Serve a CLI source file for self-update. Only files in the allowlist are served: `openclaw_cli.py`, `openclaw_cli_actions.py`, `openclaw_cli_sessions.py`, `subprocess_utils.py`.
- **Path params:** `filename` — one of the four allowed filenames
- **Response:** `200 OK` — `text/plain` file content
- **Error:** `404` if the filename is not in the allowlist

---

### `GET /cli-update/meta`
- **Auth:** 🟢 Public
- **Description:** Return SHA-256 checksums of CLI source files for update checking.
- **Response:** `200 OK`
  ```json
  {
    "openclaw_cli.py":          "e3b0c44298fc1c149afb...",
    "openclaw_cli_actions.py":  "d8e8fca2dc0f896fd7cb...",
    "openclaw_cli_sessions.py": "3a7bd3e2360a3d29eea4...",
    "subprocess_utils.py":      "f1d2d2f924e986ac86fd..."
  }
  ```

---

## OpenAI-compatible API

### `GET /v1/models`
- **Auth:** 🟢 Public
- **Description:** Return an OpenAI-compatible model list, for use with Open WebUI and other compatible clients.
- **Response:** `200 OK`
  ```json
  {
    "object": "list",
    "data": [
      {"id": "openclaw-auto",      "object": "model", "created": 1700000000, "owned_by": "openclaw"},
      {"id": "openclaw-gemini",    "object": "model", "created": 1700000000, "owned_by": "openclaw"},
      {"id": "openclaw-copilot",   "object": "model", "created": 1700000000, "owned_by": "openclaw"},
      {"id": "openclaw-openai",    "object": "model", "created": 1700000000, "owned_by": "openclaw"},
      {"id": "openclaw-anthropic", "object": "model", "created": 1700000000, "owned_by": "openclaw"}
    ]
  }
  ```

---

### `POST /v1/chat/completions`
- **Auth:** 🟢 Public
- **Description:** OpenAI-compatible chat completions endpoint. Supports both standard and streaming responses. Routes the request through the bot's LLM stack.
- **Request body:**
  ```json
  {
    "model": "openclaw-auto",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ],
    "stream": false
  }
  ```
  - `model` — one of `openclaw-auto`, `openclaw-gemini`, `openclaw-copilot`, `openclaw-openai`, `openclaw-anthropic`
  - `messages` — array of `{role, content}` objects; last message is the prompt
  - `stream` — `true` for SSE streaming, `false` (default) for batch response
- **Non-streaming response:** `200 OK`
  ```json
  {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "openclaw-auto",
    "choices": [{
      "index": 0,
      "message": {"role": "assistant", "content": "Hi there!"},
      "finish_reason": "stop"
    }],
    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 42}
  }
  ```
- **Streaming response:** `text/event-stream` SSE, each chunk as `data: {...}\n\n`, terminated with `data: [DONE]\n\n`
- **Errors:** `400` invalid JSON or missing messages; `500` LLM error

---

## File operations

### `POST /upload`
- **Auth:** `X-OpenClaw-Key` header (compared to `OPENCLAW_UPLOAD_KEY` env var; open if var is unset)
- **Description:** Accept a file upload and write it to the AI files directory (`/ai-files/`). Request must be `multipart/form-data` with a `file` field.
- **Allowed extensions:** `.txt`, `.md`, `.pdf`, `.csv`, `.json`, `.py`, and others defined in `_ALLOWED_UPLOAD_EXTENSIONS`
- **Response:** `200 OK`
  ```json
  {"status": "ok", "filename": "example.txt", "size": 1024}
  ```
- **Errors:** `400` bad multipart body; `401` wrong key; `415` extension not allowed

---

### `GET /dropbox/callback`
- **Auth:** 🟢 Public (OAuth2 redirect target)
- **Description:** Complete the Dropbox OAuth2 authorization flow. Called by Dropbox after the user grants access. Exchanges the `code` query parameter for a token and renders an HTML confirmation page.
- **Query params:** `code`, `state` (from Dropbox); `error` on failure
- **Response:** `200 OK` HTML confirmation page, or `400` HTML error page

---

## Dashboard (UI)

These endpoints serve the web dashboard. They share the same port.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard home (HTML) |
| `GET` | `/dashboard` | Dashboard home (HTML, alias) |
| `GET` | `/tech-guide` | Technology guide (HTML) |
| `GET` | `/guide` | Redirect → `/tech-guide` |
| `GET` | `/terminal` | Web terminal (HTML) |
| `GET` | `/onboarding` | Onboarding guide (HTML) |
| `GET` | `/parents-guide` | Parents guide (HTML) |
| `GET` | `/webui-guide` | Open WebUI guide (HTML) |
| `GET` | `/install` | OpenClaw CLI installer script (shell) |
| `GET` | `/install-remote` | Remote CLI installer (shell) |
| `GET` | `/install.ps1` | Windows CLI installer (PowerShell) |
| `GET` | `/downloads/openclaw_cli.py` | CLI Python file download |
| `GET` | `/downloads/openclaw-cli-support/{name}` | CLI support file download |
| `GET` | `/downloads/openclaw-cli-installer.sh` | CLI installer download |

---

## Dashboard API

These JSON API endpoints power the dashboard UI. Action endpoints (POST/DELETE) require a bearer token when `DASHBOARD_API_AUTH_REQUIRED=true`.

**Auth:** 🟢 Public for `GET` reads; 🔑 Bearer token for write/action endpoints.

### Status & overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard` | Aggregated dashboard stats |
| `GET` | `/api/status` | Bot status summary |
| `GET` | `/api/runs` | Recent LLM run history |
| `GET` | `/api/errors` | Recent error log |
| `GET` | `/api/response-stats` | Response timing statistics |
| `GET` | `/api/config-status` | Configuration status |
| `GET` | `/api/quota-status` | API quota usage |

### Memory & channels

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/memories` | Memory store contents |
| `GET` | `/api/threads` | Conversation threads |
| `GET` | `/api/channel-memory/inspect` | Inspect per-channel memory |
| `POST` | `/api/channel-memory/action` | Mutate channel memory 🔑 |
| `GET` | `/api/channel-profile/recommendations` | Channel personalization recommendations |
| `POST` | `/api/channel-profile/recommendations/action` | Apply recommendation 🔑 |

### Agent & plans

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agent/sessions` | List autonomous agent sessions |
| `GET` | `/api/agent/sessions/{session_id}` | Session detail |
| `POST` | `/api/agent/sessions/{session_id}/interventions/{action}` | Intervene in a session 🔑 |
| `GET` | `/api/plans` | List agent plans |
| `GET` | `/api/plans/{plan_id}` | Plan detail |
| `POST` | `/api/agent/ask` | Send a one-shot question to the agent 🔑 |
| `POST` | `/api/agent/ask/stream` | SSE-streaming agent question |
| `POST` | `/api/recap/generate` | Generate a weekly recap 🔑 |

### Tasks & schedules

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tasks` | All task statuses |
| `GET` | `/api/tasks/{source}/{task_id}` | Task detail |
| `GET` | `/api/schedules` | Scheduled task list |
| `POST` | `/api/schedules/{task_id}` | Update schedule 🔑 |
| `POST` | `/api/schedules/{task_id}/toggle` | Enable/disable schedule 🔑 |
| `DELETE` | `/api/schedules/{task_id}` | Delete schedule 🔑 |

### Intelligence & quality

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/quality-evals` | Quality evaluation runs |
| `GET` | `/api/quality-metrics` | Aggregated quality metrics |
| `GET` | `/api/search-stats` | Web search usage stats |
| `GET` | `/api/skill-stats` | Skill invocation stats |
| `GET` | `/api/dream-health` | Dream (background reflection) health |
| `GET` | `/api/research` | Research job history |
| `GET` | `/api/goals` | Active and completed goals |

### Approvals

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/approvals` | Pending approval requests |
| `POST` | `/api/approvals/{request_id}/decision` | Approve or reject 🔑 |

### Graph & topology

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/knowledge-graph` | Knowledge graph data |
| `GET` | `/api/topology` | Service topology map |

### SMS

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sms/settings` | SMS configuration |
| `POST` | `/api/sms/settings` | Update SMS settings 🔑 |
| `GET` | `/api/sms/status` | SMS provider status |
| `GET` | `/api/sms/history` | Inbound/outbound SMS history |

---

## Export & backup API

All endpoints require an `Authorization: Bearer <EXPORT_API_KEY>` header (🗝️). Rate limit: **10 requests/hour** per key.

### `GET /api/export/conversations`
- **Description:** Export conversation history.
- **Query params:**
  - `format` — `csv` (default), `json`, or `parquet`
  - `days` — number of days to include (default: `30`)
  - `channel_id` (optional) — filter to a specific channel
- **Response:** File download with `Content-Disposition` header. `X-Export-Records` header contains the record count.

---

### `GET /api/export/trends`
- **Description:** Export trend/metric data.
- **Query params:**
  - `format` — `csv` (default), `json`, or `parquet`
  - `days` — number of days to include (default: `30`)
  - `metric` (optional) — filter by topic
  - `category` (optional) — filter by category
- **Response:** File download.

---

### `POST /api/reports/generate`
- **Description:** Generate a report (PDF).
- **Request body:**
  ```json
  {"report_type": "weekly_summary", "data": {}}
  ```
- **Response:** PDF file download.

---

### `GET /api/backups/list`
- **Description:** List all available backups with size and creation time.
- **Response:** `200 OK`
  ```json
  {
    "backups": [
      {"name": "backup_20260418.tar.gz", "path": "/app/data/backups/...", "size_bytes": 10240, "created_at": 1700000000.0}
    ],
    "status": {}
  }
  ```

---

### `POST /api/backups/create`
- **Description:** Trigger an immediate backup.
- **Request body:**
  ```json
  {"upload_to_nas": true}
  ```
- **Response:** `200 OK` — backup result object from `backup_manager`

---

## Workflow API

No auth by default (routes registered without the action-auth wrapper).

### `GET /api/workflows/templates`
- **Description:** List available workflow templates.
- **Response:** `200 OK` `{"templates": [...], "count": N}`

### `POST /api/workflows/from-template`
- **Description:** Create a workflow from a named template.
- **Request body:** `{"template": "daily_report", "created_by": "api"}`
- **Response:** `201 Created` — workflow object; `404` if template not found

### `POST /api/workflows`
- **Description:** Create a new workflow.
- **Request body:**
  ```json
  {
    "name": "My Workflow",
    "description": "",
    "tasks": [],
    "error_handling": "fail_fast",
    "rollback_on_error": false,
    "created_by": "api"
  }
  ```
- **Response:** `201 Created` — workflow object

### `GET /api/workflows`
- **Description:** List all workflows.
- **Response:** `200 OK` `{"workflows": [...], "count": N}`

### `GET /api/workflows/{id}`
- **Description:** Get workflow details.
- **Response:** `200 OK` — workflow object; `404` if not found

### `PUT /api/workflows/{id}`
- **Description:** Update workflow fields (`name`, `description`, `error_handling`, `rollback_on_error`).
- **Request body:** Partial workflow object
- **Response:** `200 OK` — updated workflow object; `404` if not found

### `DELETE /api/workflows/{id}`
- **Description:** Delete a workflow.
- **Response:** `200 OK` `{"message": "Workflow deleted"}`; `404` if not found

### `POST /api/workflows/{id}/execute`
- **Description:** Execute a workflow immediately.
- **Request body:** `{"context": {}}` (optional)
- **Response:** `200 OK`
  ```json
  {
    "execution_id": "exec-abc123",
    "workflow_id": "wf-abc123",
    "status": "completed",
    "started_at": 1700000000.0,
    "completed_at": 1700000000.5,
    "task_results": {},
    "errors": []
  }
  ```

---

## Error response format

All error responses use a consistent JSON envelope:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {}
}
```

Common status codes:

| Code | Meaning |
|------|---------|
| `400` | Bad request / invalid JSON |
| `401` | Missing or invalid auth |
| `403` | Forbidden (localhost-only endpoint) |
| `404` | Resource not found |
| `415` | Unsupported media type |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `503` | Subsystem unavailable |

---

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `HEALTH_PORT` | All endpoints | Port the server listens on (default: `8765`) |
| `WEBHOOK_SECRET` | `POST /webhook/{source}` | Shared secret for HMAC webhook auth |
| `WEBHOOK_REQUIRE_AUTH` | `POST /webhook/{source}` | Enable webhook bearer-token validation (`true`/`false`) |
| `DASHBOARD_API_TOKEN` | Action endpoints (🔑) | Bearer token for management operations |
| `DASHBOARD_API_AUTH_REQUIRED` | Action endpoints (🔑) | Enable dashboard action auth (`true`/`false`) |
| `EXPORT_API_KEY` | Export / backup endpoints | Bearer token for data export API |
| `ALERT_CHANNEL_ID` | `POST /webhook/{source}` | Discord channel ID for webhook notifications |
| `OPENCLAW_UPLOAD_KEY` | `POST /upload` | Shared secret for file upload auth |
| `NAS_HOST` | `GET /health/services` | NAS hostname for connectivity check |
| `QMD_PATH` | `GET /health/memory` | Path to QMD JSON file (default: `/app/data/qmd.json`) |
| `GOOGLE_API_KEY` | `GET /health/llm` | Gemini API key presence check |
