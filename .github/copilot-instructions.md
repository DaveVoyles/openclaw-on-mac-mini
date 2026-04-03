# OpenClaw — Agent Reference

## Project Documentation

Before working on this codebase, read these two reference documents:

| Document | Purpose |
|----------|---------|
| [`docs/SERVICES.md`](../docs/SERVICES.md) | **All external services & APIs** — names, links, descriptions, why each is used, and required env vars |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | **System architecture diagram** — how every service, bot component, and API connects together |

> These are the authoritative sources for "what does this project use and why." Always check them before adding new integrations or env vars.

---

## Workflow Verification (REQUIRED)

**After every commit pushed to `main`, verify that ALL GitHub Actions workflows pass.** The project has two CI workflows:

| Workflow | Runner | File |
|----------|--------|------|
| `tests.yml` | **Self-hosted macOS** (this Mac Mini) | `.github/workflows/tests.yml` |
| `ci.yml` | Ubuntu (GitHub-hosted) | `.github/workflows/ci.yml` |

### Steps after each push:
1. **Push your commit(s)** to `main`
2. **Check workflow status** — use the GitHub API or `gh run list --limit 2` to verify both workflows triggered
3. **Wait for completion** — both workflows must pass before considering the task done
4. **If a workflow fails:**
   - Read the failure logs: `gh run view <run-id> --log-failed`
   - Fix the issue and push a follow-up commit
   - Do NOT move on until all workflows are green

### Quick verification command:
```bash
gh run list --repo DaveVoyles/openclaw-on-mac-mini --limit 4 --json status,name,conclusion
```

> ⚠️ The self-hosted runner runs on this Mac Mini. If it's offline or Orbstack/Docker is down, `tests.yml` will queue indefinitely. Check `gh run list` for stuck runs.

