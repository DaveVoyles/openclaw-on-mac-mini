#!/usr/bin/env bash
# Uninstall the OpenClaw standalone CLI from a remote Mac via SSH.
# Usage (run from Mac Mini):  bash scripts/uninstall_openclaw_cli_remote.sh [user@]host
set -euo pipefail

TARGET_HOST="${1:-macbook}"

echo "Uninstalling OpenClaw CLI from ${TARGET_HOST}…"

ssh "$TARGET_HOST" bash -s <<'REMOTE'
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/openclaw-cli"
BIN_DIR="$HOME/.local/bin"
RC_FILE="$HOME/.zshrc"

# Remove install directory
if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  echo "  ✓ removed $INSTALL_DIR"
fi

# Remove wrapper and symlink
for f in "$BIN_DIR/openclaw" "$BIN_DIR/openclaw-cli"; do
  if [[ -e "$f" || -L "$f" ]]; then
    rm -f "$f"
    echo "  ✓ removed $f"
  fi
done

# Strip OpenClaw lines from .zshrc
if [[ -f "$RC_FILE" ]]; then
  tmpfile=$(mktemp)
  grep -v \
    -e 'openclaw-cli/openclaw_aliases' \
    -e 'export OPENCLAW_URL=' \
    "$RC_FILE" > "$tmpfile" && mv "$tmpfile" "$RC_FILE"
  echo "  ✓ cleaned $RC_FILE"
fi

echo "OpenClaw CLI uninstalled."
REMOTE
