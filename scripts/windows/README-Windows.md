# OpenClaw for Windows — Dad's Setup Guide

Hi Dad! This guide gets your Windows PC talking to OpenClaw so your Word and Excel files sync automatically — no more uploading every time.

---

## What You'll Need

- Windows 10 or Windows 11
- Your PC connected to the same home network as the Mac Mini **OR** the Mac Mini reachable by name ("macmini")
- About 10 minutes the first time

---

## Step 1 — Allow PowerShell Scripts to Run

Open **PowerShell** (search "PowerShell" in the Start menu) and paste this, then press Enter:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Type `Y` and press Enter when prompted. This is a one-time setting.

---

## Step 2 — Copy the OpenClaw Scripts to Your PC

Ask Dave to share the `scripts\windows\` folder with you (USB drive or network share). Copy the whole folder somewhere easy to find, for example:

```
C:\Users\YourName\Documents\OpenClaw-Scripts\
```

---

## Step 3 — Run the Installer

In the same PowerShell window, navigate to the folder:

```powershell
cd "C:\Users\YourName\Documents\OpenClaw-Scripts"
```

Then run:

```powershell
.\Install-OpenClaw.ps1
```

The installer will ask you two questions:

1. **Mac Mini hostname** — just press Enter to accept the default (`macmini`), or type the IP address Dave gave you (e.g. `192.168.1.93`).
2. **Slack bot token** — leave blank and press Enter (only needed if you don't have Wi-Fi access to the Mac Mini).

The installer creates your sync folder and registers a background task that starts every time you log in.

---

## Step 4 — Set Up SSH Keys (if using Wi-Fi sync)

The fastest sync method uses SSH. To set it up:

1. In PowerShell, check if WSL is available:
   ```powershell
   wsl --status
   ```
   If you see an error, skip to Step 4b.

2. Generate an SSH key inside WSL:
   ```bash
   ssh-keygen -t ed25519 -C "dad-pc"
   ```
   Press Enter three times to accept the defaults.

3. Copy your key to the Mac Mini (ask Dave for the Mac Mini's username):
   ```bash
   ssh-copy-id dave@macmini
   ```
   Enter Dave's Mac Mini password when prompted.

4. Test the connection:
   ```bash
   ssh macmini "echo connected"
   ```
   You should see `connected` — you're all set!

### Step 4b — No WSL? Use HTTP Upload Instead

If WSL isn't available, OpenClaw automatically falls back to sending files over HTTP to the Mac Mini. No extra setup needed — the installer configures this automatically.

---

## Step 5 — Drop a File to Test

Open **File Explorer** and navigate to:

```
Documents\OpenClaw\
```

Drop a `.docx` or `.xlsx` file in there. Within a few seconds you should see it sync. Check the log to confirm:

```
%APPDATA%\OpenClaw\openclaw-watcher.log
```

You should see a line like:
```
[2026-04-18 09:15:32] [INFO] ✅ Synced via WSL: MyDocument.docx
```

---

## How It Works

- A background task starts every time you log in — you don't have to do anything.
- Any `.docx` or `.xlsx` file you drop in `Documents\OpenClaw\` is automatically sent to the Mac Mini.
- Files over 50 MB are skipped (too large — compress or split them).
- The watcher tries three methods in order:
  1. **rsync via WSL** (fastest — uses SSH)
  2. **HTTP upload** to the Mac Mini (no WSL needed)
  3. Logs an error if both fail (check your network connection)

---

## Uninstalling

If you ever want to remove OpenClaw, run:

```powershell
.\Uninstall-OpenClaw.ps1
```

This removes the background task and clears the saved settings.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "cannot be loaded because running scripts is disabled" | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` in PowerShell |
| Files aren't syncing | Check `%APPDATA%\OpenClaw\openclaw-watcher.log` for errors |
| "Cannot reach macmini via SSH" | Make sure you're on the home Wi-Fi; see Step 4 for SSH key setup |
| Installer says WSL not found | That's OK — HTTP upload fallback is used automatically |
| File syncs but OpenClaw doesn't see it | Ask Dave to check `/ai-files/` on the Mac Mini |

---

Questions? Just message Dave or send a message in Slack!
