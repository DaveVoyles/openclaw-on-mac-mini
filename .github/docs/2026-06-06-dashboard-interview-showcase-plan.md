# Plan: Make the OpenClaw Dashboard Interview-Ready (Anthropic / agent roles)

**Status:** PLANNING — awaiting approval to implement
**Author:** Copilot (fleet)
**Date:** 2026-06-06
**Target page:** `https://openclaw.davevoyles.synology.me/` → `templates/dashboard.html`

---

## 1. Goal & Audience

Dave is applying to **Anthropic** (and similar) for **agent-focused roles** and wants to
screen-share / showcase the live OpenClaw dashboard during interviews to highlight his
**work building agentic systems**.

**Success = an interviewer glancing at the page for 30 seconds immediately sees:**
"This person builds production, safety-conscious, eval-driven, multi-model agent
infrastructure" — not "this is a hobbyist Plex/homelab dashboard."

---

## 2. Current State (research findings)

**Strengths (keep & elevate):**
- Visually polished already: WebGL animated background, cohesive dark theme, frosted-glass
  cards, PWA, responsive, theme toggle, global search. (`templates/dashboard.html`, 7952 lines)
- It is a **real, live, 24/7 production system** — huge credibility signal.
- Backed by substantial real agent infrastructure with live data endpoints:
  - `/api/runs` — run history timeline
  - `/api/quality-evals` + `/api/quality-metrics` — **eval scorecards** (115 handler refs)
  - `/api/approvals` — **human-in-the-loop approvals control plane**
  - `/api/hermes/memory` + ontology — **long-term memory / context engineering**
  - `/api/skill-stats` + `/api/hermes/skills` — **composable skills/tool use**
  - `/api/plans`, `/api/tasks`, model-usage — **planning + multi-model orchestration**
- Repo is now privacy-clean (sensitive data purged from history) and stays public.

**What undersells the work today:**
- **Title:** "Dave's OpenClaw on a Mac Mini" — hobbyist framing.
- **Quick-stats strip leads with media/homelab:** Plex, Sonarr/Radarr queue, disk, sabnzbd,
  qbittorrent, NAS, Uptime Kuma. The agent metrics are buried. (lines ~968-994)
- **Nav leads with media:** Chat, Hermes, Wake, NAS, Docker, GitHub, **Plex, Queue, TV,
  Movies, Downloads, Requests**… (lines ~937-957)
- **~85 cards** with no narrative hierarchy — the agent-engineering crown jewels
  (Approvals Control Plane, Quality Eval Scorecards, Run History, Memory Ontology) sit
  *below the fold*, mixed in with Plex/Radarr/Patreon.
- Showing personal media consumption (Plex history, movie/TV watchlists) in an interview
  is both **off-message** and **mildly personal**.

---

## 3. Strategy (REVISED per Dave's feedback 2026-06-06)

**Reorganize the default dashboard to be agent-first — keep everything available.**
Dave's feedback: *don't hide* anything (transparency is fine), prioritize the biggest/most
important things up top, keep all panels available via **collapsible sections**, and diagrams
are welcome. So this is **not** a separate `?showcase` mode — it improves the real dashboard.

Three pillars:
1. **Reframe** the hero to tell the agent-platform story (permanent, but authentic).
2. **Reorder + group** — agent-engineering panels at the top; everything else grouped into
   **collapsible category sections** (e.g. Agent Engineering → System & Ops → Homelab & Media).
   Nothing is deleted or permanently hidden; lower-priority groups start collapsed.
3. **Polish** — agent-first top metrics + a concise architecture **diagram** + credibility blurb.

**Implementation note:** prefer **JS/CSS-driven grouping by card `id`** (cards already have
stable ids like `plex-activity-card`, `hermes-sessions-card`) over hand-moving ~85 DOM blocks
in the 7952-line file. A small script assigns each card to a category + priority, wraps each
category in a collapsible `<details>` section, and orders them. Additive, reversible, lower-risk.

Solo (not fleet): all edits are in one tightly-coupled template — parallel lanes would collide.

