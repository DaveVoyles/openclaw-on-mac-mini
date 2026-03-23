"""
OpenClaw LLM Integration — Phase 5: Gemini + Function Calling
Manages the Gemini API connection, tool declarations, and chat sessions.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import google.generativeai as genai

from skills import SKILLS

log = logging.getLogger("openclaw.llm")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
MODEL_NAME = os.getenv("LLM_MODEL", "gemini-2.0-flash")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))

# Rate limits (paid tier: 1000 RPM Flash, 50 RPM Pro)
MAX_CALLS_PER_MINUTE = int(os.getenv("LLM_RPM_LIMIT", "60"))
MAX_CALLS_PER_HOUR = int(os.getenv("LLM_RPH_LIMIT", "500"))

# Function-call loop limit (prevent infinite tool invocations)
MAX_TOOL_ROUNDS = 5

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load the system prompt from config/prompts/system.txt."""
    prompt_file = CONFIG_DIR / "prompts" / "system.txt"
    if prompt_file.exists():
        return prompt_file.read_text().strip()
    return (
        "You are OpenClaw, a helpful AI assistant managing a home media server. "
        "Be concise, professional, and use emojis sparingly."
    )


# ---------------------------------------------------------------------------
# Tool / function declarations for Gemini
# ---------------------------------------------------------------------------

# Map skill names → Gemini FunctionDeclarations
_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "list_containers",
        "description": "List all running Docker containers with name, status, and ports.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_container_status",
        "description": "Get detailed status, resource usage, and port mapping for a specific Docker container.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name (e.g. sonarr, radarr, plex, sabnzbd)",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_container_logs",
        "description": "Retrieve the last N lines of logs from a Docker container.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to retrieve (5-100, default 30)",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_docker_stats",
        "description": "Get CPU, memory, and network usage for all running containers.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_system_stats",
        "description": "Get Mac Mini system resource usage (CPU, memory, disk).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_uptime",
        "description": "Get system uptime of the Mac Mini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Phase 5: Advanced Skills --
    {
        "name": "check_arr_health",
        "description": "Check health status of all *arr services (Sonarr, Radarr, Lidarr, Prowlarr).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_download_clients",
        "description": "Check connectivity of download clients (SABnzbd and qBittorrent).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "check_plex_status",
        "description": "Check Plex server status and version via Tautulli.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_media",
        "description": "Search for TV shows or movies across Sonarr and Radarr catalogs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (e.g. 'Breaking Bad', 'The Matrix')",
                },
                "media_type": {
                    "type": "string",
                    "description": "Type filter: 'tv', 'movie', or 'all' (default: all)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_download_queue",
        "description": "Get active downloads from SABnzbd (Usenet) and qBittorrent (torrents).",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_recent_additions",
        "description": "Get recently added media from Plex (via Tautulli).",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent items to return (1-25, default 10)",
                },
            },
        },
    },
    {
        "name": "ping_host",
        "description": "Ping a hostname or IP to check connectivity and latency.",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP address to ping",
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "check_service_ports",
        "description": "Check if all key services are listening on their expected ports.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_status_report",
        "description": "Generate a comprehensive system status report covering all services, downloads, and Plex.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "analyze_logs",
        "description": "Analyze container logs using AI to identify errors, warnings, and suggest fixes.",
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Container name to analyze logs for",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to analyze (10-200, default 50)",
                },
            },
            "required": ["service"],
        },
    },
    # restart_container is intentionally EXCLUDED from LLM tool access.
    # The LLM can suggest a restart, but it must go through the /restart command
    # with proper authorization and policy checks.
]


def _build_tools() -> list:
    """Build the Gemini tools list from declarations."""
    return [genai.protos.Tool(function_declarations=[
        genai.protos.FunctionDeclaration(
            name=d["name"],
            description=d["description"],
            parameters=genai.protos.Schema(**_convert_schema(d["parameters"])),
        )
        for d in _TOOL_DECLARATIONS
    ])]


def _convert_schema(schema: dict) -> dict:
    """Convert a JSON-Schema-style dict to Gemini Schema keyword args."""
    type_map = {
        "object": genai.protos.Type.OBJECT,
        "string": genai.protos.Type.STRING,
        "integer": genai.protos.Type.INTEGER,
        "number": genai.protos.Type.NUMBER,
        "boolean": genai.protos.Type.BOOLEAN,
        "array": genai.protos.Type.ARRAY,
    }
    result: dict[str, Any] = {"type_": type_map.get(schema.get("type", "object"), genai.protos.Type.OBJECT)}

    if "properties" in schema:
        result["properties"] = {
            k: genai.protos.Schema(
                type_=type_map.get(v.get("type", "string"), genai.protos.Type.STRING),
                description=v.get("description", ""),
            )
            for k, v in schema["properties"].items()
        }

    if "required" in schema:
        result["required"] = schema["required"]

    return result


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, per_minute: int = MAX_CALLS_PER_MINUTE, per_hour: int = MAX_CALLS_PER_HOUR):
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._timestamps: list[float] = []

    def check(self) -> bool:
        """Return True if a call is allowed right now."""
        now = time.monotonic()
        # Prune old entries
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        minute_count = sum(1 for t in self._timestamps if now - t < 60)
        hour_count = len(self._timestamps)
        return minute_count < self._per_minute and hour_count < self._per_hour

    def record(self):
        """Record a call."""
        self._timestamps.append(time.monotonic())

    @property
    def remaining_minute(self) -> int:
        now = time.monotonic()
        used = sum(1 for t in self._timestamps if now - t < 60)
        return max(0, self._per_minute - used)

    @property
    def remaining_hour(self) -> int:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return max(0, self._per_hour - len(self._timestamps))


