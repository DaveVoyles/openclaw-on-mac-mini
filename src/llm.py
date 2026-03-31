"""
OpenClaw LLM Integration — Phase 5: Gemini + Function Calling
Manages the Gemini API connection, tool declarations, and chat sessions.

Hybrid routing (auto mode):
  - Copilot proxy (GPT-4o via local proxy)  → FREE, tried first
  - Gemini 2.0 Flash                        → cheap backup, full tool support
  - Ollama                                   → only when explicitly requested via /ask model:local
"""

import asyncio
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory import Conversation

import aiohttp
from google import genai

from config import cfg
from http_session import SessionManager
from skills import SKILLS
from spending import tracker as spending_tracker

log = logging.getLogger("openclaw.llm")


def _to_content(msg: dict) -> dict:
    """Convert internal history message to genai-compatible ContentDict.

    Internal history stores parts as plain strings, but the google-genai SDK
    requires Part objects (dicts with 'text' key).
    """
    parts = []
    for p in msg.get("parts", []):
        if isinstance(p, str):
            parts.append({"text": p})
        elif isinstance(p, dict):
            parts.append(p)
        else:
            parts.append({"text": str(p)})
    return {"role": msg["role"], "parts": parts}

# ---------------------------------------------------------------------------
# Configuration (sourced from centralized config)
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = cfg.google_api_key
_client: genai.Client | None = None
if GOOGLE_API_KEY:
    _client = genai.Client(api_key=GOOGLE_API_KEY)
MODEL_NAME = cfg.llm_model
MAX_TOKENS = cfg.llm_max_tokens
TEMPERATURE = cfg.llm_temperature
CONFIG_DIR = cfg.config_dir

# Local LLM (Ollama) settings
OLLAMA_URL = cfg.ollama_url
OLLAMA_MODEL = cfg.ollama_model
LOCAL_LLM_ENABLED = cfg.local_llm_enabled

# Deep / thinking mode — used for /research and multi-step synthesis
THINKING_MODEL = cfg.thinking_model
THINKING_BUDGET = cfg.thinking_budget

# Rate limits (paid tier: 1000 RPM Flash, 50 RPM Pro)
MAX_CALLS_PER_MINUTE = cfg.llm_rpm_limit
MAX_CALLS_PER_HOUR = cfg.llm_rph_limit

# Function-call loop limit (prevent infinite tool invocations)
MAX_TOOL_ROUNDS = cfg.llm_max_tool_rounds

# ---------------------------------------------------------------------------
# System prompt (cached with mtime-based invalidation)
# ---------------------------------------------------------------------------

_system_prompt_cache: str | None = None
_system_prompt_mtime: float = 0.0
_system_prompt_lock = threading.Lock()


def _load_system_prompt() -> str:
    """Load the system prompt from config/prompts/system.txt with mtime cache."""
    global _system_prompt_cache, _system_prompt_mtime
    prompt_file = CONFIG_DIR / "prompts" / "system.txt"
    try:
        current_mtime = prompt_file.stat().st_mtime if prompt_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    with _system_prompt_lock:
        if _system_prompt_cache is not None and current_mtime == _system_prompt_mtime:
            return _system_prompt_cache
        if prompt_file.exists():
            _system_prompt_cache = prompt_file.read_text().strip()
        else:
            _system_prompt_cache = (
                "You are OpenClaw, a helpful AI assistant managing a home media server. "
                "Be concise, professional, and use emojis sparingly."
            )
        _system_prompt_mtime = current_mtime
        return _system_prompt_cache


# ---------------------------------------------------------------------------
# Tool / function declarations for Gemini
# ---------------------------------------------------------------------------

# Map skill names → Gemini FunctionDeclarations
def _load_tool_declarations() -> list[dict[str, Any]]:
    """Load tool declarations from config/tools.yaml."""
    tools_file = Path(os.getenv("TOOLS_CONFIG", "config/tools.yaml"))
    if not tools_file.exists():
        # Fallback: try relative to this file's parent (Docker layout)
        tools_file = Path(__file__).resolve().parent.parent / "config" / "tools.yaml"
    if not tools_file.exists():
        log.error("tools.yaml not found — no tools will be available")
        return []
    import yaml
    with open(tools_file) as f:
        declarations = yaml.safe_load(f)
    log.info("Loaded %d tool declarations from %s", len(declarations), tools_file)
    return declarations


_TOOL_DECLARATIONS: list[dict[str, Any]] = _load_tool_declarations()

# ---------------------------------------------------------------------------
# Ollama — local LLM for simple / conversational queries
# ---------------------------------------------------------------------------

# Shared aiohttp session for all Ollama requests (avoids per-request TCP handshakes)
_ollama_sessions = SessionManager(
    timeout=10, name="ollama",
    connector_limit=10, connector_limit_per_host=5,
)


async def _get_ollama_session() -> aiohttp.ClientSession:
    """Return the shared Ollama aiohttp session, (re)creating if closed."""
    return await _ollama_sessions.get()


# ---------------------------------------------------------------------------
# Routing heuristics — decide whether to use Gemma (local) or Gemini
# ---------------------------------------------------------------------------
import re as _re

# Tier 1 — Route DIRECTLY to Gemini.
# These are imperative action+noun combos that require live tool execution.
# Gemma has no tools, so these would produce hallucinations or refusals.
_LIVE_ACTION_PATTERN = _re.compile(
    # Container / service control verbs
    r"\b(restart|reboot|stop|start|kill)\b.{0,40}\b(container|service|plex|sonarr|radarr|lidarr|sabnzbd|qbittorrent|prowlarr|jellyfin)\b"
    # Requests for live system data
    r"|\b(show|list|get|check|pull|view)\b.{0,40}\b(log|stats?|status|health|container|queue|request|download|backup|alert|metric)\b"
    # Explicit web-search actions
    r"|\b(search|find|look\s+up)\b.{0,40}\b(web|online|house|home|listing|property|zillow|redfin|real[\s-]?estate|news|current\s+price|weather)\b"
    # "Search <domain>" or "search for <topic>" — any search verb + domain or broad topic
    r"|\b(search|find|look\s+up)\b.{0,40}\w+\.(com|org|net|io|edu)\b"
    r"|\b(search|find|look\s+up|locate)\b.{0,20}\b(for|about|in|on)\b"
    # NAS / folder / file browsing — requires tool to list folders
    r"|\b(browse|list|show|find|locate|look)\b.{0,40}\b(folders?|director(?:y|ies)|audiobooks?|ebooks?|files?|nas|shares?)\b"
    r"|\b(what|which|do\s+we\s+have)\b.{0,30}\b(folders?|audiobooks?|books?|files?)\b"
    # "check other sites", "try other sources" — contextual web requests
    r"|\b(check|try)\b.{0,20}\b(other|more|different)\b.{0,20}\b(site|source|page|place|link)\b"
    # Weather: any standalone weather request routes through Gemini (needs get_weather tool)
    r"|\b(weather|forecast|temperature|rain|snow|sunny|humidity|wind\s+speed)\b"
    # Live-data questions: "is plex up?", "what's the current…"
    r"|\bis\s+(the\s+)?(server|plex|sonarr|radarr|nas|docker)\s+(up|running|online|working|down)\b"
    r"|\bwhat'?s?\s+(?:the\s+)?(?:current|latest|running)\b.{0,50}\b(status|usage|queue|activity)\b"
    # Approvals, sends, creates, schedules
    r"|\b(approve|deny)\b.{0,20}\b(request|id)\b"
    r"|\bsend\b.{0,20}\b(email|mail)\b"
    r"|\bcreate\b.{0,30}\b(task|event|entity|connection|calendar)\b"
    r"|\bschedule\b.{0,30}\b(task|report|research|job|recurring|weekly|daily|monthly)\b"
    # Diagnostics / jobs
    r"|\brun\b.{0,20}\b(speed\s+test|status\s+report|ping|backup|diagnostic)\b"
    r"|\bping\s+[\w.]+"
    # URLs always need browse_url (full URLs or bare domain names)
    r"|https?://"
    r"|\b\w+\.(com|org|net|io|edu)\b",
    _re.IGNORECASE,
)

