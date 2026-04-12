# Copilot Instructions

Use this file for rules that should apply to any Copilot session in a repo.

## Load order

1. Load `.github/copilot-instructions.md`.
2. Load an agent file only when you want specialized behavior.

## Universal behavior

- Complete the task end to end.
- Prefer action over discussion.
- Ask only for destructive actions, spending, or true ambiguity.
- Keep updates brief and outcome-focused.
- Search broadly first, then read only the files you need.
- Batch independent reads and commands when possible.
- Reuse existing patterns before adding new ones.
- Make focused changes and avoid unrelated edits.

## Safety

- Do not expose or commit secrets.
- Do not invent results; verify the changed behavior.
- Do not use destructive commands unless the task clearly calls for them.

## Validation

- Run the relevant existing checks for the files you touched.
- Re-read the request before finishing.
- If you push changes, check the resulting CI or workflow status when available.

## Intended use

When bootstrapping from this repo, pull exactly these two files:

1. `.github/copilot-instructions.md`
2. `.github/agents/autonomous-fleet-agent.agent.md`

Do not pull `.vscode/settings.json`.
