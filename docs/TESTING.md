# OpenClaw CLI — Testing Guide
<!-- Updated: 2026-06-07 -->


Reference for writing, running, and maintaining the CLI test suite.

---

## Quick Start

```bash
make smoke          # Core correctness gate (~18s) — run before every push
make test-fast      # All tests except slow/expensive — skips time-intensive tests
make test           # Full suite (parallel via xdist)
```

---

## Test File Location

| File | Lines | Tests |
|------|-------|-------|
| `tests/test_openclaw_cli.py` | ~7,029 | 441 |
| `tests/conftest.py` | ~100 | fixtures only |

All CLI tests live in a single file organized into test classes by feature area.

---

## How to Run

### Quick (recommended for development)

```bash
make test
# Equivalent:
.venv/bin/python3 -m pytest tests/ -x -q --tb=short
```

### CLI tests only (faster bootstrap, no conftest.py overhead)

```bash
make test-cli
# Equivalent:
.venv/bin/python3 -m pytest --noconftest -o addopts='' \
  tests/test_openclaw_cli.py tests/test_dashboard.py -q
```

### Full pytest with default config (parallel, retries)

```bash
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py -q
```

### Verbose output

```bash
make test-verbose
# Equivalent:
.venv/bin/python3 -m pytest tests/ -v --tb=short
```

### Run a single test or class

```bash
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py::TestRenderResponse -q
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py::TestRenderResponse::test_plain_fallback -q
```

### Inside Docker (matches CI/production Python 3.12)

```bash
./run_tests.sh
./run_tests.sh -k "spinner"   # keyword filter
./run_tests.sh tests/test_openclaw_cli.py  # specific file
```

---

## Parallel Execution

`pyproject.toml` enables `pytest-xdist` by default:

```toml
addopts = [
    "-n", "auto",        # use all available CPU cores
    "--dist", "loadfile", # keep all tests from the same file on the same worker
    "--reruns", "2",      # retry flaky tests up to 2 times
    "--reruns-delay", "1",
]
```

To run single-process (easier for debugging):

```bash
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py \
  --override-ini="addopts=" -q
```

---

## The 5 Excluded Flaky Tests

These tests are excluded from standard `make test` runs due to timing sensitivity or environment coupling. They should be investigated before merging if related code changes:

| Test | Reason |
|------|--------|
| `test_spinner_reduced_motion_heartbeat` | Depends on precise `time.sleep` timing; flaky under parallel load |
| `test_update_check_background_thread` | Thread join timing; can race with process teardown |
| `test_exec_streaming_output` | Subprocess streaming — sensitive to shell environment |
| `test_research_stream_progress` | Network mock timing in streaming async test |
| `test_macro_run_async_dispatch` | Thread + async coordination; occasional ordering issue |

To explicitly exclude them:

```bash
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py \
  -k "not (test_spinner_reduced_motion_heartbeat or test_update_check_background_thread \
      or test_exec_streaming_output or test_research_stream_progress \
      or test_macro_run_async_dispatch)" -q
```

---

## conftest.py Fixtures

Seven fixtures in `tests/conftest.py` provide shared test infrastructure:

### 1. `_patch_memory_dirs` (autouse)
Redirects all memory module paths (`MEMORY_DIR`, `THREADS_DIR`, `SUMMARIES_DIR`, `HANDOVER_DIR`, `_PREFS_DIR`) to a `tmp_path`-scoped temp directory. This ensures tests never touch the real filesystem or each other's memory state.

### 2. `_clear_module_caches` (autouse)
After each test, clears module-level caches (`_model`, `_thinking_model`, `_system_prompt_cache`, `_tool_cache`) on any loaded `llm`, `memory`, `spending`, or `scheduler` modules. Prevents state leakage between parallel workers.

### 3. `mock_llm`
Returns an `AsyncMock` that resolves to `("Test response", [], "test-model")` — the standard `(text, history, model_name)` tuple returned by `chat()`. Use this for any test that stubs LLM calls.

### 4. `mock_discord_interaction`
Returns a fully-mocked `discord.Interaction` with `response`, `followup`, `user`, `channel_id`, and `edit_original_response` attributes stubbed as `MagicMock`/`AsyncMock`.

### 5. `reset_emergency_stop` (autouse)
Resets the global emergency-stop flag in `approval_store` to `False` before and after every test. Prevents cross-test bleed when approval/emergency-stop tests toggle this module-level flag. Consolidated from `test_approval_store_unit.py`, `test_approvals.py`, and `test_approvals_extended.py`.

### 6. `sched`
Yields a fresh `TaskScheduler` instance backed by a `tmp_path` temp file. Patches `scheduler.SCHEDULE_FILE` so no test ever writes to `/memory`. Consolidated from `test_scheduler.py` and `test_scheduler_coverage.py`.

### 7. `_config(**overrides)` (helper function, not a fixture)
Creates a `CliConfig` with sensible defaults (`base_url="http://localhost:8765"`, `token="secret-token"`, etc.). Call with keyword overrides for specific tests:

```python
cfg = _config(timeout_seconds=5, output_json=True)
```

---

## Key Testing Patterns

