#!/usr/bin/env python3
"""
Telemetry rolling-window alert script.

Reads data/routing_audit.jsonl, computes per-provider success rate over the
last N records, and exits non-zero if any provider falls below the threshold.

Usage:
    python scripts/telemetry_alert.py [--path PATH] [--last N] [--threshold FLOAT] [--provider PROVIDER]

Exit codes:
    0  All providers above threshold
    1  One or more providers below threshold
    2  No telemetry data found
"""

import argparse
import json
import sys
from collections import defaultdict


def load_records(path: str, last: int) -> list[dict]:
    try:
        with open(path) as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records[-last:]


def compute_rates(records: list[dict]) -> dict[str, tuple[int, int]]:
    """Return {provider: (successes, total)} for each provider."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rec in records:
        provider = rec.get("provider", "unknown")
        counts[provider][1] += 1
        if rec.get("success", False):
            counts[provider][0] += 1
    return {p: (s, t) for p, (s, t) in counts.items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Alert when provider success rate drops below a threshold."
    )
    parser.add_argument(
        "--path",
        default="data/routing_audit.jsonl",
        help="Path to routing_audit.jsonl (default: data/routing_audit.jsonl)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=100,
        metavar="N",
        help="Rolling window: last N records to evaluate (default: 100)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.90,
        help="Minimum acceptable success rate 0.0–1.0 (default: 0.90)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Check only this provider (default: all providers)",
    )
    args = parser.parse_args()

    records = load_records(args.path, args.last)

    if not records:
        print(f"⚠️  No telemetry data found at: {args.path}", file=sys.stderr)
        sys.exit(2)

    rates = compute_rates(records)

    if args.provider:
        if args.provider not in rates:
            print(
                f"⚠️  Provider '{args.provider}' not found in last {args.last} records.",
                file=sys.stderr,
            )
            sys.exit(2)
        rates = {args.provider: rates[args.provider]}

    threshold_pct = args.threshold * 100
    failing = {
        p: (s, t)
        for p, (s, t) in rates.items()
        if t > 0 and (s / t) < args.threshold
    }

    if failing:
        print(f"⚠️  ALERT: Provider success rates below threshold ({threshold_pct:.1f}%):")
        for provider, (successes, total) in sorted(failing.items()):
            rate = successes / total * 100
            print(f"  {provider}: {rate:.1f}% (success={successes}/{total})")
        sys.exit(1)

    print(f"✅ All providers above threshold ({threshold_pct:.1f}%):")
    for provider, (successes, total) in sorted(rates.items()):
        rate = successes / total * 100 if total > 0 else 0.0
        print(f"  {provider}: {rate:.1f}% (success={successes}/{total})")
    sys.exit(0)


if __name__ == "__main__":
    main()
