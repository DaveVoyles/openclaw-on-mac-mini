# Slack ↔ Copilot CLI parity roadmap (Phases 2–7)

<!-- Created: 2026-05-21 -->
<!-- Status: PLAN ONLY — awaits user approval -->
<!-- Companion to: 2026-05-21-slack-copilot-bridge-plan.md (Phase 1 shipped, Phase 2 designed) -->

## User request

> "What other improvements can we make here? Come up with a detailed plan for
> improved functionality. I want to be able to have the Slack agent perform tasks
> within the CLI for me so that I can be as powerful when I am remote and using
> Slack, as I am right now when I interact directly with the terminal."

**Target outcome:** Working from Slack on a phone or laptop should feel like
sitting in front of the Mac Mini with `copilot` open in iTerm — same auth, same
working directory, same tool access, same conversational depth, same audit
trail. Anything you can do at the terminal, you can do from Slack.

## What "terminal parity" actually means

To name the gap precisely, here is what a real terminal session gives the user
today that one-shot `/copilot <prompt>` (Phase 2) does **not** give them:

| Capability | At the terminal | After Phase 2 alone | Gap |
|---|---|---|---|
| Multi-turn conversation | Natural — type, read, type again | Each `/copilot` call is independent; no memory between calls | **Phase 3** |
| File/image as context | Drop file path; paste image | Slack has no path on the host | **Phase 4** |
| Interactive prompts ("y/n?") | Answer in place | Times out or fails | **Phase 4** |
| Cancel a runaway command | `Ctrl-C` | No way to abort | **Phase 4** |
| Preview file edits before write | Copilot prints diff and asks | Hidden behind `--allow-all-tools` | **Phase 4** |
| Resume a prior session | `copilot --continue` | No session state surfaced | **Phase 3** |
| Quick repeated ops | Shell aliases, history recall | None | **Phase 5** |
| Find what you did yesterday | `history`, log files | Slack search only — no structured audit | **Phase 6** |
| Reach the NAS, not just Mac Mini | `ssh nas` | Mac Mini only | **Phase 7** |
| Scheduled tasks | `cron`, `launchd` | None | **Phase 7** |

The phases below close each gap in order of leverage.

## Risk classification

| Phase | Risk | Why |
|---|---|---|
| Phase 2 | High | Identity-equivalent host access (designed, awaits checkpoint) |
| Phase 3 | High | Same trust surface as Phase 2 + persistent sessions = larger blast radius if abused |
| Phase 4 | Medium | UX layer on Phase 3; no new trust boundaries |
| Phase 5 | Medium | Pre-canned shortcuts that wrap Phases 2–4; safer because surface is narrowed |
| Phase 6 | Low | Read-only audit + history surfacing |
| Phase 7 | High | Multi-host bridge; scheduling without supervision |

Every High risk phase reuses the same High-risk checkpoint discipline already
agreed for Phase 2: rollback plan, side-effect checkpoint, code-review gate.

---

## Phase 2 (recap) — One-shot host bridge

Already fully designed in
[`2026-05-21-slack-copilot-bridge-plan.md`](2026-05-21-slack-copilot-bridge-plan.md).

- `/copilot <prompt>` → SSH to `davevoyles@host.docker.internal` →
  `copilot --allow-all-tools -p "<prompt>"` → stream output to Slack thread.
- Per-invocation Slack button approval.
- 10-minute default timeout, `/copilot-cancel` kill switch.
- Audit log at `~/openclaw/data/audit/host_bridge.jsonl`.

**Phase 3+ assume Phase 2 is shipped.** Everything below extends that foundation
rather than replacing it.

---

## Phase 3 — Threaded interactive sessions (the parity unlock)

**Goal:** A `/copilot` invocation becomes a **conversation in a Slack thread**,
not a one-shot. Every reply in the thread becomes the next user turn for the
same long-lived `copilot` process. Closing the thread (or `/copilot-end`) ends
the session.

This is the single biggest parity win. Without it, Slack is "run one command";
with it, Slack is "open a terminal."

### Architecture

