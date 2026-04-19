# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### 🔧 Code Quality & Refactoring — Tech Debt Waves P0–W8 (April 2026)
- Extracted 8 module-level helper functions from `bot.py` into `src/bot_helpers.py` (−143L from `bot.py`) ([9f7fe33](../../commit/9f7fe33))
- Added `managed_task()` wrapper in `src/bg_tasks.py` for all fire-and-forget asyncio tasks with timeout + error logging ([7d94d48](../../commit/7d94d48))
- Modernized typing imports across 5 files: `Dict→dict`, `List→list`, `Optional[X]→X|None` ([8bd3865](../../commit/8bd3865))
- Fixed duplicate `[tool.mypy]` section in `pyproject.toml` ([94d6e95](../../commit/94d6e95))
- Fixed duplicate `webhook_secret` field in `src/config.py` ([94d6e95](../../commit/94d6e95))
- Return type annotations added to `slack_bot.py` and key LLM/storage modules ([7c22e4a](../../commit/7c22e4a), [25bda4b](../../commit/25bda4b))
- Exception chaining enforced (`raise ... from e`) across `src/cogs/journal_cog.py` ([76633fb](../../commit/76633fb))
- Replaced silent `pass` in `docker_cog` `on_timeout` handlers with `log.debug` ([78b2c6c](../../commit/78b2c6c))
- Removed unused imports across `src/` via ruff F401 ([22cf705](../../commit/22cf705))

### 🧪 Testing — Tech Debt Waves P0–W8
- Deleted 51 zero-test stub files; clean collection ([04d8b1b](../../commit/04d8b1b))
- Renamed 151 test files to eliminate 196 duplicate function names across test suite ([77069cd](../../commit/77069cd))
- Consolidated thin test files into larger test modules ([77069cd](../../commit/77069cd))
- Added `@pytest.mark.smoke`, `@pytest.mark.expensive`, `@pytest.mark.slow` markers ([684d54b](../../commit/684d54b))
- Added 39 unit tests for `bg_monitoring.py` + `bg_briefing.py` ([39a9a76](../../commit/39a9a76))
- Added 10 unit tests for `bg_tasks.py` `managed_task()` helper ([23d06bb](../../commit/23d06bb))
- Added smoke test tier (108 tests, fast CI gate) ([eefb6b1](../../commit/eefb6b1))
- `make test-fast` target excludes `@pytest.mark.slow` tests

### 📦 Dependencies — Tech Debt Waves P0–W8
- Converted 27 production deps from `>=` (unpinned) to `~=` (compatible release) ([8bd3865](../../commit/8bd3865))
- Removed unused packages: `reportlab`, `polygon-api-client`, duplicate `pandas` ([04d8b1b](../../commit/04d8b1b))
- Added `mypy>=1.20.0` to `requirements-test.txt`
- Added `.github/dependabot.yml`: weekly pip + GitHub Actions updates; major version protection for `discord.py` and `google-genai` ([bbb839d](../../commit/bbb839d))

### 🔒 Security & Auth — Tech Debt Waves P0–W8
- `src/discord_web.py`: `_require_internal()` guard restricts `/metrics` and `/smoke` to localhost only ([7d95115](../../commit/7d95115))
- `/health` remains public (load balancer probe)
- `pip-audit` added to `.github/workflows/security.yml` ([929387b](../../commit/929387b))

