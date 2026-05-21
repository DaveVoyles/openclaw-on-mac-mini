# Slack → OpenClaw → Copilot CLI bridge — Design Plan
<!-- Created: 2026-05-21 -->
<!-- Status: AWAITING USER APPROVAL — no implementation has started -->

## User request

> "From within Slack, I want to tell the OpenClaw agent to run the Copilot CLI in a
> terminal on the Mac Mini, so I can work on the Mac Mini from Slack. Example: a
> Docker app on the Mac Mini has an issue (e.g., Plex failing to locate files on
> disc), and I say in Slack: 'Run our copilot command to utilize the CLI to
> diagnose and correct any issues with our Plex instance.'"

Slack is the user's primary interface to OpenClaw. The capability should feel
like opening a terminal on the Mac Mini, but through Slack.

## Why this is non-trivial (architectural reality)

| Constraint | Source | Implication |
|---|---|---|
| OpenClaw container is `read_only: true`, `cap_drop: ALL`, `no-new-privileges:true` | `~/docker-stack/openclaw/docker-compose.yml` | Can't run host binaries; can't `docker exec` (socket is RO) |
| `copilot` CLI lives on the host at `/opt/homebrew/bin/copilot` v1.0.51 | `which copilot` | Not accessible from inside the container by any direct path |
| Plex runs as a **native macOS app**, not Docker | `docs/AGENT-GUIDE.md` | `restart_container("plex")` does nothing for Plex; the user's stated use case fundamentally needs host shell |
| Slack bot already mounts SSH keys for NAS at `/home/openclaw/.ssh:ro` | docker-compose volumes | SSH-back-to-host is the cleanest bridge mechanism, with an additional key |
| Incident Copilot is exposed to Discord (`/incident start|create|...`) but NOT to Slack | `src/cogs/incident_cog.py` (722 lines) vs `src/slack_bot.py` (5,082 lines, no `/incident`) | Confirmed real gap; Slack only has `/chat`, `/research`, `/digest`, `/health`, etc. |

## Risk classification

**Phase 1 (Slack `/incident` exposure) — Medium risk.** All code is in-process,
uses existing safe-action allowlist (`SAFE_RESTART_TARGETS`), mirrors Discord
behavior already in production.

**Phase 2 (host `copilot` CLI bridge) — High risk.** Allows Slack-originated
commands to execute arbitrary code on the Mac Mini host. Requires:

- a user-facing checkpoint before any side-effecting implementation step
- a documented rollback plan
- explicit user approval (per fleet rules and the Approval Matrix in
  `copilot-instructions.md`)

## Proposed phased delivery

### Phase 1 — Expose Incident Copilot to Slack (Medium risk, ~M lane)

**Goal:** make the existing diagnose-and-fix workflow available from Slack today,
covering every containerized service (Sonarr, Radarr, Prowlarr, qBittorrent,
SABnzbd, Overseerr, Tautulli — see `DEFAULT_SERVICE_CANDIDATES` in
`src/incident_copilot.py:27-37`).

**Surface to add:**

- `/incident <description>` — start an incident session. Calls
  `build_incident_context()` + `generate_incident_report()` and posts the result
  as a Slack message with action buttons.
- Slack `block_actions` handler for each suggested action button → calls
  `execute_incident_action()` (the existing safe-action gate).
- Slack approval mirrors the Discord `_execute_approved` pattern in
  `src/cogs/incident_cog.py:115`.

**Files to touch:**

| File | Change |
|---|---|
| `src/slack_bot.py` | Add `@app.command("/incident")` handler + `@app.action("incident_action_*")` handlers. Mirror the Discord button approval flow. |
| `src/incident_copilot.py` | No change — public API (`build_incident_context`, `generate_incident_report`, `execute_incident_action`) is already Slack-agnostic. |
| `src/slack_bot.py` | Reuse the existing `_make_handler` action-handler registration pattern (already used for file actions, retries, clarifications). |
| `tests/test_slack_bot.py` | Add tests mirroring `tests/test_incident_copilot.py`. |
| Slack app manifest | Add `/incident` to the slash commands list; redeploy via `scripts/update_slack_manifest.py`. |
| `docs/COMMANDS.md` (generated) | Regenerate via existing tooling. |

**What this does NOT solve:** Plex (native app), filesystem inspection, config
file edits, anything outside the safe-restart allowlist. Phase 2 covers those.

**Validation:**

- Reproduce a containerized incident (e.g., stop Sonarr; run `/incident Sonarr is
  down` in Slack; confirm the report appears with a suggested restart action;
  click; confirm Sonarr restarts and post-restart status is logged).
- Run `pytest tests/test_slack_bot.py tests/test_incident_copilot.py` with
  `--override-ini="addopts="` (pyproject forces `-n auto`).

**Estimated effort:** M (a few hours including tests and manifest update).

