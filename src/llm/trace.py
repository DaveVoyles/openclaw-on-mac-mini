"""Per-request metadata trace for response transparency."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RequestTrace:
    """Accumulates metadata about a single LLM request as it flows through the stack."""

    model_used: str = ""
    provider: str = ""
    skills_invoked: list[str] = field(default_factory=list)
    routing_reason: str = ""
    latency_ms: float = 0.0
    mini_model_used: bool = False
