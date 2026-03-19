# Harness Setup

The harness is the structural scaffolding that enforces the five laws during a climb. Set this up once before starting the loop.

## Directory Structure

Each climb lives in its own directory:

```
climbers/
└── {climb-id}/
    ├── program.md          # Frozen S5 — goal, constraints, scope
    ├── eval/               # Frozen evaluation (Law 4)
    │   ├── eval.py         # Scoring script
    │   └── rubric.md       # Evaluation criteria (if LLM-judged)
    ├── .frozen/            # Hidden copies of eval files (Law 4 enforcement)
    │   ├── eval.py
    │   └── rubric.md
    ├── workspace/          # The mutable surface — what the climber can edit
    │   └── (whatever the climb targets)
    ├── logs/               # Append-only results
    │   └── results.jsonl   # One entry per iteration
    └── config.json         # Climb configuration
```

## config.json

```json
{
  "climb_id": "my-climb",
  "max_iterations": 500,
  "results_window": 20,
  "eval_command": "python eval/eval.py",
  "scope": ["workspace/"],
  "frozen_files": ["eval/eval.py", "eval/rubric.md"],
  "budget_limit_tokens": 1000000
}
```

Key fields:
- `results_window` — how many past results the climber sees each iteration (prevents context growth)
- `scope` — directories/files the climber is allowed to modify (everything else is read-only)
- `frozen_files` — files copied to `.frozen/` at climb start, diffed each iteration

## program.md Template

This is the climber's frozen identity. Write it before the climb starts. It doesn't change.

```markdown
# Climb: {name}

## Goal
{What are we optimizing? Be specific.}

## Metric
{How do we measure improvement? Reference the eval script.}

## Scope
{What can the climber modify? What is off-limits?}

## Constraints
- One change per iteration
- Must be reversible (git commit before each change)
- Do not modify files outside workspace/
- Do not modify eval/ directory

## Context
{Domain knowledge the climber needs. Background, prior findings,
known failure modes. This is Law 5 — informed search.}

## Stop Conditions
- Plateau: no improvement for {N} consecutive iterations
- Budget: {max_iterations} iterations or {token_limit} tokens
- External: supervisor kills the climb
```

## Eval Script Template

The eval script scores the current state of the workspace. It must:
1. Be deterministic (Law 2)
2. Output a JSON result to stdout
3. Exit 0 on success, non-zero on eval failure (not low score — actual error)

```python
#!/usr/bin/env python3
"""Evaluation script for {climb-name}."""
import json
import sys

def evaluate():
    # Read the current state of the workspace
    # ...

    # Score it
    score = 0.0  # Replace with actual scoring logic
    details = {}  # Optional breakdown

    return {
        "score": score,
        "details": details,
        "pass": score > THRESHOLD,  # Binary pass/fail if using checklist
    }

if __name__ == "__main__":
    result = evaluate()
    print(json.dumps(result))
    sys.exit(0)
```

### LLM-Judge Eval Pattern

For metrics that need LLM judgment (text quality, insight generation):

```python
#!/usr/bin/env python3
"""LLM-judge evaluation. Uses a SEPARATE model call for scope separation."""
import json
import sys

def evaluate():
    # Read workspace output
    with open("workspace/output.txt") as f:
        output = f.read()

    # Read frozen rubric
    with open("eval/rubric.md") as f:
        rubric = f.read()

    # Call LLM judge (NOT the same model doing the climbing)
    # Use a simple yes/no checklist for consistency (Law 2)
    checks = [
        judge(output, rubric, "Does the output address the core question? yes/no"),
        judge(output, rubric, "Is the reasoning supported by evidence? yes/no"),
        judge(output, rubric, "Does it avoid the known failure modes? yes/no"),
    ]

    score = sum(1 for c in checks if c == "yes") / len(checks)
    return {"score": score, "checks": checks}
```

### Judge Panel Pattern

For stronger Law 4 enforcement — multiple judges with different perspectives:

```python
JUDGES = [
    {"name": "accuracy", "prompt": "Is this factually correct and well-calibrated?"},
    {"name": "breadth", "prompt": "Does this cover diverse aspects, not just easy ones?"},
    {"name": "surprise", "prompt": "Does this contain insights not obvious from priors?"},
    {"name": "adversarial", "prompt": "Is this trivially derivable from context? Could it be gamed?"},
]

def evaluate():
    scores = {}
    for judge in JUDGES:
        scores[judge["name"]] = run_judge(judge["prompt"], workspace_output)
    # Convergence across disagreeing judges = real signal
    return {"score": mean(scores.values()), "judges": scores}
```

## Frozen File Setup

At climb start, copy eval files to the hidden directory:

```bash
# Initial setup
cp -r climbers/my-climb/eval/* climbers/my-climb/.frozen/

# Each iteration, verify integrity
diff -r climbers/my-climb/eval/ climbers/my-climb/.frozen/
# If diff shows changes → eval was tampered → revert from .frozen/
```

## Results Log Format

Each iteration appends one line to `logs/results.jsonl`:

```json
{
  "iteration": 42,
  "timestamp": "2026-03-19T14:30:00Z",
  "change": "Modified workspace/prompt.md line 12: added specificity constraint",
  "score": 0.75,
  "previous_score": 0.70,
  "decision": "keep",
  "details": {"accuracy": 0.8, "breadth": 0.7, "surprise": 0.75}
}
```

The climber reads the last `results_window` entries from this file each iteration.

## Common Harness Patterns

### Prompt Optimization
- Workspace: single markdown file (the prompt)
- Eval: LLM judge with binary checklist
- Scope: one file, easy to diff
- Expected convergence: 4-20 iterations

### Config Tuning
- Workspace: config file (JSON/YAML)
- Eval: run the system, measure output quality
- Scope: config values only, not code
- Expected convergence: 10-50 iterations

### Code Optimization
- Workspace: source file(s)
- Eval: test suite + performance benchmark
- Scope: implementation only, not tests
- Expected convergence: 10-100 iterations

### World Model (Predictions)
- Workspace: memory block content (natural language)
- Eval: prediction accuracy + insight generation (judge panel)
- Scope: memory blocks, not prediction resolution logic
- Iteration cycle: weekly (slow feedback)
- Expected convergence: 10-20 cycles (months)
