---
name: chainlink-worker
description: Run a Chainlink backlog worker that claims ready issues, dispatches them to Codex sessions, and loops through review until approval. Use when the user wants autonomous issue execution via chainlink + codex, needs to start or stop the worker, or needs help reviewing `ready-for-review` issues.
---

# Chainlink Backlog Worker

This skill turns `chainlink` issues into Codex work. It claims one issue at a time, routes it to the right repo, waits for Codex to finish, then keeps the same session alive through review rounds.

## Files

- `worker.py` — main orchestration loop
- `prompt_builder.py` — initial and review prompt builders
- `config.py` — TOML config loader
- `poller.py` — review notifier for Keel
- `pollers.json` — poller registration

## Start

From this skill directory, run:

```bash
uv run python worker.py
```

To use a non-default config file:

```bash
uv run python worker.py --config ~/.config/chainlink-worker/config.toml
```

The worker uses `~/.config/chainlink-worker/config.toml` by default.

## Stop

Send `SIGINT` or `SIGTERM` to the worker process. It exits cleanly after the current sleep or Codex poll interval.

## Monitor

- Watch the worker stdout for lifecycle events like `issue_claimed`, `prompt_complete`, and `review_approved`
- Check `chainlink session status --json` in `/Users/timkellogg/code/sandbox/goat-herder`
- Check `npx acpx codex sessions show issue-<id>` in the routed repo
- Check `chainlink show <id> --json` for labels, comments, and milestone context

## Review Workflow

1. Install the skill and reload pollers so `chainlink-review` starts firing.
2. When the poller reports an issue, inspect the repo changes and the Codex session.
3. If changes are needed, add a review comment in chainlink:

```bash
cd /Users/timkellogg/code/sandbox/goat-herder
~/.cargo/bin/chainlink issue comment <id> "Please add focused regression coverage for ..." --kind human
```

4. The worker removes `ready-for-review`, re-prompts the same Codex session, then restores `ready-for-review`.
5. Approve with an explicit approval comment. Recommended:

```bash
cd /Users/timkellogg/code/sandbox/goat-herder
~/.cargo/bin/chainlink issue comment <id> "APPROVED" --kind resolution
```

The worker closes the issue and then closes the Codex session on the next poll.

## Repo Routing

Configure repo routing in `[repos]` inside `config.toml`.

- Exact label match wins first, so label issues with repo names like `open-strix` or `vera-prism`
- If no repo label is present, the worker falls back to matching repo labels in the issue title, description, or milestone text
- If routing is ambiguous, the worker logs an error and skips the issue

## Poller Notes

- `pollers.json` uses the Open Strix scheduler contract: top-level object with a `pollers` array
- After copying or editing the skill, call `reload_pollers`
- The poller emits a single prompt listing every issue currently labeled `ready-for-review`
