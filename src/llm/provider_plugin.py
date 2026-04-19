"""Provider plugin protocol for the OpenClaw LLM layer.

Third-party providers should implement this protocol and register via
``providers.register_provider_plugin()``.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class ProviderPlugin(Protocol):
    """Minimal interface a provider plugin must satisfy.

    Implement all three methods and pass an instance to
    ``llm.providers.register_provider_plugin(name, plugin)``.
    """

    async def call(
        self,
        message: str,
        history: list[dict],
        system_prompt: str,
        **kwargs: object,
    ) -> str | None:
        """Return a text response, or None on failure."""
        ...

    async def ping(self) -> tuple[bool, float]:
        """Return (is_available, latency_ms)."""
        ...

    async def stream(
        self,
        message: str,
        history: list[dict],
        system_prompt: str,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        """Yield response tokens incrementally."""
        ...
