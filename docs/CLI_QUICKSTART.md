# OpenClaw CLI Quick Start
<!-- Updated: 2026-04-18 -->


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
- In an interactive TTY, overlays now support `↑/↓` navigation, live filtering,
  and an inline preview pane before you press enter to select.
- High-risk approvals can now open a compact review overlay with `o`.
- The shipped picker path stays terminal-first and dependency-light; a true
  curses/Textual full-screen shell remains intentionally deferred.

## Wave 24 preview & focused inspection (current slice)

Wave 24 is currently shipping as a focused-inspection layer on top of those
existing overlays rather than as a brand-new TUI:

- **`/outputs 1` or `/outputs <filename>`** prints a bounded inline preview with
  file metadata. Large artifacts stay clipped and tell you when the preview was
  truncated.
- **`/outputs overlay`** still uses the searchable picker, but selecting an item
  immediately prints that same bounded preview in-place.
- **`/layout focus` / `/layout watch-monitor` / `/layout handoff`** keep the
  preset contract visible and now report explicit pane-focus transitions, even
  though the CLI still stops short of a true multi-pane renderer.
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

### Wave 26 — Prompt Line Enhancements

| Command | Description |
|---|---|
| `/tokenbadge [on\|off]` | Toggle `[~N tok]` token count badge shown after each response |
| `/tokeninfo` | Detailed token usage breakdown with context window bar |
| `/prompt [format\|reset]` | Customize REPL prompt — tokens: `{route}` `{session}` `{model}` `{build}` `{time}` |

> **New:** After each response, a `[~420 tok]` badge and model name are shown. Customize your prompt with `/prompt {build} ❯ `.

### Wave 27 — Celebrations & Smart Error Recovery

| Command | Description |
|---|---|
| `/streak` | Show current + best high-rating streak with 🔥 emoji and ASCII trophy at 5+ |
| `/tip` | Show a random openclaw usage tip (also appears at startup ~30% of the time) |

> **New:** `/exec` failures now show smart recovery hints (e.g., `pip install` for missing modules, `sudo` for permission errors). Rate 5 stars to trigger streak tracking and ASCII trophy at milestones!

### Wave 28 — Keyboard Shortcuts

| Command | Description |
|---|---|
| `/keys` | Show all active keyboard shortcuts and readline bindings |
| `/keybind [key action\|list\|clear]` | Set custom Ctrl+X key bindings to slash commands |
| `/bindlist` | Show all key bindings — built-in readline + your custom binds |

> **New:** Ctrl-R reverse search, Ctrl-L clear, Ctrl-W word-delete are now active. Bind any `Ctrl+X` to a slash command with `/keybind Ctrl+H /histsearch`.

The guaranteed baseline here stays `readline`-first: `/keys`, `/bindlist`,
history search, and the documented control bindings describe the behavior that
works without any extra prompt dependency. A future `prompt_toolkit`-backed
prompt session may add richer interactive-TTY editing and multiline ergonomics,
but it should remain additive rather than replacing the fallback contract.

### Wave 29 — Diff & Edit Viewer Polish

| Command | Description |
|---|---|
| `/diff [file1 file2 \| --git]` | Colorized unified diff (+ green, - red, @@ cyan) |
| `/changes` | Show session edit log + color-coded git status |
| `/snapshot [name]` | Save current git HEAD as a named restore point |
| `/rollback [name\|list]` | Preview or execute rollback to a saved snapshot |

### Wave 30 — Power Dashboard 🖥️

| Command | Description |
|---------|-------------|
| `/dashboard` | Rich stats + pins + activity + quick reference layout |
| `/benchmark [n]` | Benchmark AI server latency (n pings, default 3) |
| `/timeline` | Visual activity timeline grouped by day (last 7 days) |

### Wave 31 — Intelligent Command Suggestions & Inline Assist 🎯

| Command | Description |
|---------|-------------|
| `/followup [on\|off]` | Show or toggle the compact bottom-bar follow-up suggestions after a response |
| `/ratehint [on\|off]` | Show or hide the rating hint that shares the same bottom-bar footer |
| `/pathhints [on\|off]` | Keep file-path quick actions on or off when responses mention local files |

Wave 31 is currently shipping as a **response-assist slice**, not the full shell
roadmap:

- Long-running chat waits now expose concrete **phase / step / trust** copy such
  as `warming up`, `working`, `wrapping up`, `step N/3`, and `response ready`
  instead of a generic silent wait.
