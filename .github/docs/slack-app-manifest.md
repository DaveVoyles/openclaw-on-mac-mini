# Slack App Setup for OpenClaw

## Step 1: Create the app

Go to: https://api.slack.com/apps?new_app=1

Click **Create New App** → **From an app manifest** → pick your workspace → switch to the **JSON** tab → paste the manifest below.

## Step 2: The Manifest (JSON)

```json
{
  "display_information": {
    "name": "OpenClaw",
    "description": "Personal AI assistant — ask anything via @OpenClaw, DM, or /ask",
    "background_color": "#1a1a2e"
  },
  "features": {
    "bot_user": {
      "display_name": "OpenClaw",
      "always_online": true
    },
    "slash_commands": [
      {
        "command": "/ask",
        "description": "Ask OpenClaw anything. Add --model gemini|openai|anthropic|copilot to pick a model.",
        "usage_hint": "[--model <model>] your question",
        "should_escape": false
      },
      {
        "command": "/help",
        "description": "Show examples and tips for using OpenClaw.",
        "usage_hint": "(no arguments needed)",
        "should_escape": false
      }
    ]
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "app_mentions:read",
        "channels:history",
        "chat:write",
        "commands",
        "files:read",
        "im:history",
        "im:read",
        "im:write",
        "reactions:read",
        "reactions:write"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": [
        "app_mention",
        "file_shared",
        "message.im",
        "reaction_added"
      ]
    },
    "interactivity": {
      "is_enabled": false
    },
    "org_deploy_enabled": false,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}
```

## Step 3: Get your tokens

After creating the app:

1. **App-Level Token** (xapp-...):
   - Go to **Settings → Basic Information → App-Level Tokens**
   - Click **Generate Token and Scopes**
   - Name it anything (e.g. `openclaw-socket`)
   - Add scope: `connections:write`
   - Copy the `xapp-...` token

2. **Bot Token** (xoxb-...):
   - Go to **OAuth & Permissions → Install to Workspace**
   - Click **Install to Workspace** → Allow
   - Copy the **Bot User OAuth Token** (`xoxb-...`)

## Step 4: Add tokens to Mac Mini .env

SSH into Mac Mini and edit `/Users/davevoyles/openclaw/.env`:

```
SLACK_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

## Step 5: Deploy

From your MacBook:

```bash
make ship
```

## Step 6: Invite bot to a channel

In Slack, go to any channel and type:

```
/invite @OpenClaw
```

Then mention it with `@OpenClaw your question here` or send it a DM or use `/ask`.

## Features

| How to use | What it does |
|---|---|
| `@OpenClaw what is the weather?` | Answer in thread |
| `@OpenClaw --model gemini ...` | Force a specific model |
| Reply in a thread → `@OpenClaw follow up` | Carries full thread context |
| DM `what is the weather?` | Private answer |
| `/ask --model openai your question` | Slash command answer |
| `/help` | Show beginner-friendly examples and tips |
| Upload a file and @mention the bot | Bot reads and analyzes the file |
| Upload a file in a DM | Bot reads and analyzes the file |
| 👍 react to a bot response | Logs positive feedback |
| 👎 react to a bot response | Logs negative feedback |

### Supported `--model` values

| Flag | Routes to |
|---|---|
| `--model auto` | OpenClaw picks the best model (default) |
| `--model gemini` | Gemini 2.5 Flash |
| `--model openai` or `--model gpt` | OpenAI GPT-4o |
| `--model anthropic` or `--model claude` | Anthropic Claude |
| `--model copilot` | GitHub Copilot |

### Plain-language mode

Add `--simple` to any message or file prompt to get a plain, jargon-free response:

```
@OpenClaw explain this contract --simple
/ask summarize this meeting --simple
```

Useful for non-technical users or when you want a short, clear answer.

## Upgrading an existing app

If you already have the app installed, go to **Your Apps → OpenClaw → App Manifest** and update the JSON above (or add the missing scopes/events individually under **OAuth & Permissions** and **Event Subscriptions**). Then **reinstall** the app to your workspace so the new scopes take effect.
