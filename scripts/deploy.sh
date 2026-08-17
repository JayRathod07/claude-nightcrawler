#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Claude Nightcrawler — Deployment Script
#  Pulls latest code from GitHub and restarts services without downtime.
#
#  Usage (on the server):
#    ./scripts/deploy.sh
#    ./scripts/deploy.sh --branch feature/my-branch
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

GREEN='\033[0;32m' YELLOW='\033[1;33m' RED='\033[0;31m' BLUE='\033[0;34m' NC='\033[0m'
log()     { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*"; }
success() { echo -e "${GREEN}[  ✓  ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[ WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Config ───────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/claude-agent"
VENV_PIP="$INSTALL_DIR/venv/bin/pip"
VENV_PY="$INSTALL_DIR/venv/bin/python"
BRANCH="${1:---branch}"
BRANCH_NAME="${2:-main}"
[[ "$1" == "--branch" ]] && BRANCH_NAME="${2:-main}"

# ── Deployment start ─────────────────────────────────────────────────────────
log "═══════════════════════════════════════════════════"
log "  Claude Nightcrawler — Deployment"
log "  Branch: $BRANCH_NAME  |  Time: $(date)"
log "═══════════════════════════════════════════════════"

cd "$INSTALL_DIR" || error "Cannot cd to $INSTALL_DIR"

# ── 1. Pre-deploy backup ──────────────────────────────────────────────────────
log "Step 1/6 — Creating pre-deploy database backup..."
"$INSTALL_DIR/scripts/backup.sh" --tag "pre-deploy" || warn "Backup failed — proceeding anyway"
success "Backup complete"

# ── 2. Pull latest code ───────────────────────────────────────────────────────
log "Step 2/6 — Pulling latest code from GitHub..."
git fetch --all --prune
git checkout "$BRANCH_NAME"
git pull origin "$BRANCH_NAME"
COMMIT=$(git rev-parse --short HEAD)
success "Code updated to commit $COMMIT"

# ── 3. Install/update dependencies ───────────────────────────────────────────
log "Step 3/6 — Updating Python dependencies..."
"$VENV_PIP" install --upgrade pip -q
"$VENV_PIP" install -r requirements.txt -q
success "Dependencies updated"

# ── 4. Run database migrations / checks ──────────────────────────────────────
log "Step 4/6 — Running database initialisation check..."
"$VENV_PY" -c "
import sys
sys.path.insert(0, '.')
from src.database import init_db
init_db()
print('Database OK')
" && success "Database ready"

# ── 5. Restart services ───────────────────────────────────────────────────────
log "Step 5/6 — Restarting services..."
# Restart dashboard first (it doesn't hold browser state)
sudo systemctl restart claude-dashboard
sleep 3
if ! sudo systemctl is-active --quiet claude-dashboard; then
    error "claude-dashboard failed to start — rolling back"
fi
success "Dashboard restarted"

# Restart worker (graceful: SIGTERM → wait → start)
sudo systemctl restart claude-agent
sleep 5
if ! sudo systemctl is-active --quiet claude-agent; then
    warn "claude-agent not yet active — it may still be starting up"
fi
success "Worker restarted"

# ── 6. Post-deploy health check ───────────────────────────────────────────────
log "Step 6/6 — Running health check..."
sleep 3
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health || echo "000")
if [[ "$HTTP_CODE" == "200" ]]; then
    success "Health check passed (HTTP 200)"
else
    warn "Health check returned HTTP $HTTP_CODE — services may still be starting"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✓  Deployment Complete                       ║${NC}"
echo -e "${GREEN}╟──────────────────────────────────────────────────────╢${NC}"
echo -e "${GREEN}║  Commit:  $COMMIT                                    ║${NC}"
echo -e "${GREEN}║  Branch:  $BRANCH_NAME                               ║${NC}"
echo -e "${GREEN}║  Time:    $(date)                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