- After a normal response, OpenClaw can show a **Suggested follow-ups** block and
  a compact **bottom bar** footer with `mode: chat` plus up to three contextual
  hints like `/rate`, `/view`, `/context`, or `/links`.
- Before the next prompt, interactive REPL sessions now also print a compact
  **top context bar** with session/cwd/autoroute state plus any live
  plan/task/hidden-context/recovery cues.
- Plain mode and reduced-motion mode keep the same information, but render it as
  explicit text lists instead of Rich inline chrome.
- Existing **status-bar context** still prints after responses, but it now sits
  between the shipped top context bar and bottom footer instead of standing in
  for the whole shell pattern by itself.
- Interactive TTY chat can now pair those wait-state cues with a live streamed
  answer from the backend SSE endpoint; compact, JSON, and non-TTY paths remain
  buffered for deterministic output.

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

## Wave 31 intelligent suggestions & live feedback slice (current slice)

Wave 31 is the **suggestion layer + phase/step transparency** pass:

- **Contextual suggestions follow the actual response.** The footer can now use
  the prompt, response body, and session context to suggest things like
  `→ /view <path>`, `→ /context`, or `→ /links`.
- **Long-running operations say what they are doing.** Instead of vague
  "thinking" output, longer actions now emit richer phase/step trust cues with
  deterministic counts and the shared `response ready.` completion line.
- **The bottom footer is the first shell slice.** Interactive chat now uses a
  compact `mode: chat` footer with contextual next actions. The always-on top
  context bar remains follow-up work.
- **Trust-building language stays honest.** Prefer deterministic counts, elapsed
  time, and specific action verbs over fake percentage bars or ambiguous status
  words.

Fallback expectations:

- **Plain mode** keeps explicit phase/step labels and falls back to the existing
  predictive-affordance panel instead of a Rich footer
- **Reduced motion** keeps the same structure without animated reveals
- **Non-TTY** prints prefixed text lines instead of overlay-style chrome
- **Narrow terminals** drop low-priority hints first

## Wave 32 bookmarks & instant replay slice (current slice)

Wave 32 is the **bookmark + replay** follow-up:

- mark meaningful turns with `/bookmark "label"`
- list replay anchors with `/bookmarks`
- replay from a bookmark with `/replay --from b1`
- carry bookmark markers into `session show`, `session share`, and `session export`

## Wave 33 workflows & macros slice (current slice)

Wave 33 is the **workflow composition + dry-run** slice:

- create named workflows from command sequences with `/workflow save <name>`
- preview workflows before execution with `/workflow preview <name>`
- run workflows with session-aware placeholders like `{cwd}` and `{session}`
- keep the existing macro engine as the persistence/runtime layer for this slice

## Wave 34 quality & experiments slice (current slice)

Wave 34 is the **traceability + quality snapshot** slice:

- keep `/quality` as the rating histogram, then append the latest routing summary
- inspect the most recent routing decision and confidence with `/trace`
- defer local experiment variant controls to a later Wave 34 follow-on

## Wave 35 runbooks & exports slice (current slice)

Wave 35 is the **runbook + export-template gallery** slice:

- render the active session as a long-form Markdown handoff with `/runbook`
- export the same report from the CLI with `openclaw session export --format runbook`
- inspect the built-in reporting modes with `/exporttemplates`

## Wave 36 workspace recovery slice (current slice)

Wave 36 is the **workspace capsule + restore** slice:

- inspect the current recovery state with `/workspace status`
- save a portable recovery capsule with `/workspace save`
- browse saved capsules with `/workspace list`
- restore one into a fresh session with `/workspace restore <capsule>`

## Wave 37 pattern library slice (current slice)

Wave 37 is the **pattern library + reusable workflow-template** slice:

- save reusable flows from recent command history with `/pattern save <name> [last N]`
- promote an existing workflow into a reusable pattern with `/pattern save <name> workflow <workflow>`
- browse saved patterns with `/pattern list`
- inspect the resolved steps with `/pattern preview <name>`
- rerun a saved pattern with `/pattern run <name>`

## Wave 38 structured collaboration slice (current slice)

Wave 38 is the **ownership + risk register + readiness audit** slice:

- assign an explicit owner with `/collab assign @actor TEXT`
- track blockers with `/risk add <critical|high|medium|low> TEXT`
- review unresolved blockers with `/risk list`
- audit the next handoff with `/handoff check`
- use `/collab status` to review assignments and open risks alongside the
  existing handoff snapshot

## Wave 39 learned routing slice (current slice)