### Phase 2 — Host `copilot` CLI bridge (High risk, requires checkpoint)

**Goal:** from Slack, invoke the `copilot` CLI on the Mac Mini host **as the
user (`davevoyles`)**, with the user's own `~/.copilot/` auth and session state
— effectively a remote terminal session as the owner, gated by Slack identity.

> **User intent (2026-05-21):** "I want the tool to be able to access copilot
> CLI exactly like I am now, on this machine." → Run as `davevoyles`, full
> shell, full tool access, no sandbox sub-user. Trust boundary = Slack identity.

#### Architecture: SSH into the host as `davevoyles`

```
Slack user (owner)
    │  /copilot diagnose Plex playback errors
    ▼
src/slack_bot.py
    │  (new handler: routes /copilot; checks Slack user is in allowlist)
    ▼
src/host_bridge.py        (NEW — ~200 lines)
    │  asyncssh.connect(
    │      host="host.docker.internal",
    │      username="davevoyles",
    │      client_keys=["/home/openclaw/.ssh/host_bridge_ed25519"],
    │      known_hosts=...,
    │  )
    │  proc = await conn.create_process(
    │      f"zsh -lc {shlex.quote(invocation)}",
    │      term_type="xterm-256color",  # copilot needs a TTY for some features
    │  )
    │  stream stdout → Slack thread; capture full log to /audit/host_bridge/
    ▼
Host (davevoyles@Mac Mini)
    │  /opt/homebrew/bin/copilot -p "<prompt>" [--allow-all-tools] [--add-dir ...]
    │  Uses ~/.copilot/{auth,sessions,history} — same state as interactive use
    │  Working dir: $HOME by default; configurable per-invocation
    ▼
stdout/stderr stream back over SSH → Slack thread (chunked)
Full transcript persisted to ~/openclaw/data/audit/host_bridge/<id>.log
```

#### Why SSH and not docker exec / docker run / REST daemon

- OpenClaw container's docker socket is mounted **read-only** — cannot spawn
  sibling containers.
- `copilot` is a Homebrew install at `/opt/homebrew/bin/copilot`; it must run on
  the host as a real user with access to `~/.copilot/`.
- SSH gives us: standard transport, real TTY support (copilot uses one for some
  interactive prompts), `sshd` audit logs, key-based revocation as the kill
  switch, and OrbStack already exposes `host.docker.internal`.
- A REST daemon was considered and rejected: more code to write, more attack
  surface, no real benefit over SSH for a single-user system.

#### Security design (trust = Slack identity)

Because the user explicitly wants identity-equivalent access, the protection
layer is **not** a sandboxed sub-user. It's a chain of identity checks and audit
gates before the SSH call is made.

| Control | Mechanism |
|---|---|
| Authenticated host user | `davevoyles@host.docker.internal` — same account the user logs in as. New ed25519 key dedicated to OpenClaw → host bridge so it can be revoked independently of personal keys. |
| Slack identity gate | `slack_bot.py` checks the incoming Slack user ID against `OPENCLAW_HOST_BRIDGE_ALLOWED_USERS` (env var, comma-separated). Hard default: empty → feature disabled until the env var is set. |
| Slack workspace gate | Verify `team_id` matches the configured workspace; reject cross-workspace requests. |
| Mode gating | `/copilot diagnose` runs with `--deny-tool write,shell` (read-only inspection). `/copilot fix` requires a Slack button approval click before re-invoking with full tools. |
| Trusted mode (optional) | Per open question #2: if the user opts in, `/copilot fix` may run without per-call approval — still audited, still revocable. |
| Audit log | Every invocation appended to `~/openclaw/data/audit/host_bridge.jsonl`: Slack user, channel, prompt, mode, approval state, exit code, duration, stdout/stderr SHA-256, full transcript path. |
| Full transcript capture | `~/openclaw/data/audit/host_bridge/<uuid>.log` retains the complete CLI output for forensic review (gitignored). |
| Output sanitization (Slack display only) | Strip env-var-style secrets from chunks posted to Slack using the same regex as `.git/hooks/pre-commit`. Full unredacted output stays in the transcript log on the host. |
| Per-user rate limit | 1 concurrent session per Slack user; 20/hour cap (configurable). |
| Per-call timeout | Default 10 min; configurable via `OPENCLAW_HOST_BRIDGE_TIMEOUT_S`. On timeout, send `SIGTERM` then `SIGKILL` to the remote `copilot` PID. |
| Kill switch (Slack) | `/copilot-cancel` cancels the user's in-flight session. |
| Kill switch (operator) | `~/.ssh/authorized_keys` entry for the bridge key can be removed in one line; OpenClaw fails closed. |
| First-use confirmation | On the very first `/copilot fix` invocation per Slack user, require a one-time "I understand this runs as davevoyles with full access" confirmation button. Stored in `data/host_bridge_consent.json`. |

