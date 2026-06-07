# Completion Rules — OpenClaw

> Load this file for **every task**. These rules apply at the end of any unit of work before declaring it done.

Two mandatory checks must pass before a task is complete:

1. [CI / Remote Workflows](#1-ci--remote-workflows)
2. [User-Facing Surface Updates](#2-user-facing-surface-updates)

---

## 1. CI / Remote Workflows

**Rule:** After every push, run a quick check to confirm CI isn't broken. You do **not** need to block and wait for CI to complete — push, note the run ID, do a single status check after ~30 seconds, and move on.

"Push succeeded" is not the same as "CI passed" — but long polling loops that block user interaction are also not required.

### Steps

```bash
# 1. Push your changes
git push origin main

# 2. Wait ~30s, then do a single status check
sleep 30 && gh run list --limit 3 --json status,conclusion,name,databaseId

# 3. If the run is still in_progress, note the run ID and move on.
#    Only investigate immediately if conclusion is "failure".
```

### Required before done

- [ ] `ci.yml` — verify it is not in `failure` state after your push
- [ ] If CI shows `failure`: read the log, fix the root cause, push again, re-check
- [ ] If CI is still `in_progress` or `queued`: note the run ID and proceed — do not wait

### Acceptable states at task completion

- `in_progress` or `queued` → ✅ acceptable — CI is running, not broken
- `success` → ✅ ideal
- `cancelled` (by a subsequent push) → ✅ acceptable — superseded run
- `failure` → ❌ must fix before declaring done

### Acceptable non-failures

- `pages.yml` is permanently `continue-on-error: true` on this repo (GitHub Pages plan limitation). A graceful skip/pass is expected — **not** a true failure.
- Workflows triggered on a different branch or by a scheduled event are out of scope unless the task changed them.

### If a workflow fails after your push

1. Read the failure log with `gh run view <RUN_ID> --log-failed`.
2. Fix the root cause (lint error, broken test, bad config).
3. Push the fix.
4. Do one more status check — confirm it is no longer `failure`.

Do **not** declare the task complete while any workflow shows `failure`.

---

## 2. User-Facing Surface Updates

**Rule:** When shipping a feature or change that users interact with, assess whether the embedded guide and/or dashboard need updating — and update them if yes.

### The two surfaces

| Surface | File | Live URL |
|---------|------|----------|
| User guide | `templates/guide.html` | `https://openclaw.davevoyles.synology.me/guide` |
| Dashboard | `templates/dashboard.html` | `https://openclaw.davevoyles.synology.me` |

### When to update

Update **`templates/guide.html`** when:
- A new slash command is available (e.g. `/clear`, `/files recent`)
- A new file action or button appears (e.g. 📊 Chart, 🌍 Translate, 🔀 Compare)
- A workflow changes for end users (e.g. how to sync files, how to use batch mode)
- A new onboarding or notification behaviour is added

Update **`templates/dashboard.html`** when:
- A new metric, stat, or status indicator is available
- A new admin command or health check is shipped (e.g. `/status`, `/metrics`)
- The navigation or feature set visible on the dashboard changes

### When NOT to update

- Internal refactors, test changes, linting fixes, or CI/workflow changes with no user-visible effect
- Backend implementation details with no change to user-visible behaviour

### How to update

1. Open the relevant `templates/*.html` file.
2. Locate the section that describes the affected feature (search for an existing heading or command name).
3. Add or update the description, example, or table row.
4. Keep the same tone and style as surrounding content.
5. After editing, confirm the file is valid HTML (no broken tags) and commit with the feature commit or as a follow-up.

### Deploy the HTML changes to the live server

HTML template changes are served by the running container. After committing:

```bash
make ship-server   # pull latest + restart container so new templates are served
make verify-deploy # confirm git_sha matches HEAD
```

Visit the live URL to confirm the change is visible before declaring done.

### If the dashboard content is dynamically generated

Some dashboard sections are rendered from live data (metrics, file lists, health). In that case:
- Confirm the new data source or metric is being written correctly (check `logs/slack_metrics.jsonl` or equivalent)
- Confirm the existing dashboard template will pick it up automatically
- If the template needs a new widget or section, add it

---

## Quick reference checklist

Copy this at the bottom of any task summary when done:

```
## ✅ Completion checklist
- [ ] **Repo is PUBLIC** — no private data introduced (real emails, live Slack/Discord invites, secrets, PII). Run the pre-commit scan in [README.md](README.md) (see "This repository is PUBLIC")
- [ ] Push succeeded; CI is not in `failure` state (in_progress/queued is fine)
- [ ] `guide.html` updated if user-facing feature shipped
- [ ] `dashboard.html` updated if admin/metric feature shipped
- [ ] `make ship-server` run if templates changed
- [ ] Live URL verified
```
