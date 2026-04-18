# OpenClaw for Everyone — Getting Started

> **This guide is for you** — no technical knowledge needed.

OpenClaw is your personal AI assistant. Think of it like Gemini or ChatGPT, but it lives on your family's home server and is just for you.

You can use it two ways: from any browser, or through Slack.

---

## 🌐 Option 1: Use it from your browser (easiest)

1. Open any web browser (Chrome, Safari, Edge — anything works)
2. Go to: **[chat.davevoyles.synology.me](http://chat.davevoyles.synology.me)**
3. Start typing — it works just like Gemini or ChatGPT

That's it. You can bookmark that page and go back to it any time.

> **Tip:** On your first visit, you may see a login screen. If you need a username and password, ask Dave.

---

## 💬 Option 2: Use it from Slack

If you have Slack installed, you can chat with OpenClaw directly:

1. Open Slack
2. In the left sidebar, find **@OpenClaw** under Direct Messages
3. Type your question and press Enter

Or you can use the slash command from any channel:
```
/ask what does this word mean?
```

---

## 📄 Working with Word documents

1. In Slack, open a DM with @OpenClaw
2. Drag your Word file (`.docx` or `.doc`) into the chat window and drop it
3. OpenClaw will ask what you'd like to do — just reply with your answer

**Common things to say:**
- `proofread this for me`
- `make this sound more professional`
- `summarize this in a few bullet points`
- `fix the grammar`

---

## 📊 Working with Excel spreadsheets

1. Drag your Excel file (`.xlsx` or `.xls`) into Slack
2. OpenClaw will ask what you'd like — reply with your question

**Common things to say:**
- `what is this tracking?`
- `explain what column B means`
- `are there any errors or unusual numbers?`
- `write me a short summary of this`

---

## 💡 Tips for getting plain, easy-to-read answers

By default, OpenClaw gives detailed answers. If you'd prefer short, plain answers with no jargon:

**Turn on simple mode once and forget about it:**
```
/simple on
```

From then on, all your answers will be short and easy to read — no technical language.

To turn it off:
```
/simple off
```

---

## ✉️ Common things you can ask

You can ask OpenClaw anything, just like you'd ask a knowledgeable friend:

| What you want | What to type |
|---|---|
| Proofread an email | Paste the email text and say `proofread this` |
| Understand a letter | `explain this to me in plain English` |
| Write a thank-you note | `write a thank-you note to my doctor` |
| Explain a word or phrase | `what does "amortization" mean?` |
| Summarize a document | Upload the file and say `summarize this` |
| Find errors in a spreadsheet | Upload the file and say `find any errors` |

---

## ❓ Need help?

Type `/help` in Slack any time to see examples.

Or just ask: `what can you do?`

---

## 📁 Sync your Documents folder (Mac only)

Instead of dragging files into Slack every time, you can set up **automatic syncing**. Once installed, any Word or Excel file you drop into a special folder on your Mac will appear in OpenClaw within 30 seconds — no more uploads needed.

### How it works

1. You drop a file into **`~/Documents/OpenClaw/`** on your Mac
2. OpenClaw picks it up automatically (within ~30 seconds)
3. You open Slack, DM **@OpenClaw**, and just say what you want:
   - `edit my report.docx`
   - `proofread budget.xlsx`
   - `summarize the letter I just dropped in`

### One-time setup (ask Dave to help if needed)

Run this once in Terminal on your Mac:

```bash
bash ~/openclaw/scripts/install_watcher.sh
```

The installer will:
- Create the `~/Documents/OpenClaw/` folder for you
- Check that your Mac can reach the home server
- Set up automatic background syncing that starts at login

If you see a friendly message saying **"Your documents will now sync automatically!"** — you're all set.

> **Note:** This requires your Mac to have SSH access to the home server. If the installer shows an error about SSH, ask Dave — it's a one-time 2-minute fix.

### Supported file types

| Type | Extension | Max size |
|------|-----------|----------|
| Word documents | `.docx` | 50 MB |
| Excel spreadsheets | `.xlsx` | 50 MB |

### Troubleshooting

- **File didn't sync?** Check `~/Library/Logs/openclaw-watcher.log` (or ask Dave to look)
- **Want instant sync?** Install fswatch: open Terminal and type `brew install fswatch`
- **To stop syncing:** Ask Dave, or run `launchctl unload ~/Library/LaunchAgents/com.openclaw.watcher.plist`

---

*OpenClaw is running on the home Mac Mini. For technical questions, contact Dave.*
