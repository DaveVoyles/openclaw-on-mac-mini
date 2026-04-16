# OpenClaw — Persistence and Versioning Boundaries

This document describes what state OpenClaw persists, where that state lives, and which modules own each storage boundary.

## Scope

Use this with [ARCHITECTURE](ARCHITECTURE.md), [DEPENDENCY_MAP](DEPENDENCY_MAP.md), and [BACKGROUND_TASKS](BACKGROUND_TASKS.md).

## Persistence surfaces

| Surface | Primary owner | Format | Typical path | Notes |
| --- | --- | --- | --- | --- |
| Discord thread history | `src/thread_store.py` | SQLite | `THREAD_DB_PATH` (default `/memory/openclaw.db`) | WAL enabled; stores thread metadata, messages, and tags. |
| Named conversation threads | `src/memory_thread_persistence.py` | JSON files | `THREADS_DIR` | Legacy-style disk snapshots with TTL enforcement on load. |
| CLI sessions | `src/openclaw_cli_sessions.py` | JSON + JSONL | per-user CLI data root | Metadata, events, outputs, watch state, and handoff capsules are stored separately. |
| Health trend history | `src/health_history.py` | SQLite | `/app/data/health_history.db` | Tracks service status and disk-usage history for trends/predictions. |
| Routing telemetry | `src/llm/telemetry.py` | JSONL | `data/routing_audit.jsonl` by default | Append-only audit stream with periodic trimming. |
| Approvals | `src/approval_store.py` | In-memory | process memory | Explicitly non-durable; cleaned up by background maintenance. |

## SQLite-backed stores

### Thread store

`src/thread_store.py` is the main durable conversation database.

Schema:

- `threads`: user/channel identity, title/name, status, timestamps, message counts
- `messages`: ordered thread content with rough token estimates
- `thread_tags`: many-to-many tags per thread

Boundary rules:

- `_get_db()` lazily initializes the connection.
- WAL mode is enabled via `PRAGMA journal_mode=WAL` for better concurrent read/write behavior.
- Foreign keys are enabled and message rows cascade on thread deletion.
- Async callers serialize access through a module-level `asyncio.Lock`, while blocking SQLite work runs in an executor.

Versioning implication: there is no explicit migration framework or `schema_version` table yet, so schema evolution currently depends on additive `CREATE TABLE IF NOT EXISTS` changes and compatibility at the query layer.

### Health history

`src/health_history.py` stores time-series operational snapshots.

- `health_checks` records service state transitions over time.
- `disk_usage` supports simple trend-based “days until full” prediction.
- A lazy singleton delays DB creation until the module is first used.

This store is optimized for operational trends, not transactional domain data.

## File-backed stores

### CLI sessions

`src/openclaw_cli_sessions.py` intentionally splits session persistence into multiple files inside one session directory:

- `metadata.json` — high-level session summary
- `events.jsonl` — append-only event log
- `watch_state.json` — watch/intervention state
- `routed_action_checkpoints.json` — bounded recovery snapshots
- `outputs/` — saved command/output artifacts

Why this boundary exists:

- metadata stays easy to load and sort,
- events remain append-friendly,
- watch state and checkpoints can evolve independently,
- a corrupted sidecar file does not necessarily destroy the whole session.

`atomic_write()` is used for overwrite-style artifacts, while `events.jsonl` is append-only.

### Named memory threads

`src/memory_thread_persistence.py` persists per-user thread snapshots as JSON files.

Key boundaries:

- thread names are sanitized into safe filesystem paths,
- auto-save threads are separate from named threads,
- TTL is enforced on load using `CONTEXT_TTL`, not by background deletion,
- history is truncated to `MAX_HISTORY_LENGTH` at restore time.

This is a compatibility-oriented persistence path: simple and human-inspectable, but weaker than the SQLite-backed thread store for search and scale.

### Routing telemetry

`src/llm/telemetry.py` appends lightweight JSONL audit entries when telemetry is enabled.

- `record()` is write-only and best-effort.
- `rotate_audit_log()` trims line count instead of rotating by file name.
- `tail()` and `summarise()` treat the log as an operational feed rather than authoritative state.

## Non-durable and bounded state

### Approval store

`src/approval_store.py` is intentionally in-memory.

- pending approvals disappear on restart,
- emergency-stop state is process-local,
- `cleanup_expired()` is the retention mechanism.

This boundary is important: approval UX is runtime coordination, not durable workflow history.

### Routed action checkpoints

CLI routed-action checkpoints are bounded by count and file size.

- only recent entries are kept,
- only text files below `ROUTED_ACTION_CHECKPOINT_MAX_FILE_BYTES` are captured,
- snapshots are recovery aids, not a full version-control replacement.

## Versioning guidance from the current architecture

Current stores use three practical versioning strategies:

1. **Schema-by-code for SQLite** — tables are created lazily and must stay backward-compatible until explicit migrations exist.
2. **Shape-tolerant JSON loading** — loaders generally fail soft on malformed or missing fields.
3. **Bounded compatibility shims** — older persistence modes remain in place where runtime flows still depend on them (for example, file-backed named threads alongside SQLite thread history).

## Caveats

- There is no unified migration registry across SQLite and JSON stores.
- Cross-store updates are not transactional.
- Some default paths differ by runtime (`/memory`, `/app/data`, user config directories), so docs and ops changes must respect the owning module rather than assume one global data root.
