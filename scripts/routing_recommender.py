#!/usr/bin/env python3
"""Reads data/routing_audit.jsonl and emits routing profile recommendations."""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Routing profile recommender")
    parser.add_argument(
        "--path",
        default="data/routing_audit.jsonl",
        help="Path to the routing audit JSONL file",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=1000,
        metavar="N",
        help="Use only the last N records (default: 1000)",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output raw stats as JSON instead of Markdown",
    )
    return parser.parse_args()


def load_records(path: Path, tail: int) -> tuple[list[dict], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = lines[-tail:]
    records: list[dict] = []
    skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    return records, skipped


def compute_stats(records: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "calls": 0,
            "successes": 0,
            "latencies": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    for rec in records:
        provider = rec.get("provider", "unknown")
        b = buckets[provider]
        b["calls"] += 1
        if rec.get("success", False):
            b["successes"] += 1
        latency = rec.get("latency_ms")
        if latency is not None:
            b["latencies"].append(float(latency))
        b["input_tokens"] += int(rec.get("input_tokens", 0))
        b["output_tokens"] += int(rec.get("output_tokens", 0))

    stats: dict[str, dict] = {}
    for provider, b in buckets.items():
        calls = b["calls"]
        success_rate = (b["successes"] / calls * 100) if calls else 0.0
        lats = b["latencies"]
        avg_latency = statistics.mean(lats) if lats else 0.0
        p95_latency = (
            statistics.quantiles(lats, n=20)[18] if len(lats) >= 2 else (lats[0] if lats else 0.0)
        )
        stats[provider] = {
            "calls": calls,
            "success_rate": round(success_rate, 1),
            "avg_latency_ms": round(avg_latency, 1),
            "p95_latency_ms": round(p95_latency, 1),
            "input_tokens": b["input_tokens"],
            "output_tokens": b["output_tokens"],
        }
    return stats


def build_recommendations(stats: dict[str, dict]) -> list[str]:
    recs: list[str] = []

    slow = {p: s for p, s in stats.items() if s["p95_latency_ms"] > 2000}
    fast = {p: s for p, s in stats.items() if s["p95_latency_ms"] < 800}
    for slow_p in slow:
        for fast_p in fast:
            if slow_p != fast_p:
                recs.append(
                    f"⚠️  Consider switching from **{slow_p}** to **{fast_p}** profile "
                    f"(p95 latency {slow[slow_p]['p95_latency_ms']:.0f} ms vs "
                    f"{fast[fast_p]['p95_latency_ms']:.0f} ms)."
                )

    for provider, s in stats.items():
        if s["success_rate"] < 90.0:
            recs.append(
                f"🔴 Provider **{provider}** has low reliability "
                f"({s['success_rate']}% success rate)."
            )

    if not recs:
        recs.append("✅ All providers are performing within acceptable parameters.")

    return recs


def print_markdown(stats: dict[str, dict], recs: list[str], skipped: int) -> None:
    print("## Routing Profile Recommendations\n")
    header = "| Provider | Calls | Success % | Avg Latency | p95 Latency | Input Tokens | Output Tokens |"
    sep = "|----------|-------|-----------|-------------|-------------|--------------|---------------|"
    print(header)
    print(sep)
    for provider, s in sorted(stats.items()):
        print(
            f"| {provider:<8} | {s['calls']:>5} | {s['success_rate']:>8.1f}% "
            f"| {s['avg_latency_ms']:>9.0f} ms | {s['p95_latency_ms']:>9.0f} ms "
            f"| {s['input_tokens']:>12,} | {s['output_tokens']:>13,} |"
        )
    print()
    for provider, s in sorted(stats.items()):
        p95 = s["p95_latency_ms"]
        if p95 <= 2000:
            print(f"💡 {provider} p95 latency is {p95:.0f} ms — within acceptable range.")
    print()
    for rec in recs:
        print(rec)
    if skipped:
        print(f"\n⚠️  Skipped {skipped} malformed line(s).")


def main() -> None:
    args = parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f"No audit log found at {path}")
        sys.exit(0)

    records, skipped = load_records(path, args.tail)

    if not records:
        print("No valid records found in the audit log.")
        sys.exit(0)

    stats = compute_stats(records)

    if args.output_json:
        print(json.dumps(stats, indent=2))
        return

    recs = build_recommendations(stats)
    print_markdown(stats, recs, skipped)


if __name__ == "__main__":
    main()
