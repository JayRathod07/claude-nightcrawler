#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Claude Nightcrawler — Automated Server Setup Script
#  Target: Ubuntu 22.04 LTS (Oracle Cloud Ampere A1 / E2 Micro)
#
#  Usage:
#    chmod +x scripts/setup.sh
#    sudo ./scripts/setup.sh
#
#  What this script does:
#    1.  Updates system packages
#    2.  Installs Python 3.11, pip, venv
#    3.  Creates /opt/claude-agent installation directory
#    4.  Sets up Python virtual environment + installs requirements
#    5.  Installs Playwright and Chromium browser
#    6.  Installs Caddy web server
#    7.  Configures Caddy (copies Caddyfile, sets domain)
#    8.  Creates systemd service units
#    9.  Configures UFW firewall (22/80/443)
#    10. Sets correct file permissions
#    11. Enables and starts all services
#    12. Sets up DuckDNS cron job
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colour output ────────────────────────────────────────────────────────────
RED='\033[0;31m'  GREEN='\033[0;32m'  YELLOW='\033[1;33m'
BLUE='\033[0;34m' NC='\033[0m'

log()     { echo -e "${BLUE}[INFO ]${NC} $*"; }
success() { echo -e "${GREEN}[  ✓  ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[ WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Require root ─────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "This script must be run as root (sudo ./scripts/setup.sh)"

# ── Detect current directory ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/claude-agent"
SERVICE_USER="${SUDO_USER:-ubuntu}"

# ── Load .env if available ───────────────────────────────────────────────────
ENV_FILE="$PROJECT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    log "Loading environment from $ENV_FILE"
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
else
    warn ".env not found at $ENV_FILE — you will need to configure manually."
fi

DOMAIN_NAME="${DOMAIN_NAME:-your-agent.duckdns.org}"

# ════════════════════════════════════════════════════════════════════════════
# STEP 1: System packages
# ════════════════════════════════════════════════════════════════════════════
log "Step 1/12 — Updating system packages..."
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q \
    curl wget git gnupg2 lsb-release \
    software-properties-common ca-certificates \
    python3.11 python3.11-venv python3.11-dev python3-pip \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    ufw logrotate sqlite3
success "System packages installed"

# ════════════════════════════════════════════════════════════════════════════
# STEP 2: Create installation directory
# ════════════════════════════════════════════════════════════════════════════
log "Step 2/12 — Setting up installation directory at $INSTALL_DIR..."
if [[ "$INSTALL_DIR" != "$PROJECT_DIR" ]]; then
    rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
          --exclude='*.pyc' --exclude='.env' \
          "$PROJECT_DIR/" "$INSTALL_DIR/"
    log "Project files copied to $INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"/{logs,results,browser_data,logs/screenshots}
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/browser_data"   # browser profile is sensitive
success "Installation directory ready"

# ════════════════════════════════════════════════════════════════════════════
# STEP 3: Python virtual environment
# ════════════════════════════════════════════════════════════════════════════
log "Step 3/12 — Creating Python virtual environment..."
python3.11 -m venv "$INSTALL_DIR/venv"
VENV_PY="$INSTALL_DIR/venv/bin/python"
VENV_PIP="$INSTALL_DIR/venv/bin/pip"
"$VENV_PIP" install --upgrade pip setuptools wheel -q
success "Virtual environment created"

# ════════════════════════════════════════════════════════════════════════════
# STEP 4: Python dependencies
# ════════════════════════════════════════════════════════════════════════════
log "Step 4/12 — Installing Python dependencies..."
if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    "$VENV_PIP" install -r "$INSTALL_DIR/requirements.txt" -q
    success "Python dependencies installed"
else
    warn "requirements.txt not found — skipping dependency install"
fi

# ════════════════════════════════════════════════════════════════════════════
# STEP 5: Playwright + Chromium
# ════════════════════════════════════════════════════════════════════════════
log "Step 5/12 — Installing Playwright and Chromium browser..."
"$VENV_PY" -m playwright install chromium
"$VENV_PY" -m playwright install-deps chromium
success "Playwright and Chromium installed"

# ════════════════════════════════════════════════════════════════════════════
# STEP 6: Caddy web server
# ════════════════════════════════════════════════════════════════════════════
log "Step 6/12 — Installing Caddy..."
if ! command -v caddy &>/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q
    apt-get install -y -q caddy
else
    log "Caddy already installed ($(caddy version))"
fi
success "Caddy installed"

# ════════════════════════════════════════════════════════════════════════════
# STEP 7: Caddy configuration
# ════════════════════════════════════════════════════════════════════════════
log "Step 7/12 — Configuring Caddy..."
mkdir -p /etc/caddy /var/log/caddy
if [[ -f "$INSTALL_DIR/config/Caddyfile" ]]; then
    # Substitute domain placeholder
    sed "s/YOUR_DOMAIN/$DOMAIN_NAME/g" \
        "$INSTALL_DIR/config/Caddyfile" > /etc/caddy/Caddyfile
    log "Caddyfile deployed with domain: $DOMAIN_NAME"
else
    warn "config/Caddyfile not found — creating minimal config"
    cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN_NAME {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
fi
chown -R caddy:caddy /var/log/caddy
caddy validate --config /etc/caddy/Caddyfile || warn "Caddyfile validation warning — check config"
success "Caddy configured"

# ════════════════════════════════════════════════════════════════════════════
# STEP 8: systemd service units
# ════════════════════════════════════════════════════════════════════════════
log "Step 8/12 — Installing systemd service units..."
for SVC in claude-agent claude-dashboard; do
    SRC="$INSTALL_DIR/config/${SVC}.service"
    DST="/etc/systemd/system/${SVC}.service"
    if [[ -f "$SRC" ]]; then
        # Update WorkingDirectory and User in the service file
        sed -e "s|/opt/claude-agent|$INSTALL_DIR|g" \
            -e "s|User=ubuntu|User=$SERVICE_USER|g" \
            -e "s|Group=ubuntu|Group=$SERVICE_USER|g" \
            "$SRC" > "$DST"
        log "  Installed $DST"
    else
        warn "  $SRC not found — skipping"
    fi
done
systemctl daemon-reload
success "systemd units installed"

# ════════════════════════════════════════════════════════════════════════════
# STEP 9: Firewall configuration
# ════════════════════════════════════════════════════════════════════════════
log "Step 9/12 — Configuring UFW firewall..."
ufw allow 22/tcp   comment 'SSH'
ufw allow 80/tcp   comment 'HTTP (Caddy redirect)'
ufw allow 443/tcp  comment 'HTTPS (Caddy + Let'\''s Encrypt)'
# Block direct access to the FastAPI port — only Caddy should reach it
ufw deny  8000/tcp comment 'FastAPI (internal only)'
ufw --force enable
success "Firewall configured (22/80/443 open, 8000 blocked externally)"

# ════════════════════════════════════════════════════════════════════════════
# STEP 10: File permissions
# ════════════════════════════════════════════════════════════════════════════
log "Step 10/12 — Setting file permissions..."
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
# .env must be readable only by owner (contains secrets)
[[ -f "$INSTALL_DIR/.env" ]] && chmod 600 "$INSTALL_DIR/.env"
# Scripts should be executable
chmod +x "$INSTALL_DIR"/scripts/*.sh 2>/dev/null || true
chmod +x "$INSTALL_DIR"/config/duckdns-update.sh 2>/dev/null || true
# Logs writable by service user
chmod 755 "$INSTALL_DIR/logs"
chmod 750 "$INSTALL_DIR/results"
success "File permissions set"

# ════════════════════════════════════════════════════════════════════════════
# STEP 11: Enable and start services
# ════════════════════════════════════════════════════════════════════════════
log "Step 11/12 — Enabling and starting services..."
for SVC in claude-dashboard claude-agent caddy; do
    if systemctl list-unit-files | grep -q "^${SVC}.service"; then
        systemctl enable  "$SVC"
        systemctl restart "$SVC"
        sleep 2
        if systemctl is-active --quiet "$SVC"; then
            success "  $SVC is running"
        else
            warn "  $SVC failed to start — check: journalctl -u $SVC -n 50"
        fi
    else
        warn "  $SVC not found — skipping"
    fi
done

# ════════════════════════════════════════════════════════════════════════════
# STEP 12: DuckDNS cron job
# ════════════════════════════════════════════════════════════════════════════
log "Step 12/12 — Installing DuckDNS cron job..."
CRON_CMD="*/5 * * * * bash -c 'source $INSTALL_DIR/.env 2>/dev/null && $INSTALL_DIR/config/duckdns-update.sh' >> $INSTALL_DIR/logs/duckdns.log 2>&1"
# Install cron for the service user
(crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -v 'duckdns'; echo "$CRON_CMD") \
    | crontab -u "$SERVICE_USER" -
success "DuckDNS cron job installed (runs every 5 minutes)"

# ════════════════════════════════════════════════════════════════════════════
# DONE
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            ✓  Setup Complete — Claude Nightcrawler           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo "  1. Complete manual Claude login:"
echo "       source $INSTALL_DIR/venv/bin/activate"
echo "       python $INSTALL_DIR/scripts/manual_login.py"
echo ""
echo "  2. Verify services:"
echo "       sudo systemctl status claude-agent claude-dashboard caddy"
echo ""
echo "  3. Access dashboard:"
echo "       https://$DOMAIN_NAME"
echo ""
echo "  4. View logs:"
echo "       tail -f $INSTALL_DIR/logs/worker.log"
echo "       tail -f $INSTALL_DIR/logs/dashboard.log"
echo ""
