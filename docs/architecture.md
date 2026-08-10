# Architecture

## System Overview

Claude Nightcrawler is a multi-process Python application:

1. **Dashboard Process** — FastAPI web server (Uvicorn)
2. **Worker Process** — Infinite loop polling SQLite for tasks
3. **Caddy** — Reverse proxy with auto HTTPS

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SYSTEM ARCHITECTURE FLOW                         │
└─────────────────────────────────────────────────────────────────────┘

    [User Device: Phone/Laptop/Tablet]
                    │
              HTTPS (Port 443)
                    │
         ┌──────────▼──────────┐
         │   DuckDNS Service   │ ◄── Periodic IP update (cron)
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │   Caddy Web Server  │ ◄── Auto SSL/TLS (Let's Encrypt)
         │   Reverse Proxy     │
         └──────────┬──────────┘
                    │
              Port 8000 (localhost)
                    │
         ┌──────────▼──────────────┐
         │  FastAPI Dashboard      │
         │  • HTTP Basic Auth      │
         │  • Jinja2 Templates     │
         │  • REST API Endpoints   │
         └──────────┬──────────────┘
                    │
         ┌──────────▼──────────────┐
         │   SQLite Database       │
         │   • tasks table         │
         │   • claude_status       │
         │   • execution_log       │
         └─────┬──────────────▲────┘
               │              │
               │  Read/Write  │
         ┌─────▼──────────────┴────┐
         │  Agent Worker Process   │
         │  • Infinite task loop   │
         │  • State recovery       │
         │  • Error handling       │
         └──────────┬──────────────┘
                    │
         ┌──────────▼──────────────┐
         │  Playwright Controller  │
         │  • Persistent context   │
         │  • Session management   │
         └──────────┬──────────────┘
                    │
              Browser Profile (cookies persist)
                    │
         ┌──────────▼──────────────┐
         │    claude.ai Website    │
         └─────────────────────────┘
```

## Database Schema

### tasks
Stores all user-submitted prompts and their lifecycle state.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-incrementing task ID |
| prompt | TEXT | User's prompt text |
| status | TEXT | queued/running/completed/waiting_limit/failed |
| priority | INTEGER | Higher = processed sooner |
| created_at | TIMESTAMP | When task was submitted |
| started_at | TIMESTAMP | When worker began processing |
| completed_at | TIMESTAMP | When task finished |
| result_path | TEXT | Path to saved .md file |
| error_message | TEXT | Error details if failed |
| retry_count | INTEGER | How many times retried |
| max_retries | INTEGER | Retry limit |
| limit_reset_time | TIMESTAMP | When to retry after limit |
| metadata | TEXT | JSON for future extensibility |

### claude_status
Singleton row tracking Claude.ai rate limit state.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (=1) | Singleton key |
| available | BOOLEAN | Can accept requests? |
| reset_time | TIMESTAMP | When limit lifts |
| last_check | TIMESTAMP | Last status check |
| last_limit_message | TEXT | Raw limit message |
| total_requests_today | INTEGER | Daily counter |

### execution_log
Full audit trail of task lifecycle events.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Log entry ID |
| task_id | INTEGER FK | Reference to tasks.id |
| event_type | TEXT | started/completed/failed/retried/limit_hit |
| event_time | TIMESTAMP | When event occurred |
| details | TEXT | Human-readable details |