**Before implementing ANY new functionality from scratch**, check [ClawHub](https://clawhub.ai/) for existing AgentSkills bundles. Someone has likely already built what you need.

## What is ClawHub?

- **AgentSkills marketplace** — pre-built, reusable skill bundles for AI agents
- **npm-style versioning** — pick exact versions, pin dependencies, track changelogs
- **Vector search** — find skills semantically by describing what you need

## Workflow

1. 🔍 **Search ClawHub first** — visit https://clawhub.ai/ and search for the functionality you need
2. 📦 **Evaluate bundles** — read the README, review version history, confirm it fits the requirement
3. ✅ **Integrate it** — use the existing skill rather than reinventing it
4. 🛠️ **Build from scratch only** — if nothing suitable exists on ClawHub, then implement it yourself

> **Rule:** Never begin a ground-up implementation without first confirming ClawHub has no suitable skill bundle for it.

---

## How ClawHub Skills Work (OpenClaw Integration)

ClawHub skills are **directory bundles** of files installed into `skills/<skill-name>/` inside the project.
They are **not Python packages** — they are typically standalone Python scripts invoked via subprocess.

### Anatomy of an installed skill

```
skills/
  openclaw-tavily-search/
    SKILL.md              ← description, usage instructions
    _meta.json            ← version metadata
    scripts/
      tavily_search.py    ← the actual runnable script
  free-web-search/
    skill.md
    _meta.json
    scripts/
      web_search.py
```

### Installing a skill

```bash
# Install by slug from clawhub.ai
npx clawhub@latest install <slug>

# Examples used in this project:
npx clawhub@latest install free-web-search          # DuckDuckGo, no API key
npx clawhub@latest install openclaw-tavily-search   # Tavily AI Search
```

Skills install into `skills/<slug>/` by default (the workspace root).

### Integrating a script-based skill into OpenClaw

Because these are subprocess scripts (not importable Python modules), the integration
pattern is:

```python
import sys, json, asyncio
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent          # skills/
_MY_SCRIPT = _SKILLS_DIR / "my-skill" / "scripts" / "main.py"

async def my_skill(query: str) -> str:
    cmd = [sys.executable, str(_MY_SCRIPT), "--query", query, "--json"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    data = json.loads(stdout.decode())
    return _format(data)
```

Key points:
- Use `sys.executable` so the same Python interpreter (and venv) is used
- Pass env vars like `TAVILY_API_KEY` via `env={**os.environ, "KEY": value}`
- Always add a `timeout` to `wait_for` — external scripts can hang
- Parse stdout as JSON when the script supports `--json` flag
- Implement a graceful fallback if the script is missing or returns non-zero

### Fallback strategy

Always write skills defensively — check if the script file exists before calling it,
and provide a fallback (or a clear config error message):

```python
if _MY_SCRIPT.exists():
    # call subprocess
else:
    return "❌ Skill not installed. Run: npx clawhub@latest install my-skill"
```

### Currently installed skills

| Slug | Script | Purpose | API Key |
|------|--------|---------|----------|
| `free-web-search` | `scripts/web_search.py` | DuckDuckGo web search | none |
| `openclaw-tavily-search` | `scripts/tavily_search.py` | Tavily AI web search | `TAVILY_API_KEY` |

### Where ClawHub skills are **not** the right fit

- Skills that require native binaries (e.g. `summarize` uses a macOS brew binary) — these won't work inside a Linux Docker container
- Skills that assume Claude Desktop's `web_search` built-in tool — this project uses Discord + Gemini, not Claude Desktop
- When you need tight async integration with aiohttp session reuse — subprocess overhead is fine for occasional calls but not high-frequency loops

---

## Evaluating a ClawHub Skill

Before installing, check these signals on the skill's page:

| Signal | Good | Caution |
|--------|------|---------|
| OpenClaw security badge | **Benign** | **Suspicious** — review code manually before installing |
| Runtime requirements | Python stdlib / pip packages only | Lists a `Bin` (brew, apt, native binary) |
| Tool assumptions | No mention of `web_search` / `web_fetch` built-ins | Assumes Claude Desktop built-in tools |
| Download count | Any (high = more battle-tested) | 0 installs + no comments = untested |
| Script language | `.py` | Shell scripts, Node.js (require extra deps in Docker) |

**Docker compatibility check:** The bot runs in a **Linux Docker container**, not macOS.
A skill lists its runtime requirements under "Runtime requirements" on the skill page.
If it says `Bins: <something>`, verify that binary is available in Linux — if it needs
`brew install`, it will silently fail at runtime.

**Read the SKILL.md first.** It shows CLI flags, output format, and required env vars.
This saves you from discovering missing args after wiring it into the code.

---

## Full Integration Checklist

When adding a new ClawHub skill, update **all** of the following in order:

```
1. npx clawhub@latest install <slug>
   → installs to skills/<slug>/

2. Read skills/<slug>/SKILL.md (or skill.md)
   → understand CLI args, output format, required env vars

3. Test the script directly before writing any wrapper:
   python3 skills/<slug>/scripts/script.py --help
   python3 skills/<slug>/scripts/script.py --query "test" --json

4. Add Python dependencies to requirements.txt
   → skills often need httpx, beautifulsoup4, etc. — check their imports

5. Write the async wrapper in skills/advanced_skills.py
   → use subprocess pattern (see above), add to ADVANCED_SKILLS dict

6. Add a _TOOL_DECLARATIONS entry in llm.py
   → if the skill should be callable by Gemini via /ask

7. Optionally add a Discord slash command in bot.py
   → for skills that users will invoke directly (not just via /ask)

8. If the skill needs an API key:
   → add to .env (real value)
   → add to .env.example (blank placeholder with comment)
   → add to docs/SERVICES.md (name, purpose, env var name, link)

9. Update docs/SERVICES.md with the new service entry
   → name, link, description, why it's used, required env vars

10. Update the dashboard command list in src/dashboard.py
    → add new slash commands to _command_list() under the correct category
    → if it's a new category, add a new dict entry with "category" and "commands"
    → this powers the /dashboard command reference table

11. Update the Guide page in src/dashboard.py
    → add an entry in GUIDE_HTML under the relevant section
    → if it's a new category, add a new section and a link in the sidebar

12. Update the "Currently installed skills" table in this file
```

## Post-Update Documentation Rule

**After every feature change, bug fix, or new command**, update **all** relevant documentation:

```
1. templates/guide.html
   → Add/update section for the feature (TOC + body)
   → Include commands, usage examples, and tips

2. templates/dashboard.html
   → Update the "What's New" card if it's a significant feature
   → New commands appear automatically via /api/dashboard

3. docs/ARCHITECTURE.md
   → Add new modules to the Mermaid diagram
   → Add new data flows to the Data Flow Summary table

4. docs/MODULES.md
   → Add new src/*.py files to the module table
   → Update the file count in the header

5. README.md
   → Update the Status line if a new phase begins
   → Add a feature block for the new phase
   → Update "Planned" section as items are completed

6. .github/copilot-instructions.md (this file)
   → Add new skills to the "Currently installed skills" table
   → Update any architecture references that changed

7. config/config.yaml
   → Add config blocks for new features with sensible defaults
```

> **This rule exists so users can always discover features through the guide and dashboard.**
> Every feature must be documented — undocumented features don't exist to users.

### Key modules for memory & search features

| Module | Purpose | Storage |
|--------|---------|---------|
| `src/vector_store.py` | ChromaDB semantic memory (3 collections: memories, conversations, research) | `data/chromadb/` |
| `src/thread_store.py` | SQLite persistent threads with WAL mode, auto-titling, search | `data/memory/openclaw.db` |
| `src/qmd.py` | Quick Memory Discovery — keyword facts (also embeds to ChromaDB) | `data/memory/qmd.json` |
| `src/memory.py` | Session context + summaries (also embeds to ChromaDB) | `data/memory/sessions/` |
| `src/research_agent.py` | Research with auto-indexing into vector store | ChromaDB `research` collection |

> **Shortcut for LLM-only skills:** If the skill just needs to be callable via `/ask`
> (not a dedicated command), you only need steps 1–6, 9, 10, and 11.

---

## Suggested ClawHub Searches for This Project

When looking to add new functionality, try these search terms on https://clawhub.ai/skills:

| You want to add… | Search term |
|-----------------|-------------|
| Web search | `web search`, `tavily`, `duckduckgo` |
| Read/summarize a URL | `browse url`, `fetch url`, `summarize` |
| Weather | `weather` |
| Home automation | `home assistant`, `mqtt`, `smart home` |
| Notifications (Pushover, Slack, etc.) | `notify`, `pushover`, `slack` |
| File/data conversion | `convert`, `pdf`, `csv` |
| Calendar | `calendar`, `caldav`, `google calendar` |
| Git / GitHub | `github`, `git` |
| SSH / remote execution | `ssh`, `remote exec` |
| Monitoring / uptime | `uptime`, `monitor`, `ping` |
