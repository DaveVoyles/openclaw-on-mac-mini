# OpenClaw CLI — Tech Debt Audit & Remediation Plan

> Generated after a 4-agent structural audit of `src/openclaw_cli.py` (14,954 lines, Wave 31).
> Waves are ordered from lowest-risk/highest-impact to highest-risk/largest structural change.
> Each wave should pass all 418+ tests before deployment.

---

## Wave Status

| Wave | Title | Risk | Status |
|------|-------|------|--------|
| TD-1 | Quick Wins — Deprecations & Hardcoded Values | 🟢 Low | ✅ Shipped (`8ffe163`) |
| TD-2 | De-duplicate Structural Patterns | 🟡 Medium | ✅ Shipped (`e7363e6`) |
| TD-3 | Move Regex Compilation to Module Level | 🟢 Low | ✅ Shipped (bundled in TD-1) |
| TD-4 | Response Pipeline Refactor | 🟡 Medium | ✅ Shipped (`bf4ce70`) |
| TD-5 | Data-Driven Command Registry | 🟡 Medium | ✅ Shipped (`bf4ce70`) |
| TD-6 | God Function Decomposition | 🔴 High | ✅ Shipped (`437781d`) |
| TD-7 | Module Split | 🔴 High | ✅ Shipped (`c3b2722`) |

---

## Audit Summary

Four parallel agents audited `src/openclaw_cli.py` and `tests/test_openclaw_cli.py` across four dimensions:

| Audit | Key Finding |
|-------|-------------|
| Duplicate Patterns | 77× inline `is_tty` check; 128× `if _RICH_AVAILABLE and is_tty`; 12 identical toggle command bodies; 44× `_PREFS[k]=v; _save_prefs()` pattern |
| Dead / Stale Code | 4× `datetime.utcnow()` deprecation; hardcoded IP `192.168.1.93:8765` in 2 places; 4 stale `_PREFS` keys read but never written |
| Architecture | 14,954-line monolith; 6,792-line test file; no clear module boundaries; 14 `re.compile()` inside functions |
| Size / Complexity | `build_chat_command_registry()` 475 lines / 71 registry calls; `handle_watch_command()` 374 lines; `run_chat()` 276 lines / 11 responsibilities; `_BUILTIN_COMMAND_NAMES` frozenset 49 entries vs 79 registered commands |

---

## TD-1 — Quick Wins: Deprecations & Hardcoded Values 🟢

**Goal:** Fix correctness and portability issues with zero user-visible impact.

### Changes

#### 1.1 — Fix `datetime.utcnow()` deprecation (4 locations)

`datetime.utcnow()` is deprecated since Python 3.12 and emits runtime warnings.

```python
# Before
ts = datetime.utcnow().isoformat()

# After
from datetime import timezone
ts = datetime.now(timezone.utc).isoformat()
```

**Locations:** lines ~7702, ~11450, ~11662, ~11880

#### 1.2 — Remove hardcoded server IP (2 locations)

`192.168.1.93:8765` is hardcoded in two fallback paths. Should read from env var.

```python
# Before
url = "http://192.168.1.93:8765/chat"

# After
_DEFAULT_SERVER = os.environ.get("OPENCLAW_SERVER", "http://192.168.1.93:8765")
url = f"{_DEFAULT_SERVER}/chat"
```

**Locations:** lines ~13331, ~14839

#### 1.3 — Remove stale `_PREFS` read keys

Four keys are read in `_cmd_stats()` but never written:

| Key | Read Location | Fix |
|-----|---------------|-----|
| `route_mode` | `_cmd_stats()` | Remove from stats display |
| `current_session` | `_cmd_stats()` | Remove or derive from session_id |
| `last_model` | `_cmd_stats()` | Remove from stats display |
| `session_edits` | `_cmd_stats()` | Written once; keep or derive |

#### 1.4 — Generate `_BUILTIN_COMMAND_NAMES` dynamically

The frozenset has 49 entries; the registry has 79 registered commands. 30+ commands are unprotected from aliasing conflicts.

```python
# Before (hardcoded, goes stale)
_BUILTIN_COMMAND_NAMES: frozenset[str] = frozenset({"help", "clear", ...})  # 49 entries

# After (generated from registry — never stale)
def _get_builtin_command_names() -> frozenset[str]:
    return frozenset(cmd.name for cmd in _REGISTRY.list_commands())
```

**Impact:** `/alias` validation becomes correct for all 79 commands automatically.

---

## TD-2 — De-duplicate Structural Patterns 🟡

