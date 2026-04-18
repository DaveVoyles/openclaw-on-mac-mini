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

## 🔍 Research for your documents

OpenClaw can search the web for information and pull it right into your document — without you leaving Slack.

### How to use it

Just type your research request in a DM to @OpenClaw:

```
research climate change for my annual report
```

or

```
look up tips for saving money on groceries
```

OpenClaw will:
1. Search the web using Perplexity AI and gather up-to-date facts
2. If you have an active document (see the `/files` tip below), it will suggest new paragraphs you can paste straight in
3. Post everything in Slack so you can copy what you need

**Tip:** Use `/files report.docx` to set an active document first, then type your research request — OpenClaw will tailor the findings to your document automatically.

**Keywords that trigger research:**
- `research [topic]`
- `look up [topic]`
- `find info on [topic]`
- `search for [topic]`

You can also use the slash command from anywhere in Slack:

```
/research electric vehicles for my sustainability report
```

---

## 📦 Process multiple files at once

You can drop two or more files into a Slack message and OpenClaw will handle them all — one after another — with a live progress report in the thread.

### How to use it

1. In a DM with @OpenClaw, drag **two or more** Word or Excel files into the chat at the same time and drop them
2. OpenClaw will start a thread showing progress for each file:
   - ⏳ 1/3: report.docx...
   - ✅ 1/3: report.docx done
   - ⏳ 2/3: budget.xlsx...
   - ✅ 2/3: budget.xlsx done
   - ...
3. When all files are done, you'll see: **✅ All 3 files processed!**

### Batch with a slash command

If you've already uploaded files, you can run a batch action on all of them at once:

```
/batch summarize
```

Other actions you can use with `/batch`:
- `/batch proofread` — proofread all files
- `/batch explain` — explain each file in plain language

> **Note:** Processing multiple large files takes a little longer — OpenClaw pauses briefly between each file to stay reliable.

---

## 🔍 Check if Everything is Working — `/status`

Not sure if OpenClaw is running correctly? Just type:

```
/status
```

You'll instantly see:
- ✅ **Mac Mini reachable** — the computer is on and connected
- 📁 **How many files** are in your OpenClaw folder
- 🕐 **When the last file synced** (so you know it's working)
- 🔔 **Whether file alerts are on** for you

This is the fastest way to know "is everything okay?" without bothering Dave.

---

## 🔔 Automatic File Alerts

If Dave has set up alerts for you, OpenClaw will **automatically message you** whenever a new file lands in your OpenClaw folder — no need to go to Slack and ask.

You'll get a message like:

> 📄 New file synced: **budget-2025.xlsx** — what would you like to do?

Then just tap a button: **Summarize it**, **Proofread it**, or **Explain it**.

If you're not getting alerts but want them, ask Dave to set `SLACK_NOTIFY_USER_ID` to your Slack member ID.

---

## 📊 Turn Excel Data into a Chart

When you upload an Excel or CSV file, OpenClaw will show a **📊 Chart** button alongside the usual Summarize/Proofread options.

Tap **📊 Chart** and OpenClaw will:
1. Read the numeric data in your spreadsheet
2. Generate a bar chart as a PNG image
3. Post the chart image right in the conversation

**Good for:** monthly budgets, expense tracking, any spreadsheet with numbers you want to visualize quickly.

---

## 🌍 Translate a Document

After uploading a Word or Excel file, tap the **🌍 Translate** button. OpenClaw will ask which language you'd like:

- Spanish 🇪🇸
- French 🇫🇷
- German 🇩🇪
- Italian 🇮🇹
- Portuguese 🇵🇹
- Japanese 🇯🇵
- Chinese 🇨🇳
- Arabic 🇸🇦

Pick a language and OpenClaw will translate the document and post the result in Slack.

---

## 🔀 Compare Two Documents

Need to see what changed between two drafts, or compare two reports side by side?

1. Upload **two files** in the same Slack message (or upload the first, then the second right after)
2. Tap the **🔀 Compare** button on the second file
3. OpenClaw will post a clear summary of what's different between them

**Good for:** comparing a rough draft to a final version, or checking two budget reports from different months.

> **Note:** OpenClaw will show a typing indicator and a brief progress update while it works — longer documents take a moment.

---

## 📁 See Your Recent Files — `/files recent`

Want to see what files you've uploaded lately? Type:

```
/files recent
```

OpenClaw will show your last 10 uploads — file name, type, and how long ago you uploaded it. Only you can see this list (it's sent as an ephemeral message).

This is handy when you want to reference a file you uploaded earlier without reuploading it.

---

## 🔄 Start Fresh — `/clear`

If things feel confused (OpenClaw keeps referencing an old file, or the conversation got muddled), just type:

```
/clear
```

This resets your active file selections and clears any in-progress comparison or translation. Your file history stays intact — `/clear` just gives you a clean slate for your next question.

---

## 💡 Smart Suggestions

If you ask a question that sounds like it's about a file you've uploaded before, OpenClaw will notice and remind you:

> 💡 You uploaded **budget-2025.xlsx** recently — want to work with that?

You can just reply "yes" or ask your question normally — it's a helpful nudge, not a requirement.

---

## 👋 Your Welcome Message

The first time you send a message to @OpenClaw (in a channel or as a DM), you'll automatically get a brief welcome message a moment later explaining the most useful things you can do. You only see this once.

---

## 💻 Windows: Sync Without Extra Software

If you're on Windows and can't use the SSH sync method, OpenClaw now supports uploading files directly over the network. The Windows installer (`Install-OpenClaw.ps1`) automatically tests this connection during setup.

If you see "✅ Upload server reachable" during install, the HTTP fallback is working and your files will sync even without additional tools installed.

