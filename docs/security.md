# Security — Claude Nightcrawler

Security architecture, best practices, and responsible disclosure.

---

## Authentication

### HTTP Basic Auth
- All dashboard routes and API endpoints are protected with HTTP Basic Auth
- Credentials compared using `secrets.compare_digest()` — **timing-safe**, prevents timing attacks
- Passwords stored only in `.env` — never hardcoded or logged

### Password Recommendations
- Minimum 16 characters
- Use a password manager to generate a random string
- Example: `openssl rand -base64 32`

### Securing `.env`

```bash
chmod 600 /opt/claude-agent/.env
chown ubuntu:ubuntu /opt/claude-agent/.env
```

The `.env` file must never be committed to Git (it's in `.gitignore`).

---

## Network Security

### Port Exposure

| Port | Exposed | Notes |
|---|---|---|
| 22 | ✅ | SSH — restrict source IP if possible |
| 80 | ✅ | HTTP — Caddy redirects to HTTPS |
| 443 | ✅ | HTTPS — encrypted traffic only |
| 8000 | ❌ | FastAPI — localhost only, blocked by UFW |

### Caddy Security Headers

The `Caddyfile` sets these headers on every response:

```
Strict-Transport-Security  max-age=31536000; includeSubDomains; preload
X-Frame-Options            SAMEORIGIN
X-Content-Type-Options     nosniff
Referrer-Policy            strict-origin-when-cross-origin
Permissions-Policy         geolocation=(), microphone=(), camera=()
```

### SSH Hardening

```bash
# Disable password-based SSH (on server)
sudo nano /etc/ssh/sshd_config
# Set: PasswordAuthentication no
#      PubkeyAuthentication yes

sudo systemctl restart sshd
```

---

## systemd Hardening

Both service units include:

```ini
NoNewPrivileges=true      # process cannot gain new capabilities
ProtectSystem=full        # /usr, /boot, /etc are read-only
PrivateTmp=true           # isolated /tmp namespace (dashboard only)
ReadWritePaths=/opt/claude-agent /tmp   # explicit write whitelist
MemoryMax=1.5G            # prevent runaway memory usage
CPUQuota=80%              # prevent CPU monopolisation
```

---

## Data Security

### Browser Profile
The `browser_data/` directory contains your Claude.ai session cookies. It must be protected:

```bash
chmod 700 /opt/claude-agent/browser_data
```

This directory is excluded from Git. **Never share it publicly.**

### Database
- SQLite file contains task prompts and responses
- No passwords or API keys are stored in the database
- Backups are stored locally (`backups/`) — rotate off-server if needed

### Result Files
- Claude responses in `results/*.md` may contain sensitive information
- The download endpoint (`/results/{id}`) requires authentication
- Consider encrypting the `results/` directory if handling sensitive prompts

---

## Responsible Disclosure

If you discover a security vulnerability, please **do not open a public GitHub Issue**.

Instead, email: [open an issue marked 'Security' in private]

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if known)

We aim to respond within 48 hours and patch within 7 days.

---

## Known Limitations

- **Single-user**: no multi-user support; all authenticated users share the same agent session
- **HTTP Basic Auth**: credentials are base64-encoded in the `Authorization` header — HTTPS (enforced by Caddy) is essential
- **Claude session**: browser cookies are stored in plaintext in `browser_data/` — physical server security matters
- **No CSRF protection**: the API is not session-based, so CSRF is not applicable, but the form submit endpoints are protected only by HTTP Basic Auth
