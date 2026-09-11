#!/usr/bin/env bash
# ============================================================================
#  Claude Nightcrawler -- Oracle Cloud Quick Bootstrap
#
#  Run this on a FRESH Oracle Cloud Ubuntu 22.04 instance to go from
#  zero to running in one command:
#
#    curl -fsSL https://raw.githubusercontent.com/JayRathod07/claude-nightcrawler/main/scripts/oracle_bootstrap.sh | sudo bash
#
#  Or if you have cloned the repo already:
#    sudo bash scripts/oracle_bootstrap.sh
#
#  What this script does BEFORE running setup.sh:
#    1. Verifies this is Ubuntu 22.04
#    2. Clears Oracle Cloud iptables restrictions
#    3. Creates and configures .env from prompts
#    4. Calls setup.sh
#    5. Runs verify_deployment.sh
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO ]${NC} $*"; }
success() { echo -e "${GREEN}[  OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[ WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -eq 0 ]] || error "Run as root: sudo bash scripts/oracle_bootstrap.sh"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     Claude Nightcrawler - Oracle Cloud Bootstrap             ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ============================================================================
# STEP 1: OS Check
# ============================================================================
info "Checking operating system..."
if ! lsb_release -a 2>/dev/null | grep -q "22.04"; then
    warn "Not Ubuntu 22.04 -- this script is tested on Ubuntu 22.04 LTS."
    warn "Other Ubuntu versions may work but are not guaranteed."
    read -p "Continue anyway? [y/N] " -r
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
else
    success "Ubuntu 22.04 LTS detected"
fi

# ============================================================================
# STEP 2: Clear Oracle Cloud iptables Restrictions
# ============================================================================
info "Clearing Oracle Cloud iptables INPUT restrictions..."
# Oracle Cloud VMs have default iptables rules that block ports 80 and 443
# even after you open them in the OCI Security List.
iptables -P INPUT ACCEPT
iptables -F INPUT
apt-get install -y -q iptables-persistent 2>/dev/null || true
netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
success "Oracle Cloud iptables cleared"

# ============================================================================
# STEP 3: Locate project directory
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
info "Project directory: $PROJECT_DIR"

# ============================================================================
# STEP 4: Create .env if it does not exist
# ============================================================================
if [[ -f ".env" ]]; then
    info ".env already exists -- skipping interactive configuration"
else
    info "Creating .env from .env.example..."
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
    else
        error ".env.example not found. Clone the repository first."
    fi

    echo ""
    echo -e "${YELLOW}──────────────────────────────────────────────────────────────${NC}"
    echo -e "${YELLOW}  Configuration (press Enter to keep default values)           ${NC}"
    echo -e "${YELLOW}──────────────────────────────────────────────────────────────${NC}"
    echo ""

    # Admin password
    read -p "  Dashboard password (min 16 chars): " ADMIN_PASS
    if [[ -n "$ADMIN_PASS" ]]; then
        sed -i "s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=\"$ADMIN_PASS\"|" .env
    fi

    # Domain
    read -p "  Your DuckDNS domain (e.g. my-agent.duckdns.org): " DOMAIN
    if [[ -n "$DOMAIN" ]]; then
        SUBDOMAIN="${DOMAIN%.duckdns.org}"
        sed -i "s|DOMAIN_NAME=.*|DOMAIN_NAME=\"$DOMAIN\"|" .env
        sed -i "s|DUCKDNS_DOMAIN=.*|DUCKDNS_DOMAIN=\"$SUBDOMAIN\"|" .env
    fi

    # DuckDNS token
    read -p "  DuckDNS token: " DUCKDNS_TOK
    if [[ -n "$DUCKDNS_TOK" ]]; then
        sed -i "s|DUCKDNS_TOKEN=.*|DUCKDNS_TOKEN=\"$DUCKDNS_TOK\"|" .env
    fi

    # Telegram (optional)
    read -p "  Telegram bot token (leave blank to skip): " TG_TOKEN
    if [[ -n "$TG_TOKEN" ]]; then
        read -p "  Telegram chat ID: " TG_CHAT
        sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=\"$TG_TOKEN\"|" .env
        sed -i "s|TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=\"$TG_CHAT\"|" .env
    fi

    chmod 600 .env
    success ".env created and secured"
fi

# ============================================================================
# STEP 5: Run main setup script
# ============================================================================
echo ""
info "Running main setup script..."
chmod +x scripts/setup.sh
bash scripts/setup.sh

# ============================================================================
# STEP 6: Run verification
# ============================================================================
echo ""
info "Running deployment verification..."
chmod +x scripts/verify_deployment.sh
bash scripts/verify_deployment.sh || true

# ============================================================================
# DONE
# ============================================================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       Bootstrap Complete!                                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Next step — complete the one-time Claude login:"
echo ""
echo "    ssh -X ubuntu@YOUR_PUBLIC_IP    # reconnect with X11 forwarding"
echo "    source /opt/claude-agent/venv/bin/activate"
echo "    python /opt/claude-agent/scripts/manual_login.py"
echo ""
echo "  Then open: https://$(grep DOMAIN_NAME .env | cut -d'"' -f2)"
echo ""
