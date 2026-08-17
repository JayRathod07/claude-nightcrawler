# Architecture — Claude Nightcrawler

Technical architecture, data flow, and component design.

---

## System Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                    SYSTEM ARCHITECTURE                         │
└────────────────────────────────────────────────────────────────┘

  [User: Phone / Laptop / Tablet]
              │
        HTTPS (port 443)
              │
  ┌───────────▼───────────┐
  │     DuckDNS Service   │ ◄── cron every 5 min (duckdns-update.sh)
  └───────────┬───────────┘
              │ Dynamic DNS → Oracle Cloud Public IP
  ┌───────────▼───────────┐
  │    Caddy Web Server   │ ◄── Auto TLS (Let's Encrypt)
  │    Reverse Proxy      │     Security headers / HSTS
  └───────────┬───────────┘
              │ localhost:8000
  ┌───────────▼──────────────────┐
  │   FastAPI Dashboard          │
  │   • HTTP Basic Auth          │
  │   • Jinja2 HTML templates    │
  │   • REST API (/api/*)        │
  │   • Static assets            │
  └───────────┬──────────────────┘
              │ SQLite WAL (read)
  ┌───────────▼──────────────────┐    ┌──────────────────────────┐
  │     SQLite Database          │◄───│   Agent Worker Process   │
  │     • tasks                  │    │   • Infinite task loop   │
  │     • claude_status          │───►│   • Retry / backoff      │
  │     • task_events            │    │   • Crash recovery       │
  │     WAL mode (concurrent R/W)│    │   • Result file save     │
  └──────────────────────────────┘    └───────────┬──────────────┘
                                                  │
                                      ┌───────────▼──────────────┐
                                      │  Playwright Controller   │
                                      │  • Persistent browser    │
                                      │  • Chromium (headless)   │
                                      │  • Session cookies saved │
                                      └───────────┬──────────────┘
                                                  │  HTTPS
                                      ┌───────────▼──────────────┐
                                      │     claude.ai website    │
                                      └──────────────────────────┘
```

---

## Components

### `src/database.py` — Data Layer
- **SQLite WAL mode**: allows concurrent reads from dashboard while worker writes
- **Thread-safe**: uses `threading.local()` connections with `check_same_thread=False`
- **Schema**: `tasks` table with status FSM, `claude_status` singleton, `task_events` audit log
- **Crash recovery**: `recover_stale_tasks()` resets any `running` tasks to `queued` on startup

### `src/claude_adapter.py` — Browser Automation
- **Playwright persistent context**: browser profile stored in `browser_data/` — cookies survive restarts
- **Limit detection**: regex patterns against response text (`usage limit`, `resets in`, etc.)
- **Reset time extraction**: multiple formats (relative hours/minutes, absolute time)
- **Error screenshots**: saved to `logs/screenshots/` on failures for post-mortem debugging

### `src/agent_worker.py` — Task Processor
- **Single-threaded loop**: processes one task at a time (Claude.ai only allows one active conversation)
- **Exponential backoff**: doubles delay up to 120s on network/browser errors
- **SIGTERM handler**: sets shutdown flag — in-progress task completes gracefully before exit
- **Result files**: saved as Markdown with YAML frontmatter to `results/task_NNN.md`

### `src/dashboard.py` — Web Interface (FastAPI)
- **Authentication**: `fastapi.security.HTTPBasic` with `secrets.compare_digest` for timing-safe comparison
- **Lifespan context**: `init_db()` called once at startup via `@asynccontextmanager`
- **Jinja2 templates**: `TemplateResponse(request, "dashboard.html", context)` with custom filters
- **REST endpoints**: `/api/tasks`, `/api/stats`, `/api/status` polled by JS every 8–15s
- **Download**: `/results/{task_id}` streams the Markdown file as an attachment

### `src/auth.py` — Authentication
- `bcrypt` password hashing for `ADMIN_PASSWORD` (stored in `.env`)
- `HTTPBasic` dependency injected into all routes
- Timing-safe comparison prevents username enumeration

---

## Data Flow

### Task Submission

```
User fills form ──POST /tasks──► Dashboard validates
                                      │
                                 database.add_task()
                                      │
                              tasks table: status='queued'
                                      │
                         Agent worker polls get_next_task()
                                      │
                              status ← 'running'
                                      │
                           claude_adapter.send_prompt()
                                      │
                              status ← 'completed'
                              result_path saved
                                      │
                         Dashboard polls /api/tasks every 8s
                                      │
                         User sees status update + Download button
```

### Rate Limit Handling

```
Claude.ai returns limit message
          │
  _is_limit_message() → True
          │
  _extract_reset_time() → datetime
          │
  database.set_claude_unavailable(reset_time)
          │
  Worker: wait_for_limit_reset()
          │ polls every 60s
          │ checks if now > reset_time
          │
  database.set_claude_available()
          │
  Worker resumes normal loop
```

---

## Task State Machine

```
               ┌─────────┐
     Submit    │         │
  ────────────►│  queued │
               │         │
               └────┬────┘
                    │ worker picks up
               ┌────▼────┐
               │         │
               │ running │
               │         │
               └────┬────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
   ┌────▼────┐ ┌────▼──────┐ ┌─▼──────────────┐
   │completed│ │  failed   │ │ waiting_limit  │
   │         │ │  (retries │ │ (auto-resumes) │
   │         │ │exhausted) │ │                │
   └─────────┘ └───────────┘ └────────────────┘
```

---

## File Structure

```
/opt/claude-agent/
├── .env                    # Secrets (chmod 600)
├── requirements.txt
├── src/
│   ├── database.py         # SQLite data layer
│   ├── claude_adapter.py   # Playwright automation
│   ├── agent_worker.py     # Main worker loop
│   ├── dashboard.py        # FastAPI web app
│   └── auth.py             # HTTP Basic Auth
├── templates/
│   └── dashboard.html      # Jinja2 template
├── static/
│   ├── css/main.css        # Liquid Glass UI
│   └── js/dashboard.js     # Live polling / interactions
├── scripts/
│   ├── setup.sh            # Automated server setup
│   ├── deploy.sh           # Rolling update script
│   ├── backup.sh           # Database backup
│   ├── manual_login.py     # One-time Claude login helper
│   ├── morning_report.py   # Daily summary
│   └── health_check.py     # Health verification
├── config/
│   ├── Caddyfile           # Reverse proxy + HTTPS
│   ├── claude-agent.service
│   ├── claude-dashboard.service
│   └── duckdns-update.sh
├── data/
│   └── nightcrawler.db     # SQLite database
├── results/                # Claude response Markdown files
├── backups/                # Database snapshots
├── browser_data/           # Playwright persistent profile
└── logs/
    ├── worker.log
    ├── dashboard.log
    ├── duckdns.log
    └── screenshots/        # Error screenshots from Playwright
```
