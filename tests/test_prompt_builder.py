"""Tests for the chainlink worker prompt builders."""

from __future__ import annotations

from pathlib import Path
import sys


SKILL_DIR = Path(__file__).resolve().parents[1] / "optional-skills" / "chainlink-worker"
sys.path.insert(0, str(SKILL_DIR))
try:
    import prompt_builder as chainlink_worker_prompt_builder
finally:
    sys.path.pop(0)


def test_build_prompt_includes_issue_context() -> None:
    issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "Create the worker and the review poller.",
        "labels": ["infra", "open-strix"],
        "milestone": {
            "name": "Chainlink Backlog Worker",
            "description": "Phase 1 MVP",
            "status": "open",
        },
        "comments": [
            {
                "id": 4,
                "created_at": "2026-04-01T02:40:00Z",
                "kind": "human",
                "content": "Make sure the review loop reuses the same session.",
            }
        ],
        "related": [{"id": 51, "title": "Build review poller", "status": "open"}],
        "subissues": [],
        "blocked_by": [],
        "blocking": [],
    }

    prompt = chainlink_worker_prompt_builder.build_prompt(
        issue,
        "/repo/open-strix",
        rules=["# quality.md\nPrefer focused tests."],
    )

    assert "# Chainlink Issue #50: Build chainlink backlog worker" in prompt
    assert "Repository path: `/repo/open-strix`" in prompt
    assert "Create the worker and the review poller." in prompt
    assert "Chainlink Backlog Worker" in prompt
    assert "`infra`, `open-strix`" in prompt
    assert "#51 Build review poller" in prompt
    assert "Make sure the review loop reuses the same session." in prompt
    assert "Prefer focused tests." in prompt
    assert "Run focused validation" in prompt


def test_build_review_prompt_includes_review_comments() -> None:
    issue = {
        "id": 50,
        "title": "Build chainlink backlog worker",
        "description": "Create the worker and the review poller.",
    }

    prompt = chainlink_worker_prompt_builder.build_review_prompt(
        issue,
        [
            "Please add a timeout test.",
            "Keep the ready-for-review label flow intact.",
        ],
    )

    assert "# Review Feedback for Chainlink Issue #50: Build chainlink backlog worker" in prompt
    assert "Please add a timeout test." in prompt
    assert "Keep the ready-for-review label flow intact." in prompt
    assert "Create the worker and the review poller." in prompt
    assert "Address every review point" in prompt