```
Slack: /copilot diagnose plex playback issues
   │
   ▼
slack_bot.py creates a thread, generates session_id (uuid)
   │
   ▼
host_bridge.py:
   conn = await asyncssh.connect("host.docker.internal", username="davevoyles")
   proc = await conn.create_process("copilot", term_type="xterm-256color")
   register session_id → (conn, proc, slack_thread_ts, last_activity)
   │
   ▼
Two long-running tasks per session:
   - stdout_pump: copilot.stdout → Slack thread (batched 2s)
   - stdin_pump:  Slack thread replies → copilot.stdin
   │
   ▼
Session ends when:
   - user types /copilot-end in thread
   - 10 min of no activity in either direction
   - host process exits
   - container restart (Phase 3.1 adds resume)
```

### New Slack surface

| Command / event | Behavior |
|---|---|
| `/copilot <prompt>` | Start session; create thread; run as first turn |
| Reply in `/copilot` thread | Send as next user turn to the same `copilot` process |
| `/copilot-end` (in thread) | Gracefully close session |
| `/copilot-cancel` (in thread) | `SIGINT` current turn; keep session alive |
| `/copilot-sessions` | List your active sessions with last-activity timestamps |
| `/copilot-attach <session_id>` | Re-open a thread for an existing background session |

### Session registry

Persisted to `~/openclaw/data/host_bridge/sessions.json` (gitignored). Survives
container restarts so Phase 3.1 can offer resume. Schema:

```json
{
  "<session_id>": {
    "slack_user": "U0ATT7XTDGS",
    "slack_channel": "C…",
    "slack_thread_ts": "1716304200.123456",
    "started_at": "2026-05-21T08:00:00Z",
    "last_activity": "2026-05-21T08:14:32Z",
    "cwd": "/Users/davevoyles/docker-stack",
    "host_pid": 41523,
    "status": "active|idle|ended|crashed",
    "transcript_path": "~/openclaw/data/audit/host_bridge/<id>.log"
  }
}
```

### Files to touch (Phase 3)

- `src/host_bridge.py` (+~250 lines): session registry, stdin/stdout pumps,
  idle timeout sweeper, graceful + forced shutdown paths
- `src/slack_bot.py` (+~200 lines): thread reply handler, four new slash
  commands, session ownership check on every thread message
- `src/host_bridge_persistence.py` (NEW): atomic JSON write for the registry
- `data/host_bridge/.gitignore`: ignore everything; track the directory only
- `tests/test_host_bridge_sessions.py`: register, append, expire, resume

### Edge cases the implementation must handle

1. **Two people replying in the same thread.** Only the original `slack_user`
   may send stdin; other repliers get an ephemeral "this isn't your session"
   message. (No DM-channel hijack.)
2. **Replies arriving while a turn is mid-execution.** Queue, do not interrupt.
   Show a "⏳ queued" reaction on the queued message.
3. **Output that includes a Slack mention pattern.** Sanitize before posting so
   the bot does not accidentally page someone.
4. **`copilot` exits unexpectedly.** Post a final transcript link, mark session
   `crashed`, do not auto-restart.
5. **Container restart mid-session.** Mark sessions `crashed` on startup;
   `/copilot-sessions` shows them as resumable in Phase 3.1.

### Done-when

- Start a session from phone, ask follow-up questions over three turns, get a
  coherent answer that references earlier context.
- `/copilot-cancel` interrupts a long-running tool call without killing the
  session.
- Two parallel sessions from the same user do not interleave output.
- Container restart leaves a clean `sessions.json` (no zombie processes on the
  host — implementation must SIGTERM-on-shutdown).

---

## Phase 4 — Rich UX so Slack feels like a real terminal

**Goal:** close the daily-quality-of-life gaps that make Slack feel like
"texting a server" instead of "using a terminal."

### 4a — Cancel + status buttons on every message

Each output message posts with two buttons:

- **🛑 Cancel** — `SIGINT` the current turn (same as `/copilot-cancel`)
- **📋 Full transcript** — DM the user a snippet upload of the full unredacted
  transcript for this session

### 4b — File attachments as CLI context

When a user uploads a file in a `/copilot` thread:

1. `slack_bot.py` downloads the file to
   `~/openclaw/data/host_bridge/<session_id>/uploads/<filename>`