_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Execute a function call from the LLM
# ---------------------------------------------------------------------------


async def _execute_function_call(name: str, args: dict) -> str:
    """Look up and execute a skill by name, returning the string result."""
    skill_fn = SKILLS.get(name)
    if skill_fn is None:
        return f"Unknown function: {name}"

    log.info("LLM invoking skill: %s(%s)", name, args)
    try:
        result = await skill_fn(**args)
        return result
    except Exception as e:
        log.error("Skill %s failed: %s", name, e)
        return f"Error executing {name}: {e}"


# ---------------------------------------------------------------------------
# Main chat interface
# ---------------------------------------------------------------------------

_model: genai.GenerativeModel | None = None


def _get_model() -> genai.GenerativeModel:
    """Lazy-init the Gemini model with tools and system instruction."""
    global _model
    if _model is not None:
        return _model

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")

    genai.configure(api_key=GOOGLE_API_KEY)
    system_prompt = _load_system_prompt()

    _model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=system_prompt,
        tools=_build_tools(),
        generation_config=genai.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        ),
    )
    log.info("Gemini model initialized: %s (temp=%.1f, max_tokens=%d)", MODEL_NAME, TEMPERATURE, MAX_TOKENS)
    return _model


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
) -> tuple[str, list[dict]]:
    """
    Send a message to Gemini, handle function calls, return (response_text, updated_history).

    The history is a list of {"role": "user"|"model", "parts": [str]} dicts
    compatible with Gemini's ChatSession.

    Returns:
      (response_text, updated_history) — the text reply and full conversation history.
    """
    if not _rate_limiter.check():
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history or [],
        )

    model = _get_model()

    # Build Gemini-compatible history
    gemini_history = []
    for msg in (history or []):
        gemini_history.append(
            genai.types.ContentDict(role=msg["role"], parts=msg["parts"])
        )

    chat_session = model.start_chat(history=gemini_history)

    # Send user message (runs in executor to not block the event loop)
    loop = asyncio.get_event_loop()
    _rate_limiter.record()
    response = await loop.run_in_executor(
        None, lambda: chat_session.send_message(user_message)
    )

    # Handle function-call loop
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        # Check if the response contains function calls
        part = response.candidates[0].content.parts[0]
        if not hasattr(part, "function_call") or not part.function_call.name:
            break

        fc = part.function_call
        fn_name = fc.name
        fn_args = dict(fc.args) if fc.args else {}

        log.info("LLM function call [round %d]: %s(%s)", rounds + 1, fn_name, fn_args)

        # Execute the skill
        result_str = await _execute_function_call(fn_name, fn_args)

        # Send function result back to the model
        _rate_limiter.record()
        if not _rate_limiter.check():
            return (
                "⚠️ Rate limit reached during function execution. Partial result:\n" + result_str,
                _extract_history(chat_session),
            )

        response = await loop.run_in_executor(
            None,
            lambda result=result_str, name=fn_name: chat_session.send_message(
                genai.protos.Content(
                    parts=[genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=name,
                            response={"result": result},
                        )
                    )]
                )
            ),
        )
        rounds += 1

    # Extract final text
    try:
        text = response.text
    except (AttributeError, ValueError):
        text = "I processed your request but couldn't generate a text response."

    # Build updated history
    updated_history = _extract_history(chat_session)

    return text, updated_history


def _extract_history(chat_session) -> list[dict]:
    """Convert a ChatSession's history to our serializable format."""
    history = []
    for content in chat_session.history:
        parts = []
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
            elif hasattr(part, "function_call") and part.function_call.name:
                parts.append(f"[Called {part.function_call.name}]")
            elif hasattr(part, "function_response") and part.function_response.name:
                parts.append(f"[Result from {part.function_response.name}]")
        if parts:
            history.append({"role": content.role, "parts": parts})
    return history


# ---------------------------------------------------------------------------
# Convenience: check if LLM is configured
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    """Return True if a Google API key is set."""
    return bool(GOOGLE_API_KEY)


def get_rate_info() -> str:
    """Return a human-readable rate limit status."""
    return f"{_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining"
