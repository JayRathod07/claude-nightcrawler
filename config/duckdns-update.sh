#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  Claude Nightcrawler — DuckDNS Dynamic IP Update Script
#  Runs every 5 minutes via cron to keep your domain pointing to the server.
#
#  Setup:
#    1. Replace DUCKDNS_DOMAIN and DUCKDNS_TOKEN below (or export them as env
#       vars before running — the script checks both).
#    2. chmod +x config/duckdns-update.sh
#    3. Add to crontab:
#         */5 * * * * /opt/claude-agent/config/duckdns-update.sh >> /opt/claude-agent/logs/duckdns.log 2>&1
#
#  Or, source from .env automatically:
#    */5 * * * * bash -c 'source /opt/claude-agent/.env && /opt/claude-agent/config/duckdns-update.sh' >> /opt/claude-agent/logs/duckdns.log 2>&1
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
# Prefer environment variables (from .env sourced by cron), fall back to
# placeholder values. Replace these if you call this script standalone.
DOMAIN="${DUCKDNS_DOMAIN:-YOUR_SUBDOMAIN}"      # just the subdomain, not .duckdns.org
TOKEN="${DUCKDNS_TOKEN:-YOUR_DUCKDNS_TOKEN}"
LOG_FILE="${LOG_DIR:-/opt/claude-agent/logs}/duckdns.log"

# ── Timestamp ───────────────────────────────────────────────────────────────
TS=$(date '+%Y-%m-%d %H:%M:%S')

# ── Validation ──────────────────────────────────────────────────────────────
if [[ "$DOMAIN" == "YOUR_SUBDOMAIN" || "$TOKEN" == "YOUR_DUCKDNS_TOKEN" ]]; then
  echo "[$TS] ERROR: DUCKDNS_DOMAIN and DUCKDNS_TOKEN must be set in .env or this script."
  exit 1
fi

# ── Update DNS ──────────────────────────────────────────────────────────────
RESPONSE=$(curl -s --max-time 10 \
  "https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=")

# ── Check result ─────────────────────────────────────────────────────────────
if [[ "$RESPONSE" == "OK" ]]; then
  echo "[$TS] DuckDNS update OK — domain=${DOMAIN}.duckdns.org"
else
  echo "[$TS] DuckDNS update FAILED — response: ${RESPONSE}"
  exit 1
fi
