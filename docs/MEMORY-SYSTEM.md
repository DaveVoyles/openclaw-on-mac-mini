# 🧠 Memory System
<!-- Updated: 2026-04-18 -->


OpenClaw has a multi-layer memory system that stores, recalls, and manages knowledge across conversations. Every layer works together to give the bot persistent, contextual awareness — it "remembers" without being asked.

---

## Overview

| Layer | Backend | Purpose | Persistence |
|-------|---------|---------|-------------|
| **Conversation Memory** | In-memory + JSON | Per-user/channel chat history | 30-min TTL; saved threads persist on disk |
| **QMD Facts** | JSON file (`/memory/qmd.json`) | Long-term facts and preferences (keyword-searchable) | Permanent (manual or auto-extracted) |
| **Vector Store** | ChromaDB (`/memory/chromadb/`) | Semantic search across all knowledge | Permanent with decay |
| **Rules Engine** | JSON file (`/memory/rules.json`) | Learned behavioral rules from corrections | Permanent |
| **User Profile** | JSON file (`/memory/user_profile.json`) | Preferences, interests, working style | Permanent |
| **Goals** | JSON file (`/memory/goals.json`) | Active intentions and objectives | Until completed/dismissed |

---

## 🔄 How Auto-RAG Works

Before every LLM call, OpenClaw automatically:

1. **Searches ChromaDB** for relevant memories across all three collections (top `AUTO_RECALL_TOP_K`, default 3)
2. **Loads user profile** — preferences, interests, communication style, tools
3. **Loads relevant learned rules** — correction-based behavioral rules (top 5, threshold 0.6)
4. **Injects all context** as a `[Your Memory]` block prepended to the user message

The assembled context block looks like:

```
[Your Memory]
- [Memories · 92%] Dave prefers dark mode and uses Docker Compose
- [Research · 87%] Philadelphia housing market report from 2024-01-15
- [Conversations · 81%] Discussion about NAS backup strategy
```

This means the bot "remembers" things you've said without you needing to ask. The `recall_for_context()` function in `src/vector_store.py` handles this injection. Each result shows its source collection and similarity score.

---

## 🗂️ Collections

ChromaDB stores documents across three collections, each serving a different purpose:

| Collection | Contents | Metadata Fields |
|------------|----------|-----------------|
| `memories` | QMD facts, user preferences, learned rules, ontology entities | `type`, `tags`, `source`, `confidence` |
| `conversations` | Thread messages and session summaries | `type`, `user_id`, `thread_name` |
| `research` | Research reports and browsed source pages | `type`, `query`, `sources`, `url`, `domain` |

All collections use cosine similarity (`hnsw:space: cosine`) and support the same search API.

---

## 🤖 Automatic Fact Extraction

The **Fact Extractor** (`src/fact_extractor.py`) passively mines conversations for memorable facts:

**Trigger conditions** — extraction runs when:
- Message length ≥ 30 characters
- Not a greeting, slash command, or pure question
- Every 3rd qualifying message per user (rate-limited via `EXTRACT_EVERY_N`)

**What it extracts:**
- Concrete facts: names, dates, locations, preferences, decisions
- Personal details and context
- Up to 5 facts per conversation turn

**What it skips:**
- Greetings (`hi`, `hello`, `thanks`, `ok`)
- Slash commands (`/ask`, `/research`)
- Short messages (< 30 chars)
- Pure questions with no factual content
- Opinions and transient information

**How it stores:**
1. Gemini extracts facts at `temperature=0.1` (very low for factual accuracy)
2. Each fact is dedup-checked against ChromaDB (90% similarity threshold)
3. If unique → stored in ChromaDB (`memories` collection) with `source: "auto-extracted"` and `confidence: 0.7`
4. Also stored in QMD (`/memory/qmd.json`) as a keyword-searchable backup

---

## 📊 Memory Confidence & Ranking

Every memory has a **source** and **confidence** score that affects retrieval ranking:

