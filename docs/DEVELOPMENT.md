# OpenClaw CLI — Developer Onboarding Guide
<!-- Updated: 2026-04-18 -->


Quick start for developers contributing to the OpenClaw CLI. Start with [`docs/START-HERE.md`](START-HERE.md) for contributor wayfinding, then read [`docs/AGENT-GUIDE.md`](AGENT-GUIDE.md) for a system-wide orientation.

> **Roadmap note:** future CLI UX waves are tracked in [`docs/PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md). Use this guide for setup and validation, not as the planning source of truth.

---

## Prerequisites

- **Python 3.12+** (the production environment uses 3.12 via Docker; match locally)
- **macOS or Linux** (Windows is untested)
- **SSH access** to the Mac Mini server (192.168.1.93) for integration testing
- **Git**

---

## Clone and Setup

```bash
git clone git@github.com:DaveVoyles/openclaw.git
cd openclaw

# Create and activate the development virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install runtime + test dependencies
pip install -r requirements.txt -r requirements-test.txt
```

> **Two virtualenvs exist in this repo:**
> - `.venv` — local development (use this)
> - `.venv-test` — Docker-based CI runner used by `run_tests.sh`

---

## Running the CLI Locally

```bash
# Point to the Mac Mini server
python3 src/openclaw_cli.py --server http://192.168.1.93:8765

# Or point to a local dev server
python3 src/openclaw_cli.py --server http://localhost:8765

# With an explicit token
python3 src/openclaw_cli.py --server http://localhost:8765 --token YOUR_TOKEN
```

The CLI enters an interactive REPL. Type `/help` to see all commands.

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `OPENCLAW_URL` | Base URL of the bot server | `http://192.168.1.93:8765` |
| `OPENCLAW_TOKEN` | Auth token for server requests | (keychain or prompt) |
| `OPENCLAW_USER_NAME` | Display name for the current user | (auto-detected) |
| `OPENCLAW_CLIENT_NAME` | Client identifier sent with requests | (hostname) |
| `OPENCLAW_CLI_HOME` | Override install directory for standalone mode | (auto-detected) |
| `PLANS_DIR` | Override local plans directory | `data/plans/` |
| `MC_TASKS_FILE` | Override local tasks file path | `data/tasks.json` |

Set them in your shell profile or `.env` file (not committed to git):

```bash
export OPENCLAW_URL="http://192.168.1.93:8765"
export OPENCLAW_TOKEN="your-token-here"
```

---

## Make Targets

| Command | What It Does |
|---------|-------------|
| `make test` | Run pytest (quick, stop on first failure) |
| `make test-cli` | Run CLI + dashboard tests only, no conftest overhead |
| `make test-verbose` | Run pytest with verbose output |
| `make lint` | Run `ruff` linter on `src/` and `tests/` |
| `make format` | Auto-fix formatting with `ruff` |
| `make type-check` | Type check with `pyright` (falls back to `mypy`) |
| `make build` | Build Docker image |
| `make deploy` | Rebuild + restart Docker container |
| `make deploy-cli` | Push CLI files to macbook via SSH/SCP |
| `make verify-deploy` | Confirm deployed CLI version on macbook |
| `make clean` | Remove `__pycache__`, `.pyc`, caches |

### Running Tests

```bash
# Recommended (fast, stops on first failure)
make test

# Equivalent pytest command (excluding 5 known-flaky tests)
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_spinner_reduced_motion_heartbeat or test_update_check_background_thread or test_exec_streaming_output or test_research_stream_progress or test_macro_run_async_dispatch)" \
  -q

# Run inside Docker (matches CI environment exactly)
./run_tests.sh
```

---

## ⛔ CI Gate Policy — Read Before Starting Any Wave of Work

**CI must be green (or at baseline) before beginning new work.**

This rule exists because failing tests accumulate silently and make it impossible to tell whether a new change broke something or the build was already broken. Always check first.

### Before starting any new wave, feature, or fix

1. **Check CI status:**
   ```bash
   gh run list --limit 5
   ```

2. **If the latest run is failing**, investigate before writing new code:
   ```bash
   gh run view <RUN_ID> --log | grep "FAILED"
   ```

3. **Classify each failure** as either:
   - **Pre-existing** — was already failing before your change (acceptable to leave, document it)
   - **New regression** — introduced by recent work (must fix before proceeding)

4. **Fix all new regressions** before starting the next wave. Pre-existing failures may be left if they are tracked and acknowledged.

5. **After pushing fixes**, confirm the new run does not add failures:
   ```bash
   gh run watch <RUN_ID>
   ```

### CI baseline

The Mac Mini self-hosted runner runs the full test suite on every push. The **current known-failing baseline** is tracked in `docs/TESTING.md`. If your push increases the failure count above baseline, stop and fix before continuing.

### Quick reference

| Situation | Action |
|-----------|--------|
| CI green | ✅ Safe to start new work |
| CI failing, failures are pre-existing | ✅ Safe to start (document baseline) |
| CI failing, failures are new | 🛑 Fix regressions first |
| CI failing, cause unknown | 🛑 Investigate before starting |

---

### Linting

```bash
make lint
# Equivalent:
.venv/bin/ruff check src/ tests/
```

### Type Checking

```bash
make type-check
# Equivalent (pyright preferred, mypy fallback):
.venv/bin/pyright src/
```

---

## Deploying the CLI

```bash
# Deploy to the macbook (requires SSH alias 'macbook' configured)
make deploy-cli

# Verify the deployed version
make verify-deploy
```

The deploy script (`scripts/install_openclaw_cli_remote.sh`) uses SCP to push all `src/openclaw_cli*.py` files to `~/.local/share/openclaw-cli/` on the target host. The remote Mac does **not** need the git repo.

---

## Key Files

| Path | Purpose |
|------|---------|
| `src/openclaw_cli.py` | Main REPL, command dispatch (~13,300 lines) |
| `src/openclaw_cli_router.py` | Routing logic and intent classification |
| `src/openclaw_cli_render.py` | Response rendering and ANSI output |
| `src/openclaw_cli_sessions.py` | Session persistence and event logging |
| `src/openclaw_cli_auth.py` | Token management, keychain, error types |
| `config/config.yaml` | Bot server configuration |
| `plugins/` | Plugin system for extending commands |
| `skills/` | Skill library (both Python and SKILL.md format) |
| `tests/test_openclaw_cli.py` | 440+ CLI tests |
| `Makefile` | All dev workflow commands |

---

## Debugging

```bash
# Enable verbose logging
export OPENCLAW_LOG_LEVEL=DEBUG
python3 src/openclaw_cli.py --server http://localhost:8765

# View container logs (Mac Mini)
docker logs openclaw --tail 50 -f

# Health check
curl -s http://192.168.1.93:8765/health | python3 -m json.tool
```

---

## Build Version

The CLI tracks its build wave in `src/openclaw_cli.py`:

```python
_CLI_BUILD = "wave36"  # updated with each UX wave batch
```

Check which version is deployed:

```bash
make verify-deploy
```

---

## Testing New Features

### Streaming (W12)

To test Gemini streaming locally:

```bash
export GEMINI_STREAMING_ENABLED=true
export PROVIDER_STREAM_INTERVAL_CHARS=50  # lower for faster visual feedback
```

Then start the bot and send a question that routes to Gemini.

### Alert System (W13)

To test alert severity routing:

```bash
export OWNER_USER_ID=<your-discord-user-id>
```

CRITICAL alerts will DM that user. Use the `⏰` reaction to snooze, `✅` to resolve.

### Memory Recall Domain Guard (W6)

```bash
export RECALL_DOMAIN_GUARD_STRICT=true
```

Enables strict domain suppression — only memories relevant to the query domain are injected.
