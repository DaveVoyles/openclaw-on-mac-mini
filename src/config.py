"""
OpenClaw Centralized Configuration

Single source of truth for all config values. Loads from:
  1. config/config.yaml (base defaults)
  2. Environment variables (override YAML values)

Usage:
    from config import cfg
    log.debug("Discord token configured: %s", "***" if cfg.discord_token else "missing")
    log.debug("LLM model: %s", cfg.llm_model)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Load YAML base config
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
_CONFIG_YAML_PATH = CONFIG_DIR / "config.yaml"


def _load_yaml() -> dict[str, Any]:
    """Load configuration from YAML file.

    Returns:
        Configuration dictionary, or empty dict if file doesn't exist.
    """
    if _CONFIG_YAML_PATH.exists():
        with open(_CONFIG_YAML_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml: dict[str, Any] = _load_yaml()
_bot: dict[str, Any] = _yaml.get("bot", {})
_llm: dict[str, Any] = _yaml.get("llm", {})
_local_llm: dict[str, Any] = _yaml.get("local_llm", {})
_security: dict[str, Any] = _yaml.get("security", {})
_rate_limits: dict[str, Any] = _llm.get("rate_limits", {})
_conversation: dict[str, Any] = _llm.get("conversation", {})
_network: dict[str, Any] = _yaml.get("network", {})
_threads: dict[str, Any] = _yaml.get("threads", {})

# ---------------------------------------------------------------------------
# Timeout constants (seconds)
#   from config import TIMEOUT_FAST, TIMEOUT_DEFAULT, TIMEOUT_SLOW, TIMEOUT_LONG
# ---------------------------------------------------------------------------

TIMEOUT_FAST: int = 5         # Health checks, quick status
TIMEOUT_DEFAULT: int = 15     # Standard API calls
TIMEOUT_SLOW: int = 30        # Web scraping, search APIs
TIMEOUT_LONG: int = 60        # Container operations, LLM calls
TIMEOUT_EXTENDED: int = 120   # Image generation, research

DB_TIMEOUT_DEFAULT: int = 10  # seconds; prevent indefinite SQLite lock waits

# ---------------------------------------------------------------------------
# Config namespace — env vars take precedence over YAML
# ---------------------------------------------------------------------------

class _Config:
    """Read-only config namespace. Env vars override YAML defaults."""

    # -- Discord ---------------------------------------------------------------
    discord_token: str = os.getenv("DISCORD_BOT_TOKEN", "")
    discord_guild_id: str = os.getenv("DISCORD_GUILD_ID", "")
    allowed_user_ids: list[int] = [
        int(uid.strip())
        for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
        if uid.strip()
    ]
    alert_channel_id: int = int(os.getenv("ALERT_CHANNEL_ID", "0"))

    # -- Paths -----------------------------------------------------------------
    config_dir: Path = CONFIG_DIR
    audit_dir: Path = Path(os.getenv("AUDIT_DIR", "/audit"))
    log_dir: Path = Path(os.getenv("LOG_DIR", "/logs"))
    memory_dir: Path = Path(os.getenv("MEMORY_DIR", "/memory"))

    # -- Bot -------------------------------------------------------------------
    bot_name: str = _bot.get("name", "OpenClaw")
    version: str = _yaml.get("version", "0.6.0")
    health_port: int = int(os.getenv("HEALTH_PORT", str(_bot.get("health_port", 8765))))

    # -- LLM (Gemini) ---------------------------------------------------------
    google_api_key: str = os.getenv("GOOGLE_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", _llm.get("primary_model", "gemini-2.5-flash"))
    routing_profile: str = os.getenv("ROUTING_PROFILE", _llm.get("routing_profile", "copilot-first"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", str(_llm.get("max_tokens", 8192))))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", str(_llm.get("temperature", 0.7))))
    llm_rpm_limit: int = int(os.getenv("LLM_RPM_LIMIT", str(_rate_limits.get("per_minute", 60))))
    llm_rph_limit: int = int(os.getenv("LLM_RPH_LIMIT", str(_rate_limits.get("per_hour", 500))))
    llm_max_tool_rounds: int = int(os.getenv("LLM_MAX_TOOL_ROUNDS", str(_llm.get("max_tool_rounds", 12))))
    llm_max_history_turns: int = int(os.getenv("LLM_MAX_HISTORY_TURNS", str(_conversation.get("max_history", 50))))
    conversation_ttl_minutes: int = int(os.getenv("CONVERSATION_TTL_MINUTES", str(_conversation.get("ttl_minutes", 30))))

    # -- Deep / Thinking -------------------------------------------------------
    thinking_model: str = os.getenv("THINKING_MODEL", "gemini-2.5-flash")
    thinking_budget: int = int(os.getenv("THINKING_BUDGET", "8000"))

    # -- Local LLM (Ollama) ----------------------------------------------------
    ollama_url: str = os.getenv("OLLAMA_URL", _local_llm.get("url", "http://host.docker.internal:11434"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", _local_llm.get("model", "gemma4:e4b"))
    local_llm_enabled: bool = os.getenv("LOCAL_LLM_ENABLED", str(_local_llm.get("enabled", True))).lower() == "true"
    default_model_preference: str = os.getenv("DEFAULT_MODEL_PREFERENCE", _local_llm.get("default_preference", "auto"))
    ollama_tools_enabled: bool = os.getenv("OLLAMA_TOOLS_ENABLED", str(_local_llm.get("tools_enabled", True))).lower() == "true"

    # -- Copilot Proxy (Phase 8 enhancement) -----------------------------------
    copilot_proxy_url: str = os.getenv("COPILOT_PROXY_URL", "")
    copilot_proxy_enabled: bool = bool(os.getenv("COPILOT_PROXY_URL", ""))
    dashboard_api_token: str = os.getenv("DASHBOARD_API_TOKEN", "")
    dashboard_api_auth_required: bool = os.getenv("DASHBOARD_API_AUTH_REQUIRED", "true").lower() == "true"
    webhook_require_auth: bool = os.getenv("WEBHOOK_REQUIRE_AUTH", "true").lower() == "true"

    # -- Spending / Budget -----------------------------------------------------
    spending_file: Path = Path(os.getenv("SPENDING_FILE", "/memory/spending.json"))
    gemini_price_input_per_m: float = float(os.getenv("GEMINI_PRICE_INPUT_PER_M", "0.10"))
    gemini_price_output_per_m: float = float(os.getenv("GEMINI_PRICE_OUTPUT_PER_M", "0.40"))
    gemini_budget_limit: float = float(os.getenv("GEMINI_BUDGET_LIMIT", "30.00"))

    # -- NAS -------------------------------------------------------------------
    nas_url: str = os.getenv("NAS_URL", "http://host.docker.internal:19501")
    nas_user: str = os.getenv("NAS_USER", "")
    nas_password: str = os.getenv("NAS_PASSWORD", "")
    nas_verify_ssl: bool = os.getenv("NAS_VERIFY_SSL", "false").lower() == "true"

    # -- Gateway (Maton) -------------------------------------------------------
    maton_api_key: str = os.getenv("MATON_API_KEY", "")

    # -- Docker host -----------------------------------------------------------
    docker_host_ip: str = os.getenv("DOCKER_HOST_IP", _network.get("docker_host_ip", "192.168.1.93"))

    # -- Network defaults (single source for all hardcoded IPs) ----------------
    nas_host: str = os.getenv("NAS_HOST", _network.get("nas_ip", "192.168.1.8"))
    nas_ip: str = os.getenv("NAS_IP", _network.get("nas_ip", "192.168.1.8"))
    nas_ssh_port: int = int(os.getenv("NAS_SSH_PORT", str(_network.get("nas_ssh_port", 24))))
    nas_ssh_user: str = os.getenv("NAS_SSH_USER", _network.get("nas_ssh_user", "dave"))
    plex_port: int = int(os.getenv("PLEX_PORT", str(_network.get("plex_port", 32400))))
    monstervision_port: int = int(os.getenv("MONSTERVISION_PORT", str(_network.get("monstervision_port", 8766))))

    # -- Overseerr -------------------------------------------------------------
    overseerr_url: str = os.getenv("OVERSEERR_URL", f"http://{docker_host_ip}:5055")
    overseerr_api_key: str = os.getenv("OVERSEERR_API_KEY", "")

    # -- Email -----------------------------------------------------------------
    gmail_user: str = os.getenv("GMAIL_USER", "")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    outlook_user: str = os.getenv("OUTLOOK_USER", "")
    outlook_app_password: str = os.getenv("OUTLOOK_APP_PASSWORD", "")

    # -- SMS (Twilio provider layer) -------------------------------------------
    sms_provider: str = os.getenv("SMS_PROVIDER", "twilio")
    twilio_enabled: bool = os.getenv("TWILIO_ENABLED", "false").lower() == "true"
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_number: str = os.getenv("TWILIO_FROM_NUMBER", "")
    twilio_messaging_service_sid: str = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")
    twilio_verify_service_sid: str = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")

    # -- *arr services ---------------------------------------------------------
    sonarr_url: str = os.getenv("SONARR_URL", f"http://{docker_host_ip}:8989")
    sonarr_api_key: str = os.getenv("SONARR_API_KEY", "")
    radarr_url: str = os.getenv("RADARR_URL", f"http://{docker_host_ip}:7878")
    radarr_api_key: str = os.getenv("RADARR_API_KEY", "")
    lidarr_url: str = os.getenv("LIDARR_URL", f"http://{docker_host_ip}:8686")
    lidarr_api_key: str = os.getenv("LIDARR_API_KEY", "")
    prowlarr_url: str = os.getenv("PROWLARR_URL", f"http://{docker_host_ip}:9696")
    prowlarr_api_key: str = os.getenv("PROWLARR_API_KEY", "")

    # -- Download clients (moved to NAS 192.168.1.8 via gluetun) ---------------
    sabnzbd_url: str = os.getenv("SABNZBD_URL", "http://192.168.1.8:8775")
    sabnzbd_api_key: str = os.getenv("SABNZBD_API_KEY", "")
    qbit_url: str = os.getenv("QBIT_URL", "http://192.168.1.8:8080")

    # -- AdGuard Home (NAS) ----------------------------------------------------
    adguard_url: str = os.getenv("ADGUARD_URL", f"http://{nas_ip}:3053")
    adguard_user: str = os.getenv("ADGUARD_USER", "admin")
    adguard_password: str = os.getenv("ADGUARD_PASSWORD", "")

    # -- Uptime Kuma (Mac Mini) ------------------------------------------------
    uptime_kuma_url: str = os.getenv("UPTIME_KUMA_URL", f"http://{docker_host_ip}:3001")
    uptime_kuma_status_slug: str = os.getenv("UPTIME_KUMA_STATUS_SLUG", "main")

    # -- Plex / Tautulli -------------------------------------------------------
    tautulli_url: str = os.getenv("TAUTULLI_URL", f"http://{docker_host_ip}:8181")
    tautulli_api_key: str = os.getenv("TAUTULLI_API_KEY", "")

    # -- OMDb (movie/TV info) --------------------------------------------------
    omdb_api_key: str = os.getenv("OMDB_API_KEY", "")

    # -- Search APIs -----------------------------------------------------------
    perplexity_api_key: str = os.getenv("PERPLEXITY_API_KEY", "")
    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # -- News & Data APIs (Free Tiers) -----------------------------------------
    newsapi_key: str = os.getenv("NEWSAPI_KEY", "")  # Free: 100 req/day
    apisports_key: str = os.getenv("APISPORTS_KEY", "")  # Free: 100 req/day
    alphavantage_key: str = os.getenv("ALPHAVANTAGE_KEY", "")  # Free: 25 req/day

    # -- Trakt.tv (TV & Movie tracking) ----------------------------------------
    trakt_client_id: str = os.getenv("TRAKT_CLIENT_ID", "")
    trakt_client_secret: str = os.getenv("TRAKT_CLIENT_SECRET", "")
    trakt_access_token: str = os.getenv("TRAKT_ACCESS_TOKEN", "")
    trakt_refresh_token: str = os.getenv("TRAKT_REFRESH_TOKEN", "")

    # -- Health & Fitness APIs -------------------------------------------------
    fitbit_client_id: str = os.getenv("FITBIT_CLIENT_ID", "")
    fitbit_client_secret: str = os.getenv("FITBIT_CLIENT_SECRET", "")
    fitbit_access_token: str = os.getenv("FITBIT_ACCESS_TOKEN", "")
    fitbit_refresh_token: str = os.getenv("FITBIT_REFRESH_TOKEN", "")
    openfoodfacts_user_agent: str = os.getenv("OPENFOODFACTS_USER_AGENT", "OpenClaw/0.6.0")
    polygon_api_key: str = os.getenv("POLYGON_API_KEY", "")  # Free: 5 API calls/min

    # -- Image generation (Stable Diffusion) -----------------------------------
    sd_url: str = os.getenv("SD_URL", "http://host.docker.internal:7861")
    sd_timeout: int = int(os.getenv("SD_TIMEOUT", str(TIMEOUT_EXTENDED)))

    # -- Glances (system monitor) ----------------------------------------------
    glances_url: str = os.getenv("GLANCES_URL", "http://host.docker.internal:61208")

    # -- Ntfy (push notifications) ---------------------------------------------
    ntfy_url: str = os.getenv("NTFY_URL", "https://ntfy.sh")
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")
    ntfy_token: str = os.getenv("NTFY_TOKEN", "")  # for self-hosted with auth

    # -- AgentMail -------------------------------------------------------------
    agentmail_api_key: str = os.getenv("AGENTMAIL_API_KEY", "")
    agentmail_inbox: str = os.getenv("AGENTMAIL_INBOX", "")

    # -- Google OAuth (Calendar, Gmail) ----------------------------------------
    google_oauth_client_id: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_oauth_refresh_token: str = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "")

    # -- Weather ---------------------------------------------------------------
    openweather_api_key: str = os.getenv("OPENWEATHER_API_KEY", "")
    weather_default_location: str = os.getenv("WEATHER_DEFAULT_LOCATION", "Philadelphia, PA")

    # -- Network testing -------------------------------------------------------
    dns_test_host: str = os.getenv("DNS_TEST_HOST", "8.8.8.8")
    ping_test_host: str = os.getenv("PING_TEST_HOST", "1.1.1.1")

    # -- Copilot proxy token ---------------------------------------------------
    copilot_proxy_token: str = os.getenv("COPILOT_PROXY_TOKEN", "")

    # -- GitHub ----------------------------------------------------------------
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_default_repos: list[str] = [
        r.strip()
        for r in os.getenv("GITHUB_DEFAULT_REPOS", "").split(",")
        if r.strip()
    ]

    # -- Sentry (error monitoring) ---------------------------------------------
    sentry_auth_token: str = os.getenv("SENTRY_AUTH_TOKEN", "")
    sentry_org: str = os.getenv("SENTRY_ORG", "")
    sentry_url: str = os.getenv("SENTRY_URL", "https://sentry.io")

    # -- Webhook security ------------------------------------------------------
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")

    # -- Response limits -------------------------------------------------------
    response_max_length: int = 4000
    response_truncate_at: int = 3980
    max_file_size: int = 20 * 1024 * 1024  # 20 MB

    # -- Reflection (Phase 7: Self-evaluation) ---------------------------------
    reflection_enabled: bool = os.getenv("REFLECTION_ENABLED", "true").lower() == "true"

    # -- Auto-recall (Phase 1: Auto-RAG) --------------------------------------
    auto_recall_enabled: bool = os.getenv("AUTO_RECALL_ENABLED", str(_yaml.get("vector_store", {}).get("contextual_recall", True))).lower() == "true"
    auto_recall_top_k: int = int(os.getenv("AUTO_RECALL_TOP_K", str(_yaml.get("vector_store", {}).get("contextual_top_k", 3))))

    # -- Thread-based conversations --------------------------------------------
    thread_auto_create: bool = os.getenv("THREAD_AUTO_CREATE", str(_threads.get("auto_create", True))).lower() == "true"
    thread_archive_minutes: int = int(os.getenv("THREAD_ARCHIVE_MINUTES", str(_threads.get("archive_minutes", 60))))
    thread_max_messages: int = int(os.getenv("THREAD_MAX_MESSAGES", str(_threads.get("max_messages", 50))))

    # -- Timeouts (seconds) ----------------------------------------------------
    default_timeout: int = TIMEOUT_DEFAULT
    browse_timeout: int = TIMEOUT_DEFAULT
    research_timeout: int = TIMEOUT_EXTENDED

    # -- Validation ------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check config sanity; return list of warning/error messages."""
        issues: list[str] = []

        # --- Required ---
        if not self.discord_token:
            issues.append("❌ DISCORD_BOT_TOKEN not set — bot cannot start")
        if not self.google_api_key:
            issues.append("⚠️ GOOGLE_API_KEY not set — Gemini LLM unavailable")

        # --- Range checks ---
        if not (1 <= self.health_port <= 65535):
            issues.append(f"❌ health_port={self.health_port} out of range 1-65535")
        if not (0.0 <= self.llm_temperature <= 2.0):
            issues.append(f"⚠️ llm_temperature={self.llm_temperature} out of range 0.0-2.0")
        if self.llm_rpm_limit <= 0:
            issues.append(f"⚠️ llm_rpm_limit={self.llm_rpm_limit} must be > 0")
        if not (256 <= self.llm_max_tokens <= 32768):
            issues.append(f"⚠️ llm_max_tokens={self.llm_max_tokens} out of range 256-32768")
        if not (1 <= self.llm_max_tool_rounds <= 30):
            issues.append(f"⚠️ llm_max_tool_rounds={self.llm_max_tool_rounds} out of range 1-30")
        if not (1 <= self.conversation_ttl_minutes <= 1440):
            issues.append(f"⚠️ conversation_ttl_minutes={self.conversation_ttl_minutes} out of range 1-1440")
        if self.routing_profile not in {"copilot-first", "balanced", "gemini-first", "cost-saver"}:
            issues.append(
                "⚠️ routing_profile="
                f"{self.routing_profile} is not recognized; expected copilot-first, balanced, gemini-first, or cost-saver"
            )

        # --- Optional but recommended ---
        if not self.perplexity_api_key:
            issues.append("ℹ️ PERPLEXITY_API_KEY not set — using Tavily/DDG for search")
        if not self.firecrawl_api_key:
            issues.append("ℹ️ FIRECRAWL_API_KEY not set — Firecrawl search unavailable")
        if not self.nas_url or not self.nas_password:
            issues.append("ℹ️ NAS_URL/NAS_PASSWORD not set — NAS features unavailable")
        if self.twilio_enabled:
            if not self.twilio_account_sid:
                issues.append("❌ TWILIO_ACCOUNT_SID not set — Twilio SMS cannot send")
            if not self.twilio_auth_token:
                issues.append("❌ TWILIO_AUTH_TOKEN not set — Twilio SMS cannot authenticate")
            if not self.twilio_from_number and not self.twilio_messaging_service_sid:
                issues.append("❌ TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID required for Twilio SMS")

        return issues

    def config_status(self) -> list[dict[str, str | bool]]:
        """Return a list of {name, status, detail} dicts for every key API/service.

        Returns:
            List of configuration status dictionaries with name, status, and detail fields.
        """
        entries: list[dict[str, str | bool]] = []

        def _add(name: str, configured: bool, detail: str = "") -> None:
            """Add a configuration status entry."""
            entries.append({
                "name": name,
                "status": "configured" if configured else "missing",
                "detail": detail,
            })

        _add("Discord Bot Token", bool(self.discord_token))
        _add("Google API Key (Gemini)", bool(self.google_api_key))
        _add("Perplexity API Key", bool(self.perplexity_api_key), "Web search fallback to Tavily/DDG")
        _add("Firecrawl API Key", bool(self.firecrawl_api_key), "Web scraping")
        _add("Tavily API Key", bool(self.tavily_api_key), "Web search")
        _add("Serper API Key", bool(self.serper_api_key), "Google search")
        _add("NAS Credentials", bool(self.nas_url and self.nas_password), "Synology NAS")
        _add("Ollama (Local LLM)", self.local_llm_enabled, self.ollama_url)
        _add("Overseerr API Key", bool(self.overseerr_api_key), "Media requests")
        _add("Sonarr API Key", bool(self.sonarr_api_key), "TV show management")
        _add("Radarr API Key", bool(self.radarr_api_key), "Movie management")
        _add("Tautulli API Key", bool(self.tautulli_api_key), "Plex monitoring")
        _add("Gmail Credentials", bool(self.gmail_user and self.gmail_app_password), "Email")
        _add(
            "Twilio SMS",
            bool(
                self.twilio_enabled
                and self.twilio_account_sid
                and self.twilio_auth_token
                and (self.twilio_from_number or self.twilio_messaging_service_sid)
            ),
            "One-tap Discord→SMS",
        )
        _add("Google OAuth", bool(self.google_oauth_client_id and self.google_oauth_refresh_token), "Calendar")
        _add("Copilot Proxy", self.copilot_proxy_enabled, self.copilot_proxy_url or "not set")
        _add("AdGuard Home", bool(self.adguard_url), f"DNS ad blocker — {self.adguard_url}")
        return entries


cfg = _Config()

# Run validation at import time — warn but don't crash
try:
    import logging as _logging

    _log = _logging.getLogger("openclaw.config")
    for _w in cfg.validate():
        _log.warning("Config: %s", _w)
except (ImportError, AttributeError):
    # Logging may not be available during early import; validation runs again on bot startup
    pass
