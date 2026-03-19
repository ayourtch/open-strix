#!/usr/bin/env python3
"""
Mountaineering supervisor — manages the climber lifecycle.

Handles:
- Manifest-based registration (which climbs are active)
- Process spawning with parent-death signal
- Manifest-driven restart on supervisor recovery
- Status reporting for the monitoring block

Usage from an open-strix agent:
    from open_strix.builtin_skills.mountaineering.supervisor import Supervisor

    sup = Supervisor(state_dir="/path/to/runtime/state")
    sup.register("my-climb", "/path/to/climb/dir")
    sup.start_all()  # On boot — restarts registered climbs
    status = sup.status()  # For monitoring block
    sup.unregister("my-climb")  # Stop and remove
"""

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


CLIMBER_SCRIPT = Path(__file__).parent / "climber.py"


class Supervisor:
    """Manages the lifecycle of climber subprocesses."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.state_dir / "climbers.json"
        self._processes: dict[str, subprocess.Popen] = {}

    def _load_manifest(self) -> dict:
        """Load the manifest of registered climbs."""
        if not self.manifest_path.exists():
            return {}
        with open(self.manifest_path) as f:
            return json.load(f)

    def _save_manifest(self, manifest: dict):
        """Save the manifest."""
        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    def register(self, climb_id: str, climb_dir: str | Path, env: dict | None = None):
        """Register a new climb and start it.

        Args:
            climb_id: Unique identifier for this climb
            climb_dir: Path to the climb directory (must contain program.md, config.json, eval/)
            env: Optional extra environment variables for the climber process
        """
        climb_dir = Path(climb_dir).resolve()

        # Validate climb directory
        for required in ["program.md", "config.json", "eval"]:
            if not (climb_dir / required).exists():
                raise FileNotFoundError(f"{climb_dir / required} not found")

        manifest = self._load_manifest()
        manifest[climb_id] = {
            "climb_dir": str(climb_dir),
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "env": env or {},
        }
        self._save_manifest(manifest)

        self._spawn(climb_id, climb_dir, env or {})

    def unregister(self, climb_id: str):
        """Stop a climb and remove it from the manifest."""
        # Kill the process if running
        proc = self._processes.pop(climb_id, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        # Remove from manifest
        manifest = self._load_manifest()
        manifest.pop(climb_id, None)
        self._save_manifest(manifest)

    def start_all(self):
        """Start all registered climbs. Call this on supervisor boot."""
        manifest = self._load_manifest()
        for climb_id, entry in manifest.items():
            climb_dir = Path(entry["climb_dir"])
            if not climb_dir.exists():
                print(f"WARNING: climb dir for {climb_id} not found: {climb_dir}", file=sys.stderr)
                continue
            self._spawn(climb_id, climb_dir, entry.get("env", {}))

    def stop_all(self):
        """Stop all running climbers."""
        for climb_id in list(self._processes.keys()):
            proc = self._processes.pop(climb_id)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def status(self) -> dict[str, dict]:
        """Get status of all registered climbs.

        Returns a dict suitable for a monitoring memory block.
        """
        manifest = self._load_manifest()
        statuses = {}

        for climb_id, entry in manifest.items():
            climb_dir = Path(entry["climb_dir"])
            status = {
                "climb_dir": str(climb_dir),
                "registered_at": entry.get("registered_at", "unknown"),
            }

            # Check process status
            proc = self._processes.get(climb_id)
            if proc and proc.poll() is None:
                status["process"] = "running"
                status["pid"] = proc.pid
            elif proc:
                status["process"] = f"exited ({proc.returncode})"
            else:
                status["process"] = "not started"

            # Read recent results for trend info
            log_path = climb_dir / "logs" / "results.jsonl"
            if log_path.exists():
                results = []
                with open(log_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                results.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass

                status["total_iterations"] = len(results)

                if results:
                    last = results[-1]
                    status["last_score"] = last.get("score")
                    status["last_decision"] = last.get("decision")
                    status["last_timestamp"] = last.get("timestamp")

                    # Trend: slope over last 10 results
                    recent = results[-10:]
                    scores = [r.get("score", 0) for r in recent if r.get("score") is not None]
                    if len(scores) >= 3:
                        # Simple linear trend
                        n = len(scores)
                        x_mean = (n - 1) / 2
                        y_mean = sum(scores) / n
                        num = sum((i - x_mean) * (s - y_mean) for i, s in enumerate(scores))
                        den = sum((i - x_mean) ** 2 for i in range(n))
                        slope = num / den if den > 0 else 0
                        status["trend_slope"] = round(slope, 4)

                        # Plateau detection
                        keeps = sum(1 for r in recent if r.get("decision") == "keep")
                        plateaus = sum(1 for r in recent if r.get("decision") == "plateau")
                        if plateaus >= 3 or keeps == 0:
                            status["status"] = "plateau"
                        elif slope > 0.001:
                            status["status"] = "improving"
                        elif slope < -0.001:
                            status["status"] = "degrading"
                        else:
                            status["status"] = "flat"
                    else:
                        status["status"] = "insufficient_data"
            else:
                status["total_iterations"] = 0
                status["status"] = "no_data"

            statuses[climb_id] = status

        return statuses

    def format_monitoring_block(self) -> str:
        """Format status as a concise monitoring block string."""
        statuses = self.status()
        if not statuses:
            return "No active climbs."

        lines = []
        for climb_id, s in statuses.items():
            parts = [climb_id]
            parts.append(s.get("status", "unknown"))
            if s.get("total_iterations"):
                parts.append(f"iter {s['total_iterations']}")
            if s.get("last_score") is not None:
                parts.append(f"score {s['last_score']}")
            if s.get("trend_slope") is not None:
                parts.append(f"slope {s['trend_slope']}")
            parts.append(s.get("process", "unknown"))
            lines.append(": ".join([parts[0], ", ".join(parts[1:])]))

        return "\n".join(lines)

    def _spawn(self, climb_id: str, climb_dir: Path, env: dict):
        """Spawn a climber subprocess."""
        # Build environment
        child_env = os.environ.copy()
        child_env.update(env)

        # Use the Python from the current environment
        python = sys.executable

        log_dir = climb_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "climber_stdout.log"

        with open(stdout_log, "a") as log_file:
            proc = subprocess.Popen(
                [python, str(CLIMBER_SCRIPT), str(climb_dir)],
                env=child_env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                # prctl is set inside climber.py itself
            )

        self._processes[climb_id] = proc
        print(f"Spawned climber {climb_id} (pid={proc.pid})")


def preflight_check(climb_dir: str | Path) -> list[str]:
    """Run the pre-flight checklist on a climb directory.

    Returns a list of issues found. Empty list = ready to climb.
    """
    climb_dir = Path(climb_dir).resolve()
    issues = []

    # Structure checks
    if not (climb_dir / "program.md").exists():
        issues.append("Missing program.md (S5 — frozen goal and constraints)")
    if not (climb_dir / "config.json").exists():
        issues.append("Missing config.json (climb configuration)")
    if not (climb_dir / "eval").is_dir():
        issues.append("Missing eval/ directory (evaluation scripts)")

    # Config validation
    if (climb_dir / "config.json").exists():
        try:
            with open(climb_dir / "config.json") as f:
                config = json.load(f)

            if "eval_command" not in config:
                issues.append("config.json missing eval_command")
            if "scope" not in config:
                issues.append("config.json missing scope (what can the climber modify?)")
            if "frozen_files" not in config:
                issues.append("config.json missing frozen_files (Law 4 — scope separation)")

            # Check scope directories exist
            for scope_path in config.get("scope", []):
                if not (climb_dir / scope_path).exists():
                    issues.append(f"Scope path not found: {scope_path}")

            # Check frozen files exist
            for frozen_file in config.get("frozen_files", []):
                if not (climb_dir / frozen_file).exists():
                    issues.append(f"Frozen file not found: {frozen_file}")

        except json.JSONDecodeError:
            issues.append("config.json is not valid JSON")

    # Eval script check
    eval_dir = climb_dir / "eval"
    if eval_dir.is_dir() and not any(eval_dir.iterdir()):
        issues.append("eval/ directory is empty")

    # Workspace check
    workspace = climb_dir / "workspace"
    if not workspace.exists():
        issues.append("Missing workspace/ directory (mutable surface)")
    elif not any(workspace.rglob("*")):
        issues.append("workspace/ is empty — nothing to optimize")

    return issues


if __name__ == "__main__":
    # CLI for quick operations
    import argparse

    parser = argparse.ArgumentParser(description="Mountaineering supervisor")
    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser("preflight", help="Run pre-flight checks on a climb directory")
    check_p.add_argument("climb_dir", help="Path to climb directory")

    status_p = sub.add_parser("status", help="Show status of all registered climbs")
    status_p.add_argument("--state-dir", default=".", help="Supervisor state directory")

    args = parser.parse_args()

    if args.command == "preflight":
        issues = preflight_check(args.climb_dir)
        if issues:
            print("PRE-FLIGHT FAILED:")
            for issue in issues:
                print(f"  [ ] {issue}")
            sys.exit(1)
        else:
            print("PRE-FLIGHT PASSED: Ready to climb.")
            sys.exit(0)

    elif args.command == "status":
        sup = Supervisor(args.state_dir)
        print(sup.format_monitoring_block())

    else:
        parser.print_help()
