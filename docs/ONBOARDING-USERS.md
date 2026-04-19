# Onboarding New Users
<!-- Updated: 2026-04-19 -->

Adding a new user to OpenClaw requires **no code changes**. Access is controlled entirely via Slack workspace membership and environment variables.

---

## Slack users (primary interface)

### Step 1 — Invite them to the Slack workspace

Send them the invite link:
```
https://join.slack.com/t/dvopenclaw/shared_invite/...
```

> ⚠️ The current invite link expires **May 17, 2026**. Generate a new one from Slack → Settings → Invitations before then.

### Step 2 — Share the getting-started guide

Send them to: **https://openclaw.davevoyles.synology.me/onboarding**

Or share the user guide directly: **https://openclaw.davevoyles.synology.me/parents-guide**

### Step 3 — That's it

Any workspace member can use all Slack commands (`/chat`, `/help`, `/digest`, `/simple`, etc.) immediately. No approval or configuration needed.

---

## Optional: Slack notification target

If you want someone to receive DM notifications when files are dropped into `/ai-files`:

1. Have them go to Slack → their profile → **More (⋯)** → **Copy member ID**  
   (Format: `UXXXXXXXXXX`)
2. Update `.env` on the Mac Mini:
   ```
   SLACK_NOTIFY_USER_ID=UXXXXXXXXXX
   ```
3. Restart the server: `make ship-server`

---

## Discord users (secondary interface)

Discord access is restricted by an allowlist. Adding a user requires one env var change.

### Step 1 — Get their Discord user ID

Ask the user to:
1. Open Discord → Settings → Advanced → enable **Developer Mode**
2. Right-click their own profile → **Copy User ID**  
   (Format: `123456789012345678`)

### Step 2 — Add them to ALLOWED_USER_IDS

On the Mac Mini, edit `.env`:
```
# Comma-separated — add their ID to the existing list
ALLOWED_USER_IDS=existing_id,new_user_id
```

### Step 3 — Restart the server

```bash
make ship-server
```

That's all. The role system (admin/member/viewer) is handled automatically — new users start as `member`.

---

## Summary

| Interface | Access control | What to do |
|-----------|----------------|------------|
| Slack | Workspace membership | Send invite link |
| Slack notifications | `SLACK_NOTIFY_USER_ID` in `.env` | Add their member ID, restart |
| Discord | `ALLOWED_USER_IDS` in `.env` | Add their user ID, restart |

**No code changes. No deployments beyond `make ship-server` if env vars change.**