### 🏗️ CI / DevX — Tech Debt Waves P0–W8
- CI lint step runs before `pip install` (lint failures surface in ~5s vs ~65s) ([bbb839d](../../commit/bbb839d))
- Added pytest cache, ruff cache, mypy cache to CI (faster reruns) ([929387b](../../commit/929387b))
- `ruff format --check` enforced in CI ([929387b](../../commit/929387b))
- Added `@pytest.mark.expensive` on 3 external-service test files (replaces `--ignore` flags) ([929387b](../../commit/929387b))
- Pages/release workflows migrated to `ubuntu-latest` (saves self-hosted runner capacity) ([7d95115](../../commit/7d95115))
- CI failure step writes `GITHUB_STEP_SUMMARY` for instant root-cause scanning
- Added `.editorconfig` for cross-IDE consistency ([9eff450](../../commit/9eff450))
- Added `.pre-commit-config.yaml`: ruff lint+format, file hygiene, mypy strict, env schema validation ([07960fc](../../commit/07960fc))
- Eliminated duplicate test run, path-filter security scan, reduced flaky retries ([d24ccf6](../../commit/d24ccf6))
- Skip test suite for docs/template-only pushes ([96c4a61](../../commit/96c4a61))

### 🛠️ Makefile / Scripts — Tech Debt Waves P0–W8
- Added `make help` as default goal (self-documenting via `##` comments) ([18e62d0](../../commit/18e62d0))
- New targets: `lint-fix`, `smoke`, `smoke-verbose`, `ci`, `validate-env`, `test-fast`, `format` ([9eff450](../../commit/9eff450))
- `scripts/validate_env.py`: validates `.env` against `.env.example` ([9eff450](../../commit/9eff450))
- `scripts/validate_schema.py`: cross-checks `config/env_schema.yaml` vs `.env.example` ([cf1a5c6](../../commit/cf1a5c6))
- `scripts/mypy_enforce.py`: strict mypy on whitelisted files ([76633fb](../../commit/76633fb))
- Restored orphaned `clean:` target label in `Makefile`

### 📝 Documentation — Tech Debt Waves P0–W8
- `docs/API.md`: HTTP API reference for all endpoints (auth tier, request/response) ([1d03071](../../commit/1d03071))
- `docs/TESTING.md`: test suite structure, markers, naming conventions, fixture inventory ([684d54b](../../commit/684d54b))
- `docs/CI-TROUBLESHOOTING.md`: common CI failure patterns + fix commands
- `docs/CONTENT-OWNERSHIP.md`: authoritative topic registry for docs overlap ([25bda4b](../../commit/25bda4b))
- `config/env_schema.yaml`: structured metadata for 116 env vars (13 categories) ([1803031](../../commit/1803031))
- 45 docs files stamped with `<!-- Updated: 2026-04-18 -->` ([32cd74e](../../commit/32cd74e))
- Archived 11 orphaned wave-plan files to `.github/docs/archive/` ([04d8b1b](../../commit/04d8b1b))
- Rewrote `README.md` ([16400ba](../../commit/16400ba))

### 🔬 Observability — Tech Debt Waves P0–W8
- `src/trace_context.py`: `TraceLoggingFilter` + `set_trace/clear_trace` via Python `contextvars` ([3d285e0](../../commit/3d285e0))
- `trace_id` injected into all log records for structured logging
- 5-minute TTL collection cache in `src/vector_store_client.py` ([7d95115](../../commit/7d95115))
- `/health` endpoint enhanced with DB + vector store subsystem checks ([1123d39](../../commit/1123d39))
- `timeout=10` added to all `sqlite3.connect()` calls across 18 files ([1123d39](../../commit/1123d39))

### 🔧 Code Quality — Wave 9 & 10 Tech Debt (April 2026)
- Converted 7 repeated test groups to `@pytest.mark.parametrize` (−43L) ([ef68544](../../commit/ef68544))
- Removed 3 unused test packages: `pytest-html`, `pytest-json-report`, `pytest-metadata` ([75d551a](../../commit/75d551a))
- Added 31 unit tests for `slack_bot.py` coverage gaps ([9d41f29](../../commit/9d41f29))
- Fixed 22 auto-fixable ruff violations (I001/F401/F811) across 15 files via `ruff --fix` ([4826c7a](../../commit/4826c7a))
- Fixed F821 undefined name violations: `Dict→dict` in `alert_patreon.py`, corrected test function references in `test_performance_monitor.py` and `test_plugin_system.py` ([09f9bad](../../commit/09f9bad))
- Added return type annotations to 13 functions across `bg_tasks.py`, `bg_monitoring.py`, `bg_briefing.py` ([3698231](../../commit/3698231))
- Added 23 unit tests for `scripts/validate_env.py` and `scripts/validate_schema.py` ([ba2b0a9](../../commit/ba2b0a9))

