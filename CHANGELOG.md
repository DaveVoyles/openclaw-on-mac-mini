# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
