# Deployment Rules — OpenClaw (Mac Mini + MacBook)

> **This repo has a two-target deploy model. A `git push` alone is never enough.**

## Architecture

| Target | Machine | What lives there |
|---|---|---|
| Server | Mac Mini (`192.168.1.93`) | Docker container `openclaw`, `src/` volume-mounted read-only |
| CLI | MacBook (`macbook` SSH alias) | `~/.local/share/openclaw-cli/` — standalone Python install |

## Why `docker restart` is required for server-side changes

The container mounts `./src:/app/src:ro` so changed files are visible on disk, but **Python caches module imports at process startup**. The running process never sees updated `.py` files until the container restarts.

**Any change to a server-side file** (`src/bot.py`, `src/model_router.py`, `src/discord_web.py`, `src/llm/`, `src/config.py`, etc.) requires a container restart to take effect. This is the most common cause of "I pushed a fix but nothing changed."

## Deploy commands (run from Mac Mini)

```bash
make ship          # safe default — pull + restart server + update MacBook CLI
make ship-server   # server only: git pull, write git SHA, docker restart openclaw
make ship-cli      # CLI only: SCP openclaw_cli*.py files to MacBook
make verify-deploy # confirm: CLI build label + /health JSON with git_sha field
```

## Which target to use

| Changed file(s) | Command |
|---|---|
| `src/openclaw_cli*.py`, `src/subprocess_utils.py` | `make ship-cli` |
| Any other `src/` file | `make ship-server` |
| Both, or unsure | `make ship` |

When in doubt, run `make ship` — it always does the right thing.

## Verify the deploy landed

```bash
make verify-deploy
# Shows CLI build label + /health JSON including "git_sha"
```

Cross-check: `git rev-parse --short HEAD` must match the `git_sha` in `/health`.

The `/health` endpoint at `http://192.168.1.93:8765/health` always includes `git_sha` so you can confirm which commit the server is running.

## Deploy stop-condition

A task that changes code is **not complete** until:

1. `git push` succeeded
2. `make ship` (or the correct sub-target) ran without errors
3. `make verify-deploy` shows the expected build label and git SHA
4. The fixed behavior is confirmed working (re-run the failing scenario)
