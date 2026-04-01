#!/usr/bin/env python3
"""Main loop for the chainlink backlog worker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from config import AppConfig, load_config
    from prompt_builder import build_prompt, build_review_prompt
except ImportError:  # pragma: no cover - import path fallback
    from .config import AppConfig, load_config
    from .prompt_builder import build_prompt, build_review_prompt


CHAINLINK_BIN = Path.home() / ".cargo" / "bin" / "chainlink"

READY_EXCLUDED_LABELS = frozenset({
    "blocked",
    "blocked-on-tim",
    "in-progress",
    "ready-for-review",
})

APPROVAL_PATTERNS = (
    re.compile(r"^approved\b"),
    re.compile(r"^lgtm\b"),
    re.compile(r"^ship it\b"),
    re.compile(r"^looks good\b"),
)


class StopRequested(RuntimeError):
    """Raised when a signal asks the worker to stop."""


class CommandError(RuntimeError):
    """Raised when a shell command exits non-zero."""

    def __init__(self, command: str, result: subprocess.CompletedProcess[str]) -> None:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        super().__init__(f"command failed ({result.returncode}): {command} :: {detail}")
        self.command = command
        self.result = result


CommandRunner = Callable[[str, Path, int | None], subprocess.CompletedProcess[str]]


@dataclass(slots=True)
class WorkerState:
    """In-memory state for the current issue lifecycle."""

    current_issue: dict[str, Any] | None = None
    repo_path: Path | None = None
    session_name: str | None = None
    phase: str = "idle"
    review_rounds: int = 0
    prompt_baseline_history: int | None = None
    claimed_at: datetime | None = None
    prompt_sent_at: datetime | None = None
    ready_for_review_at: datetime | None = None
    last_review_at: datetime | None = None
    last_comment_id: int = 0

    @property
    def issue_id(self) -> int | None:
        if not isinstance(self.current_issue, dict):
            return None
        raw_id = self.current_issue.get("id")
        if raw_id is None:
            return None
        return int(raw_id)

    def clear(self) -> None:
        self.current_issue = None
        self.repo_path = None
        self.session_name = None
        self.phase = "idle"
        self.review_rounds = 0
        self.prompt_baseline_history = None
        self.claimed_at = None
        self.prompt_sent_at = None
        self.ready_for_review_at = None
        self.last_review_at = None
        self.last_comment_id = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _start_heartbeat_monitor(fd: int) -> None:
    """Exit if the parent process dies and the pipe closes."""

    def _monitor() -> None:
        try:
            os.read(fd, 1)
        except OSError:
            pass
        os._exit(0)

    thread = threading.Thread(target=_monitor, daemon=True)
    thread.start()


def run_shell(command: str, cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run one shell command with captured text output."""
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class ChainlinkWorker:
    """Pure orchestration loop for chainlink + Codex."""

    def __init__(
        self,
        config: AppConfig,
        command_runner: CommandRunner | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] = _utc_now,
        prompt_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.command_runner = command_runner or run_shell
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.prompt_dir = prompt_dir or (Path(tempfile.gettempdir()) / "chainlink-worker")
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.state = WorkerState()
        self._stop_requested = False

    def request_stop(self, reason: str) -> None:
        """Mark the loop for shutdown."""
        self._stop_requested = True
        self.log("shutdown_requested", reason=reason, issue_id=self.state.issue_id, phase=self.state.phase)

    def run(self) -> None:
        """Run the worker until a stop signal arrives."""
        self.log(
            "worker_start",
            chainlink_cwd=self.config.worker.chainlink_cwd,
            poll_seconds=self.config.worker.poll_interval_seconds,
            codex_poll_seconds=self.config.worker.codex_poll_seconds,
            agent_id=self.config.worker.agent_id,
        )
        while not self._stop_requested:
            try:
                self.run_once()
            except StopRequested:
                break
            except Exception as exc:
                self.log(
                    "worker_error",
                    issue_id=self.state.issue_id,
                    phase=self.state.phase,
                    error=str(exc),
                )
            if self._stop_requested:
                break
            self._sleep(self.config.worker.poll_interval_seconds)
        self.log("worker_stop", issue_id=self.state.issue_id, phase=self.state.phase)

    def run_once(self) -> bool:
        """Run one worker turn. Returns True when work moved forward."""
        if self.state.issue_id is None:
            issue = self.find_next_issue()
            if issue is None:
                self.log("idle")
                return False
            self.start_issue(issue)
            return True
        return self.advance_current_issue()

    def find_next_issue(self) -> dict[str, Any] | None:
        """Find the next ready issue."""
        ready_ids = self._load_ready_issue_ids()
        if ready_ids:
            for issue_id in ready_ids:
                issue = self.get_issue(issue_id)
                if self.is_issue_ready(issue):
                    return issue

        for issue_stub in self.list_open_issues():
            issue_id = int(issue_stub["id"])
            issue = self.get_issue(issue_id)
            if self.is_issue_ready(issue):
                return issue
        return None

    def list_open_issues(self) -> list[dict[str, Any]]:
        """List open issues, sorted for deterministic fallback polling."""
        data = self._run_chainlink_json("issue", "list", "--json", "-s", "open")
        if not isinstance(data, list):
            return []

        issues = [item for item in data if isinstance(item, dict) and item.get("id") is not None]
        issues.sort(key=self._issue_sort_key)
        return issues

    def get_issue(self, issue_id: int) -> dict[str, Any]:
        """Fetch a full issue payload."""
        data = self._run_chainlink_json("show", str(issue_id), "--json")
        if not isinstance(data, dict):
            raise ValueError(f"expected issue dict for #{issue_id}, got {type(data).__name__}")
        return data

    def is_issue_ready(self, issue: dict[str, Any]) -> bool:
        """Apply the worker's readiness filters."""
        status = str(issue.get("status") or "").strip().lower()
        if status != "open":
            return False

        labels = {str(label).strip().lower() for label in issue.get("labels") or [] if str(label).strip()}
        if labels & READY_EXCLUDED_LABELS:
            return False

        if issue.get("blocked_by"):
            return False

        assignee = self._extract_assignee(issue)
        if assignee and assignee != self.config.worker.agent_id:
            return False

        return True

    def resolve_repo_path(self, issue: dict[str, Any]) -> Path:
        """Route an issue to a repo using labels first, then issue text."""
        labels = [str(label).strip() for label in issue.get("labels") or [] if str(label).strip()]
        label_matches = [label for label in labels if label in self.config.repos]
        if len(label_matches) == 1:
            return self.config.repos[label_matches[0]]
        if len(label_matches) > 1:
            raise ValueError(f"issue #{issue.get('id')} matches multiple repo labels: {label_matches}")

        corpus_parts = [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
        ]
        milestone = issue.get("milestone")
        if isinstance(milestone, dict):
            corpus_parts.append(str(milestone.get("name") or ""))
            corpus_parts.append(str(milestone.get("description") or ""))

        corpus = "\n".join(corpus_parts).lower()
        text_matches = [label for label in self.config.repos if label.lower() in corpus]
        if len(text_matches) == 1:
            return self.config.repos[text_matches[0]]
        if len(text_matches) > 1:
            raise ValueError(f"issue #{issue.get('id')} mentions multiple repos: {text_matches}")

        raise ValueError(f"could not determine repo for issue #{issue.get('id')}")

    def start_issue(self, issue: dict[str, Any]) -> None:
        """Claim an issue, build the prompt, and dispatch Codex."""
        issue_id = int(issue["id"])
        repo_path = self.resolve_repo_path(issue)
        session_name = f"issue-{issue_id}"

        self.state.current_issue = issue
        self.state.repo_path = repo_path
        self.state.session_name = session_name
        self.state.phase = "claiming"
        self.state.review_rounds = 0
        self.state.last_comment_id = self._max_comment_id(issue)

        self.claim_issue(issue_id)
        self.state.claimed_at = self.now_fn()
        self.log("issue_claimed", issue_id=issue_id, session=session_name, repo=repo_path)

        rules = self.load_rules()
        prompt = build_prompt(issue, str(repo_path), rules)

        self.ensure_session(session_name, repo_path)
        baseline = self.get_history_entries(session_name, repo_path)
        prompt_file = self.write_prompt_file(issue_id, "initial", prompt)
        self.send_prompt(session_name, repo_path, prompt_file)

        self.state.prompt_baseline_history = baseline
        self.state.prompt_sent_at = self.now_fn()
        self.state.phase = "implementing"
        self.log(
            "prompt_queued",
            issue_id=issue_id,
            session=session_name,
            history_baseline=baseline,
            prompt_file=prompt_file,
        )

        self._complete_pending_prompt()

    def advance_current_issue(self) -> bool:
        """Advance the current issue based on phase and review state."""
        if self.state.phase in {"implementing", "addressing-review"}:
            self._complete_pending_prompt()
            return True

        if self.state.phase != "awaiting_review":
            self.log("unknown_phase", issue_id=self.state.issue_id, phase=self.state.phase)
            return False

        issue_id = self._require_issue_id()
        issue = self.get_issue(issue_id)
        self.state.current_issue = issue

        if str(issue.get("status") or "").strip().lower() == "closed":
            self.log("issue_already_closed", issue_id=issue_id)
            self._close_session_only()
            self.state.clear()
            return True

        new_comments = self._new_comments(issue)
        if not new_comments:
            self.log("awaiting_review", issue_id=issue_id, review_rounds=self.state.review_rounds)
            return False

        approval_comments = [comment for comment in new_comments if self.is_approval_comment(comment)]
        if approval_comments:
            self.log("review_approved", issue_id=issue_id, comments=len(approval_comments))
            self.finalize_issue()
            return True

        review_comments = [
            str(comment.get("content") or "").strip()
            for comment in new_comments
            if str(comment.get("content") or "").strip()
        ]
        if not review_comments:
            self.state.last_comment_id = self._max_comment_id(issue)
            return False

        self.state.review_rounds += 1
        self.state.last_review_at = self.now_fn()
        self.state.last_comment_id = self._max_comment_id(issue)
        self.mark_in_progress(issue_id)

        session_name = self._require_session_name()
        repo_path = self._require_repo_path()
        review_prompt = build_review_prompt(issue, review_comments)
        baseline = self.get_history_entries(session_name, repo_path)
        prompt_file = self.write_prompt_file(issue_id, f"review-{self.state.review_rounds}", review_prompt)
        self.send_prompt(session_name, repo_path, prompt_file)

        self.state.prompt_baseline_history = baseline
        self.state.prompt_sent_at = self.now_fn()
        self.state.phase = "addressing-review"
        self.log(
            "review_feedback_queued",
            issue_id=issue_id,
            round=self.state.review_rounds,
            comments=len(review_comments),
            prompt_file=prompt_file,
        )

        self._complete_pending_prompt()
        return True

    def claim_issue(self, issue_id: int) -> None:
        """Mark the issue in progress and attach the local session."""
        self._run_chainlink("label", str(issue_id), "in-progress")
        self._run_chainlink("session", "work", str(issue_id))

    def mark_ready_for_review(self, issue_id: int) -> None:
        """Flip labels from active work to review."""
        self._run_chainlink("label", str(issue_id), "ready-for-review")
        self._run_chainlink("unlabel", str(issue_id), "in-progress")

    def mark_in_progress(self, issue_id: int) -> None:
        """Flip labels from review back to active work."""
        self._run_chainlink("label", str(issue_id), "in-progress")
        self._run_chainlink("unlabel", str(issue_id), "ready-for-review")

    def finalize_issue(self) -> None:
        """Close the issue and close the associated Codex session."""
        issue_id = self._require_issue_id()
        session_name = self._require_session_name()
        repo_path = self._require_repo_path()
        self._run_chainlink("close", str(issue_id))
        self._run_codex(repo_path, "sessions", "close", session_name, check=False)
        self.log("issue_closed", issue_id=issue_id, session=session_name)
        self.state.clear()

    def ensure_session(self, session_name: str, repo_path: Path) -> None:
        """Reuse an open session or create a new one."""
        metadata = self.get_session_metadata(session_name, repo_path, allow_missing=True)
        if metadata is None or metadata.get("closed") == "yes":
            self._run_codex(repo_path, "sessions", "new", "--name", session_name)
        self._run_codex(repo_path, "set-mode", "-s", session_name, "full-access")

    def send_prompt(self, session_name: str, repo_path: Path, prompt_file: Path) -> None:
        """Queue a prompt file onto an existing session."""
        self._run_codex(repo_path, "-s", session_name, "--no-wait", "-f", prompt_file)

    def get_session_metadata(
        self,
        session_name: str,
        repo_path: Path,
        *,
        allow_missing: bool = False,
    ) -> dict[str, str] | None:
        """Read Codex session metadata from `sessions show`."""
        result = self._run_codex(repo_path, "sessions", "show", session_name, check=not allow_missing)
        if result.returncode != 0:
            return None
        return self._parse_key_value_output(result.stdout)

    def get_history_entries(self, session_name: str, repo_path: Path) -> int:
        """Return current history entry count for a Codex session."""
        metadata = self.get_session_metadata(session_name, repo_path)
        if not metadata:
            return 0
        raw_value = metadata.get("historyEntries", "0") or "0"
        return int(raw_value)

    def wait_for_codex_completion(
        self,
        session_name: str,
        repo_path: Path,
        baseline_history: int,
    ) -> dict[str, str]:
        """Wait until Codex writes at least one new history entry."""
        deadline = time.monotonic() + self.config.worker.max_codex_wait_seconds
        while True:
            metadata = self.get_session_metadata(session_name, repo_path)
            history_entries = int(metadata.get("historyEntries", "0") or "0")
            if history_entries > baseline_history:
                return metadata
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for Codex session {session_name} after "
                    f"{self.config.worker.max_codex_wait_seconds}s"
                )
            self._sleep(self.config.worker.codex_poll_seconds)

    def read_session_tail(self, session_name: str, repo_path: Path) -> str:
        """Read the last history entry for logging."""
        result = self._run_codex(repo_path, "sessions", "read", session_name, "--tail", "1")
        return result.stdout.strip()

    def load_rules(self) -> list[str]:
        """Load rule files from the configured rules directory."""
        rules_dir = self.config.worker.rules_dir
        if rules_dir is None or not rules_dir.exists():
            return []

        rules: list[str] = []
        for path in sorted(rules_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".txt"}:
                continue
            rules.append(f"# {path.name}\n\n{path.read_text(encoding='utf-8').strip()}")
        return rules

    def write_prompt_file(self, issue_id: int, name: str, prompt: str) -> Path:
        """Persist the current prompt to a temp file for `codex -f`."""
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-") or "prompt"
        prompt_path = self.prompt_dir / f"issue-{issue_id}-{safe_name}.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        return prompt_path

    def is_approval_comment(self, comment: dict[str, Any]) -> bool:
        """Treat explicit approval phrases as closure signals."""
        content = " ".join(str(comment.get("content") or "").strip().lower().split())
        kind = str(comment.get("kind") or "").strip().lower()
        if "not approved" in content:
            return False
        if kind not in {"resolution", "decision", "note", "human"}:
            return False
        return any(pattern.search(content) for pattern in APPROVAL_PATTERNS)

    def log(self, event: str, **fields: object) -> None:
        """Emit one structured, human-readable log line."""
        timestamp = self.now_fn().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        parts = [timestamp, f"event={event}"]
        for key, value in fields.items():
            parts.append(f"{key}={self._format_log_value(value)}")
        print(" ".join(parts), flush=True)

    def _complete_pending_prompt(self) -> None:
        """Wait for the in-flight prompt, then move the issue to review."""
        issue_id = self._require_issue_id()
        session_name = self._require_session_name()
        repo_path = self._require_repo_path()
        baseline_history = self.state.prompt_baseline_history
        if baseline_history is None:
            raise ValueError("prompt baseline history is not set")

        metadata = self.wait_for_codex_completion(session_name, repo_path, baseline_history)
        tail = self.read_session_tail(session_name, repo_path)

        self.mark_ready_for_review(issue_id)
        refreshed_issue = self.get_issue(issue_id)
        self.state.current_issue = refreshed_issue
        self.state.phase = "awaiting_review"
        self.state.prompt_baseline_history = None
        self.state.ready_for_review_at = self.now_fn()
        self.state.last_comment_id = self._max_comment_id(refreshed_issue)

        self.log(
            "prompt_complete",
            issue_id=issue_id,
            session=session_name,
            history_entries=metadata.get("historyEntries", "0"),
            review_rounds=self.state.review_rounds,
            tail=self._clip_text(tail),
        )

    def _load_ready_issue_ids(self) -> list[int]:
        """Use `issue ready` first, then fall back to parsing text output."""
        result = self._run_chainlink("issue", "ready", "--json")
        data = self._try_parse_json(result.stdout)
        if isinstance(data, list):
            issue_ids = [issue_id for issue_id in (self._coerce_issue_id(item) for item in data) if issue_id is not None]
            if issue_ids:
                return issue_ids
        return self._parse_ready_issue_ids(result.stdout)

    def _new_comments(self, issue: dict[str, Any]) -> list[dict[str, Any]]:
        comments = issue.get("comments") or []
        return [
            comment
            for comment in comments
            if isinstance(comment, dict) and int(comment.get("id") or 0) > self.state.last_comment_id
        ]

    def _close_session_only(self) -> None:
        session_name = self.state.session_name
        repo_path = self.state.repo_path
        if not session_name or repo_path is None:
            return
        self._run_codex(repo_path, "sessions", "close", session_name, check=False)

    def _extract_assignee(self, issue: dict[str, Any]) -> str:
        for key in ("assigned_to", "assignee", "assigned_agent", "claimed_by", "agent_id"):
            value = issue.get(key)
            if isinstance(value, dict):
                candidate = value.get("id") or value.get("name") or value.get("agent_id")
            else:
                candidate = value
            if candidate:
                return str(candidate).strip()
        return ""

    def _run_chainlink_json(self, *parts: str | Path) -> Any:
        result = self._run_chainlink(*parts)
        return self._parse_json_output(result.stdout)

    def _run_chainlink(self, *parts: str | Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = self._shell_join([CHAINLINK_BIN, *parts])
        return self._run_command(command, self.config.worker.chainlink_cwd, timeout=60, check=check)

    def _run_codex(
        self,
        repo_path: Path,
        *parts: str | Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self._shell_join(["npx", "acpx", "codex", *parts])
        return self._run_command(command, repo_path, timeout=60, check=check)

    def _run_command(
        self,
        command: str,
        cwd: Path,
        *,
        timeout: int | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        result = self.command_runner(command, cwd, timeout)
        if result.stderr.strip():
            self.log("command_stderr", command=command, stderr=self._clip_text(result.stderr.strip()))
        if check and result.returncode != 0:
            raise CommandError(command, result)
        return result

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            if self._stop_requested:
                raise StopRequested("stop requested")
            return
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_requested:
                raise StopRequested("stop requested")
            remaining = deadline - time.monotonic()
            self.sleep_fn(min(0.5, max(remaining, 0.0)))

    def _parse_json_output(self, stdout: str) -> Any:
        parsed = self._try_parse_json(stdout)
        if parsed is None:
            raise ValueError(f"expected JSON output, got: {stdout!r}")
        return parsed

    def _try_parse_json(self, stdout: str) -> Any | None:
        text = stdout.strip()
        if not text:
            return None
        for candidate in (text, self._json_suffix(text, "{"), self._json_suffix(text, "[")):
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _json_suffix(self, text: str, marker: str) -> str | None:
        index = text.find(marker)
        if index == -1:
            return None
        return text[index:]

    def _parse_ready_issue_ids(self, stdout: str) -> list[int]:
        issue_ids: list[int] = []
        for line in stdout.splitlines():
            match = re.match(r"^\s*#(\d+)\b", line)
            if match:
                issue_ids.append(int(match.group(1)))
        return issue_ids

    def _parse_key_value_output(self, stdout: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for line in stdout.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed

    def _issue_sort_key(self, issue: dict[str, Any]) -> tuple[int, str, int]:
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        priority = priority_rank.get(str(issue.get("priority") or "").lower(), 3)
        created_at = str(issue.get("created_at") or "")
        issue_id = int(issue.get("id") or 0)
        return (priority, created_at, issue_id)

    def _coerce_issue_id(self, item: Any) -> int | None:
        if isinstance(item, dict):
            raw_id = item.get("id") or item.get("issue_id") or item.get("number")
        else:
            raw_id = item
        if raw_id is None:
            return None
        return int(raw_id)

    def _max_comment_id(self, issue: dict[str, Any]) -> int:
        comments = issue.get("comments") or []
        return max(
            (int(comment.get("id") or 0) for comment in comments if isinstance(comment, dict)),
            default=0,
        )

    def _require_issue_id(self) -> int:
        issue_id = self.state.issue_id
        if issue_id is None:
            raise ValueError("worker has no active issue")
        return issue_id

    def _require_session_name(self) -> str:
        if not self.state.session_name:
            raise ValueError("worker has no active session")
        return self.state.session_name

    def _require_repo_path(self) -> Path:
        if self.state.repo_path is None:
            raise ValueError("worker has no active repo path")
        return self.state.repo_path

    def _shell_join(self, parts: list[str | Path]) -> str:
        return " ".join(shlex.quote(str(part)) for part in parts)

    def _format_log_value(self, value: object) -> str:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        text = str(value)
        if not text or any(character.isspace() for character in text) or "=" in text:
            return json.dumps(text)
        return text

    def _clip_text(self, text: str, limit: int = 240) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Run the chainlink backlog worker")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.toml")
    parser.add_argument(
        "--heartbeat-fd",
        type=int,
        default=None,
        help="Supervisor heartbeat pipe fd",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    if args.heartbeat_fd is not None:
        _start_heartbeat_monitor(args.heartbeat_fd)

    config = load_config(args.config)
    worker = ChainlinkWorker(config)

    def _handle_signal(signum: int, _frame: object) -> None:
        worker.request_stop(signal.Signals(signum).name)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
