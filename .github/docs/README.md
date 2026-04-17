# OpenClaw — Agent Context Entrypoint

This directory contains repo-specific guidance for AI agents working in this codebase.
Read this file first, then load only the linked docs relevant to your current task.

## Always load

| Doc | When to load |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Any task that changes code, runs a deploy, or verifies a fix |

## Load when relevant

| Doc | When to load |
|---|---|
| `docs/ARCHITECTURE.md` | Understanding system structure or adding new components |
| `docs/LLM-ROUTING.md` | Working on model selection, routing, or provider fallback |
| `docs/CLI_ARCHITECTURE.md` | Working on the standalone CLI (`src/openclaw_cli*.py`) |
| `docs/TESTING.md` | Running, writing, or debugging tests |
| `docs/OPERATIONS-RUNBOOK.md` | Incident response or operational procedures |
| `docs/TROUBLESHOOTING.md` | Diagnosing unexpected behavior |
