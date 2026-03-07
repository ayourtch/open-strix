# open-strix
[![PyPI version](https://img.shields.io/pypi/v/open-strix.svg)](https://pypi.org/project/open-strix/)

A persistent AI companion that lives in your Discord server, remembers everything, and gets better over time.

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
uv run open-strix
```

Three commands. You have an agent.

## What is this?

open-strix is an opinionated framework for building long-running AI agents. Not chatbots — *companions*. Agents that develop personality through conversation, maintain memory across sessions, schedule their own work, and learn from their mistakes.

It runs on cheap models (MiniMax M2.5, ~$0.01/message), talks to you over Discord, and stores everything in git. No vector databases, no cloud services, no enterprise pricing. Just files, memory blocks, and a git history you can actually read.

**How you interact with it:** You talk to it on Discord. It talks back using tools (`send_message`, `react`). It has scheduled jobs that fire even when you're not around. Over time, it develops interests, tracks your projects, and starts doing useful things without being asked.

## Why this exists

Most agent frameworks optimize for tool-calling pipelines or enterprise orchestration. open-strix optimizes for a different thing: **a single agent that knows you and gets better over time.**

Three design bets:

- **Focused.** It has a small core that's easily extended through skills, MCP servers, and an HTTP API for incoming events. 
- **Cheap.** Defaults to MiniMax M2.5 via the Anthropic-compatible API. Pennies per message. This is a personal tool, not an enterprise deployment. Run it on a $5/month VPS.
- **Stable.** This is the weird one. open-strix ships with built-in skills for self-diagnosis — prediction calibration loops, event introspection, onboarding that fades into regular operation. The agent can read its own logs, check whether its predictions were right, and notice when it's drifting. The design draws on cybernetics (specifically viable system theory): an agent that can't monitor and correct its own behavior will eventually degrade. So the correction loops are built in, not bolted on.

In other words, it's built to be sustainable.

## How it works

### The home repo

When you run `uvx open-strix setup`, it creates a directory — the agent's *home*. Everything the agent knows lives here:

```
blocks/          # YAML memory blocks — identity, goals, patterns. In every prompt.
state/           # Markdown files — projects, notes, research. Read on demand.
skills/          # Markdown skill files. Drop one in, agent picks it up.
logs/
  events.jsonl   # Every tool call, error, and event. The agent can read this.
  journal.jsonl  # Agent's own log — what happened, what it predicted.
scheduler.yaml   # Cron jobs the agent manages itself.
config.yaml      # Model, Discord config, prompt tuning.
```

Everything except logs is committed to git after every turn. The git history *is* the audit trail. You can `git log` to see exactly what your agent did and when.

### Memory

Two layers:

- **Blocks** (`blocks/*.yaml`) — short text that appears in every prompt. Identity, communication style, current focus, relationships. The agent reads and writes these via tools.
- **Files** (`state/`) — longer content the agent reads when relevant. Research notes, project tracking, world context. Blocks point to files when depth is needed.

No embeddings, no vector search. Just files and git. The agent's memory is whatever you can `cat`.

### Skills

A skill is a markdown file in `skills/` with a YAML header. That's it. No SDK, no registration, no build step. The agent sees all skills in its prompt and invokes them by name.

```yaml
---
name: my-skill
description: What this skill does and when to use it.
---
# Instructions for the agent
...
```

open-strix also ships with built-in skills that teach the agent how to operate: memory management, prediction review, self-diagnosis, onboarding, and skill creation.

### Scheduling

The agent has tools to create, modify, and remove its own scheduled jobs. Jobs are cron expressions stored in `scheduler.yaml`. When a job fires, it sends a prompt to the agent — even if no human is around.

This is how agents develop autonomy: scheduled check-ins, maintenance routines, periodic scanning. The agent decides what to schedule based on what it learns about you.

### Events API

Every tool call, incoming message, error, and scheduler trigger is logged to `logs/events.jsonl`. The agent can read its own event log — and the introspection skill teaches it how. This is the self-diagnosis backbone: the agent has full visibility into what it did and what went wrong.

When `api_port` is set in `config.yaml`, a loopback REST API accepts events from external scripts — Bluesky pollers, CI hooks, cross-agent communication. See [docs/events.md](docs/events.md) for the full event schema, query cookbook, and REST API reference.

## Growing an agent

The code is the easy part. The real work is the conversations.

A new agent starts with an `init` memory block pointing it to the onboarding skill. From there, it's supposed to have real conversations with you — not fill out forms. It learns your schedule, your projects, your communication preferences by talking to you. Over days, it drafts identity blocks, sets up scheduled jobs, and starts operating autonomously.

This takes time. Plan on a week of active conversation before the agent feels like it knows you. Plan on two weeks before it's doing useful things unprompted.

See [GROWING.md](GROWING.md) for the full guide on what this process looks like and what to expect.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and a Discord bot token.

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
# Edit .env with your API key and Discord token
uv run open-strix
```

The setup command handles everything: directory structure, git init, GitHub repo creation (with `--github`), service files for your OS, and a walkthrough for model/Discord configuration.

See [SETUP.md](SETUP.md) for detailed instructions on environment variables, model configuration, Discord setup, and deployment options.

## Configuration

`config.yaml`:

```yaml
model: MiniMax-M2.5
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
```

Models use the Anthropic-compatible API format. MiniMax M2.5 and Kimi K2.5 both work out of the box. Any model with an Anthropic-compatible endpoint will work — set `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` in `.env`.

## Tests

```bash
uv run pytest -q
```

## Safety

Agent file writes are limited to `state/` and `skills/`. Reads use repository scope. Built-in skills are read-only. This is intentionally simple and should not be treated as a security boundary.

There is no sandboxing. Agents have full shell access. See [docs/sandboxing.md](docs/sandboxing.md) for why this is deliberate and what open-strix does instead.

## License

MIT. See `LICENSE`.
