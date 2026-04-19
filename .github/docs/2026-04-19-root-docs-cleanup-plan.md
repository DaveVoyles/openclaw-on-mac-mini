# Root + Docs Cleanup Plan
<!-- Date: 2026-04-19 -->

## User request
De-clutter root directory and /docs folder. Consolidate, move to subfolders, archive stale files.

## Wave 1 — Active

| Lane | Fleet | Size | Scope | Blocked by | Status |
|------|-------|------|-------|------------|--------|
| 1 | Han 😉🚀 | M | Root directory cleanup | — | Active |
| 2 | Yoda 👽✨ | M | Docs archive + ref updates | — | Active |

### Lane 1 (Han) — Root cleanup
**Delete untracked junk:**
- `notes.txt` (garbage content)
- `.env.backup.20260418_173539` (sensitive backup)
- `audit_test/` (empty dir)
- `_consolidate_thin.py` (one-off, untracked)
- `_rename_tests.py` (one-off, untracked)

**`git rm` tracked artifacts:**
- `coverage.json`, `logs_test/openclaw.log`
- `t.db`, `test.db`, `test_t.db`, `threads.db`

**`git mv` to `scripts/`:**
- `test_apis_direct.py`, `test_weekly_recap.py`, `verify_apis.py`
- `examples/recap_templates_example.py` → `scripts/examples/recap_templates_example.py`

**Add to .gitignore:** `coverage.json`

### Lane 2 (Yoda) — Docs archive
**`git mv` to `docs/archive/`:**
- `docs/Discord_Improvements.md`
- `docs/PARENTS-GUIDE.md`

**Update refs in:**
- `docs/DOCS-GOVERNANCE.md` — update archive table entry for Discord_Improvements
- `docs/PRODUCT-ROADMAP.md` — update PARENTS-GUIDE.md ref → `templates/parents-guide.html`

## Communication Log
| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| Start | 1 | Han 😉🚀 | Launching root cleanup |
| Start | 2 | Yoda 👽✨ | Launching docs archive |
