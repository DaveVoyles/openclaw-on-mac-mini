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

## Accessibility and adaptive layout

Wave 15's currently shipped accessibility controls live in the REPL:

```text
/accessibility status
/accessibility reduced-motion on
/accessibility plain on
/accessibility high-contrast on
/accessibility reset
```

- **Reduced motion** disables spinner animation and falls back to static text
  status lines plus periodic heartbeats for slower requests.
- **Plain mode** simplifies the prompt and startup banner, and forces plain-text
  response rendering for screen-reader/basic-terminal use. It also aligns with
  `/layout plain`.
- **High contrast** is a persisted preference that is surfaced by
  `/accessibility status` and applied to the CLI's higher-contrast border and
  separator styles.
- **`/accessibility status`** reports the active toggles plus Rich availability,
  TTY detection, and terminal width.

Adaptive layout is currently split between:

- `/layout compact|normal|verbose|plain` for chrome density
- width-aware render helpers for tables and status output

## Wave 16 feedback cues

The current Wave 16 slice adds denser but still accessible feedback:

- Long-running chat/analyze/write calls now end with an explicit `response ready`
  cue, and reduced-motion mode prints periodic text heartbeats while waiting.
- `/clear`, `/layout`, and `/accessibility` return a short shared confirmation
  line instead of mixed ad-hoc phrasing.
- High/critical `/exec` and `/edit` actions now show an extra pre-approval
  warning with a recovery hint before the normal approval prompt.
- `/exec` and `/edit` end with a compact completion line after the main result so
  it is easier to tell when the action finished.

## Theme engine and personalization

Wave 17 adds a safer, more expressive personalization layer in the REPL:

```text
/theme list
/theme preview cyan
/theme next
/theme prev
/theme reset
/emoji status
/emoji preview
/emoji pack minimal
```

- **Theme switching** now supports previewing without persisting, cycling forward
  or backward, and resetting to the default accent.
- **Emoji packs** now support `classic`, `minimal`, and `ascii`. Legacy
  `/emoji on|off` still works and now maps to the new pack model.
- **Preference loading** clamps invalid stored theme, emoji-pack, and layout
  values back to known-safe defaults.

## Wave 18B performance visibility

The current performance-visibility slice focuses on long-running session and
watch flows without changing non-TTY or JSON output behavior:

- `/session` now shows watch timing cues when automation is active or recently
  ran: active phase, last run duration, and accumulated retry backoff.
- `/watch status` surfaces phase age, last checkpoint duration, and retry
  backoff totals from persisted watch state.
- `/watch history` annotates retry rows with their backoff delays.
- `/events` includes compact timing hints for approval waits, command/edit
  runtime, and watch retry backoff when those metrics are available.
- `/exec` and `/edit` now print completion details that separate approval wait
  time from actual execution/write time.

## Wave 19 interactive overlays

Wave 19 adds opt-in interactive pickers for safe list-style browsing:

```text
/overlay status
/overlay on
/outputs overlay
/sessions overlay
openclaw session list --interactive
```

- **Interactive overlays are off by default** so scripted and non-TTY usage
  keeps the existing output and control flow.
- **`/outputs overlay`** opens a searchable picker for saved artifacts, then
  previews the selected output inline.
- **`/sessions overlay`** opens a searchable picker for recent sessions, then
  shows the selected session summary plus its resume command.
- **`openclaw session list --interactive`** exposes the same picker for one-shot
  terminal usage outside the REPL.
- If stdin/stdout is not interactive, OpenClaw prints a short notice and falls
  back to the normal non-interactive list output.

## Wave 24 preview & focused inspection (current slice)

Wave 24 is currently shipping as a focused-inspection layer on top of those
existing overlays rather than as a brand-new TUI:

- **`/outputs 1` or `/outputs <filename>`** prints a bounded inline preview with
  file metadata. Large artifacts stay clipped and tell you when the preview was
  truncated.
- **`/outputs overlay`** still uses the searchable picker, but selecting an item
  immediately prints that same bounded preview in-place.
- **`/sessions overlay`** and **`openclaw session list --interactive`** let you
  search, select, and land directly in the compact Session Dashboard plus the
  exact resume command for that session.
- **`/watch status`** and **`/watch history`** are the current focused watch
  inspection surfaces: status leads with phase/polls/backoff, while history
  keeps recent progress, retries, and operator notes grouped together.
