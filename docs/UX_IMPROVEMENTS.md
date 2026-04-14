# OpenClaw CLI — UX Improvements Roadmap

> **Audience:** AI coding agents (Copilot, etc.) implementing CLI improvements autonomously.
> **How to use this doc:** Pick the next unshipped wave. Launch an agent fleet as described below. Verify done-when criteria. Mark the wave complete and move to the next.

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

Waves 21–30 should always reserve a dedicated docs/dashboard lane in parallel
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
| [Wave 25](#wave-25--multi-pane-layout-presets) | Multi-Pane Layout Presets | 🟡 In progress |
| [Wave 26](#wave-26--session-mood-celebration--emotional-feedback) | Session Mood, Celebration & Emotional Feedback | 🟡 In progress |
| [Wave 27](#wave-27--live-dashboard-shares--operator-visibility) | Live Dashboard Shares & Operator Visibility | 🔲 Ready |
| [Wave 28](#wave-28--gesture-language--predictive-affordances) | Gesture Language & Predictive Affordances | 🔲 Ready |
| [Wave 29](#wave-29--narrative-recaps--session-storytelling) | Narrative Recaps & Session Storytelling | 🔲 Ready |
| [Wave 30](#wave-30--premium-motion--choreography-layer) | Premium Motion & Choreography Layer | 🔲 Ready |

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
| `--no-stream` flag | `CliConfig.no_stream` field + `--no-stream` CLI flag added (stub for future SSE streaming) | ✅ |
| Streaming output | Token-by-token SSE streaming — deferred; backend exposes streaming internally but no client SSE endpoint yet | ⚠️ Deferred |

### Done-When

- [x] `⏱ Xs  •  N tokens  •  model` printed in dim below each response
- [x] Ctrl-C during spinner prints `[interrupted]` and returns to prompt cleanly
- [x] `--no-stream` flag wired into `CliConfig` and argparse
- [x] 180 tests pass
- [x] Deployed to macbook
- [ ] ⚠️ Streaming response tokens appear incrementally (deferred — needs backend SSE endpoint)

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
| `prompt_toolkit` integration | Full prompt_toolkit session (richer completion, multiline) | ⚠️ Deferred — not installed on macbook; readline sufficient |

### Implementation notes

- Tab completion uses `readline.set_completer` + `readline.parse_and_bind("tab: complete")` — no new dependencies
- Fuzzy suggestions use stdlib `difflib.get_close_matches(cutoff=0.6)`
- All features gracefully no-op when `readline is None` (non-POSIX platforms)

### Done-When

- [x] Tab completes `/` commands (names and aliases)
- [x] Up/Down arrow navigates history; history persists across restarts
- [x] Ctrl-R launches reverse history search (via readline)
- [x] `/cmd ?` prints usage and returns to prompt
- [x] Mistyped command shows `Did you mean /X?` suggestion
- [x] 180 tests pass
- [x] Deployed to macbook
- [ ] ⚠️ Full `prompt_toolkit` session (deferred — readline sufficient for now)

### Key Code Locations

| Item | Location |
|---|---|
| `run_chat()` prompt input | `~L5310` — currently uses `input()` or `readline` |
| Slash command registry | `build_chat_command_registry()` `~L4470` |
| Shell history persistence | `src/openclaw_cli_sessions.py` |
| `CliConfig` | `~L347` — add `no_readline: bool = False` |

### Dependencies

- `prompt_toolkit` — already a common Python package; add to `requirements.txt` if not present
- Graceful fallback to `input()` if `prompt_toolkit` is unavailable (guard with try/except at import)

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

- [ ] Tab completes `/` commands (names and first argument)
- [ ] Up/Down arrow navigates history; history persists across restarts
- [ ] Ctrl-R launches reverse history search
- [ ] `/cmd ?` prints usage and returns to prompt
- [ ] Mistyped command shows "Did you mean /X?" suggestion
- [ ] Falls back to plain `input()` if `prompt_toolkit` is missing
- [ ] 180 tests pass
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
| File edit preview / diff confirm | ⚠️ Deferred — requires deep hook into `/edit` handler |
| Token budget warning | ⚠️ Deferred — context window sizes vary by model; no reliable cap available |

### Done-When

- [x] Status bar prints after each AI response: turns in context, autoroute state, optional session
- [x] Error recovery hint appears after AI errors
- [x] Session badge in prompt is cyan; `autoroute:off` badge is yellow (distinct, warning-like)
- [x] 180 tests pass
- [x] Deployed to macbook
- [ ] ⚠️ `/edit` diff preview (deferred)
- [ ] ⚠️ Token budget warning (deferred)

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
- [ ] Failed routed actions print a concrete recovery hint when one exists
- [ ] Approval flows end with a short “what happened / how to recover” recap for risky actions
- [ ] Usage errors follow one consistent style across REPL commands
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
- [ ] Session/status output surfaces active automation state by default
- [ ] Retry paths explain when the CLI auto-retried and why
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

- [ ] `/why` explains the last route/tool decision from recorded session data
- [ ] Auto-routed actions surface a visible confidence badge
- [ ] Users can inspect the exact grounding block used for the last analyze/research/write action
- [ ] Saved outputs expose prompt/session lineage and provenance metadata
- [ ] `/events` can filter down to decision-centric entries
- [ ] Approval prompts explain why a risk level was chosen
- [ ] Ambiguous prompts that stay in chat can explain the top blocking reason
- [ ] 180 tests pass
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

- [ ] Multiline compose mode works without bypassing slash-command routing
- [ ] `/draft save`, `/draft load`, and `/draft clear` manage unsent prompts predictably
- [ ] Large risky pastes surface a preview-oriented safeguard before execution
- [ ] Prompt badges reflect normal vs draft vs multiline state
- [ ] Interrupted or failed submissions can restore the last unsent prompt
- [ ] 180 tests pass
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
- [ ] Full `tests/test_openclaw_cli.py` suite is green (currently 203 passed / 5 failing baseline)
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
- [ ] Full `tests/test_openclaw_cli.py` suite is green (baseline still has 5 unrelated failures)
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
- [ ] Full `tests/test_openclaw_cli.py` suite is green (baseline still has 5 unrelated failures)
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
| Guarded fallback behavior | `_overlay_available()` blocks prompts on non-TTY stdin/stdout and falls back to the regular list output |

### Future expansion notes

- Approval-preview overlays are still future work, not a blocker for Wave 21.
- Arrow-key/full-screen selection remains intentionally deferred until a later UX wave proves it is worth the extra complexity.
- Additional pickers can be added in later waves without reopening the initial shipped overlay slice.

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

- [ ] A documented status grammar exists for badge meaning, progress-cell shape,
      and plain-text equivalents.
- [ ] Core watch/session/event surfaces reuse the same badge and progress-cell
      vocabulary instead of per-command phrasing.
- [ ] Emoji packs and accessibility modes preserve status meaning without
      requiring color or Rich-only affordances.
- [ ] `docs/DASHBOARD_SURFACES.md` stays aligned on shared terminology and
      fallback expectations for dashboard mirrors.
- [ ] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave.

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

- [ ] Shared header/section primitives exist for dashboard-style CLI output in
      both Rich and plain/ANSI paths.
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

**Status: 🟡 In progress**

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
- `/events` does **not yet** have a dedicated preview strip or expanded-row mode.
- Browser/dashboard preview panes are still a planning target rather than a
  shipped implementation.

### Dashboard/docs alignment

| Surface group | Wave 24 expectation |
|---|---|
| `/outputs`, `/outputs overlay` | The shipped preview path is bounded inline output with metadata + truncation messaging; richer preview blocks remain follow-up work |
| `/sessions`, `openclaw session list --interactive` | Selection now opens the compact Session Dashboard plus the resume command; share/collaboration actions are still separate |
| `/watch status`, `/watch history` | These are the live focused-inspection windows today, surfacing phase/retry/note context before longer history |
| `/context`, `/events`, `openclaw session show` | `/context` keeps the bounded grounding preview, while `session show` is the current deep inspection path; `/events` preview strips are deferred |
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

**Status: 🟡 In progress**

**Goal:** introduce opt-in workspace presets that keep multiple related surfaces
visible together for power users without turning the default CLI into a
full-screen terminal app.

**Current shipped slice:** Wave 25 currently ships the **preset contract**, not
the full pane renderer. `/layout focus`, `/layout watch-monitor`, and
`/layout handoff` now persist the named preset, `/layout` reports the current
primary/supporting surface pairing plus the width/accessibility fallback, and
`/layout reset` returns to the default single-pane mode. The actual multi-pane
canvas remains follow-up work.

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
| Pane state management | Current state tracks the remembered preset and fallback mode; explicit active-pane focus transitions are still deferred |
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
- [ ] Multi-pane rendering is opt-in, accessibility-aware, and collapses cleanly
      on unsupported terminals.
- [ ] Focus switching is defined with non-interactive equivalents.
- [x] Preset persistence and reset behavior are defined through `/layout` and
      `/accessibility status`.
- [ ] `docs/DASHBOARD_SURFACES.md` records each preset’s intended surfaces and
      downgrade behavior.
- [ ] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave.

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

**Status: 🟡 In progress**

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
- [ ] Session, recap, and collaboration surfaces can express momentum or
      milestones without obscuring core status.
- [x] Emotional feedback respects plain mode and reduced motion for the shipped
      celebration paths.
- [ ] `docs/DASHBOARD_SURFACES.md` documents where mood cues are allowed and how
      they degrade.
- [ ] `docs/CLI_ARCHITECTURE.md` and `docs/CLI_QUICKSTART.md` are updated
      alongside the implementation wave.

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — mood vocabulary | Status/mood families, text equivalents, and preference boundaries |
| B — session/recap adoption | `/session`, `/sessions`, recap, and completion/closure surfaces |
| C — collaboration sentiment | `/collab`, share/export, and actor-aware tone cues |
| D — validation + restraint | Accessibility, plain-mode, and “no excessive celebration” regression checks |
| E — docs/dashboard sync | Roadmap, dashboard inventory, architecture, and quickstart updates |

---

## Wave 27 — Live Dashboard Shares & Operator Visibility

**Status: 🟡 Partial**

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

## Wave 28 — Gesture Language & Predictive Affordances

**Status: 🟡 Partial**

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

## Wave 29 — Narrative Recaps & Session Storytelling

**Status: 🔲 Ready**

**Goal:** turn raw session history into durable recap artifacts that explain what
happened, who acted, what changed, and what should happen next so handoffs,
reviews, and onboarding do not require reading full event logs.

### Design targets

| Target | Why it matters |
|---|---|
| Structured session chapters | Long-running work needs narrative sections such as setup, execution, decisions, blockers, and outcomes |
| Multi-format recap modes | Different consumers need prose, bullets, and timeline views over the same underlying session facts |
| Actor-aware storytelling | Collaboration cues should distinguish user, agent, automation, and reviewer contributions clearly |
| Actionable endings | Recaps should close with pending approvals, unresolved risks, and recommended next steps instead of passive summaries |
| Export-ready portability | Storytelling output must remain usable in plain text, saved manifests, and future dashboard summaries |

### Scope for implementation

| Area | Planned work |
|---|---|
| Recap composition model | Define reusable chapter structure for goals, key events, decisions, outputs, blockers, and next actions |
| Session history transforms | Plan helper logic that can derive bullet, timeline, and prose recap modes from existing session/export metadata |
| Collaboration narration | Integrate actor-tagged notes, decisions, and handoff snapshots into the narrative flow without duplicating raw logs |
| Export and share surfaces | Extend `openclaw session share`, `openclaw session export`, and recap-style commands with storytelling-oriented variants |
| Review/onboarding guidance | Document which recap mode fits review, handoff, audit, or onboarding use cases and how plain-text fallbacks should read |

### Dashboard surface alignment

| Surface group | Wave 29 expectation |
|---|---|
| `/session`, `/collab` | Summaries and handoff surfaces expose concise chapter-style recaps and recommended follow-ups |
| `openclaw session show`, `openclaw session export`, `openclaw session share` | Shared narrative structure across terminal inspection, pasted summaries, and saved artifacts |
| `/events`, `/outputs`, `/context` | Dense evidence remains linkable/referenceable from recaps instead of being duplicated verbatim |
| Browser/dashboard mirrors | Session detail pages and handoff cards reuse the same recap sections, chapter names, and next-step wording |
| Future review flows | Narrative exports support onboarding, async review, and audit trails without requiring the raw transcript first |

### Implementation notes for the future wave

- Treat narrative recaps as **derived views over existing facts**, not a separate
  source of truth that can drift from the underlying session data.
- Reuse collaboration metadata from Wave 20, sentiment/momentum cues from nearby
  UX work, and predictive next-step language from Wave 28 where helpful.
- Keep dense evidence discoverable: narrative views should link back to commands,
  outputs, and decisions instead of flattening all detail into prose.
- Preserve pasteable plain-text output first; richer formatting can layer on top
  of the same chapter structure later.
- Avoid promising automatic summarization quality beyond what the local session
  metadata can support deterministically.

### Done-when

- [ ] A documented recap model exists for prose, bullet, and timeline session storytelling.
- [ ] Narrative sections identify actors, decisions, blockers, outputs, and next steps using existing session facts.
- [ ] Export/share/dashboard guidance stays aligned on recap chapter names and fallback wording.
- [ ] Review and onboarding use cases are documented without requiring access to the full transcript.
- [ ] Follow-on architecture and quickstart docs are updated when implementation begins.

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

**Status: 🔲 Ready**

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

### Scope for implementation

| Area | Planned work |
|---|---|
| Startup choreography | Refine banner, session bootstrap, and first-response pacing using motion-language primitives and accessibility preferences |
| Transition pacing | Define reveal order and settle timing for session summaries, dashboards, recap surfaces, and approval flows |
| Completion + celebration cues | Add tasteful completion cascades, milestone acknowledgements, and closure rituals that remain optional and subdued |
| Error + approval choreography | Harmonize warning, blocked, approval-waiting, and retry output so they share consistent pacing and emphasis rules |
| Preference controls | Document how users opt out, reduce intensity, or choose denser/faster presentation without losing information |

### Dashboard surface alignment

| Surface group | Wave 30 expectation |
|---|---|
| Startup banner and first session surfaces | The first seconds of the CLI should reflect the same hierarchy and pacing as later dashboards |
| `/session`, `/watch`, `/collab`, recap flows | Summary surfaces adopt shared reveal ordering, completion cues, and calm error/wait choreography |
| Approval and retry paths | Waiting, escalation, retry, and success transitions use the same choreography rules across commands |
| Accessibility and personalization surfaces | `/accessibility status`, `/layout`, and preference docs clearly explain motion intensity and fallback controls |
| Browser/dashboard mirrors | Any mirrored polish language stays terminology-compatible even when the dashboard cannot reproduce terminal motion exactly |

### Implementation notes for the future wave

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

- [ ] A documented choreography model exists for startup, transitions, waiting, error, approval, and completion states.
- [ ] Preference and accessibility docs explain how motion intensity, density, and plain mode affect the final polish layer.
- [ ] Shared summary surfaces reuse the same pacing and emphasis rules instead of inventing per-command flourish.
- [ ] Dashboard/docs guidance explains which polish concepts mirror to browser surfaces and which remain terminal-specific.
- [ ] Follow-on architecture and quickstart docs are updated when implementation begins.

### Recommended fleet split

| Lane | Ownership |
|---|---|
| A — choreography primitives | Startup/transition/completion timing model and helper boundaries |
| B — surface adoption | Startup banner, summaries, approvals, retries, and recap surfaces using the shared polish rules |
| C — preferences + accessibility | Motion intensity controls, plain-mode parity, and density-aware presentation guidance |
| D — validation | Reduced-motion/plain-text checks plus consistency review across touched surfaces |
| E — docs | Architecture, quickstart, and dashboard-surface updates for premium choreography |

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
Expected: **180 passed**.

**Q: What is the deploy command?**
```bash
scp src/openclaw_cli.py macbook:/Users/davevoyles/.local/share/openclaw-cli/
```
If `openclaw_cli_sessions.py` was changed, also deploy it:
```bash
scp src/openclaw_cli_sessions.py macbook:/Users/davevoyles/.local/share/openclaw-cli/
```
