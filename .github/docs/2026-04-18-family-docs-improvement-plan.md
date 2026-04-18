# Family Document Assistant — Improvement Plan

**Created:** 2026-04-18  
**Status:** Wave 1 — Active  
**Owner:** Fleet execution  

---

## User Persona

### The Non-Technical Family Users

| Attribute | Detail |
|-----------|--------|
| **Who** | Mom (Mac) + Dad (Windows PC) |
| **Age / Tech comfort** | 60s–70s; comfortable with Slack drag-and-drop, browser, Word, Excel; no CLI |
| **Primary tasks** | Review & proofread letters/contracts, edit budgets/lists in Excel, organize files |
| **Current AI** | Google Gemini (web interface — just type questions) |
| **Pain points** | Must manually copy/paste content into Gemini; can't directly share files; no way to get an edited file back |
| **Access surfaces** | Slack DM with OpenClaw (primary), browser at `chat.davevoyles.synology.me` (secondary) |
| **File types** | `.docx` (Word), `.xlsx` (Excel), `.pdf` (read-only), `.txt` |
| **Not in scope** | Images, videos, code, technical CLI usage |

### Typical Workflows

1. **"Fix my letter"** → drag Word doc into Slack → tap ✏️ Proofread → *want: get back fixed .docx file*
2. **"What does this contract say?"** → drag PDF → tap 📋 Summarize → read plain-language summary
3. **"Check my budget"** → drag Excel → tap 🔍 Find errors → see issues listed
4. **"Research this term"** → send message with question + file → *want: Perplexity-backed answer with doc context*
5. **"Process my whole Documents folder"** → *want: drop folder, batch-process all files*

---

## Current Capabilities (Baseline)

| Capability | Status | Notes |
|-----------|--------|-------|
| File upload → auto-brief | ✅ Shipped | Wave 2 |
| Block Kit action buttons | ✅ Shipped | Wave 2 — proofread/summarize/explain/errors/read |
| `/simple on\|off` per-user pref | ✅ Shipped | Wave 1 |
| `/help` slash command | ✅ Shipped | Wave 1 |
| `document_skills.read_word/excel` | ✅ Available | Used in file action handlers |
| `document_skills.edit_word/excel` | ✅ Available | Not yet wired to Slack return flow |
| `document_skills.create_word/excel` | ✅ Available | Not yet wired |
| Gemini 2.5 Flash (primary LLM) | ✅ Live | Best for long documents |
| Perplexity (search/research) | ✅ Live | Used for web search; not yet in doc context |
| GitHub Copilot Enterprise endpoint | ✅ Live | `--model copilot` routing available |
| `/ai-files` Docker volume | ✅ Live | Mac Mini only; not accessible to Windows/Mac parents |
| `file_skills.read_file / list_files` | ✅ Available | Reads from `/ai-files`; no Slack exposure |
| Return edited doc via Slack | ❌ Missing | Needs `files:write` scope + wiring |
| Mac folder watcher | ❌ Missing | No sync from parent Mac → /ai-files |
| Windows file access | ❌ Missing | No native Windows workflow |
| `/files` Slack command | ❌ Missing | Can't list or reference /ai-files by name |
| Perplexity in document context | ❌ Missing | "Look this up" while reading a doc |
| Batch folder processing | ❌ Missing | Can't say "analyze everything in this folder" |

---

## Gap Analysis

### Gap 1: No document return (highest impact)
User gets analysis text but **can't get back a fixed .docx**. This is the single biggest UX gap vs. Gemini — you can have OpenClaw proofread AND return the corrected file.

**Root cause:** `files:write` Slack scope not in manifest; `edit_word()` not wired to Slack upload.

### Gap 2: No folder/file access from Windows or Mac
`/ai-files` is a Docker volume on Mac Mini. Parents must upload one file at a time via Slack. No way to say "here's my whole Documents folder."

**Root cause:** No file sync agent; no cloud storage integration.

### Gap 3: Model routing not task-aware for documents
Long docs should use **Gemini 2.5 Flash** (large context window). Research questions should route to **Perplexity**. Technical documents could route to **Copilot**. Currently the user must manually specify `--model`.

**Root cause:** File action handlers use `auto` routing with no doc-type heuristics.

### Gap 4: Perplexity not in document context
Can't highlight a term in a document and ask "what does this mean?" with Perplexity providing web-backed research alongside the doc content.

**Root cause:** Perplexity routing is search-only; not mixed with file context.

### Gap 5: No file browser / reference by name
Parents must re-upload files every time. Can't say "use the budget.xlsx I uploaded last week."

**Root cause:** `file_skills.list_files()` exists but not exposed via Slack.

---

## Wave Plan

### Wave 1 — NOW (Parallel: Implement + Design)

| Lane | Agent | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Han 😉🚀 | M | **Implement: Return edited document** — add `files:write` scope, wire `edit_word`/`create_word` to post back a corrected .docx after Proofread action | — | Pending |
| 2 | Yoda 👽✨ | M | **Design: Wave 2 spec** — detailed implementation spec for Mac folder watcher, `/files` Slack command, smart model routing | — | Pending |