- **`openclaw session show <session-id>`** remains the deeper inspection view
  when you want the full session/watch/checkpoint/output snapshot in one place.

## Wave 25 layout presets (current slice)

Wave 25 is currently a **preset-management** slice:

- **`/layout focus`** stores the focus preset (`/session` primary,
  `/context` supporting).
- **`/layout watch-monitor`** stores the watch preset (`/watch status` primary,
  `/watch history + /outputs` supporting).
- **`/layout handoff`** stores the collaboration preset (`/collab` primary,
  session summary + recent outputs supporting).
- **`/layout`** reports the active preset and whether the current terminal can
  honor it as `multi-pane`, must downgrade to `stacked`, or falls back to
  `single-pane`.
- **`/layout reset`** clears the preset and returns to the default single-pane
  mode.
- **`/accessibility status`** mirrors the preset + fallback state so you can
  confirm how plain mode, non-TTY use, or a narrow terminal will collapse it.

The actual split-pane canvas is still follow-up work; this slice ships the
vocabulary, persistence, and fallback reporting first.

## Wave 20 collaboration handoffs

Wave 20 adds local-first collaboration affordances without requiring any new
backend:

```text
/collab
/collab note @alice Checked the failing test shard
/collab decision @bob #handoff Keep the handoff file-local for now
openclaw session share <session-id>
openclaw session export <session-id>
```

- **`/collab`** prints a compact handoff summary with actors, recent decisions,
  recent outputs, and the exact resume/share commands.
- **`/collab note`** records an actor-tagged note in the current session's
  local event log.
- **`/collab decision`** records a tagged decision trail entry that also shows
  up in exports and session inspection.
- **`openclaw session share`** prints the same pasteable summary outside the
  REPL for handoff messages or async updates.
- **`openclaw session export`** now includes a `collaboration` block in its
  JSON payload, and handoff manifests capture the same structure.

### Wave 20 — Response Typography

| Command | Description |
|---|---|
| `/autobold [on\|off]` | Toggle auto-bolding of dollar amounts, percentages, and filenames in responses |
| `/emojiheaders [on\|off]` | Toggle emoji prefixes on AI response headings (`## 🔹`, `### ▸`) |
| `/separator [style]` | Set separator style after responses: `gradient`, `pulse`, `dots`, `wave`, `none` |

### Wave 21 — Command Palette & Tab-Complete

| Command | Description |
|---|---|
| `/palette [query]` | Fuzzy-search all slash commands by keyword |
| `/shortcuts` | Show keyboard shortcuts & quick-reference card |

> **Tip:** Press **Tab** at the prompt after typing `/` to auto-complete slash commands.

### Wave 22 — Animated Progress & Celebrations

| Command | Description |
|---|---|
| `/macrostatus` | Show saved macros with step counts and preview |
| `/celebrate [message]` | Trigger a confetti celebration animation |

> **New:** `/exec` now shows a live bouncing progress bar during command execution. Rate any response `/rate 5` to trigger the 🎉 celebration burst!

### Wave 23 — ASCII Data Visualizations

| Command | Description |
|---|---|
| `/stats [category]` | ASCII bar charts of command frequency, ratings, sessions (`all`\|`commands`\|`ratings`\|`sessions`) |
| `/quality` | Colored 8-row vertical histogram of response quality ratings (1–5 ⭐) |
| `/heatmap` | 24-hour activity heatmap showing peak usage hours with color intensity |

### Wave 24 — Smart Response Formatting

| Command | Description |
|---|---|
| `/jsonformat [on\|off]` | Toggle auto-detection and pretty-printing of JSON in responses |
| `/links [on\|off]` | Toggle OSC 8 clickable hyperlinks in responses (requires modern terminal) |
| `/pathhints [on\|off]` | Toggle file path quick-action hints shown after responses |

> **New:** Responses now automatically pretty-print JSON with syntax highlighting, make URLs clickable (iTerm2/Kitty/WezTerm), and hint at file paths with `/view` or `/edit`.

## Wave 22 status grammar (current slice)

Wave 22 is the in-progress dashboard/status-language pass. The current slice is
already visible in a few everyday surfaces:

- **`/sessions`** uses compact badge cells for activity, staleness, saved-output
  presence, and tags so recent sessions scan faster in plain text.
- **`/session`** and **`/watch status`** share phase / last-run / retry-backoff
  wording, which is the current baseline for the broader status lattice.
