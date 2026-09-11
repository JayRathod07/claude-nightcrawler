#!/usr/bin/env bash
# ============================================================================
#  Claude Nightcrawler -- Post-Deployment Verification Script
#
#  Run this after setup.sh completes to verify the entire deployment is
#  working correctly end-to-end.
#
#  Usage:
#    chmod +x scripts/verify_deployment.sh
#    ./scripts/verify_deployment.sh
#
#  Exit code 0 = all checks passed
#  Exit code 1 = one or more checks failed
# ============================================================================

set -uo pipefail

# -- Colour output ------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass()  { echo -e "  ${GREEN}[PASS]${NC} $*"; ((PASS++));  }
fail()  { echo -e "  ${RED}[FAIL]${NC} $*"; ((FAIL++));  }
warn()  { echo -e "  ${YELLOW}[WARN]${NC} $*"; ((WARN++));  }
info()  { echo -e "  ${BLUE}[INFO]${NC} $*";               }
header(){ echo -e "\n${BLUE}=== $* ===${NC}";               }

INSTALL_DIR="/opt/claude-agent"

# -- Load .env if available ---------------------------------------------------
if [[ -f "$INSTALL_DIR/.env" ]]; then
    set -a; source "$INSTALL_DIR/.env"; set +a
elif [[ -f ".env" ]]; then
    set -a; source ".env"; set +a
fi
DOMAIN_NAME="${DOMAIN_NAME:-your-agent.duckdns.org}"

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  Claude Nightcrawler -- Deployment Verification             ${NC}"
echo -e "${BLUE}============================================================${NC}"

# ============================================================================
# CHECK 1: Directory Structure
# ============================================================================
header "Directory Structure"

for dir in "$INSTALL_DIR" "$INSTALL_DIR/src" "$INSTALL_DIR/logs" \
           "$INSTALL_DIR/results" "$INSTALL_DIR/browser_data" \
           "$INSTALL_DIR/config" "$INSTALL_DIR/scripts"; do
    if [[ -d "$dir" ]]; then
        pass "Directory exists: $dir"
    else
        fail "Directory missing: $dir"
    fi
done

# ============================================================================
# CHECK 2: Key Files
# ============================================================================
header "Key Files"

for f in "$INSTALL_DIR/.env" \
         "$INSTALL_DIR/requirements.txt" \
         "$INSTALL_DIR/src/database.py" \
         "$INSTALL_DIR/src/dashboard.py" \
         "$INSTALL_DIR/src/agent_worker.py" \
         "$INSTALL_DIR/src/claude_adapter.py" \
         "$INSTALL_DIR/scripts/setup.sh" \
         "$INSTALL_DIR/scripts/manual_login.py" \
         "$INSTALL_DIR/config/Caddyfile" \
         "$INSTALL_DIR/config/claude-agent.service" \
         "$INSTALL_DIR/config/claude-dashboard.service"; do
    if [[ -f "$f" ]]; then
        pass "File exists: $(basename $f)"
    else
        fail "File missing: $f"
    fi
done

# .env permissions
if [[ -f "$INSTALL_DIR/.env" ]]; then
    PERMS=$(stat -c "%a" "$INSTALL_DIR/.env")
    if [[ "$PERMS" == "600" ]]; then
        pass ".env permissions: 600 (secure)"
    else
        warn ".env permissions: $PERMS (should be 600 -- run: chmod 600 $INSTALL_DIR/.env)"
    fi
fi

# ============================================================================
# CHECK 3: Python Virtual Environment
# ============================================================================
header "Python Environment"

VENV_PY="$INSTALL_DIR/venv/bin/python"
if [[ -f "$VENV_PY" ]]; then
    PY_VER=$("$VENV_PY" --version 2>&1)
    pass "Virtual environment: $PY_VER"
else
    fail "Python virtual environment not found at $INSTALL_DIR/venv"
fi

# Check key packages
if [[ -f "$VENV_PY" ]]; then
    for pkg in fastapi playwright uvicorn bcrypt python-dotenv; do
        if "$VENV_PY" -c "import ${pkg//-/_}" 2>/dev/null; then
            pass "Package installed: $pkg"
        else
            fail "Package missing: $pkg -- run: $INSTALL_DIR/venv/bin/pip install $pkg"
        fi
    done
fi