Wave 39 is the **route-quality insight + advisory suggestion** slice:

- capture route context automatically when you rate a routed response with `/rate`
- inspect the best-rated lane with `/quality predict`
- review learned route summaries with `/routing suggest`
- compare the strongest local lanes with `/routing analyze`

## Wave 40 automation dashboard slice (current slice)

Wave 40 is the **operator summary + computed alerts** slice:

- review cross-session automation health with `/dashboard automation`
- inspect unresolved operator alerts with `/alerts list`
- quiet an alert once acknowledged with `/alerts acknowledge <index>`
- reuse the same cross-session overview with `/fleet status` or `/fleet health`

## Wave 41 incident log slice (current slice)

Wave 41 is the **lightweight incident log + resolution** slice:

- review unresolved incidents with `/incident list`
- record a new operator issue with `/incident log TEXT`
- resolve an incident with `/incident resolve <index>`
- use `/collab status` to review open incidents inside the session handoff view
- use `/handoff check` when you want readiness to account for unresolved incidents
- review open-incident count in `/dashboard automation`

## Wave 42 source rendering reliability slice (current slice)

Wave 42 is the **response body + sources panel reliability** slice:

- source sections are stripped from the response body before they can render twice
- loose `Sources:` blocks without extra spacing still extract into the sources panel
- ANSI color codes no longer bleed into source display labels when clickable-link
  fallback is used
- the ANSI sources box now tracks live terminal width instead of a shorter cached width

`docs/COMMANDS.md` does not need regeneration because no new commands were added.

## Wave 43 context & token intelligence slice (current slice)

Wave 43 is the **context visibility + token awareness** slice:

- `/tokeninfo` shows estimated session token usage with a progress bar toward a
  128k context window
- response footers now surface token count more prominently when the API returns it
- `/session` now includes session age so you can gauge how long the current
  context has been building
- `/tokeninfo` is part of the startup tip rotation for easier discovery

`docs/COMMANDS.md` does not need regeneration because `/tokeninfo` was already documented.

## Wave 44 startup & first-run polish slice (current slice)

Wave 44 is the **adaptive startup + discovery** slice:

- startup banner greeting adapts to time of day with morning, afternoon, and
  evening copy
- session milestones celebrate repeated use at key counts like 10, 50, and 100
- the startup tips pool now includes the recent-command discovery set for
  `/tokeninfo`, `/trace`, `/handoff check`, `/fleet health`, `/alerts`,
  `/collab decision`, `/bookmark`, `/overlay`, `/pattern`, and `/draft multiline`
- `--no-banner` suppresses the startup panel for scripted or automation-focused runs

`docs/COMMANDS.md` does not need regeneration because Wave 44 changes startup behavior, not command metadata.

## Wave 45 context pressure guardrails slice (current slice)

Wave 45 is the **token breakdown + recovery guidance** slice:

- `/tokeninfo` still shows the estimated 128k progress bar, but now breaks the
  estimate down by actor so you can see which side of the conversation is
  consuming the most context
- `/tokeninfo` also calls out the largest actor share in one compact line for
  faster triage during long sessions
- medium pressure still nudges you toward refreshing context, while high and
  near-capacity pressure now recommend saving a `/bookmark` before using `/clear`
- adjacent inspection surfaces now carry lighter follow-through too: `/context`
  and `/session` surface next-send pressure plus hidden-context cues, while
  `/watch status` surfaces next-retry pressure for active automation loops

`docs/COMMANDS.md` does not need regeneration because Wave 45 deepens `/tokeninfo` behavior without changing command metadata.

## Deferred interaction affordance follow-ups (not shipped yet)

The current CLI already ships the lighter-weight slices from this area:

- `/edit` shows a unified diff before applying changes
- Wave 31 adds the top-context bar, bottom footer, and phase/step trust cues during waits
- Waves 43–45 add `/tokeninfo`, session-age visibility, actor breakdowns, and
  bookmark-before-clear guidance

What is still deferred:

- broader ambient overflow warnings beyond the already-shipped `/tokeninfo`,
  `/context`, `/session`, and `/watch status` cues
- any shell expansion beyond the shipped top-context + status + bottom-bar
  baseline

What shipped in this follow-up slice:

- interactive TTY chat now streams backend response chunks live from the
  service; use `--no-stream` when you want the older buffered path
- `openclaw edit` and `/edit` now show the edit preview before approval and
  before dry-run exit, including compact review/trust/recovery cues plus
  explicit `Dry run only.` and no-op feedback
