# Sandboxing

open-strix does not sandbox agent execution. Agents have full shell access via `bash` (or `powershell` on Windows) with no containerization, chroot, or process isolation.

This is a deliberate design choice, not an oversight.

## Why no sandboxing

**The threat model doesn't justify the weight.** In practice — across multiple agents running for months with full bash access — sandboxing has never caught or would have caught a single problem. The actual failure modes of long-running agents are social (saying the wrong thing), not technical (running `rm -rf /`).

**Sandboxing adds real costs:**

- **Breaks tooling.** Agents need to install packages, run scripts, access the network, read/write files across the home repo. Sandboxes make all of this harder. Every sandbox escape hatch you add erodes the security boundary anyway.
- **Complexity budget.** open-strix is designed to run on a $5/month VPS. Adding Docker, gVisor, or nsjail to the dependency chain is a significant operational burden for a personal agent.
- **False sense of security.** A sandboxed agent that can send Discord messages, make API calls, and commit to git can still cause plenty of damage. The sandbox protects the host OS, not the things you actually care about (your Discord server, your API keys, your git history).

## What open-strix does instead

**File write restrictions.** Agent file writes are limited to `state/` and `skills/`. This isn't a security boundary — it's a guardrail to keep the agent from accidentally overwriting its own code. The agent can still write anywhere via bash.

**Git audit trail.** Everything except logs is committed to git after every turn. If an agent does something wrong, you can see exactly what changed and revert it. This is more useful than sandboxing because it catches the failures that actually happen (bad file edits, wrong memory updates) rather than theoretical ones (arbitrary code execution).

**Event logging.** Every tool call, including every shell command, is logged to `events.jsonl`. You can audit what your agent did after the fact.

## When you might want sandboxing

If you're running an open-strix agent with access to production systems, shared infrastructure, or sensitive data beyond what's in the home repo, you should add your own isolation layer. Run the agent in a VM, container, or separate user account. open-strix won't fight you on this — it doesn't assume anything about its execution environment.

But for the intended use case — a personal companion agent on a dedicated VPS — sandboxing is solving the wrong problem.
