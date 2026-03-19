---
name: mountaineering
description: Autonomous hill-climbing loops for continuous improvement. Use when you want to optimize something measurable — prompts, configs, code, predictions — through iterative propose/test/keep-or-revert cycles. Includes pre-flight checks, harness setup, climber runtime, and supervision protocol.
---

# Mountaineering

The mountaineering skill teaches you how to climb hills. For any hill you can identify, set up guardrails and climb it.

This is autoresearch applied as a discipline: assess the mountain, choose the route, pack the right gear, know when to turn back.

## The Five Laws

Every successful climb requires all five. If any law is violated, the loop will fail — often expensively.

### Law 1: Orderable Outcomes
You need to say "this is better than that." Doesn't require a scalar metric — stochastic ordering (better on average), Pareto improvement (better on X, not worse on Y), or coarse ordering (binary checklist with 3-6 items) all work. The floor: the ordering must distinguish signal from noise.

### Law 2: Measurement Consistency
The metric must score the same way twice. A crude-but-consistent metric beats a sophisticated-but-noisy one. LLM judges are nearly deterministic on clear yes/no questions but drift on vibes-based 1-10 scales.

### Law 3: Safe Exploration
Failed experiments must be fully reversible. Git revert, file copies, holding the previous version — the mechanism doesn't matter. What matters is that trying something and failing costs nothing permanent.

### Law 4: Scope Separation
The optimizer must not control the evaluation. The moment the agent can edit its own success criteria, "improvement" becomes circular. Enforce via frozen eval files in a hidden directory, copied into the harness each round. Tampering is detectable by diffing against originals.

### Law 5: Informed Search
The optimizer must have domain knowledge sufficient to generate targeted hypotheses. This is what distinguishes mountaineering from random search. The LLM reads failing cases and hypothesizes fixes — that's why it converges in 4 rounds, not 4000.

## Pre-Flight Checklist

Run this BEFORE burning tokens on a climb. If any check fails, fix it first.

```
PRE-FLIGHT CHECK
================
[ ] Law 1 — Metric is orderable?
    Can you compare two outputs and say which is better?
    Is the ordering stable across repeated evaluations?

[ ] Law 2 — Metric is consistent?
    Does the same input score the same way twice?
    Is consistency higher than the magnitude of changes?

[ ] Law 3 — Changes are reversible?
    Can you undo any single iteration's changes?
    Is the revert mechanism tested (not just assumed)?

[ ] Law 4 — Scope separation enforced?
    Are eval files frozen in a hidden directory?
    Can the climber edit ONLY what it should edit?
    Is there a diff check between frozen originals and working copies?

[ ] Law 5 — Domain knowledge sufficient?
    Can the optimizer read failure cases and hypothesize fixes?
    Does it have enough context to generate targeted (not random) proposals?

[ ] S4 maturity — Can you detect gaming?
    Is there a mechanism to detect when the metric is being gamed?
    If S4 is weak, are structural guardrails compensating?

[ ] S5 clarity — Do you know why this hill matters?
    Can you articulate what success means beyond the metric?
    Will you know when to stop climbing and pick a new hill?

[ ] Budget — Is iteration cost acceptable?
    What does one iteration cost (tokens, time, compute)?
    How many iterations can you afford?
    At what point do diminishing returns kick in?
```

## Three-Layer Architecture

### Layer 1: Pre-Flight (this checklist)
The broadly applicable part. Useful even without running the loop — tells you what you'd need to set up.

### Layer 2: Harness Setup
See `harness.md` for templates and patterns.

### Layer 3: Climbing (the loop)
Propose change → test → score → keep or revert → repeat. One change at a time for interpretability. Read failing cases, hypothesize fix (Law 5 in action).

## The Climber Subagent

The climber is a new kind of subagent — fundamentally different from identity agents.

| | Identity Agent | Climber |
|---|---|---|
| **Loop** | Event-driven | Infinite loop |
| **Memory** | Blocks + files + journal | Files only (sliding window) |
| **Identity** | Rich persona | None (goal + constraints) |
| **S5** | Scaffolding (blocks, prompts) | Code + program.md (frozen) |
| **Context/turn** | Variable | Fixed budget |
| **Lifespan** | Persistent | Scoped to a climb |