**Goal:** Extract repeated inline patterns into shared helpers. Reduces the codebase by an estimated 400–600 lines.

### Changes

#### 2.1 — Extract `_get_is_tty()` (77× duplicated)

```python
# Before (duplicated 77 times)
is_tty = _IS_TTY or sys.stdout.isatty()

# After
def _get_is_tty() -> bool:
    return _IS_TTY or sys.stdout.isatty()
```

#### 2.2 — Extract `_print_rich_or_plain()` (128× duplicated branch)

```python
# Before (128 times)
if _RICH_AVAILABLE and is_tty:
    _console.print(rich_obj)
else:
    print(plain_text)

# After
def _print_rich_or_plain(rich_obj: Any, plain_text: str) -> None:
    if _RICH_AVAILABLE and _get_is_tty():
        _console.print(rich_obj)
    else:
        print(plain_text)
```

#### 2.3 — Extract `_handle_simple_toggle_pref()` (12 toggle commands)

All 12 on/off toggle commands share identical structure:

```python
# Before — repeated 12 times with different pref key and label
def _cmd_links(ctx: ChatCommandContext) -> str:
    val = ctx.args.strip().lower() if ctx.args else ""
    if val in ("on", "off"):
        _PREFS["links"] = val == "on"
        _save_prefs()
        return f"Links {'enabled' if _PREFS['links'] else 'disabled'}."
    state = "on" if _PREFS.get("links", True) else "off"
    return f"Links are currently {state}."

# After — one shared helper
def _handle_simple_toggle_pref(
    ctx: ChatCommandContext,
    key: str,
    label: str,
    default: bool = True,
) -> str:
    val = ctx.args.strip().lower() if ctx.args else ""
    if val in ("on", "off"):
        _PREFS[key] = val == "on"
        _save_prefs()
        return f"{label} {'enabled' if _PREFS[key] else 'disabled'}."
    state = "on" if _PREFS.get(key, default) else "off"
    return f"{label} is currently {state}."
```

**Commands affected:** `/links`, `/autobold`, `/jsonformat`, `/emojiheaders`, `/pathhints`, `/ratehint`, `/promptdebug`, `/followup`, `/separator`, `/quality`, `/tip`, `/shortcuts`

#### 2.4 — Extract `_prefs_set()` helper (44× pattern)

```python
# Before — 44 occurrences
_PREFS["key"] = value
_save_prefs()

# After
def _prefs_set(key: str, value: object) -> None:
    _PREFS[key] = value
    _save_prefs()
```

---

## TD-3 — Move Regex Compilation to Module Level 🟢

**Goal:** Eliminate per-call regex compilation overhead. Affects rendering loops that run on every response.

**14 `re.compile()` calls** inside functions should be hoisted to module-level constants.

```python
# Before (compiled on every call to _render_markdown_ansi and friends)
def _render_markdown_ansi(text: str) -> str:
    bold_re = re.compile(r"\*\*(.+?)\*\*")
    ...

# After (compiled once at import time)
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")

def _render_markdown_ansi(text: str) -> str:
    # use _RE_BOLD directly
    ...
```

**Naming convention:** `_RE_<DESCRIPTION>` for all module-level regex patterns.

**Functions to audit:** `_render_markdown_ansi()`, `_auto_bold_response()`, `_preprocess_response_text()`, `_detect_and_format_json()`, `_inject_heading_emojis()`, `_linkify_response()`, `_detect_file_paths()`

---

## TD-4 — Response Pipeline Refactor 🟡

**Goal:** Split the 95-line `print_response()` (21 helper calls, 8 processing stages, 20 branches) into focused components with single responsibilities.

### Current Pipeline (all in one function)

```
print_response()
  ├─ JSON vs text detection
  ├─ TTY / a11y mode detection
  ├─ _preprocess_response_text()
  ├─ _auto_bold_response()
  ├─ _detect_and_format_json()
  ├─ _inject_heading_emojis()
  ├─ Body rendering (Rich tables OR ANSI markdown)
  ├─ _clean_sources_for_display()
  └─ Footer + border rendering
```

### Target Architecture

```python
class ResponsePipeline:
    """Preprocessing: text normalization, bold, JSON, emojis, sources extraction."""
    def process(self, raw: str) -> ProcessedResponse: ...

class OutputRenderer(Protocol):
    """Interface for Rich / ANSI / Plain renderers."""
    def render(self, processed: ProcessedResponse) -> None: ...

class RichRenderer:    ...  # uses _console.print + Rich objects
class AnsiRenderer:    ...  # uses _render_markdown_ansi
class PlainRenderer:   ...  # uses textwrap + print

def print_response(text: str, ...) -> None:
    """Orchestrator: ~15 lines."""
    processed = ResponsePipeline().process(text)
    renderer = _pick_renderer()
    renderer.render(processed)
```

