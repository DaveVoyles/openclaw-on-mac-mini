# OpenClaw — Agent Context Entrypoint

This directory is the repo-specific extension point for the shared Copilot bootstrap files.
Read this file first, then load only the linked docs relevant to your current task.

## ⚠️ This repository is PUBLIC — never commit private data

This repo is published on GitHub at `DaveVoyles/openclaw-on-mac-mini`. **Every file you commit is world-readable.** Treat all tracked content as public.

**Before every commit or push, confirm you are NOT introducing:**

- Real personal emails — use `you@example.com` in docs/examples (never `you@example.com` or any real address)
- Live workspace/chat invite links — Slack `join.slack.com/t/.../shared_invite/...`, Discord invites, etc. Replace with "ask the owner for a current invite link"
- Real credentials, tokens, or keys — use placeholders only (`xoxb-YOUR-BOT-TOKEN`, `sk-...`, `ghp_...`). See the shared Security and Credential Policy
- Real phone numbers, home addresses, or other non-public PII
- Raw API responses, logs, chat transcripts, or memory dumps that contain personal content

**Private/runtime data must stay OUT of git** — these are already in `.gitignore`; never force-add them:

- `.env`, `.env.local`, `*.key`
- `data/memory/`, `data/vault/`, `data/logs/`, `data/audit/`, `data/backups/`, `data/exports/`, `data/chromadb/`, `data/ssh/`
- `data/user_dropbox_tokens.json`, `data/health_history.db`, `data/slack_file_history.json`

**`.env.example`** must contain only placeholders — never real values. Keep it in sync with `.env` (run `make validate-env`).

**Acceptable but don't expand gratuitously:** private LAN IPs (`192.168.x.x`) and personal home paths (`/Users/davevoyles/...`) already exist in config/docs. They are low-risk (only meaningful inside the owner's network) — fine to keep, but avoid scattering new ones.

**If you discover private data already committed:** stop, tell the user immediately, and do **not** scrub git history without explicit instruction. If it is a secret, it must be rotated.

**Quick pre-commit scan:**

Run the automated scanner (also enforced on every push/PR by `.github/workflows/privacy-scan.yml`):

```bash
make scan-private          # or: python3 scripts/scan_private_data.py
```

It flags the owner's personal email, live Slack workspace invites, real secret
tokens, and tracked credential files — while ignoring placeholders, test
fixtures, and acceptable homelab data (LAN IPs, personal paths). Exit code 1
means private data was found.

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
| [NAS-ACCESS.md](NAS-ACCESS.md) | Any task involving NAS files, ROMs, share links, or SSH to the NAS |

## Update rules

1. Preserve existing repo-specific docs when you replace the shared upstream files.
2. Run `bash /tmp/Chat-Agents/scripts/refresh-shared-files.sh /path/to/openclaw` to pull upstream changes — it preserves `.github/docs/` automatically.
3. Merge new upstream detail intentionally instead of overwriting local docs wholesale.
4. Add links from this file to any additional docs that agents should read.
5. If this file does not exist in a consumer repo, agents should continue with the shared instructions only.

## Archive

Superseded wave-plan and spec files are stored in [archive/](archive/) — do not load unless specifically reviewing historical plans.
