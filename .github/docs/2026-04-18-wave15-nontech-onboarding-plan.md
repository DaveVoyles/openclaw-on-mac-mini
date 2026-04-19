# Wave 15 — Non-Technical User Onboarding Improvements

## Request

Review and improve onboarding for non-technical family members using OpenClaw via the Parents Guide and the onboarding wizard.

## Findings

### 🐛 Critical Bug
- **`/dropbox` → `/clawbox` mismatch**: The Slack command was renamed in Wave 14 (reserved name conflict), but `parents-guide.html` still references `/dropbox connect`, `/dropbox list`, `/dropbox status`, `/dropbox forget` in 4 places. Users typing these commands in Slack will get no response.

### 📋 Onboarding Gaps

| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 1 | Onboarding wizard (`/onboarding`) is not linked from the parents guide | High — users miss the best first-run experience | S |
| 2 | Onboarding wizard still says "ask Dave for an invite link" — no actual link | High — blocks first-run | S |
| 3 | No "Start Here" call-to-action at top of guide — 35+ sections is overwhelming | High — abandonment | S |
| 4 | "Getting Started with Slack" section is at the very bottom (section 36) — new users need it first | High — wrong order | S |
| 5 | No FAQ covering: "Is it private?", "Does it remember me?", "Can I use it on my phone?", "What if it's wrong?" | Medium — repeated confusion | M |
| 6 | No expectation-setting section — users get frustrated when AI fails unexpectedly | Medium — trust | S |
| 7 | No mobile-specific guidance — guide assumes desktop throughout | Medium — many users are phone-first | S |
| 8 | Browser login says "ask Dave" with no further detail | Low — infrequent | S |

## Proposed Wave Plan

### Wave 1 — Critical fix + Discoverability (high-impact, low-risk)

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Han 😉🚀 | S | Fix `/dropbox` → `/clawbox` throughout parents-guide.html | — | Pending |
| 2 | Yoda 👽✨ | S | Add invite link + parents-guide link to onboarding.html; add "Start Here" banner and wizard link to parents-guide.html | — | Pending |

### Wave 2 — Content improvements

| Lane | Fleet | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Han 😉🚀 | M | Add FAQ section to parents-guide.html | Wave 1 done | Pending |
| 2 | Yoda 👽✨ | S | Add "Using OpenClaw on your phone" tip + expectation-setting blurb | Wave 1 done | Pending |

## Files in Scope

- `templates/parents-guide.html` — main family guide
- `templates/onboarding.html` — interactive step-by-step wizard

## Done When

- [ ] `/clawbox` replaces all `/dropbox` references in the parents guide
- [ ] Onboarding wizard has the real Slack invite link (expires May 17)
- [ ] Parents guide links to `/onboarding` near the top
- [ ] "Start Here" / first-run CTA is visible before the TOC
- [ ] FAQ section answers the 4–5 most common non-technical questions
- [ ] Mobile tip is present
- [ ] All deployed and health-checked

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| — | — | — | Plan created |
