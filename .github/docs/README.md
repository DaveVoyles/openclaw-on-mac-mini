# OpenClaw — Agent Context Entrypoint

This directory is the repo-specific extension point for the shared Copilot bootstrap files.
Read this file first, then load only the linked docs relevant to your current task.

## Shared versus repo-specific

Keep these files shared and replaceable from the upstream repo (`DaveVoyles/Chat-Agents`):

- `.github/copilot-instructions.md`
- `.github/agents/autonomous-fleet-agent.md`
- `.github/copilot-contract.json`

Keep these files local to this repo:

- `.github/docs/README.md` (this file)
- `.github/docs/DEPLOYMENT.md` and any other docs linked below

## Always load

| Doc | When to load |
|---|---|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Any task that changes code, runs a deploy, or verifies a fix |
| [COMPLETION-RULES.md](COMPLETION-RULES.md) | Every task — before declaring work done, verify CI + assess surface updates |

## Load when relevant

| Doc | When to load |
|---|---|
| `docs/ARCHITECTURE.md` | Understanding system structure or adding new components |
| `docs/LLM-ROUTING.md` | Working on model selection, routing, or provider fallback |
| `docs/CLI_ARCHITECTURE.md` | Working on the standalone CLI (`src/openclaw_cli*.py`) |
| `docs/TESTING.md` | Running, writing, or debugging tests |
| `docs/OPERATIONS-RUNBOOK.md` | Incident response or operational procedures |
| `docs/TROUBLESHOOTING.md` | Diagnosing unexpected behavior |

## Update rules

1. Preserve existing repo-specific docs when you replace the shared upstream files.
2. Run `bash /tmp/Chat-Agents/scripts/refresh-shared-files.sh /path/to/openclaw` to pull upstream changes — it preserves `.github/docs/` automatically.
3. Merge new upstream detail intentionally instead of overwriting local docs wholesale.
4. Add links from this file to any additional docs that agents should read.
5. If this file does not exist in a consumer repo, agents should continue with the shared instructions only.