# Check Playwright Chromium
if [[ -f "$VENV_PY" ]]; then
    if "$VENV_PY" -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); b.close(); p.stop()" 2>/dev/null; then
        pass "Playwright Chromium: can launch"
    else
        warn "Playwright Chromium: launch test failed (may need: $VENV_PY -m playwright install chromium)"
    fi
fi

# ============================================================================
# CHECK 4: systemd Services
# ============================================================================
header "systemd Services"

for svc in claude-agent claude-dashboard caddy; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}.service"; then
        STATUS=$(systemctl is-active "$svc" 2>/dev/null)
        ENABLED=$(systemctl is-enabled "$svc" 2>/dev/null)
        if [[ "$STATUS" == "active" ]]; then
            pass "$svc: running (enabled: $ENABLED)"
        else
            fail "$svc: not running (status: $STATUS) -- run: sudo systemctl start $svc"
        fi
    else
        fail "$svc: service unit not installed -- re-run setup.sh"
    fi
done

# ============================================================================
# CHECK 5: Network / Ports
# ============================================================================
header "Network & Ports"

# Check FastAPI is listening on 8000 (localhost only)
if ss -tlnp 2>/dev/null | grep -q "127.0.0.1:8000\|0.0.0.0:8000"; then
    LOCAL_BINDING=$(ss -tlnp 2>/dev/null | grep ":8000" | awk '{print $4}')
    if echo "$LOCAL_BINDING" | grep -q "127.0.0.1"; then
        pass "FastAPI: listening on 127.0.0.1:8000 (localhost only, correct)"
    else
        warn "FastAPI: listening on $LOCAL_BINDING (should be 127.0.0.1 only)"
    fi
else
    fail "FastAPI: not listening on port 8000 -- check claude-dashboard service"
fi

# Check Caddy / HTTPS
if ss -tlnp 2>/dev/null | grep -q ":443"; then
    pass "Caddy: listening on port 443"
elif ss -tlnp 2>/dev/null | grep -q ":80"; then
    pass "Caddy: listening on port 80 (443 may be starting)"
else
    fail "Caddy: not listening on ports 80 or 443"
fi

# ============================================================================
# CHECK 6: API Endpoints
# ============================================================================
header "API Endpoints"

