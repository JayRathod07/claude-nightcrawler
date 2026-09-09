# Monitoring & Maintenance — Claude Nightcrawler

Ongoing monitoring strategy, health checks, alerting, log management, and maintenance schedule.

---

## Table of Contents

1. [Health Check System](#health-check-system)
2. [Morning Report](#morning-report)
3. [Telegram Notifications](#telegram-notifications)
4. [Log Management](#log-management)
5. [Backup Strategy](#backup-strategy)
6. [Maintenance Schedule](#maintenance-schedule)
7. [Key Log Patterns](#key-log-patterns)
8. [External Uptime Monitoring](#external-uptime-monitoring)
9. [Performance Monitoring](#performance-monitoring)

---

## Health Check System

### Run Manually

```bash
# Human-readable output
python /opt/claude-agent/scripts/health_check.py

# Machine-readable JSON (for scripts/CI)
python /opt/claude-agent/scripts/health_check.py --json

# Send Telegram alert if any check fails
python /opt/claude-agent/scripts/health_check.py --alert

# Silent + alert (for cron use)
python /opt/claude-agent/scripts/health_check.py --alert --quiet
```

### What Gets Checked

| Check | Fail Condition | Level |
|---|---|---|
| Database | Cannot connect or integrity error | ❌ Fail |
| Claude Status | (informational) | ℹ️ Info |
| Results Dir | Missing or not writable | ❌ Fail |
| Logs Dir | Missing | ❌ Fail |
| Browser Data Dir | Missing | ❌ Fail |
| Disk Space | > 95% full | ❌ Fail |
| Disk Space | > 85% full | ⚠️ Warn |
| Python Packages | Any package unimportable | ❌ Fail |
| Dashboard HTTP | `/health` returns non-200 | ❌ Fail |
| Service: claude-agent | systemd state ≠ active | ❌ Fail |
| Service: claude-dashboard | systemd state ≠ active | ❌ Fail |
| Recent Activity | No tasks in last 24h | ⚠️ Warn |

### Automate with Cron

```bash
# Check every 30 minutes, send Telegram alert on failure
*/30 * * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/health_check.py --alert --quiet >> /opt/claude-agent/logs/health.log 2>&1
```

---

## Morning Report

Generates a daily summary of the previous 24 hours of agent activity.

### Run Manually

```bash
# Print to terminal
python /opt/claude-agent/scripts/morning_report.py

# Print + send via Telegram
python /opt/claude-agent/scripts/morning_report.py --send

# Send only (no stdout — for cron)
python /opt/claude-agent/scripts/morning_report.py --send --quiet
```

### Report Contents

- ✅ Tasks completed yesterday
- ❌ Tasks failed yesterday
- ⏱ Average completion time
- 📡 API requests made today
- 📋 Current queue state (queued/running/waiting)
- 🤖 Claude availability status
- 💾 Disk usage + results directory size
- Details of failed tasks (up to 5)
- ⚠️ Disk space warning if > 85%

### Schedule with Cron (8 AM daily)

```bash
0 8 * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/morning_report.py --send --quiet >> /opt/claude-agent/logs/morning_report.log 2>&1
```

---

## Telegram Notifications

### Setup

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **bot token**
4. Start a chat with your new bot
5. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`
6. Add to `.env`:

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

### What Triggers a Notification

| Event | Env Flag | Default |
|---|---|---|
| Worker starts | always | on |
| Task completed (with preview) | `TELEGRAM_NOTIFY_ON_COMPLETE` | on |
| Usage limit detected | `TELEGRAM_NOTIFY_ON_LIMIT` | on |
| Task fails permanently | `TELEGRAM_NOTIFY_ON_ERROR` | on |
| Worker shuts down | always | on |
| Daily morning report | manual (`--send` flag) | — |
| Health check alert | manual (`--alert` flag) | — |

### Disable Specific Notifications

```bash
# In .env — disable completion messages (reduce noise)
TELEGRAM_NOTIFY_ON_COMPLETE=false

# Disable error alerts (not recommended)
TELEGRAM_NOTIFY_ON_ERROR=false
```

---

## Log Management

### Log Files

| File | Contents | Rotation |
|---|---|---|
| `logs/worker.log` | Agent worker process events | systemd journal |
| `logs/dashboard.log` | FastAPI access and errors | systemd journal |
| `logs/duckdns.log` | DuckDNS IP update results | Manual |
| `logs/health.log` | Health check cron output | Manual |
| `logs/morning_report.log` | Morning report cron output | Manual |
| `logs/backup.log` | Backup cron output | Manual |
| `logs/screenshots/` | Playwright error screenshots | Manual |

### View Live Logs

```bash
# Worker real-time
sudo journalctl -u claude-agent -f

# Dashboard real-time  
sudo journalctl -u claude-dashboard -f

# Both together
sudo journalctl -u claude-agent -u claude-dashboard -f

# Last 100 lines of file log
tail -100 /opt/claude-agent/logs/worker.log
```

### Log Rotation (logrotate)

Create `/etc/logrotate.d/claude-agent`:

```
/opt/claude-agent/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    copytruncate
}
```

Then test: `sudo logrotate --debug /etc/logrotate.d/claude-agent`

---

## Backup Strategy

### Database Backups

The `scripts/backup.sh` script uses SQLite's online backup API — safe while the database is live.

```bash
# Manual backup
./scripts/backup.sh

# Tagged backup (pre-update)
./scripts/backup.sh --tag pre-update
```

Backups are stored in `backups/` with a 30-day retention policy.

### Automated Daily Backups

```bash
# Add to crontab (3 AM daily)
0 3 * * * /opt/claude-agent/scripts/backup.sh >> /opt/claude-agent/logs/backup.log 2>&1
```

### Off-Site Backup (Recommended)

```bash
# Example: sync backups to a remote host
rsync -az /opt/claude-agent/backups/ user@backup-server:/backups/claude-agent/

# Or to an S3-compatible bucket
s3cmd sync /opt/claude-agent/backups/ s3://my-bucket/claude-agent-backups/
```

---

## Maintenance Schedule

| Frequency | Task |
|---|---|
| **Daily** (cron 03:00) | Database backup |
| **Daily** (cron 08:00) | Morning report via Telegram |
| **Every 5 min** (cron) | DuckDNS IP update |
| **Every 30 min** (cron) | Health check + Telegram alert on failure |
| **Weekly** | Review `logs/worker.log` for patterns |
| **Weekly** | Check disk space and rotate old results if needed |
| **Monthly** | `git pull` + `pip install -r requirements.txt` + restart |
| **Monthly** | Verify Let's Encrypt certificate renewal (`caddy certificates`) |
| **Quarterly** | Review and rotate `ADMIN_PASSWORD` |

---

## Key Log Patterns

Watch for these patterns in `logs/worker.log` and `journalctl -u claude-agent`:

| Log Message | Meaning | Action Required |
|---|---|---|
| `Claude login verified` | Worker started successfully | None |
| `Starting task processing loop` | Worker is active | None |
| `No tasks in queue, sleeping Xs` | Normal idle state | None |
| `Processing task N: "..."` | Task picked up | None |
| `Task N completed` | Success | None |
| `LoginExpiredException` | Claude session expired | Run `manual_login.py` |
| `Rate limit detected` | Claude usage limit hit | Worker auto-waits; no action needed |
| `Task N failed after X retries` | Task exhausted retries | Review `error_message` in dashboard |
| `recover_stale_tasks: N recovered` | Worker restarted after crash | Review recovered tasks |
| `ResponseExtractionError` | Could not extract Claude's response | Check `logs/screenshots/` |
| `ClaudeAdapterError` | Browser automation error | Check screenshot; may be transient |
| `DuckDNS update: OK` | DNS still pointing correctly | None |
| `Certificate renewed` (Caddy) | TLS cert auto-renewed | None |

---

## External Uptime Monitoring

Use a free external monitor to alert you if the server goes down:

- **UptimeRobot** — monitors `https://your-agent.duckdns.org/health` every 5 minutes, sends email/Telegram on failure
- **BetterStack** — similar; has Telegram integration
- **Healthchecks.io** — ping-based; add a `curl` to your cron to confirm it ran

**UptimeRobot setup**:

1. Create account at [uptimerobot.com](https://uptimerobot.com)
2. Add monitor → **HTTP(s)** → URL: `https://your-agent.duckdns.org/health`
3. Alert when response is not `200 OK`
4. Set check interval: 5 minutes
5. Notification: email or Telegram bot

**Healthchecks.io setup** (verifies cron runs):

```bash
# Add to morning report cron
0 8 * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/morning_report.py --send --quiet \
    && curl -fsS https://hc-ping.com/YOUR-UUID > /dev/null
```

---

## Performance Monitoring

```bash
# Task queue depth (should drain overnight)
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT status, COUNT(*) FROM tasks GROUP BY status;"

# Worker uptime
sudo systemctl status claude-agent | grep "Active:"

# Memory usage by process
ps aux | grep -E "python|uvicorn|chrome" | awk '{sum+=$6} END {print sum/1024 " MB"}'

# Disk usage by directory
du -sh /opt/claude-agent/*/

# Average task completion time (last 24 hours)
sqlite3 /opt/claude-agent/data/nightcrawler.db "
SELECT
    ROUND(AVG((julianday(completed_at) - julianday(started_at)) * 1440), 2) AS avg_minutes,
    COUNT(*) AS completed_count
FROM tasks
WHERE status = 'completed'
  AND completed_at >= datetime('now', '-24 hours');
"

# Requests today
sqlite3 /opt/claude-agent/data/nightcrawler.db \
    "SELECT total_requests_today FROM claude_status;"
```

