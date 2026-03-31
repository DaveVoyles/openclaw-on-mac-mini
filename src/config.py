"""
OpenClaw Centralized Configuration

Single source of truth for all config values. Loads from:
  1. config/config.yaml (base defaults)
  2. Environment variables (override YAML values)

Usage:
    from config import cfg
    print(cfg.discord_token)
    print(cfg.llm_model)
"""

import os
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Load YAML base config
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
_CONFIG_YAML_PATH = CONFIG_DIR / "config.yaml"


def _load_yaml() -> dict:
    if _CONFIG_YAML_PATH.exists():
        with open(_CONFIG_YAML_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_yaml = _load_yaml()
_bot = _yaml.get("bot", {})
_llm = _yaml.get("llm", {})
_local_llm = _yaml.get("local_llm", {})
_security = _yaml.get("security", {})
_rate_limits = _llm.get("rate_limits", {})
_conversation = _llm.get("conversation", {})

# ---------------------------------------------------------------------------
# Timeout constants (seconds)
#   from config import TIMEOUT_FAST, TIMEOUT_DEFAULT, TIMEOUT_SLOW, TIMEOUT_LONG
# ---------------------------------------------------------------------------

TIMEOUT_FAST = 5         # Health checks, quick status
TIMEOUT_DEFAULT = 15     # Standard API calls
TIMEOUT_SLOW = 30        # Web scraping, search APIs
TIMEOUT_LONG = 60        # Container operations, LLM calls
TIMEOUT_EXTENDED = 120   # Image generation, research

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
    ollama_model: str = os.getenv("OLLAMA_MODEL", _local_llm.get("model", "gemma3:12b"))
    local_llm_enabled: bool = os.getenv("LOCAL_LLM_ENABLED", str(_local_llm.get("enabled", True))).lower() == "true"
    default_model_preference: str = os.getenv("DEFAULT_MODEL_PREFERENCE", _local_llm.get("default_preference", "auto"))
    ollama_tools_enabled: bool = os.getenv("OLLAMA_TOOLS_ENABLED", str(_local_llm.get("tools_enabled", True))).lower() == "true"

    # -- Copilot Proxy (Phase 8 enhancement) -----------------------------------
    copilot_proxy_url: str = os.getenv("COPILOT_PROXY_URL", "")
    copilot_proxy_enabled: bool = bool(os.getenv("COPILOT_PROXY_URL", ""))

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
    docker_host_ip: str = os.getenv("DOCKER_HOST_IP", "192.168.1.93")

    # -- Network defaults (single source for all hardcoded IPs) ----------------
    nas_host: str = os.getenv("NAS_HOST", "192.168.1.8")
    nas_ip: str = os.getenv("NAS_IP", "192.168.1.8")  # alias for LAN checks

    # -- Overseerr -------------------------------------------------------------
    overseerr_url: str = os.getenv(
        "OVERSEERR_URL",
        f"http://{os.getenv('DOCKER_HOST_IP', '192.168.1.93')}:5055",
    )
    overseerr_api_key: str = os.getenv("OVERSEERR_API_KEY", "")

    # -- Email -----------------------------------------------------------------
    gmail_user: str = os.getenv("GMAIL_USER", "")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    outlook_user: str = os.getenv("OUTLOOK_USER", "")
    outlook_app_password: str = os.getenv("OUTLOOK_APP_PASSWORD", "")

    # -- *arr services ---------------------------------------------------------
    sonarr_url: str = os.getenv("SONARR_URL", f"http://{docker_host_ip}:8989")
    sonarr_api_key: str = os.getenv("SONARR_API_KEY", "")
    radarr_url: str = os.getenv("RADARR_URL", f"http://{docker_host_ip}:7878")
    radarr_api_key: str = os.getenv("RADARR_API_KEY", "")
    lidarr_url: str = os.getenv("LIDARR_URL", f"http://{docker_host_ip}:8686")
    lidarr_api_key: str = os.getenv("LIDARR_API_KEY", "")
    prowlarr_url: str = os.getenv("PROWLARR_URL", f"http://{docker_host_ip}:9696")
    prowlarr_api_key: str = os.getenv("PROWLARR_API_KEY", "")

    # -- Download clients ------------------------------------------------------
    sabnzbd_url: str = os.getenv("SABNZBD_URL", f"http://{docker_host_ip}:8775")
    sabnzbd_api_key: str = os.getenv("SABNZBD_API_KEY", "")
    qbit_url: str = os.getenv("QBIT_URL", f"http://{docker_host_ip}:8080")

    # -- Plex / Tautulli -------------------------------------------------------
    tautulli_url: str = os.getenv("TAUTULLI_URL", f"http://{docker_host_ip}:8181")
    tautulli_api_key: str = os.getenv("TAUTULLI_API_KEY", "")

    # -- Search APIs -----------------------------------------------------------
    perplexity_api_key: str = os.getenv("PERPLEXITY_API_KEY", "")
    firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "")
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # -- Image generation (Stable Diffusion) -----------------------------------
    sd_url: str = os.getenv("SD_URL", "http://host.docker.internal:7861")
    sd_timeout: int = int(os.getenv("SD_TIMEOUT", str(TIMEOUT_EXTENDED)))

    # -- AgentMail -------------------------------------------------------------
    agentmail_api_key: str = os.getenv("AGENTMAIL_API_KEY", "")
    agentmail_inbox: str = os.getenv("AGENTMAIL_INBOX", "")

    # -- Google OAuth (Calendar, Gmail) ----------------------------------------
    google_oauth_client_id: str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    google_oauth_client_secret: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    google_oauth_refresh_token: str = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "")

    # -- Weather ---------------------------------------------------------------
    weather_default_location: str = os.getenv("WEATHER_DEFAULT_LOCATION", "Philadelphia, PA")

    # -- Network testing -------------------------------------------------------
    dns_test_host: str = os.getenv("DNS_TEST_HOST", "8.8.8.8")
    ping_test_host: str = os.getenv("PING_TEST_HOST", "1.1.1.1")

    # -- Copilot proxy token ---------------------------------------------------
    copilot_proxy_token: str = os.getenv("COPILOT_PROXY_TOKEN", "")

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

    # -- Timeouts (seconds) ----------------------------------------------------
    default_timeout: int = TIMEOUT_DEFAULT
    browse_timeout: int = TIMEOUT_DEFAULT
    research_timeout: int = TIMEOUT_EXTENDED

    # -- Validation ------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check config sanity; return list of warning messages."""
        warnings: list[str] = []
        if not self.discord_token:
            warnings.append("discord_token is empty — bot will not connect to Discord")
        if not (1 <= self.health_port <= 65535):
            warnings.append(f"health_port={self.health_port} out of range 1-65535")
        if not (0.0 <= self.llm_temperature <= 2.0):
            warnings.append(f"llm_temperature={self.llm_temperature} out of range 0.0-2.0")
        if self.llm_rpm_limit <= 0:
            warnings.append(f"llm_rpm_limit={self.llm_rpm_limit} must be > 0")
        if not (256 <= self.llm_max_tokens <= 32768):
            warnings.append(f"llm_max_tokens={self.llm_max_tokens} out of range 256-32768")
        if not (1 <= self.llm_max_tool_rounds <= 30):
            warnings.append(f"llm_max_tool_rounds={self.llm_max_tool_rounds} out of range 1-30")
        if not (1 <= self.conversation_ttl_minutes <= 1440):
            warnings.append(f"conversation_ttl_minutes={self.conversation_ttl_minutes} out of range 1-1440")
        return warnings


cfg = _Config()

# Run validation at import time — warn but don't crash
try:
    import logging as _logging

    _log = _logging.getLogger("openclaw.config")
    for _w in cfg.validate():
        _log.warning("Config: %s", _w)
except Exception:
    pass
