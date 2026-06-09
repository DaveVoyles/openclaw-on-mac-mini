# Wave 18 — Non-Technical UX Improvements

**Date:** 2026-04-18  
**Status:** 🚀 In Progress  
**Risk:** Low (HTML template edits only)  
**User note:** Family uses OpenClaw primarily for work — skip family-relatable examples

## Target Outcome
Improve parents-guide.html and onboarding.html for non-technical users: Gmail troubleshooting, account connections table, navigation help, and onboarding "ask Dave" cleanup.

## Wave Plan

| Lane | Fleet | Effort | File(s) | Blocked by | Status |
|------|-------|--------|---------|-----------|--------|
| 1 | Han 😉🚀 | M | parents-guide.html | — | Active |
| 2 | Yoda 👽✨ | M | onboarding.html, webui-guide.html | — | Active |

### Lane 1 — Han (parents-guide.html)
1. Section 7 "Need Help?": add Home Tab cross-link
2. Section 8 sync: add file naming flexibility note
3. Section 31 (before account sections): add comparison table for Gmail/Calendar/Dropbox/Windows
4. Section 32 Gmail: add troubleshooting callouts (2FA required, success message, ask Dave fallback)

### Lane 2 — Yoda (onboarding.html + webui-guide.html)
1. onboarding.html line ~411: Slack invite expiry — add "Contact Dave if link expired" link/email note
2. onboarding.html line ~553/560: Gmail "ask Dave" → "This step is optional — skip for now"
3. onboarding.html Step 8 completion: add clear CTA "Go say hello to OpenClaw in Slack"
4. webui-guide.html: add small FAQ section (login issues, slow responses)

## Communication Log

| Time | Lane | Fleet | Update |
|------|------|-------|--------|
| 21:45 | — | Orchestrator | 🚀 Wave 18 launched: Han + Yoda in parallel |

## Validation
- `git diff --stat` to confirm only template files touched
- `python3 scripts/check_markdown_links.py` if links added
- Deploy: `orbctl start && docker-compose up -d --force-recreate openclaw`
- Health: `curl https://openclaw.davevoyles.synology.me/health`

## Post-Wave
- Commit both lanes together after synthesis
- Deploy to Mac Mini (which is 4 commits behind HEAD)
