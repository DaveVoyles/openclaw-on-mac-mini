"""Lightweight routing telemetry — appends JSONL audit records."""

import json
import logging
import os
import pathlib
import time
from collections import defaultdict

log = logging.getLogger(__name__)

_LOG_PATH = pathlib.Path(
    os.getenv("ROUTING_TELEMETRY_PATH", str(pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "routing_audit.jsonl"))
)
_ENABLED = os.getenv("ROUTING_TELEMETRY", "false").lower() in ("1", "true", "yes")
_AUDIT_MAX_LINES = int(os.getenv("AUDIT_MAX_LINES", "10000"))
_AUDIT_KEEP_LINES = int(os.getenv("AUDIT_KEEP_LINES", "8000"))


def record(
    provider: str,
    model: str,
    latency_ms: float,
    success: bool,
    query_type: str = "unknown",
    tokens_used: int = 0,
) -> None:
    """Append one routing event to the JSONL audit log (no-op if disabled)."""
    if not _ENABLED:
        return
    entry = {
        "ts": time.time(),
        "provider": provider,
        "model": model,
        "latency_ms": round(latency_ms, 1),
        "success": success,
        "query_type": query_type,
        "tokens": tokens_used,
    }
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.debug("Telemetry write failed: %s", exc)


async def rotate_audit_log() -> None:
    """Trim routing_audit.jsonl to _AUDIT_KEEP_LINES if over _AUDIT_MAX_LINES. Safe to call anytime."""
    try:
        if not _LOG_PATH.exists():
            return
        with _LOG_PATH.open() as f:
            lines = f.readlines()
        if len(lines) > _AUDIT_MAX_LINES:
            with _LOG_PATH.open("w") as f:
                f.writelines(lines[-_AUDIT_KEEP_LINES:])
    except Exception:  # noqa: BLE001
        pass


def tail(n: int = 20) -> list[dict]:
    """Return the last n telemetry records (for /metrics command)."""
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
        return [json.loads(line) for line in lines[-n:]]
    except (OSError, json.JSONDecodeError):
        return []


def summarise(records: list[dict]) -> str:
    """Format a brief human-readable summary of telemetry records."""
    if not records:
        return "No telemetry records found. Set `ROUTING_TELEMETRY=true` to enable."

    total = len(records)
    successes = sum(1 for r in records if r.get("success"))
    latencies = [r["latency_ms"] for r in records if isinstance(r.get("latency_ms"), (int, float))]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

    provider_counts: dict[str, int] = defaultdict(int)
    for r in records:
        provider_counts[r.get("provider", "unknown")] += 1
    provider_summary = ", ".join(
        f"{p}: {c}" for p, c in sorted(provider_counts.items(), key=lambda x: -x[1])
    )

    return (
        f"**Last {total} routing calls**\n"
        f"✅ Success: {successes}/{total} "
        f"| ⏱ Avg latency: {avg_latency} ms\n"
        f"**Providers** — {provider_summary}"
    )