- **`/events`** keeps timing hints compact instead of forcing you to read full
  prose for approval wait or retry/backoff context.
- **`/accessibility status`** is the reference check for how these cues degrade
  in plain mode, reduced motion, and high-contrast output.

`docs/COMMANDS.md` did not need regeneration for this docs-only lane because the
command metadata itself did not change here.

## Wave 23 hierarchy slice (current slice)

Wave 23 has started shipping in the CLI, but only as a focused hierarchy pass:

- **`/session`** and **`openclaw session show`** now put status/count/watch
  signals near the top so you can scan health before reading deep detail.
- **`/sessions`** keeps the compact badge row as the first scan target for
  activity, freshness, outputs, and checkpoints.
- **`/watch status`** and **`/watch history`** now foreground status-family
  labels like `ACTIVE`, `RETRY`, and `INFO` so retry pressure and intervention
  cues are visible before raw timestamps.

This is a **partial Wave 23 slice**, not the full dashboard-elevation roadmap.
Richer summary/action regions for outputs, context, and browser mirrors remain
follow-up work.

## Wave 26 celebration slice (current slice)

Wave 26 is currently a narrow, already-shipped celebration pass:

- **`/celebrate [message]`** triggers the shared celebration burst directly.
- **`/rate 5`** prints the normal rating confirmation and then reuses the same
  celebration helper for a short milestone acknowledgement.
- In **plain mode**, **reduced motion**, or **non-TTY** output, the celebration
  path collapses to a single line instead of animation.
- **`/collab`** and **`openclaw session share`** stay neutral and pasteable in
  this slice; richer session-mood wording is still deferred.

`docs/COMMANDS.md` does not need regeneration for this lane because the command
metadata is unchanged.

## Wave 27 operator visibility slice (current slice)

Wave 27 is currently the **read-only operator snapshot** pass:

- **`openclaw session share <session-id>`** is the main pasteable summary for
  operators: it prints recent actors, decisions, notes, latest handoff,
  outputs, and the exact resume/inspect/share commands.
- **`openclaw session show <session-id>`**, **`/session`**, and **`/sessions`**
  already expose the same local snapshot vocabulary for watch state,
  collaboration context, and next-step cues.
- **`/watch status`** and **`/watch history`** are the live operator-visibility
  views for checkpoint drift, retry pressure, and operator breadcrumbs.
- This slice is **visibility only**. It does not add browser-side control,
  shared presence, or remote mutation of another operator's session.

`docs/COMMANDS.md` still does not need regeneration because command metadata is
unchanged.

## Wave 28 predictive affordances slice (current slice)

Wave 28 is currently the **small, deterministic next-step guidance** pass:

- **`/watch status`** now acts like a control-tower surface with explicit follow-up
  commands. Expect to see `/watch history` and `/watch intervene <msg>` every
  time, plus `/watch retry-limit N` while a watch is still active or `/session`
  after completion.
- **`/context`** closes with guidance that matches session state: add grounding
  with `/files add <path>`, review tracked files with `/files`, or strengthen the
  session with `/plan <id>` / `/task <id>` before the next analyze/write step.
- **Chat replies** can append a small **file hint** when the response mentions a
  real local path: `use /view or /edit`. This is terminal-only and intentionally
  suppressed in plain mode/non-TTY output so scripts stay deterministic.
- **Approval/error recovery** stays textual. High/critical `/exec` and `/edit`
  actions include a recovery sentence before approval, while chat failures point
  to `/retry` and `/reset`.
- **`/shortcuts`** is the current gesture-language reference card for repeatable
  moves like retry, history browse, command search, and session control.

`docs/COMMANDS.md` still does not need regeneration because command metadata is
unchanged.

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
| `/outputs overlay` | Open a searchable picker for saved outputs |
| `/events [n]` | Show recent session events, including structured `route` events for auto-routed prompts |
| `/collab [status\|share]` | Print an actor-oriented collaboration/handoff summary for the active session |
| `/collab note [@actor] TEXT` | Record a collaboration note in the local session audit trail |
| `/collab decision [@actor] [#tag] TEXT` | Record a tagged decision for later handoff/export |
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
| `/theme [name\|list\|preview\|next\|prev\|reset]` | List, preview, cycle, reset, or persist a theme |
| `/emoji [on\|off\|status\|pack <name>\|preview]` | Toggle emoji, inspect the active pack, or switch between `classic`, `minimal`, and `ascii` |
| `/overlay [on\|off\|status]` | Toggle opt-in interactive pickers for supported list commands |
| `/layout [compact\|normal\|verbose\|plain]` | Switch between the currently supported layout densities |
| `/accessibility [status\|mode]` | Show current accessibility state or manage accessibility prefs |
| `/accessibility reduced-motion on\|off` | Toggle reduced-motion mode for spinner/status behavior |
| `/accessibility plain on\|off` | Toggle simplified plain/screen-reader mode |
| `/accessibility high-contrast on\|off` | Toggle the stored high-contrast preference |

