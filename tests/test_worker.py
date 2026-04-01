"""Tests for the chainlink backlog worker loop."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


SKILL_DIR = Path(__file__).resolve().parents[1] / "optional-skills" / "chainlink-worker"
sys.path.insert(0, str(SKILL_DIR))
try:
    import config as chainlink_worker_config
    import worker as chainlink_worker
finally:
    sys.path.pop(0)


class FakeRunner:
    """Simple queued subprocess stub."""

    def __init__(self, responses: list[tuple[str, int, str, str]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Path, int | None]] = []

    def __call__(self, command: str, cwd: Path, timeout: int | None) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, cwd, timeout))
        if not self.responses:
            raise AssertionError(f"unexpected command: {command}")
        expected_substring, returncode, stdout, stderr = self.responses.pop(0)
        assert expected_substring in command
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def make_config(tmp_path: Path) -> chainlink_worker_config.AppConfig:
    return chainlink_worker_config.AppConfig(
        worker=chainlink_worker_config.WorkerSettings(
            chainlink_cwd=tmp_path / "goat-herder",
            poll_interval_seconds=1,
            codex_poll_seconds=1,
            max_codex_wait_seconds=30,
            agent_id="backlog-worker",
            rules_dir=None,
        ),
        repos={
            "open-strix": tmp_path / "open-strix",
            "vera-prism": tmp_path / "vera-prism",
        },
        source_path=None,
    )


def test_run_once_claims_issue_and_marks_ready_for_review(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "Lives in open-strix/optional-skills/chainlink-worker.",
        "status": "open",
        "priority": "high",
        "labels": ["infra"],
        "comments": [],
        "blocked_by": [],
        "blocking": [],
        "subissues": [],
        "related": [],
        "milestone": None,
    }

    runner = FakeRunner(
        [
            ("chainlink issue ready --json", 0, "[{\"id\": 50}]", ""),
            ("chainlink show 50 --json", 0, _json(issue), ""),
            ("chainlink label 50 in-progress", 0, "", ""),
            ("chainlink session work 50", 0, "", ""),
            ("codex sessions show issue-50", 1, "missing session", "missing session"),
            ("codex sessions new --name issue-50", 0, "", ""),
            ("codex set-mode -s issue-50 full-access", 0, "", ""),
            ("codex sessions show issue-50", 0, _codex_show(history_entries=0), ""),
            ("codex -s issue-50 --no-wait", 0, "", ""),
            ("codex sessions show issue-50", 0, _codex_show(history_entries=1), ""),
            ("codex sessions read issue-50 --tail 1", 0, "session: 1\nassistant: done", ""),
            ("chainlink label 50 ready-for-review", 0, "", ""),
            ("chainlink unlabel 50 in-progress", 0, "", ""),
            ("chainlink show 50 --json", 0, _json({**issue, "labels": ["ready-for-review"]}), ""),
        ]
    )

    worker = chainlink_worker.ChainlinkWorker(config, command_runner=runner, sleep_fn=lambda _: None)

    worked = worker.run_once()

    assert worked is True
    assert worker.state.issue_id == 50
    assert worker.state.phase == "awaiting_review"
    assert worker.state.session_name == "issue-50"
    assert worker.state.review_rounds == 0
    assert worker.state.repo_path == config.repos["open-strix"]
    assert runner.responses == []


def test_advance_current_issue_sends_review_feedback_to_same_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    current_issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "Lives in open-strix/optional-skills/chainlink-worker.",
        "status": "open",
        "priority": "high",
        "labels": ["ready-for-review"],
        "comments": [
            {"id": 1, "content": "Please add timeout coverage.", "kind": "human", "created_at": "2026-04-01T03:00:00Z"}
        ],
        "blocked_by": [],
        "blocking": [],
        "subissues": [],
        "related": [],
        "milestone": None,
    }

    runner = FakeRunner(
        [
            ("chainlink show 50 --json", 0, _json(current_issue), ""),
            ("chainlink label 50 in-progress", 0, "", ""),
            ("chainlink unlabel 50 ready-for-review", 0, "", ""),
            ("codex sessions show issue-50", 0, _codex_show(history_entries=1), ""),
            ("codex -s issue-50 --no-wait", 0, "", ""),
            ("codex sessions show issue-50", 0, _codex_show(history_entries=2), ""),
            ("codex sessions read issue-50 --tail 1", 0, "session: 1\nassistant: fixed", ""),
            ("chainlink label 50 ready-for-review", 0, "", ""),
            ("chainlink unlabel 50 in-progress", 0, "", ""),
            ("chainlink show 50 --json", 0, _json(current_issue), ""),
        ]
    )

    worker = chainlink_worker.ChainlinkWorker(config, command_runner=runner, sleep_fn=lambda _: None)
    worker.state.current_issue = {"id": 50}
    worker.state.repo_path = config.repos["open-strix"]
    worker.state.session_name = "issue-50"
    worker.state.phase = "awaiting_review"
    worker.state.last_comment_id = 0

    worked = worker.run_once()

    assert worked is True
    assert worker.state.phase == "awaiting_review"
    assert worker.state.review_rounds == 1
    assert worker.state.last_comment_id == 1
    assert runner.responses == []


def test_approval_comment_closes_issue_and_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    reviewed_issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "desc",
        "status": "open",
        "labels": ["ready-for-review"],
        "comments": [
            {"id": 2, "content": "APPROVED", "kind": "resolution", "created_at": "2026-04-01T03:10:00Z"}
        ],
        "blocked_by": [],
        "blocking": [],
        "subissues": [],
        "related": [],
        "milestone": None,
    }

    runner = FakeRunner(
        [
            ("chainlink show 50 --json", 0, _json(reviewed_issue), ""),
            ("chainlink close 50", 0, "", ""),
            ("codex sessions close issue-50", 0, "", ""),
        ]
    )

    worker = chainlink_worker.ChainlinkWorker(config, command_runner=runner, sleep_fn=lambda _: None)
    worker.state.current_issue = {"id": 50}
    worker.state.repo_path = config.repos["open-strix"]
    worker.state.session_name = "issue-50"
    worker.state.phase = "awaiting_review"
    worker.state.last_comment_id = 0

    worked = worker.run_once()

    assert worked is True
    assert worker.state.issue_id is None
    assert worker.state.session_name is None
    assert worker.state.phase == "idle"
    assert runner.responses == []


def test_resolve_repo_path_falls_back_to_issue_text(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    worker = chainlink_worker.ChainlinkWorker(config, command_runner=FakeRunner([]), sleep_fn=lambda _: None)

    issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "Lives in open-strix/optional-skills/chainlink-worker.",
        "labels": ["infra"],
        "milestone": None,
    }

    repo_path = worker.resolve_repo_path(issue)

    assert repo_path == config.repos["open-strix"]


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload)


def _codex_show(*, history_entries: int) -> str:
    return "\n".join(
        [
            "id: session-id",
            "sessionId: acp-id",
            "agentSessionId: -",
            "agent: npx acpx codex",
            "cwd: /tmp/repo",
            "name: issue-50",
            "created: 2026-04-01T03:00:00Z",
            "lastActivity: 2026-04-01T03:00:00Z",
            "lastPrompt: -",
            "closed: no",
            "closedAt: -",
            "pid: 123",
            "agentStartedAt: 2026-04-01T03:00:00Z",
            "lastExitCode: -",
            "lastExitSignal: -",
            "lastExitAt: 2026-04-01T03:00:00Z",
            "disconnectReason: connection_close",
            f"historyEntries: {history_entries}",
        ]
    )
