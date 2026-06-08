# Handoff — Public release, PII scrub & git-history rewrite (2026-06-07)

**Status:** ✅ Complete. The repo is **PUBLIC** as of 2026-06-07.
**Audience:** the next agent picking up this work.
**Read first:** [README.md](README.md) (the "⚠️ This repository is PUBLIC" section is the standing policy).

---

## TL;DR

Over 2026-06-06 → 06-07 the repo was prepared for and flipped to **public**:

1. Security/PII audit — no secrets ever committed; `.env` never tracked.
2. Scrubbed the owner's personal email + a (now-expired) Slack invite from the working tree.
3. Added an automated **privacy scanner** that blocks private data on every push/PR.
4. **Rewrote all git history** (`git filter-repo`) to remove the email + invite from every commit, then force-pushed all branches.
5. Closed 8 dependabot PRs and deleted 7 deprecated `feature/discord-*` branches → remote now has a single clean `main`.
6. Flipped repo visibility to **public**.

Everything is pushed; remote `main` is the source of truth. No open work is required — see "Open items / next steps" for optional follow-ups.

---

## What changed (and why)

### 1. PII scrub in the working tree
- Personal email `you@example.com` placeholder now used everywhere in `templates/` (was a real Gmail).
- Live Slack invite links replaced with "ask the owner for a current invite" copy in `templates/onboarding.html`, `templates/parents-guide.html`.
- Added `LICENSE` (MIT) for the public showcase.

### 2. Automated privacy guard (runs on every push/PR)
- `scripts/scan_private_data.py` — scans tracked files for the owner email, Slack invites, real secret tokens (Slack/GitHub/OpenAI/Anthropic/AWS/Google), private-key blocks, and tracked credential files. Ignores placeholders, test fixtures, and acceptable homelab data (LAN IPs, personal paths). Exit 1 on findings.
- `tests/test_scan_private_data.py` — 27 tests.
- `.github/workflows/privacy-scan.yml` — **no path filter**; runs on every push to `main` + PR.
- `make scan-private` target + a local pre-commit hook in `.pre-commit-config.yaml`.

> ⚠️ **Important — do NOT "simplify" this:** the scanner assembles the owner email from parts at runtime
> so the literal address is **never stored verbatim** in this public repo (see `_OWNER_EMAIL` in
> `scripts/scan_private_data.py`). Collapsing it back into a single string literal would re-expose the
> email. The test file uses the same assembled value.

### 3. Git history rewrite (`git filter-repo`)
- Replaced in **blobs and commit messages** across all 1794 commits:
  - the owner's real Gmail → `you@example.com`
  - the live Slack workspace invite URL (`join.slack.com/t/<workspace>/shared_invite/zt-…`) → placeholder
- Force-pushed scrubbed history to all branches.
- **Verified:** a fresh clone shows 0 occurrences of the email/invite across all branch history and commit messages.

### 4. Branch / PR cleanup
- Closed all 8 dependabot PRs (removed `refs/pull/*` pins so GitHub can GC orphaned old commits). Dependabot recreates them against clean history.
- Deleted 7 deprecated `feature/discord-*` branches (Slack replaced Discord; Discord is deprecated).
- Remote now has a **single `main` branch**.

---

## Key facts the next agent needs

- **Repo is PUBLIC** — every commit is world-readable. Follow the policy in [README.md](README.md). Run `make scan-private` before any commit.
- **Rollback bundle (pre-scrub full backup):**
  `~/.copilot/session-state/192d0f60-ee2e-4224-8df7-43e1da2d841d/files/openclaw-prescrub-20260607-112627.bundle`
  Old `main` SHA before the rewrite was `0390bb8`. Restore any branch via `git bundle`.
- **History rewrite means all commit SHAs changed.** Any external reference to a pre-`9c1e5e8` SHA is stale.
- **Residual (GitHub platform behavior):** orphaned pre-scrub commits can remain reachable by *exact 40-char SHA* until GitHub garbage-collects them. PR pins were removed (all PRs closed), so GitHub will GC on its own schedule. Those SHAs were never public (repo was private until the flip) and aren't guessable — low risk. For absolute certainty, the only guaranteed purge is delete-and-recreate the repo or ask GitHub Support to run `gc`.

## Repo conventions reaffirmed here
- **`.github/docs/README.md` is the durable, repo-local policy home** (read first; not clobbered by the shared upstream refresh). `.github/copilot-instructions.md`, `autonomous-fleet-agent.md`, `copilot-contract.json` are SHARED/upstream and get overwritten on refresh — don't put repo-specific policy there.
- **`history.md`** (repo root) — append a one-line dated entry per completed task.
- **CI runs on a self-hosted runner** (`mac-mini-m4`, label `[self-hosted, macOS, ARM64, openclaw]`); it's shared and can be busy, so runs may queue.
- **`templates/**` is path-filtered OUT of CI/deploy** — template-only commits don't trigger a deploy, and help pages are read at import time, so a container restart is needed to see template changes: `docker compose up -d --force-recreate --no-deps openclaw`.
- Git push env: `export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=all`.

## Validation commands
```bash
make scan-private                                   # privacy scan (exit 1 on findings)
python3 -m pytest tests/test_scan_private_data.py -q # 27 scanner tests
python3 -m pytest tests/ -q                          # full suite (~5933 pass, 2 skip)
make validate-env                                    # .env vs .env.example
ruff format src/ tests/ --check                      # CI format scope
```

---

## Open items / next steps (optional)
- **Nothing blocking.** The release is done and verified.
- CI test-suite runs were queued behind the busy self-hosted runner at handoff time; the scrub-critical Privacy Scan passed and code changes were validated locally. Confirm the latest `main` CI is green: `gh run list --branch main --limit 5`.
- If you want zero history residual on GitHub (beyond what GC handles): delete-and-recreate the repo, or contact GitHub Support to expedite `gc`.
- Dependabot will reopen dependency-update PRs against the cleaned history on its next run — review/merge as normal.

---

## Reference: commits from this effort
- `cd5325d`, `bb9b54b`, `d6e3874` — PII scrub + LICENSE
- `f46b93b` — public-repo policy docs
- `5b5bcda` — privacy scanner feature
- `9eb9f2f` — refactor email to assembled-from-parts (pre-rewrite)
- history rewrite via `git filter-repo` → new root/HEAD lineage (HEAD after rewrite: `9c1e5e8`)
- `ade4ab0` — log the scrub; `30e79082` — delete discord branches

> Note: the SHAs before the rewrite (e.g. `0390bb8` and earlier) only exist in the rollback bundle now.
