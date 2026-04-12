#!/usr/bin/env python3
"""
Offline summary of routing_audit.jsonl telemetry.

Usage:
    python scripts/telemetry_summary.py [--path PATH] [--last N] [--provider PROVIDER]
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone


def load_records(path: str, last: int | None, provider: str | None) -> list[dict]:
    try:
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        print(f"File not found: {path}", file=sys.stderr)
        return []

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return []

    if last is not None:
        records = records[-last:]

    if provider:
        records = [r for r in records if r.get("provider") == provider]

    return records


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def build_provider_stats(records: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "successes": 0, "latencies": [], "tokens": 0}
    )
    for r in records:
        prov = r.get("provider", "unknown")
        stats[prov]["calls"] += 1
        if r.get("success"):
            stats[prov]["successes"] += 1
        lat = r.get("latency_ms", 0.0)
        stats[prov]["latencies"].append(lat)
        stats[prov]["tokens"] += r.get("tokens", 0)
    return stats


def build_query_type_stats(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in records:
        qt = r.get("query_type", "unknown")
        counts[qt] += 1
    return counts


def print_provider_table(stats: dict[str, dict]) -> None:
    header = f"| {'Provider':<12} | {'Calls':>5} | {'Success%':>8} | {'Avg ms':>7} | {'P95 ms':>7} | {'Tokens':>8} |"
    sep    = f"| {'-'*12} | {'-'*5} | {'-'*8} | {'-'*7} | {'-'*7} | {'-'*8} |"
    print(header)
    print(sep)
    for prov, s in sorted(stats.items()):
        calls = s["calls"]
        success_pct = (s["successes"] / calls * 100) if calls else 0.0
        avg_lat = sum(s["latencies"]) / len(s["latencies"]) if s["latencies"] else 0.0
        p95_lat = p95(s["latencies"])
        tokens = s["tokens"]
        print(
            f"| {prov:<12} | {calls:>5} | {success_pct:>7.1f}% | {avg_lat:>7.1f} | {p95_lat:>7.1f} | {tokens:>8} |"
        )


def print_query_type_table(counts: dict[str, int]) -> None:
    header = f"| {'Type':<16} | {'Calls':>5} |"
    sep    = f"| {'-'*16} | {'-'*5} |"
    print(header)
    print(sep)
    for qt, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"| {qt:<16} | {count:>5} |")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline summary of routing_audit.jsonl telemetry."
    )
    parser.add_argument(
        "--path",
        default="data/routing_audit.jsonl",
        help="Path to the JSONL audit log (default: data/routing_audit.jsonl)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        metavar="N",
        help="Only summarise the last N records",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Filter to a single provider name",
    )
    args = parser.parse_args()

    records = load_records(args.path, args.last, args.provider)

    if not records:
        label = f"last {args.last} records" if args.last else "all records"
        print(f"## Routing Telemetry Summary ({label})")
        print("No records found.")
        return

    label = f"last {args.last} records" if args.last else f"all {len(records)} records"
    print(f"## Routing Telemetry Summary ({label})")

    timestamps = [r["ts"] for r in records if "ts" in r]
    if timestamps:
        print(f"Period: {fmt_ts(min(timestamps))} → {fmt_ts(max(timestamps))}")

    print()
    provider_stats = build_provider_stats(records)
    print_provider_table(provider_stats)

    print()
    print("## Query Types")
    query_counts = build_query_type_stats(records)
    print_query_type_table(query_counts)


if __name__ == "__main__":
    main()
