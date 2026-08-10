# Claude Nightcrawler — Setup Guide

## Prerequisites

- Ubuntu 22.04 LTS (Oracle Cloud Ampere A1 or E2 Micro recommended)
- Python 3.10 or higher
- Git
- A DuckDNS account (free subdomain)
- A Telegram Bot Token (optional, for notifications)

## Step 1: Clone the Repository

```bash
git clone https://github.com/yourusername/claude-nightcrawler.git
cd claude-nightcrawler
```

## Step 2: Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
playwright install-deps
```

## Step 3: Configure Environment

```bash
cp .env.example .env
nano .env
```

Key settings to update:
- `ADMIN_PASSWORD` — set a strong password
- `DOMAIN_NAME` — your DuckDNS subdomain
- `DUCKDNS_TOKEN` — from your DuckDNS dashboard
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — optional

## Step 4: Initialize the Database

```bash
python -c "from src.database import init_db; init_db(); print('DB ready!')"
```

## Step 5: Log in to Claude.ai

```bash
python scripts/manual_login.py
```
This opens a visible browser window — log into your Claude.ai account.
The session is saved to `browser_data/` and reused on every run.

## Step 6: Run the Services

```bash
# Start the worker
python src/agent_worker.py &

# Start the dashboard
uvicorn src.dashboard:app --host 0.0.0.0 --port 8000
```

## Step 7: Set Up systemd (Production)

```bash
sudo cp config/claude-agent.service /etc/systemd/system/
sudo cp config/claude-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-agent claude-dashboard
sudo systemctl start claude-agent claude-dashboard
```

## Step 8: Set Up HTTPS with Caddy

Follow the [Deployment Guide](deployment.md) for Caddy and DuckDNS configuration.

## Verifying the Setup

```bash
# Check service health
python scripts/health_check.py

# Run tests
pytest tests/ -v

# View logs
journalctl -u claude-agent -f
```
