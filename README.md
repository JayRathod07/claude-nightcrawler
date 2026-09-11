# 🕷️ Claude Nightcrawler — Overnight Automation Agent

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.40-45ba4b?style=for-the-badge&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-279%20passing-brightgreen?style=for-the-badge&logo=pytest&logoColor=white)
![Caddy](https://img.shields.io/badge/Caddy-2.x-1F88C0?style=for-the-badge&logo=caddy&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

**A self-hosted, zero-cost automation agent that runs 24/7 on Oracle Cloud Free Tier.**  
Submit tasks from your phone, wake up to completed results — saved as Markdown and delivered via Telegram.

</div>

---

## 🧠 What is Claude Nightcrawler?

Claude Nightcrawler is an intelligent task-queue system that interacts with **Claude.ai on your behalf** — overnight, unattended, completely free.

It solves one specific problem: Claude.ai's free-tier usage limits reset every few hours. Instead of babysitting the browser, you submit all your prompts into a queue, and the agent handles everything — including automatically detecting rate limits, waiting for them to reset, and resuming where it left off.

```
You go to sleep → Agent runs all night → You wake up to completed Markdown results
```

---

## ✨ Features

| Feature | Description |
|---|---|
| 🌐 **Web Dashboard** | Mobile-friendly UI to submit & monitor tasks from anywhere |
| 🔁 **Smart Retry** | Detects usage limits, waits for reset, resumes automatically |
| 💾 **Crash Recovery** | Stale-task detection restores 'running' tasks on every startup |
| 🔒 **Secure HTTPS** | Caddy auto-SSL via Let's Encrypt + DuckDNS subdomain |
| 📱 **Telegram Alerts** | Push notifications on completion, rate limits, or errors |
| 📝 **Markdown Output** | Structured `.md` files with YAML frontmatter for every task |
| 🆓 **Zero Cost** | Designed for Oracle Cloud Ampere A1 Always Free tier |
| 🧵 **Thread-safe DB** | SQLite with WAL mode, ACID compliance, connection pooling |
| 📊 **Priority Queue** | Assign priority to tasks; FIFO tie-breaking |
| 🛡️ **Circuit Breaker** | Auto-pauses on 5 consecutive failures to avoid spinning |
| 🌅 **Morning Report** | Daily Telegram summary of overnight activity |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                 YOU (Phone / Laptop / Tablet)                 │
└────────────────────────┬─────────────────────────────────────┘
                         │  HTTPS (port 443)
              ┌──────────▼──────────┐
              │   DuckDNS + Caddy   │  ← Auto SSL/TLS
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────────┐
              │   FastAPI Dashboard     │  ← HTTP Basic Auth
              │   (src/dashboard.py)    │     Jinja2 Templates
              └──────────┬──────────────┘
                         │  read / write
              ┌──────────▼──────────────┐
              │   SQLite Database       │  ← tasks, claude_status,
              │   (src/database.py)     │     execution_log tables
              └──────────┬──────────────┘
                         │  poll every 10s
              ┌──────────▼──────────────┐
              │   Agent Worker          │  ← Infinite loop
              │   (src/agent_worker.py) │     SIGTERM-safe shutdown
              └──────────┬──────────────┘
                         │
              ┌──────────▼──────────────┐
              │   Claude Adapter        │  ← Playwright Chromium
              │   (src/claude_adapter.py│     Persistent profile
              └──────────┬──────────────┘
                         │  browser automation
              ┌──────────▼──────────────┐
              │     claude.ai           │
              └─────────────────────────┘
```

---

## 📁 Project Structure

```
claude-nightcrawler/
│
├── src/                        # Core Python source
│   ├── __init__.py
│   ├── database.py             # ✅ SQLite data layer — 3 tables, CRUD, priority queue
│   ├── claude_adapter.py       # ✅ Playwright browser automation — login, submit, extract
│   ├── agent_worker.py         # ✅ Infinite task loop — retry, backoff, limit-wait
│   ├── dashboard.py            # 🔜 FastAPI web dashboard (Phase 4)
│   ├── notifier.py             # ✅ Telegram push notifications
│   ├── auth.py                 # ✅ HTTP Basic Auth with bcrypt
│   ├── models.py               # ✅ Pydantic v2 schemas
│   ├── constants.py            # ✅ TaskStatus, EventType enums
│   └── utils.py                # ✅ Shared utilities
│
├── scripts/
│   ├── manual_login.py         # ✅ One-time Claude.ai login helper
│   ├── health_check.py         # ✅ System health verification
│   ├── morning_report.py       # ✅ Daily Telegram summary
│   └── setup.sh                # 🔜 Ubuntu server setup script (Phase 5)
│
├── templates/                  # 🔜 Jinja2 HTML templates (Phase 4)
├── static/                     # 🔜 CSS / JS assets (Phase 4)
│
├── config/
│   ├── claude-agent.service    # 🔜 systemd worker unit (Phase 5)
│   ├── claude-dashboard.service # 🔜 systemd dashboard unit (Phase 5)
│   └── Caddyfile               # 🔜 Caddy reverse-proxy config (Phase 5)
│
├── tests/
│   ├── test_database.py        # ✅ 45 database tests
│   ├── test_adapter.py         # ✅ 55 adapter tests (fully mocked)
│   └── test_worker.py          # ✅ 31 worker / notifier tests
│
├── results/                    # Task outputs saved here (git-ignored)
├── logs/                       # Rotating log files (git-ignored)
├── browser_data/               # Chromium profile / cookies (git-ignored)
├── docs/
│   ├── architecture.md
│   ├── setup-guide.md
│   └── troubleshooting.md
│
├── .env.example                # Environment variable template
├── requirements.txt            # Pinned Python dependencies
├── pytest.ini                  # Test configuration
└── README.md
```

---

## 🚀 Quick Start (Ubuntu 22.04)

```bash
# 1. Clone
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler

# 2. Install dependencies
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium && playwright install-deps

# 3. Configure
cp .env.example .env
nano .env   # set ADMIN_PASSWORD, DOMAIN_NAME, DUCKDNS_TOKEN

# 4. Initialise database
python -c "from src.database import init_db; init_db(); print('DB ready')"

# 5. Login to Claude.ai (one-time, requires display)
python scripts/manual_login.py

# 6. Start the worker
python src/agent_worker.py
```

See [docs/setup-guide.md](docs/setup-guide.md) for the full Ubuntu + systemd deployment.

---

## 🗄️ Database Schema

### `tasks` — Task queue

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment |
| `prompt` | TEXT | User's prompt (stripped, validated) |
| `status` | TEXT | `queued` → `running` → `completed` / `waiting_limit` / `failed` |
| `priority` | INTEGER | Higher = processed first |
| `created_at` | TIMESTAMP | Submission time |
| `started_at` | TIMESTAMP | Worker pickup time |
| `completed_at` | TIMESTAMP | Finish time |
| `result_path` | TEXT | Path to `.md` output file |
| `error_message` | TEXT | Last error detail |
| `retry_count` | INTEGER | Attempts made |
| `max_retries` | INTEGER | Retry limit (default 3) |
| `limit_reset_time` | TIMESTAMP | When to retry after a rate-limit |

### `claude_status` — Singleton rate-limit tracker

| Column | Type | Notes |
|--------|------|-------|
| `available` | BOOLEAN | Is Claude accepting requests? |
| `reset_time` | TIMESTAMP | When the limit lifts |
| `last_limit_message` | TEXT | Raw text of the limit message |
| `total_requests_today` | INTEGER | Daily counter |

### `execution_log` — Full audit trail

| Column | Type | Notes |
|--------|------|-------|
| `task_id` | INTEGER FK | Reference to `tasks.id` |
| `event_type` | TEXT | `started` / `completed` / `failed` / `retried` / `limit_hit` |
| `event_time` | TIMESTAMP | When it happened |
| `details` | TEXT | Human-readable description |

---

## ⚙️ Configuration (`.env`)

```dotenv
# Dashboard access
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-password

# Domain (DuckDNS)
DOMAIN_NAME=your-name.duckdns.org
DUCKDNS_TOKEN=your-token

# Worker behaviour
TASK_POLL_INTERVAL=10          # seconds between queue checks
MAX_RETRY_ATTEMPTS=3           # per task
RETRY_DELAY_SECONDS=5          # base for exponential backoff
DEFAULT_LIMIT_WAIT_HOURS=5     # fallback if reset time not detected

# Browser
PLAYWRIGHT_HEADLESS=true
BROWSER_TIMEOUT=30000          # ms
BROWSER_DATA_DIR=browser_data

# Telegram (optional)
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Logging
LOG_LEVEL=INFO
LOG_MAX_SIZE_MB=5
```

---

## 🧪 Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific phase
pytest tests/test_database.py -v   # Phase 1 — 45 tests
pytest tests/test_adapter.py -v    # Phase 2 — 55 tests
pytest tests/test_worker.py -v     # Phase 3 — 31 tests
```

**Current status: 131 tests, 0 failures.**

> All adapter tests are fully mocked — no real browser needed in CI.

---

## 📋 How the Worker Processes a Task

```
DB.get_next_task()
        │
        ▼
adapter.send_prompt(prompt)
        │
   ┌────┴────────────┐
   │                 │
   ▼                 ▼
status=ok         status=limit
   │                 │
save_result()    wait_for_limit_reset()
   │                 │
DB: completed    DB: waiting_limit → queued
   │
Telegram notify
```

On failure: `retry_count++` → exponential backoff → re-queue  
On `max_retries` exceeded: mark `failed` + Telegram alert  
On 5 consecutive failures: 60s circuit-breaker pause

---

## 🔔 Telegram Notifications

| Event | Message |
|-------|---------|
| Worker starts | 🤖 Claude Nightcrawler started |
| Task completed | ✅ Task N completed — preview of response |
| Rate limit hit | ⏸️ Limit detected — reset time countdown |
| Task failed | ❌ Task N error — error details |
| Worker stops | 🛑 Claude Nightcrawler stopped |

---

## 📊 Build Progress

| Phase | Component | Status | Tests |
|-------|-----------|--------|-------|
| 0 | Project scaffold, docs, CI templates | ✅ Complete | — |
| 1 | Database layer (`database.py`) | ✅ Complete | 45 ✅ |
| 2 | Claude adapter (`claude_adapter.py`) | ✅ Complete | 55 ✅ |
| 3 | Agent worker (`agent_worker.py`) | ✅ Complete | 31 ✅ |
| 4 | Web dashboard (`dashboard.py` + templates) | ✅ Complete | 35 ✅ |
| 5 | Infrastructure & Deployment (systemd + Caddy + setup.sh) | ✅ Complete | — |
| UI | Liquid Glass premium UI upgrade | ✅ Complete | — |
| 6 | Notifications & monitoring (Telegram, morning report, health) | ✅ Complete | 31 ✅ |
| 7 | Testing & QA (integration, load, auth/utils unit tests) | ✅ Complete | 68 ✅ |
| 8 | Documentation (8 comprehensive docs in `docs/`) | ✅ Complete | — |
| 9 | Oracle Cloud Deployment (bootstrap, verify, .env.example) | ✅ Complete | — |

**Total: 279 / 279 tests passing** 🟢 (265 core + 14 load)

---

## 🚀 Quick Deployment

### Option A — One-Command Bootstrap (Recommended for fresh Oracle Cloud VM)

```bash
# Clone the repo, then run the interactive bootstrap:
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler
sudo bash scripts/oracle_bootstrap.sh
```

The bootstrap script will:
1. Clear Oracle Cloud iptables restrictions (critical!)
2. Prompt for your domain, password, and DuckDNS token
3. Run the full setup (Python, Playwright, Caddy, systemd)
4. Verify the deployment automatically

### Option B — Manual Step-by-Step

```bash
# 1. Clone on your Oracle Cloud server
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler

# 2. Configure secrets
cp .env.example .env
nano .env   # set ADMIN_PASSWORD, DOMAIN_NAME, DUCKDNS_TOKEN

# 3. Run automated setup (installs Python, Playwright, Caddy, systemd)
chmod +x scripts/setup.sh
sudo ./scripts/setup.sh

# 4. Verify deployment
chmod +x scripts/verify_deployment.sh
./scripts/verify_deployment.sh

# 5. One-time Claude login (requires X11 forwarding: ssh -X ...)
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/manual_login.py

# 6. Access your dashboard
# https://your-agent.duckdns.org
```

---

## 🔧 Requirements

- Python 3.10+
- Playwright (Chromium)
- A display or VNC for the one-time login step
- Ubuntu 22.04 (for production) or any OS for development
- Oracle Cloud Free Tier account (recommended) — or any VPS
- DuckDNS subdomain (free) — for HTTPS in production
- Telegram Bot Token (optional)

---

## 📚 Documentation

| Doc | Description |
|-----|-------------|
| [Setup Guide](docs/setup-guide.md) | Step-by-step Oracle Cloud deployment |
| [Deployment Guide](docs/deployment.md) | Service management, updates, backups |
| [Architecture](docs/architecture.md) | Component diagram & data flow |
| [API Reference](docs/api-reference.md) | REST API endpoints & schemas |
| [Troubleshooting](docs/troubleshooting.md) | Common issues & fixes |
| [Contributing](docs/contributing.md) | Development setup & PR process |
| [Security](docs/security.md) | Security architecture & best practices |

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

Contributions welcome! See [docs/contributing.md](docs/contributing.md) for full guidelines.

Bug reports → [bug report template](.github/ISSUE_TEMPLATE/bug_report.md)  
Feature requests → [feature request template](.github/ISSUE_TEMPLATE/feature_request.md)

Please run `pytest tests/ -v` (all 166 tests must pass) before submitting a PR.
