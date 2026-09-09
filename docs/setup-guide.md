# Setup Guide — Claude Nightcrawler

Complete step-by-step instructions for deploying Claude Nightcrawler from zero to a running production instance on Oracle Cloud Free Tier.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Oracle Cloud Instance](#1-oracle-cloud-instance)
3. [DuckDNS Dynamic DNS](#2-duckdns-dynamic-dns)
4. [Server Setup](#3-server-setup)
5. [Environment Configuration](#4-environment-configuration)
6. [Running the Setup Script](#5-running-the-setup-script)
7. [Manual Claude Login](#6-manual-claude-login)
8. [Verify Everything Works](#7-verify-everything-works)
9. [Automated Daily Reports](#8-automated-daily-reports)
10. [What to Expect After Setup](#9-what-to-expect-after-setup)

---

## Prerequisites

| Requirement | Details |
|---|---|
| Oracle Cloud account | [oracle.com/cloud/free](https://oracle.com/cloud/free) — always-free tier, no credit card charges |
| DuckDNS account | [duckdns.org](https://www.duckdns.org) — free dynamic DNS, sign in with Google/GitHub |
| Local machine | Any OS with SSH client installed |
| Claude.ai account | Free account at [claude.ai](https://claude.ai) — your credentials are **never stored** |

> **Important**: Claude Nightcrawler controls a real browser session. It does NOT use the Claude API and does NOT store your password — it saves a browser cookie (session file) in `browser_data/` just like a normal browser.

---

## 1. Oracle Cloud Instance

### 1.1 Create a Free Tier Compute Instance

1. Sign in at [cloud.oracle.com](https://cloud.oracle.com)
2. Navigate to **Compute → Instances → Create Instance**
3. Configure:

   | Setting | Value |
   |---|---|
   | **Name** | `claude-nightcrawler` (or any name) |
   | **Image** | Ubuntu 22.04 LTS (Canonical) |
   | **Shape** | VM.Standard.A1.Flex — **Always Free**, 1 OCPU, 6 GB RAM *(recommended)* |
   | **Shape (alt)** | VM.Standard.E2.1.Micro — **Always Free**, 1 OCPU, 1 GB RAM *(too little RAM for Playwright — avoid)* |
   | **Boot volume** | 50 GB (Always Free maximum) |
   | **Networking** | Create new VCN with public subnet, assign public IP |
   | **SSH keys** | Upload your `~/.ssh/id_rsa.pub` or generate a new key pair |

4. Click **Create** and wait 2–5 minutes for provisioning
5. Note the **Public IP Address** shown on the instance detail page

### 1.2 Open Firewall Ports (Security Lists)

Navigate to **Networking → Virtual Cloud Networks → your-VCN → Security Lists → Default Security List**

Add **Ingress Rules**:

| Source | Protocol | Port | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | 22 | SSH access |
| `0.0.0.0/0` | TCP | 80 | HTTP (Caddy redirects to HTTPS) |
| `0.0.0.0/0` | TCP | 443 | HTTPS (main access) |

> **Do NOT open port 8000** — FastAPI is bound to `127.0.0.1` only. All traffic must go through Caddy.

### 1.3 Configure Ubuntu Firewall (UFW)

Oracle Cloud adds `iptables` rules that block traffic even when OCI security lists allow it. After SSHing in, flush the restrictive rules:

```bash
sudo iptables -P INPUT ACCEPT
sudo iptables -F
sudo netfilter-persistent save
```

### 1.4 SSH into the Instance

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@YOUR_PUBLIC_IP
```

> **Windows**: Use Git Bash, WSL, or PowerShell's built-in SSH.

---

## 2. DuckDNS Dynamic DNS

DuckDNS gives your Oracle Cloud instance a stable `*.duckdns.org` domain name that Caddy uses for automatic TLS certificates.

1. Go to [duckdns.org](https://www.duckdns.org) and sign in with Google, GitHub, or Twitter
2. Enter a subdomain name (e.g., `my-claude-agent`) → full domain: `my-claude-agent.duckdns.org`
3. Enter your Oracle instance **Public IP** and click **Add Domain** / **Update IP**
4. Copy your **DuckDNS Token** from the top of the page — you will need it in `.env`

Verify DNS resolution from your local machine:

```bash
nslookup my-claude-agent.duckdns.org
# Should return your Oracle Public IP
```

---

## 3. Server Setup

### 3.1 Update System Packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git curl wget -y
```

### 3.2 Clone the Repository

```bash
cd /opt
sudo git clone https://github.com/JayRathod07/claude-nightcrawler.git
sudo chown -R ubuntu:ubuntu /opt/claude-nightcrawler
cd /opt/claude-nightcrawler
```

---

## 4. Environment Configuration

### 4.1 Create the `.env` File

```bash
cp .env.example .env
nano .env
```

Fill in **all** required values:

```bash
# Authentication
ADMIN_USERNAME="admin"
ADMIN_PASSWORD="your-very-secure-password-at-least-16-chars"

# Network
DOMAIN_NAME="my-claude-agent.duckdns.org"
DUCKDNS_DOMAIN="my-claude-agent"
DUCKDNS_TOKEN="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# Paths (defaults are fine for standard setup)
DB_PATH="/opt/claude-agent/data/nightcrawler.db"
RESULTS_DIR="/opt/claude-agent/results"
BROWSER_DATA_DIR="/opt/claude-agent/browser_data"
LOGS_DIR="/opt/claude-agent/logs"

# Telegram Notifications (optional — leave blank to disable)
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""

# Worker Tuning (optional)
POLL_INTERVAL="10"
BETWEEN_TASK_WAIT="3"
RETRY_BASE_DELAY="5"
MAX_RETRIES="3"
```

### 4.2 Secure the `.env` File

```bash
chmod 600 .env
```

### 4.3 Password Hashing (Recommended)

For extra security, store a bcrypt hash instead of plaintext:

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"
```

Copy the output (starts with `$2b$`) and use it as `ADMIN_PASSWORD` in `.env`.

---

## 5. Running the Setup Script

```bash
chmod +x scripts/setup.sh
sudo ./scripts/setup.sh
```

**What it does** (12 steps):

| Step | Action |
|---|---|
| 1 | Install Python 3.11, pip, venv, build tools |
| 2 | Install Playwright and Chromium browser |
| 3 | Create `/opt/claude-agent/` directory structure |
| 4 | Copy project files and set permissions |
| 5 | Create Python virtual environment |
| 6 | Install Python dependencies |
| 7 | Initialise SQLite database |
| 8 | Install and configure Caddy web server |
| 9 | Install systemd service units |
| 10 | Configure DuckDNS cron job (every 5 minutes) |
| 11 | Enable and start all services |
| 12 | Run initial health check |

**Expected duration**: 5–15 minutes. The script is idempotent — safe to re-run.

---

## 6. Manual Claude Login

Before the worker can process tasks, it needs an active Claude.ai browser session. This is a **one-time setup**.

### 6.1 With X11 Forwarding (Recommended)

From your **local machine**:

```bash
ssh -X ubuntu@YOUR_PUBLIC_IP
```

Then on the server:

```bash
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/manual_login.py
```

A Chromium window will open on your local screen. Log in to `claude.ai` normally. Close the browser when done — the session is saved to `browser_data/`.

### 6.2 Without X11 (Cookie Method)

1. Log in to `claude.ai` on your **local** browser
2. Open DevTools → Application → Cookies → `claude.ai`
3. Copy the `sessionKey` cookie value
4. Run on the server:

```bash
python /opt/claude-agent/scripts/manual_login.py --cookie "YOUR_SESSION_KEY"
```

### 6.3 Verify Login Worked

```bash
sudo systemctl restart claude-agent
sudo journalctl -u claude-agent -n 20
```

Look for:
```
INFO  Claude login verified ✓
INFO  Starting task processing loop
```

If you see `LoginExpiredException`, the session expired — repeat the login process.

---

## 7. Verify Everything Works

### 7.1 Check Service Status

```bash
sudo systemctl status claude-agent claude-dashboard caddy
```

All three should show **`active (running)`**.

### 7.2 Test the Health Endpoint

```bash
curl http://127.0.0.1:8000/health
# Expected: {"status": "ok", "timestamp": "..."}
```

### 7.3 Run the Health Check Script

```bash
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/health_check.py
```

### 7.4 Access the Dashboard

Open in your browser: `https://my-claude-agent.duckdns.org`

> If HTTPS is not working yet, wait 30–60 seconds for Caddy to obtain a Let's Encrypt certificate.

### 7.5 Submit a Test Task

Submit a simple prompt like `Say "hello world"`. It should appear as `queued`, change to `running` within ~15 seconds, then `completed` within 1–3 minutes.

---

## 8. Automated Daily Reports

```bash
crontab -e
```

Add:

```bash
# Daily morning report (8 AM server time)
0 8 * * * /opt/claude-agent/venv/bin/python /opt/claude-agent/scripts/morning_report.py

# Daily database backup (3 AM)
0 3 * * * /opt/claude-agent/scripts/backup.sh >> /opt/claude-agent/logs/backup.log 2>&1
```

---

## 9. What to Expect After Setup

| Event | Expected Behaviour |
|---|---|
| Task submitted | Worker picks it up within `POLL_INTERVAL` seconds |
| Claude processing | Takes 30 seconds to 5 minutes per task |
| Claude rate limit hit | Worker automatically waits until reset time, then resumes |
| Server reboot | All services start automatically; any interrupted tasks are re-queued |
| Claude login expires | Worker logs `LoginExpiredException`; run `manual_login.py` again |
| New code deployed | Run `./scripts/deploy.sh` for zero-downtime update |

---

## Troubleshooting

See [`troubleshooting.md`](troubleshooting.md) for common issues and solutions.

For architecture details, see [`architecture.md`](architecture.md).