### Trust & explainability

| Command | What it does |
| --- | --- |
| `/why` | Explain the last routing or tool decision — shows confidence badge (`[HIGH]`/`[MED]`/`[LOW]`), rationale, and grounding |
| `/events [n\|decisions]` | Show last N session events; `decisions` filters to route/plan/approval/exec/edit events only |

### Composer & input flow

| Command | What it does |
| --- | --- |
| `/draft [save\|load\|clear\|restore]` | Save, load, clear, or restore a draft prompt across turns |
| `/draft multiline [on\|off]` | Toggle multiline compose mode (`\end` on its own line to submit) |
| `/template [list\|save\|use\|delete]` | Manage reusable prompt templates persisted across sessions |
| `/pasteguard [on\|off]` | Guard large pastes that would route to risky commands — shows preview and requires confirmation |

### Search, aliases & pins (Wave 16)

| Command | What it does |
| --- | --- |
| `/search <query>` | Full-text search the current session's event history; matches highlighted in bold yellow |
| `/search --all <query>` | Cross-session search across the last 200 sessions (up to 15 hits) |
| `/alias <name> <expansion>` | Define a command shorthand — e.g. `/alias r /research` |
| `/alias rm <name>` | Remove an alias; `/alias` with no args lists all defined aliases |
| `/pin [name]` | Pin the last AI response for quick recall (auto-named `pin-1`, `pin-2` …) |
| `/pin recall <name>` | Re-display a pinned response inline |
| `/pin rm <name>` | Remove a pin by name; `/pins` lists all pins |

### Macros & command history (Wave 17)

| Command | What it does |
| --- | --- |
| `/history [n]` | Show the last N commands from input history (default 20); `/history clear` resets |
| `/macro list` | List all saved macros with their command counts |
| `/macro save <name> [last N]` | Save the last N history entries as a named macro (default 5) |
| `/macro show <name>` | Display the commands stored in a macro |
| `/macro run <name>` | Execute a macro's slash commands in sequence; natural-language entries are skipped with a warning |
| `/macro rm <name>` | Delete a named macro |

### Response rating & quality (Wave 18)

| Command | What it does |
| --- | --- |
| `/rate [good\|ok\|bad\|meh\|1-5]` | Rate the last AI response; stored in `_PREFS["ratings"]` and as a session event |
| `/quality` | Show response quality stats — avg score, star distribution chart, most recent ratings |
| `/ratehint [on\|off]` | Toggle the dim "rate this response" hint shown after each AI reply |

### Context injection & prompt engineering (Wave 19)

| Command | What it does |
| --- | --- |
| `/inject <path>` | Inject file content as context prefix for the next AI message (consumed once; max 8000 chars) |
| `/inject --url <url>` | Inject URL content as context prefix for next message |
| `/inject clear` | Clear the pending injection; `/inject status` shows queued char count |
| `/system` | View the current persistent system prompt (prepended to every AI message) |
| `/system set <text>` | Set a system prompt (max 2000 chars); `/system append <text>` to extend |
| `/system clear` | Clear the system prompt |
| `/promptdebug` | Preview the full prompt that would be sent to AI (system + inject + your message) |

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
- `openclaw session share <session-id>` — print a pasteable collaboration handoff summary from local session data
- `openclaw session list --interactive` — open a searchable picker when you want to browse sessions interactively in a real TTY
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

## Docs/dashboard sync for future waves

If you are shipping a new CLI dashboard or status surface, also update:

- `docs/DASHBOARD_SURFACES.md` for the surface inventory and wave checklist
- `docs/CLI_ARCHITECTURE.md` for implementation/guard details
- `docs/UX_IMPROVEMENTS.md` for roadmap status and shipped evidence

Only update `docs/COMMANDS.md` by regenerating it from runtime command metadata
when command names or descriptions change.

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
[autoroute:off] openclaw> /update
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
