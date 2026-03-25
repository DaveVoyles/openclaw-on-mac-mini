#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# OpenClaw Backup & Restore Script
# ────────────────────────────────────────────────────────────────
# Creates timestamped backups of all persistent data and config.
# Run daily via cron or manually before upgrades.
#
# Usage:
#   ./scripts/backup_restore.sh backup              # create a backup
#   ./scripts/backup_restore.sh restore <archive>   # restore from archive
#   ./scripts/backup_restore.sh list                 # list available backups
#
# What gets backed up:
#   - config/           (config.yaml, permissions, prompts, skills)
#   - data/tasks.json   (Mission Control kanban board)
#   - data/memory/      (QMD memory, spending, ontology, summaries)
#   - data/audit/       (audit JSONL logs)
#   - .env              (secrets — excluded from git, critical to preserve)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${OPENCLAW_BACKUP_DIR:-$PROJECT_ROOT/backups}"

# ── Helpers ───────────────────────────────────────────────────

_log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [backup] $*"; }

_require() {
  command -v "$1" >/dev/null 2>&1 || { echo "❌ Required command not found: $1"; exit 1; }
}

# ── Backup ────────────────────────────────────────────────────

do_backup() {
  _require tar
  _require gzip

  local timestamp
  timestamp="$(date '+%Y%m%d_%H%M%S')"
  local archive="${BACKUP_DIR}/openclaw_backup_${timestamp}.tar.gz"

  mkdir -p "$BACKUP_DIR"

  _log "Starting backup → $archive"

  # Collect paths relative to project root
  local paths=()
  cd "$PROJECT_ROOT"

  # Config (always present)
  [[ -d config ]] && paths+=(config)

  # Persistent data
  [[ -f data/tasks.json ]]  && paths+=(data/tasks.json)
  [[ -d data/memory ]]      && paths+=(data/memory)
  [[ -d data/audit ]]       && paths+=(data/audit)

  # Secrets
  [[ -f .env ]] && paths+=(.env)

  if [[ ${#paths[@]} -eq 0 ]]; then
    _log "⚠️  Nothing to back up — no data/config found."
    exit 1
  fi

  tar czf "$archive" "${paths[@]}"

  local size
  size="$(du -h "$archive" | cut -f1)"
  _log "✅ Backup complete: $archive ($size)"
  _log "   Contents: ${paths[*]}"

  # Prune backups older than 30 days
  local pruned
  pruned=$(find "$BACKUP_DIR" -name "openclaw_backup_*.tar.gz" -mtime +30 -delete -print | wc -l | tr -d ' ')
  if [[ "$pruned" -gt 0 ]]; then
    _log "🗑️  Pruned $pruned backup(s) older than 30 days."
  fi
}

# ── Restore ───────────────────────────────────────────────────

do_restore() {
  _require tar

  local archive="$1"
  if [[ ! -f "$archive" ]]; then
    # Try relative to BACKUP_DIR
    archive="${BACKUP_DIR}/$1"
  fi
  if [[ ! -f "$archive" ]]; then
    echo "❌ Archive not found: $1"
    echo "   Available backups:"
    do_list
    exit 1
  fi

  _log "⚠️  This will overwrite current config and data with the backup."
  _log "Archive: $archive"
  echo ""

  # Show contents first
  echo "Contents:"
  tar tzf "$archive" | head -30
  echo ""

  read -rp "Proceed with restore? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    _log "Restore cancelled."
    exit 0
  fi

  # Stop the bot if running
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^openclaw$"; then
    _log "Stopping openclaw container..."
    docker compose -f "$PROJECT_ROOT/docker-compose.yml" down
  fi

  # Create a pre-restore backup
  _log "Creating pre-restore safety backup..."
  BACKUP_DIR="$BACKUP_DIR" do_backup || true

  # Extract
  cd "$PROJECT_ROOT"
  tar xzf "$archive"
  _log "✅ Restore complete from $archive"
  _log "   Restart the bot with: docker compose up -d --build"
}

# ── List ──────────────────────────────────────────────────────

do_list() {
  mkdir -p "$BACKUP_DIR"
  local count
  count=$(find "$BACKUP_DIR" -name "openclaw_backup_*.tar.gz" 2>/dev/null | wc -l | tr -d ' ')

  if [[ "$count" -eq 0 ]]; then
    echo "No backups found in $BACKUP_DIR"
    return
  fi

  echo "Available backups ($count):"
  echo ""
  # shellcheck disable=SC2012
  ls -lh "$BACKUP_DIR"/openclaw_backup_*.tar.gz 2>/dev/null | \
    awk '{printf "  %-12s %s\n", $5, $NF}'
}

# ── Main ──────────────────────────────────────────────────────

case "${1:-}" in
  backup)
    do_backup
    ;;
  restore)
    if [[ -z "${2:-}" ]]; then
      echo "Usage: $0 restore <archive-file>"
      echo ""
      do_list
      exit 1
    fi
    do_restore "$2"
    ;;
  list)
    do_list
    ;;
  *)
    echo "OpenClaw Backup & Restore"
    echo ""
    echo "Usage:"
    echo "  $0 backup              Create a timestamped backup"
    echo "  $0 restore <archive>   Restore from a backup archive"
    echo "  $0 list                List available backups"
    echo ""
    echo "Backup directory: $BACKUP_DIR"
    echo "Override with: OPENCLAW_BACKUP_DIR=/path $0 backup"
    ;;
esac
