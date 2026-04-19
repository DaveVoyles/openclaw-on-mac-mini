# Plan: Local Folder Access for Users (v3)

**Date:** 2026-04-18
**Status:** Planning — awaiting user approval

---

## User Request

> I want OpenClaw to look at a folder on a users machine and review the contents like Copilot CLI where you CD into a folder. Make sure it works on Windows and Mac.

---

## The Copilot CLI Analogy

The Copilot CLI works on local files because **it runs ON your machine**. A cloud bot (like OpenClaw Slack) cannot reach into a users local filesystem — fundamental OS security boundary, true of all cloud AI.

**The solution:** the **OpenClaw CLI** (Python, cross-platform) + a new scan command + a Windows PowerShell installer.

---

## Three Tiers

### Tier 3 (works today) — Slack drag-and-drop
Select all files in a folder (Cmd+A Mac / Ctrl+A Windows) and drag into Slack. OpenClaw reads them. No install.

### Tier 2 — Open WebUI folder drag (zero install)
Open chat.davevoyles.synology.me in Chrome or Edge and drag the entire folder in. Works on Mac and Windows. ~20 files per batch limit.

### Tier 1 — OpenClaw CLI scan command (best experience)

**Mac/Linux:**
Install: curl -fsSL https://openclaw.davevoyles.synology.me/install | bash
Use: cd ~/Desktop/MyDocs && openclaw scan

**Windows (PowerShell):**
Install: iwr https://openclaw.davevoyles.synology.me/install.ps1 | iex
Use: cd ~/Desktop/MyDocs; openclaw scan

CLI reads files locally, sends folder tree + content to OpenClaw, streams response.
Python pathlib already used throughout — no Windows path issues.

---

## Windows Installer Plan

New install.ps1 will:
1. Check Python 3.x (prompt winget if missing)
2. Download openclaw_cli.py + support files to %USERPROFILE%\.openclaw3. Create openclaw.cmd wrapper, add to user PATH
4. Set OPENCLAW_URL in user environment

---

## Wave Plan

### Wave 1 (parallel)

Lane 1 - Han: Add scan [path] command to src/openclaw_cli.py
  - Reads all files in cwd or given path
  - Builds folder tree + content summary
  - Sends to AI as context

Lane 2 - Yoda: Create scripts/install_openclaw_cli_windows.ps1 + /install.ps1 route
  - Verify Open WebUI folder drag on Mac + Windows

### Wave 2

Lane 1 - Han: Update /onboarding with Mac + Windows install instructions + scan command docs

---

## Files to Touch

- src/openclaw_cli.py — add scan command
- src/dashboard/routes.py — add GET /install.ps1 route
- src/dashboard/html_handlers.py — PowerShell installer handler
- scripts/install_openclaw_cli_windows.ps1 — new Windows installer
- templates/onboarding.html — update with cross-platform instructions
- docs/CLI_QUICKSTART.md — document scan command

---

## Status

- [ ] User approves plan
- [ ] Wave 1 launched
- [ ] Wave 2 launched
- [ ] Deployed and verified on Mac and Windows
