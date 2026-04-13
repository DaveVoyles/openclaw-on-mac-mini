#!/usr/bin/env python3
"""Quick smoke test for all configured LLM providers.

Usage:
  python scripts/provider_smoke_test.py
  python scripts/provider_smoke_test.py --providers copilot,ollama
  python scripts/provider_smoke_test.py --message "Say hello in 5 words"
  python scripts/provider_smoke_test.py --json
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

ALL_PROVIDERS = ["copilot", "openai", "anthropic", "ollama"]
DEFAULT_MESSAGE = "Reply with exactly: SMOKE_TEST_OK"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test all configured LLM providers.")
    parser.add_argument(
        "--providers",
        default=",".join(ALL_PROVIDERS),
        help="Comma-separated list of providers to test (default: all)",
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Test message to send to each provider",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait per provider (default: 30)",
    )
    return parser.parse_args()


async def test_provider(provider: str, message: str, timeout: float) -> dict:
    try:
        from llm.providers import call_provider  # noqa: PLC0415

        resp = await asyncio.wait_for(
            call_provider(provider, message, [], ""),
            timeout=timeout,
        )
        if resp and resp.text:
            return {
                "provider": provider,
                "ok": True,
                "model": resp.model or "(unknown)",
                "latency_ms": resp.latency_ms,
                "preview": resp.text[:80],
                "error": None,
            }
        return {
            "provider": provider,
            "ok": False,
            "model": "(error)",
            "latency_ms": None,
            "preview": None,
            "error": "Provider returned empty response",
        }
    except TimeoutError:
        return {
            "provider": provider,
            "ok": False,
            "model": "(error)",
            "latency_ms": None,
            "preview": None,
            "error": f"Timed out after {timeout}s",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider": provider,
            "ok": False,
            "model": "(error)",
            "latency_ms": None,
            "preview": None,
            "error": str(exc),
        }


async def run(providers: list[str], message: str, timeout: float) -> list[dict]:
    tasks = [test_provider(p, message, timeout) for p in providers]
    return await asyncio.gather(*tasks)


def print_table(results: list[dict], dev_mode: bool) -> None:
    print()
    header = "Provider Smoke Test Results"
    if dev_mode:
        header += "  [DEV MODE — stub responses]"
    print(header)
    print("=" * len(header))

    for r in results:
        icon = "✅" if r["ok"] else "❌"
        provider = r["provider"].ljust(10)
        model = r["model"].ljust(12)
        latency = f"{int(r['latency_ms'])}ms".ljust(8) if r["latency_ms"] is not None else "---     "
        text = f'"{r["preview"]}"' if r["preview"] else f'"{r["error"]}"'
        print(f"{icon} {provider} → {model} {latency} {text}")

    healthy = sum(1 for r in results if r["ok"])
    total = len(results)
    print()
    print(f"{healthy}/{total} providers healthy")


def main() -> None:
    args = parse_args()
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    dev_mode = os.getenv("DEV_MODE", "").lower() in ("1", "true", "yes")

    try:
        results = asyncio.run(run(providers, args.message, args.timeout))
    except ImportError as exc:
        msg = f"Import error (check env/deps): {exc}"
        if args.output_json:
            print(json.dumps({"error": msg, "results": [], "healthy": 0, "total": 0}))
        else:
            print(f"❌ {msg}", file=sys.stderr)
        sys.exit(1)

    healthy = sum(1 for r in results if r["ok"])
    total = len(results)

    if args.output_json:
        print(json.dumps({"results": results, "healthy": healthy, "total": total}, indent=2))
    else:
        print_table(results, dev_mode)

    sys.exit(0 if healthy == total else 1)


if __name__ == "__main__":
    main()