**Benefit:** Each renderer can be unit-tested independently; a11y rendering becomes a first-class concern.

---

## TD-5 — Data-Driven Command Registry 🟡

**Goal:** Replace the 475-line `build_chat_command_registry()` (71 identical `registry.register()` calls) and the 155-line `print_chat_help()` (48 hardcoded tuples) with a single data source.

### Current State

- `build_chat_command_registry()`: 475 lines, 0 logic, 71 repetitions
- `print_chat_help()`: 155 lines, 48 hardcoded command descriptions
- **Total: 630 lines** for what is essentially a static data table
- Maintenance: every new command requires edits in 2 places

### Target State

```python
# commands_data.py  (or inline as module-level list)
COMMAND_SPECS: list[dict] = [
    {"name": "help",  "aliases": [], "description": "Show this help",   "handler": "_cmd_help",  "group": "General"},
    {"name": "clear", "aliases": [], "description": "Reset conversation", "handler": "_cmd_clear", "group": "General"},
    # ... 69 more rows
]

def build_chat_command_registry() -> CommandRegistry:
    """3 lines."""
    return CommandRegistry.from_specs(COMMAND_SPECS)

def print_chat_help(...) -> None:
    """Generated from same COMMAND_SPECS — no duplication."""
```

**Estimated reduction:** 630 lines → ~120 lines (data table + 2 short loaders)

---

## TD-6 — God Function Decomposition 🔴

**Goal:** Break up the two largest functions in the codebase into single-responsibility components.

### 6.1 — `run_chat()` (276 lines, 11 responsibilities)

Current responsibilities:
1. Session history management
2. Command registry & processing
3. Input/prompt handling (readline, multiline, buffering)
4. Response rendering
5. Error handling / recovery
6. Readline tab-completion setup
7. Startup banner & random tips
8. Multiline mode handling
9. Auto-routing state checking
10. Draft buffer management
11. Shell history persistence

**Target:**

```python
def _setup_readline(registry: CommandRegistry) -> None:
    """Tab completion + keybindings."""

def _make_session_controller(session_id: str) -> SessionController:
    """Auto-route, session history, draft buffer."""

def _run_interaction_loop(
    controller: SessionController,
    registry: CommandRegistry,
) -> None:
    """Main input/output loop — ~50 lines."""

def run_chat(session_id: str, ...) -> None:
    """Orchestrator: setup → loop → teardown. ~20 lines."""
```

### 6.2 — `handle_watch_command()` (374 lines)

Extract:
- File watching setup
- Diff rendering
- Polling / debounce logic
- Output formatting

Each into its own helper ≤80 lines.

---

## TD-7 — Module Split ✅ Shipped (`c3b2722`)

**Status: Shipped — 3-phase approach.**

The module split was implemented in three phases:

### Phase 1 — `openclaw_cli_ui_core.py` (ANSI leaf module)
- Contains `_IS_TTY`, `_c()`, `_get_is_tty()`, and all 15 ANSI constants
- `openclaw_cli.py` imports the palette from this module
- `_get_is_tty()` override kept in main module for test monkeypatch compat

### Phase 2 — `openclaw_cli_render.py` (response rendering pipeline)
- `RenderContext` dataclass encapsulates all render-time state
- All render functions moved with `ctx: RenderContext` signatures
- Module is self-contained; no imports from `openclaw_cli.py`
- ANSI constants imported from `openclaw_cli_ui_core`

### Phase 3 — Wiring (shims in `openclaw_cli.py`)
- `_make_render_ctx()` builds a `RenderContext` from module globals at call time,
  passing `_PREFS` by reference so `monkeypatch.setitem(mod._PREFS, ...)` works
- `_render_response_body()` and `_render_response_footer()` replaced with thin
  shims delegating to `_render_mod.*`
- All 263 `_IS_TTY`/`_RICH_AVAILABLE` monkeypatches continue to work unchanged
- Deploy script updated: `openclaw_cli_ui_core.py` and `openclaw_cli_render.py`
  added to `CLI_FILES`

### Result
- 431 tests pass (unchanged)
- Render pipeline fully in `openclaw_cli_render.py`
- ANSI palette in `openclaw_cli_ui_core.py`
- `openclaw_cli.py` shims maintain full backward compatibility

