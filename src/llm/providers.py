"""
OpenClaw LLM Providers — single source of truth for non-Gemini provider API calls.

Handles OpenAI, Anthropic, and Copilot-proxy HTTP calls.
"""

import asyncio
import dataclasses
import json as _json
import logging
import os
import random
import time as _time
from typing import AsyncGenerator, Optional

import aiohttp

from http_session import SessionManager as _SessionManager

log = logging.getLogger("openclaw.llm.providers")


# ---------------------------------------------------------------------------
# Public response envelope
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProviderResponse:
    """Typed envelope returned by :func:`call_provider`.

    Backwards-compatible with callers that treat the result as a string:
    ``str(result)`` and truthiness checks work via ``__str__``/``__bool__``.
    ``text`` is ``None`` when the provider call failed.
    """

    text: str | None
    provider: str
    model: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0

    def __str__(self) -> str:
        """Allow existing callers that do ``str(result)`` to keep working."""
        return self.text or ""

    def __bool__(self) -> bool:
        return bool(self.text)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COPILOT_PROXY_URL: str = os.getenv("COPILOT_PROXY_URL", "")
COPILOT_PROXY_ENABLED: bool = COPILOT_PROXY_URL != ""
_proxy_healthy: bool = False  # set by check_proxy_health() at startup

# Configurable fallback chain: primary provider is prepended at call time.
_FALLBACK_CHAIN_RAW = os.getenv("PROVIDER_FALLBACK_CHAIN", "copilot,ollama")
_FALLBACK_CHAIN: list[str] = [p.strip() for p in _FALLBACK_CHAIN_RAW.split(",") if p.strip()]

_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "gemma3:4b")

# Populated by chat_openai / chat_anthropic before they return; read by call_provider.
_last_usage: dict = {"input_tokens": 0, "output_tokens": 0}

# Cumulative token counts for this process lifetime; updated by call_provider().
_cumulative_tokens: dict[str, int] = {"input": 0, "output": 0}


def token_usage_summary() -> dict:
    """Return cumulative input/output tokens seen this process lifetime."""
    return dict(_cumulative_tokens)

# ---------------------------------------------------------------------------
# Proxy health check
# ---------------------------------------------------------------------------


async def check_proxy_health(timeout: float = 5.0) -> bool:
    """Ping the Copilot proxy endpoint; update _proxy_healthy. Call once at bot startup."""
    global _proxy_healthy
    if not COPILOT_PROXY_ENABLED:
        _proxy_healthy = False
        return False
    url = f"{COPILOT_PROXY_URL.rstrip('/')}/health"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                _proxy_healthy = resp.status < 500
                log.info(
                    "Copilot proxy health: %s (HTTP %d)",
                    "OK" if _proxy_healthy else "DEGRADED",
                    resp.status,
                )
                return _proxy_healthy
    except Exception as exc:
        _proxy_healthy = False
        log.warning("Copilot proxy unreachable: %s", exc)
        return False


def proxy_is_healthy() -> bool:
    """Return cached proxy health state."""
    return _proxy_healthy


# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_provider_sessions = _SessionManager(timeout=60, name="llm-providers")

# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------

_RETRY_ATTEMPTS = int(os.getenv("PROVIDER_RETRY_ATTEMPTS", "2"))
_RETRY_BASE_DELAY = float(os.getenv("PROVIDER_RETRY_BASE_DELAY", "1.0"))

# HTTP status codes that indicate a transient server-side problem and are safe to retry.
_TRANSIENT_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


