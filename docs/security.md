# Security — Claude Nightcrawler

Security architecture, hardening measures, best practices, and responsible disclosure.

---

## Table of Contents

1. [Security Model Overview](#security-model-overview)
2. [Authentication](#authentication)
3. [Network Security](#network-security)
4. [systemd Service Hardening](#systemd-service-hardening)
5. [Data Protection](#data-protection)
6. [Claude Session Security](#claude-session-security)
7. [Secret Management](#secret-management)
8. [Audit and Logging](#audit-and-logging)
9. [Security Checklist](#security-checklist)
10. [Known Limitations](#known-limitations)
11. [Responsible Disclosure](#responsible-disclosure)

---

## Security Model Overview

Claude Nightcrawler is a **single-user, self-hosted application**. The security model is:

| Layer | Mechanism |
|---|---|
| Transport | Caddy enforces HTTPS; HTTP is redirected; HSTS is enforced |
| Authentication | HTTP Basic Auth on all endpoints; timing-safe comparison |
| Network | Dashboard bound to `127.0.0.1` only; not accessible from internet |
| Secrets | `.env` file with `chmod 600`; never logged or committed |
| Process | systemd hardening: read-only filesystem, no privilege escalation |
| Claude credentials | Never stored — browser session cookie only |

---

## Authentication

### HTTP Basic Authentication

All dashboard and API endpoints (except `/health`) are protected with HTTP Basic Auth.

- Credentials are checked using `secrets.compare_digest()` — **timing-safe**, immune to timing attacks that could enumerate valid usernames
- The `WWW-Authenticate: Basic` header is returned on 401 responses, triggering browser password dialogs
- Credentials are transmitted base64-encoded — **HTTPS is mandatory** (enforced by Caddy)

### Password Storage

**Option 1 — bcrypt hash (recommended)**:

The `ADMIN_PASSWORD` in `.env` can be a bcrypt hash. The auth module detects the `$2b$` or `$2a$` prefix and uses `bcrypt.checkpw()` for verification.

Generate a hash:

```bash
python3 -c "
import bcrypt
plain = b'your-password-here'
hashed = bcrypt.hashpw(plain, bcrypt.gensalt(rounds=12))
print(hashed.decode())
"
```

Set in `.env`:
```
ADMIN_PASSWORD="$2b$12$..."
```

When using a bcrypt hash, you enter your **original plain password** in the browser — the comparison happens server-side.

**Option 2 — Plain text (development only)**:

Plain text passwords work but are not recommended for production. Use bcrypt for any internet-accessible deployment.

### Password Recommendations

```bash
# Generate a strong random password
openssl rand -base64 32

# Or using Python
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Requirements:
- Minimum 16 characters
- Use a password manager to generate and store it
- Never reuse passwords from other services

### Changing the Password

1. Generate a new password (and optionally a bcrypt hash)
2. Update `ADMIN_PASSWORD` in `.env`
3. Restart dashboard: `sudo systemctl restart claude-dashboard`
4. Update your password manager entry

---

## Network Security

### Port Exposure

The following ports are open in Oracle Cloud Security Lists and UFW:

| Port | Exposed | Protocol | Purpose |
|---|---|---|---|
| 22 | Yes | TCP | SSH — restrict source IP if possible |
| 80 | Yes | TCP | HTTP — Caddy redirects to HTTPS immediately |
| 443 | Yes | TCP | HTTPS — all user traffic |
| 8000 | **No** | TCP | FastAPI — bound to `127.0.0.1`, blocked by firewall |

> Port 8000 must **never** be exposed. The FastAPI dashboard has no rate limiting or IP blocking — it relies on Caddy for those protections.

### Caddy Security Headers

The `Caddyfile` sets these response headers:

| Header | Value | Purpose |
|---|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Force HTTPS for 1 year |
| `X-Frame-Options` | `DENY` | Prevent clickjacking |
| `X-Content-Type-Options` | `nosniff` | Prevent MIME-type sniffing |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limit referrer leakage |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Restrict browser APIs |

### SSH Hardening

Disable password-based SSH authentication:

```bash
sudo nano /etc/ssh/sshd_config
```

Set:
```
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
MaxAuthTries 3
```

Then restart:

```bash
sudo systemctl restart sshd
```

> **Warning**: Ensure your SSH key is working before disabling password auth. Test in a second terminal before closing the first.

### Firewall Rules

Verify iptables rules are restrictive:

```bash
sudo iptables -L INPUT -n --line-numbers
```

Expected: rules allowing ports 22, 80, 443 and ESTABLISHED/RELATED, DROP all else.

If Oracle Cloud's default iptables rules are too open:

```bash
sudo iptables -F
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo netfilter-persistent save
```

---

## systemd Service Hardening

Both `claude-agent.service` and `claude-dashboard.service` include:

```ini
[Service]
# Prevent privilege escalation (execve, setuid, etc.)
NoNewPrivileges=true

# Mount /usr, /boot, /etc as read-only
ProtectSystem=full

# Isolated /tmp namespace (prevents /tmp race conditions)
PrivateTmp=true

# Restrict visible network interfaces (dashboard only)
# PrivateNetwork=true  (not used — needs network access)

# Explicit write access list
ReadWritePaths=/opt/claude-agent /tmp

# Memory limit (prevents OOM from taking down the system)
MemoryMax=1.5G

# CPU limit (prevents CPU monopolisation)
CPUQuota=80%

# Restart on crash
Restart=on-failure
RestartSec=10
```

**Verify hardening is active**:

```bash
sudo systemctl cat claude-agent | grep -E "NoNew|Protect|Private|Memory|CPU"
sudo systemd-analyze security claude-agent
```

---

## Data Protection

### Task Data (Database)

The SQLite database (`data/nightcrawler.db`) contains:
- Task prompts and responses
- Task status and timing
- Claude usage statistics

**It does NOT contain**:
- Passwords (only the bcrypt hash in `.env`, not in DB)
- API keys
- Claude account credentials

**Protect the database**:

```bash
chmod 640 /opt/claude-agent/data/nightcrawler.db
```

### Result Files

Claude responses are stored as Markdown files in `results/`. These may contain sensitive information depending on what you asked Claude.

- The `/results/{id}` endpoint requires authentication
- Consider encrypting the `results/` directory if handling confidential prompts:

```bash
# Encrypt with age (recommended)
age -r $(cat ~/.ssh/id_ed25519.pub | ssh-to-age) results/task_42.md > results/task_42.md.age
```

### Backups

Database backups in `backups/` contain all task data. If this data is sensitive:

1. Encrypt backups before storage
2. Store off-server (S3, rclone to cloud storage)
3. Delete local backups after transfer

```bash
# Example: encrypt and upload to S3
gpg --symmetric --cipher-algo AES256 /opt/claude-agent/backups/nightcrawler_$(date +%Y%m%d)*.db
aws s3 cp /opt/claude-agent/backups/*.db.gpg s3://your-backup-bucket/
```

---

## Claude Session Security

### What Is Stored

Playwright saves the Claude.ai browser session in `browser_data/`. This directory contains:
- Authentication cookies (session token)
- Browser local storage
- Cache data

**This is equivalent to being logged in as you on Claude.ai.**

### Protecting the Session

```bash
chmod 700 /opt/claude-agent/browser_data
```

The `browser_data/` directory is:
- Listed in `.gitignore` — never committed to git
- Not included in application backups by default
- Should never be copied to untrusted systems

### Session Expiry

Claude.ai sessions expire after a period (typically days to weeks). When this happens:
1. The worker logs `LoginExpiredException`
2. No tasks are processed until the session is renewed
3. Run `manual_login.py` to create a new session

This is intentional — a long-lived session with no expiry would be a security risk.

---

## Secret Management

### The `.env` File

All secrets are stored in `/opt/claude-agent/.env`:

```bash
# Permissions (must be set)
chmod 600 /opt/claude-agent/.env
chown ubuntu:ubuntu /opt/claude-agent/.env

# Verify
ls -la /opt/claude-agent/.env
# Expected: -rw------- 1 ubuntu ubuntu ...
```

**Rules**:
1. Never commit `.env` to git (it is in `.gitignore`)
2. Never log the contents of `.env`
3. Use `mask_secret()` from `src/utils.py` when logging secrets for debugging

### Verifying `.gitignore` Protects Secrets

```bash
git check-ignore -v .env browser_data/ data/ results/
# Each line should show that it IS ignored
```

### Rotating Secrets

**ADMIN_PASSWORD**:

```bash
nano /opt/claude-agent/.env  # change ADMIN_PASSWORD
sudo systemctl restart claude-dashboard
```

**DUCKDNS_TOKEN**:

```bash
# Regenerate on duckdns.org, then:
nano /opt/claude-agent/.env  # update DUCKDNS_TOKEN
```

**TELEGRAM_BOT_TOKEN**:

```bash
# Revoke old token with @BotFather: /revoke
# Create new token: /newbot or get from existing
nano /opt/claude-agent/.env  # update TELEGRAM_BOT_TOKEN
```

---

## Audit and Logging

### task_events Table

Every status transition is logged to the `task_events` table:

```sql
SELECT task_id, event_type, details, created_at
FROM task_events
ORDER BY id DESC
LIMIT 20;
```

This provides an audit trail of what the system did, when, and to which tasks.

### Worker Logs

All worker activity is logged to:
- `logs/worker.log` (rotating, max 10 MB, 5 backups)
- `journalctl -u claude-agent` (systemd journal)

Sensitive data (like prompt content) is **not** logged in normal operation. Only task IDs and status transitions are logged at INFO level.

### Access Logs

Caddy can be configured to write access logs:

```
# In Caddyfile
log {
    output file /opt/claude-agent/logs/caddy-access.log
    format json
}
```

---

## Security Checklist

Run before going live:

```
Authentication:
[ ] ADMIN_PASSWORD is at least 16 characters or a bcrypt hash
[ ] Password is stored in a password manager
[ ] Default password "changeme" has been replaced

Files and Permissions:
[ ] .env has chmod 600 permissions
[ ] browser_data/ has chmod 700 permissions
[ ] .env is NOT in git: git check-ignore .env
[ ] browser_data/ is NOT in git: git check-ignore browser_data/

Network:
[ ] Port 8000 is NOT open in OCI security list
[ ] Port 8000 is NOT accessible from internet: curl http://YOUR_IP:8000 (should timeout)
[ ] Only ports 22, 80, 443 are open

SSH:
[ ] SSH password authentication is DISABLED
[ ] You have tested key-based SSH login before disabling password auth
[ ] SSH key has a passphrase (optional but recommended)

TLS:
[ ] HTTPS is working: curl -I https://your-agent.duckdns.org
[ ] Certificate is valid: sudo caddy certificates
[ ] HTTP redirects to HTTPS: curl -I http://your-agent.duckdns.org

Services:
[ ] ProtectSystem=full is in service units
[ ] NoNewPrivileges=true is in service units

Data:
[ ] Results directory reviewed for sensitive data
[ ] Backup strategy is in place (and tested)
[ ] Backups are off-server if data is sensitive
```

---

## Known Limitations

| Limitation | Detail | Mitigation |
|---|---|---|
| **Single user** | No multi-user support; all authenticated users share the same Claude session | Use a strong unique password; do not share credentials |
| **HTTP Basic Auth** | Credentials are base64-encoded in the `Authorization` header | HTTPS (enforced by Caddy) protects them in transit |
| **Plain session cookies** | `browser_data/` stores cookies in plaintext | Set `chmod 700`; never share or backup to untrusted locations |
| **No CSRF protection** | Form endpoints protected by Basic Auth only | HTTP Basic Auth prevents cross-site request forgery in practice |
| **No rate limiting on API** | API endpoints behind Caddy but no per-endpoint rate limiting | Consider adding `rate_limit` in Caddyfile if brute-force is a concern |
| **No IP allowlist** | Any IP can attempt login | Caddy can add IP restrictions if needed |

---

## Responsible Disclosure

If you discover a security vulnerability in Claude Nightcrawler, please **do not open a public GitHub Issue**.

**Instead**:
1. Open a GitHub Issue marked **[Security]** and request it be made private
2. Or contact the maintainer directly via GitHub

**Include in your report**:
- Description of the vulnerability
- Steps to reproduce
- Potential impact and affected versions
- Suggested fix (if known)

We aim to acknowledge reports within 48 hours and release a patch within 7 days for critical issues.
