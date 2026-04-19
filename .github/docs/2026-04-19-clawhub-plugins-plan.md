# ClawHub Plugin Evaluation & Implementation Plan
<!-- Date: 2026-04-19 | Status: APPROVED — DO NOT EXECUTE until user triggers -->

## Context

Reviewed all 50 plugins at https://clawhub.ai/plugins against OpenClaw's existing integrations.
5 plugins identified as worth integrating. 45 rejected (duplicates, irrelevant platform, crypto, overkill).

**OpenClaw stack (existing — do not re-implement):**
- LLM routing: Gemini, Ollama, Copilot proxy, Perplexity/Tavily/Firecrawl
- Channels: Slack (primary), Discord (secondary)
- Calendar: Google Calendar via OAuth2 (`src/calendar_skills.py`)
- Documents: Word/Excel parsing (`src/document_skills.py`)
- Memory: ChromaDB vector store + `data/dream/MEMORY.md`
- Image gen: Stable Diffusion local (`src/image_gen.py`)
- File inbox: Dropbox sync + `/clawbox` command
- Health: Fitbit; Finance: AlphaVantage, API-Sports, NewsAPI
- Fleet orchestration: bespoke wave-based agent system

---

## Selected Plugins (5)

### 1. Session Bloat Warning — `@teodorarg/openclaw-session-bloat-warning`
- **What it adds:** Compaction-surface warning for CLI sessions; pre/post compaction notices with guidance
- **Why:** CLI users (Dave) hit context limits silently. This surfaces the pressure before it becomes a problem.
- **Complexity:** S — plugin install + config only
- **Risk:** Low — additive, no existing behavior touched
- **Clawhub page:** https://clawhub.ai/plugins/%40openclaw%2Fopenclaw-session-bloat-warning

### 2. Website Screenshot — `@rishabhdugar/pdf-api-screenshot`
- **What it adds:** Full-page PNG screenshots via PDFAPIHub; JS rendering, mobile/desktop/tablet viewports, cookie consent auto-click
- **Why:** No screenshot capability exists. Useful for research tasks ("show me what this page looks like"), monitoring, and visual debugging via Slack.
- **Complexity:** S/M — plugin install + expose as a Slack tool call or slash command
- **Risk:** Low — external API, no local state
- **Clawhub page:** https://clawhub.ai/plugins/pdf-api-screenshot
- **Note:** PDFAPIHub account required — check pricing/limits before committing

### 3. Receipt Scanner + PDF & Image OCR — `@rishabhdugar`
- **Receipt Scanner:** https://clawhub.ai/plugins/receipt-scanner
- **PDF & Image OCR:** https://clawhub.ai/plugins/pdf-ocr-scan
- **What it adds:** OCR for scanned docs, receipts, images; 100+ languages; perspective correction; bounding boxes
- **Why:** `/clawbox` file inbox accepts files but can't extract text from images/PDFs today. Adding OCR makes dropped receipts, photos, and scanned docs immediately queryable.
- **Complexity:** M — plugin install + wire into `src/file_skills.py` / `/clawbox` post-processing
- **Risk:** Low-Medium — touches file handling pipeline; test with sample receipts
- **Note:** Same PDFAPIHub provider as Screenshot — single account covers both

### 4. Apple PIM — `@omarshahine/apple-pim-cli`
- **What it adds:** macOS Calendar, Reminders, Contacts, and Mail via native Swift CLIs
- **Why:** OpenClaw runs on a Mac Mini M4. Google Calendar covers events but Reminders and Contacts are native Apple — no current integration. Apple Reminders is where many users manage to-dos.
- **Complexity:** M/L — Swift CLIs must be compiled/installed on Mac Mini host; Docker container needs host access; integrate new skills alongside `src/calendar_skills.py`
- **Risk:** Medium — requires host-level Swift toolchain; macOS permissions (Full Disk Access, Contacts, Calendar, Mail)
- **Clawhub page:** https://clawhub.ai/plugins/apple-pim-cli
- **Pre-requisite:** Verify Swift is available on Mac Mini host (`swift --version`); check plugin's Swift CLI source

### 5. Episodic Memory — `@yoshiakefasu/episodic-claw`
- **What it adds:** Episodic Memory Engine — structured time-ordered recall by episode/event/context
- **Why:** Current memory (ChromaDB semantic search + MEMORY.md narrative) is good for "what do I know about X" but weak for "what happened last Tuesday". Episodic layer adds timeline recall.
- **Complexity:** L — needs to integrate with existing ChromaDB setup; understand storage model; avoid duplicate indexing
- **Risk:** Medium — touches memory system; wrong integration could degrade recall quality
- **Clawhub page:** https://clawhub.ai/plugins/episodic-claw
- **Pre-requisite:** Read plugin source before integrating; understand its storage backend

---

## Implementation Waves

### Wave 1 — Quick Wins (Session Bloat + Screenshot)
**Size:** S + S | **Risk:** Low | **No blockers**

| Lane | Fleet | Size | Scope |
|------|-------|------|-------|
| 1 | Han 😉🚀 | S | Install + configure Session Bloat Warning plugin; verify compaction notices appear in CLI |
| 2 | Yoda 👽✨ | S | Install Website Screenshot plugin; expose via Slack as tool call; test with a sample URL |

