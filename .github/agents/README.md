# `.github/agents/`

Agent configuration for this repository. Loaded by Copilot/agents per the
load order in [`../docs/README.md`](../docs/README.md).

## Files in this directory

| File | Scope | What it is |
| ---- | ----- | ---------- |
| [`autonomous-fleet-agent.md`](autonomous-fleet-agent.md) | SHARED (upstream) | Fleet/orchestration rules — multi-agent waves, lane assignment, checkpoints, synthesis. Load when a task involves multiple agents, independent lanes, or parallel execution. |
| `README.md` | LOCAL | This file. |

## Related instruction files (one level up)

These live in `.github/`, not here, but are part of the same instruction set:

| File | Scope | What it is |
| ---- | ----- | ---------- |
| [`../copilot-instructions.md`](../copilot-instructions.md) | SHARED (upstream) | Always-on base execution rules for every session. |
| [`../copilot-contract.json`](../copilot-contract.json) | SHARED (upstream) | Machine-readable metadata: canonical paths, deprecated paths, contract version. |

## SHARED vs LOCAL

- **SHARED (upstream)** files are refreshed from `DaveVoyles/Chat-Agents` and are
  **overwritten** on refresh — do **not** put repo-specific policy in them.
- Repo-specific guidance belongs in [`../docs/`](../docs/) (the LOCAL, read-first
  docs), starting at [`../docs/README.md`](../docs/README.md).

## Load order

1. `../copilot-instructions.md` — always.
2. `../copilot-contract.json` — when you need machine-readable metadata.
3. `../docs/README.md` — the repo entrypoint; tells you which `../docs/` files to load.
4. `autonomous-fleet-agent.md` — when the task is fleet/multi-agent orchestration.