#### Two Slack commands

- `/copilot diagnose <description>` — read-only mode (`--deny-tool write,shell`).
  Inspects the host (file listings, container status, network checks, log
  reading). Returns CLI output streamed to the Slack thread.
- `/copilot fix <description>` — full-tool mode. Default path: posts the
  diagnose-mode result + an **"Approve fix"** button. Click re-invokes with
  full tools. Trusted-mode opt-in (per user) skips the button.
- `/copilot-cancel` — terminates the user's current session.

#### Files to add/change

| File | Change |
|---|---|
| `src/host_bridge.py` | NEW — `asyncssh`-based connection mgmt, streaming, timeout, audit log writer, output sanitization for Slack chunks |
| `src/slack_bot.py` | Add `/copilot`, `/copilot-cancel` slash commands + approval action handlers; identity gate; first-use consent flow |
| `~/docker-stack/openclaw/docker-compose.yml` | Add env vars: `OPENCLAW_HOST_BRIDGE_ENABLED`, `OPENCLAW_HOST_BRIDGE_HOST=host.docker.internal`, `OPENCLAW_HOST_BRIDGE_USER=davevoyles`, `OPENCLAW_HOST_BRIDGE_KEY=/home/openclaw/.ssh/host_bridge_ed25519`, `OPENCLAW_HOST_BRIDGE_ALLOWED_USERS`, `OPENCLAW_HOST_BRIDGE_WORKSPACE_ID`, `OPENCLAW_HOST_BRIDGE_TIMEOUT_S` |
| `~/openclaw/data/ssh/host_bridge_ed25519{,.pub}` | NEW — generated keypair (gitignored; permissions 0600/0644) |
| Host: `~/.ssh/authorized_keys` | Append the pubkey with a `# openclaw-host-bridge` comment for easy revocation — **no `command=` restriction** (user wants full access) |
| `pyproject.toml` / requirements | Add `asyncssh` if not already present |
| `config/tools.yaml` | Add `host_copilot_diagnose` and `host_copilot_fix` tool declarations so the LLM can call them when natural-language requests imply host work |
| `skills/__init__.py` | Register the two new skills in the `SKILLS` dict |
| `docs/AGENT-EXTENSION-GUIDE.md` | New section: "Adding a host-side tool" |
| `tests/test_host_bridge.py` | NEW — mock SSH transport; verify identity gate, timeout, sanitization, rate limit, audit log shape |

#### Why this is High risk even with identity-equivalent intent

The user has explicitly accepted that the bridge runs as `davevoyles` with full
tool access. The remaining risks the implementation must still mitigate:

1. **Prompt injection.** A malicious or compromised Slack message could trick
   `copilot` into destructive shell actions. Mitigation: read-only `/copilot diagnose`
   as the default; approval gate on `/copilot fix`; audit log for every call.
2. **Slack account compromise.** If the user's Slack account is taken over, the
   attacker gets terminal access to the Mac Mini. Mitigation: workspace ID gate,
   per-user allowlist, first-use consent, easy key revocation, no auto-execute
   on unattended `/copilot fix` unless trusted mode is opted in.
3. **Output leakage.** CLI output may contain secrets. Mitigation: Slack-side
   redaction with the existing secret regex; full unredacted log stays on host.
4. **Long-running runaway commands.** Mitigation: per-call timeout + Slack
   `/copilot-cancel` + operator kill switch via authorized_keys.

#### Validation (Phase 2)

| Check | Evidence |
|---|---|
| Happy path | `/copilot diagnose ls /Users/davevoyles/docker-stack/openclaw` returns the directory listing in Slack |
| Boundary | Prompt > 4000 chars rejected; timeout > 5 min killed |
| Negative/error | Slack user not in allowlist → polite refusal, audit log entry |
| Concurrency/idempotency | Second concurrent call from same user → "already running" message; first call still completes cleanly |
| Specialist (security) | `code-review` agent reviews `host_bridge.py` + wrapper script before commit; manual review of `authorized_keys` restriction |
| Real Plex case | `/copilot fix Plex can't see files on /Users/davevoyles/mnt/Misc` → diagnose shows mount status (`mount`, `ls`, Plex log tail); approval button lets `copilot` choose the fix (remount SMB share, restart Plex via `osascript`, refresh library, etc.) |
| Identity-equivalence | Run `/copilot diagnose whoami && echo $HOME && copilot --version` → output must show `davevoyles`, `/Users/davevoyles`, and the same `copilot` version the user sees in their own terminal |
| Session state | Run `/copilot diagnose ls ~/.copilot/` → must show the same auth and session files the user sees interactively |

#### Rollback plan