---

## 4. Proposed Changes (waves)

### Wave 0 — Data-availability audit (S, required before Wave 1)
Confirm each "hero" panel has **non-empty live data** right now (runs, evals, approvals,
skills, memory, model usage). If any is sparse, either (a) seed representative data or
(b) demote it. Leading with an empty card backfires. *Read-only check.*

### Wave 1 — Showcase Mode hero + framing (M)
- New hero (showcase mode only):
  - **Title:** "OpenClaw — Autonomous Multi-LLM Agent Operations Platform"
  - **Tagline:** one line, e.g. *"A production agent that runs my digital life via Slack/Discord —
    eval-gated, approval-gated, multi-model (Claude · Copilot/Hermes · Gemini)."*
  - **Tech badges:** Claude/Anthropic · human-in-the-loop approvals · quality evals ·
    long-term memory · 5,894 tests · CI/CD · security scanning · 24/7 uptime.
- Keep the "live / online / uptime" badges — real-time is a strength.
- Subtle "Showcase view" indicator + one-click toggle back to full dashboard.

### Wave 2 — Agent-first metric strip + curation (M)
- Replace the media-led quick-stats strip (showcase mode) with **agent metrics**:
  agent runs (24h/total) · tools/skills available · memory entries · eval pass-rate ·
  models orchestrated · approvals processed · avg response time.
- Reorder so the first screen is: **Run History Timeline · Approvals Control Plane ·
  Quality Eval Scorecards · Long-Term Memory (Ontology) · Skills · Model Usage.**
- **Hide in showcase mode:** Plex, Sonarr/Radarr/Lidarr queue, Overseerr, sabnzbd/qbt
  downloads, NAS browse, Patreon cookies, Tailscale, media calendar/watchlists.
  (CSS class–based hide driven by a `body.showcase` flag — no card deletion.)

### Wave 3 — Architecture & credibility blurb (S)
- A collapsible "About this system" panel (top, showcase mode): 3-4 sentence architecture
  story + a small text/SVG diagram: `Slack/Discord → Agent core → {memory, skills, approvals,
  evals} → multi-model router → tools`. Link to repo + README.
- Frame explicitly around Anthropic-relevant themes: **safety/oversight (approvals),
  evals, context/memory engineering, model-agnostic orchestration, observability.**

### Wave 4 (optional) — README + screenshots for the repo (M)
- Add 2-3 showcase-mode screenshots / a short GIF to `README.md`, an architecture diagram,
  and an agent-focused project summary so the **GitHub repo** also lands well when shared as
  a link. (Repo is the other thing interviewers open.)

---

## 5. Risk, Validation, Doc Sync

- **Risk:** Low-Medium. Additive CSS/JS + hero variant in one template; no backend or auth
  changes; no card removal. Reversible via the toggle.
- **Validation:**
  - Showcase mode renders with no empty hero cards (Wave 0 gate).
  - Full/default dashboard unchanged when `?showcase` is absent.
  - Mobile + desktop layouts intact; theme toggle still works.
  - No personal/media cards visible in showcase mode (privacy check).
  - Existing dashboard tests still pass (`pytest tests/ -q`).
- **Doc sync:** update README (Wave 4) and note Showcase Mode in any dashboard docs;
  `.env.example` unaffected (no new vars unless we add a default-mode flag).

---

## 6. Open Questions (for Dave)

1. **Scope:** dashboard-only (Waves 1-3), or also README/screenshots (Wave 4)?
2. **Activation:** `?showcase=1` URL param (share a clean link) — good enough, or also a
   persistent toggle button in the header?
3. **Hide vs. relabel:** fully hide media/homelab cards in showcase mode (recommended), or
   keep them but move them to the very bottom under a collapsed "Homelab" section?
4. **Tagline wording:** OK to foreground "Claude/Anthropic" in the model lineup?

---

## 7. Recommendation

Implement **Waves 0-3** (dashboard Showcase Mode) first — highest interview impact, low risk,
non-destructive. Add **Wave 4** (README/screenshots) next so the repo link also impresses.
Recommended activation: `?showcase=1` **plus** a small header toggle.