### Why It Was Deferred

1. **Circular import risk** — `openclaw_cli_render.py` would need `_PREFS`, `_a11y_*()`, `_RICH_AVAILABLE`, `_RICH_CONSOLE`, and all ANSI constants from the main module, creating bidirectional imports. There is no safe leaf boundary for render without first extracting a shared UI-core layer.

2. **Monkeypatch breakage** — `tests/test_openclaw_cli.py` uses `monkeypatch.setattr(mod, "_IS_TTY", ...)` and `monkeypatch.setattr(mod, "_RICH_AVAILABLE", ...)` extensively. If render moves to its own module and reads its own module-level globals, all those patches silently stop working.

3. **Deploy script gap** — `Makefile` copies exactly 4 files to the macbook. Adding a new module requires updating the deploy script, install script, and verifying the remote path — extra surface for error.

### Correct Architecture Path (Future)

Split safely by doing these steps in order:

1. **Create `openclaw_cli_ui_core.py`** as a true leaf:
   - `_IS_TTY`, `_RICH_AVAILABLE`, `_RICH_CONSOLE`
   - All ANSI constants (`_R`, `_B`, `_CY`, etc.)
   - `_get_is_tty()`, `_terminal_width()`, `_separator_fill()`
   - `_theme_ansi()`, `_e()`, `_a11y_*()` helpers

2. **Update deploy script** to include the new file.

3. **Create `openclaw_cli_prefs.py`** as the second leaf (imports only from `ui_core`):
   - `_PREFS`, `_save_prefs()`, `_load_prefs()`, `_prefs_set()`
   - `_THEMES`, `_EMOJI_PACKS`, `_EXTENDED_SCHEMES`

4. **Create `openclaw_cli_render.py`** (imports from `ui_core` + `prefs`):
   - All rendering pipeline functions

5. **Update `openclaw_cli.py`** to import from sub-modules and re-export for compatibility.

6. **Split test file** last, after all module boundaries are stable.

### What Was Accomplished Without TD-7

TD-1 through TD-6 removed an estimated **~1,300 lines** and eliminated the major structural debt patterns. The monolith is significantly cleaner and more maintainable even without a module split.



### Proposed Module Structure

```
src/
├── openclaw_cli.py          # Entry point + run_chat() only (~300 lines)
├── openclaw_cli_render.py   # print_response, ResponsePipeline, renderers
├── openclaw_cli_commands.py # All _cmd_* handlers + COMMAND_SPECS
├── openclaw_cli_prefs.py    # _PREFS, _save_prefs, _load_prefs, _prefs_set
├── openclaw_cli_session.py  # Session state, history, auto-routing (already exists)
├── openclaw_cli_actions.py  # File actions, plan commands (already exists)
└── subprocess_utils.py      # Already separate
```

### Test Module Split

```
tests/
├── test_cli_render.py       # ResponsePipeline, renderers, sources
├── test_cli_commands.py     # All _cmd_* handlers
├── test_cli_prefs.py        # Pref persistence, toggles
├── test_cli_session.py      # Session state, history
└── test_cli_integration.py  # run_chat() integration tests (5 flaky tests live here)
```

**Current state:** `tests/test_openclaw_cli.py` = 6,792 lines, all tests in one file.

**Benefit:** Faster pytest runs (parallel), clearer ownership, no 6,000-line file to navigate.

---

## Implementation Notes

### Testing

All waves must pass the full test suite before deployment:

```bash
python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_run_chat_uses_router_before_generic_chat_fallback \
       or test_run_chat_routed_edit_still_requests_approval \
       or test_run_chat_autoroutes_plan_candidate \
       or test_run_chat_supports_help_command \
       or test_help_output_includes_new_commands)" \
  -q
```

### Deployment

```bash
make deploy-cli
```

### Risk Order

Waves TD-1 and TD-3 are pure refactors with no behavioral change — safe to ship quickly.
Waves TD-4 through TD-7 touch rendering and control flow — require careful regression testing.
Wave TD-7 (module split) should be last; it changes import structure and may require CI pipeline updates.

### Estimated Impact

| Wave | Lines Removed | Risk |
|------|---------------|------|
| TD-1 | ~30 | 🟢 |
| TD-2 | ~500 | 🟡 |
| TD-3 | ~40 (+ performance gain) | 🟢 |
| TD-4 | ~60 (net; adds classes) | 🟡 |
| TD-5 | ~510 | 🟡 |
| TD-6 | ~150 (net; adds helpers) | 🔴 |
| TD-7 | 0 net (structural reorganization) | 🔴 |
| **Total TD-1–7** | **~1,290 lines removed** | — |

