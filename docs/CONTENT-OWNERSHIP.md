# Content Ownership Map
<!-- Updated: 2026-04-18 -->


This document defines the authoritative source for each major topic area
and points to supporting references. Use this to avoid duplication across docs.

## Topic Registry

| Topic | Authoritative Doc | Supporting Refs | Notes |
|-------|------------------|-----------------|-------|
| Thread / conversation persistence | [PERSISTENCE.md](PERSISTENCE.md) | [MEMORY-SYSTEM.md](MEMORY-SYSTEM.md), [ARCHITECTURE.md](ARCHITECTURE.md) | Covers SQLite thread store and legacy JSON thread files |
| Background tasks & scheduler | [BACKGROUND_TASKS.md](BACKGROUND_TASKS.md) | [ARCHITECTURE.md](ARCHITECTURE.md), [SERVICES.md](SERVICES.md) | Supervised loops (bg_tasks.py) and time-window tasks |
| Weekly recap / report engine | [WEEKLY_RECAP_ENGINE.md](WEEKLY_RECAP_ENGINE.md) | [RECAP-TEMPLATES.md](RECAP-TEMPLATES.md), [BACKGROUND_TASKS.md](BACKGROUND_TASKS.md) | Multi-API aggregation; templates live in RECAP-TEMPLATES.md |
| Personalized digests | [PERSONALIZED_DIGESTS.md](PERSONALIZED_DIGESTS.md) | [WEEKLY_RECAP_ENGINE.md](WEEKLY_RECAP_ENGINE.md), [BACKGROUND_TASKS.md](BACKGROUND_TASKS.md) | Per-user scheduled delivery; overlaps with recap engine |
| AI memory / context layers | [MEMORY-SYSTEM.md](MEMORY-SYSTEM.md) | [PERSISTENCE.md](PERSISTENCE.md), [ARCHITECTURE.md](ARCHITECTURE.md) | ChromaDB vector store, QMD facts, rules, user profile, goals |
| LLM routing & provider selection | [LLM-ROUTING.md](LLM-ROUTING.md) | [ARCHITECTURE.md](ARCHITECTURE.md), [MODULES.md](MODULES.md) | Gemini/Copilot/Ollama routing logic; provider fallback chain |
| External services & API catalog | [SERVICES.md](SERVICES.md) | [API_SETUP.md](API_SETUP.md), [API_COSTS.md](API_COSTS.md) | Authoritative list of services; setup and costs are separate |
| API costs & budgeting | [API_COSTS.md](API_COSTS.md) | [SERVICES.md](SERVICES.md) | Financial planning; service list is in SERVICES.md |
| Architecture overview & data flow | [ARCHITECTURE.md](ARCHITECTURE.md) | [MODULES.md](MODULES.md), [DEPENDENCY_MAP.md](DEPENDENCY_MAP.md) | Mermaid diagram of all runtime services |
| Module / file reference | [MODULES.md](MODULES.md) | [ARCHITECTURE.md](ARCHITECTURE.md) | Exhaustive per-file table for all src/*.py |

---

## Duplication Risk Areas

### Area 1: Thread and Conversation Persistence

- **Authoritative:** [PERSISTENCE.md](PERSISTENCE.md)
- **Overlaps found in:** [MEMORY-SYSTEM.md](MEMORY-SYSTEM.md) (lists in-memory conversation history and JSON threads as a memory layer), [ARCHITECTURE.md](ARCHITECTURE.md) (thread_store.py shown in diagram)
- **Resolution:** PERSISTENCE.md owns the schema and storage boundary details. MEMORY-SYSTEM.md should link there for persistence specifics rather than re-describing storage paths or WAL config.

### Area 2: Weekly Recap and Personalized Digests

- **Authoritative:** [WEEKLY_RECAP_ENGINE.md](WEEKLY_RECAP_ENGINE.md) for the recap engine; [PERSONALIZED_DIGESTS.md](PERSONALIZED_DIGESTS.md) for per-user digests
- **Overlaps found in:** Both docs describe scheduled delivery, multi-API aggregation, and Discord output format in similar terms. [RECAP-TEMPLATES.md](RECAP-TEMPLATES.md) also covers topic configuration that partially duplicates PERSONALIZED_DIGESTS.md.
- **Resolution:** WEEKLY_RECAP_ENGINE.md owns the aggregation engine. PERSONALIZED_DIGESTS.md owns per-user preferences and scheduling. RECAP-TEMPLATES.md owns template definitions. Cross-link; do not copy content.

### Area 3: Background Tasks and Scheduled Work

- **Authoritative:** [BACKGROUND_TASKS.md](BACKGROUND_TASKS.md)
- **Overlaps found in:** [WEEKLY_RECAP_ENGINE.md](WEEKLY_RECAP_ENGINE.md) and [PERSONALIZED_DIGESTS.md](PERSONALIZED_DIGESTS.md) both describe how tasks are scheduled, duplicating the supervisor architecture described in BACKGROUND_TASKS.md.
- **Resolution:** BACKGROUND_TASKS.md owns the supervisor/loop lifecycle. Feature docs (recap, digests) should reference it for scheduling mechanics rather than re-explaining loop registration.

### Area 4: External Services and API Cost

- **Authoritative:** [SERVICES.md](SERVICES.md) for the service catalog; [API_COSTS.md](API_COSTS.md) for financial planning
- **Overlaps found in:** [API_SETUP.md](API_SETUP.md) repeats service descriptions already in SERVICES.md. [ARCHITECTURE.md](ARCHITECTURE.md) names services in its diagram.
- **Resolution:** SERVICES.md is the single source for what a service is and why it exists. API_SETUP.md owns how to configure it. API_COSTS.md owns cost estimates. Avoid re-listing service descriptions in setup or cost docs.

### Area 5: AI Memory and Vector Store

- **Authoritative:** [MEMORY-SYSTEM.md](MEMORY-SYSTEM.md)
- **Overlaps found in:** [ARCHITECTURE.md](ARCHITECTURE.md) describes ChromaDB in the data-flow diagram. [PERSISTENCE.md](PERSISTENCE.md) lists storage surfaces but omits ChromaDB (gap, not duplication).
- **Resolution:** MEMORY-SYSTEM.md owns the full memory layer model. PERSISTENCE.md should add a row for the ChromaDB surface to close the gap. ARCHITECTURE.md should reference MEMORY-SYSTEM.md for detail rather than inline description.

---

## Conventions

- If you add new documentation, register it in the **Topic Registry** table above first.
- If content already exists in the authoritative doc, link to it — do not copy it.
- PRs that introduce new docs should update this file in the same commit.
- When a topic's authoritative doc changes, update the Supporting Refs column here.
