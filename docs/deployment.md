# Deployment Guide — Claude Nightcrawler

This guide covers production deployment architecture, service management, updates, and rollbacks.

---

## Architecture Overview

```
Internet ──HTTPS──► DuckDNS ──► Oracle Cloud Public IP
                                       │
                              Caddy (port 443)
                              • Auto TLS (Let's Encrypt)
                              • Security headers
                              • Reverse proxy
                                       │
                              FastAPI Dashboard (port 8000, localhost only)
                              • HTTP Basic Auth
                              • REST API
                              • Jinja2 templates
                                       │
                              SQLite Database (WAL mode)
                                    ↑  │
                                    │  ▼
                              Agent Worker Process
                              • Playwright / Chromium
                              • Task queue loop
                              • Claude.ai automation
```

---

## Service Management

### Check Status

```bash
sudo systemctl status claude-agent claude-dashboard caddy
```

### Start / Stop / Restart

```bash
sudo systemctl restart claude-agent
sudo systemctl restart claude-dashboard
sudo systemctl reload caddy        # reload config without dropping connections
```

### Enable/Disable Auto-Start

```bash
sudo systemctl enable  claude-agent claude-dashboard caddy   # start at boot
sudo systemctl disable claude-agent                           # don't start at boot
```

### View Live Logs

```bash
# Worker
sudo journalctl -u claude-agent -f --lines=50

# Dashboard
sudo journalctl -u claude-dashboard -f --lines=50

# File logs
tail -f /opt/claude-agent/logs/worker.log
tail -f /opt/claude-agent/logs/dashboard.log
```

---

## Updating the Application

### Option A: Automated Deploy Script

```bash
cd /opt/claude-agent
./scripts/deploy.sh
```

This script:
1. Creates a pre-deploy database backup
2. `git pull origin main`
3. Updates pip dependencies
4. Runs DB init check
5. Restarts services in order (dashboard → worker)
6. Runs a health check

### Option B: Manual Update

```bash
cd /opt/claude-agent
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart claude-dashboard claude-agent
```

### Deploy a Specific Branch

```bash
./scripts/deploy.sh --branch feature/my-feature
```

---

## Database Backup & Restore

### Manual Backup

```bash
./scripts/backup.sh
```

Backups are saved to `/opt/claude-agent/backups/nightcrawler_YYYYMMDD_HHMMSS.db`.

### Automated Daily Backups

Add to crontab (`crontab -e`):

```bash
0 3 * * * /opt/claude-agent/scripts/backup.sh >> /opt/claude-agent/logs/backup.log 2>&1
```

### Restore from Backup

```bash
# Stop services first
sudo systemctl stop claude-agent claude-dashboard

# Restore
cp /opt/claude-agent/backups/nightcrawler_20240101_030000.db \
   /opt/claude-agent/data/nightcrawler.db

# Verify integrity
sqlite3 /opt/claude-agent/data/nightcrawler.db "PRAGMA integrity_check;"

# Restart
sudo systemctl start claude-dashboard claude-agent
```

---

## Caddy / TLS

### Validate Config

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

### Reload Config

```bash
sudo systemctl reload caddy
```

### Check Certificate

```bash
sudo caddy certificates
```

Caddy automatically renews Let's Encrypt certificates — no manual action needed.

---

## DuckDNS IP Updates

The `config/duckdns-update.sh` script runs every 5 minutes via cron to keep your domain pointed at the server's current IP. This is essential because Oracle Cloud dynamic IPs can change on reboot.

Check last update:

```bash
tail -5 /opt/claude-agent/logs/duckdns.log
```

Manual update:

```bash
bash -c 'source /opt/claude-agent/.env && /opt/claude-agent/config/duckdns-update.sh'
```

---

## Resource Usage

| Component | CPU | RAM |
|---|---|---|
| Agent Worker + Chromium | 10–40% | ~400–800 MB |
| FastAPI Dashboard | <2% | ~80 MB |
| Caddy | <1% | ~20 MB |
| SQLite | negligible | <10 MB |
| **Total** | **~45%** | **~900 MB** |

The A1.Flex instance (6 GB RAM) has ample headroom. The E2.1.Micro (1 GB RAM) is tight — Playwright may OOM. Use A1.Flex if possible.

---

## Security Checklist

- [ ] `.env` has `chmod 600` permissions
- [ ] `ADMIN_PASSWORD` is at least 16 characters
- [ ] Port 8000 is NOT exposed externally (UFW blocks it)
- [ ] SSH uses key-based authentication (password auth disabled)
- [ ] `ProtectSystem=full` and `NoNewPrivileges=true` in service units
- [ ] Let's Encrypt certificate is active (`caddy certificates`)
- [ ] DuckDNS updates are running (`cat logs/duckdns.log`)