- **Safe state:** Phase 2 not started; Slack `/copilot` command does not exist.
- **Rollback steps (in order):**
  1. **Immediate kill (host side, one command):** delete the line tagged
     `# openclaw-host-bridge` from `~/.ssh/authorized_keys`. Bridge fails closed
     within the next invocation.
  2. Set `OPENCLAW_HOST_BRIDGE_ENABLED=false` in compose; `docker compose up -d openclaw`. Disables the Slack handlers.
  3. Remove `/copilot` and `/copilot-cancel` from Slack manifest via `scripts/update_slack_manifest.py`.
  4. Revert commit on `~/openclaw` repo; redeploy container.
  5. Remove `OPENCLAW_HOST_BRIDGE_*` env vars from compose for full cleanup.
- **Trigger:** any of — secret leak in Slack output, runaway resource usage on host, unauthorized user invocation, prompt-injection causing destructive action.
- **Verified restorable:** must be drilled before Phase 2 ships.

#### Open questions for the user

1. **Which Slack user(s) should have access initially?** Default proposal: only the workspace owner's Slack user ID (set via `OPENCLAW_HOST_BRIDGE_ALLOWED_USERS`). Feature is disabled until this env var is non-empty.
2. **Should `/copilot fix` require button approval every time, or allow a "trusted mode" for the owner?** Default proposal: button approval on first use per session; opt-in trusted mode via `/copilot-trust` for the owner (still audited, still revocable).
3. **Default working directory for `copilot` invocations?** Default proposal: `$HOME` (`/Users/davevoyles`) — matches "exactly like I am now". User can override per call: `/copilot diagnose in:~/docker-stack ...`.
4. **Output streaming cadence?** Default proposal: 2-second batched chunks, max 50 chunks per Slack thread; full unredacted transcript always available at `~/openclaw/data/audit/host_bridge/<id>.log`.
5. **Should we also expose this via Discord, or Slack-only?** Default proposal: Slack-only initially since Slack is the user's primary interface. Discord can mirror later using the same `host_bridge.py` core.
6. **Tool flags in fix mode.** Default proposal: pass `--allow-all-tools` (matches user's interactive experience). Alternative: explicit allowlist like `--allow-tool 'shell(docker *)' --allow-tool 'shell(osascript *)'`. The default matches stated intent; the alternative is safer.
7. **Working directory for the SSH session.** `ssh ... cd "$dir" && /opt/homebrew/bin/copilot ...`. Default proposal: `$HOME`, configurable per call.

## Decision tree (what runs when)

```
User says in Slack: "diagnose Sonarr 502 errors"
    → /incident handler picks it up (Phase 1)
    → in-process: container logs + LLM + safe restart button
    → fast, no host shell needed

User says in Slack: "Plex can't see files on /Users/davevoyles/mnt/Misc"
    → /copilot diagnose handler picks it up (Phase 2)
    → SSH to host, run `copilot -p "..." --deny-tool write,shell`
    → returns mount status, file paths, suggested actions
    → user clicks "Approve fix" → second SSH invocation with --allow-tool shell

User says in Slack: "edit my docker-compose.yml for grafana to add port 3000"
    → /copilot fix handler picks it up (Phase 2)
    → diagnose mode shows the proposed change
    → approval button executes it
```

## Phase 1 is a hard prerequisite for Phase 2 because:

1. It validates the Slack action-button approval pattern in production.
2. It establishes the per-Slack-user permission gating code path.
3. It exercises the audit log path that Phase 2 reuses.
4. It satisfies the user's stated use case for every containerized service today
   while the riskier work goes through review.

## Recommended next actions (for user approval)

1. ✅ **Approve Phase 1** — Slack `/incident`. I can implement this autonomously as
   a Medium-risk fleet task (research + implement + code-review + tests) since it
   reuses existing safe-action gating and adds no new attack surface.
2. ⏸ **Phase 2 awaits user checkpoint** — confirm:
   - the SSH-back-to-host architecture is acceptable (vs an alternative like a
     small REST daemon on the host listening on `127.0.0.1`)
   - the answers to the five open questions above
   - explicit approval to start (this is High risk and requires the checkpoint)

## What I did NOT do (and why)

- **I did not build any of this.** Phase 2 is High risk and requires a user
  checkpoint before the first side-effecting step per fleet rules. Phase 1 is
  Medium risk but I'm pausing for explicit go-ahead since the user is offline
  and this is a meaningful new surface area, not a docs refresh.
- **I did not modify the Slack app manifest** or any production config.
- **I did not generate SSH keys, create a `bridge` user, or modify
  `authorized_keys` on the host.** All host-side changes need explicit approval.

---

**Authoring agent:** Copilot CLI (Claude Opus 4.7) — solo planning, no fleet
launched. Solo is appropriate because the work is design-only and benefits from
single-author coherence; a fleet would create coordination overhead with no
parallelism win at this stage. Implementation phases will use a fleet.