# Tier 2 — Well-known domains where Gemma consistently fabricates answers.
# These are proper nouns tied to live services or specialised data sources.
_GEMMA_WEAK_DOMAINS = _re.compile(
    r"\b(zillow|redfin|trulia|narberth|upper\s+darby|maton|tailscale|tautulli"
    r"|overseerr|prowlarr|sabnzbd|synology|hyper\s+backup|ontology"
    r"|audiobooks?|ebooks?|nas\b.{0,20}(folder|share|storage)|filestation)\b",
    _re.IGNORECASE,
)


def _needs_tools(message: str) -> bool:
    """Return True if the query requires live tool execution and should bypass Gemma."""
    return bool(_LIVE_ACTION_PATTERN.search(message) or _GEMMA_WEAK_DOMAINS.search(message))


# Compiled patterns that signal Gemma is pretending to call tools it doesn't have.
# Any match in Gemma's response triggers an automatic fallback to Gemini.
_VAGUE_RESPONSE_RE = _re.compile(
    r"i'?m\s+not\s+sure"
    r"|\bi\s+don'?t\s+have\s+specific\b"
    r"|\bi\s+couldn'?t\s+find\b"
    r"|\bi\s+don'?t\s+have\s+access\s+to\s+real[\s-]?time\b"
    r"|\bmy\s+training\s+data\b"
    r"|\bmy\s+knowledge\s+cutoff\b"
    r"|\bi\s+recommend\s+checking\b"
    r"|\byou\s+might\s+want\s+to\s+search\b",
    _re.IGNORECASE,
)

_FACTUAL_QUESTION_RE = _re.compile(
    r"^(who|what|when|where|how|is|are|was|were|did|does|do|can|could|will|has|have)\b",
    _re.IGNORECASE,
)

_GEMMA_HALLUCINATION_RE = _re.compile(
    r"(i'?m?\s+)?(now\s+)?(searching|browsing|checking|fetching|looking\s+up)\b"
    r"|\b(let\s+me\s+)?(search|check|look\s+that\s+up|fetch)\s+(that|the|for)\b"
    r"|(checking|querying)\s+(zillow|redfin|the\s+server|docker|container|plex)\b"
    r"|\b(i\s+)?(don'?t|cannot|can'?t)\s+(access|browse|check|reach)\s+(the\s+)?(internet|web|real[\s-]?time|live|current)\b"
    r"|\b(as\s+an?\s+ai|as\s+a\s+language\s+model)\b.{0,80}\b(cannot|don'?t|no\s+access)\b"
    r"|\bi\s+don'?t\s+have\s+(real[\s-]?time|access\s+to|live)\b"
    r"|(would\s+need\s+to\s+|i\s+could\s+)?(search|check|query)\s+(this|that|it)\s+for\s+you\b"
    # Promises to do something without actually doing it
    r"|\b(let\s+me\s+)(start|begin|check|search|look|find|locate)\b"
    r"|\bone\s+moment\b"
    r"|\bi'?ll\s+(search|check|look|find|locate|browse|start)\b",
    _re.IGNORECASE,
)


def _gemma_response_seems_valid(reply: str) -> bool:
    """Return True if the Gemma response is genuine and not a tool-use hallucination."""
    if len(reply.strip()) < 10:
        return False
    return not bool(_GEMMA_HALLUCINATION_RE.search(reply))


async def _ollama_available() -> bool:
    """Return True if Ollama is reachable and the model is loaded."""
    try:
        session = await _get_ollama_session()
        async with session.get(
            f"{OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=3)
        ) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return any(OLLAMA_MODEL.split(":")[0] in m for m in models)
    except Exception as exc:
        log.debug("Ollama availability check failed: %s", exc)
        return False
