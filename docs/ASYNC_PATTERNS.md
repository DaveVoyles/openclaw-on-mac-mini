# OpenClaw CLI — Async Execution Patterns

Reference for understanding how the synchronous CLI bridges to async server communication, and how to write new async command handlers correctly.

---

## Overview

The CLI is architecturally **synchronous** at the top level:

- User input comes from readline / `input()` in the REPL loop
- Command dispatch is a standard Python function call chain
- Click/argparse argument parsing is synchronous

Server communication (aiohttp) and background operations (ResearchAgent, plan execution) are **async**. The CLI bridges the two worlds with a thin wrapper.

---

## The `run_async` Bridge

All async coroutines called from synchronous CLI command handlers go through `run_async`:

```python
# src/openclaw_cli.py

def run_async(coro: Any) -> Any:
    """Run an async coroutine from the synchronous CLI entrypoint."""
    return asyncio.run(coro)
```

Usage throughout the codebase:

```python
# Plan management
result = run_async(read_plan(args.plan_id))
result = run_async(resume_plan(args.plan_id))

# Research agent
report = run_async(ResearchAgent().run(effective_query, on_progress=_progress))

# Shell command execution
result = run_async(run_shell_command(command_parts, cwd=_exec_cwd, timeout=60))

# Plan creation
create_result = str(run_async(create_plan(goal, steps_text="\n".join(step_commands))))
```

`asyncio.run()` creates a **new event loop** for each call. This is intentional — the CLI has no persistent event loop.

---

## Common Mistake: Nested `asyncio.run()`

**Problem:** Calling `run_async()` (which calls `asyncio.run()`) from inside an already-running event loop raises:

```
RuntimeError: This event loop is already running.
```

This happens if you try to call `run_async` from within an `async def` function or from a context where pytest-asyncio has already started a loop.

**Solutions:**

1. **In production code:** Only call `run_async` from synchronous command handlers, never from within `async def`.

2. **In tests:** Use `pytest-asyncio` and define the test function as `async def`. The test runner handles the event loop.

3. **If you need to bridge from an async context:** Use `await` directly instead of `run_async`:

   ```python
   # Wrong (inside async def):
   result = run_async(my_coroutine())  # ❌ RuntimeError

   # Correct:
   result = await my_coroutine()      # ✅
   ```

---

## Background Thread Pattern

For operations that should not block the REPL (e.g., version checks, fire-and-forget), the CLI uses daemon threads:

```python
# Version check at startup (non-blocking)
_update_thread: threading.Thread | None = threading.Thread(
    target=_check_for_update_sync,
    daemon=True
)
_update_thread.start()
```

Key properties:
- `daemon=True` — thread is killed when the main process exits; no cleanup needed
- The thread does its own `asyncio.run()` internally if it needs async work
- Results are communicated via module-level state or printed directly

Another example — long-running background fetch:

```python
def _run():
    result = run_async(fetch_something())
    # store result or print it

thread = threading.Thread(target=_run, daemon=True)
thread.start()
# REPL continues immediately
```

---

## Writing a New Async Command Handler

### Pattern 1: Simple async call (most common)

```python
def cmd_my_feature(args: argparse.Namespace, ctx: CliContext) -> None:
    """Handler for /my-feature command."""
    async def _fetch() -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{ctx.config.base_url}/my-endpoint") as resp:
                data = await resp.json()
                return data["result"]

    result = run_async(_fetch())
    print(result)
```

### Pattern 2: Async with progress callback

```python
def cmd_research(args: argparse.Namespace, ctx: CliContext) -> None:
    async def _progress(message: str) -> None:
        print(f"  ⏳ {message}")

    report = run_async(ResearchAgent().run(args.query, on_progress=_progress))
    print(report)
```

### Pattern 3: Background (non-blocking)

```python
def cmd_trigger_background(args: argparse.Namespace, ctx: CliContext) -> None:
    def _run():
        result = run_async(slow_operation(args.target))
        print(f"\n✅ Background task complete: {result}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print("⏳ Running in background…")
```

---

## Testing Async Code

`pyproject.toml` sets `asyncio_mode = "auto"` — all `async def` tests are automatically awaited.

### Mock a coroutine

```python
from unittest.mock import AsyncMock

async def test_my_async_handler(monkeypatch):
    mock_fetch = AsyncMock(return_value="test result")
    monkeypatch.setattr(mod, "fetch_something", mock_fetch)
    result = await mod.my_async_function("query")
    assert result == "test result"
    mock_fetch.assert_awaited_once_with("query")
```

### Test a sync handler that calls `run_async`

```python
from unittest.mock import patch, AsyncMock

def test_cmd_uses_run_async(capsys, monkeypatch):
    async def _fake_fetch():
        return "mocked result"

    monkeypatch.setattr(mod, "fetch_something", _fake_fetch)
    cmd_my_feature(_make_args(), _make_ctx())
    out, _ = capsys.readouterr()
    assert "mocked result" in out
```

### Test a background thread

```python
import threading

def test_background_thread_completes(monkeypatch):
    results = []

    async def _fake_slow():
        return "done"

    monkeypatch.setattr(mod, "slow_operation", _fake_slow)

    # Start the command
    cmd_trigger_background(_make_args(), _make_ctx())

    # Wait for the daemon thread to finish
    for t in threading.enumerate():
        if t != threading.main_thread():
            t.join(timeout=2)

    # Check side effects
    # ...
```

---

## The Event Loop in the CLI

- **No persistent loop:** The CLI does not create or hold a global event loop. Each `run_async` call creates and destroys a fresh loop via `asyncio.run()`.
- **REPL thread:** The main thread runs the REPL synchronously. Async work is dispatched into loops created on demand.
- **Background threads:** Each daemon thread that needs async work calls `asyncio.run()` independently — they are isolated from the REPL thread's execution.

This design means the CLI is easy to reason about (no shared async state) but does not support streaming results back to the REPL in real time without threads.