| Source | Confidence | Description |
|--------|-----------|-------------|
| `user-explicit` | 1.0 | User ran `/remember` directly |
| `auto-extracted` | 0.7 | Fact extractor mined from conversation |
| `correction` | 1.0 | Rules engine learned from a user correction |
| `profile` | 1.0 | User profile sync |

**Ranking formula** — during search, similarity is adjusted by:
- **Confidence boost**: `similarity *= 0.9 + (confidence × 0.1)` — high-confidence facts rank higher
- **Decay penalty**: `similarity *= 0.9` — decayed documents get a 10% penalty
- **Threshold filter**: results below `CHROMA_SIMILARITY_THRESHOLD` (default 0.7) are discarded
- **Access tracking**: frequently-retrieved memories get reinforced via `bump_access()`

---

## 📉 Memory Decay

OpenClaw uses a 30-day decay cycle to keep the vector store relevant:

1. **Tracking** — every search result bumps `access_count` and `last_accessed` metadata
2. **Decay scan** — `get_decayed_documents()` finds documents where:
   - `last_accessed` is older than 30 days (`max_age_days=30`)
   - `access_count` < 1 (`min_access_count=1`)
3. **Marking** — `mark_decayed()` flags matching documents with `decayed: true`
4. **Effect** — decayed documents receive a 10% similarity penalty but are **not deleted**
5. **Reinforcement** — `/memory-refresh` or any search that hits a decayed doc bumps its access count, effectively un-decaying it

This ensures rarely-used memories fade gracefully while important ones persist.

---

## 📝 Knowledge Routing

When you use `/remember`, facts are intelligently routed to the best store (`src/qmd.py` → `_classify_fact()`):

| Content Signal | Route | Example |
|----------------|-------|---------|
| "I prefer…", "I like…", "my timezone…" | → **User Profile** | "I prefer dark mode" |
| "don't…", "always…", "you should…" | → **Rules Engine** | "Don't use emojis in code reviews" |
| Everything else | → **QMD + ChromaDB** | "My NAS IP is 192.168.1.8" |

All facts are **also** stored in QMD and ChromaDB regardless of routing, ensuring full searchability.

---

## 🗣️ Conversation Memory

Managed by `ConversationStore` in `src/memory.py`:

- **Key**: `(user_id, channel_id)` tuple — each user+channel pair has its own context
- **Format**: Gemini-compatible `[{"role": "user"|"model", "parts": [str]}]`
- **TTL**: 30 minutes of inactivity (`CONTEXT_TTL = 1800`)
- **Max history**: 50 messages per conversation (`MAX_HISTORY_LENGTH`)
- **Cleanup**: expired conversations are auto-summarized (if ≥ 4 messages) and stored in ChromaDB + `/memory/summaries/`
- **Session handover**: when a session expires, a handover note with pending items is saved and injected into the next session

### Named Threads

You can save and resume conversations:

| Operation | Details |
|-----------|---------|
| **Save** | Snapshots history to `/memory/threads/{user_id}_{name}.json` |
| **Load** | Restores history from disk and makes it the active conversation |
| **Names** | 1–32 chars, letters/digits/hyphens/underscores only |

---

## 👤 User Profile

The profile system (`src/user_profile.py`) tracks:

| Field | Type | Description |
|-------|------|-------------|
| `preferences` | `dict` | Key-value pairs (e.g., `timezone=EST`, `theme=dark`) |
| `interests` | `list[str]` | Topics of interest (e.g., `Docker`, `home automation`) |
| `tools` | `list[str]` | Tools the user works with (e.g., `VS Code`, `Docker Compose`) |
| `working_style` | `str` | How the user prefers to work |
| `communication_style` | `str` | Communication preferences |
| `context_notes` | `list[str]` | Free-text notes about the user |
| `learned_at` | `dict` | Timestamps for when each field was last updated |

**Auto-learning**: `learn_from_message()` uses Gemini to detect personal info from conversations and updates the profile automatically. The profile is also synced to ChromaDB (`doc_id: "user_profile"`) for semantic recall.

