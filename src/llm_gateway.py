"""Backward-compatible shim for legacy LLMGateway imports."""

from llm.chat import chat as _chat


class LLMGateway:
    """Compatibility facade for older integration tests and imports."""

    @staticmethod
    async def chat(*args, **kwargs):
        return await _chat(*args, **kwargs)
