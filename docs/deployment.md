# Deployment Guide — Claude Nightcrawler

Production deployment architecture, service management, updates, rollbacks, backup/restore, and scaling.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Service Management](#service-management)
3. [Updating the Application](#updating-the-application)
4. [Rollback Procedure](#rollback-procedure)
5. [Database Backup and Restore](#database-backup-and-restore)
6. [Caddy and TLS](#caddy-and-tls)
7. [DuckDNS IP Updates](#duckdns-ip-updates)
8. [Environment Variables Reference](#environment-variables-reference)
9. [Resource Usage](#resource-usage)
10. [Security Checklist](#security-checklist)
11. [Monitoring in Production](#monitoring-in-production)

---

## Architecture Overview

```
Internet --HTTPS--> DuckDNS --> Oracle Cloud Public IP
                                       |
                              Caddy (port 443)
                              * Auto TLS (Let's Encrypt)
                              * Security headers (HSTS, CSP)
                              * Reverse proxy to localhost:8000
                                       |
                              FastAPI Dashboard (port 8000, localhost only)
                              * HTTP Basic Auth
                              * REST API
                              * Jinja2 templates (Liquid Glass UI)
                                       |
                              SQLite Database (WAL mode)
                                    ^  |
                                    |  v
                              Agent Worker Process
                              * Playwright / Chromium
                              * Task queue loop
                              * Claude.ai automation
```

### systemd Services

Three systemd services manage the processes:

| Service | Binary | Description |
|---|---|---|
| `claude-agent.service` | `python src/agent_worker.py` | Worker process; processes task queue |
| `claude-dashboard.service` | `uvicorn src.dashboard:app` | FastAPI web dashboard |
| `caddy.service` | `caddy run` | Reverse proxy + HTTPS |

All services:
- `Restart=on-failure` — auto-restart if they crash
- `WantedBy=multi-user.target` — start on boot
- `ProtectSystem=full`, `NoNewPrivileges=true` — security hardening

---

## Service Management

### Check Status

```bash
# All three services
sudo systemctl status claude-agent claude-dashboard caddy

# Individual
sudo systemctl status claude-agent
```

### Start / Stop / Restart

```bash
# Restart worker (e.g., after login session renewal)
sudo systemctl restart claude-agent

# Restart dashboard (e.g., after config change)
sudo systemctl restart claude-dashboard

# Reload Caddy config without dropping connections
sudo systemctl reload caddy

# Stop everything (e.g., for maintenance)
sudo systemctl stop claude-agent claude-dashboard
```

### Enable / Disable Auto-Start on Boot

```bash
# Enable (default — set by setup.sh)
sudo systemctl enable claude-agent claude-dashboard caddy

# Disable (if you want manual start only)
sudo systemctl disable claude-agent
```

### View Logs

```bash
# Live journal logs (worker)
sudo journalctl -u claude-agent -f --lines=50

# Live journal logs (dashboard)
sudo journalctl -u claude-dashboard -f --lines=50

# Last 100 lines (worker)
sudo journalctl -u claude-agent -n 100

# Logs since a specific time
sudo journalctl -u claude-agent --since "2024-01-15 02:00:00"

# File-based logs (rotating, managed by Python logging)
tail -f /opt/claude-agent/logs/worker.log
tail -f /opt/claude-agent/logs/dashboard.log
```

---

## Updating the Application

### Option A: Automated Deploy Script (Recommended)

```bash
cd /opt/claude-agent
./scripts/deploy.sh
```

The deploy script performs a **zero-downtime rolling update**:

1. Create pre-deploy database backup
2. `git pull origin main`
3. Activate venv and update pip dependencies
4. Run `python -c "from src.database import init_db; init_db()"` (schema migration check)
5. Restart dashboard (`claude-dashboard.service`)
6. Wait 3 seconds
7. Restart worker (`claude-agent.service`)
8. Run health check — abort and notify if it fails

### Option B: Deploy a Specific Branch

```bash
./scripts/deploy.sh --branch feature/my-feature
```

### Option C: Manual Update

```bash
cd /opt/claude-agent
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet
sudo systemctl restart claude-dashboard claude-agent
curl http://127.0.0.1:8000/health
```

### Checking What Changed

```bash
git log --oneline -10
git diff HEAD~1 HEAD --stat
```

---

## Rollback Procedure

### Step 1 — Stop Services

```bash
sudo systemctl stop claude-agent claude-dashboard
```

### Step 2 — Restore Database (if schema changed)

```bash
# List available backups
ls -lh /opt/claude-agent/backups/

# Restore
cp /opt/claude-agent/backups/nightcrawler_20240115_030000.db \
   /opt/claude-agent/data/nightcrawler.db

# Verify integrity
sqlite3 /opt/claude-agent/data/nightcrawler.db "PRAGMA integrity_check;"
# Expected output: ok
```

### Step 3 — Revert Code

```bash
cd /opt/claude-agent
git log --oneline -5          # find the commit to revert to
git checkout <commit-hash>
pip install -r requirements.txt --quiet
```

### Step 4 — Restart Services

```bash
sudo systemctl start claude-dashboard claude-agent
curl http://127.0.0.1:8000/health
```

---

## Database Backup and Restore

### Manual Backup

```bash
./scripts/backup.sh
```

Backups are saved to:
```
/opt/claude-agent/backups/nightcrawler_YYYYMMDD_HHMMSS.db
```

The script uses SQLite's `.backup` command (online backup — no service stop required) and automatically deletes backups older than 30 days.

### Automated Daily Backups

Add to `crontab -e` (as the `ubuntu` user):

```bash
# Daily backup at 3 AM
0 3 * * * /opt/claude-agent/scripts/backup.sh >> /opt/claude-agent/logs/backup.log 2>&1
```

### Verify Backup Integrity

```bash
sqlite3 /opt/claude-agent/backups/nightcrawler_20240115_030000.db \
    "PRAGMA integrity_check; SELECT count(*) as tasks FROM tasks;"
```

### Restore from Backup

```bash
# 1. Stop worker first (dashboard can stay up)
sudo systemctl stop claude-agent

# 2. Make a safety copy of current DB
cp /opt/claude-agent/data/nightcrawler.db \
   /opt/claude-agent/data/nightcrawler.db.pre-restore

# 3. Restore backup
cp /opt/claude-agent/backups/nightcrawler_20240115_030000.db \
   /opt/claude-agent/data/nightcrawler.db

# 4. Verify
sqlite3 /opt/claude-agent/data/nightcrawler.db "PRAGMA integrity_check;"

# 5. Restart
sudo systemctl start claude-agent
```

---

## Caddy and TLS

### Validate Caddyfile Syntax

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

### Reload Caddy Configuration

```bash
sudo systemctl reload caddy
```

### Check TLS Certificate Status

```bash
sudo caddy certificates
```

Caddy automatically renews Let's Encrypt certificates — no manual action needed. Certificates are stored in `/var/lib/caddy/.local/share/caddy/`.

### Caddyfile Configuration

Located at `/etc/caddy/Caddyfile`:

```
your-agent.duckdns.org {
    reverse_proxy localhost:8000

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }

    encode gzip
    log {
        output file /opt/claude-agent/logs/caddy.log
    }
}
```

### Troubleshoot TLS Issues

```bash
# Check Caddy logs
sudo journalctl -u caddy -n 50

# Test DNS resolution
nslookup your-agent.duckdns.org

# Test HTTP redirect
curl -I http://your-agent.duckdns.org

# Test HTTPS
curl -I https://your-agent.duckdns.org/health
```

---

## DuckDNS IP Updates

The `config/duckdns-update.sh` script runs every 5 minutes via cron to keep your domain pointed at the server's current IP. This is essential because Oracle Cloud IPs can change on reboot.

**Check last update**:

```bash
tail -20 /opt/claude-agent/logs/duckdns.log
```

Expected output when working:
```
OK
```

**Manual update**:

```bash
bash /opt/claude-agent/config/duckdns-update.sh
```

**Verify cron is set**:

```bash
crontab -l | grep duckdns
# Expected: */5 * * * * /opt/claude-agent/config/duckdns-update.sh ...
```

---

## Environment Variables Reference

All variables are set in `/opt/claude-agent/.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `ADMIN_USERNAME` | Yes | — | Dashboard login username |
| `ADMIN_PASSWORD` | Yes | — | Dashboard password (plain text or bcrypt hash) |
| `DOMAIN_NAME` | Yes | — | Full DuckDNS domain: `name.duckdns.org` |
| `DUCKDNS_DOMAIN` | Yes | — | Just the subdomain part: `name` |
| `DUCKDNS_TOKEN` | Yes | — | DuckDNS API token |
| `DB_PATH` | No | `data/nightcrawler.db` | Path to SQLite database file |
| `RESULTS_DIR` | No | `results` | Directory for result Markdown files |
| `BROWSER_DATA_DIR` | No | `browser_data` | Playwright persistent profile directory |
| `LOGS_DIR` | No | `logs` | Directory for log files |
| `TELEGRAM_BOT_TOKEN` | No | — | Telegram bot token (notifications disabled if blank) |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat ID for notifications |
| `POLL_INTERVAL` | No | `10` | Seconds between queue checks when no tasks |
| `BETWEEN_TASK_WAIT` | No | `3` | Seconds to pause between consecutive tasks |
| `RETRY_BASE_DELAY` | No | `5` | Base seconds for exponential backoff |
| `MAX_RETRIES` | No | `3` | Maximum retry attempts before marking task failed |

---

## Resource Usage

Measured on VM.Standard.A1.Flex (ARM, 1 OCPU, 6 GB RAM):

| Component | Idle CPU | Active CPU | RAM |
|---|---|---|---|
| Agent Worker + Chromium | <1% | 10–40% | ~400–800 MB |
| FastAPI Dashboard | <1% | <2% | ~80 MB |
| Caddy | <1% | <1% | ~20 MB |
| SQLite | negligible | negligible | <10 MB |
| **Total** | **~2%** | **~45%** | **~900 MB** |

**VM size recommendation**:
- **A1.Flex (6 GB RAM)**: Recommended. Ample headroom for Chromium.
- **E2.1.Micro (1 GB RAM)**: Avoid. Playwright may trigger OOM killer.

**Disk usage** (approximate after 30 days of moderate use):
- Database: ~10 MB
- Results (1000 tasks): ~50 MB
- Logs (30 days): ~20 MB
- Backups (30 days): ~300 MB

---

## Security Checklist

Run before going live and after each major update:

```
[ ] .env has chmod 600 permissions
[ ] ADMIN_PASSWORD is at least 16 characters (or bcrypt hash)
[ ] Port 8000 is NOT exposed in OCI security list
[ ] SSH uses key-based authentication (password auth disabled)
[ ] ProtectSystem=full and NoNewPrivileges=true in service units
[ ] Let's Encrypt certificate is active: sudo caddy certificates
[ ] DuckDNS updates are running: tail logs/duckdns.log
[ ] UFW or iptables allow only ports 22, 80, 443
[ ] .env is NOT committed to git: git status --ignored | grep .env
[ ] browser_data/ is NOT committed to git
```

---

## Monitoring in Production

### Quick Health Check

```bash
source /opt/claude-agent/venv/bin/activate
python scripts/health_check.py
```

### Morning Report

```bash
python scripts/morning_report.py
```

### External Uptime Monitor

Point an uptime monitor (UptimeRobot, BetterStack, etc.) at:

```
https://your-agent.duckdns.org/health
```

Expected response: `{"status": "ok", ...}`

Check interval: 5 minutes. Alert if status is not `200 OK`.

### Key Log Patterns to Watch

| Log Message | Meaning | Action |
|---|---|---|
| `Claude login verified` | Worker started successfully | None |
| `LoginExpiredException` | Claude session expired | Run `manual_login.py` |
| `Rate limit detected` | Claude usage limit hit | Worker auto-waits; no action needed |
| `Task N failed after X retries` | Task exhausted retries | Check `error_message` in dashboard |
| `recover_stale_tasks: N recovered` | Worker restarted after crash | Review recovered tasks |
| `Certificate renewed` (Caddy) | TLS cert renewed | None |
| `DuckDNS update: OK` | DNS still pointing correctly | None |

See [`monitoring.md`](monitoring.md) for the complete monitoring playbook.