---

### Added — Wave 10 External Integrations (April 2026)
- **`/email [today|week|<keyword>]`** — Check Gmail inbox or search emails directly from Slack; powered by existing `email_skills.py` (IMAP, no OAuth needed — just App Password)
- **`/calendar [today|week]`** — View Google Calendar events from Slack; powered by existing `calendar_skills.py` (OAuth via `scripts/google_oauth_setup.py`)
- **`/dropbox [list]`** — Browse recent files from a Dropbox watch folder; new `src/dropbox_sync.py` module
- **Dropbox folder watcher** — Background loop polls `DROPBOX_WATCH_PATH` every 30 s; auto-DMs when new files appear (no manual uploads needed)
- **`dropbox>=12.0.2`** added to `requirements.txt`
- **3 new manifest entries** — `/email`, `/calendar`, `/dropbox` registered in `scripts/update_slack_manifest.py`
- **`.env.example`** — Added `DROPBOX_ACCESS_TOKEN` and `DROPBOX_WATCH_PATH` with setup comments
- **PARENTS-GUIDE.md** — New "Connecting Your Accounts" section with step-by-step Gmail, Calendar, and Dropbox setup

### Added — Wave 9 Slack Parent Experience (April 2026)
- **User personalization** — `/nickname Chuck` / `/nickname Lisa`; persisted in `data/slack_user_personas.json`
- **Clarification prompts** — Vague short questions trigger a Block Kit card with 3 quick-reply buttons (Explain this, Give me an example, Tell me more)
- **App Home wiki tab** — `app_home_opened` event + `_build_home_view()`: personalized greeting, all commands, recent files

### Added — Wave 8 Slack Improvements (April 2026)
- **`/saved`** — saves and recalls important replies
- **`/search`** — searches file history
- **`/schedule`** — sets daily digest time
- **DM thread memory** — bot replies in-thread for follow-up context
- **Retry cache** — re-runs last prompt on error button click

### Added — Wave 5 Slack Feature Expansion (April 2026)
- **5 new Slack slash commands:** `/digest`, `/template`, `/brief`, `/mystats`, `/clear`
  - `/digest on|off|status` — per-user daily DM digest of recently modified files (`data/digest_prefs.json`)
  - `/template list|<name>` — DMs a starter template file from `data/templates/`
  - `/brief` — shows last 5 uploaded files with timestamps
  - `/mystats` — per-user usage stats from `slack_metrics.jsonl`
  - `/clear` — clears session history and active file selections
- **2 Slack command renames** (Slack reserved the originals):
  - `/ask` → `/chat` (Slack AI claimed `/ask`)
  - `/status` → `/health` (Slack reserved `/status`)
- **13 total Slack commands** now registered in the app manifest
- **`make slack-manifest`** — improved workflow that copies JSON to clipboard + opens the correct browser URL (`app.slack.com/app-settings/…`)
- **`make slack-manifest-push`** — API push target (requires `xoxe.xoxp-` Slack CLI token)

### Fixed — Wave 5
- Slack manifest portal URL updated to `app.slack.com/app-settings/T0ATWRAK4Q4/A0ATR6KFXNJ/app-manifest` (old `api.slack.com/apps` URL is 404)
- Documented that `xapp-` tokens cannot push manifests — only `xoxe.xoxp-` (Slack CLI) or browser paste works