2. Posts a path message in the thread: `📎 /Users/davevoyles/openclaw/data/host_bridge/<id>/uploads/foo.log`
3. Path is now visible to the `copilot` process via the same volume mount
   already shared between container and host

Images: same path mechanism. `copilot` reads them via `--add-dir`.

### 4c — Interactive prompt detection

Detect when `copilot` stdout matches a prompt pattern (e.g., `[y/N]`, `? Choose`).
When detected:

1. Pause stdout pump for 500ms to let the prompt settle
2. Post a Slack message with the prompt text + dynamic buttons
   (`Yes` / `No` / `Custom`)
3. Button click pipes the chosen string + `\n` to stdin

Pattern library lives in `src/host_bridge_prompts.py` and is unit-testable.

### 4d — Diff preview for `copilot`'s file edits

Easiest path: keep `--allow-all-tools` but post a diff-formatted Slack snippet
**before** the write is applied. Implementation option:

- Detect `Writing N lines to <path>` in stdout
- Run `diff -u <path> <tempfile>` on the host and capture
- Post the diff as a Slack snippet in the thread before resuming

This is the trickiest piece because it requires intercepting `copilot`'s write
boundary. Alternative path (simpler, still useful): post a Slack notification
**after** each write with `git diff -- <path>` so the user can see what changed
and revert if needed.

**Recommendation:** ship the "after" version first (Phase 4d.1), then iterate
toward true before-write previews (Phase 4d.2) only if before-write proves
necessary in practice.

### 4e — Mobile-friendly output

- ANSI escape stripping (`pyte` or a small handwritten state machine)
- Output >12 lines → automatic snippet upload, not message wall
- Spinners/progress bars (`⠋⠙⠹⠸`) collapsed to "(working…)" with periodic
  heartbeat
- Code-fence detection so multi-line output renders in a monospace block

### Done-when

- `/copilot edit my plex compose to add a volume mount` posts a diff preview
  before the file is touched (or immediately after, per the recommended
  staging).
- Phone users can read every reply without horizontal scrolling.
- Drop a 200KB log into a thread, ask "what's failing in this", get an answer.

---

## Phase 5 — Quick-action shortcuts (narrow the surface, increase speed)

**Goal:** the most frequent operations should be one slash command, not a
freeform `copilot` invocation. Each shortcut wraps a vetted prompt in a
predictable way and routes to the Phase 3 session machinery.

### Initial shortcut set