### Patching module-level globals

The CLI uses module-level globals (`_IS_TTY`, `_PREFS`, `_RICH_AVAILABLE`). Patch them with `monkeypatch.setattr`:

```python
import openclaw_cli as mod

def test_plain_output(capsys, monkeypatch):
    monkeypatch.setattr(mod, "_IS_TTY", True)
    monkeypatch.setattr(mod, "_RICH_AVAILABLE", False)
    # ... test body
```

### Patching `_PREFS` (use `setitem`, not `setattr`)

`_PREFS` is a shared dict. Mutate individual keys:

```python
monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
monkeypatch.setitem(mod._PREFS, mod._A11Y_REDUCED_MOTION, True)
```

Never replace the whole dict with `setattr` — this breaks the by-reference sharing contract.

### Capturing output

```python
def test_something(capsys):
    run_the_command()
    out, err = capsys.readouterr()
    assert "expected text" in out
```

### Adding tests for a new command

1. Find or create the test class for your command area (e.g., `class TestMyCommand`)
2. Use the `_config()` helper to build a `CliConfig`
3. Mock network calls with `unittest.mock.patch` or `AsyncMock`
4. Patch `mod._IS_TTY` and relevant `mod._PREFS` entries to control rendering
5. Call the handler function directly (not through the REPL parser) for unit tests
6. Use `capsys.readouterr()` to assert on output

Example skeleton:

```python
class TestMyCommand:
    def test_basic_output(self, capsys, monkeypatch):
        monkeypatch.setattr(mod, "_IS_TTY", True)
        monkeypatch.setitem(mod._PREFS, mod._A11Y_PLAIN_MODE, True)
        cfg = _config()
        # ... invoke command, assert output
        out, _ = capsys.readouterr()
        assert "expected" in out
```

---

## Coverage

```bash
.venv/bin/python3 -m pytest tests/test_openclaw_cli.py \
  --cov=src/openclaw_cli --cov-report=html -q

# Open report
open htmlcov/index.html
```

---

## Test Markers

Defined in `pyproject.toml`:

| Marker | Purpose | Run command |
|--------|---------|-------------|
| `smoke` | Core correctness gate (~108 tests, ~18s) | `pytest -m smoke` |
| `slow` | Tests taking >0.1s due to sleeps or I/O | `pytest -m "not slow"` |
| `integration` | Tests requiring a live server | `pytest -m integration` |
| `expensive` | Tests requiring external services | `pytest -m "not expensive"` |
| `requires_python312` | Needs Python 3.12 — run via `./run_tests.sh` | `pytest -m requires_python312` |
| `requires_secrets` | Tests needing API keys | — |
| `requires_docker` | Tests needing Docker | — |

Skip slow and expensive tests:

```bash
python3 -m pytest -m "not slow and not expensive" -q   # same as: make test-fast
```

Filter by marker:

```bash
.venv/bin/python3 -m pytest tests/ -m "not integration" -q
```

---

## Agent quality evaluation

OpenClaw scores agent responses two ways:

**1. Live quality-eval scorecard (production traffic).** Every `/ask` is journaled
(`error_tracker.journal_ask_outcome`) and `quality_eval_state.build_quality_eval_scorecard`
scores recent runs across four correctness/safety metrics — channel-leakage prevention,
follow-up anchor correctness, profile adherence, and table readability / copy-safety. The
rolled-up scorecard, run timeline, and latency percentiles are visible live on the
dashboard. Regression coverage for the journaling hook and the journal-backed latency
stats lives in `tests/test_error_tracker_journal_hook.py`.

**2. Offline replay eval harness (deterministic, CI-safe).** `src/offline_quality_eval.py`
replays a fixed set of prompt/response fixtures and scores them against tunable thresholds
with no network or model calls, so it is fully deterministic. It reports coverage,
source-diversity, evidence-completeness, unsupported-claim and warning rates, and a latency
bucket, plus per-domain breakdowns, drift detection against an optional baseline, and
bounded (advisory-only) threshold auto-calibration proposals.

```bash
# Run the harness directly (machine-readable JSON report; exit 0 = pass)
PYTHONPATH=src python3 -m offline_quality_eval \
  --fixtures tests/evals/fixtures/replay_prompts.json

# Compare against a saved baseline to surface drift
PYTHONPATH=src python3 -m offline_quality_eval \
  --fixtures tests/evals/fixtures/replay_prompts.json \
  --baseline path/to/baseline.json --output report.json

# Run as a test
python3 -m pytest tests/evals/test_offline_quality_eval.py -q
```

Fixtures live in `tests/evals/fixtures/replay_prompts.json`; add new prompt/response cases
there to grow coverage without touching the harness.

---

## Test Infrastructure Notes

- **`asyncio_mode = "auto"`** — all async test functions are automatically awaited (no `@pytest.mark.asyncio` needed)
- **`--timeout=30`** — any test running longer than 30 seconds is killed
- **`--dist loadfile`** — xdist keeps all tests from the same file on the same worker, reducing import overhead
- The test file imports `openclaw_cli as mod` and `openclaw_cli_sessions as sessions_mod` directly for white-box patching
