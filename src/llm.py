"""
OpenClaw LLM Integration — Phase 5: Gemini + Function Calling
Manages the Gemini API connection, tool declarations, and chat sessions.

Hybrid routing:
  - Simple / conversational queries → Ollama (local, free, fast)
  - Anything requiring tool/function calls  → Gemini 2.0 Flash
  - Ollama unavailable or LOCAL_LLM_ENABLED=false → Gemini for everything
"""

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import aiohttp
import google.generativeai as genai

from skills import SKILLS
from spending import tracker as spending_tracker
from config import cfg

log = logging.getLogger("openclaw.llm")

# ---------------------------------------------------------------------------
# Configuration (sourced from centralized config)
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = cfg.google_api_key
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
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
MAX_TOOL_ROUNDS = 12

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
_ollama_session: aiohttp.ClientSession | None = None
_ollama_session_lock: asyncio.Lock | None = None


async def _get_ollama_session() -> aiohttp.ClientSession:
    """Return the shared Ollama aiohttp session, (re)creating if closed."""
    global _ollama_session, _ollama_session_lock
    if _ollama_session_lock is None:
        _ollama_session_lock = asyncio.Lock()
    async with _ollama_session_lock:
        if _ollama_session is None or _ollama_session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
            _ollama_session = aiohttp.ClientSession(connector=connector)
        return _ollama_session


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
    # Weather: any standalone weather request routes through Gemini (needs get_weather tool)
    r"|\b(weather|forecast|temperature|rain|snow|sunny|humidity|wind\s+speed)\b"
    # Live-data questions: "is plex up?", "what's the current…"
    r"|\bis\s+(the\s+)?(server|plex|sonarr|radarr|nas|docker)\s+(up|running|online|working|down)\b"
    r"|\bwhat'?s?\s+(?:the\s+)?(?:current|latest|running)\b.{0,50}\b(status|usage|queue|activity)\b"
    # Approvals, sends, creates
    r"|\b(approve|deny)\b.{0,20}\b(request|id)\b"
    r"|\bsend\b.{0,20}\b(email|mail)\b"
    r"|\bcreate\b.{0,30}\b(task|event|entity|connection|calendar)\b"
    # Diagnostics / jobs
    r"|\brun\b.{0,20}\b(speed\s+test|status\s+report|ping|backup|diagnostic)\b"
    r"|\bping\s+[\w.]+"
    # URLs always need browse_url
    r"|https?://",
    _re.IGNORECASE,
)

# Tier 2 — Well-known domains where Gemma consistently fabricates answers.
# These are proper nouns tied to live services or specialised data sources.
_GEMMA_WEAK_DOMAINS = _re.compile(
    r"\b(zillow|redfin|trulia|narberth|upper\s+darby|maton|tailscale|tautulli"
    r"|overseerr|prowlarr|sabnzbd|synology|hyper\s+backup|ontology)\b",
    _re.IGNORECASE,
)


def _needs_tools(message: str) -> bool:
    """Return True if the query requires live tool execution and should bypass Gemma."""
    return bool(_LIVE_ACTION_PATTERN.search(message) or _GEMMA_WEAK_DOMAINS.search(message))


