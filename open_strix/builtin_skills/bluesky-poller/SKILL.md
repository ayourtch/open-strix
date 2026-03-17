---
name: bluesky-poller
description: Monitor Bluesky notifications (replies, mentions, quotes) on a schedule. Ships with a ready-to-use poller that follows the pollers.json contract with follow-gate trust tiers. Use when setting up Bluesky monitoring for an agent.
---

# Bluesky Notification Poller

Monitors your Bluesky account for new replies, mentions, and quotes. Emits events to the agent only when there's something actionable.

## Setup

### 1. Set environment variables

The poller reads credentials from the agent's environment (`.env` file or system env):

| Variable | Required | Description |
|----------|----------|-------------|
| `BLUESKY_HANDLE` | yes | Your Bluesky handle (e.g., `agent.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | yes | App password from Bluesky Settings > App Passwords |

**Important:** Use a dedicated agent account, not a personal account. The poller doesn't mark notifications as read, but using a separate account keeps things clean.

### 2. Configure pollers.json

The skill ships with a default `pollers.json` that polls every 5 minutes:

```json
{
  "pollers": [
    {
      "name": "bluesky-mentions",
      "command": "python poller.py",
      "cron": "*/5 * * * *"
    }
  ]
}
```

Adjust the cron schedule as needed. For lower-traffic accounts, `*/15 * * * *` (every 15 minutes) is fine.

### 3. Reload pollers

After installation, call `reload_pollers` to register the poller with the scheduler.

## What It Reports

The poller emits events for:
- **Replies** to your posts
- **Mentions** of your handle
- **Quote posts** of your content

Each event includes the author, text, and URIs needed to respond.

Likes and follows are ignored — they don't require agent action.

## Trust Tiers (Follow-Gate)

Events from accounts you follow are emitted as normal prompts. Events from accounts you don't follow are prefixed with `[PERMISSION NEEDED]` — the agent should ask the operator before engaging.

The follow list is cached for 1 hour to avoid API rate limits.

## Dependencies

Requires the `atproto` Python package:

```bash
pip install atproto
```

Or with uv:

```bash
uv add atproto
```
