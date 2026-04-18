# Completion Rules — OpenClaw

> Load this file for **every task**. These rules apply at the end of any unit of work before declaring it done.

Two mandatory checks must pass before a task is complete:

1. [CI / Remote Workflows](#1-ci--remote-workflows)
2. [User-Facing Surface Updates](#2-user-facing-surface-updates)

---

## 1. CI / Remote Workflows

**Rule:** A task is not complete until every GitHub Actions workflow that touches the affected area is green.

"Push succeeded" is not the same as "CI passed." Always verify.

### Steps

```bash
# 1. Check the most recent run for each workflow
gh run list --limit 10 --json status,conclusion,name,headBranch

# 2. For any run that is not "success", get details
gh run view <RUN_ID> --log-failed

# 3. Fix the root cause, push, and re-check until all runs show "success"
```

### Required before done

- [ ] `ci.yml` — lint (ruff) + tests — **must be green**
- [ ] All other workflows triggered by the branch/push — **must be green or intentionally skipped**
- [ ] No workflow stuck in "queued" or "in_progress" for longer than its expected runtime

### Acceptable non-failures

- `pages.yml` is permanently `continue-on-error: true` on this repo (GitHub Pages plan limitation). A graceful skip/pass is expected — **not** a true failure.
- Workflows triggered on a different branch or by a scheduled event are out of scope unless the task changed them.

### If a workflow fails after your push

1. Read the failure log with `gh run view <RUN_ID> --log-failed`.
2. Fix the root cause (lint error, broken test, bad config).
3. Push the fix.
4. Re-run `gh run list` and confirm green before marking done.

Do **not** declare the task complete while any workflow is red.

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
- [ ] CI / all workflows green (`gh run list --limit 5`)
- [ ] `guide.html` updated if user-facing feature shipped
- [ ] `dashboard.html` updated if admin/metric feature shipped
- [ ] `make ship-server` run if templates changed
- [ ] Live URL verified
```