| Command | Translates to |
|---|---|
| `/host status` | `copilot -p "show docker ps, brew services list, top CPU/mem"` (read-only) |
| `/host logs <service> [n]` | `copilot -p "tail -n N logs for <service>; flag anomalies"` |
| `/host restart <service>` | Approval-gated; runs container restart via existing safe-action allowlist, falls through to `copilot` for Plex (native app) |
| `/host disk` | `copilot -p "df -h, du -sh ~/docker-stack/*; flag anomalies"` |
| `/host net` | `copilot -p "ping NAS, traefik health, plex web reachable"` |
| `/host plex-fix` | `copilot --allow-all-tools -p "diagnose and resolve Plex media-not-found issues"` (the user's original use case) |
| `/host git <command>` | Wraps `git -C ~/docker-stack <command>` |

These are syntactic sugar on top of Phase 3; they exist because typing
`/host disk` on a phone is faster than `/copilot show me disk usage and flag
anything red`.

### Files to touch

- `src/slack_bot.py`: `@app.command("/host")` dispatcher with subcommand routing
- `src/host_bridge_shortcuts.py` (NEW, ~100 lines): subcommand → prompt mapping
- `tests/test_host_bridge_shortcuts.py`: subcommand routing, allowlist boundary

### Done-when

- All seven shortcuts dispatched correctly from phone in <5s round trip.
- Unknown subcommand prints help, not error.
- `/host restart plex` calls the right path (native app shortcut) not the
  Docker path.

---

## Phase 6 — Audit, history, and observability

**Goal:** answer the question "what did OpenClaw do last week, on my behalf?"
without grepping Slack.

### 6a — Structured audit log surfacing

The Phase 2 audit log (`~/openclaw/data/audit/host_bridge.jsonl`) becomes
queryable from Slack:

- `/copilot-history` — paginated last 20 invocations: timestamp, prompt
  preview, exit code, duration, transcript link
- `/copilot-history search <query>` — full-text search across past transcripts
- `/copilot-history <session_id>` — DM the full transcript as a snippet

### 6b — Daily digest line in existing `/digest`

Add a new section to the daily digest:

> 🤖 **Copilot CLI usage**: 14 invocations · 2 errors · top topic: "plex media
> path"

### 6c — Metrics export

Append per-invocation metrics to the existing `prometheus_metrics` registry so
Grafana (if present) can chart usage.

### Done-when

- `/copilot-history` returns last 20 invocations with working transcript links.
- The daily digest includes the new CLI usage line.

---

## Phase 7 — Multi-host & scheduled tasks (advanced)

### 7a — NAS bridge

Same machinery as Phase 2, second target:

- `/copilot-nas <prompt>` — SSH to `dave@192.168.1.8` (port 24), run a host
  bridge over the Synology shell. Same approval pattern, separate allowlist.
- The NAS is where Homepage, Hardcoverr, and the reverse proxy live, so most
  "fix the dashboard" requests should land here.

### 7b — Scheduled `copilot` tasks

- `/copilot-schedule daily 9am "summarize overnight incident log"` — registers
  a `croniter` job in `data/host_bridge/schedules.json`
- A background worker dispatches scheduled invocations through the same Phase 2
  pipeline, posts results to the configured channel
- `/copilot-schedule list|remove <id>`

### Done-when

- A scheduled job posts its first result to Slack on the next tick.
- `/copilot-nas` runs as expected against Homepage.

---

## Cross-cutting concerns

### Security (rechecked at each phase)

| Concern | Where addressed |
|---|---|
| Slack identity gate | Inherited from Phase 2; checked on every thread reply in Phase 3 |
| Workspace ID gate | Inherited from Phase 2 |
| Per-user concurrent session cap | Tightened in Phase 3 — default 3 active sessions per user |
| Per-user rate limit | Inherited; expanded to count thread replies too |
| Output redaction | Same regex; applied to streaming, file diffs, history surfacing |
| Audit log integrity | Append-only JSONL; hash-chain extension considered for Phase 6 |
| Kill switch | Authorized-key removal still revokes everything in one line |

### Observability

- All phases write to `data/audit/host_bridge.jsonl` with consistent schema
- Prometheus counters: `copilot_sessions_active`, `copilot_invocations_total`,
  `copilot_errors_total`, `copilot_session_duration_seconds`
- Healthcheck endpoint adds `host_bridge_active_sessions` field

### Testing strategy per phase

| Phase | Primary test surface |
|---|---|
| 3 | `tests/test_host_bridge_sessions.py` — registry, stdin/stdout pumps with `asyncssh` mocks |
| 4 | `tests/test_host_bridge_ux.py` — ANSI stripping, prompt detection, diff formatting |
| 5 | `tests/test_host_bridge_shortcuts.py` — subcommand routing |
| 6 | `tests/test_host_bridge_history.py` — search, pagination |
| 7 | `tests/test_host_bridge_nas.py`, `tests/test_host_bridge_schedule.py` |

All phases must run under the existing `pytest --override-ini="addopts="`
invocation (avoids `pyproject.toml`'s `-n auto --dist loadfile` flake).

---

## Recommended delivery sequence

| Order | Phase | Why this order |
|---|---|---|
| 1 | **Phase 2** | Foundation; user has already answered all questions |
| 2 | **Phase 3** | Biggest single parity win; everything else depends on it |
| 3 | **Phase 5** | Cheap UX wins; can ship before Phase 4 polish is done |
| 4 | **Phase 4** | Rich UX once daily-driver shortcuts prove usage patterns |
| 5 | **Phase 6** | Once usage exists, surfacing it becomes valuable |
| 6 | **Phase 7** | Last because NAS bridge ≠ Mac Mini bridge in trust surface |

Total estimated effort, distributed:

- Phase 2 — M (designed; ~half-day to ship)
- Phase 3 — L → split into 2× M (session registry; pump loop + tests)
- Phase 4 — L → split into 4× S/M (cancel buttons; uploads; prompt detect; ANSI)
- Phase 5 — M
- Phase 6 — M
- Phase 7 — L → split into 2× M (NAS bridge; scheduler)

---

## Open questions — provisional answers (locked 2026-05-21)

User was unavailable when the plan was reviewed, so the orchestrator locked in
reasonable defaults below. Each is **provisional** — the user can override any
answer before Phase 3 implementation begins. Decisions chosen to bias toward
safety and minimal blast radius.

1. **Idle timeout for an active Phase 3 session.**
   **→ Locked: 10 minutes of no activity ends the session.** Matches the
   Phase 2 per-call timeout the user already approved; consistent ceiling.
2. **Max concurrent sessions per user.**
   **→ Locked: 3.** Enough for genuine parallel investigations (one Plex,
   one network, one general). Higher caps invite forgotten zombie sessions.
3. **Non-owner repliers in a `/copilot` thread.**
   **→ Locked: read-only view.** They can see all output but cannot send
   stdin. Only the original Slack user (matched by `user_id`) can drive the
   session. Rationale: this preserves the single-owner trust boundary the
   user established in Phase 2 (allowlist of `U0ATT7XTDGS`), while letting
   teammates or future-you-on-a-shared-channel follow what the agent is
   doing. Non-owner stdin attempts get an ephemeral "this isn't your
   session" message. **Override path:** if the user later wants co-drivers,
   add an `OPENCLAW_HOST_BRIDGE_CO_DRIVERS` env var with additional Slack
   user IDs — same allowlist pattern as Phase 2.
4. **Diff-preview policy for Phase 4d.**
   **→ Locked: ship "after-write notification" first (Phase 4d.1),
   evaluate before-write (4d.2) only if real usage shows it's needed.**
   The user runs `copilot --allow-all-tools` interactively today without
   diff previews; post-write notifications already exceed terminal-equivalent
   transparency. Before-write previews require intercepting `copilot`'s
   write boundary, which is non-trivial and risks breaking CLI behavior.
5. **Phase 5 shortcut list.**
   **→ Locked: ship the seven listed shortcuts.** Defer the suggested
   additions (`/host backup status`, `/host plex-scan`, `/host snapraid
   status`) to a Phase 5.1 follow-up once the first seven prove their
   patterns. Adding shortcuts is a one-line entry in
   `src/host_bridge_shortcuts.py`; cheap to grow incrementally.
6. **Phase 7 schedule destination.**
   **→ Locked: configurable per-schedule, default to user DM.** `/copilot-schedule`
   accepts an optional `--channel <C…>` arg; without it, output goes to a DM
   with the user who created the schedule. Rationale: scheduled output is
   personal by default (matches `cron` mental model), but channel posting
   is one flag away when the user explicitly wants visibility.

**These are reversible defaults**, not architectural commitments. Each lives
in a single env var or config constant; flipping any of them requires no
schema migration.

---

## What this plan deliberately does NOT include

To keep the surface honest, here is what was considered and set aside:

- **Voice → CLI transcription.** Slack supports voice notes; would be neat but
  not on the critical path to terminal parity. Park for v2.
- **Web UI mirror of Slack threads.** OpenClaw already has a dashboard; adding
  a third surface increases auth/permission complexity disproportionately.
- **Slack-side LLM that decides whether to call `copilot`.** Tempting but
  collapses the trust boundary — the user explicitly wants to be the one who
  invokes the CLI. Keep the entry point explicit.
- **Sandboxed sub-user on the host.** User has already said: full identity
  access as `davevoyles`. Not revisiting.
- **Discord parity.** User said in this session: "No need for Discord
  integration. Slack is always our primary focus." Park indefinitely.

---

## Status

- ✅ All six Phase 3 open questions provisionally answered (see "Open questions"
  section above); user can override any default before Phase 3 starts
- ⏸️ Awaiting user approval to begin Phase 2 implementation (High-risk
  side-effect checkpoint must precede the first SSH key generation)
- ⏸️ Awaiting confirmation of the recommended phase order (2 → 3 → 5 → 4 → 6 → 7)

No code touched, no commits made. This plan is the deliverable.