---

## Implementation status — Wave 1 & 2 DONE (2026-06-06)

**Wave 1 — Hero reframe + Architecture diagram (live, validated):**
- `<h1>` now reads **"OpenClaw — Autonomous Multi-LLM Agent Platform"** with a one-line agent-platform tagline (memory, 90 skills, approvals, evals, multi-model; 24/7 on a Mac Mini; Slack/Discord native).
- Added a row of credibility badges in the hero: Hermes+Copilot, multi-model router, human-in-the-loop, quality evals, persistent memory, "5,890+ tests · CI/CD".
- Added a collapsible **🏗 Architecture & how it works** card (`#architecture-card`) near the top: prose + a 5-stage flow diagram (Interfaces → Agent Core → Model Router → Guarded Action → Tools), themed to JHU palette, responsive (arrows rotate on mobile).

**Wave 2 — Featured Agent Engineering section (live, additive + reversible):**
- Added stable ids to 6 previously id-less crown-jewel agent cards: `approvals-card`, `run-history-card`, `model-usage-card`, `ontology-card`, `active-plans-card`, `task-status-card`.
- Added a small client-side script (before `</body>`) that builds a collapsible **🤖 Agent Engineering** `<details>` section right under the dashboard chat and MOVES 12 agent cards into it (hermes status/memory/skills/sessions, quality evals, approvals, run history, model usage, ontology, active plans, task status, memory browser). Open by default; nothing else is hidden or removed — the rest of the dashboard remains below, intact.
- Enhanced `jumpTo()` to auto-open a card's ancestor `<details>` before scrolling (so search/jump still reaches collapsed sections).

**How it's served / validated:**
- `templates/dashboard.html` is cached at import (`helpers.py` `DASHBOARD_HTML = read_text()`), so changes require a container restart: `docker compose up -d --force-recreate --no-deps openclaw`. Done; container healthy.
- Validated: authenticated fetch on :8765 serves new hero/architecture/ids/style/script; promote script syntax-checked; all 12 featured ids unique; no test asserts on changed markup.
- **Visual confirmation pending from user** on the live page.

**Not done (deferred, optional):** collapsing Homelab/Media into its own collapsed-by-default section; agent-first quick-stats strip with live metrics; README screenshots. Awaiting user feedback before further reorg.

---

## Status update (autonomous session, 2026-06-06)

### ✅ Completed & verified live (CI green)
- **A1** Task Status overflow → `overflow-x:auto` (scrolls inside card).
- **A2** Memory Browser tags → stack vertically (`.memory-tags` flex column).
- **A3** Recent Activity → normalized `host_bridge` shell schema (action/detail/result
  + epoch→ISO), then **filtered** automated per-minute dashboard health polls
  (`slack_user_id="dashboard"`) for a high-signal agent feed.
- **Bonus (user-reported live):** defined the missing `apiFetch` helper — fixed
  Downloads (SABnzbd/qBittorrent), Uptime, Audit Log, and NAS Status cards in one shot.

Commits: 4702979, 367b3c9, 85ad611 (Wave A + ruff), 571d4e5 (apiFetch), 504cef1 (activity filter). All pushed; CI passing.

### ⏸ Deferred — Wave B (tiered grouping) & Wave C (agent stat strip)
**Reason:** Both are client-built DOM reorganizations that the plan itself flags as
**not headlessly verifiable** — they need a human eyeball on the live page. Investigation
showed the dashboard DOM is **irregular**: some cards are standalone block elements with
inline `margin-bottom` (e.g. `interfaces-card`, `wol-card`, `tailscale-card`) while others
are children of the single `.grid` (line 1688). Reorganizing ~80 heterogeneous cards
without visual confirmation risks breaking a currently-working, interview-ready page.

**Decision:** Hold Wave B/C until the user can review the live page after Wave A. The
proven `promoteAgentCards` pattern (line 8023) is ready to extend once verification is possible.