async def _chat_ollama(
    user_message: str,
    history: list[dict],
    system_prompt: str,
) -> str | None:
    """
    Send a message to Ollama's /api/chat endpoint.
    Returns the response text, or None on failure.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:  # keep last 10 turns for context
        role = msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_predict": MAX_TOKENS},
    }

    try:
        session = await _get_ollama_session()
        async with session.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                log.warning("Ollama returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            return data.get("message", {}).get("content") or None
    except asyncio.TimeoutError:
        log.warning("Ollama request timed out")
        return None
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return None


def _build_tools() -> list:
    """Build the Gemini tools list from declarations."""
    return [genai.types.Tool(function_declarations=[
        genai.types.FunctionDeclaration(
            name=d["name"],
            description=d["description"],
            parameters=genai.types.Schema(**_convert_schema(d["parameters"])),
        )
        for d in _TOOL_DECLARATIONS
    ])]


def _convert_schema(schema: dict) -> dict:
    """Convert a JSON-Schema-style dict to Gemini Schema keyword args."""
    type_map = {
        "object": genai.types.Type.OBJECT,
        "string": genai.types.Type.STRING,
        "integer": genai.types.Type.INTEGER,
        "number": genai.types.Type.NUMBER,
        "boolean": genai.types.Type.BOOLEAN,
        "array": genai.types.Type.ARRAY,
    }
    result: dict[str, Any] = {"type": type_map.get(schema.get("type", "object"), genai.types.Type.OBJECT)}

    if "properties" in schema:
        result["properties"] = {
            k: genai.types.Schema(
                type=type_map.get(v.get("type", "string"), genai.types.Type.STRING),
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
    """Sliding-window rate limiter with jittered backoff for concurrent callers."""

    def __init__(self, per_minute: int = MAX_CALLS_PER_MINUTE, per_hour: int = MAX_CALLS_PER_HOUR):
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._sync_lock = __import__("threading").Lock()

    def _evict(self) -> None:
        """Drop timestamps older than 1 hour from the front of the deque."""
        cutoff = time.monotonic() - 3600
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def check(self) -> bool:
        """Return True if a call is allowed right now (thread-safe)."""
        with self._sync_lock:
            self._evict()
            now = time.monotonic()
            minute_count = sum(1 for t in self._timestamps if now - t < 60)
            hour_count = len(self._timestamps)
            return minute_count < self._per_minute and hour_count < self._per_hour

    def record(self):
        """Record a call (thread-safe)."""
        with self._sync_lock:
            self._timestamps.append(time.monotonic())

    async def wait_for_capacity(self, max_wait: float = 30.0) -> bool:
        """Wait with jittered exponential backoff until capacity is available.

        Returns True if capacity was acquired, False if max_wait exceeded.
        Uses a lock to prevent thundering-herd: only one caller backs off at a time.
        """
        import random
        backoff = 1.0
        waited = 0.0
        async with self._lock:
            while not self.check():
                if waited >= max_wait:
                    return False
                jitter = random.uniform(0.8, 1.2)
                sleep_time = min(backoff * jitter, max_wait - waited)
                log.info("Rate limiter: backing off %.1fs (waited %.1fs)", sleep_time, waited)
                await asyncio.sleep(sleep_time)
                waited += sleep_time
                backoff = min(backoff * 2, 15.0)  # cap at 15s
        return True

    @property
    def remaining_minute(self) -> int:
        now = time.monotonic()
        used = sum(1 for t in self._timestamps if now - t < 60)
        return max(0, self._per_minute - used)

    @property
    def remaining_hour(self) -> int:
        self._evict()
        return max(0, self._per_hour - len(self._timestamps))


_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Tool result TTL cache — avoid redundant calls for read-only snapshot tools
# ---------------------------------------------------------------------------

# Tools whose results are safe to cache within a short window.
# These don't change faster than 30 seconds and are frequently chained together.
_CACHEABLE_TOOLS: frozenset[str] = frozenset({
    "get_system_stats",
    "get_docker_stats",
    "get_nas_storage_health",
    "get_nas_alerts",
    "get_disk_smart_status",
    "get_backup_status",
    "get_uptime",
    "check_arr_health",
    "check_download_clients",
    "check_plex_status",
    "get_plex_activity",
    "get_network_status",
    "get_tailscale_status",
})
_TOOL_CACHE_TTL = 30  # seconds
_TOOL_CACHE_MAX_SIZE = 256

# {"tool_name|arg_hash": (result, timestamp)}
_tool_cache: dict[str, tuple[str, float]] = {}


def _cache_key(name: str, args: dict) -> str:
    import hashlib
    return f"{name}|{hashlib.md5(str(sorted(args.items())).encode()).hexdigest()[:8]}"


def _evict_tool_cache() -> None:
    """Evict expired entries; if still over max, drop oldest."""
    now = time.monotonic()
    expired = [k for k, (_, ts) in _tool_cache.items() if now - ts >= _TOOL_CACHE_TTL]
    for k in expired:
        del _tool_cache[k]
    while len(_tool_cache) > _TOOL_CACHE_MAX_SIZE:
        oldest_key = min(_tool_cache, key=lambda k: _tool_cache[k][1])
        del _tool_cache[oldest_key]


# ---------------------------------------------------------------------------
# Execute a function call from the LLM
# ---------------------------------------------------------------------------


async def _execute_function_call(name: str, args: dict) -> str:
    """Look up and execute a skill by name, returning the string result."""
    from tool_health import circuit_breaker, tool_health

    skill_fn = SKILLS.get(name)
    if skill_fn is None:
        return f"Unknown function: {name}"

    # Circuit breaker: fast-fail on repeatedly broken tools
    if circuit_breaker.is_open(name):
        return f"⚠️ {name} is temporarily unavailable (circuit open — recent failures). Try an alternative approach."

    # Return cached result for read-only snapshot tools if still fresh
    if name in _CACHEABLE_TOOLS:
        key = _cache_key(name, args)
        if key in _tool_cache:
            cached_result, cached_at = _tool_cache[key]
            if time.monotonic() - cached_at < _TOOL_CACHE_TTL:
                log.debug("Returning cached result for %s (age: %.1fs)", name, time.monotonic() - cached_at)
                return cached_result

    log.info("LLM invoking skill: %s(%s)", name, args)
    try:
        result = await skill_fn(**args)
        if not isinstance(result, str):
            result = str(result)
        if name in _CACHEABLE_TOOLS:
            _tool_cache[_cache_key(name, args)] = (result, time.monotonic())
            _evict_tool_cache()
        circuit_breaker.record_success(name)
        tool_health.record(name, success=True)
        return result
    except Exception as e:
        log.error("Skill %s failed: %s", name, e)
        circuit_breaker.record_failure(name)
        tool_health.record(name, success=False)
        return f"Error executing {name}: {e}"


# ---------------------------------------------------------------------------
# Main chat interface
# ---------------------------------------------------------------------------

import dataclasses


@dataclasses.dataclass
class _ModelConfig:
    """Holds model name + generation config (replaces old GenerativeModel)."""
    model_name: str
    config: genai.types.GenerateContentConfig


_model: _ModelConfig | None = None


_model_system_prompt: str | None = None
_model_lock: asyncio.Lock | None = None


def _init_gemini_model(
    model_name: str,
    *,
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_TOKENS,
    thinking_budget: int | None = None,
    with_tools: bool = True,
) -> _ModelConfig:
    """Create a _ModelConfig holding model name + generation config."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")

    config_kwargs: dict[str, Any] = {
        "system_instruction": _load_system_prompt(),
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }

    if with_tools:
        config_kwargs["tools"] = _build_tools()

    if thinking_budget is not None:
        config_kwargs["thinking_config"] = genai.types.ThinkingConfig(
            thinking_budget=thinking_budget,
        )
        log.info("ThinkingConfig enabled (budget=%d tokens)", thinking_budget)

    return _ModelConfig(
        model_name=model_name,
        config=genai.types.GenerateContentConfig(**config_kwargs),
    )


