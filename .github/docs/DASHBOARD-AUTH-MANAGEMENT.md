# Dashboard Authentication Management

## Your Admin Credentials

**Save these in a secure location:**

```
Username: davevoyles
Password: ***REMOVED***
```

Use these credentials to log in at: https://openclaw.davevoyles.synology.me/login

---

## Agent-Accessible Reset Mechanism

If you forget your credentials, **any agent can immediately reset them** without requiring user intervention.

### Reset Script

Located at: `.github/scripts/reset-dashboard-auth.sh`

**Three Actions Available:**

#### 1. Check Current Status
```bash
.github/scripts/reset-dashboard-auth.sh status
```

Shows whether authentication is enabled/disabled and the current username (password hidden for security).

#### 2. Generate New Credentials
```bash
.github/scripts/reset-dashboard-auth.sh reset
```

Automatically generates and displays new secure credentials. Instructions:
- New credentials are written to `.env` immediately
- Restart the OpenClaw container to activate: `docker compose restart openclaw`
- Save the displayed password immediately (not stored elsewhere)

#### 3. Disable Authentication Entirely
```bash
.github/scripts/reset-dashboard-auth.sh disable
```

Removes all authentication requirements from the dashboard:
- Anyone can access https://openclaw.davevoyles.synology.me without login
- Useful if you want to re-enable auth later with different credentials
- Re-enable anytime by running `reset` action

---

## How It Works

The reset script modifies the `.env` file to update credentials:

1. **openclaw/.env** — Runtime config loaded by the container
2. **docker-stack/openclaw/.env** — Backup/reference copy

Both files stay in sync after updates.

### Security Notes

- Credentials in `.env` are **NOT committed to Git** (`.env` is in `.gitignore`)
- Session tokens are **HTTP-only cookies** (can't be stolen by JavaScript)
- Cookies use **HTTPS-only + SameSite=Strict** (CSRF protection)
- Authentication is **optional** — if credentials are blank, dashboard is public
- Each session lasts **24 hours** before automatic logout

---

## Agent Implementation Guide

When an agent needs to reset credentials:

### Scenario: "User forgot dashboard password"

```bash
# Step 1: Check current status
cd /Users/davevoyles/openclaw
.github/scripts/reset-dashboard-auth.sh status

# Step 2: Generate new credentials
.github/scripts/reset-dashboard-auth.sh reset

# Step 3: Restart container
cd /Users/davevoyles/docker-stack
docker compose restart openclaw

# Step 4: Verify (optional)
curl -s https://openclaw.davevoyles.synology.me/api/status | jq .
```

### Scenario: "Disable auth if user completely locked out"

```bash
cd /Users/davevoyles/openclaw

# Disable temporarily
.github/scripts/reset-dashboard-auth.sh disable

# Restart to apply
cd /Users/davevoyles/docker-stack && docker compose restart openclaw

# User can now access: https://openclaw.davevoyles.synology.me (no login)
# Reset new credentials when user is ready:
.github/scripts/reset-dashboard-auth.sh reset
```

---

## File Locations

| File | Purpose |
|------|---------|
| `/Users/davevoyles/openclaw/.github/scripts/reset-dashboard-auth.sh` | Reset script (executable) |
| `/Users/davevoyles/openclaw/.env` | Primary env config (loaded by container) |
| `/Users/davevoyles/docker-stack/openclaw/.env` | Backup env config |
| `src/config.py` | Config loader (reads env vars) |
| `src/discord_web.py` | Session auth middleware |
| `src/dashboard/routes.py` | Protected page routes |

---

## Troubleshooting

**Q: Container didn't restart with new credentials**
- A: Run `docker compose restart openclaw` in docker-stack directory
- Or: Full restart with `docker compose down && docker compose up -d`

**Q: Reset script says "No .env file found"**
- A: Ensure you're running from correct directory; script looks for `.env` one level up
- Fix: `cd /Users/davevoyles/openclaw && .github/scripts/reset-dashboard-auth.sh reset`

**Q: Reset script permissions error**
- A: Make script executable: `chmod +x .github/scripts/reset-dashboard-auth.sh`

**Q: Credentials work locally but not through reverse proxy**
- A: Check that HTTPS is enforced (secure flag requires HTTPS)
- Or temporarily disable secure flag in `src/discord_web.py` line ~500 for testing

---

## API Access (No Auth Required)

Dashboard authentication **only protects page routes** (HTML rendered pages).

The REST API endpoints remain accessible for automation:

```bash
# These work WITHOUT session auth:
curl https://openclaw.davevoyles.synology.me/api/status
curl https://openclaw.davevoyles.synology.me/api/runs

# These require BEARER token (unchanged):
curl -H "Authorization: Bearer oc_api_..." \
  https://openclaw.davevoyles.synology.me/api/action
```

---

## Next Steps (Optional Enhancements)

- [ ] Add rate limiting on login attempts (prevent brute force)
- [ ] Enable password hashing in .env (bcrypt instead of plaintext)
- [ ] Add multi-user support with role-based access
- [ ] Implement session revocation endpoint
- [ ] Add login attempt logging

These can be added anytime without changing the current implementation.