# Health check (no auth)
if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    HEALTH_RESP=$(curl -sf http://127.0.0.1:8000/health)
    pass "Health endpoint: $HEALTH_RESP"
else
    fail "Health endpoint: http://127.0.0.1:8000/health not responding"
fi

# Test with auth if credentials are available
if [[ -n "${ADMIN_USERNAME:-}" && -n "${ADMIN_PASSWORD:-}" ]]; then
    if curl -sf -u "${ADMIN_USERNAME}:${ADMIN_PASSWORD}" \
            http://127.0.0.1:8000/api/stats >/dev/null 2>&1; then
        pass "API /api/stats: accessible with credentials"
    else
        warn "API /api/stats: check credentials in .env"
    fi
fi

# ============================================================================
# CHECK 7: Database
# ============================================================================
header "Database"

DB_FILE="${DB_PATH:-$INSTALL_DIR/data/nightcrawler.db}"
if [[ -f "$DB_FILE" ]]; then
    INTEGRITY=$(sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null)
    if [[ "$INTEGRITY" == "ok" ]]; then
        pass "Database: exists and integrity OK"
        TASK_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM tasks;" 2>/dev/null || echo "?")
        info "  Tasks in database: $TASK_COUNT"
    else
        fail "Database: integrity check failed -- $INTEGRITY"
    fi
else
    fail "Database file not found: $DB_FILE"
fi

# ============================================================================
# CHECK 8: DuckDNS
# ============================================================================
header "DuckDNS & DNS"

if [[ -f "$INSTALL_DIR/config/duckdns-update.sh" ]]; then
    pass "DuckDNS update script: present"
else
    fail "DuckDNS update script: missing"
fi

# Check cron
if crontab -l 2>/dev/null | grep -q "duckdns"; then
    pass "DuckDNS cron: installed"
else
    warn "DuckDNS cron: not found -- add manually or re-run setup.sh"
fi

# DNS resolution
if command -v nslookup >/dev/null 2>&1; then
    DNS_RESULT=$(nslookup "$DOMAIN_NAME" 2>/dev/null | grep -A1 "Name:" | grep "Address" | awk '{print $2}')
    if [[ -n "$DNS_RESULT" ]]; then
        pass "DNS resolution: $DOMAIN_NAME -> $DNS_RESULT"
    else
        warn "DNS resolution: could not resolve $DOMAIN_NAME (check DuckDNS IP)"
    fi
fi

# ============================================================================
# CHECK 9: Firewall
# ============================================================================
header "Firewall (UFW)"

if command -v ufw >/dev/null 2>&1; then
    UFW_STATUS=$(ufw status 2>/dev/null | head -1)
    info "UFW status: $UFW_STATUS"
    if ufw status 2>/dev/null | grep -q "22/tcp.*ALLOW"; then
        pass "UFW: port 22 (SSH) allowed"
    else
        warn "UFW: port 22 not explicitly allowed"
    fi
    if ufw status 2>/dev/null | grep -q "80/tcp.*ALLOW"; then
        pass "UFW: port 80 (HTTP) allowed"
    else
        warn "UFW: port 80 not allowed"
    fi
    if ufw status 2>/dev/null | grep -q "443/tcp.*ALLOW"; then
        pass "UFW: port 443 (HTTPS) allowed"
    else
        warn "UFW: port 443 not allowed"
    fi
fi

# ============================================================================
# CHECK 10: Disk Space
# ============================================================================
header "Disk Space"

DISK_USAGE=$(df "$INSTALL_DIR" 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%')
if [[ -n "$DISK_USAGE" ]]; then
    if [[ "$DISK_USAGE" -lt 80 ]]; then
        pass "Disk usage: ${DISK_USAGE}% (healthy)"
    elif [[ "$DISK_USAGE" -lt 90 ]]; then
        warn "Disk usage: ${DISK_USAGE}% (getting full -- clean old logs/results)"
    else
        fail "Disk usage: ${DISK_USAGE}% (critical -- free space immediately)"
    fi
fi

# ============================================================================
# CHECK 11: Memory
# ============================================================================
header "Memory"

MEM_FREE=$(free -m 2>/dev/null | awk 'NR==2{print $7}')
MEM_TOTAL=$(free -m 2>/dev/null | awk 'NR==2{print $2}')
if [[ -n "$MEM_FREE" ]]; then
    if [[ "$MEM_FREE" -gt 500 ]]; then
        pass "Available memory: ${MEM_FREE}MB / ${MEM_TOTAL}MB"
    elif [[ "$MEM_FREE" -gt 200 ]]; then
        warn "Available memory: ${MEM_FREE}MB / ${MEM_TOTAL}MB (low -- consider adding swap)"
    else
        fail "Available memory: ${MEM_FREE}MB / ${MEM_TOTAL}MB (very low -- add swap!)"
    fi
fi

# ============================================================================
# CHECK 12: Browser Session
# ============================================================================
header "Claude Browser Session"

BROWSER_DATA="${BROWSER_DATA_DIR:-$INSTALL_DIR/browser_data}"
if [[ -d "$BROWSER_DATA" ]] && [[ "$(ls -A "$BROWSER_DATA" 2>/dev/null)" ]]; then
    pass "Browser data directory: populated (session may be saved)"
    COOKIE_FILES=$(find "$BROWSER_DATA" -name "Cookies" 2>/dev/null | wc -l)
    if [[ "$COOKIE_FILES" -gt 0 ]]; then
        pass "Cookie files found: $COOKIE_FILES (session exists)"
    else
        warn "No cookie files found -- run: python $INSTALL_DIR/scripts/manual_login.py"
    fi
else
    warn "Browser data directory is empty -- you need to run manual login:"
    warn "  source $INSTALL_DIR/venv/bin/activate"
    warn "  python $INSTALL_DIR/scripts/manual_login.py"
fi

# ============================================================================
# SUMMARY
# ============================================================================
echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "  Results: ${GREEN}${PASS} passed${NC}  ${YELLOW}${WARN} warnings${NC}  ${RED}${FAIL} failed${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}Deployment has failures that need to be fixed.${NC}"
    echo "See the FAIL messages above for details."
    echo ""
    exit 1
elif [[ $WARN -gt 0 ]]; then
    echo -e "${YELLOW}Deployment is mostly OK but has warnings.${NC}"
    echo "Review WARN messages above — some may need attention."
    echo ""
    if [[ $WARN -gt 3 ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}All checks passed! Claude Nightcrawler is fully deployed.${NC}"
    echo ""
    echo "  Access your dashboard: https://$DOMAIN_NAME"
    echo ""
fi

exit 0