### Added — April 2026 Interface Expansion
- **Open WebUI** (`chat.davevoyles.synology.me`): Browser-based ChatGPT-style interface connected to OpenClaw's `/v1` API. Supports persistent chat history, markdown/table/code rendering, and regenerate. Auth disabled for private LAN use.
- **Slack bot** (`src/integrations/slack_bot.py`): Socket Mode Slack bot supporting DMs and `@openclaw` mentions in channels. Uses App-Level Token + Bot OAuth token. Configured via Slack app manifest (YAML).
- **Dashboard v2** (`openclaw-dashboard.davevoyles.synology.me`, port 7001): Lightweight second dashboard for stats and monitoring, separate from the main ops dashboard.
- **Traefik reverse proxy routes** for all three new services via Synology NAS (`config/traefik/dynamic/mac-mini.yml`): `chat.*`, `openclaw-dashboard.*`.
- **Nav buttons** in OpenClaw dashboard header for Open WebUI and Dashboard v2 (beside Refresh button).
- **Access Points card** in OpenClaw dashboard: visual grid linking all five interfaces (Discord, Open WebUI, Dashboard v2, CLI, Slack).
- **Interface comparison table** in OpenClaw dashboard: "Which Interface Should I Use?" — side-by-side guide covering best-use, strengths, and links for each interface.

### Fixed — April 2026
- **Templates symlink bug**: `src/templates/` is a symlink to `../templates/`. Docker bind-mount `./src:/app/src:ro` did not follow the symlink target outside the mounted directory. Fixed by adding `./templates:/app/templates:ro` as a separate volume mount in `docker-compose.yml`.
- **Port conflict**: Dashboard v2 remapped from port 7000 → 7001 (7000 is reserved by macOS AirPlay Receiver).
- **Multi-line paste**: Bracketed paste mode fix for the readline REPL path.
- **Escape cancel**: Escape key now cancels a running search mid-stream.

### Added
- Multi-stage Docker builds for production (<500MB target)
- Trivy security scanning in CI/CD pipeline
- GitHub Pages documentation site with Jekyll
- Enhanced pre-commit hooks framework
- Automated release workflow with multi-platform builds
- Conventional commits enforcement
- Release helper script for version management

### Changed
- Optimized Docker image size with multi-stage builds
- Enhanced security scanning with Trivy, Bandit, and Safety
- Improved documentation with GitHub Pages
- Comprehensive pre-commit hooks for code quality

### Infrastructure
- Production-ready docker-compose.prod.yml
- Health checks with extended intervals
- Resource limits and restart policies
- Named volumes for data persistence
- Network isolation and security hardening
- Automated changelog generation
- Multi-platform Docker builds (amd64, arm64)

---

## [0.6.0] - 2024-04-05

### Added
- Plugin system for extensibility
- Advanced scheduling with cron expressions
- Performance monitoring and profiling
- Backup management system
- Health checking and self-healing
- SMS provider integration via Twilio
- Enhanced reporting with templates
- Data export in multiple formats (CSV, JSON, Parquet)

### Changed
- Refactored codebase for better modularity
- Improved test coverage to 80%+
- Enhanced type safety with mypy
- Optimized performance with profiling

---

## [0.5.0] - 2024-04-02

### Added
- ChromaDB vector store for semantic memory
- Trend detection and analysis
- Weekly recap engine
- Personalized digest system
- Real estate monitoring with Zillow integration
- Trakt.tv integration for media tracking

### Changed
- Migrated to Google Gemini AI (from OpenAI)
- Enhanced Discord UI with modals and dropdowns
- Improved error handling and logging

---

## [0.4.0] - 2024-03-28

### Added
- Bookmark management system
- Research automation with citations
- Obsidian vault integration
- Advanced web scraping with Playwright
- PDF analysis capabilities

### Changed
- Refactored skill system for better organization
- Enhanced configuration management

---

## [0.3.0] - 2024-03-23

### Added
- Basic Discord bot framework
- Initial skill system (weather, news, search)
- Configuration management
- Health check endpoint
- Docker support

### Changed
- Migrated from proof-of-concept to production structure

---

## [0.2.0] - 2024-03-15

### Added
- Initial prototype with basic AI capabilities
- Simple command handling

---

## [0.1.0] - 2024-03-10

### Added
- Project inception
- Basic project structure
