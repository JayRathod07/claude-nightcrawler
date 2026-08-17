# Troubleshooting Guide — Claude Nightcrawler

Common issues, diagnostic steps, and solutions.

---

## Quick Diagnostics

Run this first for a fast system overview:

```bash
sudo systemctl status claude-agent claude-dashboard caddy
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
tail -20 /opt/claude-agent/logs/worker.log
```

---

## Issue: Dashboard Not Loading

### Symptom
`https://your-agent.duckdns.org` returns 502 Bad Gateway or connection refused.

### Diagnosis

```bash
# Is the dashboard process running?
sudo systemctl status claude-dashboard

# Is it listening on port 8000?
ss -tlnp | grep 8000

# Is Caddy running?
sudo systemctl status caddy

# Can Caddy reach the dashboard locally?
curl -v http://127.0.0.1:8000/health

# Check Caddy logs
sudo journalctl -u caddy -n 50
```

### Solutions

```bash
# Restart dashboard
sudo systemctl restart claude-dashboard

# Check for port conflict
ss -tlnp | grep 8000

# Validate and reload Caddy config
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

---

## Issue: Cannot Access Domain (DNS Problem)

### Symptom
Browser cannot resolve `your-agent.duckdns.org`.

### Diagnosis

```bash
# Check DuckDNS last update
cat /opt/claude-agent/logs/duckdns.log | tail -5

# Manual DNS lookup
nslookup your-agent.duckdns.org

# Verify DuckDNS update works
bash -c 'source /opt/claude-agent/.env && /opt/claude-agent/config/duckdns-update.sh'

# Check cron is running
crontab -l | grep duckdns
```

### Solution

```bash
# Run manual DuckDNS update
bash -c 'source /opt/claude-agent/.env && /opt/claude-agent/config/duckdns-update.sh'

# Re-install cron job
(crontab -l 2>/dev/null | grep -v duckdns; \
 echo "*/5 * * * * bash -c 'source /opt/claude-agent/.env && /opt/claude-agent/config/duckdns-update.sh' >> /opt/claude-agent/logs/duckdns.log 2>&1") \
 | crontab -
```

---

## Issue: SSL Certificate Error

### Symptom
Browser shows certificate warning or `ERR_CERT_INVALID`.

### Diagnosis

```bash
sudo caddy certificates
curl -vI https://your-agent.duckdns.org 2>&1 | grep -E "SSL|certificate|expire"
sudo journalctl -u caddy | grep -i "acme\|cert\|tls"
```

### Solution
Caddy automatically renews certificates. If renewal failed:

```bash
# Force certificate renewal
sudo systemctl stop caddy
sudo caddy run --config /etc/caddy/Caddyfile &
sleep 30
sudo pkill caddy
sudo systemctl start caddy
```

> Ensure ports 80 and 443 are open in Oracle Cloud Security Lists AND UFW.

---

## Issue: Tasks Stuck in "Running" State

### Symptom
Task shows `running` for more than 10 minutes.

### Cause
The worker crashed while processing the task. The database has the task marked as `running` but no process is actually handling it.

### Solution

```bash
# Restart the worker — recover_stale_tasks() automatically resets them on startup
sudo systemctl restart claude-agent

# Or manually reset via SQLite
sqlite3 /opt/claude-agent/data/nightcrawler.db \
  "UPDATE tasks SET status='queued', retry_count=retry_count+1 WHERE status='running';"
```

---

## Issue: Claude Login Expired

### Symptom
Worker log shows `LoginExpiredException` or tasks fail immediately with login errors.

### Solution

```bash
# Stop worker so it doesn't interfere
sudo systemctl stop claude-agent

# Re-run manual login
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/manual_login.py

# Restart worker
sudo systemctl start claude-agent
```

---

## Issue: Worker Keeps Crashing (OOM)

### Symptom
`claude-agent` service restarts repeatedly. `journalctl` shows `Killed` or OOM events.

### Diagnosis

```bash
# Check recent OOM events
sudo dmesg | grep -i "killed process"
sudo journalctl -k | grep -i oom

# Check memory
free -h
```

### Solutions

```bash
# Add swap space (recommended for E2.1.Micro with 1 GB RAM)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Reduce Playwright memory usage
# Edit .env: add HEADLESS=true (should already be set)

# Check MemoryMax in service unit
sudo systemctl cat claude-agent | grep MemoryMax
```

---

## Issue: Tasks Always Fail

### Symptom
Tasks move to `failed` status with error messages.

### Diagnosis

```bash
# View error details in DB
sqlite3 /opt/claude-agent/data/nightcrawler.db \
  "SELECT id, error_message FROM tasks WHERE status='failed' ORDER BY id DESC LIMIT 5;"

# Check error screenshots
ls -la /opt/claude-agent/logs/screenshots/

# View recent worker logs
tail -100 /opt/claude-agent/logs/worker.log | grep -i error
```

### Common Causes

| Error | Cause | Solution |
|---|---|---|
| `LoginExpiredException` | Claude session expired | Re-run `manual_login.py` |
| `Timeout: element not found` | Claude UI changed | Check for UI updates; update selectors |
| `Browser startup failed` | Playwright/Chromium issue | Run `playwright install chromium` |
| `No response elements found` | Response extraction failed | Check error screenshots |

---

## Issue: High Disk Usage

### Diagnosis

```bash
du -sh /opt/claude-agent/*/
du -sh /opt/claude-agent/results/
du -sh /opt/claude-agent/browser_data/
du -sh /opt/claude-agent/logs/
```

### Solution

```bash
# Clean old results (keep last 30 days)
find /opt/claude-agent/results -name "*.md" -mtime +30 -delete

# Clean old logs
find /opt/claude-agent/logs -name "*.log" -mtime +14 -exec truncate -s 0 {} \;

# Clean browser cache
rm -rf /opt/claude-agent/browser_data/Default/Cache/
```

---

## Useful Debug Commands

```bash
# Check all service logs in real time
journalctl -u claude-agent -u claude-dashboard -f

# Database state overview
sqlite3 /opt/claude-agent/data/nightcrawler.db "
  SELECT status, COUNT(*) FROM tasks GROUP BY status;
  SELECT * FROM claude_status;
"

# Run health check script
python /opt/claude-agent/scripts/health_check.py

# Test dashboard API directly
curl -u admin:password http://127.0.0.1:8000/api/status
curl -u admin:password http://127.0.0.1:8000/api/stats
```