async def _get_model() -> _ModelConfig:
    """Lazy-init the Gemini model config; reloads when system prompt changes."""
    global _model, _model_system_prompt, _model_lock
    if _model_lock is None:
        _model_lock = asyncio.Lock()
    async with _model_lock:
        system_prompt = _load_system_prompt()
        if _model is not None and _model_system_prompt == system_prompt:
            return _model

        _model = _init_gemini_model(MODEL_NAME, temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        _model_system_prompt = system_prompt
        log.info("Gemini model initialized: %s (temp=%.1f, max_tokens=%d)", MODEL_NAME, TEMPERATURE, MAX_TOKENS)
        return _model


# ---------------------------------------------------------------------------
# Shared tool-calling loop (used by chat and chat_deep)
# ---------------------------------------------------------------------------

async def _run_tool_loop(
    chat_session,
    response,
    *,
    max_rounds: int = MAX_TOOL_ROUNDS,
    on_tool_call: Any | None = None,
    parallel: bool = True,
    label: str = "LLM",
) -> tuple[Any, int]:
    """Execute the function-call loop on *chat_session*.

    Returns ``(final_response, rounds_executed)``.

    When *parallel* is True (default for normal chat), all function_call
    parts in a single response are gathered concurrently.  When False
    (deep research), only the first function_call part is executed per
    round — matching the sequential research pattern that's easier to
    follow in Discord progress updates.
    """
    loop = asyncio.get_running_loop()
    rounds = 0

    while rounds < max_rounds:
        # Collect function_call parts from this response
        try:
            all_parts = response.candidates[0].content.parts
        except (IndexError, AttributeError):
            break

        function_calls = [
            (part.function_call.name, dict(part.function_call.args) if part.function_call.args else {})
            for part in all_parts
            if hasattr(part, "function_call") and part.function_call and part.function_call.name
        ]

        if not function_calls:
            break

        # In sequential mode, process only the first call per round
        if not parallel:
            function_calls = function_calls[:1]

        log.info("%s function call(s) [round %d]: %s", label, rounds + 1,
                 ", ".join(f"{n}({a})" for n, a in function_calls))

        # Fire progress callbacks (before execution — with args)
        if on_tool_call:
            for fn_name, fn_args in function_calls:
                try:
                    await on_tool_call(fn_name, rounds + 1, args=fn_args)
                except Exception as exc:
                    log.debug("on_tool_call callback failed: %s", exc)

        # Execute tool calls
        results = await asyncio.gather(*[
            _execute_function_call(fn_name, fn_args)
            for fn_name, fn_args in function_calls
        ])

        # Fire progress callbacks (after execution — with result preview)
        if on_tool_call:
            for (fn_name, _), result in zip(function_calls, results):
                try:
                    await on_tool_call(fn_name, rounds + 1, result_preview=result[:200])
                except Exception as exc:
                    log.debug("on_tool_call result callback failed: %s", exc)

        # Rate-limit check before sending results back
        _rate_limiter.record()
        if not _rate_limiter.check():
            # Build a fake text-only response — caller handles this
            return response, rounds + 1

        # Send all function results back to the model
        response_parts = [
            genai.types.Part(
                function_response=genai.types.FunctionResponse(
                    name=fn_name,
                    response={"result": result},
                )
            )
            for (fn_name, _), result in zip(function_calls, results)
        ]

        response = await loop.run_in_executor(
            None,
            lambda parts=response_parts: chat_session.send_message(parts),
        )
        await _record_usage(response)
        rounds += 1

    return response, rounds


# ---------------------------------------------------------------------------
# chat() helper decomposition
# ---------------------------------------------------------------------------

# Per-model context limits — leave ~20% headroom for tools + response
_CONTEXT_LIMITS = {
    "gemini": {"max_turns": 50, "max_chars": 500_000},   # Gemini supports 1M+ tokens
    "ollama": {"max_turns": 40, "max_chars": 400_000},    # Gemma3 supports 128K tokens
    "default": {"max_turns": 20, "max_chars": 80_000},    # Conservative fallback
}


def _get_context_limits(model_hint: str = "default") -> tuple[int, int]:
    """Return (max_turns, max_chars) for the given model."""
    limits = _CONTEXT_LIMITS.get(model_hint, _CONTEXT_LIMITS["default"])
    return limits["max_turns"], limits["max_chars"]


def _estimate_chars(history: list[dict]) -> int:
    """Rough character count of conversation history."""
    total = 0
    for msg in history:
        for p in msg.get("parts", []):
            if isinstance(p, str):
                total += len(p)
    return total


async def _trim_history(
    history: list[dict],
    model_hint: str = "default",
    *,
    conversation: "Conversation | None" = None,
) -> list[dict]:
    """Keep first 2 turns (persona context) + last N to avoid context overflow.

    *model_hint* controls the context limits: 'gemini' for generous limits,
    'ollama' for Gemma3's 128K window, or 'default' for conservative fallback.

    When the history has 40+ turns and hasn't been summarized yet in this
    session, the oldest 20 non-system turns (indices 2–21) are replaced with
    a single model turn containing a bullet-point summary.  If summarization
    fails, falls back to the original drop behaviour.

    If the history still exceeds the character limit after turn trimming,
    progressively drop older turns until it fits.
    """
    max_turns, max_chars = _get_context_limits(model_hint)

    # --- Auto-summarize before dropping turns ---
    should_summarize = (
        len(history) >= 40
        and conversation is not None
        and not conversation.summarized
        and GOOGLE_API_KEY
    )

    if should_summarize:
        original_len = len(history)
        # Turns 2-21 (20 turns) are candidates for summarization
        summarize_end = min(22, len(history))
        turns_to_summarize = history[2:summarize_end]

        if turns_to_summarize:
            try:
                summary_text = await _generate_context_summary(turns_to_summarize)
                if summary_text:
                    summary_turn = {
                        "role": "model",
                        "parts": [f"[Session Summary] {summary_text}"],
                    }
                    history = history[:2] + [summary_turn] + history[summarize_end:]
                    conversation.summarized = True
                    log.info(
                        "Context auto-summarized: %d turns → %d turns",
                        original_len,
                        len(history),
                    )
            except Exception as exc:
                log.warning("Auto-summarization failed, falling back to drop: %s", exc)

    if len(history) > max_turns:
        history = history[:2] + history[-(max_turns - 2):]

    # Character-based overflow protection
    while len(history) > 4 and _estimate_chars(history) > max_chars:
        # Remove the 3rd message (preserve first 2 for context, keep recent messages)
        history = history[:2] + history[3:]
        log.debug("Trimmed history to %d turns (%d chars)", len(history), _estimate_chars(history))

    return list(history)


async def _generate_context_summary(turns: list[dict]) -> str:
    """Summarize a block of conversation turns into a compact bullet-point summary.

    Uses a low temperature (0.1) Gemini call with max 500 output tokens.
    Returns the summary text, or empty string on failure.
    """
    lines: list[str] = []
    for msg in turns:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = " ".join(str(p) for p in msg["parts"] if isinstance(p, str))[:300]
        if content:
            lines.append(f"{role}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    prompt = (
        "Summarize this conversation so far in 3-5 bullet points, "
        "preserving key facts, decisions, and findings.\n\n"
        f"Conversation:\n{transcript}"
    )

    summary_config = genai.types.GenerateContentConfig(
        max_output_tokens=500,
        temperature=0.1,
    )
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=summary_config,
        ),
    )
    return response.text.strip()


async def _try_local_model(
    user_message: str, history: list[dict], *, force: bool = False,
) -> str | None:
    """Attempt to serve via Gemma/Ollama. Returns reply text or None to fall through.

    When *force* is True (user explicitly chose "local"), skip the
    ``_needs_tools()`` check but still verify Ollama is reachable.
    """
    if not LOCAL_LLM_ENABLED:
        return None

    # Try Ollama with native tool calling for tool-requiring queries
    if not force and _needs_tools(user_message) and cfg.ollama_tools_enabled:
        if await _ollama_available():
            try:
                from ollama_tools import chat_ollama_with_tools
                system_prompt = _load_system_prompt()
                reply, tools_used = await chat_ollama_with_tools(
                    user_message, history, system_prompt, _TOOL_DECLARATIONS,
                    _execute_function_call,
                    ollama_url=OLLAMA_URL, ollama_model=OLLAMA_MODEL,
                    temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                )
                if reply and tools_used:
                    log.info("Served by Ollama with tools (%d calls): %.60s…",
                             len(tools_used), user_message)
                    return reply
            except Exception as e:
                log.info("Ollama tool calling failed, falling back: %s", e)
        # Fall through to None → Gemini handles it

    if not force and _needs_tools(user_message):
        return None
    if not await _ollama_available():
        log.debug("Gemma/Ollama not reachable, using Gemini")
        return None

    system_prompt = _load_system_prompt()
    gemma_reply = await _chat_ollama(user_message, history, system_prompt)

    if gemma_reply and _gemma_response_seems_valid(gemma_reply):
        log.info("Served by Gemma (%s): %.60s…", OLLAMA_MODEL, user_message)
        return gemma_reply

    if gemma_reply:
        log.info("Gemma response failed validation (hallucination signals detected), falling back to Gemini")
    else:
        log.info("Gemma returned empty response, falling back to Gemini")
    return None


