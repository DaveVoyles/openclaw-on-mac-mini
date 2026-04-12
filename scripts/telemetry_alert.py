#!/usr/bin/env python3
"""
Telemetry rolling-window alert script.

Reads data/routing_audit.jsonl, computes per-provider success rate and latency
over the last N records, and exits non-zero if any provider falls below the
success threshold or exceeds the latency limit.

Usage:
    python scripts/telemetry_alert.py [--path PATH] [--last N] [--threshold FLOAT]
                                       [--provider PROVIDER] [--max-latency MS]
                                       [--latency-percentile P]

Exit codes:
    0  All providers above threshold and below latency limit
    1  One or more providers below threshold or above latency limit
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


def compute_latencies(records: list[dict]) -> dict[str, list[float]]:
    """Return {provider: [latency_ms, ...]} for each provider."""
    latencies: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        provider = rec.get("provider", "unknown")
        lat = rec.get("latency_ms")
        if lat is not None:
            latencies[provider].append(float(lat))
    return dict(latencies)


def percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Alert when provider success rate drops below a threshold or latency exceeds a limit."
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
        help="Minimum acceptable success rate 0.0-1.0 (default: 0.90)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Check only this provider (default: all providers)",
    )
    parser.add_argument(
        "--max-latency",
        type=int,
        default=2000,
        metavar="MS",
        help="Maximum acceptable pN latency in ms (default: 2000)",
    )
    parser.add_argument(
        "--latency-percentile",
        type=int,
        default=95,
        choices=[50, 75, 90, 95, 99],
        metavar="P",
        help="Percentile to check for latency (50, 75, 90, 95, 99) (default: 95)",
    )
    args = parser.parse_args()

    records = load_records(args.path, args.last)

    if not records:
        print(f"⚠️  No telemetry data found at: {args.path}", file=sys.stderr)
        sys.exit(2)

    rates = compute_rates(records)
    latencies = compute_latencies(records)

    if args.provider:
        if args.provider not in rates:
            print(
                f"⚠️  Provider '{args.provider}' not found in last {args.last} records.",
                file=sys.stderr,
            )
            sys.exit(2)
        rates = {args.provider: rates[args.provider]}
        latencies = {k: v for k, v in latencies.items() if k == args.provider}

    threshold_pct = args.threshold * 100
    p = args.latency_percentile
    max_lat = args.max_latency

    failing_rate = {
        prov: (s, t)
        for prov, (s, t) in rates.items()
        if t > 0 and (s / t) < args.threshold
    }
    failing_latency = {
        prov: percentile(lats, p)
        for prov, lats in latencies.items()
        if percentile(lats, p) > max_lat
    }

    if failing_rate or failing_latency:
        if failing_rate:
            print(f"⚠️  ALERT: Provider success rates below threshold ({threshold_pct:.1f}%):")
            for prov, (successes, total) in sorted(failing_rate.items()):
                rate = successes / total * 100
                print(f"  {prov}: {rate:.1f}%")
        if failing_latency:
            print(f"⚠️  ALERT: Provider p{p} latency above limit ({max_lat}ms):")
            for prov, lat_val in sorted(failing_latency.items()):
                print(f"  {prov}: p{p}={lat_val:.0f}ms")
        sys.exit(1)

    print(
        f"✅ All providers above threshold ({threshold_pct:.1f}%) and below latency limit ({max_lat}ms p{p}):"
    )
    for prov, (successes, total) in sorted(rates.items()):
        rate = successes / total * 100 if total > 0 else 0.0
        lat_val = percentile(latencies.get(prov, []), p)
        print(f"  {prov}: {rate:.1f}% success, p{p}={lat_val:.0f}ms")
    sys.exit(0)


if __name__ == "__main__":
    main()
