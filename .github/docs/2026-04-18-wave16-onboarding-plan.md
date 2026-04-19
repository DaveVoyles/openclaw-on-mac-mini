# Wave 16 — Onboarding Bug Fixes + UX Improvements

## Request

Follow-on to Wave 15: fix remaining stale slash commands, add critical FAQ content, and
simplify the most intimidating technical section for non-technical family users.

## Audit Findings

### 🔴 Critical Bugs — Stale slash commands

| File | Line(s) | Stale ref | Actual command | Impact |
|------|---------|-----------|----------------|--------|
| parents-guide.html | 522, 1080 | `/ask` | `/chat` | Users who try this get nothing |
| parents-guide.html | 841–844 | `/search` | `/filesearch` | Command doesn't exist |
| parents-guide.html | 874, 876 | `/saved` | `/mypins` | Command doesn't exist |
| onboarding.html | 538 | `/dropbox connect` | `/clawbox connect` | Already fixed in parent guide but missed in onboarding |
| onboarding.html | 546 | `/dropbox list` | `/clawbox list` | Same |
| onboarding.html | 554 | `/inbox` | `/email` | Non-existent command |

### 🟠 High-Value UX Improvements

1. `/clawbox` rename confusing — add "(that's your Dropbox)" hint in parents-guide Section 34
2. FAQ missing: "What if OpenClaw stops responding?" (server down scenario)
3. FAQ missing: file retention/privacy details
4. Mac sync section (Section 8) uses SSH, Terminal, launchctl — way too technical
5. Section 7 "Need Help?" is too brief and buried
6. 37-item TOC is overwhelming — group into 3 categories with visual dividers

## Wave Plan

### Wave 1 — Bug Fixes (parallel, critical)

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Han 😉🚀 | S | Fix `/ask`→`/chat`, `/search`→`/filesearch`, `/saved`→`/mypins` in parents-guide.html | — | Pending |
| 2 | Yoda 👽✨ | S | Fix `/dropbox connect`→`/clawbox connect`, `/dropbox list`→`/clawbox list`, `/inbox`→`/email` in onboarding.html | — | Pending |

### Wave 2 — UX Improvements (parallel)

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Leia 👑💁‍♀️ | M | Add FAQ items: server-down scenario, file retention; clarify `/clawbox` rename in Section 34; beef up Section 7 "Need Help?" | Wave 1 | Pending |
| 2 | Chewy 🐻💪 | M | Simplify Mac sync section (remove Terminal commands, replace with plain-English "ask Dave"); add TOC section groupings | Wave 1 | Pending |

## Files in Scope

- `templates/parents-guide.html`
- `templates/onboarding.html`

## Done When

- [ ] `/chat` replaces all `/ask` references in parents-guide.html
- [ ] `/filesearch` replaces all `/search` references in parents-guide.html (command + TOC + anchor text)
- [ ] `/mypins` replaces all `/saved` references in parents-guide.html (command + TOC + heading + anchor)
- [ ] `/clawbox connect` and `/clawbox list` in onboarding.html
- [ ] `/email` replaces `/inbox` in onboarding.html
- [ ] FAQ has "What if OpenClaw stops responding?" answer
- [ ] FAQ has file retention answer
- [ ] Section 34 clarifies that /clawbox = Dropbox
- [ ] Mac sync section simplified for non-technical users
- [ ] Deployed and health-checked

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| 21:27 | 1 | Han 😉🚀 | ✅ Wave 1 done: /ask→/chat (×2), /search→/filesearch (×4), /saved→/mypins (×3) in parents-guide.html |
| 21:27 | 2 | Yoda 👽✨ | ✅ Wave 1 done: /dropbox→/clawbox (×2), /inbox→/email in onboarding.html |
| 21:29 | 3 | Leia 👑 | ✅ Wave 2 done: Section 7 expanded, Section 34 /clawbox clarified, FAQ+2 new answers |
| 21:29 | 4 | Chewy 🐻 | ✅ Wave 2 done: Mac sync simplified, TOC grouped into 4 sections, FAQ quick-link added |
| 21:30 | — | — | ✅ Committed c0d427e, deployed, health check passed |

## Status: COMPLETE

All done-when criteria met. git_sha: c0d427e
