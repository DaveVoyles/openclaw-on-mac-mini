"""
OpenClaw LLM Providers — single source of truth for non-Gemini provider API calls.

Handles OpenAI, Anthropic, and Copilot-proxy HTTP calls.
"""

import asyncio
import dataclasses
import logging
import os
import random
import time as _time
from typing import Optional

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
_proxy_healthy: bool = True

# Configurable fallback chain: primary provider is prepended at call time.
_FALLBACK_CHAIN_RAW = os.getenv("PROVIDER_FALLBACK_CHAIN", "copilot,ollama")
_FALLBACK_CHAIN: list[str] = [p.strip() for p in _FALLBACK_CHAIN_RAW.split(",") if p.strip()]

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


async def check_proxy_health() -> bool:
    """Ping the Copilot proxy /health endpoint and update ``_proxy_healthy``.

    Only runs when ``COPILOT_PROXY_ENABLED`` is True.  Returns the new value
    of ``_proxy_healthy`` so callers can log or branch on it.
    """
    global _proxy_healthy
    if not COPILOT_PROXY_ENABLED:
        return _proxy_healthy
    try:
        session = await _provider_sessions.get()
        async with session.get(
            f"{COPILOT_PROXY_URL.rstrip('/')}/health",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            _proxy_healthy = resp.status == 200
    except aiohttp.ClientError:
        _proxy_healthy = False
    return _proxy_healthy


# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_provider_sessions = _SessionManager(timeout=60, name="llm-providers")

# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------


async def _retry_with_backoff(
    coro_fn,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_status: tuple = (429, 500, 502, 503, 504),
):
    """Retry *coro_fn* with exponential back-off on transient HTTP errors.

    *coro_fn* must be a zero-arg async callable.  On a retryable
    :class:`aiohttp.ClientResponseError` or
    :class:`aiohttp.ClientConnectionError` the helper sleeps
    ``base_delay * 2**attempt * jitter`` seconds and tries again.
    Non-retryable HTTP errors are re-raised immediately.
    After *max_retries* exhausted, the last exception is re-raised.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except aiohttp.ClientResponseError as exc:
            if exc.status not in retryable_status:
                raise
            last_exc = exc
        except aiohttp.ClientConnectionError as exc:
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

        data = await _retry_with_backoff(_do_openai_post)
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

        data = await _retry_with_backoff(_do_anthropic_post)
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


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------


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
    """Route to the right provider. provider is one of: openai, anthropic, copilot.

    Always returns a :class:`ProviderResponse`; ``resp.text`` is ``None`` when
    the provider call failed (circuit open, API error, unknown provider, etc.).
    """
    if _is_open(provider):
        log.debug("Circuit open for %s — skipping", provider)
        return ProviderResponse(text=None, provider=provider, model=model, latency_ms=0.0)
    if provider == "openai":
        model_name = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_openai(message, history, system_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
        latency_ms = (_time.monotonic() - t0) * 1000
        resp = ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    elif provider == "anthropic":
        model_name = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4.5")
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_anthropic(message, history, system_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
        latency_ms = (_time.monotonic() - t0) * 1000
        resp = ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    elif provider == "copilot":
        # copilot uses openai-compat with proxy URL
        model_name = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        t0 = _time.monotonic()
        _last_usage.update(input_tokens=0, output_tokens=0)
        raw = await chat_openai(message, history, system_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
        latency_ms = (_time.monotonic() - t0) * 1000
        resp = ProviderResponse(text=raw, provider=provider, model=model_name, latency_ms=latency_ms, input_tokens=_last_usage["input_tokens"], output_tokens=_last_usage["output_tokens"])
    else:
        log.warning("call_provider: unknown provider %r", provider)
        return ProviderResponse(text=None, provider=provider, model=model, latency_ms=0.0)
    if resp.text is not None:
        _cumulative_tokens["input"] += resp.input_tokens
        _cumulative_tokens["output"] += resp.output_tokens
    return resp
