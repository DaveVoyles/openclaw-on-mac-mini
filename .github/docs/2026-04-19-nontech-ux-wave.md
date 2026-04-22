# Wave — Non-Technical User UX Improvements

**Date:** 2026-04-19  
**Goal:** Implement 10 improvements to make OpenClaw more welcoming and useful for non-technical family members (Chuck, Lisa).

## Items

| # | Item | Lane | File | Size |
|---|------|------|------|------|
| 1 | Enhanced welcome DM with starter prompts | Yoda | slack_bot.py | S |
| 2 | Friendly error messages | Leia | slack_bot.py | M |
| 3 | "Just talk to me" occasional tip | Leia | slack_bot.py | S |
| 4 | Upload confirmation DM | Yoda | slack_bot.py | M |
| 5 | Proactive "summarize?" in file alert | Yoda | slack_bot.py | S |
| 6 | Onboarding Mac/Windows note at final step | Han | onboarding.html | S |
| 7 | Daily digest on by default | Yoda | slack_bot.py | S |
| 8 | OCR/photo section in User Guide | Han | parents-guide.html | S |
| 9 | Monthly tips nudge | Leia | slack_bot.py | M |
| 10 | Writing help section in User Guide | Han | parents-guide.html | S |

## Wave Plan

### Wave 1 — Parallel (Han + Yoda)
| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 1 | Han 😉🚀 | M | Templates: parents-guide (#8, #10), onboarding (#6) | — | Pending |
| 2 | Yoda 👽✨ | M | slack_bot.py: welcome DM (#1), upload DM (#4), digest default (#7) | — | Pending |

### Wave 2 — Sequential (Leia, after Yoda commits)
| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|------------|--------|
| 3 | Leia 👑💁‍♀️ | M | slack_bot.py: friendly errors (#2), tip reminder (#3), monthly nudge (#9) | Yoda done | Pending |

## Key file facts
- `src/slack_bot.py`: 4985 lines. `_WELCOME_MESSAGE` at line 439. `_handle_upload` at line 581. `_digest_loop` at line 801. `_send_file_alert` at line 869. `_check_new_user_onboarding` at line 309.
- `templates/parents-guide.html`: ~1150+ lines. Sections added as `<div class="section-card">` with `<h2 id="...">`.
- `templates/onboarding.html`: 721 lines. Last step has quick-links at line ~620. totalSteps = 8. Navigation JS at line 680.
- Deploy: `make ship-server`. Templates are bind-mounted (live immediately). Python changes need container recreate.
- After any slack_bot.py edit: `git add src/slack_bot.py && git commit && git push && make ship-server`

## Communication log
| Time | Lane | Fleet | Update |
|------|------|-------|--------|
