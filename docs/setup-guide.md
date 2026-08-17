# Setup Guide — Claude Nightcrawler

Complete step-by-step instructions for deploying Claude Nightcrawler on Oracle Cloud Free Tier.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Oracle Cloud account | [oracle.com/cloud/free](https://oracle.com/cloud/free) — always-free tier |
| DuckDNS account | [duckdns.org](https://www.duckdns.org) — free dynamic DNS |
| Local machine | Any OS with SSH client |
| Claude.ai account | Free account; credentials saved via browser session |

---

## Part 1 — Oracle Cloud Instance

### 1.1 Create a Free Tier Instance

1. Sign in to [cloud.oracle.com](https://cloud.oracle.com)
2. **Compute → Instances → Create Instance**
3. Choose configuration:
   - **Image**: Ubuntu 22.04 LTS (Canonical)
   - **Shape**: VM.Standard.A1.Flex (Ampere, Always Free) — 1 OCPU, 6 GB RAM  
     *or* VM.Standard.E2.1.Micro (AMD, Always Free) — 1 OCPU, 1 GB RAM
   - **Boot volume**: 50 GB (Always Free limit)
4. **Networking**: Create or select a VCN with public subnet
5. **SSH keys**: Upload your public key (`~/.ssh/id_rsa.pub`)
6. Click **Create** — wait 2–5 minutes for provisioning
7. Note the **Public IP Address**

### 1.2 Open Firewall Ports

In **Networking → Virtual Cloud Networks → Security Lists → Default Security List**, add Ingress Rules:

| Protocol | Port | Description |
|---|---|---|
| TCP | 22 | SSH |
| TCP | 80 | HTTP (Caddy redirect) |
| TCP | 443 | HTTPS |

> **Note**: Do NOT open port 8000 — FastAPI binds to localhost only.

### 1.3 SSH into the Instance

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@YOUR_PUBLIC_IP
```

---

## Part 2 — DuckDNS Setup

1. Go to [duckdns.org](https://www.duckdns.org) and sign in
2. Enter a subdomain name, e.g. `my-agent` → full domain: `my-agent.duckdns.org`
3. Enter your Oracle instance Public IP, click **Add Domain**
4. Copy your **DuckDNS token** (shown at the top of the page)

---

## Part 3 — Server Setup

### 3.1 Clone the Repository

```bash
sudo apt install git -y
git clone https://github.com/JayRathod07/claude-nightcrawler.git
cd claude-nightcrawler
```

### 3.2 Configure Environment

```bash
cp .env.example .env
nano .env
```

Required values:

```bash
ADMIN_USERNAME="admin"
ADMIN_PASSWORD="your-very-secure-password"
DOMAIN_NAME="my-agent.duckdns.org"
DUCKDNS_DOMAIN="my-agent"
DUCKDNS_TOKEN="your-duckdns-token"
```

```bash
chmod 600 .env
```

### 3.3 Run Setup Script

```bash
chmod +x scripts/setup.sh
sudo ./scripts/setup.sh
```

---

## Part 4 — Manual Claude Login

```bash
# With X11 forwarding
ssh -X ubuntu@YOUR_PUBLIC_IP
source /opt/claude-agent/venv/bin/activate
python /opt/claude-agent/scripts/manual_login.py
```

---

## Part 5 — Verify

```bash
sudo systemctl status claude-agent claude-dashboard caddy
curl http://127.0.0.1:8000/health
```

Then open: **`https://my-agent.duckdns.org`**

---

## Troubleshooting

See [troubleshooting.md](troubleshooting.md) for common issues.
