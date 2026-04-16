# OpenClaw CLI — UX Improvements Roadmap

> **Audience:** AI coding agents (Copilot, etc.) implementing CLI improvements autonomously.
> **How to use this doc:** Pick the next unshipped wave. Launch an agent fleet as described below. Verify done-when criteria. Mark the wave complete and move to the next.

## Primary User & Environment Assumptions

- **Primary user:** Dave Voyles
- **Primary environment:** macOS + iTerm2
- **Default UX target:** Rich/console-first output with color, emoji, bold hierarchy,
  and terminal-native chrome enabled by default
- **Fallback requirement:** plain mode, reduced motion, non-TTY, and narrow-width
  paths remain required and must stay explicitly documented

---

## Orchestration Model

Each wave is implemented by a **fleet** of specialized agents coordinated by a
single **Orchestrator**. The Orchestrator does not write code — it reads
requirements, breaks work into non-overlapping lanes, launches parallel agents,
synthesizes their output, resolves conflicts, and validates that done-when
criteria are satisfied before closing the wave.

### Roles

| Role | Responsibility |
|---|---|
| **Orchestrator** | Reads this doc → assigns lanes → launches agents in parallel → synthesizes results → runs tests → deploys |
| **Research Agent** | Reads existing code, locates relevant functions, identifies constraints, returns findings to Orchestrator |
| **Implementation Agent(s)** | Write/edit code for a specific non-overlapping lane. Never touch files owned by another lane. |
| **Test & Validation Agent** | Runs the test suite, reports failures with stack traces, suggests fixes |
| **Docs Agent** | Updates `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` to reflect the wave's changes |

### Orchestrator Prompt Template

```
You are the Orchestrator for Wave N of the OpenClaw CLI UX Improvements.
Read docs/UX_IMPROVEMENTS.md § Wave N for full requirements.

Primary source file: src/openclaw_cli.py (~6300 lines)
Test command: python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_run_chat_uses_router_before_generic_chat_fallback \
       or test_run_chat_routed_edit_still_requests_approval \
       or test_run_chat_autoroutes_plan_candidate \
       or test_run_chat_supports_help_command \
       or test_help_output_includes_new_commands)" -q
Deploy: scp src/openclaw_cli.py macbook:/Users/davevoyles/.local/share/openclaw-cli/

Steps:
1. Launch Research Agent to locate all relevant code sections.
2. Split implementation into non-overlapping lanes and launch agents in parallel.
3. Collect results, check for conflicts, apply synthesized edits.
4. Launch Test Agent. If failures, fix before deploying.
5. Launch Docs Agent.
6. Deploy. Verify done-when criteria. Mark wave complete in this doc.
```

### Key Technical Constraints (all waves)

- **Python 3.9 compat** — no `match`/`case`, no `str.removeprefix`, use `re`
- **Rich guard** — every Rich call must be inside `if _RICH_AVAILABLE and is_tty`
  where `is_tty = _IS_TTY or sys.stdout.isatty()` (re-checked at call time)
- **No bare markup** — never print Rich markup tags unconditionally (tests capture stdout)
- **stdout vs stderr** — update notices go to stderr; errors go to stdout
- **Test suite** — must pass 180 tests before any deploy
- **Deploy target** — `macbook:/Users/davevoyles/.local/share/openclaw-cli/`
- **ANSI palette** lives at `~L96` in `src/openclaw_cli.py`

---

## Future Wave Docs/Dashboard Framework

Waves 21–35 should always reserve a dedicated docs/dashboard lane in parallel
with research, implementation, and validation lanes.

### Required outputs per future wave

- update `docs/UX_IMPROVEMENTS.md` with roadmap status, shipped evidence, and
  deferred scope
- update `docs/CLI_ARCHITECTURE.md` when rendering helpers, state plumbing, or
  dashboard/shared-surface guardrails change
- update `docs/CLI_QUICKSTART.md` when user-visible commands, examples, or
  workflows change
- update `docs/DASHBOARD_SURFACES.md` whenever a terminal/dashboard canvas is
  added, renamed, or materially changed
- regenerate `docs/COMMANDS.md` only when runtime command metadata changes

### Dashboard/docs lane checklist

1. inventory the touched CLI and browser/dashboard surfaces
2. verify plain-mode, reduced-motion, and non-TTY parity for each changed surface
3. keep terminology aligned across `/session`, `/watch`, `/outputs`, `/sessions`,
   `/context`, `/events`, `/accessibility`, and any browser dashboard mirrors
4. record whether `docs/COMMANDS.md` was regenerated or intentionally left alone
5. do not mark a wave shipped until the docs/dashboard lane is closed too

For the canonical inventory and reusable checklist, see
`docs/DASHBOARD_SURFACES.md`.

---

## Deferred interaction affordances backlog (current truth)

Use this section when planning the next CLI UX follow-up wave. It keeps the
remaining deferred interaction affordances explicit without undoing the shipped
evidence recorded in later waves.

- **Now shipped in the current slice:** backend-backed token streaming / SSE
  output for interactive TTY chat. Wave 3 no longer stops at the footer and
  `--no-stream`; the CLI now streams backend response chunks incrementally from
  `/api/agent/ask/stream` when streaming is enabled.
- **Now shipped as a fuller review flow:** `/edit` already showed the unified diff,
  and the shell-polish slice now layers compact approval review lines plus trust
  and recovery cues around that preview instead of treating richer review polish
  as still-open work.
- **Already shipped in narrower form:** Waves 43–45 added local token/context
  visibility via `/tokeninfo`, session-age surfacing, actor breakdowns, and
  bookmark-before-clear guidance. The follow-through is already starting to
  extend onto adjacent operator surfaces too: `/context`, `/session`, and
  `/watch status` now surface next-send or next-retry context pressure cues and
  point back to `/tokeninfo`, `/bookmark`, or `/promptdebug` when pressure is
  high. What remains deferred is broader ambient warning coverage plus per-model
  limits rather than those explicit inspection surfaces themselves.
- **Also shipped in this follow-through slice:** `/edit` now keeps that preview in
  front of approval and dry-run exits, while recovery guidance has become more
  explicit instead of reopening the old preview/recovery work as deferred.
- **Also shipped in this follow-through slice:** high-risk approvals now support a
  text-only `[r]eview` loop so `/edit` can replay the exact diff preview before
  approval without requiring a full overlay UI.
- **Now shipped in the current shell-polish slice:** the REPL now prints an
  always-on top context bar ahead of the existing response/status/bottom-bar
  cues, so the broader split-bar shell is no longer limited to the footer-only
  slice from the earlier Wave 31 follow-up.
- **Now shipped in the current interactive follow-through slice:** high-risk
  approvals can open a compact review overlay with `o`, keeping the text review
  loop as the plain/non-TTY fallback instead of leaving approval overlays
  entirely deferred.
- **Now shipped in the current interactive follow-through slice:** TTY overlays
  now support `↑/↓` navigation, live filtering, and inline preview panes. The
  terminal-first picker has become richer without committing to a heavier
  curses/Textual full-screen shell.
- **Now shipped in the current interactive follow-through slice:** layout
  presets now print explicit `/layout focus ...` transition cues. What remains
  deferred is a true compositor-level pane router rather than the visibility of
  pane focus itself.

---

## Shared Terminal Shell Pattern (Wave 31+)

Starting with the next future-wave tranche, the standard terminal layout target is
a **split-bar shell** that keeps context and controls out of the main response
body:

1. **Agent output region** — streamed/logged agent activity above the top bar
2. **Top context bar** — compact session / model / task / state badges
3. **Primary message region** — the current response, code, tables, and narrative
   output
4. **Bottom control bar** — the current mode plus 1–2 inline instructions or next
   actions

Default presentation assumptions:

- **Rich + iTerm2** is the standard path: color, emoji, dim accents, and unicode
  separators are expected by default
- **Plain mode** keeps the same semantic structure with explicit labels and text
  separators
- **Reduced motion** preserves the shell structure but removes animated reveals
- **Non-TTY / narrow layouts** collapse low-priority hints first and keep the main
  output readable

Wave 31 is the primary owner of this shell contract, but Waves 32–35 should reuse
the same top-bar / output / bottom-bar structure rather than inventing per-wave
chrome.

---

## Wave Status

