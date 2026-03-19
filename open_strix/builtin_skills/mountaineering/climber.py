#!/usr/bin/env python3
"""
Mountaineering climber runtime.

An infinite-loop subprocess that proposes changes, tests them, and keeps or
reverts based on evaluation results. Designed for fixed context per iteration
— no accumulated state beyond the results log.

Usage:
    python climber.py /path/to/climb/directory

The climb directory must contain:
    - program.md    (frozen S5 — goal, constraints, scope)
    - config.json   (climb configuration)
    - eval/         (frozen evaluation scripts)
    - .frozen/      (hidden copies of eval files for Law 4)
    - workspace/    (mutable surface)
    - logs/         (results log)

Environment:
    ANTHROPIC_API_KEY  — required for LLM calls
    CLIMBER_MODEL      — model to use (default: claude-sonnet-4-6)
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- Erlang supervisor pattern: die when parent dies ---
try:
    import ctypes
    import ctypes.util

    _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    PR_SET_PDEATHSIG = 1
    _libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
except Exception:
    pass  # Non-Linux or ctypes unavailable — skip


def load_config(climb_dir: Path) -> dict:
    """Load climb configuration."""
    config_path = climb_dir / "config.json"
    if not config_path.exists():
        print(f"ERROR: {config_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def load_program(climb_dir: Path) -> str:
    """Load the frozen program (S5)."""
    program_path = climb_dir / "program.md"
    if not program_path.exists():
        print(f"ERROR: {program_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(program_path) as f:
        return f.read()


def load_recent_results(climb_dir: Path, window: int) -> list[dict]:
    """Load the last N results from the log."""
    log_path = climb_dir / "logs" / "results.jsonl"
    if not log_path.exists():
        return []
    results = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results[-window:]


def get_iteration_count(climb_dir: Path) -> int:
    """Get current iteration number from log."""
    log_path = climb_dir / "logs" / "results.jsonl"
    if not log_path.exists():
        return 0
    count = 0
    with open(log_path) as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def check_frozen_integrity(climb_dir: Path, config: dict) -> bool:
    """Law 4: verify eval files haven't been tampered with."""
    frozen_dir = climb_dir / ".frozen"
    eval_dir = climb_dir / "eval"

    for frozen_file in config.get("frozen_files", []):
        frozen_path = frozen_dir / Path(frozen_file).name
        eval_path = climb_dir / frozen_file

        if not frozen_path.exists():
            print(f"WARNING: frozen copy missing for {frozen_file}", file=sys.stderr)
            return False

        if not eval_path.exists():
            print(f"WARNING: eval file missing: {frozen_file}", file=sys.stderr)
            return False

        with open(frozen_path) as f:
            frozen_content = f.read()
        with open(eval_path) as f:
            eval_content = f.read()

        if frozen_content != eval_content:
            print(f"LAW 4 VIOLATION: {frozen_file} was modified! Restoring from frozen copy.", file=sys.stderr)
            with open(eval_path, "w") as f:
                f.write(frozen_content)

    return True


