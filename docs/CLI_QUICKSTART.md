# OpenClaw CLI Quick Start

Use this guide when you want the OpenClaw launcher to behave like a real terminal app instead of calling `oc-ask` manually every time.

## Preferred command model

- **`OpenClaw`** — preferred interactive launcher in shell setup and installer flows
- **`openclaw`** — canonical executable name for direct one-shot commands and Python packaging
- **`oc-ask` / `oc-chat`** — compatibility shortcuts that remain supported, but are no longer the primary documented path

## Install from a running OpenClaw service

### On the same LAN

```bash
curl -fsSL http://192.168.1.93:8765/install | bash
```

### Remote (HTTPS)

```bash
curl -fsSL https://openclaw.davevoyles.synology.me/install | bash
```

The installer auto-detects **zsh** vs **bash** and updates the matching rc file by default. You can override that behavior with:

```bash
curl -fsSL http://192.168.1.93:8765/install | bash -s -- --shell bash
curl -fsSL http://192.168.1.93:8765/install | bash -s -- --rc-file ~/.bashrc
```

The installer automatically runs `openclaw --health` after setup as a smoke check. If you only want to lay down the files, pass:

```bash
curl -fsSL http://192.168.1.93:8765/install | bash -s -- --skip-verify
```

Then reload your shell:

```bash
source ~/.zshrc   # or ~/.bashrc if bash was selected
```

The shell bootstrap is currently **supported for bash and zsh**. If you use fish or another shell, install the CLI normally and source the generated launcher script manually instead of relying on automatic rc-file edits.

The installer now downloads the **core CLI module set** (`openclaw_cli.py` plus its local support modules) instead of assuming a single Python file is enough for standalone use. If you want the full repo-powered feature set with every optional dependency available, use the developer install path below.

## First run

```bash
OpenClaw
openclaw "what changed overnight?"
openclaw analyze --cwd ~/openclaw @README.md "summarize the repo"
openclaw research "best async Python patterns in 2026"
openclaw write --title "Weekly recap" "Draft the report"
openclaw watch --cwd ~/openclaw --on-change --iterations 5 @README.md "watch for repo regressions"
openclaw exec -- git status
openclaw exec --plan-id <plan-id> -- make test
openclaw edit notes.md --content "# Title"
openclaw edit notes.md --plan-id <plan-id> --task-id <task-id> --content "# Title"
openclaw session list
openclaw session show <session-id>
openclaw --version
openclaw --health
openclaw --health --json
```

`openclaw --health` now prints a concise operator-friendly summary by default. Add `--json` when you want the raw `/health` payload.

## Hybrid REPL — in-session slash commands

The interactive session (`OpenClaw` / `openclaw chat`) is a hybrid REPL: natural-language prompts and slash commands coexist in the same input stream. Every slash command is handled locally before the input reaches the LLM.

### Lifecycle controls

