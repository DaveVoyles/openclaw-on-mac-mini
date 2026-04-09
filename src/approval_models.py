"""
OpenClaw Approval Models — data classes and enumerations.

Separated from approvals.py to allow import by both the store and the UI
without circular dependency.
"""

import time
from dataclasses import dataclass, field
from enum import Enum

# How long an approval request stays valid (seconds)
APPROVAL_TTL = 300  # 5 minutes


class RiskLevel(Enum):
    LOW = "LOW"          # Auto-execute, no approval
    MEDIUM = "MEDIUM"    # Execute with enhanced logging
    HIGH = "HIGH"        # Requires explicit approval via buttons
    CRITICAL = "CRITICAL"  # Requires approval + shows dry-run preview


@dataclass
class ApprovalRequest:
    """A single pending action awaiting approval."""
    request_id: str
    action: str                  # e.g. "restart_container"
    target: str                  # e.g. "sonarr"
    risk_level: RiskLevel
    requester_id: int
    requester_name: str
    channel_id: int
    created_at: float = field(default_factory=time.monotonic)
    resolved: bool = False
    approved: bool = False
    resolver_id: int | None = None
    resolver_name: str | None = None
    detail: str = ""             # Extra context (dry-run output, etc.)

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > APPROVAL_TTL

    @property
    def age_seconds(self) -> int:
        return int(time.monotonic() - self.created_at)
