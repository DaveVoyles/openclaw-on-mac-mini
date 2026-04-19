#!/usr/bin/env bash
# install_mac_watcher.sh — installs the OpenClaw Mac folder watcher.
# Run once on parent's Mac. Requires SSH access to macmini.
#
# NOTE: The canonical installer is scripts/install_watcher.sh (referenced in
# Makefile and docs/PARENTS-GUIDE.md). This script targets mac_folder_watcher.py
# instead of watch_folder.sh and uses a different log-path layout. Prefer
# install_watcher.sh unless you specifically need the python-watcher variant.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_DIR/scripts/mac_folder_watcher.py"
PLIST_SRC="$REPO_DIR/scripts/com.openclaw.watcher.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.openclaw.watcher.plist"
LOGS_DIR="$HOME/Library/Logs/OpenClaw"
WATCH_DIR="$HOME/Documents/OpenClaw"

# 1. Create directories
mkdir -p "$WATCH_DIR" "$LOGS_DIR"
echo "✅ Watch folder: $WATCH_DIR"

# 2. Verify SSH access
echo "🔍 Checking SSH access to macmini..."
ssh -o ConnectTimeout=5 -o BatchMode=yes macmini "echo ok" \
  || { echo "❌ Cannot reach macmini via SSH. See PARENTS-GUIDE.md for setup."; exit 1; }

# 3. Verify /ai-files exists on Mac Mini
ssh macmini "test -d /ai-files" \
  || { echo "❌ /ai-files not found on macmini. Is OpenClaw running?"; exit 1; }

# 4. Install plist
sed \
  -e "s|SCRIPT_PATH|$SCRIPT|g" \
  -e "s|LOGS_PATH|$LOGS_DIR|g" \
  -e "s|LOGS_DIR|$LOGS_DIR|g" \
  "$PLIST_SRC" > "$PLIST_DEST"
echo "✅ Plist installed: $PLIST_DEST"

# 5. Load launchd job (unload first if already loaded)
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"
echo "✅ Watcher started."

echo ""
echo "Done! Drop a .docx, .xlsx, or .pdf into:"
echo "  $WATCH_DIR"
echo "It will sync to OpenClaw within 60 seconds."
echo "Logs: $LOGS_DIR/openclaw-watcher.log"