**System prompt injection**: `get_profile_prompt()` formats the profile as a `[User Profile]` block injected into every LLM call.

---

## ⚖️ Rules Engine

The rules engine (`src/rules_engine.py`) learns from corrections:

1. **Detection** — `detect_correction()` checks for patterns like "no,", "that's wrong", "actually,", "don't do that", "I prefer…"
2. **Extraction** — Gemini distils a one-liner operational rule from the correction + bot's previous response
3. **Storage** — rule saved to both `/memory/rules.json` and ChromaDB (`type: "rule"`)
4. **Retrieval** — `get_relevant_rules()` searches ChromaDB with a looser threshold (0.6) so rules surface for tangentially related topics
5. **Injection** — relevant rules are prepended to the system prompt at inference time

**Rule format** in JSON:
```json
{
  "id": "rule_1700000000000",
  "rule": "Always use Docker Compose v2 syntax, not v1.",
  "source": "User said: 'No, use docker compose not docker-compose'",
  "created_at": "2024-01-15T10:30:00Z",
  "access_count": 0
}
```

---

## 🎯 Memory Manager

`src/memory_manager.py` provides a unified facade over all memory backends:

| Method | Description |
|--------|-------------|
| `store(content, source, confidence, tags, dedup)` | Store across QMD + ChromaDB with optional dedup |
| `recall(query, top_k, include_rules, include_profile)` | Search all sources, merge and rank by similarity |
| `forget(memory_id)` | Remove from all three ChromaDB collections |
| `stats()` | Aggregated stats: vector store counts, QMD count, rules count, profile status |

**Deduplication** — when `dedup=True` (default), `store()` generates a deterministic content-based ID and checks ChromaDB for 90%+ similar documents before inserting. Duplicates reinforce the existing memory instead.

---

## ⌨️ Commands

| Command | Description |
|---------|-------------|
| `/remember <content>` | Explicitly store a fact (routed to best store) |
| `/recall <query>` | Search memories (keyword + semantic, merged results) |
| `/memory-stats` | View collection stats across all backends |
| `/memory-refresh` | Reinforce recent memories (bump access counts) |
| `/rules` | View all learned behavioral rules |
| `/profile` | View your learned user profile |
| `/goals` | View active goals and intentions |
| `/thread save <name>` | Save current conversation to disk |
| `/thread load <name>` | Resume a saved conversation |
| `/threads` | List all saved threads |

---

## ⚙️ Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `AUTO_RECALL_ENABLED` | `true` | Enable/disable Auto-RAG context injection |
| `AUTO_RECALL_TOP_K` | `3` | Max memories to inject per query |
| `EMBEDDING_MODEL` | *(empty)* | Custom Ollama embedding model (empty = ChromaDB built-in `all-MiniLM-L6-v2`) |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama server URL for custom embeddings |
| `CHROMA_DIR` | `/memory/chromadb` | ChromaDB storage directory |
| `CHROMA_SIMILARITY_THRESHOLD` | `0.7` | Minimum similarity for search results |
| `QMD_MEMORY_FILE` | `/memory/qmd.json` | QMD fact store path |
| `REFLECTION_ENABLED` | `true` | Self-evaluate complex responses before sending |

> ⚠️ **Changing `EMBEDDING_MODEL`** requires re-indexing. Delete the ChromaDB directory (`/memory/chromadb`) and restart — existing embeddings are incompatible across models.

---

## 📁 Data Files

| Path | Contents |
|------|----------|
| `/memory/chromadb/` | ChromaDB persistent storage (3 collections) |
| `/memory/qmd.json` | QMD keyword-searchable fact store (max 5,000 entries) |
| `/memory/rules.json` | Learned behavioral rules |
| `/memory/user_profile.json` | Structured user profile |
| `/memory/goals.json` | Active goals and intentions |
| `/memory/threads/` | Saved named conversation threads |
| `/memory/summaries/` | Auto-generated session summaries |