async def _call_with_retry(
    coro_factory,
    provider: str,
    attempts: int = _RETRY_ATTEMPTS,
    base_delay: float = _RETRY_BASE_DELAY,
):
    """Call coro_factory() up to *attempts* times with exponential backoff on transient errors.

    *coro_factory* is a zero-arg callable that returns a new coroutine each call.
    Returns the result of the first successful call, or raises the last exception.

    Transient errors (retried): :class:`aiohttp.ClientConnectionError`,
    :class:`asyncio.TimeoutError`, and :class:`aiohttp.ClientResponseError` with
    HTTP status in ``_TRANSIENT_STATUS`` (429, 500, 502, 503, 504).

    Permanent errors (re-raised immediately): any other HTTP status (400, 401, 403, …)
    or unexpected exception type.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except aiohttp.ClientResponseError as exc:
            if exc.status not in _TRANSIENT_STATUS:
                raise  # permanent error — don't retry
            last_exc = exc
            delay = base_delay * (2**attempt)
            log.warning(
                "Provider %s attempt %d/%d failed (HTTP %d), retrying in %.1fs",
                provider, attempt + 1, attempts, exc.status, delay,
            )
            await asyncio.sleep(delay)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            last_exc = exc
            delay = base_delay * (2**attempt)
            log.warning(
                "Provider %s attempt %d/%d failed (%s), retrying in %.1fs",
                provider, attempt + 1, attempts, exc, delay,
            )
            await asyncio.sleep(delay)
        except Exception:
            raise  # non-transient, don't retry
    raise last_exc  # type: ignore[misc]


async def _retry_with_backoff(
    coro_fn,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_status: tuple = (429, 500, 502, 503, 504),
):
    """Legacy back-off helper — kept for any callers outside providers.py.

    New code should prefer :func:`_call_with_retry`.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except aiohttp.ClientResponseError as exc:
            if exc.status not in retryable_status:
                raise
            last_exc = exc
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            last_exc = exc
        if attempt < max_retries:
            delay = base_delay * (2**attempt) * random.uniform(0.8, 1.2)
            log.debug(
                "Retryable error on attempt %d; retrying in %.2fs", attempt, delay
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Simple half-open circuit breaker for non-Gemini providers
# ---------------------------------------------------------------------------
_CB_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
_CB_TIMEOUT   = float(os.getenv("CB_TIMEOUT_SECONDS", "30.0"))

_circuit: dict[str, dict] = {}  # provider -> {failures: int, open_until: float}


def _is_open(provider: str) -> bool:
    """Return True if the circuit is open (provider should be skipped)."""
    state = _circuit.get(provider, {})
    if state.get("open_until", 0) > _time.monotonic():
        return True
    return False


def _record_failure(provider: str) -> None:
    state = _circuit.setdefault(provider, {"failures": 0, "open_until": 0.0})
    state["failures"] += 1
    if state["failures"] >= _CB_THRESHOLD:
        state["open_until"] = _time.monotonic() + _CB_TIMEOUT
        log.warning("Circuit opened for provider %s for %.0fs", provider, _CB_TIMEOUT)


def _record_success(provider: str) -> None:
    _circuit.pop(provider, None)


def reset_circuit(provider: str | None = None) -> None:
    """Reset circuit state — intended for tests."""
    if provider:
        _circuit.pop(provider, None)
    else:
        _circuit.clear()


# ---------------------------------------------------------------------------
# Provider functions
# ---------------------------------------------------------------------------


async def chat_openai(
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message via OpenAI's API. Returns response text or None."""
    global _proxy_healthy
    if _is_open("openai"):
        return None
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key and not COPILOT_PROXY_ENABLED:
        return None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")

    # Use proxy only when enabled *and* currently healthy; otherwise fall back to direct OpenAI.
    use_proxy = COPILOT_PROXY_ENABLED and _proxy_healthy

    try:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            content = " ".join(p for p in msg["parts"] if isinstance(p, str))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        # Use Copilot proxy if available and healthy, otherwise direct OpenAI
        if use_proxy:
            base_url = COPILOT_PROXY_URL.rstrip("/")
            proxy_token = os.getenv("COPILOT_PROXY_TOKEN", api_key or "")
            headers = {"Content-Type": "application/json"}
            if proxy_token:
                headers["Authorization"] = f"Bearer {proxy_token}"
        else:
            base_url = "https://api.openai.com/v1"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

        session = await _provider_sessions.get()

        async def _do_openai_post() -> dict:
            async with session.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

        data = await _call_with_retry(_do_openai_post, "openai")
        content = data["choices"][0]["message"]["content"]
        _record_success("openai")
        # Record token usage
        usage = data.get("usage", {})
        inp = usage.get("prompt_tokens", 0)
        out = usage.get("completion_tokens", 0)
        _last_usage["input_tokens"] = inp
        _last_usage["output_tokens"] = out
        if inp or out:
            try:
                from spending import tracker as _spending
                await _spending.record_copilot(model=model)
            except Exception:
                pass
        elif COPILOT_PROXY_ENABLED:
            from spending import tracker as spending_tracker
            await spending_tracker.record_copilot(model=model)
        return content
    except aiohttp.ClientResponseError as e:
        log.warning("OpenAI returned HTTP %d", e.status)
        _record_failure("openai")
        return None
    except aiohttp.ClientError as e:
        log.warning("OpenAI call failed (connection error): %s", e)
        if use_proxy:
            _proxy_healthy = False
            log.warning("Proxy marked unhealthy after connection error")
        _record_failure("openai")
        return None
    except Exception as e:
        log.warning("OpenAI call failed: %s", e)
        _record_failure("openai")
        return None


async def chat_openai_vision(
    message: str,
    image_bytes: bytes,
    mime_type: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message with an inline image via OpenAI's vision API.

    Uses the Copilot proxy when available (GPT-4o-vision), otherwise falls
    back to the direct OpenAI API.  Returns the response text or ``None`` on
    failure.
    """
    import base64

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key and not COPILOT_PROXY_ENABLED:
        return None

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o")

    image_b64 = base64.b64encode(image_bytes).decode()
    user_content = [
        {"type": "text", "text": message},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
        },
    ]

    messages = [{"role": "user", "content": user_content}]

    if COPILOT_PROXY_ENABLED:
        base_url = COPILOT_PROXY_URL.rstrip("/")
        proxy_token = os.getenv("COPILOT_PROXY_TOKEN", api_key or "")
        headers = {"Content-Type": "application/json"}
        if proxy_token:
            headers["Authorization"] = f"Bearer {proxy_token}"
    else:
        base_url = "https://api.openai.com/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    try:
        session = await _provider_sessions.get()
        async with session.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                log.warning("OpenAI vision returned HTTP %d", resp.status)
                return None
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("OpenAI vision call failed: %s", e)
        return None


async def chat_anthropic(
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> Optional[str]:
    """Send a message via Anthropic's API. Returns response text or None."""
    # When Copilot proxy is available, route Claude calls through it
    # (the proxy serves Claude models in OpenAI-compatible format)
    if COPILOT_PROXY_ENABLED:
        return await chat_openai(
            message, history, system_prompt,
            model=model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5"),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if _is_open("anthropic"):
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")

    try:
        messages = []
        for msg in history[-10:]:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            content = " ".join(p for p in msg["parts"] if isinstance(p, str))
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        session = await _provider_sessions.get()

        async def _do_anthropic_post() -> dict:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "system": system_prompt,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

        data = await _call_with_retry(_do_anthropic_post, "anthropic")
        content_blocks = data.get("content", [])
        content = " ".join(
            b["text"] for b in content_blocks if b.get("type") == "text"
        )
        _record_success("anthropic")
        # Record token usage
        usage = data.get("usage", {})
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        _last_usage["input_tokens"] = inp
        _last_usage["output_tokens"] = out
        if inp or out:
            try:
                from spending import tracker as _spending
                await _spending.record_copilot(model=model)
            except Exception:
                pass
        return content
    except aiohttp.ClientResponseError as e:
        log.warning("Anthropic returned HTTP %d", e.status)
        _record_failure("anthropic")
        return None
    except Exception as e:
        log.warning("Anthropic call failed: %s", e)
        _record_failure("anthropic")
        return None


async def chat_ollama(
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
) -> Optional[str]:
    """Send a message to a local Ollama server via its /api/chat endpoint."""
    if _is_open("ollama"):
        return None

    model = model or _OLLAMA_DEFAULT_MODEL
    url = f"{_OLLAMA_BASE_URL}/api/chat"

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        role = "assistant" if msg["role"] == "model" else msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }

    try:
        async def _do_ollama_post() -> dict:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    r.raise_for_status()
                    return await r.json()

        data = await _call_with_retry(_do_ollama_post, "ollama")
        text = data.get("message", {}).get("content")
        inp = data.get("prompt_eval_count", 0)
        out = data.get("eval_count", 0)
        _last_usage["input_tokens"] = inp
        _last_usage["output_tokens"] = out
        _record_success("ollama")
        return text
    except aiohttp.ClientResponseError as e:
        log.warning("Ollama returned HTTP %d", e.status)
        _record_failure("ollama")
        return None
    except aiohttp.ClientError as e:
        log.warning("Ollama call failed (connection error): %s", e)
        _record_failure("ollama")
        return None
    except Exception as e:
        log.warning("Ollama call failed: %s", e)
        _record_failure("ollama")
        return None


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------


async def _call_one(
    provider: str,
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> "ProviderResponse | None":
    """Attempt a single provider call.  Returns ``None`` when the circuit is open
    or the underlying HTTP call fails; never raises.
    """
    if _is_open(provider):
        log.debug("Circuit open for %s — skipping", provider)
        return None
    if provider in ("openai", "copilot"):
        model_name = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_openai(message, history, system_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
        latency_ms = (_time.monotonic() - t0) * 1000
        if raw is None:
            return None
        return ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    if provider == "anthropic":
        model_name = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_anthropic(message, history, system_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
        latency_ms = (_time.monotonic() - t0) * 1000
        if raw is None:
            return None
        return ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    if provider == "ollama":
        model_name = model or _OLLAMA_DEFAULT_MODEL
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_ollama(message, history, system_prompt, model=model_name, temperature=temperature)
        latency_ms = (_time.monotonic() - t0) * 1000
        if raw is None:
            return None
        return ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    log.warning("_call_one: unknown provider %r", provider)
    return None


async def call_provider(
    provider: str,
    message: str,
    history: list[dict],
    system_prompt: str,
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
) -> ProviderResponse:
    """Route to the right provider, walking ``_FALLBACK_CHAIN`` on failure.

    Always returns a :class:`ProviderResponse`; ``resp.text`` is ``None`` when
    every provider in the chain failed (circuit open, API error, unknown, etc.).
    """
    seen: set[str] = set()
    chain: list[str] = []
    for p in [provider] + _FALLBACK_CHAIN:
        if p not in seen:
            seen.add(p)
            chain.append(p)

    for i, p in enumerate(chain):
        resp = await _call_one(p, message, history, system_prompt, model=model, temperature=temperature, max_tokens=max_tokens)
        if resp is not None and resp.text is not None:
            if i > 0:
                log.warning("call_provider: %s failed, using fallback provider %s", provider, p)
            _cumulative_tokens["input"] += resp.input_tokens
            _cumulative_tokens["output"] += resp.output_tokens
            return resp
        if i == 0 and len(chain) > 1:
            log.warning("call_provider: %s returned None, trying fallback chain", provider)

    return ProviderResponse(text=None, provider=provider, model=model, latency_ms=0.0)


# ---------------------------------------------------------------------------
# Streaming generators
# ---------------------------------------------------------------------------

async def _stream_openai(
    provider: str,
    prompt: str,
    history: list | None,
    system_prompt: str,
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    """Yield text chunks from OpenAI / Copilot-proxy streaming API (SSE)."""
    global _proxy_healthy  # noqa: PLW0603
    api_key = os.getenv("OPENAI_API_KEY", "")
    use_proxy = COPILOT_PROXY_ENABLED and _proxy_healthy

    if provider == "copilot" or use_proxy:
        base_url = COPILOT_PROXY_URL.rstrip("/")
        proxy_token = os.getenv("COPILOT_PROXY_TOKEN", api_key or "")
        headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if proxy_token:
            headers["Authorization"] = f"Bearer {proxy_token}"
    else:
        if not api_key:
            return
        base_url = "https://api.openai.com/v1"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    model_name = model or os.getenv("OPENAI_MODEL", "gpt-4o")
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in (history or [])[-10:]:
        role = "assistant" if msg["role"] == "model" else msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})

    session = await _provider_sessions.get()
    async with session.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        },
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        resp.raise_for_status()
        async for raw_line in resp.content:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = _json.loads(payload)
                chunk = (data["choices"][0].get("delta") or {}).get("content") or ""
                if chunk:
                    yield chunk
            except Exception:  # noqa: BLE001
                continue


async def _stream_anthropic(
    prompt: str,
    history: list | None,
    system_prompt: str,
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    """Yield text chunks from Anthropic streaming API (SSE).

    When a Copilot proxy is configured, routes through it (OpenAI-compat)
    instead of calling Anthropic directly.
    """
    if COPILOT_PROXY_ENABLED:
        async for chunk in _stream_openai(
            "copilot", prompt, history, system_prompt,
            model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5"),
            temperature, max_tokens,
        ):
            yield chunk
        return

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    model_name = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")
    messages: list[dict] = []
    for msg in (history or [])[-10:]:
        role = "assistant" if msg["role"] == "model" else msg["role"]
        content = " ".join(p for p in msg["parts"] if isinstance(p, str))
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})

    session = await _provider_sessions.get()
    async with session.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={
            "model": model_name,
            "system": system_prompt,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        },
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        resp.raise_for_status()
        async for raw_line in resp.content:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                data = _json.loads(payload)
                if data.get("type") == "content_block_delta":
                    chunk = (data.get("delta") or {}).get("text") or ""
                    if chunk:
                        yield chunk
            except Exception:  # noqa: BLE001
                continue


async def call_provider_stream(
    provider: str,
    prompt: str,
    *,
    history: list | None = None,
    system_prompt: str = "",
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> AsyncGenerator[str, None]:
    """Stream text chunks from a non-Gemini provider. Yields str chunks.

    Circuit-breaker aware: yields nothing when the provider circuit is open.
    Records success/failure in the circuit breaker after the stream ends.
    """
    if _is_open(provider):
        log.debug("Circuit open for %s — skipping stream", provider)
        return
    try:
        if provider in ("openai", "copilot"):
            async for chunk in _stream_openai(
                provider, prompt, history, system_prompt, model, temperature, max_tokens
            ):
                yield chunk
            _record_success(provider)
        elif provider == "anthropic":
            async for chunk in _stream_anthropic(
                prompt, history, system_prompt, model, temperature, max_tokens
            ):
                yield chunk
            _record_success(provider)
        else:
            log.warning("call_provider_stream: unknown provider %r", provider)
    except Exception as exc:
        _record_failure(provider)
        log.warning("Stream error from %s: %s", provider, exc)


async def scan_providers() -> dict[str, bool]:
    """Run parallel lightweight pings to all configured providers. Returns availability map."""
    import asyncio as _asyncio

    async def _ping_copilot() -> bool:
        if not COPILOT_PROXY_ENABLED:
            return False
        return await check_proxy_health()

    async def _ping_ollama() -> bool:
        try:
            from model_routing_policy import is_ollama_alive

            return await is_ollama_alive()
        except Exception:
            return False

    async def _ping_openai() -> bool:
        import os as _os

        return bool(_os.getenv("OPENAI_API_KEY"))

    async def _ping_anthropic() -> bool:
        import os as _os

        return bool(_os.getenv("ANTHROPIC_API_KEY"))

    copilot, ollama, openai, anthropic = await _asyncio.gather(
        _ping_copilot(),
        _ping_ollama(),
        _ping_openai(),
        _ping_anthropic(),
        return_exceptions=True,
    )
    return {
        "copilot": copilot is True,
        "ollama": ollama is True,
        "openai": openai is True,
        "anthropic": anthropic is True,
    }