- `/tokeninfo` now follows through with stronger recovery guidance by pointing
  high-pressure sessions toward saving a `/bookmark` before using `/clear`
- the interactive REPL now prints a top context bar ahead of the next prompt so
  session state, linked plan/task, hidden-context hints, and `/rollback last`
  recovery cues stay visible without reopening older shell-chrome planning

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

## Wave 29 session storytelling slice (current slice)

Wave 29 is currently the **plain-text recap scaffold** for session review and
handoff, and the next slice stays intentionally restrained rather than turning
every surface into a narrative export suite:

- **`openclaw session share <session-id>`** is the clearest storytelling view
  today. It groups the recap into stable chapters: **ACTORS**, **RECENT
  DECISIONS**, **RECENT NOTES**, **LATEST HANDOFF**, **OPERATOR SNAPSHOT**,
  **RECENT OUTPUTS**, and **COMMANDS**.
- **`openclaw session show <session-id>`** keeps the same facts visible during
  inspection: actor-aware collaboration details, momentum/milestone wording,
  saved outputs, and the exact resume command.
- **`/session`** and **`/sessions`** are the only surfaces that should stretch
  a bit further in the next slice: they can acknowledge momentum or milestone
  cues, but status/count/watch context still needs to be the first thing you
  scan.
- The current ending is intentionally deterministic: the “next steps” are the
  explicit `resume`, `inspect`, and `share` commands rather than generated prose.
- **`/collab`**, **`openclaw session share`**, and **`openclaw session export`**
  stay neutral and pasteable in this slice; they should keep facts and commands
  ahead of mood wording.
- **Not shipped yet:** bullet-mode recaps, timeline-mode recaps, recap-specific
  export variants, or richer browser/dashboard storytelling.

`docs/COMMANDS.md` still does not need regeneration because command metadata is
unchanged.

## Wave 30 premium choreography slice (current slice)

Wave 30 is currently the **calm pacing + fallback polish** pass:

- **Startup stays readable first.** In plain mode or on narrow terminals, the
  startup banner resolves to a short static block with server, user, session,
  and auto-routing state instead of decorative chrome.
- **Long waits stay alive without extra motion.** Reduced-motion mode swaps the
  spinner for a static working line, periodic `Still working on ...` heartbeats,
  and a final `response ready.` cue.
- **High-risk actions use the same warning voice.** `/exec` and `/edit` print a
  compact `Review carefully` warning plus recovery guidance before the normal
  approval prompt for high/critical actions.
- **Celebration remains subdued.** `/celebrate` and 5-star `/rate` feedback
  still collapse to a single-line `🎉 ...` message when reduced motion, plain
  mode, or non-TTY output is active.
- **Not shipped yet:** a shared reveal-order system across every dashboard
  surface, richer retry choreography, or browser-side motion mirrors.

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
| `/tokeninfo` | Show per-model context window limit, actor token breakdown, usage %, and overflow warnings at 80/90/95% thresholds (W21) |
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

### Shell chrome (always-on, W22)

The interactive REPL prints two persistent bars on every turn — no explicit command needed:

- **Top bar** (`_print_shell_top_bar`) — shown after each AI response: `session · model · autoroute` state plus a `watch: active` indicator when a watch loop is running.
- **Bottom bar** (`_print_shell_bottom_bar`) — shown before each prompt: current mode plus 1–2 contextual hint commands.

Both bars degrade gracefully: Rich+TTY renders dim unicode bars; ANSI-only TTY falls back to a plain ANSI line; plain mode and non-TTY environments emit a simple `--- key: value | … ---` text line or suppress the bar entirely below minimum width.



| Command | What it does |
| --- | --- |
| `/draft [save\|load\|clear\|restore]` | Save, load, clear, or restore a draft prompt across turns |
| `/draft multiline [on\|off]` | Toggle multiline compose mode (`\end` on its own line to submit) |
| `/template [list\|save\|use\|delete]` | Manage reusable prompt templates persisted across sessions |
| `/pasteguard [on\|off]` | Guard large pastes that would route to risky commands — shows preview and requires confirmation |

`/draft multiline` is the stable user-facing composer surface regardless of the
line-editing backend underneath it. If the optional `prompt_toolkit` prompt path
lands, it should improve interactive-TTY editing and completion without changing
the command vocabulary; plain mode, non-TTY/scripted runs, and environments
without that dependency should still fall back to the existing `readline` or
plain `input()` flow.

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