**Wave 1 checkpoints:** 10m first checkpoint; 30m hard stop per lane.

**Wave 1 done when:**
- Han: Proofread action returns a downloadable corrected .docx in Slack; `files:write` in manifest; 2+ new tests
- Yoda: Wave 2 spec written to `.github/docs/2026-04-18-wave2-spec.md`; covers Mac watcher + `/files` + model routing; includes done-when criteria for each lane

---

### Wave 2 — After Wave 1 Synthesis (Parallel: Implement + Design)

| Lane | Agent | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Leia 👑💁‍♀️ | M | **Implement: Mac folder watcher** — Python script + launchd plist; watches `~/Documents/OpenClaw/` and syncs new `.docx/.xlsx/.pdf` to `/ai-files` on Mac Mini via rsync over SSH | Wave 1 Yoda spec | Pending |
| 2 | Chewy 🐻💪 | M | **Implement: `/files` Slack command** — list files in `/ai-files`, reference by name in subsequent queries, LRU cache for file registry | Wave 1 Yoda spec | Pending |
| 3 | R2 🤖🔧 | M | **Design: Wave 3 spec** — Windows PowerShell watcher script + installer, Perplexity-in-document-context, batch folder processing | — | Pending |

---

### Wave 3 — After Wave 2 Synthesis (Parallel: Implement + Design)

| Lane | Agent | Effort | Scope | Blocked by | Status |
|------|-------|--------|-------|-----------|--------|
| 1 | Luke 🌟⚔️ | L | **Implement: Windows companion script** — PowerShell file watcher that watches a folder and auto-uploads files to Slack; `.ps1` installer with setup wizard | Wave 2 R2 spec | Pending |
| 2 | Darth 😈⚡ | M | **Implement: Smart model routing for docs** — auto-route large docs to Gemini, research questions to Perplexity, short code-heavy docs to Copilot | Wave 2 R2 spec | Pending |

---

### Wave 4 — Future (Backlog)

| Feature | Rationale |
|---------|-----------|
| OneDrive sync (Windows) | Parents likely already use OneDrive; zero-install solution for Windows file access |
| iCloud/Google Drive sync (Mac) | Same for Mac parents |
| Batch folder processing | "Analyze everything in this folder" — requires bulk file handling + progress reporting |
| Document templates | "Create a letter like this one but for X" — uses `create_word()` |
| Document history / "re-use my last file" | Extended file registry backed by disk; TTL-based cleanup |
| Perplexity in document context | Hybrid query: doc text + Perplexity web search for terms/entities |

---

## Model Routing Strategy (Documents)

| Scenario | Recommended Model | Reason |
|----------|------------------|--------|
| Long document (>4k words) | `gemini` (Gemini 2.5 Flash) | 1M context window; best for full doc analysis |
| Short letter / memo | `auto` | Any model handles it |
| Research a term / fact-check | `perplexity` | Web-backed, cited answers |
| Code in document / technical spec | `copilot` | GitHub Copilot Enterprise; best for technical content |
| Quick formatting / proofreading | `auto` or local Gemma | Fast, cheap |
| Spreadsheet with formulas | `gemini` | Understands structured/tabular data well |

---

## Technical Requirements

### `files:write` Scope (Wave 1)
Required to post files back to Slack. Add to `scripts/update_slack_manifest.py`:
```python
"files:write",
```
User must run `make slack-manifest` and reinstall after.

### `/ai-files` Volume Access Pattern
`file_skills.py` reads from `/ai-files` (Docker volume). The folder watcher (Wave 2/3) must rsync to:
```
macmini:/ai-files/
```
Permission model: files placed there are readable by the `openclaw` container.

### Mac Folder Watcher Architecture
```
~/Documents/OpenClaw/ (parent's Mac)
    ↓ rsync over SSH (every 60s or on file change via FSEvents)
macmini:/ai-files/
    ↓ readable by slack_bot.py via file_skills.list_files()
```

### Windows Companion Architecture
```
C:\Users\Dad\Documents\OpenClaw\ (Windows watched folder)
    ↓ PowerShell FileSystemWatcher + Slack API file upload
Slack DM → OpenClaw bot receives file_shared event
    ↓ existing auto-brief + Block Kit buttons handle it
```

---

## Communication Log

| Time | Lane | Agent | Update |
|------|------|-------|--------|
| 2026-04-18T13:05 | — | Orchestrator | 🔍 Plan created; Wave 1 ready to launch |

---

## Retrospectives

*(filled in after each wave)*

---

## Done-When (overall)

- [ ] Parents on Mac can drop a file in `~/Documents/OpenClaw/` and interact via Slack without any manual upload
- [ ] Parents on Windows can drop a file in a watched folder and get Slack buttons automatically
- [ ] Proofread action returns downloadable corrected .docx in Slack
- [ ] Long documents auto-route to Gemini; research questions auto-route to Perplexity
- [ ] All features documented in `docs/PARENTS-GUIDE.md`
- [ ] 35+ tests passing