| Command | What it does |
| --- | --- |
| `/help` | Print the full in-REPL command reference |
| `/clear` | Reset the current conversation history (keeps the session alive) |
| `/quit` | Exit the REPL and return to the shell |
| `/update` | Self-upgrade the CLI without leaving the session (see [Updating](#updating) below) |
| `/autoroute [on\|off]` | Show whether high-confidence freeform prompts can auto-route, or toggle that behavior for this session |
| `/rollback last` | Restore the newest routed safety checkpoint when auto-rollback is available (currently text file edits only) |

### Session & context inspection

| Command | What it does |
| --- | --- |
| `/session` | Show a compact summary of the current session (ID, plan/task linkage, file count, automation state) |
| `/context` | Show the active working directory, tracked file list, linked plan/task IDs, and a bounded preview of the grounding OpenClaw will inject into the next analyze/write/research-style action |
| `/outputs` | List saved outputs for the active session with 1-based indices |
| `/outputs <index>` or `/outputs <filename>` | Preview a saved artifact inline without leaving the REPL |
| `/events [n]` | Show recent session events, including structured `route` events for auto-routed prompts |
| <code>/plan [&lt;id&gt;&#124;unlink]</code> | Show, link, or unlink the session plan |
| <code>/task [&lt;id&gt;&#124;unlink]</code> | Show, link, or unlink the session task |

### In-REPL action commands

These mirror the top-level `openclaw` subcommands but run inside the current session so all context (cwd, tracked files, plan/task linkage) is inherited automatically.

| Command | What it does |
| --- | --- |
| `/analyze <goal>` | Run a workspace-aware analysis using the current session context |
| `/research <query>` | Run the research agent on a query and save the report into the session |
| `/write <task>` | Generate a markdown document from a writing task and save it as a session output |
| `/exec [--] <command>` | Run a shell command with risk-aware approval prompts and session tracking |
| `/edit <path> [--content TEXT] [--append TEXT]` | Inspect or write a file; shows a unified diff before applying changes |

### Freeform auto-routing and plan decomposition

When auto-route is on (the default), OpenClaw can turn high-confidence freeform prompts into the matching slash command before the LLM sees them. Prompts that clearly look like a single action — for example a command to analyze, research, write, execute, or edit something — are routed through the existing in-REPL handlers and announced inline so you can see the equivalent slash command that ran. That routed extraction is now smarter about quoted or fenced shell commands for `/exec`, and about edit-style prompts that clearly describe an append or replace operation against a detected file target.

When a prompt clearly contains ordered actions, OpenClaw can also decompose it into a multi-step plan candidate. High-confidence plan candidates are created as linked plans, attached to the active session before execution starts, and auto-run step-by-step in the REPL. Each step prints inline progress such as `[1/3] /research ...` so you can see exactly which routed step is running.

When the session already has linked plan/task context, the router can also use that grounding to sharpen ambiguous prompts. Active plan, task, and current-step context can improve route precision, and explicit references like `step 2` or `step three` can resolve against linked plan data when it is available.

If the router is unsure — or the linked plan/task data is missing, unavailable, or still ambiguous — the prompt stays in normal chat. That keeps ambiguous requests conversational instead of forcing a tool action. Use `/autoroute off` to keep every prompt in chat for the current session, or `/autoroute on` to re-enable high-confidence routing.

High/critical routed `/exec` and `/edit` steps still use the same approval checks as typing those slash commands directly, so auto-routing does not bypass approvals. Routed multi-step actions also capture safety checkpoints. `/rollback last` restores the newest recoverable routed edit checkpoint, but automatic rollback currently supports text file edits only. Routed `/exec` checkpoints stay manual-recovery only: OpenClaw prints the pre-action workspace signature so you can recover manually if needed. Only the latest five routed checkpoints are retained per session. Each routed turn is also saved as a structured `route` event that you can inspect with `/events` or `openclaw session show <session-id>`.

`/outputs` is the fastest way to revisit saved artifacts mid-session: run `/outputs` to see the active session's saved files, then `/outputs 1` or `/outputs recap.md` to preview one inline. The listing is 1-based so the index shown in the list is the index you pass back.

`/context` now includes a bounded preview of the exact grounding block the next `analyze`, `write`, or `research`-style action will inherit, so you can sanity-check the injected cwd, tracked files, plan/task framing, and recent saved-output context before you run the next step.

`/plan <id>` and `/task <id>` still link immediately inside the REPL, but they now surface validation status more explicitly. When local validation sources are available, OpenClaw reports whether the linked plan or task was confirmed. When those validation sources are unavailable in the current install, OpenClaw still records the link and tells you validation was unavailable.

## Agent-oriented commands

- `openclaw analyze` — build workspace-aware prompts from `--cwd`, `--file`, and `@path` references
- `openclaw research` — run the built-in research agent and save a report into the local session
- `openclaw write` — draft markdown output and save it to a session artifact or explicit file
- `openclaw watch` — run a bounded, resumable automation loop with saved checkpoints and optional `--on-change` gating
- `openclaw exec` — run tracked shell commands with higher-risk approval prompts; pass `--plan-id <id>` or `--task-id <id>` to tag the command to a plan or task
- `openclaw edit` — preview or apply text edits with unified diffs; supports `--plan-id` / `--task-id` tagging
- `openclaw session list|show|resume|export` — inspect resumable local CLI sessions (`show <session-id>` prints full metadata, plan/task linkage, tracked files, automation state, and watch checkpoint/retry history)
- `openclaw plan <subcommand>` — manage agent-loop plans from the terminal (see below)

### Plan subcommands

| Subcommand | What it does |
| --- | --- |
| `openclaw plan create "<goal>" [--steps-text "..."]` | Create a new plan; prints the `plan_id` |
| `openclaw plan list [--status all\|in-progress\|completed\|interrupted]` | List plans with ID, status, progress, and goal |
| `openclaw plan show <plan_id>` | Show full step detail for a plan |
| `openclaw plan resume <plan_id>` | Resume an interrupted plan from the next pending step |
| `openclaw plan cancel <plan_id>` | Cancel an active plan (marks interrupted; safe to resume later) |

`openclaw plan` requires the `agent_loop` module, which is present in a full repo checkout or package install but **not** in a standalone thin install. See [thin-install limitations](#thin-install-limitations) below.

### Watch progress, retry, and saved state semantics

`openclaw watch` persists a watch-state file keyed to the session ID. The state file tracks:

- **`progress_log`** — rolling record of each iteration's outcome (kept to the last 20 entries)
- **`retry_history`** — record of automatic transient-error retries with timestamps and backoff delays
- **`retry_limit`** — maximum consecutive automatic retries before the loop stops (default: **3**); backoff is capped exponential (1 s → 2 s → 4 s → 8 s max)
- **`checkpoints`** / **`active_checkpoint`** — per-iteration checkpoint snapshots used to reconstruct state on resume
- **`workspace_signature`** — content hash used by `--on-change` to skip iterations when files have not changed

When a transient error occurs (network blip, timeout), `openclaw watch` retries automatically up to `retry_limit` times before stopping. Permanent errors stop immediately. On `--resume <session-id>` the saved state is loaded and the loop continues from the last checkpoint, replaying the `progress_log` summary to the terminal.

### Thin-install limitations {#thin-install-limitations}

The standalone installer (`curl … /install | bash`) downloads the **core CLI module set** — `openclaw_cli.py` plus its direct support modules. This covers: `ask` / chat, `--health`, `analyze`, `write`, `watch`, `exec`, `edit`, `session`, and `auth`.

Commands that depend on optional server-side Python packages (`agent_loop`, research dependencies, etc.) will print a hint and exit with a non-zero code on a thin install:

```
Use a repo checkout/package install for advanced commands,
or stick to core standalone flows like ask/chat/health/analyze/write/exec/edit/watch.
```

Affected commands on a thin install: `openclaw plan`, `openclaw research` (full multi-source mode).
Switch to the developer/package install (`python -m pip install -e .`) to unlock these.

Recent terminal-agent sessions also appear in the dashboard under **Terminal Agent Sessions**, including watch-mode checkpoint counts and automation status. Clicking a session loads a richer detail view with plan/task linkage, recent progress log, intervention history, and a **Watch Insights** panel showing the per-poll checkpoint timeline (poll index, phase, status, summary) and the full retry history (poll, attempt, error type). The Scheduled Tasks card can pause, resume, and update cron/prompt jobs from the browser. The dashboard control-plane also exposes **Active Plans** (linked steps and sessions) and **Unified Task Status** (Mission Control + scheduler tasks in one view).

## Developer install from a repo checkout

If you are working inside a local checkout, you can install the packaged entrypoints directly:

```bash
python -m pip install -e .
openclaw --version
make test-cli
```

`make test-cli` runs the standalone CLI and installer/dashboard regression slice without the heavier shared pytest bootstrap used by the full bot suite.

## Token setup

The CLI needs an API token for `/api/agent/ask`.

- On **macOS**, the preferred path is the **Keychain** prompt during install, or `openclaw auth login`
- On **Linux, Windows, or WSL**, run `openclaw auth login` to save a local CLI token, or set `OPENCLAW_TOKEN` / `DASHBOARD_API_TOKEN`
- You can also export the token ad hoc for one command: `OPENCLAW_TOKEN=... openclaw "status"`

```bash
openclaw auth login
openclaw auth status
openclaw auth logout
```

If you skipped token setup, the CLI now warns before requests are sent.

## Updating

### Standalone installs (laptop / remote Mac)

Run `/update` from inside the REPL at any time:

```
[no-route] openclaw> /update
Updating openclaw 0.6.0 from http://192.168.1.93:8765…
  ↓ openclaw_cli.py  ✓
  ↓ openclaw_cli_actions.py  ✓
  ↓ openclaw_cli_sessions.py  ✓
  ↓ subprocess_utils.py  ✓

✓ Updated. Restart openclaw to use the new version.
```

The CLI fetches the four source files directly from the OpenClaw server (`OPENCLAW_URL/cli-update/<filename>`), atomically replaces them in `~/.local/share/openclaw-cli/`, then resets the update-needed flag. Restart the REPL to run the new version.

The update banner on startup uses SHA256 file hashes (from `OPENCLAW_URL/cli-update/meta`) to determine whether an update is available — not PyPI version strings.

### Developer / venv installs (Mac Mini)

```bash
openclaw update        # CLI subcommand
pip install --upgrade openclaw
```

### From the Mac Mini to a remote Mac

```bash
bash scripts/install_openclaw_cli_remote.sh macbook
```

## Remote Mac setup

If the laptop needs SSH trust + CLI bootstrapping in one step:

```bash
curl -fsSL http://192.168.1.93:8765/install-remote | bash
```

That flow:

1. Enables Remote Login when needed
2. Trusts the Mac Mini SSH key
3. Bootstraps the OpenClaw shell setup on the remote Mac