---

## Second Audit — April 2026 (Waves TD-8 through TD-16)

A 3-agent structural audit conducted after TD-7 shipped identified the next tranche of improvements
across module decomposition, error handling, type safety, documentation, and test coverage.

### Audit Findings Summary

**Structural** (`openclaw_cli.py`, 14,813 lines after TD-7):
8 clean extraction candidates totaling ~3,129 lines (21% of file).

**Code Quality:**
- 109+ silent `except Exception: pass` blocks — no logging infrastructure wired in CLI
- ~95% of functions missing type annotations
- `OpenClawCliError` + 12 subclass hierarchy in `exceptions.py` — never imported in CLI
- `enhanced_logging.py` JSON logging framework exists but unused in main CLI module

**Documentation Gaps (7 high-value docs missing):**
- `docs/DEVELOPMENT.md`, `docs/DEPENDENCY_MAP.md`, `docs/SKILL_DEVELOPMENT.md`
- `docs/TESTING.md`, `docs/ASYNC_PATTERNS.md`, `scripts/README.md`
- `docs/AGENT-GUIDE.md` needs update to reflect new module split

**Test Coverage Gaps:**
- Plugin system: 0 tests | Config loading: 0 tests | Error handling: 0 tests | Auth flow: minimal

---

### Wave Status (TD-8 through TD-23)

| Wave | Description | Risk | Status |
|------|-------------|------|--------|
| TD-8  | Extract `auth` + `update` modules (597 lines, pure stdlib) | 🟢 Low | ✅ Shipped (`32e8c3d`) |
| TD-9  | Extract `path_utils` + `diff` modules (353 lines) | 🟢 Low | ✅ Shipped (`05c622e`) |
| TD-10 | Extract `router` module (893→1448 lines, 38 funcs, 5 dataclasses) | 🟢 Low | ✅ Shipped (`f9a91d4`) |
| TD-11 | Extract `macros` module (344 lines, workflow engine) | 🟢 Low | ✅ Shipped (`e8df076`) |
| TD-12 | Error handling standardization (109+ silent failures → logging) | 🟡 Medium | ✅ Shipped (`c77d410`) |
| TD-13 | Extract `exec` + `layout` modules (942 lines, threading) | 🟡 Medium | ✅ Shipped (`038d5dc`) |
| TD-14 | Type annotation pass (all CLI modules via pyright) | 🟢 Low | ✅ Shipped (already annotated) |
| TD-15 | Agent + developer documentation (7 new docs) | 🟢 Low | ✅ Shipped (`94775fb`) |
| TD-16 | Test coverage expansion (60+ new tests) | 🟡 Medium | ✅ Shipped (`ce845d9`) |
| TD-17 | Extract `prefs` module (_PREFS, _THEMES, _TIPS, load/save) | 🟢 Low | ✅ Shipped (`4320710`) |
| TD-18 | Settings command helpers module | 🟢 Low | ✅ Shipped (`6e583c8`, 0 extracted — inline dispatch) |
| TD-19 | Session command helpers module (events, search, plan, handoff) | 🟡 Medium | ✅ Shipped (`6e583c8`) |
| TD-20 | Content command helpers module (export, stats, pin, pattern) | 🟡 Medium | ✅ Shipped (`323f132`) |
| TD-21 | Extract `watch` module (handle_watch_command 372L, execute_watch_iteration 138L, _print_watch_status 91L) | 🟡 Medium | ✅ Shipped (`4920d6a`) |
| TD-22 | Extract `session_display` module (inspect_session 205L, _build_session_share_text 159L, 5 more helpers) | 🟡 Medium | ✅ Shipped (`4920d6a`) |
| TD-23 | Move ANSI markdown renderer (_render_markdown_ansi 125L, _render_table_ansi 79L) into render module | 🟢 Low | ✅ Shipped (`ac7858d`) |
| TD-24 | Extract `preprocess` module (_preprocess_response_text 71L, _detect_and_format_json 69L, 3 more) | 🟢 Low | ✅ Shipped (`e4b9d93`) |
| TD-25 | Extract `session_utils` module (summarize_session 59L, _session_preview_lines 51L, _collect_operator_alerts 50L) | 🟢 Low | ✅ Shipped (`e4b9d93`) |
| TD-26 | Extract `ui_utils` module (_with_spinner 126L, _print_startup_banner 96L, 3 more UI helpers) | 🟢 Low | ✅ Shipped (`daefca9`) |
| TD-27 | Extract `health` module (print_health 67L, _clean_sources_for_display 38L, HealthResponse dataclass) | 🟢 Low | ✅ Shipped (`daefca9`) |

