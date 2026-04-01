#!/usr/bin/env python3
"""Poller that surfaces chainlink issues waiting for review."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

try:
    from config import load_config
except ImportError:  # pragma: no cover - import path fallback
    from .config import load_config


CHAINLINK_BIN = Path.home() / ".cargo" / "bin" / "chainlink"
POLLER_NAME = os.environ.get("POLLER_NAME", "chainlink-review")


def run_chainlink(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one chainlink shell command."""
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def shell_join(parts: list[str | Path]) -> str:
    """Quote a shell command safely."""
    return " ".join(shlex.quote(str(part)) for part in parts)


def parse_json_output(stdout: str) -> Any:
    """Parse plain JSON or JSON with warning prefixes."""
    text = stdout.strip()
    if not text:
        return None
    for marker in (None, "{", "["):
        candidate = text if marker is None else _json_suffix(text, marker)
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"expected JSON output, got: {stdout!r}")


def _json_suffix(text: str, marker: str) -> str | None:
    index = text.find(marker)
    if index == -1:
        return None
    return text[index:]


def emit(prompt: str) -> None:
    """Emit one poller event to stdout."""
    event = {
        "poller": POLLER_NAME,
        "source_platform": "chainlink",
        "prompt": prompt,
    }
    print(json.dumps(event), flush=True)


def format_since(raw_timestamp: str | None) -> str:
    """Render an ISO timestamp in local time."""
    if not raw_timestamp:
        return "unknown time"
    normalized = raw_timestamp.replace("Z", "+00:00")
    instant = datetime.fromisoformat(normalized)
    return instant.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def main() -> int:
    """CLI entrypoint."""
    config = load_config()
    command = shell_join([CHAINLINK_BIN, "issue", "list", "--json", "-l", "ready-for-review"])
    result = run_chainlink(command, config.worker.chainlink_cwd)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return result.returncode

    issues = parse_json_output(result.stdout)
    if not isinstance(issues, list) or not issues:
        return 0

    lines = ["Issues ready for review:"]
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = issue.get("id", "?")
        title = issue.get("title") or "Untitled issue"
        since = format_since(issue.get("updated_at") or issue.get("created_at"))
        lines.append(f'- #{issue_id} "{title}" (ready-for-review since {since})')

    if len(lines) == 1:
        return 0

    emit("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