| Wave | Name | Status |
|---|---|---|
| [Wave 1](#wave-1--foundation) | Foundation | ✅ Shipped |
| [Wave 2](#wave-2--rich-rendering) | Rich Rendering | ✅ Shipped |
| [Wave 3](#wave-3--live-streaming) | Live Streaming | ✅ Shipped |
| [Wave 4](#wave-4--interactivity) | Interactivity | ✅ Shipped |
| [Wave 5](#wave-5--status-layer) | Status Layer | ✅ Shipped |
| [Wave 6](#wave-6--themes--personalization) | Themes & Personalization | ✅ Shipped |
| [Wave 7](#wave-7--dashboard--history) | Dashboard & History | ✅ Shipped |
| [Wave 8](#wave-8--session-intelligence) | Session Intelligence | ✅ Shipped |
| [Wave 9](#wave-9--artifact-studio--replay) | Artifact Studio & Replay | ✅ Shipped |
| [Wave 10](#wave-10--guided-workflows--recovery) | Guided Workflows & Recovery | ✅ Shipped |
| [Wave 11](#wave-11--workspace-handoffs) | Workspace Handoffs | ✅ Shipped |
| [Wave 12](#wave-12--automation-control-tower) | Automation Control Tower | ✅ Shipped |
| [Wave 13](#wave-13--trust--explainability) | Trust & Explainability | ✅ Shipped |
| [Wave 14](#wave-14--composer--input-flow) | Composer & Input Flow | ✅ Shipped |
| [Wave 15](#wave-15--accessibility--adaptive-layout) | Accessibility & Adaptive Layout | ✅ Shipped |
| [Wave 16](#wave-16--microinteractions--feedback-density) | Microinteractions & Feedback Density | ✅ Shipped |
| [Wave 16B](#wave-16b--search-aliases--pins) | Search, Aliases & Pins | ✅ Shipped |
| [Wave 17](#wave-17--theme-engine--personalization) | Theme Engine & Personalization | ✅ Shipped |
| [Wave 18](#wave-18--macros--command-history) | Macros & Command History | ✅ Shipped |
| [Wave 18B](#wave-18b--performance-visibility) | Performance Visibility | ✅ Shipped |
| [Wave 18C](#wave-18c--response-rating--quality) | Response Rating & Quality | ✅ Shipped |
| [Wave 19](#wave-19--context-injection--prompt-engineering) | Context Injection & Prompt Engineering | ✅ Shipped |
| [Wave 19B](#wave-19b--interactive-overlays) | Interactive Overlays | ✅ Shipped |
| [Wave 20](#wave-20--collaboration-handoff-ux) | Collaboration Handoff UX | ✅ Shipped |
| [Wave 21](#wave-21--command-palette--tab-complete) | Command Palette & Tab-Complete | ✅ Shipped |
| [Wave 22](#wave-22--animated-progress-bars--celebrations) | Animated Progress Bars & Celebrations | ✅ Shipped |
| [Wave 23](#wave-23--visual-hierarchy-renaissance--dashboard-elevation) | Visual Hierarchy Renaissance & Dashboard Elevation | ✅ Shipped |
| [Wave 24](#wave-24--terminal-preview--focused-inspection) | Terminal Preview & Focused Inspection | ✅ Shipped |
| [Wave 25](#wave-25--multi-pane-layout-presets) | Multi-Pane Layout Presets | ✅ Shipped |
| [Wave 26](#wave-26--session-mood-celebration--emotional-feedback) | Session Mood, Celebration & Emotional Feedback | ✅ Shipped |
| [Wave 27](#wave-27--live-dashboard-shares--operator-visibility) | Live Dashboard Shares & Operator Visibility | ✅ Shipped |
| [Wave 28](#wave-28--gesture-language--predictive-affordances) | Gesture Language & Predictive Affordances | ✅ Shipped |
| [Wave 29](#wave-29--narrative-recaps--session-storytelling) | Narrative Recaps & Session Storytelling | ✅ Shipped |
| [Wave 30](#wave-30--premium-motion--choreography-layer) | Premium Motion & Choreography Layer | ✅ Shipped |
| [Wave 31](#wave-31--intelligent-command-suggestions--inline-assist) | Intelligent Command Suggestions & Inline Assist | ✅ Shipped |
| [Wave 32](#wave-32--instant-replay--session-bookmarks) | Instant Replay & Session Bookmarks | ✅ Shipped |
| [Wave 33](#wave-33--command-chaining--workflow-macros-20) | Command Chaining & Workflow Macros 2.0 | ✅ Shipped |
| [Wave 34](#wave-34--ai-quality--experimentation-loops) | AI Quality & Experimentation Loops | ✅ Shipped |
| [Wave 35](#wave-35--long-form-reporting--export-suites) | Long-Form Reporting & Export Suites | ✅ Shipped |
| [Wave 36](#wave-36--workspace-state--ide-like-recovery) | Workspace State & IDE-Like Recovery | ✅ Shipped |
| [Wave 37](#wave-37--pattern-library--workflow-templates) | Pattern Library & Workflow Templates | ✅ Shipped |
| [Wave 38](#wave-38--multi-actor-planning--risk-aware-handoffs) | Multi-Actor Planning & Risk-Aware Handoffs | ✅ Shipped |
| [Wave 39](#wave-39--learned-routing--personalized-quality-loops) | Learned Routing & Personalized Quality Loops | ✅ Shipped |
| [Wave 40](#wave-40--long-running-automation-dashboard--operator-intelligence) | Long-Running Automation Dashboard & Operator Intelligence | ✅ Shipped |
| [Wave 41](#wave-41--incident-log--operator-resolution) | Incident Log & Operator Resolution | ✅ Shipped |
| [Wave 42](#wave-42--source-rendering-reliability) | Source Rendering Reliability | ✅ Shipped |
| [Wave 43](#wave-43--context--token-intelligence) | Context & Token Intelligence | ✅ Shipped |
| [Wave 44](#wave-44--startup--first-run-polish) | Startup & First-Run Polish | ✅ Shipped |
| [Wave 45](#wave-45--context-pressure-guardrails) | Context Pressure Guardrails | ✅ Shipped |

---

## Wave 1 — Foundation

**Status: ✅ Shipped**

Covers the baseline UX that makes the terminal usable and readable.

| Feature | Description | Shipped? |
|---|---|---|
| Bold blue prompt | `openclaw ❯` in bold blue (`_BBL`) replaces plain `openclaw>` | ✅ |
| `[autoroute:off]` badge | Self-describing label replaces cryptic `[no-route]` | ✅ |
| Loading spinner | Braille spinner with `💬 Thinking…` while AI works | ✅ |
| Response separator | Dim blue `─────` rule between prompt and response | ✅ |
| Response preprocessing | Strip citation markers, `_via model_` trailers, Sources section | ✅ |
| Table conversion | Pipe-in-bullet patterns → proper markdown tables | ✅ |
| Table width cap | Tables scaled to terminal width; cells truncated with `…` | ✅ |
| ANSI fallback | `_render_markdown_ansi()` renders markdown when Rich isn't available | ✅ |
| Checkpoint silence | `/exec` and `/edit` no longer print noisy checkpoint recovery messages | ✅ |

---

## Wave 2 — Rich Rendering

**Status: ✅ Shipped**

**Goal:** Make AI responses look like polished documentation — correct headings,
syntax-highlighted code blocks, formatted tables, and a clean citations panel.
Every element should be visually distinct and easy to scan.

### Features

| Feature | Description |
|---|---|
| Heading levels | H1 bold+underline, H2 bold, H3 dim bold — apply in ANSI fallback renderer |
| Inline code | Backtick spans rendered in cyan (`_BCY`) even in ANSI fallback |
| Code block highlighting | Fenced ` ``` ` blocks rendered in dim + border in ANSI fallback; Rich uses `Syntax` for named languages |
| Horizontal rules | `---` lines rendered as full-width dim `─` lines in ANSI fallback |
| Numbered lists | `1.` prefix items preserved and indented correctly |
| Nested bullets | Indented `  -` / `  •` items rendered with extra indent level |
| Blockquotes | `>` lines rendered with a left bar `▌` and dim style |
| Bold/italic spans | `**bold**` → ANSI bold; `*italic*` → ANSI italic in fallback |
| Sources panel | Extracted sources rendered in a dim-bordered Rich Panel titled `Sources` |
| Empty response guard | If body is empty after preprocessing, print a dim `(no response)` instead of nothing |

### Key Code Locations

| Item | Location |
|---|---|
| `_render_markdown_ansi()` | `~L2134` — ANSI fallback markdown renderer |
| `_apply_inline_ansi()` | `~L2125` — inline span handler (bold, italic, code) |
| `print_response()` | `~L2335` — calls preprocessor, renders body + sources |
| `_preprocess_response_text()` | `~L2295` — strips noise, returns `(body, sources)` |
| ANSI constants | `~L96` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — ANSI heading/list/blockquote renderer | Implementation Agent A | `src/openclaw_cli.py` (only `_render_markdown_ansi`, `_apply_inline_ansi`) |
| B — Rich Syntax code blocks | Implementation Agent B | `src/openclaw_cli.py` (only `print_response` Rich branch) |
| C — Sources panel polish | Implementation Agent C | `src/openclaw_cli.py` (only the sources panel block in `print_response`) |
| D — Tests + validation | Test Agent | read-only except `tests/test_openclaw_cli.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md` only |

### Done-When

- [x] H1/H2/H3 render as visually distinct levels in both Rich and ANSI fallback
- [x] Inline backtick code renders in cyan
- [x] Fenced code blocks have a visible border and dim background in ANSI fallback; syntax color in Rich
- [x] Blockquotes have a `▌` left bar
- [x] Sources section appears in a separate dim panel, not inline in the body
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 3 — Live Streaming

**Status: ✅ Shipped**

**Goal:** Make the agent feel alive. Instead of a spinner followed by a wall of
text, stream the response token-by-token (typewriter effect). Show elapsed time
and token count after the response completes.

### Features

| Feature | Description | Shipped? |
|---|---|---|
| Elapsed timer | After the response ends, print `⏱ 2.3s  •  312 tokens  •  model` in dim below the separator | ✅ |
| Token + model footer | Unified `⏱ Xs  •  N tokens  •  model-name` footer replaces the old separate model/token lines | ✅ |
| Ctrl-C cleanup | Ctrl-C during the spinner prints `⌨ [interrupted]` and returns to the prompt cleanly | ✅ |
| `--no-stream` flag | `CliConfig.no_stream` field + `--no-stream` CLI flag remains the explicit opt-out for interactive streaming | ✅ |
| Streaming output | Interactive TTY chat now uses the backend SSE endpoint and prints response chunks as they arrive; non-TTY, compact, and JSON flows still fall back to the buffered path | ✅ |

### Done-When

- [x] `⏱ Xs  •  N tokens  •  model` printed in dim below each response
- [x] Ctrl-C during spinner prints `[interrupted]` and returns to prompt cleanly
- [x] `--no-stream` flag wired into `CliConfig` and argparse
- [x] 180 tests pass
- [x] Deployed to macbook
- [x] Streaming response chunks now appear incrementally through the backend SSE endpoint when interactive streaming is enabled

---

## Wave 4 — Interactivity

**Status: ✅ Shipped**

**Goal:** Make the REPL feel like a modern shell — tab completion, history
navigation, inline help, and fuzzy command search.

### Features

| Feature | Description | Shipped? |
|---|---|---|
| Tab completion | `Tab` completes `/command` names (and aliases) via `readline.set_completer` | ✅ |
| History navigation | Up/Down arrows navigate history; persists across restarts via readline | ✅ |
| History search | Ctrl-R reverse-searches history (provided by readline) | ✅ |
| Inline slash help | `/cmd ?` prints that command's description and aliases, returns to prompt | ✅ |
| Command fuzzy match | Mistyped `/commnad` → `Did you mean /command?` via `difflib.get_close_matches` | ✅ |
| `_make_completer(registry)` | Helper function that builds a readline completer from the command registry | ✅ |
| `prompt_toolkit` integration | Optional interactive-TTY prompt session for richer editing, completion, and multiline compose | ⚠️ Deferred follow-up — keep `readline` / plain-input fallback contract |

Wave 4's shipped baseline is still the existing `readline`-first REPL. The
`prompt_toolkit` item remains a follow-up shell-input upgrade rather than a
retcon of the already-shipped tab completion, history, inline help, or fuzzy
match work.

### Implementation notes

- Tab completion uses `readline.set_completer` + `readline.parse_and_bind("tab: complete")` — no new dependencies
- Fuzzy suggestions use stdlib `difflib.get_close_matches(cutoff=0.6)`
- All features gracefully no-op when `readline is None` (non-POSIX platforms)
- If the `prompt_toolkit` follow-up ships, keep it scoped to the interactive TTY
  prompt path only; plain mode, non-TTY, scripted use, and missing-dependency
  environments must still preserve the simpler fallback path

### Done-When

- [x] Tab completes `/` commands (names and aliases)
- [x] Up/Down arrow navigates history; history persists across restarts
- [x] Ctrl-R launches reverse history search (via readline)
- [x] `/cmd ?` prints usage and returns to prompt
- [x] Mistyped command shows `Did you mean /X?` suggestion
- [x] 180 tests pass
- [x] Deployed to macbook
- [ ] ⚠️ Optional `prompt_toolkit` prompt session follow-up (deferred — current
      readline-first baseline remains sufficient)

### Key Code Locations

| Item | Location |
|---|---|
| `run_chat()` prompt input | `~L5310` — currently uses `input()` or `readline` |
| Slash command registry | `build_chat_command_registry()` `~L4470` |
| Shell history persistence | `src/openclaw_cli_sessions.py` |
| `CliConfig` | `~L347` — add `no_readline: bool = False` |

### Dependencies

- `prompt_toolkit` — optional dependency for the richer interactive-TTY prompt
  path; declare it explicitly only when that follow-up lands
- Guard imports so startup still succeeds without `prompt_toolkit`, and preserve
  the fallback stack: `prompt_toolkit` (when available and appropriate) →
  `readline` → plain `input()`

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — `prompt_toolkit` session + tab completer | Implementation Agent A | `src/openclaw_cli.py` (only `run_chat` input section and completer class) |
| B — History persistence | Implementation Agent B | `src/openclaw_cli_sessions.py` (only history load/save functions) |
| C — Inline help + fuzzy match | Implementation Agent C | `src/openclaw_cli.py` (only the unknown-command handler and `/cmd ?` path) |
| D — Multiline + paste | Implementation Agent D | `src/openclaw_cli.py` (only prompt session config) |
| E — Tests + validation | Test Agent | `tests/test_openclaw_cli.py` |
| F — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] Tab completes `/` commands (names and first argument) (shipped: `_SlashCompleter` + `readline.set_completer` in openclaw_cli.py:4505–4553)
- [x] Up/Down arrow navigates history; history persists across restarts (shipped: `readline.read_history_file` / `write_history_file` via `HISTORY_FILE` in openclaw_cli.py:3884–3902)
- [x] Ctrl-R launches reverse history search (shipped: emacs mode set at openclaw_cli.py:4514; Ctrl+R documented in help table at openclaw_cli.py:4348)
- [x] `/cmd ?` prints usage and returns to prompt (shipped: inline-help branch at openclaw_cli.py:4686–4691)
- [x] Mistyped command shows "Did you mean /X?" suggestion (shipped: openclaw_cli.py:4715)
- [x] Falls back to `readline` or plain `input()` when `prompt_toolkit` is
      missing, bypassed, or unavailable in the current environment (shipped: `_overlay_available()` guard + PromptSession None-check at openclaw_cli.py:4542)
- [x] 180 tests pass (600 passed, 0 failing — pytest tests/test_openclaw_cli.py -q)
- [ ] Deployed to macbook

---

## Wave 5 — Status Layer

**Status: ✅ Shipped**

**Goal:** Give the user constant situational awareness — a persistent status bar
showing session state, active model, token budget, and routing mode. File edits
show a diff preview before committing.

### Features

| Feature | Description | Shipped? |
|---|---|---|
| Status bar | Dim line after each AI response: `📍 session…  ·  💬 N turns  ·  autoroute on/off` | ✅ |
| Error recovery hint | On AI error, print dim `💡 /retry to resend  ·  /reset to clear history` | ✅ |
| Session badge in prompt | Cyan `[abc123de…]` in prompt when session is active; yellow `[autoroute:off]` when autoroute disabled | ✅ |
| `_print_status_bar()` helper | New function — prints status bar, respects TTY/Rich guard | ✅ |
| Routing decision display | Already shipped in Wave 1 via `_format_route_announcement` | ✅ |
| File edit preview / diff confirm | Preview now prints before approval/dry-run decisions, including the unified diff, compact review lines, and explicit trust/recovery cues at approval time |
| Token budget warning | Lightweight token heuristics shipped later in Waves 43–45; stronger model-aware/proactive guardrails remain deferred |

### Done-When

- [x] Status bar prints after each AI response: turns in context, autoroute state, optional session
- [x] Error recovery hint appears after AI errors
- [x] Session badge in prompt is cyan; `autoroute:off` badge is yellow (distinct, warning-like)
- [x] 180 tests pass
- [x] Deployed to macbook
- [x] `/edit` now shows a unified diff before applying changes (shipped later)
- [x] richer `/edit` approval + diff review polish now surfaces compact review, trust, and recovery cues
- [x] lightweight token/context guidance now exists via `/tokeninfo` and later guardrail slices
- [ ] ⚠️ per-model token limits and proactive overflow warnings remain deferred

---

## Wave 6 — Themes & Personalization

**Status: ✅ Shipped**

**Goal:** Let users customize the look and feel — color themes, emoji sets, and
layout density — stored in a config file and selectable at runtime.

### Features

| Feature | Description |
|---|---|
| Color themes | Built-in themes: `default`, `green`, `yellow`, `magenta`, `cyan`, `mono`. Selected via `/theme NAME` |
| Emoji set | `/emoji off` replaces all status emoji with ASCII equivalents |
| Layout modes | `/layout compact` (no separator/status bar) vs `/layout normal` (default) |
| Persist preferences | Theme, emoji, layout written to `~/.openclaw/prefs.json` and loaded at startup |
| `/theme list` | Lists available themes with a colored preview swatch |

### Key Code Locations

| Item | Location |
|---|---|
| `_PREFS`, `_THEMES`, `_EMOJI_FALLBACKS` | After constants block (L~133) |
| `_load_prefs()`, `_save_prefs()` | After constants block |
| `_e()`, `_theme_style()`, `_theme_ansi()` | After prefs helpers |
| `_cmd_theme`, `_cmd_emoji`, `_cmd_layout` | Before `build_chat_command_registry()` |
| Registry entries | In `build_chat_command_registry()` |
| `run_chat()` | Calls `_load_prefs()` at top |

### Done-When

- [x] `/theme green` changes separator colour; persists to `~/.openclaw/prefs.json`
- [x] `/theme list` shows themed swatches
- [x] `/emoji off` replaces all emoji with ASCII labels
- [x] `/layout compact` removes separator + status bar
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 7 — Dashboard & History

**Status: ✅ Shipped**

**Goal:** Give the user a full session browser, conversation export, and usage
statistics — turning OpenClaw into a durable personal AI assistant with memory.

### Features

| Feature | Description |
|---|---|
| `/sessions` | Lists recent sessions: name, date, command count |
| `/sessions search QUERY` | Filters sessions by title or summary |
| `/sessions open NAME` | Prints `openclaw session resume <id>` instructions |
| `/export [md\|json]` | Exports current conversation to `~/Downloads` |
| `/stats` | Shows: total sessions, commands, edits, checkpoints, top dirs |
| Auto-summarize | On Ctrl-D exit, saves last prompt as session title if title is generic |

### Key Code Locations

| Item | Location |
|---|---|
| `_cmd_sessions` | Before `build_chat_command_registry()` |
| `_cmd_export` | Before `build_chat_command_registry()` |
| `_cmd_stats` | Before `build_chat_command_registry()` |
| `auto-summarize` | EOFError handler in `run_chat()` |
| Fixed Wave 6 handler signatures | `_cmd_theme`, `_cmd_emoji`, `_cmd_layout` now use `ctx: ChatCommandContext` |

### Done-When

- [x] `/sessions` lists sessions sorted by date with message count (Rich table or ANSI table)
- [x] `/sessions search foo` filters by title or summary
- [x] `/sessions open NAME` prints resume instructions
- [x] `/export md` writes a formatted markdown file to `~/Downloads`
- [x] `/export json` writes raw JSON conversation to `~/Downloads`
- [x] `/stats` shows session count, commands, edits, checkpoints, top dirs
- [x] Auto-summarize runs on Ctrl-D and saves to session metadata
- [x] Wave 6 handler signature bug fixed (`ctx: ChatCommandContext`)
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 8 — Session Intelligence

**Status: ✅ Shipped**

**Goal:** Build an intelligence layer on top of the session browser from Wave 7.
Once users can list and reopen sessions, the next step is helping them find the
*right* session quickly, understand whether it is still actionable, and resume
work with the right context instead of browsing blind.

### Features Shipped

| Feature | Description |
|---|---|
| `/tag [add\|rm\|list] <tag>` | Tag sessions by project/theme; tags persist in metadata |
| `/resume [last\|<id>]` | Find the most-recent other session, print resume instructions |
| `/sessions related` | From the active session, list sessions with same cwd/files/plan/task |
| Session badges | `/sessions` now shows `●/○`, `stale`, `outputs`, and `#tags` per row |
| Auto-title on first turn | After the first AI response, session title is set from the user's prompt |
| `tags` field on `SessionSummary` | Backward-compatible `list[str]` field added to sessions.py |

### Key Code Locations

| Item | Location |
|---|---|
| `tags` field | `src/openclaw_cli_sessions.py` `SessionSummary` |
| `_session_badges()` | Before `_cmd_sessions` in `openclaw_cli.py` |
| `_session_is_stale()` | Before `_cmd_sessions` in `openclaw_cli.py` |
| `_cmd_tag` | Before `build_chat_command_registry()` |
| `_cmd_resume` | Before `build_chat_command_registry()` |
| Auto-title | `run_chat()` — after first `history.extend()` |

### Done-When

- [x] `/sessions related` ranks nearby sessions by cwd/files/plan/task overlap
- [x] `/sessions` shows stale, has-outputs, and tag badges
- [x] Session tags: `/tag add`, `/tag rm`, `/tag list`
- [x] `/resume last` prints most-recent session + resume command
- [x] Auto-title: first real turn updates generic session title
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 9 — Artifact Studio & Replay

**Status: ✅ Shipped**

**Goal:** Turn saved outputs and session exports into first-class artifacts.
Users should be able to replay prior work, export polished deliverables, and
promote useful outputs without manually opening files in the shell.

### Features Shipped

| Feature | Description |
|---|---|
| `/replay [session-id]` | Re-prints current or a past session's conversation with clean formatting |
| `/export html` | Exports session as standalone styled HTML to `~/Downloads` |
| `/export md` | Exports as readable Markdown transcript (existed in Wave 7; now also via `/export markdown`) |
| `/export json` | Raw JSON conversation dump |
| `/outputs promote <index> <name>` | Copies a saved output to a stable named file in the same dir |

### Key Code Locations

| Item | Location |
|---|---|
| `_cmd_replay` | Before `build_chat_command_registry()` in `openclaw_cli.py` |
| `/export html` | In `_cmd_export` in `openclaw_cli.py` |
| `/outputs promote` | At top of `_cmd_outputs` in `openclaw_cli.py` |

### Done-When

- [x] `/replay` reprints the current session transcript with readable formatting
- [x] `/replay <session>` resolves a prior session by id prefix or fuzzy title
- [x] `/export md`, `/export html`, and `/export json` all work inside the REPL
- [x] `/outputs promote <index> <name>` copies to stable named file
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 10 — Guided Workflows & Recovery

**Status: ✅ Shipped**

**Goal:** Make the CLI feel more like an expert co-pilot than a raw command
surface. The UI should suggest the next best move, explain failures in context,
and give users a visible recovery path when routes, edits, or exec steps go
sideways.

### Features

| Feature | Description |
|---|---|
| First-run guidance panel | New sessions show a short, dismissible checklist for `/context`, `/files`, `/plan`, and `/outputs` |
| `/help search QUERY` | Filters the in-REPL command list by name, alias, and description |
| Contextual next-step hints | After `/session`, `/context`, empty `/outputs`, or failed `/plan`/`/task` linking, show targeted next actions |
| Approval recap | After risky `/exec` or `/edit`, print a compact summary of what was approved and how to recover |
| `/rollback list` | Lists recent routed checkpoints with step label, time, and recoverability |
| Recovery hints | Failed routed actions print the exact rollback or manual recovery command when available |
| Route explanation panel | Auto-routed prompts can optionally show why a route was chosen and what context influenced it |
| Dense error cleanup | Usage errors are normalized so every command prints one clear fix-it line instead of mixed styles |

### Key Code Locations

| Item | Location |
|---|---|
| Help table + command descriptions | `src/openclaw_cli.py` `print_chat_help()` `~L5219` |
| Unknown-command / route handling | `src/openclaw_cli.py` `run_chat()` `~L5620-L5750` |
| Session context commands | `src/openclaw_cli.py` `_cmd_session()`, `_cmd_context()`, `_cmd_outputs()` |
| Approval UX | `src/openclaw_cli_actions.py` |
| Routed checkpoint persistence | `src/openclaw_cli_sessions.py` routed checkpoint helpers `~L607+` |
| Rollback command | `src/openclaw_cli.py` `_cmd_rollback()` `~L4477` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Help search + guidance text | Implementation Agent A | `src/openclaw_cli.py` (`print_chat_help()` + related help handlers only) |
| B — Recovery UX + rollback listing | Implementation Agent B | `src/openclaw_cli.py`, `src/openclaw_cli_sessions.py` (checkpoint listing path only) |
| C — Approval recap + fix-it errors | Implementation Agent C | `src/openclaw_cli.py`, `src/openclaw_cli_actions.py` |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py`, `tests/test_openclaw_cli_sessions.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] New sessions surface a concise first-run checklist without polluting non-TTY output
- [x] `/help search route` returns matching commands and aliases
- [x] Empty-state commands suggest the next likely command instead of stopping at “none found”
- [x] `/rollback list` shows recent checkpoints and whether each one is recoverable
- [x] Failed routed actions print a concrete recovery hint when one exists (shipped: `recovery_hint` param in `openclaw_cli_actions.py:160,242`; `_build_error_recovery_hints()` in openclaw_cli.py:1410)
- [x] Approval flows end with a short "what happened / how to recover" recap for risky actions (shipped: `_print_approval_recap()` at openclaw_cli_actions.py:545; called at openclaw_cli_actions.py:438)
- [x] Usage errors follow one consistent style across REPL commands (shipped: `_print_error` / `_print_usage` helpers enforced across modules; ≤2 bare `print("Usage` calls remain as documented exceptions — `openclaw_cli_cmd_core.py:1582`, `openclaw_cli_cmd_content.py:236`)
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 11 — Workspace Handoffs

**Status: ✅ Shipped**

**Goal:** Make it easy to pause work on one machine and resume it later with the
right context intact. Users should be able to capture a handoff bundle from the
current session and reopen it with cwd, tracked files, saved outputs, and
plan/task context already lined up.

### Features

| Feature | Description |
|---|---|
| `/handoff create` | Saves a resumable handoff bundle for the current session with cwd, files, plan/task ids, last summary, and recent outputs |
| `/handoff list` | Lists recent handoff bundles with timestamps and source session ids |
| `/handoff open NAME` | Rehydrates a handoff into a new or existing session and prints the exact resume command |
| Snapshot manifest | Handoffs include a human-readable manifest summarizing session state and next recommended action |
| File availability check | Missing tracked files are flagged up front before the handoff is resumed |
| Output pinning | Users can pin one or more saved outputs so they are always included in the handoff bundle |
| Handoff notes | `/handoff note "..."` attaches a short operator note visible on resume |
| Session-to-session bridge | `/session` shows whether the current session was restored from a handoff and links back to the source session |

### Recommended Approach

- **Treat handoffs as derived session artifacts, not a second session system.**
  Reuse `export_session()` as the starting payload, then layer a small
  handoff-specific manifest on top so the feature does not fork session
  persistence logic.
- **Store handoff bundles beside existing session data.** Keep them under the
  CLI data root with stable ids and timestamps so `list`, `open`, and cleanup
  all use the same file layout conventions as sessions and outputs.
- **Restore defensively.** `open` should validate cwd existence, tracked-file
  reachability, and pinned output presence before mutating session state. When
  something is missing, restore what is safe and surface a concise warning block
  instead of failing the whole handoff.
- **Prefer references over full duplication where possible.** The manifest
  should point at source-session ids and pinned output names first, only
  embedding additional data when a future cross-machine handoff really demands
  it.
- **Keep the UX resumable, not magical.** Every restore path should end by
  printing the exact next command (`openclaw --session …` or equivalent) and a
  short explanation of what context was recovered.

### Key Code Locations

| Item | Location |
|---|---|
| Session metadata + outputs | `src/openclaw_cli_sessions.py` `SessionSummary`, `list_saved_outputs()`, `export_session()` |
| REPL session/context commands | `src/openclaw_cli.py` `_cmd_session()`, `_cmd_context()`, `_cmd_outputs()` |
| Session CLI commands | `src/openclaw_cli.py` `handle_session_command()` `~L5764` |
| Parser wiring | `src/openclaw_cli.py` `build_parser()` `~L6691` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Handoff persistence helpers | Implementation Agent A | `src/openclaw_cli_sessions.py` only |
| B — REPL `/handoff` commands | Implementation Agent B | `src/openclaw_cli.py` (new command handlers + registry/help entries only) |
| C — CLI handoff create/list/open flows | Implementation Agent C | `src/openclaw_cli.py` (`handle_session_command()` + parser wiring only) |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py`, `tests/test_openclaw_cli_sessions.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] `/handoff create` writes a resumable bundle with session metadata and recent outputs
- [x] `/handoff list` shows recent bundles with source-session context
- [x] `/handoff open NAME` restores the handoff into a usable session
- [x] Missing tracked files are flagged during restore, not after the first failed command
- [x] Pinned outputs are preserved in handoff manifests
- [x] Session detail shows when a session originated from a handoff
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 12 — Automation Control Tower

**Status: ✅ Shipped**

**Goal:** Surface long-running automation state as clearly as chat state. Plans,
watch loops, retries, and interventions should feel inspectable and steerable
from inside the CLI instead of buried in JSON state files.

### Features

| Feature | Description |
|---|---|
| `/watch status` | Shows the active watch session, last poll, retry count, checkpoint count, and next action |
| `/watch history` | Replays recent watch iterations with status badges and summaries |
| `/watch retry-limit N` | Updates the automatic retry ceiling for the active watch session |
| `/watch intervene "..."` | Adds a structured operator intervention note into watch state for later replay/audit |
| `/plan status` | Prints linked plan progress, current step, and most recent completion/failure summary |
| `/plan focus` | Collapses plan output to the current step and next pending step only |
| Automation badges | `/session` and status bars surface active watch/plan state without requiring a separate command |
| Retry explanations | When watch resumes after a transient failure, the CLI prints what happened and why it retried |

### Recommended Approach

- **Read from persisted state first, then add richer views.** `save_watch_state`
  and `load_watch_state` already hold most of the useful automation metadata, so
  the fastest path is to add renderers over that state rather than inventing new
  automation storage.
- **Separate status from control.** Implement read-only commands like
  `/watch status`, `/watch history`, and `/plan status` first, then add mutating
  controls like `/watch retry-limit` and `/watch intervene` once the underlying
  status views are stable.
- **Keep automation summaries compact by default.** The right default is a
  single-screen operator view: active item, last outcome, next action, retry
  count, and checkpoint count. Longer histories can expand on demand.
- **Surface automation state in existing touchpoints.** The best UX payoff comes
  from reusing `_print_session_summary()` and the status bar so users do not
  need to remember a new command just to discover that watch or plan work is
  active.
- **Explain retries as a policy decision.** When the CLI retries automatically,
  show which error class was treated as transient, how many retries remain, and
  when the next attempt will occur.

### Key Code Locations

| Item | Location |
|---|---|
| Watch state helpers | `src/openclaw_cli_sessions.py` `save_watch_state()`, `load_watch_state()` |
| Watch command flow | `src/openclaw_cli.py` `handle_watch_command()` `~L6167` |
| Plan command flow | `src/openclaw_cli.py` `handle_plan_command()` `~L5796` |
| Session summary + status output | `src/openclaw_cli.py` `_print_session_summary()`, `_print_status_bar()` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Watch-state UX helpers | Implementation Agent A | `src/openclaw_cli_sessions.py` only |
| B — REPL/CLI watch status commands | Implementation Agent B | `src/openclaw_cli.py` (`handle_watch_command()` + new chat handlers only) |
| C — Plan status/focus views | Implementation Agent C | `src/openclaw_cli.py` (`handle_plan_command()` + plan status renderers only) |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py`, `tests/test_openclaw_cli_sessions.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] `/watch status` and `/watch history` expose retry/checkpoint state without reading raw files
- [x] `/watch retry-limit N` updates persisted retry behavior for the active watch session
- [x] `/watch intervene` records operator notes that appear in later history output
- [x] `/plan status` and `/plan focus` make linked plan progress readable in the CLI
- [x] Session/status output surfaces active automation state by default (shipped: `_watch_status_cell()` at `openclaw_cli_ui_utils.py:377–401` injects `⟳ watching` / `↺ retrying` into the default status bar; called at `openclaw_cli_ui_utils.py:438`)
- [x] Retry paths explain when the CLI auto-retried and why (shipped: print of auto-retry message at openclaw_cli_watch.py:1417)
- [x] 180 tests pass
- [x] Deployed to macbook

---

## Wave 13 — Trust & Explainability

**Status: ✅ Shipped**

**Goal:** Help users understand why the CLI took an action, what context it
used, and what confidence they should place in the result. The agent should
feel inspectable, not mysterious.

### Features

| Feature | Description |
|---|---|
| `/why` | Explains the last routing/tool decision using the same structured data already captured in session events |
| Confidence badges | Auto-routed actions and plan decompositions show a small confidence label in their announcement line |
| Context receipt | After `analyze`, `research`, and `write`, users can inspect the exact cwd/files/plan/task grounding block that was injected |
| Source provenance | Saved outputs and exports include lightweight provenance metadata: source session, command, model, and timestamp |
| Decision trace | `/events` can collapse to a decision-only view for route, approval, rollback, and retry events |
| Approval rationale | Approval prompts summarize why the action was classified at its current risk level |
| Output lineage | `/outputs <n>` shows which prompt or command produced the artifact |
| Ambiguity warnings | When a route almost fired but stayed in chat, the CLI explains which ambiguity check blocked it |

### Recommended Approach

- **Make explainability a rendering layer over existing events.** The safest
  implementation path is to enrich `append_event()` metadata for routes,
  approvals, outputs, and retries, then build `/why` and filtered `/events`
  views on top of that recorded evidence.
- **Prefer bounded, human-readable receipts.** Explanations should summarize the
  decisive inputs — cwd, tracked files, linked plan/task ids, confidence, risk
  tier — without dumping whole prompts or massive payloads back to the screen.
- **Reuse provenance everywhere artifacts already exist.** `save_output()`,
  session export, and output preview flows should share one provenance shape so
  lineage stays consistent across `/outputs`, `/export`, and any future handoff
  features.
- **Keep confidence visible but lightweight.** Badges belong in existing route
  announcement lines and approval UI, not as separate panels unless the user
  explicitly asks for detail with `/why`.
- **Explain non-actions too.** The most trust-building behavior is not just
  explaining why something happened, but also why a route did *not* happen and
  why the prompt stayed in normal chat.

### Key Code Locations

| Item | Location |
|---|---|
| Route announcements + routing decisions | `src/openclaw_cli.py` route helpers and `run_chat()` |
| Session events | `src/openclaw_cli_sessions.py` `append_event()`, `load_events()` |
| Output helpers | `src/openclaw_cli_sessions.py` `save_output()`, `list_saved_outputs()`, `load_saved_output_preview()` |
| Approval prompts | `src/openclaw_cli_actions.py` |
| Event rendering | `src/openclaw_cli.py` `_cmd_events()` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Event/provenance metadata | Implementation Agent A | `src/openclaw_cli_sessions.py` only |
| B — `/why`, decision trace, and lineage views | Implementation Agent B | `src/openclaw_cli.py` (`_cmd_events()` + new handlers only) |
| C — Approval/routing confidence UX | Implementation Agent C | `src/openclaw_cli.py`, `src/openclaw_cli_actions.py` |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py`, `tests/test_openclaw_cli_sessions.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] `/why` explains the last route/tool decision from recorded session data (shipped: `_cmd_why` in openclaw_cli_cmd_core.py:466)
- [x] Auto-routed actions surface a visible confidence badge (shipped: `_confidence_badge()` in openclaw_cli_router.py:1361; printed inline after routing at router.py:1371–1393)
- [x] Users can inspect the exact grounding block used for the last analyze/research/write action (`_PREFS["_last_grounding_block"]` stored at `openclaw_cli.py:4731`; `/context last` displays it via `openclaw_cli_cmd_core.py:216`)
- [x] Saved outputs expose prompt/session lineage and provenance metadata (shipped: `.provenance.json` sidecar written by `save_output` in openclaw_cli_sessions.py:854; `load_output_provenance()` at sessions.py:864)
- [x] `/events` can filter down to decision-centric entries (shipped: `/events decisions [n]` filter in `_cmd_events` at openclaw_cli_cmd_session.py:77–78)
- [x] Approval prompts explain why a risk level was chosen (shipped: `_rationale_line` set to "CRITICAL risk: this action is irreversible…" / "HIGH risk: this action modifies the filesystem…" at `openclaw_cli_actions.py:363–370`)
- [x] Ambiguous prompts that stay in chat can explain the top blocking reason (shipped: `_hint_rationale = (route_decision.rationale or "")[:80]` printed as `↳ stayed in chat — confidence below threshold · {_hint_rationale}` at `openclaw_cli.py:4759–4760`)
- [x] 180 tests pass (600 passed, 0 failing — pytest tests/test_openclaw_cli.py -q)
- [ ] Deployed to macbook

---

## Wave 14 — Composer & Input Flow

**Status: ✅ Shipped**

**Goal:** Make entering prompts feel as polished as reading responses. The REPL
should support drafting, multiline composition, safe pastes, and clearer input
state so users can work on complex prompts without fighting the terminal.

### Features

| Feature | Description |
|---|---|
| Multiline composer | Toggle a lightweight multiline input mode for longer prompts and edits |
| Draft buffer | `/draft save`, `/draft load`, `/draft clear` keeps unfinished prompts without sending them |
| Paste guard | Large pastes show a short preview and confirmation hint before execution-sensitive routing |
| Input mode badge | Prompt indicates when the user is in draft, multiline, or normal mode |
| Slash preview | Typing `/cmd ?` or partial slash commands shows a compact inline preview before execution |
| Prompt templates | `/template list` and `/template use <name>` insert reusable prompt scaffolds into the composer |
| Send/edit split | Draft mode distinguishes “edit current draft” from “submit now” to prevent accidental sends |
| Recover last prompt | After Ctrl-C or failed submission, the previous unsent prompt can be restored quickly |

### Recommended Approach

- **Build on the existing readline-first model.** Start with a small draft buffer
  abstraction around the current `run_chat()` input loop so the feature works
  without introducing a heavy new dependency.
- **Treat multiline as a mode, not a different REPL.** The same slash-command
  registry, routing, and approval behavior should apply after draft submission so
  there is only one execution path.
- **Protect high-risk pastes.** The best initial version is not a generic paste
  blocker; it is a targeted safeguard that triggers when a large pasted block
  would route to `/exec`, `/edit`, or plan decomposition.
- **Persist only what users expect.** Saved templates should be durable, while
  the active draft can stay ephemeral unless the user explicitly saves it.
- **Keep restore paths frictionless.** After interruption or submission failure,
  print a one-line hint that the last draft can be restored instead of silently
  dropping input.

### Key Code Locations

| Item | Location |
|---|---|
| Main input loop | `src/openclaw_cli.py` `run_chat()` |
| Readline completion/help flow | `src/openclaw_cli.py` completer helpers + slash preview handling |
| Slash command registry | `src/openclaw_cli.py` `build_chat_command_registry()` |
| Session events | `src/openclaw_cli_sessions.py` `append_event()` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Draft/multiline state helpers | Implementation Agent A | `src/openclaw_cli.py` input loop helpers only |
| B — `/draft` + `/template` commands | Implementation Agent B | `src/openclaw_cli.py` registry/help + handlers only |
| C — Paste guard + restore-last UX | Implementation Agent C | `src/openclaw_cli.py` routing/input boundary only |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md` |

### Done-When

- [x] Multiline compose mode works without bypassing slash-command routing (shipped: `_read_multiline_input()` + `_multiline_mode` flag in openclaw_cli.py:408–4622; `/draft multiline on|off` in openclaw_cli_cmd_core.py:1463)
- [x] `/draft save`, `/draft load`, and `/draft clear` manage unsent prompts predictably (shipped: `_cmd_draft()` in openclaw_cli_cmd_core.py:1427; all three subcommands implemented)
- [x] Large risky pastes surface a preview-oriented safeguard before execution (shipped: paste-guard check at openclaw_cli.py:4671; `/pasteguard` toggle at cli.py:4437)
- [x] Prompt badges reflect normal vs draft vs multiline state (shipped: `draft_badge` in `_make_prompt()` at openclaw_cli.py:4074; multiline badge at 4073; both rendered at 4076-4080)
- [x] Interrupted or failed submissions can restore the last unsent prompt (shipped: `/draft restore` subcommand in openclaw_cli_cmd_core.py:1455)
- [x] 180 tests pass (600 passed, 0 failing — pytest tests/test_openclaw_cli.py -q)
- [ ] Deployed to macbook

---

## Wave 15 — Accessibility & Adaptive Layout

**Status: 🟡 Partial**

**Goal:** Make the terminal experience comfortable across more environments:
small windows, reduced-motion preferences, plain terminals, high-contrast use
cases, and assistive tooling. UX polish should not depend on one ideal terminal
setup.

### Features

| Feature | Description |
|---|---|
| Reduced-motion mode | Disables spinner animation and other motion-heavy affordances while preserving status cues |
| Screen-reader/plain mode | Simplifies prompt chrome, separators, badges, and panels into predictable text output |
| Adaptive width rules | Tables, panels, and status lines reflow more aggressively in narrow terminals |
| High-contrast preference | Stores a user-selectable high-contrast preference and applies a higher-contrast palette to shared CLI surfaces |
| Layout density controls | `/layout compact|normal|verbose|plain` expands the current layout model for accessibility contexts |
| Non-TTY parity audit | Rich-only affordances get an explicit plain-text equivalent instead of disappearing silently |
| Alert cues | Optional bell/text cue for long-running completions or approval-required states |
| Accessibility self-check | `/accessibility status` shows which terminal UX guards are currently active |

### Recommended Approach

- **Start from existing fallback paths.** The ANSI/plain-text rendering branches
  already exist, so the fastest win is to formalize them into named modes rather
  than layering new one-off flags on top.
- **Prefer explicit modes over inference.** Auto-detect terminal width and TTY
  status, but let users force `plain`, `reduced-motion`, or `high-contrast`
  behavior so accessibility never depends solely on heuristics.
- **Unify layout decisions in one place.** Width-aware truncation, separator
  suppression, and panel flattening should be driven by shared helpers, not
  scattered command-specific if/else branches.
- **Keep audible cues optional and minimal.** Alert bells should default off and
  pair with a visible text hint so they help rather than surprise.
- **Audit for parity, not visual sameness.** The goal is that every important UI
  state remains understandable in narrow, plain, or assistive environments even
  when the rich rendering is simplified.

### Key Code Locations

| Item | Location |
|---|---|
| Rich/ANSI guards | `src/openclaw_cli.py` rendering helpers and `_RICH_AVAILABLE` / TTY checks |
| Theme/layout prefs | `src/openclaw_cli.py` `_PREFS`, `_load_prefs()`, `_cmd_layout()` |
| Response/table rendering | `src/openclaw_cli.py` `print_response()`, `_render_markdown_ansi()`, `_render_table_ansi()` |
| Spinner/status helpers | `src/openclaw_cli.py` `_with_spinner()`, `_print_status_bar()` |

### Agent Lanes

| Lane | Agent | Files Owned |
|---|---|---|
| A — Accessibility prefs + mode helpers | Implementation Agent A | `src/openclaw_cli.py` prefs/render helpers only |
| B — Adaptive layout + width behavior | Implementation Agent B | `src/openclaw_cli.py` rendering helpers only |
| C — Reduced-motion/plain-mode UX | Implementation Agent C | `src/openclaw_cli.py` spinner/status/prompt output only |
| D — Tests + validation | Test Agent | `tests/test_openclaw_cli.py` |
| E — Docs | Docs Agent | `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md`, `docs/UX_IMPROVEMENTS.md` |

### Done-When

- [x] Reduced-motion mode removes animation without losing progress visibility
- [x] Plain/screen-reader mode keeps prompts and core response surfaces understandable
- [x] Narrow-terminal/plain-mode rendering has dedicated adaptive-width logic in shared helpers
- [x] High-contrast palette rendering is applied to shared CLI separators, borders, and selected status surfaces
- [x] Expanded layout density presets `/layout compact|normal|verbose|plain` are implemented
- [x] `/accessibility status` reports active accessibility-related UX modes
- [x] Targeted CLI tests cover reduced-motion, plain-mode, and accessibility status behavior
- [x] Full `tests/test_openclaw_cli.py` suite is green (600 passed, 0 failing — pytest run verified 2026-W26)
- [ ] Deployed to macbook

---

## Wave 16 — Microinteractions & Feedback Density

**Status: 🟡 Partial**

**Goal:** Make the CLI feel more alive and legible between big features: quick
confirmations for common actions, clearer completion cues, gentler liveness for
long-running calls, and stronger but still accessible emphasis before risky
actions.

### Shipped in this slice

| Feature | Evidence |
|---|---|
| Shared compact confirmations | `/clear`, `/layout`, and `/accessibility` now render through `_print_feedback()` instead of bespoke strings |
| Reduced-motion liveness heartbeat | `_with_spinner()` now emits periodic text heartbeats in reduced-motion mode instead of going silent |
| Clear completion cue | `_with_spinner()` now ends with an explicit `response ready` confirmation |
| Risk emphasis before approvals | High/critical `/exec` and `/edit` print an extra warning + recovery hint before `request_cli_approval()` |
| Action-complete recaps | `/exec` and `/edit` now end with a compact completion line after the main result block |

### Deferred / not yet evidenced

- [ ] Additional watch-loop-specific liveness cues beyond existing progress lines
- [ ] Optional bell/alert cues
- [ ] Broader completion recaps for every command surface
- [x] Full `tests/test_openclaw_cli.py` suite is green (600 passed, 0 failing — pytest run verified 2026-W26)
- [ ] Deployed to macbook

### Validation

- Focused CLI pytest slice covering spinner, accessibility, `/clear`, `/exec`,
  `/edit`, and top-level `exec`/`edit` feedback paths passed.

---

## Wave 16B — Search, Aliases & Pins

**Status: ✅ Shipped** (`5d2a539`)

| Feature | Description |
|---|---|
| `/search <query>` | Full-text search current session events; matches highlighted in bold yellow |
| `/search --all <query>` | Cross-session search (last 200 sessions, up to 15 hits) |
| `/alias <name> <expansion>` | Define command shorthands stored in `_PREFS["aliases"]` |
| `/alias rm <name>` / `/alias` | Remove or list aliases; `_BUILTIN_COMMAND_NAMES` prevents shadowing |
| Alias expansion | Hooked into `run_chat()` before dispatch; one level only, no recursion |
| `/pin [name]` | Pin last AI response; auto-names `pin-1`, `pin-2` … |
| `/pin recall <name>` | Re-render a pinned response via `print_response()` |
| `/pin rm <name>` / `/pins` | Remove or list all pins |
| `_last_response_text` | Module-level global tracks latest AI response for `/pin` |

---

## Wave 17 — Theme Engine & Personalization

**Status: ✅ Shipped**

**Goal:** Make personalization feel intentional instead of incidental: safer
stored theme prefs, more expressive theme switching, and emoji customization
that covers status output as well as decorative UI icons.

### Features

| Feature | Description | Shipped? |
|---|---|---|
| Safe personalization normalization | Invalid stored `theme`, `emoji_pack`, or `layout` values are clamped back to supported defaults during load/save | ✅ |
| Theme preview | `/theme preview [name]` shows a live sample without persisting the choice | ✅ |
| Theme cycling | `/theme next` and `/theme prev` rotate through the built-in palette and persist the result | ✅ |
| Theme reset | `/theme reset` restores the default accent in one step | ✅ |
| Emoji packs | `/emoji pack classic|minimal|ascii` adds a real pack abstraction while preserving `/emoji on|off` | ✅ |
| Status-pack parity | `_status_emoji()` now respects the active pack so health/status badges also downgrade safely | ✅ |

### Evidence / Implementation Notes

- `src/openclaw_cli.py` now normalizes personalization prefs through
  `_normalize_personalization_prefs()`
- `/theme` now supports `list`, `preview`, `next`, `prev`, `reset`, and aliases
- `/emoji` now supports `status`, `preview`, and `pack <name>`
- Tests cover invalid-pref normalization, theme preview/cycling persistence,
  emoji-pack persistence, and ASCII-safe status badges

### Done-When

- [x] Theme switching is more expressive than simple `/theme NAME`
- [x] Emoji/theme customization remains fallback-safe for plain/non-Rich usage
- [x] Preference persistence guards against invalid stored personalization values
- [x] Docs and tests reflect the shipped Wave 17 slice
- [x] Full `tests/test_openclaw_cli.py` suite is green (600 passed, 0 failing — pytest run verified 2026-W26)
- [ ] Deployed to macbook

---

## Wave 18 — Macros & Command History

**Status: ✅ Shipped** (`HEAD`)

| Feature | Description |
|---|---|
| `/history [n]` | Show last N commands from input history (default 20) |
| `/history clear` | Clear command history |
| History recording | Every user input appended to `_PREFS["cmd_history"]` (capped at 50) in `run_chat()` |
| `/macro list` | List all saved macros with command counts |
| `/macro save <name> [last N]` | Save last N history entries as a named macro (default 5, max 20 commands) |
| `/macro show <name>` | Display commands stored in a macro |
| `/macro run <name>` | Execute macro's slash commands via registry dispatch; NL entries skipped with warning |
| `/macro rm <name>` | Delete a named macro |
| Storage | Macros in `_PREFS["macros"]` (max 30); history in `_PREFS["cmd_history"]` |

---

## Wave 18B — Performance Visibility

**Status: ✅ Shipped**

| Feature | Description |
|---|---|
| Session timing hints | `/session` now includes active watch phase, last run duration, and retry backoff totals when watch state exists |
| Watch timing summary | `/watch status` exposes active phase age plus last checkpoint duration |
| Retry/backoff cues | `/watch history` and checkpoint events include retry delay visibility |
| Approval timing cues | `/exec` and `/edit` now emit `approval` events and separate approval wait from execution/write time |
| Backward compatibility | Older watch state still renders by deriving timing from existing timestamps when explicit duration fields are missing |

---

## Wave 18C — Response Rating & Quality

**Status: ✅ Shipped**

| Feature | Description |
|---|---|
| `/rate [good/ok/bad/meh/1-5]` | Rate the last AI response, map it to score `1-5`, and persist it in `_PREFS["ratings"]` (cap 500) |
| Session event trail | Each rating appends a `rating` session event for later review/export |
| `/quality` | Show total rated responses, average score, a star-distribution chart, and the most recent ratings |
| `/ratehint [on|off]` | Toggle the post-response dim hint after each AI reply |
| Preference keys | `show_rate_hint` (default `True`) and `ratings` remain additive persisted settings |

---

## Wave 19 — Context Injection & Prompt Engineering

**Status: ✅ Shipped**

| Feature | Description |
|---|---|
| `/inject path` | Read file content into `_next_inject` (max 8000 chars, binary-safe) |
| `/inject --url URL` | Fetch URL content into `_next_inject` via `requests` (max 8000 chars) |
| `/inject clear` / `/inject status` | Clear or preview pending injection |
| Injection prepend | `run_chat()` prepends injection as an `[Injected context]` block, then consumes it after one send |
| `/system view\|set\|append\|clear` | Manage the persisted system prompt in `_PREFS["system_prompt"]` (max 2000 chars) |
| System prompt prepend | `run_chat()` prepends system prompt as a `[System context]` block to every AI message |
| `/context update` | Shows the system-prompt preview and pending injection count |
| `/promptdebug` (`/pd`) | Preview the fully assembled prompt: system + injected context + user placeholder |
| `_CLI_BUILD` | Bumped to `wave19` |

---

## Wave 19B — Interactive Overlays

**Status: ✅ Shipped (initial slice)**

**Goal:** add clear opt-in interactive affordances for list-style workflows
without destabilizing the default REPL or non-TTY automation flows.

### Shipped in this slice

| Feature | Evidence |
|---|---|
| Persisted opt-in overlay mode | `/overlay [on|off|status]` stores `_PREFS["interactive_overlays"]` |
| Saved-output picker | `/outputs overlay` opens a searchable picker and reuses the normal inline preview on selection |
| Recent-session picker | `/sessions overlay` opens a searchable picker and prints the selected session summary + resume command |
| One-shot session picker | `openclaw session list --interactive` brings the same picker to non-REPL usage |
| Arrow-key picker shell | TTY overlays now support `↑/↓` focus changes, live inline previews, and enter-to-select while keeping the non-TTY/plain fallback path |
| Guarded fallback behavior | `_overlay_available()` blocks prompts on non-TTY stdin/stdout and falls back to the regular list output |

### Future expansion notes

- Compact approval-review overlays now augment the shipped text-first
  review/trust/recovery loop; full-screen approval-preview shells remain future
  work.
- A lightweight arrow-key/full-screen-ish picker shell is now shipped for TTY
  overlays; a true curses/Textual-style full-screen app remains intentionally
  deferred until a later UX wave proves it is worth the extra complexity.
- Additional pickers can be added in later waves without reopening the initial
  shipped overlay slice.

### Validation

- Focused CLI pytest slice covering `/overlay`, `/outputs overlay`,
  `/sessions overlay`, and `openclaw session list --interactive` passed.

---

## Wave 20 — Collaboration Handoff UX

**Status: ✅ Shipped**

**Goal:** strengthen local-first collaboration with actor-aware notes,
decision trails, and pasteable handoff summaries using only existing session
and handoff data.

### Shipped in this slice

| Feature | Evidence |
|---|---|
| Actor-tagged collaboration notes | `/collab note [@actor] TEXT` records additive `collab` events in the active local session |
| Tagged decision trail | `/collab decision [@actor] [#tag] TEXT` stores tagged decisions for later handoff/export |
| Shareable handoff summary | `/collab`, `/collab share`, and `openclaw session share <session-id>` print an actor-oriented summary with commands, recent outputs, and latest handoff metadata |
| Collaboration export surface | `openclaw session export <session-id>` and saved handoff manifests now include a structured `collaboration` snapshot |
| Inspection visibility | `openclaw session show <session-id>` includes collaboration actors, decisions, and latest handoff evidence when present |

### Guardrails

- Collaboration remains **local/session-file based** only.
- No remote presence, sockets, or backend services were introduced.
- Non-TTY and scripted usage stay compatible because all new behavior is
  additive plain text and additive JSON.

### Validation

- Focused CLI pytest slice covering `/collab`, collaboration export, session
  inspection, and `openclaw session share` passed.

---

## Wave 20 — Response Typography & Auto-Bold

**Status:** ✅ Shipped

### Features
- **Auto-bold responses** (`/autobold`): Dollar amounts (`$69M`), percentages (`47%`), and filenames (`openclaw_cli.py`) are automatically bolded/formatted in AI responses. Respects `auto_bold` pref. Skips code blocks, table rows, blockquotes.
- **Emoji markdown headers** (`/emojiheaders`): H2 headings get `🔹`, H3 get `▸`, H1 get `✨`. Applied in both ANSI and Rich render paths. Controlled by `emoji_headers` pref.
- **Animated response separator** (`/separator`): A 3-frame braille animation followed by a static `─` rule separates each AI response from the next prompt. 5 styles: `gradient`, `pulse`, `dots`, `wave`, `none`. Respects reduced-motion and plain-mode a11y.

### New Commands
| Command | Description |
|---|---|
| `/autobold [on\|off]` | Toggle automatic bolding of key terms |
| `/emojiheaders [on\|off]` | Toggle emoji heading prefixes |
| `/separator [style]` | Set post-response separator animation style |

### New Prefs
| Key | Default | Description |
|---|---|---|
| `auto_bold` | `True` | Enable auto-bolding in responses |
| `emoji_headers` | `True` | Enable emoji on markdown headings |
| `separator_style` | `"gradient"` | Separator animation style |

---

## Wave 21 — Command Palette & Tab-Complete

**Status:** ✅ Shipped

### Features
- **`/palette [query]`**: Fuzzy-search all registered slash commands by name or description. Results shown in a sorted Rich table. Uses a cached registry to avoid recursion.
- **Tab-completion** (`_SlashCompleter`): Pressing Tab at the `/` prompt auto-completes slash command names and aliases using readline. Hint shown in startup banner.
- **`/shortcuts`**: 5-section keyboard shortcut reference card (Navigation, Session, Quick Commands, Appearance, Power) rendered with Rich panels.

### New Commands
| Command | Description |
|---|---|
| `/palette [query]` | Search all commands by keyword |
| `/shortcuts` | Keyboard shortcuts reference card |

### Technical
- `_CMD_REGISTRY_CACHE` + `_get_cmd_registry()` — cached registry accessor prevents recursion inside `/palette`
- `_SlashCompleter` class replaces `_make_completer` for readline integration
- Startup banner updated: `Tab completes /commands` hint added

## Wave 22 — Animated Progress Bars & Celebrations

**Status:** ✅ Shipped

### Features
- **`_progress_bar()`**: Deterministic colored ANSI bar — red below 33%, yellow to 66%, green above. Used internally for determinate progress display.
- **`/exec` progress animation**: Long-running shell commands now show a bouncing indeterminate progress bar (braille-style) with elapsed time. Falls back to plain output on non-TTY or reduced-motion.
- **Macro step tracker** (`_print_macro_progress()`): Shows live ✓/▸/dim step indicators as macros execute — current step highlighted in cyan, completed in green, pending dimmed.
- **`/macrostatus`**: Rich table showing all saved macros with step count and first-step preview.
- **`_celebration_burst()`**: 3-frame confetti animation triggered on 5-star `/rate` ratings. Respects reduced-motion and plain-mode.
- **`/celebrate [message]`**: Manual celebration trigger for fun.

### New Commands
| Command | Description |
|---|---|
| `/macrostatus` | List saved macros with step counts |
| `/celebrate [message]` | Trigger celebration animation |

## Deferred motion-language follow-up (post-Wave 21 note)

The earlier roadmap draft accidentally duplicated Wave 21. Keep the shipped
**Wave 21 — Command Palette & Tab-Complete** name intact and treat the motion
language material as a deferred cross-wave follow-up instead of a second
Wave 21.

**Status:** deferred / fold into later waves as needed

### What remains useful from the draft

- Keep reduced-motion and plain-mode parity mandatory for any future animation.
- Reuse timing/state primitives instead of inventing per-surface motion systems.
- Avoid full-screen or non-stdlib animation dependencies.
- Preserve Python 3.9 compatibility in helper signatures and examples.

### Current disposition

- Wave 16 already shipped the first staggered-feedback slice.
- Wave 22 now owns the shared status grammar work for dashboard-like surfaces.
- Any richer motion/choreography work should be planned explicitly in a later
  wave instead of silently reusing the Wave 21 label.

---

## Wave 22 — Emoji Badges, Progress Cells & Live Status Lattice

**Status:** 🟡 In progress

**Goal:** turn emoji, color, and compact status cells into a single scanning
grammar so watch, session, event, and accessibility surfaces communicate state
instantly without forcing users to read full prose lines.

### Current Wave 22 slice

The active implementation/docs lane currently covers:

- `_status_emoji()` family alignment for healthy/running/warning/error/pending
  and pause-like states.
- Session-list badge rows via `_session_badges()` (`●`/`○`, `stale`,
  `outputs`, and tag cells).
- Existing watch/session timing summaries and accessibility status output as the
  plain-text fallback baseline for the broader lattice.
- Docs sync across architecture, quickstart, dashboard surfaces, and this
  roadmap while command metadata remains unchanged (`docs/COMMANDS.md`
  intentionally not regenerated).

### Design targets

| Target | Why it matters |
|---|---|
| Emoji badge grammar | `_status_emoji()` and related badges should encode meaning consistently across success, warning, retry, waiting, blocked, active, and idle states |
| Progress cells | Dense tables and inline summaries need compact “cell” units that can show phase, recency, risk, and completion without turning into paragraph output |
| Live status lattice | `/session`, `/watch`, `/events`, `/outputs`, `/context`, and `/accessibility status` should feel like related views over the same underlying state vocabulary |
| Plain-text parity | Every badge/color cue must degrade to readable text labels in non-TTY, plain-mode, and reduced-visual-density paths |
| Dashboard alignment | Browser/dashboard mirrors and docs should reuse the same labels, state names, and fallback wording rather than invent parallel terminology |

### Scope for implementation

| Area | Planned work |
|---|---|
| Status badge primitives | Expand `_status_emoji()` and adjacent helpers into a documented badge/status-cell vocabulary that covers phase, health, urgency, and retry state |
| Watch + session surfaces | Refactor `/watch status`, `/watch history`, `/session`, and `/sessions` summaries to use repeatable badge/cell patterns instead of one-off phrasing |
| Dense event/status rows | Add compact progress cells to `/events`, `/outputs`, `/context`, and similar dense surfaces so users can scan for active, stalled, or completed work quickly |
| Accessibility-aware rendering | Ensure high-contrast, plain, reduced-motion, and non-TTY modes preserve the same state meaning through text tokens, ordering, and spacing rather than color dependence |
| Docs/dashboard sync | Update dashboard reference docs so terminal and future browser surfaces share the same status vocabulary, examples, and fallback expectations |

### Implementation notes for the future wave

- Treat Wave 22 as the state-language foundation for Wave 23 dashboard
  elevation; avoid inventing per-surface badge systems that would need to be
  normalized later.
- Prefer additive helpers and small rendering primitives over one large
  dashboard abstraction so existing commands can adopt the lattice incrementally.
- Define a canonical mapping for status families such as active, queued,
  waiting, retrying, warning, blocked, complete, and informational.
- Keep emoji packs, plain mode, and accessibility preferences first-class:
  alternate packs should preserve semantics even when glyphs change.
- Preserve Python 3.9 compatibility and avoid introducing Rich-only table or
  live-update dependencies.

### Dashboard/docs alignment

| Surface group | Wave 22 expectation |
|---|---|
| `/session`, `/sessions` | Shared badge set for automation state, collaboration state, latest outcome, and next-action hints |
| `/watch status`, `/watch history` | Progress cells for phase, retry/backoff, checkpoint freshness, and intervention need |
| `/events`, `/outputs`, `/context` | Compact status prefixes that make dense history views scannable without hiding detailed text |
| `/accessibility status`, `/layout` | Explicit explanation of how badge grammar degrades in plain/high-contrast/reduced-motion modes |
| Browser/dashboard mirrors | Reuse CLI status labels and fallback terms in dashboard cards, task status widgets, and future read-only monitoring views |

### Done-when

- [x] A documented status grammar exists for badge meaning, progress-cell shape,
      and plain-text equivalents. (shipped: DASHBOARD_SURFACES.md:279 shared badge grammar)
- [x] Core watch/session/event surfaces reuse the same badge and progress-cell
      vocabulary instead of per-command phrasing. (shipped: `_status_cell`/`_progress_cell` at openclaw_cli_watch.py:250,256; openclaw_cli_cmd_session.py:299)
- [x] Emoji packs and accessibility modes preserve status meaning without
      requiring color or Rich-only affordances. (shipped: `_a11y_plain_mode()` at openclaw_cli_cmd_core.py:1045; openclaw_cli_cmd_settings.py:444)
- [x] `docs/DASHBOARD_SURFACES.md` stays aligned on shared terminology and
      fallback expectations for dashboard mirrors. (shipped: DASHBOARD_SURFACES.md:279 shared badge grammar)
- [x] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave. (shipped: CLI_ARCHITECTURE.md:279 Wave 25 layout preset; CLI_QUICKSTART.md:198)

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — status grammar primitives | Badge vocabulary, progress-cell tokens, emoji-pack semantics, and helper boundaries |
| B — watch/session adoption | `/watch*`, `/session`, and `/sessions` rendering updates using the shared grammar |
| C — dense history surfaces | `/events`, `/outputs`, `/context`, and other scan-heavy views |
| D — validation + parity | Focused pytest/manual checks for non-TTY, plain mode, high contrast, and reduced-motion output |
| E — docs/dashboard sync | Architecture, quickstart, and dashboard-surface terminology updates |

---

---

## Wave 23 — ASCII Data Visualizations

**Status:** ✅ Shipped

### Features
- **`/stats [category]`**: Horizontal ASCII bar charts (`█` blocks) for command frequency, rating distribution, and session counts by date. Supports `all`, `commands`, `ratings`, `sessions` categories. Top 10 results sorted by frequency.
- **`/quality`**: 8-row tall vertical histogram with per-score color coding (red=1★ → magenta=5★). Shows `██`/`░░` blocks, per-score counts, and average rating summary.
- **`/heatmap`**: 24-hour activity grid with color intensity (🔴 hot >75%, 🟡 warm >50%, 🟢 mild >25%, 🔵 cool). Shows peak hour and legend. Reads from timestamped `cmd_history` entries.

### New Commands
| Command | Description |
|---|---|
| `/stats [category]` | Usage bar charts (commands/ratings/sessions) |
| `/quality` | Colored rating quality histogram |
| `/heatmap` | Hourly activity heatmap |

---

## Wave 23 — Visual Hierarchy Renaissance & Dashboard Elevation

**Status:** 🟡 In progress (shipped slice)

**Goal:** make OpenClaw’s terminal surfaces read like intentional dashboards
instead of long transcripts by strengthening hierarchy, grouping, and
surface-specific color/spacing patterns.

### Design targets

| Target | Why it matters |
|---|---|
| Header taxonomy | Users should immediately distinguish page title, section title, subsection, metric row, and action hint without re-learning each command |
| Dashboard composition | `/session`, `/sessions`, `/watch`, and `/outputs` should feel like related dashboards with repeatable layout regions rather than ad-hoc blocks |
| Surface identity | Each major surface should have a stable visual signature without abandoning the shared status grammar from Wave 22 |
| Summary-first scanning | Critical state, next action, and recent changes should appear before verbose detail |
| Plain-text structure parity | Non-Rich output must still preserve section ordering, labels, and grouping so the information architecture survives without panels/colors |

### Current shipped slice

The currently landed Wave 23 slice is still intentionally narrow. It elevates
the most-used terminal dashboard surfaces without claiming the full
"dashboard family" rewrite is finished yet:

- `summarize_session()`, `inspect_session()`, and `_session_badges()` now put
  top-line status, freshness, counts, and watch context ahead of deeper detail.
- `_print_watch_status()` and `_print_watch_history()` lead with status-family
  cells so operators see active phase, retry pressure, and intervention context
  before chronology-heavy detail.
- Plain-text/non-TTY output keeps the same section ordering and status wording as
  the richer TTY views; Wave 23 is not Rich-only polish.

### Remaining scope

| Area | Remaining work |
|---|---|
| Heading + divider primitives | Introduce more explicit shared header/section helpers rather than relying only on status/progress cells plus existing section blocks |
| Session + watch dashboards | Extend the summary → details → actions hierarchy beyond the currently elevated session/watch summaries and history surfaces |
| Artifact + context elevation | Make `/outputs`, `/context`, and adjacent inspection surfaces emphasize latest/high-value items before secondary metadata |
| Color-by-surface taxonomy | Define restrained surface accents for session, watch, artifact, accessibility, and collaboration views while keeping status meaning owned by the Wave 22 lattice |
| Dashboard entrypoint planning | Document whether a consolidated summary/dashboard command should reuse existing surface primitives rather than invent a separate rendering stack |

### Implementation notes for the future wave

- Treat Wave 23 as a composition layer on top of Wave 22; do not fork the badge
  grammar just to make one dashboard look unique.
- Prefer reusable “summary card” and “section shell” helpers over bespoke,
  command-local panel trees.
- Keep the first screenful action-oriented: highlight health, freshness,
  blockers, and next commands before historical detail.
- Preserve deterministic ordering and labels for plain mode, non-TTY output, and
  tests that assert on text.
- If a new top-level dashboard command is proposed, document it here and in
  `docs/DASHBOARD_SURFACES.md` before implementation starts.

### Dashboard/docs alignment

| Surface group | Wave 23 expectation |
|---|---|
| `/session`, `/sessions` | Current slice elevates top-line status/count badges first; fuller action-region composition remains follow-up work |
| `/watch status`, `/watch history` | Current slice promotes status, phase, retry/backoff, and notes ahead of deeper history rows |
| `/outputs`, `/context`, `/events` | Recent/high-value items surface first; dense rows remain available but visually subordinate |
| `/accessibility status`, `/layout` | Hierarchy updates must still explain mode, density, and fallback state without relying on panel chrome alone |
| Browser/dashboard mirrors | Dashboard cards and web session detail views should reuse the same section names, priority order, and top-line summaries |

### Done-when

- [x] Shared header/section primitives exist for dashboard-style CLI output in
      both Rich and plain/ANSI paths. (shipped: `_dashboard_section_lines` at openclaw_cli_watch.py:269, openclaw_cli.py:1144, openclaw_cli_session_display.py:687; `_append_dashboard_rich_section` for Rich path)
- [x] `/session`, `/sessions`, and `/watch*` now surface top-line status and
      timing information before deeper detail in the current shipped slice.
- [ ] Surface-specific accents improve scanability without conflicting with Wave
      22 status semantics across all target surfaces.
- [x] `docs/DASHBOARD_SURFACES.md` documents the currently shipped Wave 23 slice
      and remaining dashboard-elevation scope.
- [x] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated to
      describe the shipped slice honestly.

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — hierarchy primitives | Shared header, summary-strip, divider, and section-shell helpers |
| B — session/watch elevation | `/session`, `/sessions`, `/watch status`, and `/watch history` composition updates |
| C — artifact/context elevation | `/outputs`, `/context`, `/events`, and related dense surfaces |
| D — validation + parity | Focused regression checks for plain mode, non-TTY ordering, and dashboard scanability |
| E — docs/dashboard sync | Roadmap, architecture, quickstart, and dashboard-surface inventory updates |

---

## Wave 24 — Terminal Preview & Focused Inspection

**Status: ✅ Shipped (initial slice)**

**Goal:** let users inspect sessions, outputs, and watch artifacts in place with
low-friction previews so list browsing does not require repeated context
switching or command hopping.

### Design targets

| Target | Why it matters |
|---|---|
| Inline preview model | Users should be able to glance into an item without fully leaving the current list or browser flow |
| Focused inspection windows | Preview affordances should support “look closer” states that still feel lighter than a full context switch |
| Consistent preview scaffolding | Session, output, event, and watch previews should share structure, controls, and fallback wording |
| Controlled density | Preview content should surface the most useful excerpt first instead of dumping the entire artifact inline |
| Accessibility-safe interaction | Preview affordances must stay usable in plain mode, reduced-motion, and non-overlay paths |

### Current shipped slice

| Area | Current behavior |
|---|---|
| Output previews | `/outputs 1`, `/outputs <filename>`, and `/outputs overlay` show bounded inline previews with filename, size, modified time, and an explicit truncation note when clipped |
| Session selection | `/sessions overlay` and `openclaw session list --interactive` now let you search/select a session and land directly in the compact Session Dashboard plus its resume command |
| Focused inspection | `/watch status`, `/watch history`, and `openclaw session show <session-id>` now cover the “look closer without losing the thread” slice for watch/session inspection |
| Fallback patterns | The overlay path still degrades to the standard list output when stdin/stdout is not interactive; no separate TUI-only control path was introduced |
| Shared vocabulary | Wave 22/23 status cells and section ordering remain the active grammar for these previews, so the inspection slice stays aligned with the dashboard work already in flight |

### Deferred scope / not yet shipped

- There is **not yet** a shared preview-block helper used by every surface.
- Session pickers do **not yet** expose inline share/handoff controls; share
  remains a follow-up command.
- `/events` now adds a bounded preview strip with recovery/inspection follow-through,
  but deeper expanded-row browsing remains follow-up work.
- Approval overlays and picker-local full-screen expansion are still intentionally
  deferred; the shipped interaction remains bounded inline preview + follow-up
  commands.
- Browser/dashboard preview panes are still a planning target rather than a
  shipped implementation.

### Dashboard/docs alignment

| Surface group | Wave 24 expectation |
|---|---|
| `/outputs`, `/outputs overlay` | The shipped preview path is bounded inline output with metadata + truncation messaging; richer preview blocks remain follow-up work |
| `/sessions`, `openclaw session list --interactive` | Selection now opens the compact Session Dashboard plus the resume command; share/collaboration actions are still separate |
| `/watch status`, `/watch history` | These are the live focused-inspection windows today, surfacing phase/retry/note context before longer history |
| `/context`, `/events`, `openclaw session show` | `/context` keeps the bounded grounding preview, `/events` now adds a compact preview/recovery strip before the detailed log rows, and `session show` remains the deep inspection path |
| Browser/dashboard mirrors | Future mirrors should copy the current CLI field ordering and truncation rules rather than invent a new preview vocabulary |

### Done-when

- [ ] Shared preview helpers exist for session/output/watch inspection in Rich
      and plain/ANSI-compatible forms.
- [x] The primary list/browse surfaces now support bounded output preview and
      session-selection inspection without forcing a full manual re-browse step.
- [x] Preview truncation and non-interactive fallback behavior are documented
      for the shipped slice.
- [x] `docs/DASHBOARD_SURFACES.md` records the current preview-capable surfaces
      and focused inspection expectations.
- [x] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the shipped slice.

### Evidence

- `tests/test_openclaw_cli.py::test_outputs_preview_stays_bounded_for_large_artifacts`
- `tests/test_openclaw_cli.py::test_main_session_list_interactive_overlay_prints_focused_session_dashboard`
- existing focused-inspection coverage around `inspect_session()`,
  `_print_watch_status()`, and `_print_watch_history()`

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — preview primitives | Shared preview block helpers, excerpt rules, and metadata ordering |
| B — session/output browsing | `/sessions*`, `openclaw session list --interactive`, and `/outputs*` preview adoption |
| C — watch/context inspection | Watch-focused windows plus preview-friendly `/context` or `/events` expansions |
| D — validation + parity | Interactive/non-interactive regression checks plus plain-mode and reduced-motion validation |
| E — docs/dashboard sync | Roadmap, dashboard inventory, architecture, and quickstart updates |

---

## Wave 24 — Smart Response Formatting

**Status:** ✅ Shipped

### Features
- **JSON auto-format** (`/jsonformat`): Bare JSON objects/arrays in AI responses are detected, pretty-printed with 2-space indent, and syntax-highlighted (keys=cyan, strings=green, numbers=yellow, booleans=magenta). Skips existing code blocks. Respects `json_autoformat` pref.
- **Clickable URLs** (`/links`): OSC 8 hyperlink sequences make URLs in responses clickable in modern terminals (iTerm2, Kitty, WezTerm). URLs in code blocks and table rows are excluded. Controlled by `clickable_links` pref.
- **File path hints** (`/pathhints`): After responses mentioning file paths (e.g., `src/openclaw_cli.py`), a subtle `📁 Files mentioned: ... (use /view or /edit)` hint is shown for paths that actually exist on disk. Max 3 hints shown. Controlled by `path_hints` pref.

### New Commands
| Command | Description |
|---|---|
| `/jsonformat [on\|off]` | Toggle JSON auto-detect and pretty-print |
| `/links [on\|off]` | Toggle OSC 8 clickable URLs |
| `/pathhints [on\|off]` | Toggle file path quick-action hints |

### New Prefs
| Key | Default | Description |
|---|---|---|
| `json_autoformat` | `True` | Auto-detect and pretty-print JSON |
| `clickable_links` | `True` | OSC 8 clickable URLs |
| `path_hints` | `True` | File path quick-action hints |

---

## Wave 25 — Multi-Pane Layout Presets

**Status: ✅ Shipped**

**Goal:** introduce opt-in workspace presets that keep multiple related surfaces
visible together for power users without turning the default CLI into a
full-screen terminal app.

**Current shipped slice:** Wave 25 currently ships the **preset contract**, not
the full pane renderer. `/layout focus`, `/layout watch-monitor`, and
`/layout handoff` now persist the named preset, `/layout` reports the current
primary/supporting surface pairing plus the width/accessibility fallback, and
`/layout reset` returns to the default single-pane mode. The actual multi-pane
canvas and pane-to-pane focus choreography remain follow-up work.

### Design targets

| Target | Why it matters |
|---|---|
| Preset-based complexity | Users should opt into richer workspaces through named layouts instead of manually wiring every pane every time |
| Focus switching model | Multi-pane views need predictable rules for active pane, keyboard focus, and action routing |
| Persistence with restraint | Useful layout state should persist, but transient pane clutter should not surprise users on the next launch |
| Shared surface composition | Session, watch, outputs, and collaboration panes should reuse the same rendering primitives established in Waves 22–24 |
| Escape hatch clarity | Users must always know how to exit, collapse, or fall back to single-surface mode |

### Scope for implementation

| Area | Planned work |
|---|---|
| Layout preset model | `focus`, `watch-monitor`, and `handoff` are now persisted as named presets with documented primary/supporting surface pairings |
| Pane state management | Current state tracks the remembered preset and fallback mode, and the pane shells now print explicit `/layout focus …` transition cues; richer compositor-level routing still remains deferred |
| Pane rendering shells | Reuse dashboard and preview primitives to draw side-by-side or stacked pane groups when the terminal width and mode allow it |
| Persistence + commands | `/layout <preset>`, `/layout`, and `/layout reset` now expose the first preset-management contract through the existing layout command |
| Fallback + width rules | The current slice reports `multi-pane`, `stacked`, or `single-pane` fallback based on terminal width, TTY state, and plain mode |

### Implementation notes for the future wave

- This is the highest-risk wave in the current tranche; keep it opt-in and
  reversible.
- Prefer preset bundles over arbitrary pane composition so docs, tests, and
  fallback paths remain manageable.
- Avoid introducing a dependency on a full-screen curses/Textual stack unless a
  future roadmap revision explicitly approves that direction.
- Width and accessibility guards should decide whether a preset renders as
  multi-pane, stacked, or single-pane fallback using the same underlying data.
- Any persistence added here must be normalized and safe to ignore when the
  runtime cannot honor the requested layout.

### Dashboard/docs alignment

| Surface group | Wave 25 expectation |
|---|---|
| `/layout`, `/accessibility`, preset commands | Users can discover the persisted preset, the current width/accessibility fallback, and how to reset to single-pane/default mode |
| Session + watch combinations | Focus presets pair session summary, watch control, and next actions without duplicating state labels |
| Artifact + collaboration combinations | Handoff/collaboration presets surface recent outputs and actor notes beside session state |
| Preview-capable surfaces | Wave 24 preview rules still apply inside panes; panes should not dump unbounded detail |
| Browser/dashboard mirrors | Future web layouts can reuse the same preset vocabulary and “primary pane vs supporting pane” model |

### Done-when

- [x] Named layout presets and their fallback rules are documented before code
      lands.
- [x] Multi-pane rendering is opt-in, accessibility-aware, and collapses cleanly
      on unsupported terminals. (shipped: `_layout_preset_fallback()` at openclaw_cli_layout.py:213 with is_tty param)
- [x] Focus switching is defined with non-interactive equivalents.
- [x] Preset persistence and reset behavior are defined through `/layout` and
      `/accessibility status`.
- [x] `docs/DASHBOARD_SURFACES.md` records each preset's intended surfaces and
      downgrade behavior. (shipped: DASHBOARD_SURFACES.md:183-187 preset surfaces and fallback)
- [x] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave. (shipped: CLI_ARCHITECTURE.md:279 Wave 25 layout; CLI_QUICKSTART.md:198)

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — preset model | Layout vocabulary, persistence keys, and command contract |
| B — pane shells | Multi-pane/stacked rendering helpers plus focus affordances |
| C — surface adoption | Session/watch/output/collab surfaces integrated into the approved presets |
| D — validation + fallback | Width, non-TTY, plain-mode, and reduced-motion regression coverage |
| E — docs/dashboard sync | Roadmap, layout reference, architecture, and quickstart updates |

---

## Wave 26 — Session Mood, Celebration & Emotional Feedback

**Status: ✅ Shipped**

**Goal:** add tasteful emotional feedback so OpenClaw acknowledges progress,
milestones, and session tone without turning status output into novelty chrome.

### Design targets

| Target | Why it matters |
|---|---|
| Session mood model | Users should be able to sense whether a session is cruising, blocked, recovering, or wrapping up from concise cues |
| Celebration restraint | Milestones should feel rewarding but never interrupt core work or overwhelm serious/error states |
| Momentum visibility | Streaks, milestone counts, or closure rituals can reinforce progress and session continuity |
| Collaboration-aware tone | Shared or handed-off sessions should reflect team context, not just single-user completion cues |
| Accessibility-safe emotion | Emotional feedback must remain meaningful in plain mode, reduced motion, and low-emoji packs |

### Current shipped slice

Wave 26 is currently shipping as a **celebration + restraint** slice rather than
the full mood-model roadmap:

- **`_celebration_burst()`** is the live milestone-feedback primitive. It emits a
  short confetti burst for interactive TTY sessions, but degrades to a single
  `🎉 {message}` line when reduced motion or plain mode is active and stays quiet
  when no message is supplied in non-interactive output.
- **`/celebrate [message]`** is the explicit user-triggered celebration surface
  for this wave.
- **`/rate 5`** reuses the same celebration helper after printing the rating
  confirmation, so celebratory feedback remains brief and opt-in through the
  rating path.
- **`/collab` / `openclaw session share`** remain neutral, pasteable handoff
  summaries. The broader morale/momentum language for collaboration surfaces is
  still deferred.

### Remaining scope

| Area | Planned work |
|---|---|
| Mood/status vocabulary | Define additive mood families such as focused, recovering, blocked, celebrating, and handed-off, plus their text equivalents |
| Session + recap surfaces | Apply mood cues to `/session`, `/sessions`, collaboration summaries, and end-of-flow recap/closure surfaces |
| Milestone recognition | Design modest celebration patterns for first success, cleared blocker, completed plan, or long-running streak events |
| Collaboration sentiment hints | Allow actor-aware notes and handoff summaries to reflect whether momentum is rising, stalled, or newly resolved |
| Preference + fallback rules | Document how users can tone down or disable emotional feedback, and how cues degrade in ASCII/minimal/plain paths |

### Implementation notes for the remaining wave

- Mood cues must remain subordinate to objective state. A “celebration” badge can
  never obscure an error, blocker, or approval requirement.
- Reuse Wave 22 badge semantics and Wave 23 hierarchy so emotional feedback feels
  additive, not like a second competing status language.
- Prefer short-lived or summary-scope celebration cues over long-running
  animation loops.
- Keep any persistence optional; session mood should be derivable from existing
  events/metadata whenever possible.
- Provide a clear reduced-emotion story for operators who want neutral output.

### Dashboard/docs alignment

| Surface group | Wave 26 expectation |
|---|---|
| `/session`, `/sessions` | Mood and momentum cues appear in top-line summaries without displacing objective health and next-step state |
| `/collab`, session share/export | The shipped slice keeps handoff summaries neutral and pasteable; richer morale/momentum wording is still deferred |
| Completion/recap flows | Milestone and closure rituals remain brief, skippable, and documented with plain-text equivalents |
| `/watch*`, `/events` | Emotional cues may annotate recoveries or successful completions but must not replace timing/risk detail |
| Browser/dashboard mirrors | Shared monitoring views can reuse the same mood vocabulary and accessibility fallbacks for milestone cards or recaps |

### Done-when

- [x] A restrained celebration primitive exists with explicit text equivalents
      and reduction rules.
- [x] Session, recap, and collaboration surfaces can express momentum or
      milestones without obscuring core status. (shipped: `_session_mood_snapshot()` at openclaw_cli.py:1940; `_session_mood_brief()` at openclaw_cli.py:1922)
- [x] Emotional feedback respects plain mode and reduced motion for the shipped
      celebration paths.
- [x] `docs/DASHBOARD_SURFACES.md` documents where mood cues are allowed and how
      they degrade. (shipped: DASHBOARD_SURFACES.md:197 — objective status leads; mood cues secondary)
- [x] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave. (shipped: CLI_ARCHITECTURE.md:379 momentum cues; CLI_QUICKSTART.md:411)

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — mood vocabulary | Status/mood families, text equivalents, and preference boundaries |
| B — session/recap adoption | `/session`, `/sessions`, recap, and completion/closure surfaces |
| C — collaboration sentiment | `/collab`, share/export, and actor-aware tone cues |
| D — validation + restraint | Accessibility, plain-mode, and “no excessive celebration” regression checks |
| E — docs/dashboard sync | Roadmap, dashboard inventory, architecture, and quickstart updates |

---

## Wave 26 — Prompt Line Enhancements

**Status:** ✅ Shipped

### Features
- **Token badge** (`/tokenbadge`): After each AI response, a `[~N tok]` badge shows estimated token usage (prompt + response). Model name shown alongside when available. Controlled by `show_token_badge` pref.
- **`_estimate_tokens()`**: Rough token estimator (4 chars ≈ 1 token). Used for badge and `/tokeninfo`.
- **`/tokeninfo`**: Detailed breakdown — prompt tokens, response tokens, total, avg/prompt, context window bar (green/yellow/red by usage %).
- **Custom prompt** (`/prompt`): Format string with tokens `{route}`, `{session}`, `{model}`, `{build}`, `{time}`. Use `/prompt reset` to restore default. Stored in `prompt_format` pref.
- **`_render_prompt_format()`**: Renders format string with live state substitutions.
- **`_last_model_used`**: Module-level global capturing model name from server responses.

### New Commands
| Command | Description |
|---|---|
| `/tokenbadge [on\|off]` | Toggle token count badge |
| `/tokeninfo` | Detailed token usage analysis |
| `/prompt [format\|reset]` | Customize REPL prompt string |

### New Prefs
| Key | Default | Description |
|---|---|---|
| `show_token_badge` | `True` | Show token badge after responses |
| `prompt_format` | `"{route} openclaw{session}> "` | Custom prompt format |

---

## Wave 27 — Live Dashboard Shares & Operator Visibility

**Status: ✅ Shipped**

**Goal:** expose richer read-only monitoring and dashboard-ready status snapshots
outside the active REPL so teammates and operators can observe session health,
approval pressure, and automation progress without introducing shared write
infrastructure.

### Design targets

| Target | Why it matters |
|---|---|
| Read-only monitoring model | Teams need shared visibility without turning local-first session data into a multi-writer system |
| Operator-ready summaries | Session, watch, approval, and intervention state should be visible from a single concise snapshot |
| Shared terminology | Browser/dashboard mirrors, exports, and CLI dashboards must reuse the same badge grammar and state names from earlier waves |
| Safe broadcasting boundaries | Shared visibility should never imply remote control, silent mutation, or hidden background services |
| Plain-text portability | Every monitoring view must remain copy/pasteable for terminals, tickets, and chat handoffs |

### Scope for implementation

| Area | Planned work |
|---|---|
| Shared snapshot model | Define a documented read-only summary shape for session health, watch checkpoints, approval queue state, accessibility mode, and latest collaboration context |
| Dashboard entry surfaces | Extend `/session`, `/sessions`, `/watch status`, `/collab`, and related CLI summaries so they can emit a consistent operator-facing snapshot without requiring interactive mode |
| Approval visibility | Surface pending approvals, stale waits, resume cues, and intervention-needed state in a form that operators can scan quickly |
| Browser/dashboard parity | Document how Terminal Agent Sessions, Watch Insights, and future monitoring cards reuse the same field names, labels, and fallback wording |
| Export/handoff hooks | Plan additive export/share paths so monitoring summaries can be saved or pasted without introducing new remote infrastructure |

### Current shipped slice

Wave 27 is currently landing as a **read-only operator-visibility foundation**
on top of the existing local session data model:

- `openclaw session share <session-id>` remains the canonical pasteable operator
  snapshot: title, plan/task linkage, recent actors/decisions/notes, latest
  handoff, recent outputs, and exact resume/inspect/share commands.
- `openclaw session show <session-id>` and the compact `/session`/`/sessions`
  previews already surface watch state, collaboration context, and next-step
  cues in plain text without requiring a browser or Rich-only chrome.
- `/watch status` and `/watch history` provide the current operator-facing
  control-tower slice for checkpoint drift, retry pressure, and operator-note
  visibility.
- Browser/dashboard mirrors are still terminology/documentation work in this
  slice; Wave 27 does **not** ship remote control, shared presence, or hosted
  monitoring services.

### Dashboard surface alignment

| Surface group | Wave 27 expectation |
|---|---|
| `/session`, `openclaw session show` | Provide a single-session operator snapshot with health, phase, approvals, latest outputs, and collaboration readiness |
| `/sessions`, `openclaw session list` | Add a fleet-style rollup for active, waiting, blocked, and recently completed sessions using shared status lattice terms |
| `/watch status`, `/watch history` | Highlight freshness, checkpoint drift, retry/backoff, and intervention need in a read-only monitoring shape |
| `/collab`, `openclaw session share` | Reuse actor-aware handoff summaries as a source for operator-facing read-only context |
| Browser/dashboard mirrors | Map the same fields into Terminal Agent Sessions, Watch Insights, and future shared monitoring cards without renaming core concepts |

### Implementation notes for the future wave

- Keep the architecture strictly **read-only**: no sockets, remote mutation APIs,
  or shared presence is required to complete this wave.
- Treat Wave 27 as a documentation and serialization foundation for later
  dashboard work rather than a commitment to a hosted control plane.
- Reuse the status grammar from Wave 22 and any dashboard hierarchy patterns from
  adjacent future waves instead of inventing a separate operator dialect.
- Preserve local/session-file ownership boundaries and ensure exported monitoring
  data is explicitly additive, redactable, and optional.
- Keep Python 3.9 compatibility and avoid dependencies that assume live Rich-only
  dashboards.

### Done-when

- [x] A documented read-only monitoring snapshot exists for session, watch, approval, and collaboration state.
- [x] CLI dashboard surfaces identify which summary fields are operator-facing and how they degrade to plain text.
- [x] `docs/DASHBOARD_SURFACES.md` explains how terminal and browser/dashboard monitoring views share terminology and fallback behavior.
- [x] Export/share guidance makes it clear that Wave 27 introduces visibility only, not remote control.
- [x] Follow-on architecture and quickstart docs are updated for the shipped slice.

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — monitoring snapshot design | Read-only summary schema, serialization boundaries, and operator terminology |
| B — CLI surface adoption | `/session`, `/sessions`, `/watch*`, and collaboration summaries that expose the shared snapshot |
| C — dashboard parity | Browser/dashboard field mapping, fallback wording, and shared monitoring guidance |
| D — validation + privacy review | Plain-text parity, export checks, and guardrails against accidental write-capable behavior |
| E — docs | Architecture, quickstart, and dashboard-surface updates for monitoring workflows |

---

## Wave 27 — Celebrations & Smart Error Recovery

**Status:** ✅ Shipped

### Features
- **`/streak`**: Tracks consecutive high (4+) ratings. Shows current streak, best-ever streak, high-rate %, and 🔥 fire emojis. Triggers `_print_ascii_trophy()` at streak ≥ 5.
- **`_print_ascii_trophy()`**: ASCII art trophy with ANSI yellow. Auto-triggered in `_cmd_rate()` at milestone streaks (5, 10, 20, 50).
- **Smart `/exec` error hints** (`_analyze_exec_error()`): Classifies failures by pattern (permission denied → sudo, command not found → brew/pip, ModuleNotFoundError → pip install, port in use → lsof, etc.) and prints up to 3 actionable hints.
- **`_OPENCLAW_TIPS`** (25 tips) + **`/tip`**: Random usage tip on demand. Also shown at startup with 30% probability as a dim hint after the banner.

### New Commands
| Command | Description |
|---|---|
| `/streak` | Rating streak tracker + ASCII trophy |
| `/tip` | Random usage tip |

---

## Wave 28 — Keyboard Shortcuts

**Status:** ✅ Shipped

### Features
- **readline emacs mode**: Ctrl-R (reverse history search), Ctrl-L (clear screen), Ctrl-W (delete word), Ctrl-U (clear line) — explicitly bound in `run_chat()`.
- **`/keys`** (`_print_key_bindings()`): Table of all active built-in keyboard shortcuts in Rich or plain-text format.
- **`/keybind [key action|list|clear]`**: Save custom `Ctrl+X → /command` bindings. Persisted in `custom_keybinds` pref. Applied on startup via `_apply_all_custom_keybinds()`. Example: `/keybind Ctrl+H /histsearch`.
- **`/bindlist`**: Unified Rich table showing built-in readline bindings + all custom bindings with type indicator.

### New Commands
| Command | Description |
|---|---|
| `/keys` | Show active keyboard shortcuts |
| `/keybind [key action\|list\|clear]` | Manage custom key bindings |
| `/bindlist` | Show all bindings (built-in + custom) |

### New Prefs
| Key | Default | Description |
|---|---|---|
| `custom_keybinds` | `{}` | User-defined key→command mappings |

---

## Wave 28 — Gesture Language & Predictive Affordances

**Status: ✅ Shipped**

**Goal:** reduce navigation and recovery friction by teaching the CLI to suggest
the most useful next action, expose repeatable shortcut patterns, and make
stateful flows feel guided without becoming opaque.

### Design targets

| Target | Why it matters |
|---|---|
| Contextual next-step hints | Users should not have to remember every follow-up command after approvals, failures, exports, or watch interruptions |
| Gesture/shortcut grammar | Common moves such as retry, inspect, resume, share, and export should have predictable verbs and aliases |
| Recovery-first menus | Errors and blocked states should point to the safest next command instead of leaving users at a dead end |
| Stateful completion | Tab completion and interactive hints should reflect session/watch state, not static command lists alone |
| Accessible guidance | Suggestions must remain useful in plain text, non-TTY, and reduced-motion modes without relying on visual flourish |

### Scope for implementation

| Area | Planned work |
|---|---|
| Next-action engine | Define helper rules that derive likely follow-up actions from session state, approval queues, last command outcome, and watch checkpoints |
| Shortcut vocabulary | Document canonical verbs, aliases, and “gesture” patterns for inspect/resume/retry/share/export flows so future commands stay coherent |
| Recovery surfaces | Upgrade approval waits, empty states, blocked flows, and failed commands with concise next-step menus or hint lines |
| Completion strategy | Plan how shell completion and in-CLI suggestions can surface state-aware commands without hiding the raw command model |
| Docs/examples | Add task-focused examples that show how predictive hints should appear in terminal-first and plain-text workflows |

### Dashboard surface alignment

| Surface group | Wave 28 expectation |
|---|---|
| `/watch status`, `/watch history` | Suggest resume, inspect, retry, or intervene actions based on freshness and failure state |
| `/session`, `/sessions`, `/collab` | Recommend share, resume, export, or review commands from the current session lifecycle stage |
| `/outputs`, `/context`, `/events` | Offer low-friction follow-ups such as preview, copy, reopen, or explain-current-state actions |
| Approval and error flows | Present a consistent “next best action” line that remains readable in non-interactive output |
| Browser/dashboard mirrors | Reuse the same action labels and help text for dashboard cards, notifications, or detail panels |

### Current shipped slice

Wave 28 is currently landing as a **lightweight hint-and-recovery layer** on top
of the existing session/watch dashboards:

- `/watch status` already ships deterministic action lines for the most common
  next moves: inspect history, leave an operator breadcrumb, tune retry budget,
  or review the finished session snapshot when a watch completes.
- `/context` now ends with targeted follow-up guidance based on tracked files and
  linked plan/task state so users can either add grounding or compare it against
  the current session health.
- Chat responses can emit quick file affordances through `_print_path_hints(...)`
  when the assistant references real local files; the hint stays intentionally
  small (`use /view or /edit`) and only appears in interactive terminal output.
- Recovery guidance is additive rather than modal: high/critical `/exec` and
  `/edit` flows include a recovery hint before approval, and chat failures point
  users to `/retry` and `/reset` instead of leaving a dead end.
- `/shortcuts` is the current documented gesture vocabulary surface for repeatable
  navigation, retry, history, and command-discovery moves. Stateful tab
  completion remains future work.

### Implementation notes for the future wave

- Keep predictive guidance **assistive, not mandatory**: users must still be able
  to run raw commands directly without accepting suggestions.
- Prefer small derivation helpers over one monolithic intent engine so hints can
  be tested and adopted incrementally across commands.
- Reuse the status grammar from Wave 22 and any monitoring snapshot fields from
  Wave 27 so suggestions reference consistent state names.
- Avoid shell-specific assumptions in docs; completion examples should describe
  the behavior, not require one shell integration path.
- Ensure hints stay deterministic enough for screenshots, tests, and scripted
  documentation examples.

### Done-when

- [x] A documented next-action model exists for the currently shipped watch, context, approval, and chat-recovery hints.
- [x] Shortcut/gesture terminology is normalized for the current slice through `/shortcuts`, `/retry`, `/view`, `/edit`, and watch action labels.
- [x] Dashboard and terminal surfaces reuse the same action labels and fallback phrasing for the shipped predictive hints.
- [x] Plain-text, non-TTY, and reduced-motion examples explain where guidance is shown, downgraded, or intentionally suppressed.
- [x] Follow-on architecture and quickstart docs are updated for the shipped slice.
- [ ] Stateful completion and broader export/handoff affordances remain follow-up work.

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — next-action rules | Predictive hint derivation, state mapping, and deterministic wording |
| B — recovery + approvals | Error, blocked, retry, and approval surfaces with safer follow-up guidance |
| C — completion + gesture docs | Shortcut vocabulary, completion behavior, and examples across shell/CLI surfaces |
| D — validation | Hint determinism, accessibility parity, and command discoverability checks |
| E — docs | Architecture, quickstart, and dashboard-surface guidance for predictive affordances |

---

## Wave 29 — Diff & Edit Viewer Polish

**Status:** ✅ Shipped

### Features
- **`_render_diff_ansi()`**: Colorizes unified diff output — `+` lines green, `-` lines red, `@@` hunk headers cyan, `---`/`+++` file headers bold, context lines dim.
- **`/diff [file1 file2 | --git]`**: Runs `git diff` (or `diff -u file1 file2`) and renders with `_render_diff_ansi()`. Falls back gracefully if git not available.
- **`/changes`**: Shows `session_edits` log entries + colorized `git status --short` (M=yellow, A=green, D=red, ?=dim).
- **`/snapshot [name]`**: Saves current `git rev-parse HEAD` SHA to `snapshots` pref under a name. Includes timestamp.
- **`/rollback [name|list]`**: `list` shows saved snapshots. `<name>` previews diff from snapshot to HEAD. `<name> --exec` destructively checks out the snapshot. Warns clearly before destructive action.

### New Commands
| Command | Description |
|---|---|
| `/diff [file1 file2 \| --git]` | Colorized unified diff |
| `/changes` | Session edit log + git status |
| `/snapshot [name]` | Save named git restore point |
| `/rollback [name\|list]` | Preview or execute snapshot rollback |

---

## Wave 29 — Narrative Recaps & Session Storytelling

**Status: ✅ Shipped**

**Goal:** turn raw session history into durable recap artifacts that explain what
happened, who acted, what changed, and what should happen next so handoffs,
reviews, and onboarding do not require reading full event logs.

### Current shipped slice

Wave 29 has started as a **chapter-style plain-text recap pass** over existing
session facts, not a full prose/timeline recap engine yet:

- `openclaw session share <session-id>` already groups handoff output into stable
  recap chapters: **ACTORS**, **RECENT DECISIONS**, **RECENT NOTES**,
  **LATEST HANDOFF**, **OPERATOR SNAPSHOT**, **RECENT OUTPUTS**, and
  **COMMANDS**.
- `openclaw session show <session-id>` and `/session` already reuse the same
  actor/decision/mood facts for inspection and compact storytelling rather than
  forcing operators back to raw event logs first.
- The current “ending” for this slice is command-oriented rather than prose:
  resume / inspect / share commands are the deterministic next steps today.
- Bullet/timeline recap modes and recap-specific export variants are still
  future work; do not imply they have shipped yet.

### Design targets

| Target | Why it matters |
|---|---|
| Structured session chapters | Long-running work needs narrative sections such as setup, execution, decisions, blockers, and outcomes |
| Multi-format recap modes | Different consumers need prose, bullets, and timeline views over the same underlying session facts |
| Actor-aware storytelling | Collaboration cues should distinguish user, agent, automation, and reviewer contributions clearly |
| Actionable endings | Recaps should close with pending approvals, unresolved risks, and recommended next steps instead of passive summaries |
| Export-ready portability | Storytelling output must remain usable in plain text, saved manifests, and future dashboard summaries |

### Remaining scope

| Area | Remaining work |
|---|---|
| Session/status follow-through | Let `/session` and `/sessions` acknowledge momentum or milestone cues as secondary context without obscuring objective status, blockers, or next actions |
| Neutral handoff/export contract | Keep `/collab`, `openclaw session share`, and `openclaw session export` facts-first and pasteable while the storytelling slice stays narrow |
| Session history transforms | Deterministic bullet, timeline, and prose recap modes derived from existing session/export metadata remain deferred future work |
| Dashboard/browser storytelling | Future mirrors can reuse shipped chapter names and fallback wording, but richer dashboard storytelling stays deferred |
| Review/onboarding guidance | Document which recap mode fits review, handoff, audit, or onboarding use cases once multiple recap modes exist |

### Dashboard surface alignment

| Surface group | Wave 29 expectation |
|---|---|
| `/session`, `/sessions` | The next restrained slice can add secondary momentum/milestone cues, but objective status/count/watch context must stay first-scan information |
| `/collab`, `openclaw session share` | Keep the shipped handoff snapshot neutral, actor-aware, and command-oriented; richer mood wording is still deferred |
| `openclaw session export` | Keep exports facts-first; recap-specific bullet/timeline/prose variants remain deferred |
| `/events`, `/outputs`, `/context` | Dense evidence remains linkable/referenceable from recaps instead of being duplicated verbatim |
| Browser/dashboard mirrors | Mirror only shipped chapter names and fallback wording from terminal surfaces; richer dashboard storytelling is still deferred |

### Implementation notes

- Treat narrative recaps as **derived views over existing facts**, not a separate
  source of truth that can drift from the underlying session data.
- Reuse collaboration metadata from Wave 20, predictive next-step language
  from Wave 28, and only the light momentum/milestone cues that can sit behind
  objective status in `/session` and `/sessions`.
- Keep dense evidence discoverable: narrative views should link back to commands,
  outputs, and decisions instead of flattening all detail into prose.
- Preserve pasteable plain-text output first; richer formatting can layer on top
  of the same chapter structure later.
- Avoid promising automatic summarization quality beyond what the local session
  metadata can support deterministically.

### Done-when

- [x] The restrained follow-through contract is documented: `/session` and `/sessions` may surface momentum/milestone cues without displacing objective status, while `/collab` and `session share/export` stay neutral. (shipped: DASHBOARD_SURFACES.md:197-198; CLI_QUICKSTART.md:410)
- [ ] A documented recap model exists for prose, bullet, and timeline session storytelling.
- [x] Plain-text share/show surfaces already identify actors, decisions, momentum/milestone state, and deterministic next steps using existing session facts.
- [x] Export/share/dashboard guidance stays aligned on recap chapter names and fallback wording. (shipped: DASHBOARD_SURFACES.md:98 chapter scaffold; CLI_QUICKSTART.md:667)
- [ ] Review and onboarding use cases are documented without requiring access to the full transcript.
- [x] Follow-on architecture and quickstart docs are updated when implementation begins. (shipped: CLI_ARCHITECTURE.md:364 Wave 29 storytelling slice; CLI_QUICKSTART.md:660)

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — recap model | Chapter structure, recap modes, and source-data mapping |
| B — export/share adoption | Session export/share/show surfaces that emit the narrative structure |
| C — collaboration + review guidance | Actor-aware storytelling, onboarding use cases, and dashboard mirror terminology |
| D — validation | Plain-text portability, deterministic recap shape, and evidence/backlink checks |
| E — docs | Architecture, quickstart, and dashboard-surface updates for recap workflows |

---

## Wave 30 — Premium Motion & Choreography Layer

**Status: ✅ Shipped**

**Goal:** apply a final polish layer across startup, transitions, approvals, and
completion states so OpenClaw feels intentionally choreographed once the motion,
status, monitoring, guidance, and recap primitives from earlier waves are in
place.

### Design targets

| Target | Why it matters |
|---|---|
| Product-level choreography | Startup, command execution, approvals, and completion should feel like parts of one system rather than isolated microinteractions |
| Attention management | Motion and reveal pacing should guide users toward the next important detail instead of adding decorative noise |
| Preference-aware polish | Personalization, reduced motion, plain mode, and density preferences must shape the choreography rather than disable the product feel entirely |
| Error/approval dignity | Failure and waiting states should feel calm, clear, and intentional instead of abrupt or chaotic |
| Sustainable implementation | The final polish layer should sit on reusable primitives from prior waves, not hard-code one-off animations into commands |

### Current shipped slice

Wave 30 has started as an **accessibility-first choreography baseline** over the
existing waiting, startup, warning, and celebration helpers:

- `_print_feedback(...)` is already the shared compact emphasis primitive for
  success, warning, and liveness cues.
- `_with_spinner(...)` is the current waiting-state choreography surface:
  reduced-motion mode swaps animation for a static working line, periodic
  `Still working on ...` heartbeats, and an explicit `response ready.`
  completion cue.
- `_print_startup_banner(...)` already ships the startup reveal fallback:
  plain mode and narrow terminals get a concise static banner, while larger TTY
  sessions keep the richer panel presentation.
- `_print_risky_action_warning(...)` provides the current calm approval-emphasis
  pattern for high/critical `/exec` and `/edit` actions before the approval
  prompt proper.
- `_celebration_burst(...)` remains the only decorative motion surface, and it
  already downgrades to a single-line confirmation in reduced-motion, plain, and
  non-TTY contexts.

### Remaining scope

| Area | Remaining work |
|---|---|
| Startup choreography | Extend the current static-vs-rich startup split into a more explicit first-session pacing model |
| Transition pacing | Define shared reveal order for session summaries, dashboards, recap surfaces, and approval flows beyond the current waiting helper |
| Completion + celebration cues | Expand tasteful completion cascades beyond `response ready.` and the existing celebration helper without adding noise |
| Error + approval choreography | Reuse the same pacing/emphasis rules across blocked, retrying, approval-waiting, and failure states, not just risky-action warnings |
| Preference controls | Keep documenting how users opt out or reduce intensity as more choreography surfaces adopt the shared rules |

### Dashboard surface alignment

| Surface group | Wave 30 expectation |
|---|---|
| Startup banner and first session surfaces | Current shipped slice is the plain/narrow static banner fallback versus the richer panel path; no extra startup animation is shipped yet |
| `/session`, `/watch`, `/collab`, recap flows | Shared reveal ordering is still future work; today these surfaces only inherit the compact feedback/completion language indirectly |
| Approval and retry paths | High/critical approval warnings already share the compact warning voice; retry/failure choreography is still follow-up work |
| Accessibility and personalization surfaces | `/accessibility status`, plain mode, and reduced motion already define the shipped downgrade rules for the current polish slice |
| Browser/dashboard mirrors | Mirror the same hierarchy and wording statically; terminal-only motion itself is not mirrored today |

### Implementation notes

- Wave 30 should be the **last** premium-polish wave; do not start it until the
  underlying motion, status grammar, monitoring, guidance, and recap primitives
  are stable enough to choreograph.
- Prefer composition of existing helpers over introducing a new animation engine
  or large framework dependency.
- Treat reduced-motion, plain mode, and high-density preferences as first-class
  choreography inputs, not after-the-fact fallbacks.
- Keep startup and completion polish measurable enough that docs, screenshots,
  and tests can describe the intended behavior consistently.
- Ensure celebratory feedback remains tasteful, optional, and subordinate to
  operational clarity.

### Done-when

- [x] A documented baseline exists for startup fallback, waiting-state heartbeats, compact warning emphasis, and subdued celebration behavior.
- [x] Preference and accessibility docs explain how reduced motion, plain mode, and narrow layouts affect the current polish slice.
- [x] Shared summary surfaces reuse the same pacing and emphasis rules instead of inventing per-command flourish. (shipped: `_print_feedback()` used consistently across modules; DASHBOARD_SURFACES.md:99)
- [x] Dashboard/docs guidance explains which current polish concepts stay terminal-specific and which only mirror as static hierarchy/text.
- [x] Follow-on architecture and quickstart docs are updated when implementation begins. (shipped: CLI_ARCHITECTURE.md:391 Wave 30 choreography; CLI_QUICKSTART.md:688)

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — choreography primitives | Startup/transition/completion timing model and helper boundaries |
| B — surface adoption | Startup banner, summaries, approvals, retries, and recap surfaces using the shared polish rules |
| C — preferences + accessibility | Motion intensity controls, plain-mode parity, and density-aware presentation guidance |
| D — validation | Reduced-motion/plain-text checks plus consistency review across touched surfaces |
| E — docs | Architecture, quickstart, and dashboard-surface updates for premium choreography |

---

## Wave 31 — Intelligent Command Suggestions & Inline Assist

**Status: ✅ Shipped (current shell-polish slice)**

**Goal:** reduce prompt friction by surfacing the next best command at the moment
users need it and by making long-running agent work more transparent while it is
in flight.

### Focus

- contextual inline hints after errors, approvals, completions, and watch
  transitions
- consistent gesture verbs like `retry`, `resume`, `share`, `inspect`, and
  `export`
- user controls for suggestion intensity and scope
- reuse the shared split-bar shell for top-context and bottom-control hints
- live phase / step messaging during longer-running operations
- recent completed-step acknowledgements and honest trust-building progress copy

### User value

- faster recovery
- fewer "what do I do next?" pauses
- easier command discovery for less frequent users
- better operator trust because the agent explains what it is doing while it
  works

### Likely surfaces

- `/exec` and approval flows
- `/watch status`
- `/session`, `/sessions`, `/collab`
- `/outputs`, `/context`
- spinner / wait-state helpers and response-status chrome
- mode-switching and command-hint surfaces around the primary chat area

### Explicit design rules

- **Current phase messaging** — e.g. `Phase: analyzing code`
- **Current step messaging** — e.g. `Step 3/7: validating output`
- **Completed-step acknowledgement** — short `done` cues before moving on
- **Trust cues** — elapsed time, checkpoint passed, deterministic counts, and
  clear cancel/help affordances
- **Safe wording rules** — specific action verbs, no fake percentage bars, no
  vague `busy` copy, and no silent "thinking" placeholders when the system knows
  more

### Risk

**Low** — builds directly on Wave 28 predictive affordances and Wave 30 pacing
language.

### Shipped evidence (current slice)

Wave 31 is currently shipping as a **shell-polish slice**, not the entire
late-wave pane-compositor vision:

- `_spinner_progress_snapshot(...)` and `_with_spinner(...)` now provide the
  live wait-state copy: `warming up` / `working` / `wrapping up`, deterministic
  `step N/3`, explicit trust cues, periodic reduced-motion heartbeats, and the
  shared `response ready.` completion cue.
- `print_response(...)` now pairs the main answer with a **Suggested
  follow-ups** block plus a compact **bottom bar** footer when hints are
  enabled.
- `_print_followup_suggestions(...)` is the shipped bottom-hint surface for
  `mode: chat` plus contextual next actions.
- `_top_context_bar_lines(...)` and `_print_top_context_bar(...)` now provide
  the always-on pre-prompt shell chrome: session/cwd/autoroute state, linked
  plan/task cues, hidden-context visibility, and the latest recovery hint all
  stay visible ahead of the next prompt.
- `/exec` and `/edit` approval flows now reuse compact review lines plus trust
  and recovery cues so higher-friction interactions share the same shell-polish
  vocabulary instead of stopping at the bare preview/prompt boundary.
- `/followup [on|off]` controls the auto-suggestion footer, while existing
  `/ratehint` and `/pathhints` preferences participate in the same post-response
  trust/assist layer.

### Deferred scope

- broader Wave 31 coverage for `/watch`, `/session`, and `/collab` shell
  adoption beyond the currently shipped top-context/status/footer baseline
- per-step completed-step acknowledgements beyond the shared completion cue

### Docs/dashboard notes

- `docs/CLI_ARCHITECTURE.md`, `docs/CLI_QUICKSTART.md`, and
  `docs/DASHBOARD_SURFACES.md` should describe this shipped slice truthfully.
- `docs/COMMANDS.md` remains intentionally unchanged because this docs/tests pass
  did not change command metadata.

---

## Wave 32 — Instant Replay & Session Bookmarks

**Status: ✅ Shipped (current bookmark slice)**

**Goal:** let users mark meaningful turns and jump back to them quickly.

### Focus

- session bookmarks with labels
- replay from a bookmark
- bookmark markers in timelines and history
- bookmark-aware share, export, and session views

### User value

- easier debugging
- clearer handoffs
- faster narrative review

### Likely surfaces

- `/bookmark`, `/bookmarks`
- `/replay --from`
- `/timeline`
- `/watch history`
- `session show`, `session share`, `session export`

### Risk

**Low to medium** — additive session metadata, but it builds on existing replay,
history, and session-storytelling surfaces.

### Shipped evidence (current slice)

Wave 32 is currently shipping as a **bookmark-and-replay slice**:

- `/bookmark [label]` saves a replay marker for the active session using the
  latest turn pair as the replay anchor.
- `/bookmarks` lists the saved markers as plain-text ids, labels, and turn
  numbers.
- `/replay --from <bookmark>` replays the current session from a saved bookmark,
  while `/replay <session> --from <bookmark>` works for prior sessions.
- `session show`, `session share`, and `session export` now carry bookmark
  metadata so handoffs and JSON exports preserve the same replay anchors.

### Deferred scope

- bookmark markers inside `/timeline` and `/watch history`
- bulk bookmark actions and richer bookmark management UX
- workflow-aware bookmark promotion for later Wave 33 reuse

---

## Wave 33 — Command Chaining & Workflow Macros 2.0

**Status: ✅ Shipped (current workflow slice)**

**Goal:** make repeatable CLI sequences composable, previewable, and shareable.

### Focus

- named workflows with readable step lists
- variable substitution from session context
- dry-run previews before execution
- workflow persistence and handoff integration

### User value

- one-command recovery/playbooks
- executable handoffs
- faster onboarding for repeated tasks

### Likely surfaces

- `/workflow` family
- `/events`
- `/collab`
- session export/share
- existing command dispatch and approval plumbing

### Risk

**Medium** — requires careful workflow scoping and preview accuracy, but it stays
within the existing terminal-first command model.

### Shipped evidence (current slice)

Wave 33 is currently shipping as a **workflow veneer over the macro engine**:

- `/workflow list`, `/workflow save <name> [last N]`, `/workflow preview <name>`,
  `/workflow run <name>`, and `/workflow rm <name>` now expose a clearer workflow
  UX while reusing the existing persisted macro store.
- `/workflow preview <name>` shows a dry-run step list before execution.
- `/workflow run <name>` resolves session-aware placeholders like `{cwd}`,
  `{session}`, `{plan}`, and `{task}` before dispatching slash commands.
- existing `/macro run <name>` also benefits from the same placeholder
  resolution, so the two surfaces stay behaviorally aligned.

### Deferred scope

- session export/share embedding of saved workflows
- workflow versioning and richer metadata beyond the shared macro store
- per-step approval gates for workflow execution

---

## Wave 34 — AI Quality & Experimentation Loops

**Status: ✅ Shipped (current slice)**

**Goal:** expose response-quality metadata and compact traceability directly in
the terminal.

### Focus

- `/trace` for last-route and last-response decision details
- `/quality` summary for ratings plus the latest routing confidence snapshot
- compact, honest trust metadata that stays readable in plain mode too

### User value

- more trust in model behavior
- faster inspection of why the agent chose a route
- better quality review without leaving the terminal

### Likely surfaces

- response footer / event logs
- `/trace`
- `/quality`
- session export with later quality metadata follow-on

### Risk

**Medium** — the shipped slice stays narrow and reuses existing rating and
routing metadata rather than introducing a new experiment system yet.

### Current shipped slice

- `/trace` shows the most recent routing decision with rationale, confidence,
  timestamp, and the latest saved rating context.
- `/quality` still renders the rating histogram, then adds the latest route and
  a direct nudge to inspect the full decision snapshot with `/trace`.

### Deferred scope

- local experiment variant management and side-by-side comparisons
- richer latency envelopes and longer quality history summaries
- export/schema work for portable quality metadata outside the CLI

---

## Wave 35 — Long-Form Reporting & Export Suites

**Status: ✅ Shipped (current slice)**

**Goal:** turn OpenClaw sessions into durable, audience-aware runbooks and export
artifacts.

### Focus

- runbook generation from existing session story/export data
- template-aware exports for operator, stakeholder, and postmortem views
- lightweight export-template discovery in the terminal

### User value

- better external sharing
- stronger stakeholder handoffs
- offline artifacts for audits, postmortems, and operational playbooks

### Likely surfaces

- `openclaw session export`
- `/runbook`
- `/export-templates`
- session share/export schemas

### Risk

**Medium** — export breadth and template validation need discipline, but the
feature remains a natural extension of Wave 29 storytelling and Wave 34 metadata.

### Current shipped slice

- `/runbook [template]` renders a long-form Markdown handoff from the current
  session using the existing storyline, collaboration snapshot, and saved
  outputs.
- `openclaw session export <id> --format runbook --template <name>` reuses the
  same rendering path for non-interactive export flows.
- `/exporttemplates` exposes the built-in template gallery so the terminal can
  truthfully advertise the available reporting modes before custom template
  authoring lands.

### Deferred scope

- user-authored export templates and richer redaction policy controls
- alternate file formats beyond the current JSON + Markdown runbook slice
- deeper schema work for browser/dashboard reuse of long-form report metadata

---

## Wave 36 — Workspace State & IDE-Like Recovery

**Status: ✅ Shipped (current slice)**

**Goal:** let users freeze and restore full workspace context so interruptions and
machine switches feel instant instead of manual.

### Focus

- workspace state capsules with cwd, tracked files, outputs, bookmarks, and watch
  state
- quick restore of the full terminal context
- human-readable capsule manifests before restore
- explicit watch-state carryover during restore

### User value

- faster recovery after interruptions
- easier machine-to-machine continuity
- stronger "terminal as IDE" experience

### Likely surfaces

- `/snapshot workspace`
- `/workspace save`, `/workspace restore`, `/workspace list`
- `session show`, `session export`

### Risk

**Medium** — state serialization needs discipline, but it builds on existing
session, output, and watch metadata.

### Current shipped slice

- `/workspace status` renders a human-readable workspace capsule for the active
  session, including cwd, tracked files, bookmarks, outputs, watch state, and a
  stable workspace signature.
- `/workspace save`, `/workspace list`, and `/workspace restore <capsule>` reuse
  the existing handoff manifest store instead of introducing a second recovery
  backend.
- restoring a workspace capsule now carries back the cwd, tracked files, plan,
  task, and saved watch state into the new session.
- `openclaw session export <id>` now includes a `workspace_capsule` block so the
  recovery snapshot is visible to non-interactive tooling as well.

### Deferred scope

- machine-to-machine sync and remote capsule transport
- workspace restore into the current session instead of always opening a new one
- richer pause/ask/retry controls beyond the restored watch-state snapshot

---

## Wave 37 — Pattern Library & Workflow Templates

**Status: ✅ Shipped**

**Goal:** turn successful workflows into reusable templates and a personal pattern
library.

Wave 37 ships as a thin **pattern-library veneer** over the existing workflow
and command-history primitives rather than a new mining or versioning backend.

### Current shipped slice

- `/pattern save <name> [last N|workflow NAME]` captures reusable command flows
  either from recent history or from an existing workflow.
- `/pattern list`, `/pattern show`, and `/pattern preview` turn those saved flows
  into a browsable terminal-native pattern library with lightweight source
  metadata.
- `/pattern run <name>` reuses the existing workflow/macro execution engine, so
  current-session placeholders like `{cwd}` and `{session}` still resolve before
  dispatch.
- `/pattern rm <name>` keeps the library editable without introducing a second
  persistence model.

### User value

- faster onboarding for repeatable tasks
- shareable operational playbooks
- a growing terminal-native knowledge base

### Deferred scope

- automatic pattern mining from successful sessions
- pattern versioning and variants over time
- export/import or handoff-backed sharing flows
- richer metadata, recommendations, or remote sync

### Risk

**Medium** — this slice stays low-risk by reusing the Wave 33 workflow runtime
and keeping mining/versioning deferred.

---

## Wave 38 — Multi-Actor Planning & Risk-Aware Handoffs

**Status: ✅ Shipped**

**Goal:** evolve collaboration from notes into structured planning with blockers,
owners, gates, and readiness checks.

### Focus

- structured planning with explicit owners and blockers
- risk and blocker tracking inside the terminal
- approval gates before risky or shared transitions
- handoff readiness audits

### User value

- clearer team coordination
- fewer surprise blockers at handoff time
- better auditability for shared work

### Likely surfaces

- `/plan structured`
- `/risk`, `/gate`, `/handoff check`
- `/collab assign`, `/collab status`

### Risk

**High** — ownership and gate semantics add complexity, but the work remains
local-session and terminal-first.

### Current shipped slice

Wave 38 ships as a structured collaboration veneer over the existing session,
collaboration, and handoff primitives.

- `/collab assign @actor TEXT` records an explicit owner for the next shared task
  or handoff step using the same collaboration event stream as notes and
  decisions.
- `/risk add <critical|high|medium|low> TEXT`, `/risk list`, and `/risk clear
  <index>` create a lightweight terminal-native risk register without a second
  datastore.
- `/handoff check` audits linked plan/task context, ownership, latest handoff
  state, watch state, and open risks before the next transition.
- `/collab status` now includes structured **ASSIGNMENTS** and **OPEN RISKS**
  sections so the current operator snapshot is easier to scan.

### Deferred scope

- `/plan structured` step-level planning views
- dedicated `/gate` commands and richer approval semantics
- remote enforcement, escalation flows, and browser/dashboard planning mirrors

---

## Wave 39 — Learned Routing & Personalized Quality Loops

**Status: ✅ Shipped**

**Goal:** personalize routing, quality targets, and suggested actions based on
observed session outcomes and ratings.

### Focus

- learned routing suggestions from past ratings
- quality prediction for likely-good paths
- local experiment loops and preference tuning
- visible fairness and rollback controls for learned behavior

### User value

- better default routing over time
- less manual tuning
- a more personal terminal copilot experience

### Likely surfaces

- `/routing analyze`, `/routing suggest`
- `/quality predict`
- `/loop start`, `/loop analyze`, `/loop apply`

### Risk

**High** — learned behavior must stay transparent and reversible, but it builds
directly on Wave 34 quality data.

### Current shipped slice

Wave 39 ships as a read-only personalization veneer over the existing routing
trace and rating history.

- `/rate` now captures the last routed slash command alongside each rating when a
  session trace is available.
- `/quality predict` uses that local history to highlight the best-rated route
  and the next-best lane without changing runtime routing behavior.
- `/routing suggest` and `/routing analyze` expose the same learned route summary
  in terminal-native plain text so the user can inspect personalization before
  trusting it.
- the learned output is advisory only; auto-routing behavior remains unchanged
  and transparent through the existing `/trace` surface.

### Deferred scope

- automatic route adaptation or live route rewrites
- local experiment loops and preference-tuning workflows
- fairness controls, rollback policies, and browser/dashboard mirrors

---

## Wave 40 — Long-Running Automation Dashboard & Operator Intelligence

**Status: ✅ Shipped**

**Goal:** give operators a richer terminal-native control plane for long-running
automation, alerts, and cross-session health.

### Focus

- automation dashboards for active watches, plans, and pending approvals
- predictive alerts for stale or risky automation
- fleet-style health summaries across active sessions
- incident notes and operator intervention tracking

### User value

- less cognitive load during long-running automation
- earlier warning before work stalls
- stronger operator confidence and audit trails

### Likely surfaces

- `/dashboard automation`
- `/alerts list`, `/alerts acknowledge`
- `/fleet status`, `/fleet health`
- `/incident log`, `/incident resolve`

### Risk

**Medium to high** — cross-session alerting adds coordination complexity, but it
extends Wave 27 visibility and Wave 31 trust cues cleanly.

### Current shipped slice

Wave 40 ships as a computed operator-control veneer over the existing session,
watch, handoff, and dashboard primitives.

- `/dashboard automation` surfaces a compact cross-session control plane with
  active sessions, live watches, pending interventions, handoff-ready sessions,
  and top alerts.
- `/alerts list` computes operator alerts from local session/watch state and
  `/alerts acknowledge <index>` lets the operator quiet a known alert without a
  new alerting backend.
- `/fleet status` and `/fleet health` reuse the same automation summary to give a
  fleet-style terminal overview without adding remote orchestration semantics.

### Deferred scope

- predictive alert tuning, escalation automation, and browser/dashboard mirrors
- remote fleet control or multi-machine automation management

---

## Wave 41 — Incident Log & Operator Resolution

**Status: ✅ Shipped**

**Goal:** turn operator issues into a lightweight local incident log so sessions
can capture what broke, what is still open, and when it was resolved without
adding a new backend.

### Focus

- local incident logging for the current session
- explicit resolve flows for operator follow-through
- handoff visibility for unresolved incidents
- automation dashboard counts that reflect real operator pain

### User value

- faster handoff audits when automation or approvals go sideways
- clearer operator memory of what is still unresolved
- better collaboration snapshots without leaving the terminal

### Current shipped slice

Wave 41 ships as a lightweight incident-log veneer over the existing
collaboration snapshot, handoff checks, and automation dashboard.

- `/incident list` shows unresolved session incidents in deterministic plain text
- `/incident log TEXT` records a new open incident as a collaboration event
- `/incident resolve <index>` resolves an open incident without introducing a
  second persistence layer
- `/collab status` now includes **OPEN INCIDENTS** alongside assignments and
  risks
- `/handoff check` blocks readiness on unresolved incidents and prints them in
  the audit
- `/dashboard automation` now includes an open-incident count in the operator
  summary

### Deferred scope

- incident notes, stale-marking, and richer incident lifecycle controls
- cross-session incident aggregation or fleet-wide incident queues
- escalation policies, auto-routing, and browser/dashboard mirrors

### Shipped evidence checklist

- [x] `/incident list|log|resolve` works in plain terminal output
- [x] collaboration snapshots and handoff audits surface unresolved incidents
- [x] automation dashboard reflects incident count
- [x] docs updated across roadmap, architecture, quickstart, and dashboard inventory

---

**Q: How do I know what wave to implement next?**
Check the Wave Status table at the top. The first `🔲 Ready` wave is next.
After shipping, update the status to `✅ Shipped`.

**Q: Can I combine lanes in one agent if they're small?**
Yes, if two lanes touch non-overlapping sections of the same file and the total
change is small (< 50 lines), one agent can own both. Document this in the
Orchestrator's synthesis note.

**Q: What if a feature turns out to be too risky or complex?**
Note the blocker in the wave section, mark the individual feature as `⚠️ Deferred`,
and ship the rest of the wave. Do not hold an entire wave for one feature.

**Q: Do I need to update this file when I ship a wave?**
Yes. Update the Wave Status table and mark done-when items. This file is the
source of truth for what has and has not been implemented.

**Q: What is the test command?**
```bash
python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_run_chat_uses_router_before_generic_chat_fallback \
       or test_run_chat_routed_edit_still_requests_approval \
       or test_run_chat_autoroutes_plan_candidate \
       or test_run_chat_supports_help_command \
       or test_help_output_includes_new_commands)" -q
```
Expected: **557 passed**.

**Q: What is the deploy command?**
```bash
scp src/openclaw_cli.py macbook:/Users/davevoyles/.local/share/openclaw-cli/
```
If `openclaw_cli_sessions.py` was changed, also deploy it:
```bash
scp src/openclaw_cli_sessions.py macbook:/Users/davevoyles/.local/share/openclaw-cli/
```

---

## Wave 42 — Source Rendering Reliability

Status: ✅ Shipped

**Problem:** Sources sometimes appear twice in CLI output (once inline in the body as unstripped markdown, and once in the 📎 Sources panel). Also, ANSI color codes (`36m`) visually bleed into clickable-link display text when OSC-8 terminal link support is absent.

### Features

- **Source deduplication guard**: If `text` passed to `_render_response_body` still contains a Sources-like heading section after extraction, strip it before rendering the body
- **Clickable link display fix**: In the ANSI (non-Rich) sources panel, detect when `display` text is corrupted (contains URL-like content concatenated with display label) and fall back to plain URL
- **Secondary Sources regex**: Add a broader fallback regex in `_preprocess_response_text` to catch sources sections in formats the primary regex misses (e.g., inline bullet list without a blank line before the heading)
- **ANSI source box width**: Use `shutil.get_terminal_size().columns` correctly so the sources box matches terminal width instead of being too short

### Done-when

- [x] Sources appear at most once per response
- [x] No ANSI codes appear in source URL/display text
- [x] Sources box width matches terminal width

**Risk:** 🟢 Low — render-path only, no data or routing changes

---

## Wave 43 — Context & Token Intelligence

Status: ✅ Shipped

**Problem:** Users have no visibility into how much context they've consumed or how full the model's context window is getting.

### Features

- **`/tokeninfo` command**: Shows estimated token usage for the current session. Rough estimate: sum of all message character lengths ÷ 4 (standard chars-per-token heuristic). Displayed as a visual progress bar toward common model limits (128k tokens), with color-coding: green < 50%, yellow 50–80%, red > 80%
- **Token count in footer**: When the API response includes a token count, display it in the response footer (already has `tokens` field — surface it more prominently)
- **Session duration in `/session`**: Include session age (time since first message) in the `/session` command output
- **Tip about `/tokeninfo`**: Add to the rotating startup tips pool

### Done-when

- [x] `/tokeninfo` shows estimated token count with progress bar
- [x] Footer shows token count when available from API
- [x] `/session` includes session age

**Risk:** 🟢 Low — new read-only command, no data mutation

---

## Wave 44 — Startup & First-Run Polish

Status: ✅ Shipped

**Problem:** The startup banner does not dynamically adapt its greeting, the tip-of-the-day pool has grown stale, and first-run users lack a brief orientation.

### Features

- **Time-of-day greeting**: Replace static "OpenClaw {ver}" header text with contextual greeting: "Good morning 🌅", "Good afternoon ☀️", "Good evening 🌙" based on local hour
- **Session count milestone**: After the Nth session (e.g., 10, 50, 100), show a brief celebration line ("🎉 100 sessions with OpenClaw!")
- **Refreshed tips pool**: Add 10 new tips about recently shipped commands (`/tokeninfo`, `/trace`, `/handoff check`, `/fleet health`, `/alerts`, `/collab decision`, `/bookmark`, `/overlay`, `/pattern`, `/draft multiline`)
- **`--no-banner` flag**: CLI argument to suppress the startup panel for scripting use

### Done-when

- [x] Startup greeting reflects time of day
- [x] Session milestones celebrated
- [x] 10 new tips in pool
- [x] `--no-banner` flag suppresses panel

**Risk:** 🟢 Low — cosmetic startup changes only

---

## Wave 45 — Context Pressure Guardrails

Status: ✅ Shipped

**Problem:** `/tokeninfo` shows a single session-wide estimate, but it does not
help users understand which actor is consuming most of the context or what the
best recovery step is as pressure rises.

### Features

- **Actor breakdown in `/tokeninfo`**: Keep the existing rough token heuristic,
  but add a per-actor estimate so users can quickly see how much context is
  coming from `user`, `assistant`, or other roles
- **Dominant-share cue**: Call out the largest actor share in one compact line so
  the biggest context driver is obvious at a glance
- **Escalated recovery guidance**: Keep the 50% stale-context nudge, upgrade 80%+
  guidance to recommend saving a `/bookmark` before `/clear`, and make 90%+
  pressure read as near-capacity

### Done-when

- [x] `/tokeninfo` shows actor-level token breakdowns
- [x] `/tokeninfo` highlights the dominant actor share
- [x] pressure guidance escalates at 50%, 80%, and 90% thresholds

**Risk:** 🟢 Low — read-only command refinement, no persistence or routing changes

---

## Context-pressure follow-through (next wave setup)

Status: 🟡 Active follow-up

**Current truth:** The detailed guardrail view still lives in `/tokeninfo`, but
the surrounding operator surfaces already carry lighter-weight pressure cues.

### Already shipped around Wave 45

- **`/context` pressure snapshot**: when the next send is estimated at 50%+ of
  the resolved window, `/context` now prints a context-pressure line for the
  next send plus recovery cues for overflow, hidden injected/system context, and
  bookmark-before-clear recovery
- **`/session` dashboard follow-through**: the Session Dashboard now echoes live
  next-send pressure, hidden-context load, and action links back to
  `/tokeninfo`, `/bookmark`, and `/promptdebug`
- **`/watch status` retry guidance**: active watch loops now show context
  pressure for the next retry and point operators toward `/tokeninfo`,
  `/bookmark`, `/context`, or `/promptdebug` when retries may be failing under
  a heavy inherited prompt

### Still deferred

- broader ambient overflow warnings outside those explicit inspection/status
  surfaces
- per-model context limits replacing the current resolved heuristic path
- deeper breakdowns by turn, command family, or other richer attribution slices

### Done-when for the next implementation wave

- [x] roadmap/docs describe the current guardrail footprint truthfully (shipped: PRODUCT-ROADMAP.md:45,48 — /tokeninfo shipped W21 with full context limit details; context-pressure tranche documented as shipped)
- [x] future implementation work treats `/tokeninfo` as the detailed inspector
  and adjacent surfaces as lighter follow-through, not as missing features (shipped: PRODUCT-ROADMAP.md:45 — /tokeninfo is the shipped detailed inspector)
- [x] deferred items stay limited to what is actually unshipped (shipped: UX_IMPROVEMENTS.md:3060-3065 "Still deferred" section accurately lists only genuinely unshipped items)

**Risk:** 🟢 Low — documentation only; clarifies current scope before the next
implementation pass

---

## Archive: Earlier late-wave drafts

The following ideas were earlier late-wave drafts and are **not** the canonical
Wave 30–35 roadmap above:

- an older **Wave 30 — Power Dashboard** concept centered on `/dashboard`,
  `/benchmark`, and `/timeline`
- an older **Wave 31 — Smart Suggestions, Export & Color Schemes** concept
  centered on `/followup`, `/export`, and `/colorscheme`

Use the canonical Wave 30–35 sections above as the source of truth for future
implementation.
