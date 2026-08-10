# Troubleshooting Guide

## Common Issues

### Browser / Playwright Issues

**Problem:** `playwright install` fails
```
Solution: Run with admin/sudo: sudo playwright install-deps
```

**Problem:** Login session not persisting
```
Solution: Ensure browser_data/ directory exists and has write permissions.
Run: chmod 755 browser_data/
```

**Problem:** "Could not find prompt input field"
```
Solution: Claude.ai may have updated its UI. Check logs/screenshots/ for
error screenshots. Update selectors in src/claude_adapter.py.
```

### Database Issues

**Problem:** `database is locked`
```
Solution: Only one process should write at a time. Check for zombie processes:
ps aux | grep python
```

**Problem:** Tasks stuck in 'running' state
```
Solution: Restart the worker service. It auto-recovers stale tasks on startup.
sudo systemctl restart claude-agent
```

### Service Issues

**Problem:** Dashboard not accessible
```
Solution: Check if uvicorn is running:
sudo systemctl status claude-dashboard

Check Caddy is proxying correctly:
sudo systemctl status caddy
caddy validate --config /etc/caddy/Caddyfile
```

**Problem:** Telegram notifications not arriving
```
Solution: Verify your bot token and chat ID in .env.
Test manually:
python -c "from src.notifier import Notifier; n = Notifier(); n.send('Test')"
```

### Permission Issues

**Problem:** `.env` file errors
```
Solution: Set correct permissions:
chmod 600 .env
```

## Log Locations

| Log | Location |
|-----|----------|
| Worker logs | `logs/worker.log` or `journalctl -u claude-agent` |
| Dashboard logs | `logs/dashboard.log` or `journalctl -u claude-dashboard` |
| Error screenshots | `logs/screenshots/` |
| Caddy logs | `journalctl -u caddy` |

## Getting Help

1. Check the logs first
2. Search [existing issues](https://github.com/yourusername/claude-nightcrawler/issues)
3. Open a new issue with the bug report template
