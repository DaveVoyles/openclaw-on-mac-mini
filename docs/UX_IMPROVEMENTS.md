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
| [Wave 16](#wave-16--search-aliases--pins) | Search, Aliases & Pins | ✅ Shipped |
| [Wave 17](#wave-17--macros--command-history) | Macros & Command History | ✅ Shipped |
| [Wave 18](#wave-18--response-rating--quality) | Response Rating & Quality | ✅ Shipped |

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


---

## Wave 18 — Response Rating & Quality

**Status: shipped**

| Feature | Description |
|---|---|
| /rate [good/ok/bad/meh/1-5] | Rate last AI response; maps to score 1-5; stored in _PREFS[ratings] (cap 500) |
| Session event | Each rating fires append_event(kind=rating) for session history |
| /quality | Shows total rated, avg score, star distribution bar chart, most recent 3 ratings |
| /ratehint [on/off] | Toggles post-response dim hint after each AI reply |
| Pref keys | show_rate_hint (default True); ratings list |