# Compiled patterns that signal Gemma is pretending to call tools it doesn't have.
# Any match in Gemma's response triggers an automatic fallback to Gemini.
_GEMMA_HALLUCINATION_RE = _re.compile(
    r"(i'?m?\s+)?(now\s+)?(searching|browsing|checking|fetching|looking\s+up)\b"
    r"|\b(let\s+me\s+)?(search|check|look\s+that\s+up|fetch)\s+(that|the|for)\b"
    r"|(checking|querying)\s+(zillow|redfin|the\s+server|docker|container|plex)\b"
    r"|\b(i\s+)?(don'?t|cannot|can'?t)\s+(access|browse|check|reach)\s+(the\s+)?(internet|web|real[\s-]?time|live|current)\b"
    r"|\b(as\s+an?\s+ai|as\s+a\s+language\s+model)\b.{0,80}\b(cannot|don'?t|no\s+access)\b"
    r"|\bi\s+don'?t\s+have\s+(real[\s-]?time|access\s+to|live)\b"
    r"|(would\s+need\s+to\s+|i\s+could\s+)?(search|check|query)\s+(this|that|it)\s+for\s+you\b",
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
            timeout=aiohttp.ClientTimeout(total=30),
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

_model: genai.GenerativeModel | None = None


_model_system_prompt: str | None = None
_model_lock: asyncio.Lock | None = None


def _init_gemini_model(
    model_name: str,
    *,
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_TOKENS,
    thinking_budget: int | None = None,
    with_tools: bool = True,
) -> genai.GenerativeModel:
    """Create a configured GenerativeModel instance (shared factory)."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not set. Add it to your .env file.")

    gen_config_kwargs: dict[str, Any] = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }

    if thinking_budget is not None:
        thinking_cfg = getattr(genai.types, "ThinkingConfig", None)
        if thinking_cfg is not None:
            gen_config_kwargs["thinking_config"] = thinking_cfg(thinking_budget=thinking_budget)
            log.info("ThinkingConfig enabled (budget=%d tokens)", thinking_budget)
        else:
            log.info("ThinkingConfig not available in this SDK version — using low-temperature deep mode")

    return genai.GenerativeModel(
        model_name=model_name,
        system_instruction=_load_system_prompt(),
        tools=_build_tools() if with_tools else None,
        generation_config=genai.GenerationConfig(**gen_config_kwargs),
    )


async def _get_model() -> genai.GenerativeModel:
    """Lazy-init the Gemini model; reloads when system prompt changes."""
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
            if hasattr(part, "function_call") and part.function_call.name
        ]

        if not function_calls:
            break

        # In sequential mode, process only the first call per round
        if not parallel:
            function_calls = function_calls[:1]

        log.info("%s function call(s) [round %d]: %s", label, rounds + 1,
                 ", ".join(f"{n}({a})" for n, a in function_calls))

        # Fire progress callbacks
        if on_tool_call:
            for fn_name, _ in function_calls:
                try:
                    await on_tool_call(fn_name, rounds + 1)
                except Exception as exc:
                    log.debug("on_tool_call callback failed: %s", exc)

        # Execute tool calls
        results = await asyncio.gather(*[
            _execute_function_call(fn_name, fn_args)
            for fn_name, fn_args in function_calls
        ])

        # Rate-limit check before sending results back
        _rate_limiter.record()
        if not _rate_limiter.check():
            # Return partial results as a courtesy message
            partial = "\n".join(results)
            # Build a fake text-only response — caller handles this
            return response, rounds + 1

        # Send all function results back to the model
        response_parts = [
            genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=fn_name,
                    response={"result": result},
                )
            )
            for (fn_name, _), result in zip(function_calls, results)
        ]

        response = await loop.run_in_executor(
            None,
            lambda parts=response_parts: chat_session.send_message(
                genai.protos.Content(parts=parts)
            ),
        )
        await _record_usage(response)
        rounds += 1

    return response, rounds


# ---------------------------------------------------------------------------
# chat() helper decomposition
# ---------------------------------------------------------------------------

_MAX_HISTORY_TURNS = 20
_MAX_HISTORY_CHARS = 80_000  # ~20K tokens at ~4 chars/token — leave room for tools + response


def _estimate_chars(history: list[dict]) -> int:
    """Rough character count of conversation history."""
    total = 0
    for msg in history:
        for p in msg.get("parts", []):
            if isinstance(p, str):
                total += len(p)
    return total


def _trim_history(history: list[dict]) -> list[dict]:
    """Keep first 2 turns (persona context) + last N to avoid context overflow.

    If the history still exceeds _MAX_HISTORY_CHARS after turn trimming,
    progressively drop older turns until it fits.
    """
    if len(history) > _MAX_HISTORY_TURNS:
        history = history[:2] + history[-(_MAX_HISTORY_TURNS - 2):]

    # Character-based overflow protection
    while len(history) > 4 and _estimate_chars(history) > _MAX_HISTORY_CHARS:
        # Remove the 3rd message (preserve first 2 for context, keep recent messages)
        history = history[:2] + history[3:]
        log.debug("Trimmed history to %d turns (%d chars)", len(history), _estimate_chars(history))

    return list(history)


async def _try_local_model(
    user_message: str, history: list[dict]
) -> str | None:
    """Attempt to serve via Gemma/Ollama. Returns reply text or None to fall through."""
    if not LOCAL_LLM_ENABLED or _needs_tools(user_message):
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


async def _gemini_chat(
    user_message: str,
    history: list[dict],
    model: genai.GenerativeModel,
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
    gemini_history = [
        genai.types.ContentDict(role=msg["role"], parts=msg["parts"])
        for msg in history
    ]

    chat_session = model.start_chat(history=gemini_history)

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
    updated_history = _extract_history(chat_session)
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"

    return text, updated_history, model_name


# ---------------------------------------------------------------------------
# Streaming chat — yields text chunks for progressive Discord updates
# ---------------------------------------------------------------------------

async def chat_stream(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
):
    """Async generator yielding ``(chunk_text, is_final, metadata)`` tuples.

    ``metadata`` is a dict with ``model_used``, ``updated_history`` (only on
    the final chunk), and ``needs_tools`` (bool).

    For tool-requiring queries, the tool loop runs non-streaming (emitting
    tool-call progress via *on_tool_call*), then the **final text** is
    yielded in one chunk with ``is_final=True``.

    For simple queries (no tools), text is yielded progressively as
    Gemini streams tokens.
    """
    history = _trim_history(history or [])

    # ── Local Ollama path (non-streaming, single yield) ──────────────────
    gemma_reply = await _try_local_model(user_message, history)
    if gemma_reply is not None:
        updated = history + [
            {"role": "user", "parts": [user_message]},
            {"role": "model", "parts": [gemma_reply]},
        ]
        yield gemma_reply, True, {"model_used": OLLAMA_MODEL, "updated_history": updated, "needs_tools": False}
        return

    # Rate-limit pre-check
    if not _rate_limiter.check():
        msg = (
            "⚠️ Rate limit reached. Please wait a moment before asking again. "
            f"({_rate_limiter.remaining_minute}/min, {_rate_limiter.remaining_hour}/hr remaining)"
        )
        yield msg, True, {"model_used": MODEL_NAME, "updated_history": history, "needs_tools": False}
        return

    model = await _get_model()
    model_name = model.model_name if hasattr(model, "model_name") else "unknown"
    needs_tools = _needs_tools(user_message)

    if needs_tools:
        # Tool queries: run the full tool loop (non-streaming), then yield result
        text, updated_history, model_name = await _gemini_chat(
            user_message, history, model,
            on_tool_call=on_tool_call,
            parallel_tools=True,
            label="LLM",
        )
        yield text, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": True}
        return

    # ── No-tool Gemini streaming path ────────────────────────────────────
    gemini_history = [
        genai.types.ContentDict(role=msg["role"], parts=msg["parts"])
        for msg in history
    ]
    chat_session = model.start_chat(history=gemini_history)

    loop = asyncio.get_running_loop()
    _rate_limiter.record()

    try:
        response = await loop.run_in_executor(
            None, lambda: chat_session.send_message(user_message, stream=True)
        )
    except Exception as e:
        yield f"❌ **LLM Error:** {e}", True, {"model_used": model_name, "updated_history": history, "needs_tools": False}
        return

    accumulated = ""
    try:
        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                accumulated += chunk.text
                yield accumulated, False, {"model_used": model_name, "needs_tools": False}
    except Exception as e:
        if not accumulated:
            accumulated = f"❌ Streaming error: {e}"

    # Resolve the response to record usage
    try:
        response.resolve()
        await _record_usage(response)
    except Exception as exc:
        log.debug("Stream response resolve/usage recording failed: %s", exc)

    updated_history = _extract_history(chat_session)
    yield accumulated, True, {"model_used": model_name, "updated_history": updated_history, "needs_tools": False}


async def chat(
    user_message: str,
    history: list[dict] | None = None,
    user_name: str = "User",
    on_tool_call: Any | None = None,
) -> tuple[str, list[dict], str]:
    """
    Send a message and return (response_text, updated_history, model_used).

    ``on_tool_call(tool_name, round_num)`` is an optional async callback invoked
    before each tool execution — used for progressive Discord status updates.

    Routing decision tree:
      1. Does the query need live tool execution? (_needs_tools)
            YES → Gemini directly (function-calling capable)
      2. Is Gemma available and LOCAL_LLM_ENABLED?
            NO  → Gemini
      3. Does Gemma's response pass the hallucination / quality check?
            YES → Return Gemma response (fast, free, private)
            NO  → Silently retry with Gemini
    """
    history = _trim_history(history or [])

    # -- Local model (Gemma) path ─────────────────────────────────────────────
    gemma_reply = await _try_local_model(user_message, history)
    if gemma_reply is not None:
        updated = history + [
            {"role": "user", "parts": [user_message]},
            {"role": "model", "parts": [gemma_reply]},
        ]
        return gemma_reply, updated, OLLAMA_MODEL

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
    return await _gemini_chat(
        user_message,
        history,
        model,
        on_tool_call=on_tool_call,
        parallel_tools=True,
        label="LLM",
    )


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


async def close_sessions() -> None:
    """Close all persistent aiohttp sessions. Call on bot shutdown."""
    global _ollama_session
    if _ollama_session is not None and not _ollama_session.closed:
        await _ollama_session.close()
        _ollama_session = None
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

_thinking_model: genai.GenerativeModel | None = None
_thinking_model_prompt: str | None = None
_thinking_model_lock = threading.Lock()


def _get_thinking_model() -> genai.GenerativeModel:
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
        summary_model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            generation_config=genai.GenerationConfig(
                max_output_tokens=300,
                temperature=0.2,
            ),
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: summary_model.generate_content(prompt)
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
) -> str:
    """
    Analyze an image using Gemini's multimodal vision capabilities.
    Returns a descriptive text response.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."
    if mime_type not in SUPPORTED_IMAGE_MIMES:
        return f"❌ Unsupported image type: {mime_type}"

    # Use a fresh model without tools for vision tasks
    vision_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        ),
    )

    try:
        image_part = genai.protos.Part(
            inline_data=genai.protos.Blob(mime_type=mime_type, data=image_bytes)
        )
        text_part = genai.protos.Part(text=prompt)
        content = genai.protos.Content(parts=[image_part, text_part])

        response = await asyncio.to_thread(vision_model.generate_content, content)
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Image analysis failed: %s", e)
        return f"❌ Image analysis failed: {e}"


async def analyze_document(text: str, prompt: str) -> str:
    """
    Analyze document text using Gemini (no tool loop — direct generation).
    Used by /analyze-file command.
    """
    if not GOOGLE_API_KEY:
        return "❌ GOOGLE_API_KEY not configured."

    doc_model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config=genai.GenerationConfig(
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        ),
    )

    full_prompt = f"{prompt}\n\n---\n\n{text}"

    try:
        response = await asyncio.to_thread(doc_model.generate_content, full_prompt)
        await _record_usage(response)
        return response.text or "No response from model."
    except Exception as e:
        log.error("Document analysis failed: %s", e)
        return f"❌ Document analysis failed: {e}"
