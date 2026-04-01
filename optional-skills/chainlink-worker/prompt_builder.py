#!/usr/bin/env python3
"""Prompt builders for the chainlink backlog worker."""

from __future__ import annotations

from typing import Any


def build_prompt(issue: dict[str, Any], repo_path: str, rules: list[str] | None = None) -> str:
    """Build a Codex prompt from a chainlink issue."""
    issue_id = issue.get("id", "?")
    title = issue.get("title") or "Untitled issue"

    sections = [
        f"# Chainlink Issue #{issue_id}: {title}",
        "",
        "Work in the repository below and complete the issue.",
        f"Repository path: `{repo_path}`",
        "",
        "## Issue Description",
        _normalize_text(issue.get("description"), empty="_No description provided._"),
        "",
        "## Milestone",
        _format_milestone(issue.get("milestone")),
        "",
        "## Labels",
        _format_labels(issue.get("labels")),
        "",
        "## Related Issues",
        _format_issue_list(issue.get("related")),
        "",
        "## Subissues",
        _format_issue_list(issue.get("subissues")),
        "",
        "## Blocked By",
        _format_issue_list(issue.get("blocked_by")),
        "",
        "## Blocking",
        _format_issue_list(issue.get("blocking")),
        "",
        "## Comments",
        _format_comments(issue.get("comments")),
        "",
        "## Extra Rules",
        _format_rules(rules or []),
        "",
        "## Execution Requirements",
        "- Inspect the relevant code before editing.",
        "- Make the smallest correct change that fully resolves the issue.",
        "- Add or update tests around the changed behavior.",
        "- Run focused validation for what you changed.",
        "- End with a concise summary of files changed, tests run, and any follow-up risks.",
    ]
    return "\n".join(sections).strip() + "\n"


def build_review_prompt(issue: dict[str, Any], review_comments: list[str]) -> str:
    """Build a follow-up prompt from review feedback."""
    issue_id = issue.get("id", "?")
    title = issue.get("title") or "Untitled issue"
    description = _normalize_text(issue.get("description"), empty="_No description provided._")

    lines = [
        f"# Review Feedback for Chainlink Issue #{issue_id}: {title}",
        "",
        "Stay in the same repository and continue in the existing Codex session.",
        "",
        "## New Review Comments",
        _format_review_comments(review_comments),
        "",
        "## Original Issue Summary",
        description,
        "",
        "## Instructions",
        "- Address every review point before you stop.",
        "- Keep the existing implementation context and prior fixes intact.",
        "- Update or add focused tests if the review changes behavior.",
        "- Re-run the relevant validation.",
        "- End with a concise summary mapped back to the review comments.",
    ]
    return "\n".join(lines).strip() + "\n"


def _format_milestone(milestone: Any) -> str:
    if not isinstance(milestone, dict) or not milestone:
        return "_No milestone attached._"

    lines = [
        f"- Name: {milestone.get('name') or 'Unnamed milestone'}",
        f"- Status: {milestone.get('status') or 'unknown'}",
    ]
    description = _normalize_text(milestone.get("description"), empty="")
    if description:
        lines.append(f"- Description: {description}")
    return "\n".join(lines)


def _format_labels(labels: Any) -> str:
    if not labels:
        return "_No labels._"
    clean = [str(label).strip() for label in labels if str(label).strip()]
    if not clean:
        return "_No labels._"
    return ", ".join(f"`{label}`" for label in clean)


def _format_issue_list(items: Any) -> str:
    if not items:
        return "_None._"

    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            issue_id = item.get("id") or item.get("issue_id") or "?"
            title = item.get("title") or item.get("name") or "Untitled"
            extra = item.get("description") or item.get("status") or ""
            line = f"- #{issue_id} {title}"
            if extra:
                line += f" — {_single_line(extra)}"
            lines.append(line)
            continue
        lines.append(f"- {_single_line(item)}")
    return "\n".join(lines)


def _format_comments(comments: Any) -> str:
    if not comments:
        return "_No comments yet._"

    lines: list[str] = []
    for comment in comments:
        if isinstance(comment, dict):
            created_at = comment.get("created_at") or "unknown time"
            kind = comment.get("kind") or "note"
            content = _normalize_text(comment.get("content"), empty="")
            if not content:
                continue
            lines.append(f"- [{created_at}] ({kind}) {content}")
            continue
        lines.append(f"- {_single_line(comment)}")

    if not lines:
        return "_No comments yet._"
    return "\n".join(lines)


def _format_rules(rules: list[str]) -> str:
    if not rules:
        return "_No additional rules provided._"

    blocks: list[str] = []
    for index, rule in enumerate(rules, start=1):
        blocks.append(f"### Rule Set {index}\n{rule.strip()}")
    return "\n\n".join(blocks)


def _format_review_comments(review_comments: list[str]) -> str:
    if not review_comments:
        return "- No review comments provided."
    return "\n".join(f"- {_single_line(comment)}" for comment in review_comments)


def _normalize_text(value: Any, empty: str) -> str:
    if value is None:
        return empty
    text = str(value).strip()
    if not text:
        return empty
    return text


def _single_line(value: Any) -> str:
    return " ".join(str(value).split())