def _extract_final_text(response, rounds: int, chat_session) -> str:
    """Pull the final answer text out of *response*, requesting synthesis if needed."""
    try:
        text = response.text
    except (AttributeError, ValueError):
        try:
            parts = response.candidates[0].content.parts
            text = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
        except Exception as exc:
            log.debug("Response text extraction fallback failed: %s", exc)
            text = ""

        if not text and rounds >= MAX_TOOL_ROUNDS:
            log.info("Tool round limit hit with no synthesis — requesting forced summary")
            try:
                _rate_limiter.record()
                synthesis_response = chat_session.send_message(
                    "You have reached the maximum number of tool calls. "
                    "Please synthesize everything you have gathered so far "
                    "into a final, helpful answer for the user. "
                    "Do not call any more tools."
                )
                # Note: usage recording must happen in the caller for async compat
                text = synthesis_response.text
            except Exception as e:
                log.error("Forced synthesis failed: %s", e)

        if not text:
            text = "I processed your request but the model returned no text content."
            if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                text += f" (Safety/Blocked: {response.prompt_feedback})"

    if rounds >= MAX_TOOL_ROUNDS:
        text += f"\n\n⚠️ *Tool call limit reached ({MAX_TOOL_ROUNDS}) — some sources may not have been checked.*"
    return text


async def _reflect_on_response(
    text: str,
    user_message: str,
    rounds: int,
) -> str:
    """Self-evaluate a response and refine if issues are found.

    Only runs for complex responses (tool calls involved). Uses a lightweight
    Gemini call to check for errors, contradictions, or missing information.
    Returns the original or improved text.
    """
    if not cfg.reflection_enabled:
        return text
    # Skip reflection for tool-based responses — tool results are factual.
    # Reflection was rewriting correct "no results found" responses into
    # hallucinated "I can't access the NAS" responses.
    if rounds >= 1:
        return text
    # Don't reflect on very short or error responses
    if len(text) < 50 or text.startswith("❌") or text.startswith("⚠️"):
        return text

    try:
        reflection_config = genai.types.GenerateContentConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=0.2,  # Low temperature for careful evaluation
        )

        reflection_prompt = (
            "You are a quality reviewer. Examine this AI response to a user query.\n"
            "IMPORTANT: This AI assistant HAS tool access and CAN execute actions like "
            "searching folders, checking services, and browsing the web. If the response "
            "reports results from a tool call (e.g. 'no items found', 'search completed'), "
            "do NOT change it to say 'I cannot access' or 'I don't have the ability to'. "
            "The tool was already executed and the result is accurate.\n\n"
            f"USER QUERY: {user_message}\n\n"
            f"AI RESPONSE:\n{text}\n\n"
            "Check for:\n"
            "1. Factual errors or contradictions\n"
            "2. Missing important information the user asked for\n"
            "3. Misinterpreted data or tool results\n"
            "4. Confusing or unclear explanations\n\n"
            "If the response is good, reply with EXACTLY: LGTM\n"
            "If you find issues, reply with a corrected version of the response "
            "(just the improved response, no meta-commentary)."
        )

        loop = asyncio.get_running_loop()
        _rate_limiter.record()
        response = await loop.run_in_executor(
            None,
            lambda: _client.models.generate_content(
                model=MODEL_NAME, contents=reflection_prompt,
                config=reflection_config,
            ),
        )
        await _record_usage(response)

        reflection = response.text.strip()
        if reflection.upper() == "LGTM" or reflection.upper().startswith("LGTM"):
            log.debug("Reflection: response passed self-evaluation")
            return text

        # The reflection produced an improved version
        # Safeguard: if reflection is much shorter, it over-summarized — keep original
        if len(reflection) < len(text) * 0.5 and len(text) > 100:
            log.info("Reflection: discarded (%.0f%% shorter than original — likely over-summarized)",
                     (1 - len(reflection) / len(text)) * 100)
            return text
        log.info("Reflection: response was refined (original %d chars → refined %d chars)",
                 len(text), len(reflection))
        return reflection

    except Exception as e:
        log.debug("Reflection failed (non-fatal): %s", e)
        return text


async def _gemini_chat(
    user_message: str,
    history: list[dict],
    model: _ModelConfig,
    *,
    on_tool_call: Any | None = None,
    parallel_tools: bool = True,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    label: str = "LLM",
) -> tuple[str, list[dict], str]:
    """Common Gemini chat path: rate-limit, send, tool-loop, extract text.

    Returns (response_text, updated_history, model_name).
    """
    # Rate-limit check with jittered exponential backoff
    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history,
            model.model_name if hasattr(model, "model_name") else "unknown",
        )

    # Build Gemini-compatible history
    gemini_history = [_to_content(msg) for msg in history]

    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    # Send user message (runs in executor to not block the event loop)
    loop = asyncio.get_running_loop()
    _rate_limiter.record()
    response = await loop.run_in_executor(
        None, lambda: chat_session.send_message(user_message)
    )
    await _record_usage(response)

    # Handle function-call loop
    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=max_tool_rounds,
        on_tool_call=on_tool_call,
        parallel=parallel_tools,
        label=label,
    )

    text = _extract_final_text(response, rounds, chat_session)

    # Self-evaluate complex responses (Phase 7: Reflection)
    text = await _reflect_on_response(text, user_message, rounds)

    updated_history = _extract_history(chat_session)
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    return text, updated_history, model_name


# ---------------------------------------------------------------------------
# Auto-RAG — recall relevant context before each LLM call (Phase 1)
# ---------------------------------------------------------------------------


async def _auto_recall_context(user_message: str) -> str:
    """Fetch recalled context from the vector store for Auto-RAG injection.

    Combines:
    1. Semantic recall from ChromaDB (facts, conversations, research)
    2. User profile summary (preferences, interests, working style)
    3. Relevant learned rules

    Returns a formatted context string or empty string if disabled/unavailable.
    """
    if not cfg.auto_recall_enabled:
        return ""

    parts = []

    # 1. Vector store recall
    try:
        import vector_store

        context = await vector_store.recall_for_context(user_message)
        if context:
            parts.append(context)
    except Exception as e:
        log.debug("Auto-RAG vector recall failed (non-fatal): %s", e)

    # 2. User profile (always inject if available)
    try:
        from user_profile import get_profile_prompt

        profile = get_profile_prompt()
        if profile and profile.strip():
            parts.append(profile)
    except Exception as e:
        log.debug("Auto-RAG profile injection failed (non-fatal): %s", e)

    # 3. Relevant rules
    try:
        from rules_engine import get_relevant_rules

        rules = await get_relevant_rules(user_message, top_k=3)
        if rules:
            rules_block = "[Active Rules]\n" + "\n".join(f"- {r}" for r in rules)
            parts.append(rules_block)
    except Exception as e:
        log.debug("Auto-RAG rules injection failed (non-fatal): %s", e)

    if parts:
        combined = "\n\n".join(parts)
        count = combined.count("\n- ")
        log.info(
            "Auto-RAG: injected %d context items for: %.60s…",
            count,
            user_message,
        )
        return combined

    return ""


def _strip_recalled_prefix(history: list[dict], original: str, augmented: str) -> list[dict]:
    """Remove the Auto-RAG context prefix from the last user turn in history."""
    if original == augmented:
        return history
    for entry in reversed(history):
        if entry.get("role") == "user":
            entry["parts"] = [
                original if p == augmented else p for p in entry["parts"]
            ]
            break
    return history


