# OpenClaw — Scripts Directory Index

All scripts in `scripts/`. Run from the repository root unless noted otherwise.

---

## Quick Reference Table

| Script | Language | Purpose | Safe locally? |
|--------|----------|---------|---------------|
| `install_openclaw_cli_remote.sh` | Bash | Push CLI files to a remote Mac via SSH+SCP | ✅ Yes |
| `uninstall_openclaw_cli_remote.sh` | Bash | Remove standalone CLI from a remote Mac via SSH | ✅ Yes |
| `setup_openclaw_cli_mac.sh` | Bash | Configure local Mac shell for CLI usage (PATH, exports, aliases) | ✅ Yes |
| `enable_openclaw_cli_remote_mac.sh` | Bash | Configure a remote Mac's shell for CLI usage | ✅ Yes |
| `aliases.sh` | Bash | Shell aliases (`openclaw`, `oc-*`). Source in `.zshrc` | ✅ Yes (source only) |
| `build-and-verify.sh` | Bash | Build Docker image + run post-deploy smoke tests | ⚠️ Rebuilds container |
| `release.sh` | Bash | Create and push a new release tag | ⚠️ Pushes to git |
| `health-check.sh` | Bash | Check container health, endpoint liveness | ✅ Yes (read-only) |
| `backup_restore.sh` | Bash | Backup/restore persistent data and config | ⚠️ Touches data dirs |
| `post_deploy_test.py` | Python | Post-deploy smoke test (infra + Discord + /ask) | ⚠️ Needs live server |
| `provider_smoke_test.py` | Python | Quick LLM provider health check (all or selected) | ⚠️ Consumes API tokens |
| `syntax_check.py` | Python | Check Python syntax for all `src/` files | ✅ Yes |
| `create_plugin.py` | Python | Interactive plugin generator from templates | ✅ Yes |
| `routing_recommender.py` | Python | Parse `data/routing_audit.jsonl` → recommend routing profiles | ✅ Yes (read-only) |
| `telemetry_summary.py` | Python | Summarize routing telemetry from `data/routing_audit.jsonl` | ✅ Yes (read-only) |
| `telemetry_alert.py` | Python | Alert if provider success/latency degrades | ✅ Yes (read-only) |
| `backfill_vectors.py` | Python | One-time backfill of memories into ChromaDB (idempotent) | ⚠️ Runs inside Docker |
| `add-uptime-kuma-monitor.py` | Python | Register OpenClaw in Uptime Kuma monitoring | ⚠️ Writes to kuma.db |
| `google_oauth_setup.py` | Python | One-time Google OAuth2 flow for Calendar/Gmail | ⚠️ Writes credentials |
| `nas_proxy.py` | Python | NAS proxy server (Mac Mini host → Synology NAS) | ⚠️ Starts a server |
| `sd_server.py` | Python | Stable Diffusion HTTP server for Apple Silicon | ⚠️ Starts a server |
| `pre-commit-setup.sh` | Bash | Install and configure pre-commit hooks | ✅ Yes |
| `test_agentmail.py` | Python | AgentMail API probe (dev utility) | ⚠️ Contains hardcoded key |
| `test_gemini.py` | Python | Manual Gemini API smoke test | ⚠️ Consumes API tokens |
| `openclaw_cli.py` | Python | Standalone CLI launcher shim (used by `aliases.sh`) | ✅ Yes |
| `com.openclaw.nasproxy.plist` | plist | launchd plist for NAS proxy daemon (macOS) | ⚠️ System service |
| `com.user.delayprotonlaunch.plist` | plist | launchd plist to delay Proton app launch | ⚠️ System service |
| `com.user.delayprotonvpn.plist` | plist | launchd plist to delay ProtonVPN launch | ⚠️ System service |
| `delay_proton_launch.sh` | Bash | Delay helper for Proton app startup | ✅ Yes |
| `delay_proton_vpn.sh` | Bash | Delay helper for ProtonVPN startup | ✅ Yes |

---

## Grouped by Category

### Deployment

| Script | When to Use |
|--------|-------------|
| `install_openclaw_cli_remote.sh` | Push CLI to macbook or any remote Mac. Usage: `bash scripts/install_openclaw_cli_remote.sh [user@]host [url]` |
| `uninstall_openclaw_cli_remote.sh` | Remove CLI from a remote Mac. Usage: `bash scripts/uninstall_openclaw_cli_remote.sh [user@]host` |
| `build-and-verify.sh` | Full container rebuild + smoke tests after a significant change |
| `release.sh` | Create a release tag and push. Run from the Mac Mini after merging. |
| `backfill_vectors.py` | Run once inside Docker after deploying ChromaDB support: `docker exec openclaw python scripts/backfill_vectors.py` |

### Testing and Smoke Tests

| Script | When to Use |
|--------|-------------|
| `post_deploy_test.py` | After `docker compose up -d --build` to verify the bot is healthy |
| `provider_smoke_test.py` | Verify all LLM providers are responding. `python scripts/provider_smoke_test.py --providers copilot,ollama` |
| `syntax_check.py` | Quick pre-commit syntax validation. `python scripts/syntax_check.py` |
| `health-check.sh` | Check container running + HTTP health endpoint |
| `test_gemini.py` | Manual Gemini API test (dev only) |

### Setup and Configuration

| Script | When to Use |
|--------|-------------|
| `setup_openclaw_cli_mac.sh` | First-time CLI setup on a local Mac (exports, PATH, aliases) |
| `enable_openclaw_cli_remote_mac.sh` | First-time CLI setup on a remote Mac |
| `aliases.sh` | Source in `.zshrc`: `source ~/openclaw/scripts/aliases.sh` |
| `pre-commit-setup.sh` | Install pre-commit hooks on a fresh clone |
| `google_oauth_setup.py` | One-time Google OAuth2 credential setup for Calendar/Gmail integration |
| `add-uptime-kuma-monitor.py` | Register the service in Uptime Kuma (run once per instance) |

### Maintenance

| Script | When to Use |
|--------|-------------|
| `backup_restore.sh` | Backup: `./scripts/backup_restore.sh backup`. Restore: `./scripts/backup_restore.sh restore <archive>` |
| `telemetry_summary.py` | Review routing telemetry: `python scripts/telemetry_summary.py --last 100` |
| `telemetry_alert.py` | CI-friendly latency/success check: exits non-zero on degradation |
| `routing_recommender.py` | Emit routing profile recommendations from audit log |

### Dev Utilities

| Script | When to Use |
|--------|-------------|
| `create_plugin.py` | Interactive scaffolding for new plugins |
| `nas_proxy.py` | Run NAS proxy on Mac Mini host (outside Docker) when Docker networking can't reach the NAS |
| `sd_server.py` | Local Stable Diffusion server using Apple Silicon MPS |
| `openclaw_cli.py` | CLI shim invoked by `aliases.sh` — not meant to be run directly |

### System Services (macOS launchd)

These `.plist` files are loaded into launchd (`launchctl load`) and should only be installed once per machine:

| File | Purpose |
|------|---------|
| `com.openclaw.nasproxy.plist` | Keep `nas_proxy.py` running as a background daemon |
| `com.user.delayprotonlaunch.plist` | Delay Proton Mail launch at login |
| `com.user.delayprotonvpn.plist` | Delay ProtonVPN launch at login |
