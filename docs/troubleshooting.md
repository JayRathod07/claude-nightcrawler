# Troubleshooting Guide — Claude Nightcrawler

Diagnostic steps and solutions for common issues.

---

## Table of Contents

1. [Quick Diagnostics](#quick-diagnostics)
2. [Dashboard Not Loading](#dashboard-not-loading)
3. [Cannot Access Domain (DNS)](#cannot-access-domain-dns)
4. [SSL Certificate Error](#ssl-certificate-error)
5. [Tasks Stuck in Running State](#tasks-stuck-in-running-state)
6. [Claude Login Expired](#claude-login-expired)
7. [Tasks Always Fail](#tasks-always-fail)
8. [Worker Keeps Crashing (OOM)](#worker-keeps-crashing-oom)
9. [High Disk Usage](#high-disk-usage)
10. [Rate Limit Not Clearing](#rate-limit-not-clearing)
11. [Telegram Notifications Not Working](#telegram-notifications-not-working)
12. [No Tasks Being Processed](#no-tasks-being-processed)
13. [Wrong Password Error](#wrong-password-error)
14. [Database Locked / Corruption](#database-locked--corruption)
15. [Useful Debug Commands](#useful-debug-commands)

---

## Quick Diagnostics

Run this first for a fast system overview:

```bash
# Service status
sudo systemctl status claude-agent claude-dashboard caddy

# Health endpoint
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# Recent worker logs
tail -30 /opt/claude-agent/logs/worker.log

# Database state
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT status, COUNT(*) FROM tasks GROUP BY status;
     SELECT available, reset_time FROM claude_status;"

# Disk and memory
df -h /opt/claude-agent
free -h
```

---

## Dashboard Not Loading

**Symptom**: `https://your-agent.duckdns.org` returns 502 Bad Gateway or connection refused.

**Step 1 — Is the dashboard running?**

```bash
sudo systemctl status claude-dashboard
# Look for: active (running)
```

If it is stopped, start it:

```bash
sudo systemctl start claude-dashboard
sudo journalctl -u claude-dashboard -n 30
```

**Step 2 — Is it listening on port 8000?**

```bash
ss -tlnp | grep 8000
# Expected: 127.0.0.1:8000
```

If nothing is shown, the process is not bound. Check the logs for startup errors.

**Step 3 — Is Caddy running and configured correctly?**

```bash
sudo systemctl status caddy
curl -v http://127.0.0.1:8000/health    # direct access
curl -vI https://your-agent.duckdns.org/health   # via Caddy

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**Step 4 — Port conflict?**

```bash
ss -tlnp | grep 8000
# Another process using port 8000 will block the dashboard
```

**Common causes and fixes**:

| Cause | Fix |
|---|---|
| Dashboard crashed | `sudo systemctl restart claude-dashboard` |
| Caddyfile syntax error | `sudo caddy validate --config /etc/caddy/Caddyfile` |
| Port 8000 used by another process | Kill the conflicting process |
| Python dependency missing | `source venv/bin/activate && pip install -r requirements.txt` |

---

## Cannot Access Domain (DNS)

**Symptom**: Browser cannot resolve `your-agent.duckdns.org` or shows `ERR_NAME_NOT_RESOLVED`.

**Diagnose**:

```bash
# Check if DuckDNS last update succeeded
tail -5 /opt/claude-agent/logs/duckdns.log
# Expected: OK

# DNS lookup
nslookup your-agent.duckdns.org
# Should return your Oracle Public IP

# Verify the server's current public IP matches
curl -s https://ipinfo.io/ip
```

**Fix**:

```bash
# Run a manual DuckDNS update
bash /opt/claude-agent/config/duckdns-update.sh

# Verify cron is set
crontab -l | grep duckdns
```

If cron is missing, reinstall it:

```bash
(crontab -l 2>/dev/null | grep -v duckdns; \
 echo "*/5 * * * * bash /opt/claude-agent/config/duckdns-update.sh >> /opt/claude-agent/logs/duckdns.log 2>&1") \
 | crontab -
```

---

## SSL Certificate Error

**Symptom**: Browser shows `ERR_CERT_INVALID`, `NET::ERR_CERT_AUTHORITY_INVALID`, or a certificate warning.

**Diagnose**:

```bash
sudo caddy certificates
sudo journalctl -u caddy | grep -i "acme\|cert\|tls\|error"
curl -vI https://your-agent.duckdns.org 2>&1 | grep -E "SSL|cert|expire"
```

**Cause 1**: DNS was not pointing to the server when Caddy first tried ACME validation.

**Fix**:

```bash
# Ensure DuckDNS is updated and DNS resolves correctly first:
nslookup your-agent.duckdns.org   # must return your IP

# Then restart Caddy to retry certificate issuance:
sudo systemctl restart caddy
sleep 30
sudo caddy certificates
```

**Cause 2**: Ports 80 and 443 not open in Oracle Cloud Security List.

**Fix**: Add ingress rules in OCI Console → Networking → Security Lists (see setup-guide.md).

**Cause 3**: iptables blocking port 80 (required for ACME HTTP-01 challenge).

```bash
sudo iptables -L INPUT -n | grep "80\|443"
# Flush if needed:
sudo iptables -P INPUT ACCEPT && sudo iptables -F
sudo netfilter-persistent save
```

---

## Tasks Stuck in Running State

**Symptom**: Task shows `running` status for more than 10–15 minutes.

**Cause**: The worker process crashed while processing the task. The database still shows `running` but nothing is processing it.

**Fix 1 — Restart worker** (automatic recovery):

```bash
sudo systemctl restart claude-agent
# recover_stale_tasks() resets stuck tasks to 'queued' on startup
```

**Fix 2 — Manual SQL reset** (if worker cannot restart):

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "UPDATE tasks SET status='queued', retry_count=retry_count+1 WHERE status='running';"
```

**Verify**:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT id, status, retry_count FROM tasks WHERE id = <task_id>;"
```

---

## Claude Login Expired

**Symptom**: Worker log shows `LoginExpiredException`. Tasks fail immediately.

**Why it happens**: Claude.ai sessions expire after days to weeks. The browser cookie in `browser_data/` is no longer valid.

**Fix**:

```bash
# Stop worker
sudo systemctl stop claude-agent

# Re-login (with X11 forwarding from local machine)
ssh -X ubuntu@YOUR_PUBLIC_IP
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/manual_login.py

# Restart worker
sudo systemctl start claude-agent

# Verify
sudo journalctl -u claude-agent -n 10
# Look for: Claude login verified
```

**Alternative (cookie method)**:

1. Log in to claude.ai in your local browser
2. Open DevTools → Application → Cookies → claude.ai → copy `sessionKey` value
3. `python /opt/claude-agent/scripts/manual_login.py --cookie "YOUR_KEY"`

---

## Tasks Always Fail

**Symptom**: Tasks move to `failed` with error messages.

**Step 1 — Check error messages**:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT id, error_message FROM tasks WHERE status='failed' ORDER BY id DESC LIMIT 5;"
```

**Step 2 — Check error screenshots**:

```bash
ls -lt /opt/claude-agent/logs/screenshots/ | head -5
# Transfer screenshots to your local machine for inspection:
# scp ubuntu@YOUR_IP:/opt/claude-agent/logs/screenshots/error_*.png ~/Desktop/
```

**Common error patterns**:

| Error Message | Cause | Fix |
|---|---|---|
| `LoginExpiredException` | Claude session expired | Re-run `manual_login.py` |
| `Timeout waiting for selector` | Claude UI changed | Check if Claude.ai updated; may need selector update |
| `ResponseExtractionError` | Could not find response text | Review screenshots; may be a UI layout change |
| `Browser startup failed` | Chromium installation issue | `playwright install chromium` |
| `ERR_INTERNET_DISCONNECTED` | Network connectivity issue | Check Oracle Cloud networking; ping google.com |
| `context was destroyed` | Browser crashed | `sudo systemctl restart claude-agent` |

**Step 3 — Test Claude adapter manually**:

```bash
source /opt/claude-agent/venv/bin/activate
python3 -c "
from src.claude_adapter import ClaudeAdapter
a = ClaudeAdapter()
a.start()
print('Logged in:', a.check_login())
"
```

---

## Worker Keeps Crashing (OOM)

**Symptom**: `claude-agent` restarts repeatedly. `journalctl` shows `Killed` or OOM events.

**Diagnose**:

```bash
sudo dmesg | grep -i "killed process\|oom"
sudo journalctl -k | grep -i "oom\|memory"
free -h
```

**Fix 1 — Add swap space** (recommended for A1.Flex, essential for E2.Micro):

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # verify swap appears
```

**Fix 2 — Reduce Chromium memory**:

Add Playwright launch args to `src/claude_adapter.py` (if customised):

```python
browser = playwright.chromium.launch_persistent_context(
    ...,
    args=["--disable-dev-shm-usage", "--no-sandbox", "--memory-pressure-off"]
)
```

**Fix 3 — Monitor memory after startup**:

```bash
watch -n 5 "free -h && ps aux --sort=-%mem | head -10"
```

---

## High Disk Usage

**Diagnose**:

```bash
df -h /opt/claude-agent
du -sh /opt/claude-agent/*/
# Typical culprits: results/, browser_data/, logs/
```

**Clean old results** (keep last 30 days):

```bash
find /opt/claude-agent/results -name "*.md" -mtime +30 -delete
```

**Rotate old logs**:

```bash
# Truncate logs over 50 MB
find /opt/claude-agent/logs -name "*.log" -size +50M -exec truncate -s 0 {} \;

# Or delete old screenshots
find /opt/claude-agent/logs/screenshots -name "*.png" -mtime +7 -delete
```

**Clean Chromium cache** (safe to delete):

```bash
rm -rf /opt/claude-agent/browser_data/Default/Cache/
rm -rf /opt/claude-agent/browser_data/Default/Code\ Cache/
```

**Compress old backups**:

```bash
find /opt/claude-agent/backups -name "*.db" -mtime +7 | \
    xargs -I {} gzip {}
```

---

## Rate Limit Not Clearing

**Symptom**: Tasks stay in `waiting_limit` long after the reset time has passed.

**Diagnose**:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT available, reset_time, last_limit_message FROM claude_status;"
```

**Fix 1 — Force clear the rate limit** (if reset_time is clearly in the past):

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "UPDATE claude_status SET available=1, reset_time=NULL WHERE id=1;"
```

**Fix 2 — Restart worker** (which re-checks via `is_claude_available()`):

```bash
sudo systemctl restart claude-agent
```

**Fix 3 — Re-queue stuck waiting_limit tasks**:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "UPDATE tasks SET status='queued' WHERE status='waiting_limit';"
```

---

## Telegram Notifications Not Working

**Symptom**: Morning reports and health alerts are not arriving.

**Step 1 — Verify configuration**:

```bash
grep TELEGRAM /opt/claude-agent/.env
# Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set
```

**Step 2 — Test manually**:

```bash
source /opt/claude-agent/venv/bin/activate
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv('/opt/claude-agent/.env')
from src.notifier import Notifier
n = Notifier()
n.send_message('Test from Claude Nightcrawler')
print('Sent OK')
"
```

**Step 3 — Common Telegram errors**:

| Error | Cause | Fix |
|---|---|---|
| `400 Bad Request: chat not found` | Wrong `TELEGRAM_CHAT_ID` | Verify chat ID with @userinfobot |
| `401 Unauthorized` | Wrong bot token | Regenerate token with @BotFather |
| `No response` | Bot not started | Send /start to your bot first |
| `Flood control exceeded` | Too many messages | Notifier handles retry_after; wait |

---

## No Tasks Being Processed

**Symptom**: Tasks sit in `queued` indefinitely.

**Step 1 — Is the worker running?**

```bash
sudo systemctl status claude-agent
```

**Step 2 — Is Claude rate-limited?**

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT available, reset_time FROM claude_status;"
```

If `available=0`, check `reset_time`. If it is in the past, force clear:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "UPDATE claude_status SET available=1, reset_time=NULL WHERE id=1;"
sudo systemctl restart claude-agent
```

**Step 3 — Is the worker polling?**

```bash
tail -20 /opt/claude-agent/logs/worker.log
# Should show "No tasks in queue, sleeping Xs" or "Processing task N"
```

---

## Wrong Password Error

**Symptom**: Browser shows 401 Unauthorized even with the correct password.

**Cause 1**: The password was set as a bcrypt hash in `.env` but you're entering the plain password. Verify which format is in use:

```bash
grep ADMIN_PASSWORD /opt/claude-agent/.env
# If it starts with $2b$, it's bcrypt — enter the ORIGINAL plain password
```

**Cause 2**: `.env` was not reloaded after changing `ADMIN_PASSWORD`:

```bash
sudo systemctl restart claude-dashboard
```

**Cause 3**: Special characters in the password need escaping in the URL:

```bash
# Use -u flag in curl instead of embedding in URL
curl -u "admin:my!password" http://127.0.0.1:8000/api/stats
```

---

## Database Locked / Corruption

**Symptom**: Logs show `sqlite3.OperationalError: database is locked` or `SQLITE_CORRUPT`.

**Diagnose**:

```bash
sqlite3 /opt/claude-agent/data/nightcrawler.db "PRAGMA integrity_check;"
# Expected: ok
```

**Fix: locked database**

```bash
# Find and kill any zombie sqlite3 processes
lsof /opt/claude-agent/data/nightcrawler.db
sudo kill -9 <PID>

# Restart services
sudo systemctl restart claude-agent claude-dashboard
```

**Fix: corruption**

```bash
# Stop everything
sudo systemctl stop claude-agent claude-dashboard

# Try recovery
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    ".recover" > /tmp/recovered.sql
sqlite3 /tmp/recovered.db < /tmp/recovered.sql
sqlite3 /tmp/recovered.db "PRAGMA integrity_check;"

# If recovered: replace DB
cp /opt/claude-agent/data/nightcrawler.db \
   /opt/claude-agent/backups/nightcrawler_corrupt_$(date +%Y%m%d).db
mv /tmp/recovered.db /opt/claude-agent/data/nightcrawler.db

# Or restore from backup (see deployment.md)
```

---

## Useful Debug Commands

```bash
# All service logs combined, live
journalctl -u claude-agent -u claude-dashboard -f

# Database overview
sqlite3 /opt/claude-agent/data/nightcrawler.db "
  SELECT status, COUNT(*) as count FROM tasks GROUP BY status;
  SELECT available, reset_time, total_requests_today FROM claude_status;
"

# Recent task events (audit log)
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT * FROM task_events ORDER BY id DESC LIMIT 20;"

# Run health check script
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/health_check.py

# Run morning report
python /opt/claude-agent/scripts/morning_report.py

# Test API endpoints directly
curl -u admin:password http://127.0.0.1:8000/api/status
curl -u admin:password http://127.0.0.1:8000/api/stats
curl -u admin:password "http://127.0.0.1:8000/api/tasks?limit=5"

# Check Playwright can start
source /opt/claude-agent/venv/bin/activate
python3 -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); print('OK'); b.close()"
```
