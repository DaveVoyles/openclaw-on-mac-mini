# Slack App Setup for OpenClaw

## Step 1: Create the app

Go to: https://api.slack.com/apps?new_app=1

Click **Create New App** → **From an app manifest** → pick your workspace → switch to the **JSON** tab → paste the manifest below.

> **Note:** The manifest JSON is now generated from `scripts/update_slack_manifest.py` (the Python dict there is the single source of truth).
> Run `python3 scripts/update_slack_manifest.py --print` to get the latest JSON, or `make slack-manifest` to push it directly to Slack via the API.

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

The easiest path is `make slack-manifest` (requires `SLACK_APP_ID` and `SLACK_CONFIG_TOKEN` in `.env` — see `.env.example` for setup instructions).

For manual updates, run `python3 scripts/update_slack_manifest.py --print`, copy the JSON, then go to **Your Apps → OpenClaw → App Manifest** and paste it. Then **reinstall** the app to your workspace so any new scopes take effect.