# ---------------------------------------------------------------------------
# Streaming chat — yields text chunks for progressive Discord updates
# ---------------------------------------------------------------------------

async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
):
    """Async generator yielding ``(chunk_text, is_final, metadata)`` tuples.

    ``metadata`` is a dict with ``model_used``, ``updated_history`` (only on
    the final chunk), and ``needs_tools`` (bool).

    *model_preference* controls routing (see :func:`chat` docstring).

    For tool-requiring queries, the tool loop runs non-streaming (emitting
    tool-call progress via *on_tool_call*), then the **final text** is
    yielded in one chunk with ``is_final=True``.

    For simple queries (no tools), text is yielded progressively as
    Gemini streams tokens.
    """
    # Determine model hint for context limits
    if model_preference == "local":
        _model_hint = "ollama"
    elif model_preference == "gemini":
        _model_hint = "gemini"
    else:
        _model_hint = "gemini"  # auto mode may use either, use generous default
    history = await _trim_history(history or [], model_hint=_model_hint)

    # Track routing decisions to surface to user
    _routing_notes: list[str] = []

    # ── Auto-RAG: recall relevant context ────────────────────────────────
    recalled_context = await _auto_recall_context(user_message)
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {user_message}"
    else:
        model_message = user_message

    # ── Multi-model routing (Phase 8) ───────────────────────────────────
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
            )
            log.debug("Model router (stream): %s", route)

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False}
                    return

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    yield reply, True, {"model_used": f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}", "updated_history": updated, "needs_tools": False}
                    return
        except Exception as e:
            log.debug("Multi-model routing failed (non-fatal, stream): %s", e)

    # ── Forced OpenAI / Anthropic mode ─────────────────────────────────
    if model_preference in ("openai", "anthropic"):
        try:
            import os

            from model_router import chat_anthropic, chat_openai
            system_prompt = _load_system_prompt()
            if model_preference == "openai":
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
            else:
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
            if reply:
                updated = history + [
                    {"role": "user", "parts": [user_message]},
                    {"role": "model", "parts": [reply]},
                ]
                yield reply, True, {"model_used": model_label, "updated_history": updated, "needs_tools": False}
                return
            # Fall through to Gemini if the call failed
            log.info("%s call failed, falling back to Gemini", model_preference)
        except Exception as e:
            log.info("%s call failed, falling back to Gemini: %s", model_preference, e)

    # ── Forced local mode ────────────────────────────────────────────────
    if model_preference == "local":
        if not LOCAL_LLM_ENABLED:
            yield "⚠️ Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        if not await _ollama_available():
            yield "⚠️ Ollama is not reachable. Check that the service is running.", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        gemma_reply = await _try_local_model(model_message, history, force=True)
        if gemma_reply is not None:
            updated = history + [
                {"role": "user", "parts": [user_message]},
                {"role": "model", "parts": [gemma_reply]},
            ]
            yield gemma_reply, True, {"model_used": OLLAMA_MODEL, "updated_history": updated, "needs_tools": False}
            return
        # Gemma returned empty — silently fall through to Gemini instead
        # of dead-ending with an unhelpful error message.
        log.info("Local model returned empty, auto-falling back to Gemini")

    # ── Forced Gemini mode ───────────────────────────────────────────────
    if model_preference in ("gemini", "local"):
        # "local" lands here only when Gemma failed and we're falling back
        if not GOOGLE_API_KEY:
            yield "⚠️ Gemini API key not configured (`GOOGLE_API_KEY`).", True, {"model_used": "none", "updated_history": history, "needs_tools": False}
            return
        # Fall through to the Gemini paths below (skip local attempt)
    else:
        # ── Auto mode: Always use Gemini (has 106 tools). ──
        # Copilot proxy is only used as a fallback when Gemini is rate-limited.
        # Previously tried Copilot first for "simple" queries, but it lacks
        # tool access and would say "I'll search..." without actually searching.
        pass
        # Copilot fallback happens below only if Gemini rate-limit check fails

    # Rate-limit pre-check — if Gemini is rate-limited, try Copilot proxy as fallback
    if not _rate_limiter.check():
        try:
            from model_router import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply and _gemma_response_seems_valid(reply):
                    import os
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    _routing_notes.append("Gemini rate-limited → used Copilot proxy")
                    yield reply, True, {"model_used": f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}", "updated_history": updated, "needs_tools": False, "routing_notes": _routing_notes}
                    return
        except Exception:
            pass
        msg = (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)"
        )
        yield msg, True, {"model_used": MODEL_NAME, "updated_history": history, "needs_tools": False}
        return

    model = await _get_model()
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    # All queries go through Gemini with tools available.
    # Gemini will only call tools if the query requires them —
    # simple questions get direct text responses, complex ones get tool calls.
    # This eliminates the fragile _needs_tools() regex classification.
    text, updated_history, model_name = await _gemini_chat(
        model_message, history, model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)

    # Post-response hallucination guard: if the model promises actions
    # without having executed tools, the response is invalid
    if not _gemma_response_seems_valid(text):
        log.warning("Post-response hallucination detected, retrying with explicit tool instruction")
        retry_msg = (
            f"{model_message}\n\n"
            "IMPORTANT: You have tool access. Do NOT say 'let me search' or 'one moment'. "
            "USE the available tools (e.g. nas_list_folder, search_web, browse_url) to "
            "find the answer, then respond with the actual results."
        )
        text, updated_history, model_name = await _gemini_chat(
            retry_msg, history, model,
            on_tool_call=on_tool_call,
            parallel_tools=True,
            label="LLM-retry",
        )
        updated_history = _strip_recalled_prefix(updated_history, user_message, retry_msg)

    # ── Auto-escalate vague responses to web search ──────────────────────
    if (
        _VAGUE_RESPONSE_RE.search(text)
        and _FACTUAL_QUESTION_RE.search(user_message.strip())
    ):
        log.info("Auto-escalating to web search for: %s", user_message)
        search_fn = SKILLS.get("search_web")
        if search_fn is not None:
            try:
                search_results = await search_fn(user_message)
                if search_results and search_results.strip():
                    enhanced_msg = (
                        f"{model_message}\n\n"
                        "Here are fresh web search results to help answer the question:\n"
                        f"{search_results}\n\n"
                        "Use these results to give a thorough, factual answer."
                    )
                    text, updated_history, model_name = await _gemini_chat(
                        enhanced_msg, history, model,
                        on_tool_call=on_tool_call,
                        parallel_tools=True,
                        label="LLM-escalate",
                    )
                    updated_history = _strip_recalled_prefix(
                        updated_history, user_message, enhanced_msg,
                    )
            except Exception as exc:
                log.warning("Auto-escalation web search failed: %s", exc)

    yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": True, "routing_notes": _routing_notes}
    return

    # ── No-tool Gemini streaming path ────────────────────────────────────
    gemini_history = [_to_content(msg) for msg in history]
    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    loop = asyncio.get_running_loop()
    _rate_limiter.record()

    try:
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message_stream(model_message)
        )
    except Exception as e:
        yield f"❌ **LLM Error:** {e}", True, {"model_used": model_name, "updated_history": history, "needs_tools": False, "routing_notes": _routing_notes}
        return

    accumulated = ""
    last_chunk = None
    try:
        for chunk in response:
            last_chunk = chunk
            try:
                text = chunk.text
            except (ValueError, AttributeError):
                continue
            if text:
                accumulated += text
                yield accumulated, False, {"model_used": model_name, "needs_tools": False}
    except Exception as e:
        if not accumulated:
            accumulated = f"❌ Streaming error: {e}"

    # Record usage from the last chunk (streaming doesn't need resolve())
    if last_chunk is not None:
        try:
            await _record_usage(last_chunk)
        except Exception as exc:
            log.debug("Stream usage recording failed: %s", exc)

    updated_history = _extract_history(chat_session)
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
    yield accumulated, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": False, "routing_notes": _routing_notes}


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
    model_preference: str = "auto",
) -> tuple[str, list[dict], str]:
    """
    Send a message and return (response_text, updated_history, model_used).

    ``on_tool_call(tool_name, round_num)`` is an optional async callback invoked
    before each tool execution — used for progressive Discord status updates.

    *model_preference* controls routing:
      - ``"auto"``  — Copilot proxy first (free), then Gemini with tools
      - ``"local"`` — force Ollama/Gemma; error if unavailable
      - ``"gemini"`` — skip everything, go straight to Gemini

    Routing decision tree (when auto):
      1. Multi-model router (Phase 8) checks for openai/anthropic routing
      2. Try Copilot proxy (GPT-4o via local proxy, free)
      3. Fall through to Gemini with full tool support
    """
    # Determine model hint for context limits
    if model_preference == "local":
        _model_hint = "ollama"
    elif model_preference == "gemini":
        _model_hint = "gemini"
    else:
        _model_hint = "gemini"  # auto mode may use either, use generous default
    history = await _trim_history(history or [], model_hint=_model_hint)

    # -- Auto-RAG: recall relevant context ────────────────────────────────────
    recalled_context = await _auto_recall_context(user_message)
    if recalled_context:
        model_message = f"{recalled_context}\n\n---\nUser's question: {user_message}"
    else:
        model_message = user_message

    # -- Multi-model routing (Phase 8) ────────────────────────────────────
    if model_preference == "auto":
        try:
            import os

            from model_router import chat_anthropic, chat_openai, classify_query
            route = classify_query(
                user_message,
                has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
                has_anthropic_key=bool(os.getenv("ANTHROPIC_API_KEY")),
                needs_tools=_needs_tools(user_message),
            )
            log.debug("Model router: %s", route)

            if route.model_type == "openai":
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
                log.info("OpenAI call failed, falling through to default routing")

            elif route.model_type == "anthropic":
                system_prompt = _load_system_prompt()
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
                log.info("Anthropic call failed, falling through to default routing")
        except Exception as e:
            log.debug("Multi-model routing failed (non-fatal): %s", e)

    # -- Forced OpenAI / Anthropic mode ──────────────────────────────────────
    if model_preference in ("openai", "anthropic"):
        try:
            import os

            from model_router import chat_anthropic, chat_openai
            system_prompt = _load_system_prompt()
            if model_preference == "openai":
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
            else:
                reply = await chat_anthropic(model_message, history, system_prompt,
                                             temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                model_label = f"anthropic/{os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4.5')}"
            if reply:
                updated = history + [
                    {"role": "user", "parts": [user_message]},
                    {"role": "model", "parts": [reply]},
                ]
                return reply, updated, model_label
            log.info("%s call failed, falling back to Gemini", model_preference)
        except Exception as e:
            log.info("%s call failed, falling back to Gemini: %s", model_preference, e)

    # -- Forced local mode ────────────────────────────────────────────────────
    if model_preference == "local":
        if not LOCAL_LLM_ENABLED:
            return "⚠️ Local LLM is disabled (`LOCAL_LLM_ENABLED=false`).", history, "none"
        if not await _ollama_available():
            return "⚠️ Ollama is not reachable. Check that the service is running.", history, "none"
        gemma_reply = await _try_local_model(model_message, history, force=True)
        if gemma_reply is not None:
            updated = history + [
                {"role": "user", "parts": [user_message]},
                {"role": "model", "parts": [gemma_reply]},
            ]
            return gemma_reply, updated, OLLAMA_MODEL
        # Gemma returned empty — silently fall through to Gemini
        log.info("Local model returned empty, auto-falling back to Gemini")

    # -- Forced Gemini mode ───────────────────────────────────────────────────
    if model_preference in ("gemini", "local"):
        # "local" lands here only when Gemma failed and we're falling back
        if not GOOGLE_API_KEY:
            return "⚠️ Gemini API key not configured (`GOOGLE_API_KEY`).", history, "none"
        if not _rate_limiter.check():
            return (
                "⚠️ Rate limit reached. Please wait a moment before asking again. "
                f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
                history,
                MODEL_NAME,
            )
        model = await _get_model()
        text, updated_history, model_name = await _gemini_chat(
            model_message, history, model,
            on_tool_call=on_tool_call, parallel_tools=True, label="LLM",
        )
        updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
        return text, updated_history, model_name

    # -- Auto mode: Copilot for simple queries, Gemini for tool queries ─────
    if not _needs_tools(user_message):
        try:
            from model_router import COPILOT_PROXY_ENABLED, chat_openai
            if COPILOT_PROXY_ENABLED:
                system_prompt = _load_system_prompt()
                reply = await chat_openai(model_message, history, system_prompt,
                                          temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
                if reply:
                    import os
                    updated = history + [
                        {"role": "user", "parts": [user_message]},
                        {"role": "model", "parts": [reply]},
                    ]
                    return reply, updated, f"copilot/{os.getenv('OPENAI_MODEL', 'gpt-4o')}"
                log.info("Copilot proxy failed, falling through to Gemini")
        except Exception as e:
            log.debug("Copilot proxy failed: %s", e)
    # Tool-requiring queries go straight to Gemini (has 105 tools registered)

    # -- Gemini path (shared helper) ──────────────────────────────────────────
    # Quick rate-limit pre-check before expensive model init — the full
    # backoff loop runs inside _gemini_chat, but if we're already exhausted
    # we can bail out without touching the API-key-gated model constructor.
    if not _rate_limiter.check():
        return (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)",
            history,
            MODEL_NAME,
        )

    model = await _get_model()
    text, updated_history, model_name = await _gemini_chat(
        model_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )
    updated_history = _strip_recalled_prefix(updated_history, user_message, model_message)
    return text, updated_history, model_name


def _extract_history(chat_session) -> list[dict]:
    """Convert a ChatSession's history to our serializable format."""
    history = []
    for content in chat_session.get_history():
        parts = []
        for part in content.parts:
            if hasattr(part, "text") and part.text:
                parts.append(part.text)
            elif hasattr(part, "function_call") and part.function_call and part.function_call.name:
                parts.append(f"[Called {part.function_call.name}]")
            elif hasattr(part, "function_response") and part.function_response and part.function_response.name:
                parts.append(f"[Result from {part.function_response.name}]")
        if parts:
            history.append({"role": content.role, "parts": parts})
    return history


# ---------------------------------------------------------------------------
# Convenience: check if LLM is configured
# ---------------------------------------------------------------------------


async def close_sessions() -> None:
    """Close all persistent aiohttp sessions. Call on bot shutdown."""
    await _ollama_sessions.close()
    log.info("Closed Ollama aiohttp session")


async def _record_usage(response) -> None:
    """Extract usage_metadata from a Gemini response and record spending."""
    try:
        meta = response.usage_metadata
        if meta:
            inp = getattr(meta, "prompt_token_count", 0) or 0
            out = getattr(meta, "candidates_token_count", 0) or 0
            if inp or out:
                await spending_tracker.record(inp, out)
    except Exception as e:
        log.warning("Failed to record token usage: %s", e)


def is_configured() -> bool:
    """Return True if a Google API key is set (Gemini) OR local LLM is enabled."""
    return bool(GOOGLE_API_KEY) or LOCAL_LLM_ENABLED


def get_rate_info() -> str:
    """Return a human-readable rate limit status for Gemini Flash."""
    return f"{_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining"


# ---------------------------------------------------------------------------
# Deep research chat — Gemini with extended thinking (for /research)
# ---------------------------------------------------------------------------

_thinking_model: _ModelConfig | None = None
_thinking_model_prompt: str | None = None
_thinking_model_lock = threading.Lock()


def _get_thinking_model() -> _ModelConfig:
    """Lazy-init the thinking/deep-research variant of the Gemini model."""
    global _thinking_model, _thinking_model_prompt
    system_prompt = _load_system_prompt()
    with _thinking_model_lock:
        if _thinking_model is not None and _thinking_model_prompt == system_prompt:
            return _thinking_model

        _thinking_model = _init_gemini_model(
            THINKING_MODEL,
            temperature=0.3,
            max_tokens=MAX_TOKENS * 2,
            thinking_budget=THINKING_BUDGET,
        )
        _thinking_model_prompt = system_prompt
        log.info("Thinking model initialized: %s", THINKING_MODEL)
        return _thinking_model


async def chat_deep(
    user_message: str,
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """
    Deep research chat — always uses Gemini with extended thinking.
    Supports a progress callback ``on_tool_call(tool_name, round_num)``
    for streaming progress updates to a Discord thread.

    Returns (response_text, updated_history).
    """
    history = history or []

    try:
        model = _get_thinking_model()
    except Exception as exc:
        # Fall back to normal model if thinking config is unsupported
        log.warning("Thinking model unavailable, falling back to standard model: %s", exc)
        model = await _get_model()

    text, updated_history, _ = await _gemini_chat(
        user_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=False,
        max_tool_rounds=MAX_TOOL_ROUNDS * 2,
        label="Deep research",
    )

    return text, updated_history


async def summarize_conversation(history: list[dict]) -> str:
    """
    Produce a 3-5 sentence summary of a conversation history for
    long-term memory storage. Uses the standard Gemini model directly
    (no tools, no conversation context).
    """
    if not GOOGLE_API_KEY or not history:
        return ""

    # Build a compact transcript (user turns only for efficiency)
    lines = []
    for msg in history[-20:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = " ".join(str(p) for p in msg["parts"] if isinstance(p, str))[:200]
        if content:
            lines.append(f"{role}: {content}")

    if not lines:
        return ""

    transcript = "\n".join(lines)
    prompt = (
        "Summarize the following conversation in 3-5 concise sentences. "
        "Capture the main topics, any decisions made, and key facts mentioned. "
        "Write in third person (e.g. 'The user asked about...').\n\n"
        f"Conversation:\n{transcript}"
    )

    try:
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=300,
                temperature=0.2,
            ),
        )
        return response.text.strip()
    except Exception as e:
        log.warning("Failed to summarize conversation: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Phase 8: Multimodal helpers (image + document analysis)
# ---------------------------------------------------------------------------

# Supported image MIME types for Gemini
SUPPORTED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/webp",
    "image/heic", "image/heif", "image/gif",
}


async def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = "Describe this image in detail. Note any text, errors, or important information.",
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> str:
    """
    Analyze an image using Gemini's multimodal vision capabilities.

    If the prompt suggests tool usage is needed (e.g. "restart the broken service"),
    delegates to analyze_image_with_tools() which uses the full tool-calling model.
    Otherwise uses a lightweight model without tools for faster simple descriptions.

    Returns a descriptive text response.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}"

    # If the prompt suggests tool usage, use the full tool-enabled model
    if _needs_tools(prompt):
        text, _ = await analyze_image_with_tools(
            image_bytes, mime_type, prompt,
            history=history, on_tool_call=on_tool_call,
        )
        return text

    # Simple vision: use a lightweight model without tools (faster)
    try:
        image_part = genai.types.Part(
            inline_data=genai.types.Blob(mime_type=mime_type, data=image_bytes)
        )
        text_part = genai.types.Part(text=prompt)

        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=genai.types.Content(parts=[image_part, text_part]),
            config=genai.types.GenerateContentConfig(
                max_output_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            ),
        )
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Image analysis failed: %s", e)
        return f"❌ Image analysis failed: {e}"


async def analyze_image_with_tools(
    image_bytes: bytes,
    mime_type: str,
    prompt: str = "Describe this image in detail. Note any text, errors, or important information.",
    history: list[dict] | None = None,
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict]]:
    """Analyze an image using the main tool-enabled model.

    Unlike analyze_image(), this uses the full tool-calling model so the LLM
    can see the image AND call tools in the same turn — e.g. "analyze this
    dashboard screenshot and restart the unhealthy service."

    Returns (response_text, updated_history).
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured.", history or []
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}", history or []

    history = await _trim_history(history or [], model_hint="gemini")

    # Rate-limit check
    if not await _rate_limiter.wait_for_capacity(max_wait=30.0):
        return (
            "⚠️ Rate limit reached. Please wait a moment.",
            history,
        )

    model = await _get_model()

    # Build Gemini history
    gemini_history = [_to_content(msg) for msg in history]

    chat_session = _client.chats.create(
        model=model.model_name, config=model.config, history=gemini_history,
    )

    # Create multimodal content with image + text
    image_part = genai.types.Part(
        inline_data=genai.types.Blob(mime_type=mime_type, data=image_bytes)
    )
    text_part = genai.types.Part(text=prompt)
    multimodal_parts = [image_part, text_part]

    loop = asyncio.get_running_loop()
    _rate_limiter.record()

    try:
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(multimodal_parts)
        )
        await _record_usage(response)
    except Exception as e:
        log.error("Image analysis with tools failed: %s", e)
        return f"❌ Image analysis failed: {e}", history

    # Run tool loop if the model wants to call tools based on what it saw
    response, rounds = await _run_tool_loop(
        chat_session, response,
        max_rounds=MAX_TOOL_ROUNDS,
        on_tool_call=on_tool_call,
        parallel=True,
        label="Vision+Tools",
    )

    text = _extract_final_text(response, rounds, chat_session)
    updated_history = _extract_history(chat_session)

    return text, updated_history


async def analyze_document(text: str, prompt: str) -> str:
    """
    Analyze document text using Gemini (no tool loop — direct generation).
    Used by /analyze-file command.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."

    doc_config = genai.types.GenerateContentConfig(
        max_output_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )

    full_prompt = f"{prompt}\n\n---\n\n{text}"

    try:
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=MODEL_NAME,
            contents=full_prompt,
            config=doc_config,
        )
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Document analysis failed: %s", e)
        return f"❌ Document analysis failed: {e}"
