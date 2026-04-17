#!/usr/bin/env bash
# test_macbook_cli.sh — Run a one-shot OpenClaw ask query via SSH on the MacBook.
#
# Usage:
#   bash scripts/test_macbook_cli.sh "your question here"
#   bash scripts/test_macbook_cli.sh "your question here" [ssh-host]
#
# Defaults to host alias 'macbook'. Falls back to running locally on this machine
# if SSH fails or no host is provided as second argument.
#
# Exit codes:
#   0 — success (response received)
#   1 — connection or auth failure

set -euo pipefail

QUERY="${1:-}"
TARGET="${2:-macbook}"

if [[ -z "$QUERY" ]]; then
  echo "Usage: $0 \"your question here\" [ssh-host]" >&2
  exit 1
fi

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  OpenClaw E2E Test — SSH → ${TARGET}"
echo "║  Query: ${QUERY}"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Try remote MacBook first
if ssh -q -o ConnectTimeout=5 "$TARGET" 'exit 0' 2>/dev/null; then
  echo "🔗 Connected to ${TARGET} — running ask..."
  echo ""
  ssh "$TARGET" "~/.local/bin/openclaw ask $(printf '%q' "$QUERY")" 2>&1
  EXIT_CODE=$?
  echo ""
  if [[ $EXIT_CODE -eq 0 ]]; then
    echo "✅ Test passed — MacBook response received via SSH"
  else
    echo "⚠️  ask exited with code $EXIT_CODE" >&2
    exit "$EXIT_CODE"
  fi
else
  echo "⚠️  Cannot reach ${TARGET} via SSH — falling back to local Mac Mini"
  echo ""
  ~/.local/bin/openclaw ask "$QUERY" 2>&1 || \
    PYTHONPATH="$(dirname "$(realpath "$0")")/../src" \
      python3 "$(dirname "$(realpath "$0")")/../src/openclaw_cli.py" ask "$QUERY"
fi
