#!/usr/bin/env bash
# install_watcher.sh — One-click installer for the OpenClaw Mac Folder Watcher.
#
# Run this once on your Mac to set up automatic document syncing.
# After installation, any .docx or .xlsx file you drop into
# ~/Documents/OpenClaw/ will sync to OpenClaw automatically.
#
# Requirements:
#   - SSH key configured for macmini (ask Dave if you're not sure)
#   - macOS 10.13 or later

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_SRC="$REPO_DIR/scripts/watch_folder.sh"
PLIST_SRC="$REPO_DIR/scripts/com.openclaw.watcher.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.openclaw.watcher.plist"
LOGS_DIR="$HOME/Library/Logs"
WATCH_DIR="$HOME/Documents/OpenClaw"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║     OpenClaw Folder Watcher — Installer      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# 1. Create watch directory
mkdir -p "$WATCH_DIR"
echo "✅ Watch folder ready: $WATCH_DIR"

# 2. Check SSH access to macmini
echo ""
echo "🔍 Checking connection to OpenClaw server (macmini)..."
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        macmini "exit 0" 2>/dev/null; then
    echo ""
    echo "❌ Could not connect to macmini via SSH."
    echo ""
    echo "   Before running this installer, you need to set up SSH access."
    echo "   Ask Dave to help, or see docs/PARENTS-GUIDE.md for instructions."
    echo ""
    exit 1
fi
echo "✅ Connected to macmini"

# 3. Verify /ai-files exists and is writable on Mac Mini
echo ""
echo "🔍 Checking OpenClaw storage on macmini..."
if ! ssh macmini "test -d /ai-files" 2>/dev/null; then
    echo ""
    echo "❌ The /ai-files folder was not found on macmini."
    echo "   Is OpenClaw running? Ask Dave to check."
    echo ""
    exit 1
fi
if ! ssh macmini "touch /ai-files/.openclaw-write-test && rm /ai-files/.openclaw-write-test" 2>/dev/null; then
    echo ""
    echo "❌ Cannot write to /ai-files on macmini. Ask Dave to fix the permissions."
    echo ""
    exit 1
fi
echo "✅ OpenClaw storage is ready"

# 4. Make watcher script executable
chmod +x "$SCRIPT_SRC"

# 5. Install the launchd plist (substitute real paths)
mkdir -p "$HOME/Library/LaunchAgents"
sed \
    -e "s|SCRIPT_PATH|$SCRIPT_SRC|g" \
    -e "s|LOGS_DIR|$LOGS_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DEST"
echo ""
echo "✅ Sync service installed"

# 6. Unload any existing job (ignore errors if not loaded)
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# 7. Load the launchd job
launchctl load "$PLIST_DEST"
echo "✅ Sync service started"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  🎉  Your documents will now sync automatically!            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  DROP files into:  ~/Documents/OpenClaw/                    ║"
echo "║  Supported types:  .docx  .xlsx  (up to 50 MB)             ║"
echo "║  Sync time:        within 30 seconds                        ║"
echo "║                                                              ║"
echo "║  Then open Slack and DM @OpenClaw:                          ║"
echo "║    \"edit my report.docx\"                                    ║"
echo "║    \"summarize budget.xlsx\"                                  ║"
echo "║                                                              ║"
echo "║  View sync logs:   ~/Library/Logs/openclaw-watcher.log      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