**Done when:**
- Session bloat warnings surface at appropriate context thresholds in CLI
- `/chat screenshot https://example.com` (or equivalent) returns a PNG attached to Slack message
- Commit pushed, `make ship-server` run, health check passes

---

### Wave 2 — File Intelligence (OCR + Receipt Scanner)
**Size:** M + M | **Risk:** Low-Medium | **Blocked by:** PDFAPIHub account confirmed

| Lane | Fleet | Size | Scope | Blocked by |
|------|-------|------|-------|------------|
| 1 | Han 😉🚀 | M | Install Receipt Scanner; wire into /clawbox post-processing pipeline in `src/file_skills.py` | PDFAPIHub account |
| 2 | Yoda 👽✨ | M | Install PDF & Image OCR; expose as standalone tool call for arbitrary image/PDF OCR | PDFAPIHub account |

**Pre-flight check:** Confirm PDFAPIHub pricing tier. Both plugins use same provider.

**Done when:**
- Dropping a receipt photo into `/clawbox` triggers OCR and returns extracted line items
- `/chat ocr <file>` (or equivalent) extracts text from image/PDF
- Tests with 3 sample files: receipt photo, scanned doc, typed PDF

---

### Wave 3 — macOS Native Integration (Apple PIM)
**Size:** L | **Risk:** Medium | **Blocked by:** Swift toolchain verified on Mac Mini host

| Lane | Fleet | Size | Scope |
|------|-------|------|-------|
| 1 | Han 😉🚀 | M | Host-side: install Swift CLIs on Mac Mini; verify macOS permissions (Calendar, Reminders, Contacts, Mail); test each CLI directly |
| 2 | Yoda 👽✨ | M | Bot-side: install Apple PIM plugin; wire into Slack skill alongside existing `calendar_skills.py`; add Reminders + Contacts tools |

**Pre-flight checks:**
- `swift --version` on Mac Mini host
- Check plugin source for which Swift CLIs it compiles
- Confirm Docker container can reach host CLIs (via bind mount or SSH)

**Done when:**
- "Add reminder: buy milk tomorrow at 9am" creates a native macOS Reminder
- "Show my contacts named John" returns from macOS Contacts
- Existing Google Calendar commands still work (regression check)

---

### Wave 4 — Memory Enhancement (Episodic Memory)
**Size:** L | **Risk:** Medium | **Blocked by:** Waves 1-3 stable**

| Lane | Fleet | Size | Scope |
|------|-------|------|-------|
| 1 | Han 😉🚀 | M | Audit episodic-claw source: storage model, ChromaDB interaction, indexing schema |
| 2 | Yoda 👽✨ | M | Integration design: how episodic-claw layers on existing memory; sketch schema; identify collision points |

**Wave 4b (implementation — after design validated):**
| Lane | Fleet | Size | Scope |
|------|-------|------|-------|
| 1 | Han 😉🚀 | M | Install + configure episodic-claw alongside ChromaDB |
| 2 | Yoda 👽✨ | M | Integration tests: verify existing semantic recall still works; add episodic recall test cases |

**Done when:**
- "What did I ask you about last Tuesday?" returns episodic context
- Existing ChromaDB semantic search unaffected
- MEMORY.md narrative sync still works

---

## Rejected Plugins — Reference

| Category | Plugins | Reason |
|----------|---------|--------|
| Crypto/blockchain | Algorand, LI.FI, ICPSwap, Solana, Strake | No crypto use case |
| Wrong channel | WhatsApp (×2), Telegram (Claw Switchboard) | We use Slack/Discord |
| Already built | WeCanBot, Bitrouter, OpenViking, LingDu, AxonFlow, Magneto AI, Interven Guard, Openclaw Agent Protocol, Openclaw Workflow Planner, Openclaw Host Git Workflow | We have equivalent functionality |
| Sales/marketing | Aigroup Lead Discovery, SignalPipe, Starplast Ops | Not our use case |
| Coverage | Aigroup Financial Services (×2) | AlphaVantage already integrated |
| Hardware not present | Lutron Caseta | No Lutron devices |
| Personal/novelty | Soul, TruClaw, BTG, claw.cleaning | Not practical for homelab assistant |
| Storage covered | Storj, File Upload & Share | Dropbox + /clawbox covers this |
| Overkill | Openclaw Canon, Openclaw Delx Witness | Revisit if operational pain grows |
| Dev/edge case | PDF Watermark, PDF to JPG, PDF to PPTX, Document Scanner | Occasional edge case, not worth dependency |
| Experimental | P2P Portal, AIWork Channel, MCP Apps, ClawWatch, agentschatapp | Unknown maturity, no clear benefit |

---

## Execution Notes for Future Agents

1. **Start with Wave 1** — zero risk, quick value signal
2. **PDFAPIHub account** is shared across Wave 2 plugins — one signup covers both
3. **Apple PIM (Wave 3)** requires host-level access — check Docker bind mounts in `docker-compose.yml`
4. **Episodic Memory (Wave 4)** — read plugin source before touching anything; ChromaDB integrity is critical
5. **Never remove existing memory** (`data/chromadb/`) without explicit user approval
6. **After each wave:** run `make verify-deploy` and check server health before proceeding
7. **Commands to verify:** `python3 -m pytest tests/ -x -q` after any `src/` changes