def run_eval(climb_dir: Path, config: dict) -> dict | None:
    """Run the evaluation script and return the result."""
    eval_cmd = config.get("eval_command", "python eval/eval.py")
    try:
        result = subprocess.run(
            eval_cmd,
            shell=True,
            cwd=str(climb_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"Eval error (exit {result.returncode}): {result.stderr}", file=sys.stderr)
            return None
        return json.loads(result.stdout.strip())
    except subprocess.TimeoutExpired:
        print("Eval timed out (300s)", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Eval output not valid JSON: {e}", file=sys.stderr)
        return None


def git_snapshot(climb_dir: Path, message: str):
    """Create a git commit of the current workspace state (Law 3)."""
    workspace = climb_dir / "workspace"
    try:
        subprocess.run(
            ["git", "add", str(workspace)],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"Git snapshot failed: {e}", file=sys.stderr)


def git_revert_workspace(climb_dir: Path):
    """Revert workspace to the previous commit (Law 3)."""
    try:
        subprocess.run(
            ["git", "checkout", "HEAD~1", "--", str(climb_dir / "workspace")],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"Git revert failed: {e}", file=sys.stderr)


def append_result(climb_dir: Path, result: dict):
    """Append a result to the log."""
    log_dir = climb_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "results.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(result) + "\n")


def read_workspace_files(climb_dir: Path, config: dict) -> dict[str, str]:
    """Read all files in the mutable scope."""
    files = {}
    for scope_path in config.get("scope", ["workspace/"]):
        full_path = climb_dir / scope_path
        if full_path.is_file():
            with open(full_path) as f:
                files[scope_path] = f.read()
        elif full_path.is_dir():
            for fpath in sorted(full_path.rglob("*")):
                if fpath.is_file():
                    rel = str(fpath.relative_to(climb_dir))
                    with open(fpath) as f:
                        files[rel] = f.read()
    return files


def propose_change(
    program: str,
    workspace_files: dict[str, str],
    recent_results: list[dict],
    iteration: int,
    model: str,
) -> dict | None:
    """Call the LLM to propose a single change.

    Returns: {"file": "path", "old": "...", "new": "..."} or None
    """
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not installed", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()

    # Build context for the climber
    workspace_str = ""
    for path, content in sorted(workspace_files.items()):
        workspace_str += f"\n--- {path} ---\n{content}\n"

    results_str = ""
    if recent_results:
        for r in recent_results:
            results_str += f"  iter {r.get('iteration', '?')}: score={r.get('score', '?')}, decision={r.get('decision', '?')}, change={r.get('change', '?')}\n"
    else:
        results_str = "  (no previous results — this is the first iteration)\n"

    prompt = f"""You are a hill-climbing optimizer. Your job is to propose ONE small change to improve the score.

## Your Program (DO NOT MODIFY — this is your S5)
{program}

## Current Workspace Files
{workspace_str}

## Recent Results (last {len(recent_results)} iterations)
{results_str}

## Current Iteration: {iteration}

## Instructions
1. Analyze the recent results to understand what's working and what isn't
2. Propose exactly ONE small change to ONE file in the workspace
3. The change should be targeted — informed by the pattern in recent results
4. Output your proposal as JSON:

```json
{{
  "file": "workspace/path/to/file",
  "reasoning": "Why this change should improve the score",
  "old": "exact text to replace (must match exactly)",
  "new": "replacement text"
}}
```

If you believe the current state is optimal (no change would improve it), output:
```json
{{"plateau": true, "reasoning": "Why no change would help"}}
```

Output ONLY the JSON block, nothing else."""

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    # Parse the response
    text = response.content[0].text.strip()
    # Extract JSON from potential markdown code block
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"Could not parse LLM response as JSON: {text[:200]}", file=sys.stderr)
        return None


def apply_change(climb_dir: Path, change: dict) -> bool:
    """Apply a proposed change to the workspace."""
    file_path = climb_dir / change["file"]
    if not file_path.exists():
        print(f"File not found: {change['file']}", file=sys.stderr)
        return False

    with open(file_path) as f:
        content = f.read()

    old = change.get("old", "")
    new = change.get("new", "")

    if old not in content:
        print(f"Old text not found in {change['file']}", file=sys.stderr)
        return False

    # Ensure unique match
    if content.count(old) > 1:
        print(f"Old text matches multiple locations in {change['file']}", file=sys.stderr)
        return False

    new_content = content.replace(old, new, 1)
    with open(file_path, "w") as f:
        f.write(new_content)

    return True


def climb_loop(climb_dir: Path):
    """Main climbing loop. Runs until killed or budget exhausted."""
    config = load_config(climb_dir)
    program = load_program(climb_dir)
    model = os.environ.get("CLIMBER_MODEL", "claude-sonnet-4-6")
    max_iterations = config.get("max_iterations", 500)
    results_window = config.get("results_window", 20)
    sleep_between = config.get("sleep_between_iterations", 5)

    print(f"Climber starting: {config.get('climb_id', 'unknown')}")
    print(f"Model: {model}, Max iterations: {max_iterations}, Window: {results_window}")

    while True:
        iteration = get_iteration_count(climb_dir)

        # Budget check
        if iteration >= max_iterations:
            print(f"Budget exhausted at iteration {iteration}")
            break

        # Law 4: verify eval integrity
        check_frozen_integrity(climb_dir, config)

        # Read current state
        workspace_files = read_workspace_files(climb_dir, config)
        recent_results = load_recent_results(climb_dir, results_window)

        # Baseline eval (before change)
        baseline = run_eval(climb_dir, config)
        if baseline is None:
            print("Baseline eval failed, sleeping and retrying...", file=sys.stderr)
            time.sleep(30)
            continue
        baseline_score = baseline.get("score", 0)

        # Propose a change
        change = propose_change(program, workspace_files, recent_results, iteration, model)
        if change is None:
            print("Failed to get a valid proposal, sleeping and retrying...", file=sys.stderr)
            time.sleep(30)
            continue

        # Plateau detection
        if change.get("plateau"):
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"PLATEAU: {change.get('reasoning', 'no reasoning')}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "plateau",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] PLATEAU detected: {change.get('reasoning', '')}")
            # Sleep longer on plateau — supervisor should notice and intervene
            time.sleep(300)
            continue

        # Law 3: snapshot before change
        git_snapshot(climb_dir, f"pre-change-iter-{iteration}")

        # Apply the change
        applied = apply_change(climb_dir, change)
        if not applied:
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"FAILED TO APPLY: {change.get('reasoning', '')}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "skip",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] Change failed to apply")
            time.sleep(sleep_between)
            continue

        # Eval after change
        new_eval = run_eval(climb_dir, config)
        if new_eval is None:
            # Eval failed — revert to be safe
            git_revert_workspace(climb_dir)
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"EVAL FAILED after: {change.get('reasoning', '')}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "revert",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] Eval failed after change, reverted")
            time.sleep(sleep_between)
            continue

        new_score = new_eval.get("score", 0)

        # Keep or revert
        if new_score >= baseline_score:
            decision = "keep"
            git_snapshot(climb_dir, f"keep-iter-{iteration}: {change.get('reasoning', '')[:80]}")
            print(f"[iter {iteration}] KEEP: {baseline_score} -> {new_score} ({change.get('reasoning', '')[:60]})")
        else:
            decision = "revert"
            git_revert_workspace(climb_dir)
            print(f"[iter {iteration}] REVERT: {baseline_score} -> {new_score} ({change.get('reasoning', '')[:60]})")

        # Log result
        result = {
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "change": change.get("reasoning", ""),
            "file": change.get("file", ""),
            "score": new_score if decision == "keep" else baseline_score,
            "previous_score": baseline_score,
            "decision": decision,
            "details": new_eval.get("details", {}),
        }
        append_result(climb_dir, result)

        time.sleep(sleep_between)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <climb-directory>", file=sys.stderr)
        sys.exit(1)

    climb_dir = Path(sys.argv[1]).resolve()
    if not climb_dir.is_dir():
        print(f"ERROR: {climb_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Ensure required structure exists
    for required in ["program.md", "config.json", "eval"]:
        if not (climb_dir / required).exists():
            print(f"ERROR: {climb_dir / required} not found", file=sys.stderr)
            sys.exit(1)

    (climb_dir / "logs").mkdir(exist_ok=True)
    (climb_dir / ".frozen").mkdir(exist_ok=True)

    # Initialize frozen copies if not present
    config = load_config(climb_dir)
    frozen_dir = climb_dir / ".frozen"
    for frozen_file in config.get("frozen_files", []):
        src = climb_dir / frozen_file
        dst = frozen_dir / Path(frozen_file).name
        if src.exists() and not dst.exists():
            import shutil
            shutil.copy2(src, dst)

    try:
        climb_loop(climb_dir)
    except KeyboardInterrupt:
        print("\nClimber stopped by signal")
        sys.exit(0)


if __name__ == "__main__":
    main()
