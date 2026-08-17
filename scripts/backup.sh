#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Claude Nightcrawler — Database Backup Script
#  Creates timestamped SQLite backups with automatic rotation (keep 30 days).
#
#  Usage:
#    ./scripts/backup.sh                  # regular backup
#    ./scripts/backup.sh --tag pre-deploy # tagged backup
#
#  Cron (daily at 03:00):
#    0 3 * * * /opt/claude-agent/scripts/backup.sh >> /opt/claude-agent/logs/backup.log 2>&1
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/claude-agent"
DB_FILE="$INSTALL_DIR/data/nightcrawler.db"
BACKUP_DIR="$INSTALL_DIR/backups"
KEEP_DAYS=30
TAG="${2:-}"    # optional label e.g. "pre-deploy"

# ── Timestamp ────────────────────────────────────────────────────────────────
TS=$(date '+%Y%m%d_%H%M%S')
[[ -n "$TAG" ]] && LABEL="${TS}_${TAG}" || LABEL="$TS"
BACKUP_FILE="$BACKUP_DIR/nightcrawler_${LABEL}.db"

# ── Ensure backup dir exists ─────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ── Check DB exists ───────────────────────────────────────────────────────────
if [[ ! -f "$DB_FILE" ]]; then
    # Try alternate location (some installs put it in project root)
    DB_FILE="$INSTALL_DIR/nightcrawler.db"
    if [[ ! -f "$DB_FILE" ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: Database not found at expected path — skipping backup"
        exit 0
    fi
fi

# ── Backup using SQLite online backup (safe with live DB) ─────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting backup: $DB_FILE → $BACKUP_FILE"
sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"
SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup complete: $BACKUP_FILE ($SIZE)"

# ── Verify backup integrity ───────────────────────────────────────────────────
INTEGRITY=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" 2>&1)
if [[ "$INTEGRITY" == "ok" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Integrity check: OK"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Integrity check failed: $INTEGRITY"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# ── Rotate old backups (keep last KEEP_DAYS days) ─────────────────────────────
DELETED=$(find "$BACKUP_DIR" -name "nightcrawler_*.db" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
[[ "$DELETED" -gt 0 ]] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] Rotated $DELETED old backup(s)"

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL=$(find "$BACKUP_DIR" -name "nightcrawler_*.db" | wc -l)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Total backups retained: $TOTAL"
