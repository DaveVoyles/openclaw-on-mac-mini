---
layout: default
title: OpenClaw - Autonomous AI Agent
---

# 🦅 OpenClaw
<!-- Updated: 2026-05-21 -->


> **Autonomous AI agent with Slack interface — Production-ready infrastructure**

[![CI](https://github.com/DaveVoyles/openclaw-on-mac-mini/actions/workflows/ci.yml/badge.svg)](https://github.com/DaveVoyles/openclaw-on-mac-mini/actions/workflows/ci.yml)
[![Security](https://github.com/DaveVoyles/openclaw-on-mac-mini/actions/workflows/security.yml/badge.svg)](https://github.com/DaveVoyles/openclaw-on-mac-mini/actions/workflows/security.yml)
[![codecov](https://codecov.io/gh/DaveVoyles/openclaw-on-mac-mini/branch/main/graph/badge.svg)](https://codecov.io/gh/DaveVoyles/openclaw-on-mac-mini)

## 🚀 Quick Start

```bash
# Clone repository
git clone https://github.com/DaveVoyles/openclaw-on-mac-mini.git
cd openclaw-on-mac-mini

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run with Docker Compose
docker-compose up -d

# Or run in production mode
docker-compose -f docker-compose.prod.yml up -d
```

## ✨ Features

- **101+ AI-Powered Commands** - Research, analytics, bookmarks, real estate, and more
- **Multi-Modal AI** - Text, vision, and document analysis with Google Gemini
- **Semantic Memory** - ChromaDB vector store for intelligent context recall
- **Task Scheduling** - Cron-based automated tasks and reminders
- **Health Monitoring** - Self-healing with alerts and backup systems
- **Slack Integration** — Slash commands, Block Kit messages, file alerts, and `/copilot` host-CLI sessions
- **Production-Ready** - Multi-stage Docker, security scanning, automated releases

## 📚 Core Documentation

<div class="docs-grid">
  <div class="doc-card">
    <h3>🧩 <a href="AGENT-EXTENSION-GUIDE.html">Agent Extension Guide</a></h3>
    <p>Step-by-step recipes for adding skills, commands, providers, dashboard endpoints, background loops, plugins, schedules, and persistence. Start here when extending OpenClaw.</p>
  </div>

  <div class="doc-card">
    <h3>🔎 <a href="AUDIT-REPORT.html">Latest Doc Audit</a></h3>
    <p>2026-05-21 doc-vs-code reconciliation. Verified ground-truth counts, dead-ref list, and follow-up actions.</p>
  </div>

  <div class="doc-card">
    <h3>🙋 <a href="PARENTS-GUIDE.html">Non-Technical User Guide</a></h3>
    <p>Getting started with OpenClaw — no tech knowledge needed. Browser, Slack, files, and plain-language mode.</p>
  </div>

  <div class="doc-card">
    <h3>📖 <a href="COMMANDS.html">Commands Reference</a></h3>
    <p>Complete guide to all 101+ available commands</p>
  </div>

  <div class="doc-card">
    <h3>🏗️ <a href="ARCHITECTURE.html">Architecture</a></h3>
    <p>System design, components, and data flow</p>
  </div>

  <div class="doc-card">
    <h3>🧭 <a href="LLM-ROUTING.html">LLM Routing</a></h3>
    <p>Provider orchestration, routing policy, and fallback flow</p>
  </div>

  <div class="doc-card">
    <h3>🛡️ <a href="RESILIENCE.html">Resilience</a></h3>
    <p>Error handling, health signals, and runtime fallback behavior</p>
  </div>

  <div class="doc-card">
    <h3>💾 <a href="PERSISTENCE.html">Persistence</a></h3>
    <p>Storage boundaries, durability model, and versioning implications</p>
  </div>

  <div class="doc-card">
    <h3>⏱️ <a href="BACKGROUND_TASKS.html">Background Tasks</a></h3>
    <p>Supervisor loops, scheduled work, and recovery mechanics</p>
  </div>

  <div class="doc-card">
    <h3>🔧 <a href="API_REFERENCE.html">API Reference</a></h3>
    <p>Developer API documentation and integration guides</p>
  </div>

  <div class="doc-card">
    <h3>🧭 <a href="START-HERE.html">Contributor Start Here</a></h3>
    <p>Best first stop for onboarding, architecture orientation, and first contributions</p>
  </div>

  <div class="doc-card">
    <h3>🤝 <a href="CONTRIBUTING.html">Contributing</a></h3>
    <p>Detailed contribution guidelines and implementation checklists</p>
  </div>

  <div class="doc-card">
    <h3>🔒 <a href="API_SETUP.html">API Setup</a></h3>
    <p>Configure external service integrations</p>
  </div>

  <div class="doc-card">
    <h3>📊 <a href="SERVICES.html">Services</a></h3>
    <p>Backend services and infrastructure</p>
  </div>

  <div class="doc-card">
    <h3>🛰️ <a href="NETWORK-TOPOLOGY.html">Network Topology</a></h3>
    <p>Traffic paths, ports, and remote-access expectations</p>
  </div>

  <div class="doc-card">
    <h3>🚨 <a href="OPERATIONS-RUNBOOK.html">Operations Runbook</a></h3>
    <p>Incident response, monitoring thresholds, and recovery basics</p>
  </div>

  <div class="doc-card">
    <h3>🚢 <a href="DEPLOYMENT.html">Deployment</a></h3>
    <p>Environment setup, local vs production, verification, and rollback basics</p>
  </div>
</div>

## 🧭 Planning & Docs Governance

<div class="docs-grid docs-grid-compact">
  <div class="doc-card">
    <h3>🗺️ <a href="PRODUCT-ROADMAP.html">Product Roadmap</a></h3>
    <p>Canonical entrypoint for future improvements and cross-cutting planning</p>
  </div>

  <div class="doc-card">
    <h3>🧱 <a href="DOCS-GOVERNANCE.html">Docs Governance</a></h3>
    <p>Documentation taxonomy, lifecycle rules, and maintenance guidance</p>
  </div>
</div>

## 🧩 Subsystem Reference

Deep-dive docs for individual subsystems. Linked here so they don't drift orphaned from the rest of the docs set.

- [**Memory System**](MEMORY-SYSTEM.html) — Multi-layer memory: facts, profiles, QMD, vector store, rules engine
- [**Research & Autonomous Features**](RESEARCH-GUIDE.html) — `/research`, agent loop, goal tracker, scheduled briefings
- [**Personalized Digests**](PERSONALIZED_DIGESTS.html) — Per-user digest delivery via `digest_manager` + `scheduler`
- [**Weekly Recap Engine**](WEEKLY_RECAP_ENGINE.html) — News, finance, and sports recap aggregator
- [**Recap Templates**](RECAP-TEMPLATES.html) — Topic templates feeding the recap engine
- [**Data Synthesis**](DATA_SYNTHESIS.html) — Multi-source synthesis (NewsAPI + API-Sports + Alpha Vantage)
- [**Async Patterns**](ASYNC_PATTERNS.html) — How the CLI bridges sync invocation to async server I/O

## 📜 Roadmaps & History

- [**Tech Debt Audit (TD-1 → TD-7)**](tech_debt.html) — Shipped CLI refactor waves with commit SHAs
- [**UX Improvements Roadmap**](UX_IMPROVEMENTS.html) — Shipped CLI/UX wave history (Wave 1 → 19+)



<div class="stats-grid">
  <div class="stat-card">
    <h3>101+</h3>
    <p>AI Commands</p>
  </div>

  <div class="stat-card">
    <h3>30+</h3>
    <p>API Integrations</p>
  </div>

  <div class="stat-card">
    <h3>80%</h3>
    <p>Test Coverage</p>
  </div>

  <div class="stat-card">
    <h3>24/7</h3>
    <p>Uptime</p>
  </div>
</div>

## 🛠️ Technology Stack

- **Language**: Python 3.12
- **Framework**: slack-bolt (Socket Mode)
- **AI**: Google Gemini (Flash 2.5 primary) + Ollama (local fallback)
- **Memory**: ChromaDB vector store
- **Infrastructure**: Docker, Docker Compose
- **CI/CD**: GitHub Actions
- **Security**: Trivy, Bandit, Safety

## 📈 Recent Updates

- ✅ Multi-stage Docker builds (<500MB)
- ✅ Trivy security scanning in CI/CD
- ✅ GitHub Pages documentation site
- ✅ Enhanced pre-commit hooks
- ✅ Automated release workflow

## 🎯 Use Cases

### 📚 Research Assistant
Autonomous web research with citations, summaries, and Obsidian vault storage.

### 📊 Analytics & Insights
Track trends, generate reports, and visualize data from multiple sources.

### 🏠 Real Estate Monitoring
Automated property searches with Zillow integration and alert notifications.

### 📖 Content Curation
Bookmark management, article summarization, and knowledge base building.

### 🎬 Media Management
Track movies/TV shows with Trakt.tv, manage audiobooks, and monitor downloads.

## 🔗 Quick Links

- [GitHub Repository](https://github.com/DaveVoyles/openclaw-on-mac-mini)
- [Issue Tracker](https://github.com/DaveVoyles/openclaw-on-mac-mini/issues)
- [Changelog](https://github.com/DaveVoyles/openclaw-on-mac-mini/releases)
- [Docker Hub](https://hub.docker.com/r/davevoyles/openclaw)

## 📝 License

MIT License - see the [repository license](https://github.com/DaveVoyles/openclaw-on-mac-mini/blob/main/LICENSE).

---

<div class="footer">
  <p>Built with ❤️ by <a href="https://github.com/DaveVoyles">Dave Voyles</a></p>
  <p>Powered by Google Gemini AI</p>
</div>

<style>
.docs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
  margin: 2rem 0;
}

.docs-grid-compact {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  margin-top: 1rem;
}

.doc-card {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  padding: 1.5rem;
  transition: transform 0.2s, box-shadow 0.2s;
}

.doc-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.doc-card h3 {
  margin-top: 0;
  margin-bottom: 0.5rem;
}

.doc-card p {
  margin: 0;
  color: #57606a;
  font-size: 0.9rem;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
  margin: 2rem 0;
}

.stat-card {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 8px;
  padding: 1.5rem;
  text-align: center;
}

.stat-card h3 {
  margin: 0;
  font-size: 2.5rem;
  font-weight: bold;
}

.stat-card p {
  margin: 0.5rem 0 0 0;
  font-size: 0.9rem;
  opacity: 0.9;
}

.footer {
  margin-top: 4rem;
  padding-top: 2rem;
  border-top: 1px solid #d0d7de;
  text-align: center;
  color: #57606a;
}

.footer p {
  margin: 0.5rem 0;
}
</style>
