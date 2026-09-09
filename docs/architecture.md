# Architecture — Claude Nightcrawler

Technical architecture, component design, data flow, and design decisions.

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture Diagram](#system-architecture-diagram)
3. [Component Reference](#component-reference)
4. [Data Flow](#data-flow)
5. [Task State Machine](#task-state-machine)
6. [Database Schema](#database-schema)
7. [Concurrency Model](#concurrency-model)
8. [Error Handling and Resilience](#error-handling-and-resilience)
9. [Security Architecture](#security-architecture)
10. [File Structure](#file-structure)
11. [Key Design Decisions](#key-design-decisions)

---

## Overview

Claude Nightcrawler is a **task queue system** that automates interactions with Claude.ai through browser automation (Playwright). It consists of:

- **Agent Worker** — a long-running process that picks tasks from a queue and sends them to Claude.ai via a controlled Chromium browser
- **Web Dashboard** — a FastAPI web application for submitting tasks, monitoring status, and downloading results
- **Reverse Proxy** — Caddy provides HTTPS termination, security headers, and automatic TLS certificate renewal
- **Database** — SQLite in WAL mode acts as the shared message bus between the worker and dashboard

The system is designed to run on a **single Oracle Cloud ARM instance** (free tier) with minimal resource usage.

---

## System Architecture Diagram

```
  [User: Browser on Phone / Laptop / Tablet]
               |
         HTTPS (port 443)
               |
  +------------------------+
  |    DuckDNS Service     | <-- cron every 5 min (duckdns-update.sh)
  |  my-agent.duckdns.org  |     keeps domain to Oracle Public IP in sync
  +------------------------+
               |
  +------------------------+
  |   Caddy Web Server     | <- Auto TLS (Let's Encrypt)
  |   Reverse Proxy        |    Security headers (HSTS, X-Frame-Options)
  |   port 443 to 8000     |    Compression
  +------------------------+
               |
  +----------------------------------+
  |   FastAPI Dashboard              |
  |   src/dashboard.py               |
  |   * HTTP Basic Auth (auth.py)    |
  |   * Jinja2 HTML templates        |
  |   * REST API (/api/*)            |
  |   * Static assets (Liquid Glass) |
  +----------------------------------+
               |
  +---------------------------+     +---------------------------+
  |   SQLite Database         |     |  Agent Worker Process     |
  |   data/nightcrawler.db    |<----|  src/agent_worker.py      |
  |                           |     |  * Infinite task loop     |
  |   tables:                 |     |  * Exponential backoff    |
  |   * tasks                 |---->|  * SIGTERM graceful stop  |
  |   * claude_status         |     |  * Crash recovery         |
  |   * task_events           |     |  * Result file write      |
  |                           |     |  * Telegram notifications |
  |   WAL mode:               |     +-------------+-------------+
  |   readers never block     |                   |
  +---------------------------+          +--------v-----------+
                                         | Playwright         |
                                         | claude_adapter.py  |
                                         | * Persistent Chrome|
                                         | * Cookie session   |
                                         | * Limit detection  |
                                         | * Error screenshots|
                                         +--------+-----------+
                                                  |
                                         +--------v-----------+
                                         |  claude.ai website |
                                         +--------------------+
```

---

## Component Reference

### `src/database.py` — Data Layer

The SQLite database is the **shared state bus** between all processes.

| Feature | Detail |
|---|---|
| **WAL mode** | Write-Ahead Logging enables concurrent reads during writes |
| **Thread-local connections** | Each thread gets its own connection via `threading.local()` |
| **Custom exceptions** | `DatabaseError`, `TaskNotFoundError`, `DatabaseConnectionError` |
| **Crash recovery** | `recover_stale_tasks()` resets `running` tasks to `queued` on startup |
| **Self-healing rate limit** | `is_claude_available()` auto-clears limit flag when `reset_time` has passed |

**Key functions**:

| Function | Purpose |
|---|---|
| `init_db()` | Create tables and WAL pragma if they do not exist |
| `add_task(prompt, priority)` | Insert a new task; returns its integer ID |
| `get_next_task()` | Atomically pick the highest-priority queued task (FIFO on tie) |
| `update_task_status(id, status)` | FSM transition; logs to `task_events` |
| `set_claude_available(available, reset_time, limit_message)` | Update the singleton rate-limit record |
| `is_claude_available()` | Check status; auto-heals if `reset_time` has passed |
| `recover_stale_tasks()` | Called on worker startup; returns count recovered |
| `get_statistics()` | Returns aggregate counts by status and avg completion time |
| `log_task_event(task_id, event_type, details)` | Append to audit log |

---

### `src/claude_adapter.py` — Browser Automation

Wraps Playwright to automate Claude.ai.

| Feature | Detail |
|---|---|
| **Persistent context** | Browser profile in `browser_data/` — cookies survive process restarts |
| **Headless Chromium** | Runs without display; X11 not needed in production |
| **Limit detection** | Multiple regex patterns match Claude rate-limit response messages |
| **Reset time extraction** | Handles relative ("resets in 3 hours") and absolute ("at 6:00 AM") formats |
| **Error screenshots** | Saved to `logs/screenshots/error_TIMESTAMP.png` on any exception |

**`send_prompt()` return values**:

```python
# Success
{"status": "ok", "text": "Claude response text..."}

# Rate limited
{"status": "limit", "reset_time": datetime(...), "message": "Usage limit text"}
```

---

### `src/agent_worker.py` — Task Processor

The main worker loop. Runs as a systemd service (`claude-agent.service`).

| Feature | Detail |
|---|---|
| **Single-threaded** | One task at a time — Claude.ai allows only one active conversation per session |
| **Poll interval** | Configurable via `POLL_INTERVAL` env var (default: 10s) |
| **Exponential backoff** | `backoff_delay(retry_count, base=5)` — doubles per retry, capped at 120s plus up to 10% jitter |
| **SIGTERM handler** | Sets `_shutdown` flag; current task completes gracefully before exit |
| **`save_result(task, text)`** | Writes `results/task_NNN.md` with YAML frontmatter |
| **`wait_for_limit_reset()`** | Polls `is_claude_available()` every 60s until limit clears |

---

### `src/dashboard.py` — Web Interface

FastAPI application with Jinja2 templates and a REST API.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | Yes | Dashboard HTML |
| `POST` | `/tasks` | Yes | Submit task via form |
| `GET` | `/api/tasks` | Yes | JSON task list |
| `GET` | `/api/stats` | Yes | JSON statistics |
| `GET` | `/api/status` | Yes | Claude availability status |
| `DELETE` | `/tasks/{id}` | Yes | Delete a task |
| `GET` | `/results/{id}` | Yes | Download result file |
| `GET` | `/health` | No | Public health probe |

---

### `src/auth.py` — Authentication

| Feature | Detail |
|---|---|
| **HTTPBasic** | FastAPI HTTPBasic with `secrets.compare_digest` — timing-safe |
| **bcrypt support** | If `ADMIN_PASSWORD` starts with `$2b$` or `$2a$`, verifies as bcrypt hash |
| **Plain text fallback** | Supports plain text passwords for development convenience |
| **401 response** | Returns `WWW-Authenticate: Basic` header for browser password prompts |

---

### `src/notifier.py` — Telegram Notifications

| Feature | Detail |
|---|---|
| **MarkdownV2** | Escapes special characters for Telegram MarkdownV2 parse mode |
| **Retry logic** | 3 attempts on network errors; no retry on 4xx client errors |
| **429 handling** | Reads `retry_after` header from Telegram API and waits accordingly |
| **Graceful degradation** | All notification failures are logged but never raise to caller |

---

### `src/utils.py` — Shared Utilities

| Function | Purpose |
|---|---|
| `format_duration(seconds)` | Human-readable: `"2h 15m"`, `"45s"` |
| `truncate(text, max_len)` | Truncate with ellipsis suffix |
| `sanitise_filename(text)` | Remove illegal filename characters |
| `ensure_dirs(*paths)` | mkdir -p for multiple directories |
| `now_iso()` | Current UTC time as ISO 8601 string |
| `file_size_human(path)` | Human-readable file size: `"48.0 KB"` |
| `mask_secret(secret)` | Show first 4 chars plus stars for safe logging |

---

## Data Flow

### Task Submission Flow

```
User fills form
    |
POST /tasks
    |
dashboard.py validates:
  * prompt not empty
  * priority is integer
    |
database.add_task(prompt, priority)
    |
tasks table: id=N, status=queued, created_at=NOW
    |
JS polls /api/tasks every 8s
  -> User sees "queued" badge
    |
agent_worker.py: get_next_task()
  -> picks highest priority, FIFO on tie
    |
database.update_task_status(N, running)
    |
claude_adapter.send_prompt(prompt)
  -> Playwright types into Claude.ai
  -> waits for response
    |
response: status=ok
    |
save_result(task, text)
  -> writes results/task_N.md
    |
database.update_task_status(N, completed, result_path=...)
    |
JS polls -> User sees completed + Download button
```

### Rate Limit Flow

```
claude_adapter.send_prompt() returns {status: limit, reset_time: T}
    |
database.set_claude_available(False, reset_time=T, limit_message=msg)
database.update_task_status(task_id, waiting_limit)
    |
agent_worker.wait_for_limit_reset(task_id, T, notifier)
    | polls every 60s
    | checks database.is_claude_available()
    |   -> auto-heals when now >= reset_time
    |
database.is_claude_available() returns True
    |
database.set_claude_available(True)
database.update_task_status(task_id, queued)  <- re-queued for retry
    |
Worker resumes normal loop
```

### Crash Recovery Flow

```
Process crashes (OOM, SIGKILL, server reboot)
    |
systemd restarts claude-agent.service
    |
agent_worker.py startup:
    |
database.recover_stale_tasks()
  -> UPDATE tasks SET status=queued, retry_count=retry_count+1
     WHERE status=running
  -> returns count of recovered tasks
    |
Worker continues normally -- no tasks lost
```

---

## Task State Machine

```
                submit
   ---------------------------------> queued
                                         |
                                  worker picks up
                                         |
                                       running
                                         |
         +---------------------------+---+-------------------------+
         |                           |                             |
    response OK               limit hit                    error / exception
         |                           |                             |
    completed               waiting_limit             retry_count < MAX_RETRIES
                                     |                             |
                              reset time passes               retry_count++
                                     |                    status <- queued
                                  queued <------------------------------
                                     |
                               retry_count >= MAX_RETRIES
                                     |
                                   failed
```

**Valid transitions**:

| From | To | Trigger |
|---|---|---|
| (new) | `queued` | Task submitted |
| `queued` | `running` | Worker picks up via `get_next_task` |
| `running` | `completed` | Successful Claude response |
| `running` | `waiting_limit` | Claude rate limit detected |
| `running` | `queued` | Error with retries remaining |
| `running` | `failed` | Error with max retries exhausted |
| `running` | `queued` | Crash recovery on startup |
| `waiting_limit` | `queued` | Rate limit reset |

---

## Database Schema

### `tasks` table

```sql
CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt        TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'queued',
    priority      INTEGER NOT NULL DEFAULT 0,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    result_path   TEXT,
    error_message TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    completed_at  TEXT
);

CREATE INDEX idx_tasks_status_priority
    ON tasks(status, priority DESC, created_at ASC);
```

### `claude_status` table

```sql
CREATE TABLE claude_status (
    id                   INTEGER PRIMARY KEY DEFAULT 1,
    available            INTEGER NOT NULL DEFAULT 1,
    reset_time           TEXT,
    last_check           TEXT,
    last_limit_message   TEXT,
    total_requests_today INTEGER NOT NULL DEFAULT 0,
    last_reset_date      TEXT
);
-- Singleton: always exactly one row (id=1)
```

### `task_events` table

```sql
CREATE TABLE task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id),
    event_type TEXT    NOT NULL,
    details    TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

---

## Concurrency Model

The system uses a **two-process, one-writer** model:

| Process | Role | DB Access |
|---|---|---|
| `claude-agent` (worker) | Writes task status, claude_status, events | Write |
| `claude-dashboard` (FastAPI) | Reads tasks and stats for display | Read-only |

**SQLite WAL mode** is critical:
- WAL allows multiple simultaneous readers while a writer is active
- The dashboard can serve requests without being blocked by worker writes
- `PRAGMA journal_mode=WAL` is set by `init_db()` at startup

**Thread safety**:
- FastAPI runs multiple uvicorn worker threads
- Each thread gets its own `sqlite3.Connection` via `threading.local()`
- `check_same_thread=False` is set since connections are isolated per thread

---

## Error Handling and Resilience

| Failure Mode | Response |
|---|---|
| Worker process crash | systemd restarts it; `recover_stale_tasks()` re-queues interrupted tasks |
| Claude login expiry | `LoginExpiredException` logged; worker stops; operator runs `manual_login.py` |
| Network error to Claude | `ClaudeAdapterError`; task retried with exponential backoff |
| Response extraction fail | `ResponseExtractionError`; error message saved; task retried |
| SQLite locked error | Built-in SQLite retry via WAL plus timeout |
| Telegram notification fail | Logged; never raises; notification silently skipped |
| Server reboot | All services have `Restart=on-failure` and `WantedBy=multi-user.target` |

---

## Security Architecture

| Layer | Mechanism |
|---|---|
| **Transport** | Caddy enforces HTTPS; HTTP redirected to HTTPS; HSTS header |
| **Authentication** | HTTP Basic Auth with `secrets.compare_digest` (timing-safe) |
| **Password storage** | bcrypt hash supported (never stores plaintext if bcrypt used) |
| **Dashboard binding** | FastAPI binds to `127.0.0.1:8000` — not exposed to internet |
| **Secrets** | `.env` file with `chmod 600`; never committed to git |
| **Service isolation** | `ProtectSystem=full`, `NoNewPrivileges=true`, `PrivateTmp=true` in systemd units |
| **Port exposure** | Only ports 22, 80, 443 open in OCI security list |
| **Claude credentials** | Never stored — browser cookie (session) only |

---

## File Structure

```
/opt/claude-agent/
+-- .env                         <- Secrets file (chmod 600, never in git)
+-- requirements.txt
+-- pytest.ini
+-- src/
|   +-- database.py              <- SQLite data layer
|   +-- claude_adapter.py        <- Playwright browser automation
|   +-- agent_worker.py          <- Main worker loop + process_task
|   +-- dashboard.py             <- FastAPI web application
|   +-- auth.py                  <- HTTP Basic Auth + bcrypt
|   +-- notifier.py              <- Telegram notifications
|   +-- utils.py                 <- Shared helper functions
+-- templates/
|   +-- dashboard.html           <- Jinja2 template (Liquid Glass UI)
+-- static/
|   +-- css/main.css             <- Liquid Glass / glassmorphism styles
|   +-- js/dashboard.js          <- Live polling + UI interactions
+-- scripts/
|   +-- setup.sh                 <- Automated server provisioning
|   +-- deploy.sh                <- Rolling update with backup
|   +-- backup.sh                <- Database backup with retention
|   +-- manual_login.py          <- One-time Claude.ai login helper
|   +-- morning_report.py        <- Daily summary (stdout / Telegram)
|   +-- health_check.py          <- System health verification
+-- config/
|   +-- Caddyfile                <- Reverse proxy + auto-TLS config
|   +-- claude-agent.service     <- systemd unit: worker process
|   +-- claude-dashboard.service <- systemd unit: FastAPI dashboard
|   +-- duckdns-update.sh        <- Dynamic DNS updater (cron)
+-- docs/                        <- Documentation (this folder)
+-- tests/                       <- Test suite (279 tests)
+-- data/
|   +-- nightcrawler.db          <- SQLite database (runtime)
+-- results/                     <- Claude response Markdown files
+-- backups/                     <- Database snapshots
+-- browser_data/                <- Playwright persistent Chromium profile
+-- logs/
    +-- worker.log
    +-- dashboard.log
    +-- duckdns.log
    +-- backup.log
    +-- screenshots/             <- Error screenshots from Playwright
```

---

## Key Design Decisions

### Why SQLite instead of PostgreSQL?

- **Zero ops**: no separate database process to manage, backup, or monitor
- **Single file**: trivially backed up with `cp`
- **WAL mode**: sufficient concurrency for this use case (1 writer + N readers)
- **Free tier friendly**: no RAM overhead of a database server

### Why Playwright instead of the Claude API?

- **Cost**: Claude API requires a paid plan; Claude.ai free tier is sufficient
- **Capability**: browser automation accesses the same Claude as the web UI, including artifacts
- **Persistence**: session cookies survive process restarts

### Why single-threaded worker?

- **Claude.ai limitation**: only one conversation can be active at a time per account
- **Simplicity**: no thread synchronisation needed in the adapter
- **Correct semantics**: tasks are FIFO/priority-ordered naturally

### Why Caddy instead of nginx?

- **Automatic TLS**: Caddy fetches and renews Let's Encrypt certificates automatically
- **Zero config HTTPS**: works out of the box with a single `reverse_proxy` directive
- **Security defaults**: HSTS, modern TLS, security headers enabled by default