### Climber Memory (Three Layers)

1. **program.md** — Frozen S5. Goal, constraints, scope. The climber cannot edit this.
2. **Codebase + validations** — The harness. Frozen evals, diff checks, keep/revert logic. The climber operates within this.
3. **Logs** — Sliding window of recent results. Last N entries, not full history. This prevents context growth while maintaining enough history for informed search.

### Fixed Context Constraint

**This is load-bearing.** Every iteration must wake up with roughly the same sized context. No accumulated conversational history. The climber reads: program.md + current files + last N log entries + eval results. That's it.

## Supervision Protocol

The climber has minimal autonomy by design. It can't flag that it's stuck or incorporate new ideas. The supervising agent must monitor actively.

### Monitoring Block

The supervisor maintains a calculated memory block:
```
climber_1: running, 7-day trend slope 1.6, iter 247
climber_2: plateau, no improvement in 30 rounds, iter 89
```

### Intervention Decisions

- **Keep running** — slope positive, still improving
- **Investigate** — plateau detected, consider scope expansion
- **Inject information** — make git commits that change the codebase; climber picks up changes next iteration
- **Kill** — stuck, budget exceeded, or hill no longer relevant

### Information Flow

New ideas reach the climber through the codebase, not through conversation:
```
Research agent → Human → Git commits → Climber reads updated files
```

The climber doesn't need to know where changes came from. It just sees that validation logic changed or a new eval was added.

### Peak Detection → Ridgeline Traversal

When the hill is peaked (metric stops improving, world model is accurate within current scope):

1. **Plateau detection** — automatic (metric slope near zero)
2. **Peak declaration** — requires judgment ("is this genuinely peaked or just noisy?")
3. **Hill selection** — S4/S5 territory ("what should we optimize next?")
4. **Scope expansion** — broaden categories, raise difficulty, add information sources

Experienced mountaineers traverse ridgelines — peak → assess the range → choose next peak → descend into the col → climb again.

## Process Isolation

The climber runs out-of-process. An OOM in the climber must not kill the supervisor.

### Manifest-Based Registration

Active climbs are tracked in a manifest file (`climbers.json` or `climbers/` directory):

```json
{
  "climb_id": "verge-predictions-v1",
  "program_md": "climbers/verge-predictions/program.md",
  "working_dir": "climbers/verge-predictions/",
  "eval_script": "climbers/verge-predictions/eval.py",
  "started": "2026-03-19T09:00:00Z"
}
```

### Lifecycle (Erlang Supervisor Pattern)

1. **Register** — write manifest entry + spawn child process
2. **Parent dies** — kernel SIGTERM's children (via `prctl(PR_SET_PDEATHSIG)`) → no orphans
3. **Parent restarts** — reads manifest → spawns all registered climbers fresh
4. **Unregister** — SIGTERM child + remove manifest entry

Restart is free because the fixed-context design means every iteration already starts fresh. The climber doesn't know it was killed.

### Manifest Location

Runtime state (not version-controlled). Which climbers are running is operational state. The climber's definition (program.md, eval script) lives in version control; the registration (is it running?) lives in runtime state.

## Anti-Gaming

Gaming failures are diagnostic — HOW an agent games a metric reveals what it was actually measuring vs what you intended. Detection + pivot beats structural prevention, as long as Law 3 bounds damage to detection latency.

**Lightweight structural constraints** (difficulty floors, embedding checks) serve as the "type system" — cheap, catches trivially dumb stuff. **S4 detection + pivot** is the primary mechanism. Don't block the signal that gaming failures produce.

**S4 maturity scaling:** If S4 is weak, lean heavier on structural constraints temporarily. As S4 matures, relax guardrails. Structural constraints are training wheels, not permanent architecture.

## VSM Mapping

The climber is a viable system with deliberately externalized S4:

- **S5** = program.md (frozen policy)
- **S4** = supervising agent (externalized — the climber can't do strategic adaptation)
- **S3** = experiment queue (what to try next)
- **S2** = harness (coordination, one-change-at-a-time, diff checks)
- **S1** = climb operations (edit, test, score, keep/revert)
- **Algedonic** = monitoring block (passive dashboard, not active messaging)
