#!/usr/bin/env bash
# watch_folder.sh — OpenClaw Mac Folder Watcher
#
# Watches ~/Documents/OpenClaw/ and rsyncs new .docx and .xlsx files
# (≤ 50 MB) to macmini:/ai-files/ via SSH.
#
# Normally run by launchd (see com.openclaw.watcher.plist).
# Can also be run manually for testing.
#
# Requirements: rsync (ships with macOS), SSH key configured for macmini.
# Optional:     fswatch (brew install fswatch) for event-driven mode.

set -euo pipefail

WATCH_DIR="${HOME}/Documents/OpenClaw"
REMOTE="macmini:/ai-files/"
LOG_FILE="${HOME}/Library/Logs/openclaw-watcher.log"
POLL_INTERVAL=30
LAST_SYNC_JSON="${HOME}/openclaw/data/last_sync.json"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Create watched directory if it doesn't exist
mkdir -p "$WATCH_DIR"
mkdir -p "$(dirname "$LOG_FILE")"

ssh_reachable() {
    ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
        macmini "exit 0" 2>/dev/null
}

sync_files() {
    if ! ssh_reachable; then
        log "⚠️  macmini unreachable — skipping sync, will retry next cycle"
        return 0
    fi

    local output
    output=$(rsync -az \
        --ignore-existing \
        --max-size=50m \
        --include="*.docx" \
        --include="*.xlsx" \
        --exclude="*" \
        --out-format="%f" \
        "${WATCH_DIR}/" \
        "$REMOTE" 2>&1) || {
        log "❌ rsync error: $output"
        return 0
    }

    if [[ -n "$output" ]]; then
        local last_file=""
        while IFS= read -r f; do
            if [[ -n "$f" ]]; then
                log "✅ Synced: $f"
                last_file="$f"
            fi
        done <<< "$output"
        # Write last_sync.json so /status can report when sync last ran
        if [[ -n "$last_file" ]]; then
            mkdir -p "$(dirname "$LAST_SYNC_JSON")"
            printf '{"timestamp":"%s","last_file":"%s"}\n' \
                "$(date '+%Y-%m-%d %H:%M:%S')" "$last_file" \
                > "$LAST_SYNC_JSON" 2>/dev/null || true
        fi
    fi
}

log "🚀 OpenClaw folder watcher started (watching: $WATCH_DIR)"

# Use fswatch for event-driven watching if available; otherwise poll
if command -v fswatch &>/dev/null; then
    log "📡 Using fswatch (event-driven mode)"
    # Run one initial sync, then watch for changes
    sync_files
    fswatch -0 --event Created --event Updated --event Renamed \
        --include='.*\.(docx|xlsx)$' --extended \
        "$WATCH_DIR" | while IFS= read -r -d '' _event; do
        sync_files
    done
else
    log "🔄 fswatch not found — using polling mode (every ${POLL_INTERVAL}s)"
    log "   Tip: install fswatch for instant sync: brew install fswatch"
    while true; do
        sync_files
        sleep "$POLL_INTERVAL"
    done
fi
