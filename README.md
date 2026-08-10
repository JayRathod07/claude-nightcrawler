# Claude Nightcrawler — Overnight Automation Agent

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.40-45ba4b?style=for-the-badge&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

**A self-hosted, zero-cost automation agent that runs 24/7 on Oracle Cloud Free Tier**

</div>

---

## 🧠 What is Claude Nightcrawler?

Claude Nightcrawler is an intelligent task automation system designed to interact with Claude.ai on your behalf — overnight, unattended, completely free. Submit tasks from your phone or laptop, and wake up to completed results in your inbox (via Telegram) or saved as Markdown files.

## ✨ Features

| Feature | Description |
|---|---|
| 🌐 **Web Dashboard** | Mobile-friendly UI to submit & monitor tasks |
| 🔁 **Auto Retry** | Detects usage limits, pauses, and resumes automatically |
| 💾 **Crash Recovery** | Systemd + stale task recovery on restart |
| 🔒 **Secure HTTPS** | Caddy auto-SSL via Let's Encrypt + DuckDNS |
| 📱 **Telegram Alerts** | Notifications on completion, limits, or errors |
| 📝 **Markdown Output** | Structured `.md` files with metadata for every task |
| 🆓 **Zero Cost** | Runs on Oracle Cloud Ampere A1 (always free) |
| 🧵 **Thread-safe DB** | SQLite WAL mode with ACID compliance |

## 🏗️ Architecture

```
[Phone/Laptop] → HTTPS → DuckDNS → Caddy → FastAPI Dashboard
                                                    ↕
                                              SQLite Database
                                                    ↕
                                          Agent Worker Process
                                                    ↕
                                         Playwright → Claude.ai
```

## 🚀 Quick Start

```bash
# 1. Clone repository
git clone https://github.com/yourusername/claude-nightcrawler.git
cd claude-nightcrawler

# 2. Run setup (on Ubuntu 22.04)
./scripts/setup.sh

# 3. Configure environment
cp .env.example .env
nano .env  # Set your passwords and tokens

# 4. Access dashboard
https://your-domain.duckdns.org
```

## 📁 Project Structure

```
claude-nightcrawler/
├── src/                    # Core Python source
│   ├── database.py         # SQLite layer (Phase 1)
│   ├── claude_adapter.py   # Playwright automation (Phase 2)
│   ├── dashboard.py        # FastAPI web app (Phase 3)
│   └── agent_worker.py     # Task queue worker (Phase 4)
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS/JS assets
├── config/                 # systemd & Caddy configs
├── scripts/                # Setup & deployment scripts
├── tests/                  # Pytest test suite
└── docs/                   # Full documentation
```

## 📚 Documentation

- [Setup Guide](docs/setup-guide.md)
- [Deployment Guide](docs/deployment.md)
- [Architecture](docs/architecture.md)
- [API Reference](docs/api-reference.md)
- [Troubleshooting](docs/troubleshooting.md)

## 🔧 Requirements

- Oracle Cloud Free Tier account (or any Ubuntu 22.04 server)
- DuckDNS subdomain (free)
- Python 3.10+
- Telegram Bot (optional, for notifications)

## 📊 Current Phase

- [x] **Phase 0** — Project initialization & GitHub setup
- [x] **Phase 1** — Database layer (SQLite)
- [ ] **Phase 2** — Claude adapter (Playwright)
- [ ] **Phase 3** — FastAPI web dashboard
- [ ] **Phase 4** — Agent worker & task queue
- [ ] **Phase 5** — Deployment & systemd services
- [ ] **Phase 6** — Notifications & monitoring

## 📄 License

MIT License — See [LICENSE](LICENSE) for details.

## 🤝 Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/contributing.md) for guidelines.
