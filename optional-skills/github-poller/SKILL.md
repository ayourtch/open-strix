---
name: github-poller
description: Monitor GitHub repositories for new issues, PRs, comments, and reviews on a schedule. Ready-to-use poller following the pollers.json contract. Install from ClawHub or copy into your agent's skills/ folder.
---

# GitHub Repository Poller

Monitors GitHub repositories for new activity. Emits events to the agent only when there's something actionable.

## Installation

Install from [ClawHub](https://clawhub.ai):

```bash
npx clawhub install github-poller
```

Or copy this skill directory into your agent's `skills/` folder.

After installation, call `reload_pollers` to register the poller with the scheduler.

## Setup

### 1. Set environment variables

The poller reads credentials from the agent's environment (`.env` file or system env):

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | yes | GitHub personal access token or `gh` CLI token |
| `GITHUB_REPOS` | yes | Comma-separated list of repos to monitor (e.g., `owner/repo1,owner/repo2`) |

**Note:** The `gh` CLI auth token works if `gh` is installed and authenticated. The poller tries `gh auth token` as a fallback if `GITHUB_TOKEN` isn't set.

### 2. Configure pollers.json

The skill ships with a default `pollers.json` that polls every 15 minutes:

```json
{
  "pollers": [
    {
      "name": "github-activity",
      "command": "python poller.py",
      "cron": "*/15 * * * *"
    }
  ]
}
```

Adjust the cron schedule as needed. For active repos, `*/5 * * * *` works but watch API rate limits.

### 3. Reload pollers

After installation, call `reload_pollers` to register the poller with the scheduler.

## What It Reports

The poller emits events for:
- **New issues** opened on your repos
- **New pull requests** opened on your repos
- **New comments** on issues and PRs (excluding your own bot account)
- **PR reviews** submitted (excluding your own)

Each event includes the repo, author, title/body, and URLs needed to act.

## Filtering

- Comments and reviews from the authenticated account are excluded (avoids self-notification loops)
- Only activity since the last successful poll is reported
- Empty polls produce no output (silence = nothing new)

## Dependencies

Requires either:
- The `gh` CLI installed and authenticated, OR
- A `GITHUB_TOKEN` environment variable with repo read access

No additional Python packages needed — uses `subprocess` to call `gh` API.