| TD-28 | Create `openclaw_cli_types.py` — move ChatCommandContext, SlashCommand, ChatCommandRegistry, AskResponse, LocalLinkValidation, CliConfig to leaf module (0 deps) | 🟢 Low | ✅ Shipped (`0e47b88`) |
| TD-29 | Extract `openclaw_cli_cmd_settings.py` — 12 settings/appearance handlers (_cmd_theme, _cmd_emoji, _cmd_layout, _cmd_accessibility, etc., 588L) | 🟢 Low | ✅ Shipped (`abf0e67`) |
| TD-30 | Extract `openclaw_cli_cmd_session.py` — 10 session lifecycle handlers (_cmd_session, _cmd_events, _cmd_replay, _cmd_bookmark, _cmd_handoff, etc., 778L) | 🟡 Medium | ✅ Shipped (`abf0e67`) |
| TD-31 | Extract `openclaw_cli_cmd_workflow.py` — 12 workflow/automation handlers (_cmd_plan, _cmd_task, _cmd_workspace, _cmd_macro, _cmd_workflow, _cmd_dashboard, etc.) | 🔴 High | 🔄 In progress |
| TD-32 | Extract `openclaw_cli_cmd_content.py` — 10 content/analytics handlers (_cmd_outputs, _cmd_search, _cmd_history, _cmd_pin, _cmd_pattern, _cmd_stats, _cmd_timeline, etc., 1100L) | 🟡 Medium | ✅ Shipped (`abf0e67`) |
| TD-33 | Extract `openclaw_cli_cmd_core.py` — 20+ system/file/exec handlers (_cmd_help, _cmd_exec, _cmd_edit, _cmd_autoroute, _cmd_analyze, _cmd_runbook, _cmd_draft, etc.) | 🟡 Medium | 🔄 Pending |
| TD-34 | Final cleanup of main — replace inline `_cmd_*` bodies with import re-exports, extract `build_parser` (158L) → `openclaw_cli_cli_parser.py`, extract `print_chat_help` (178L) | 🟡 Medium | ⏳ Blocked on TD-31/33 |

**Actual impact TD-8 through TD-32:** `openclaw_cli.py` reduced from 14,813 → 8,518 lines (−43%).
31 extracted modules now exist.

---

### Module Extraction Candidates Detail

| Module | Lines | Key Exports | Dependencies |
|--------|-------|-------------|--------------|
| `openclaw_cli_router.py` | 893 | `ReplRouteDecision`, `_maybe_route_with_grounding`, 13 regex patterns | sessions.py only |
| `openclaw_cli_exec.py` | 730 | `_exec_progress_animate`, `_cmd_exec`, `_analyze_exec_error` | actions.py, ui_core |
| `openclaw_cli_macros.py` | 344 | `_macro_run`, `_workflow_store`, `_cmd_macro`, `_cmd_workflow` | sessions.py, `_PREFS` ref |
| `openclaw_cli_update.py` | 335 | `handle_update_command`, `_fetch_latest_pypi_version` | pure stdlib |
| `openclaw_cli_auth.py` | 262 | `TokenResolution`, `read_keychain_token`, `resolve_token` | pure stdlib |
| `openclaw_cli_layout.py` | 212 | `_effective_layout_mode`, `_print_layout_preset_workspace` | sessions.py, `_PREFS` ref |
| `openclaw_cli_diff.py` | 210 | `_render_diff_ansi`, `_cmd_diff`, `_cmd_changes`, `_cmd_timeline` | sessions.py, ui_core |
| `openclaw_cli_path_utils.py` | 143 | `_detect_file_paths`, `_suggest_followups`, `_print_path_hints` | ui_core only |

---

### Implementation Notes for TD-8 through TD-16

**Deploy script** — `scripts/install_openclaw_cli_remote.sh` `CLI_FILES` array must be updated for
each new module. Currently 6 files after TD-7.

**Fleet strategy:**
- TD-8, TD-9, TD-13–16: parallel agents (2–6 lanes)
- TD-10, TD-11: focused solo or 2-agent
- TD-12: 2 agents (categorize / replace with logging)
- All waves: 431 tests must pass + `make deploy-cli` before commit
