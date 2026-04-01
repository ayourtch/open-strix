#!/usr/bin/env python3
"""Configuration loader for the chainlink backlog worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "chainlink-worker" / "config.toml"

DEFAULT_CHAINLINK_CWD = Path("/Users/timkellogg/code/sandbox/goat-herder")

DEFAULT_REPOS = {
    "vera-prism": Path("/Users/timkellogg/code/sandbox/goat-herder/vera-prism"),
    "data-lake-ml": Path("/Users/timkellogg/code/icertis/data-lake-ml"),
    "herder": Path("/Users/timkellogg/code/sandbox/goat-herder/herder"),
    "open-strix": Path("/Users/timkellogg/code/sandbox/goat-herder/open-strix"),
}


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Runtime settings for the worker loop."""

    chainlink_cwd: Path = DEFAULT_CHAINLINK_CWD
    poll_interval_seconds: int = 30
    codex_poll_seconds: int = 10
    max_codex_wait_seconds: int = 1800
    agent_id: str = "backlog-worker"
    rules_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Full worker configuration."""

    worker: WorkerSettings
    repos: dict[str, Path]
    source_path: Path | None = None


def default_config() -> AppConfig:
    """Return the built-in defaults."""
    return AppConfig(
        worker=WorkerSettings(),
        repos=dict(DEFAULT_REPOS),
        source_path=None,
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config from TOML, or fall back to defaults."""
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    defaults = default_config()
    if not config_path.exists():
        return defaults

    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    worker_raw = _as_dict(raw.get("worker"))
    repos_raw = _as_dict(raw.get("repos"))

    worker = WorkerSettings(
        chainlink_cwd=_as_path(
            worker_raw.get("chainlink_cwd"),
            defaults.worker.chainlink_cwd,
        ),
        poll_interval_seconds=_as_positive_int(
            worker_raw.get("poll_interval_seconds"),
            defaults.worker.poll_interval_seconds,
        ),
        codex_poll_seconds=_as_positive_int(
            worker_raw.get("codex_poll_seconds"),
            defaults.worker.codex_poll_seconds,
        ),
        max_codex_wait_seconds=_as_positive_int(
            worker_raw.get("max_codex_wait_seconds"),
            defaults.worker.max_codex_wait_seconds,
        ),
        agent_id=_as_text(worker_raw.get("agent_id"), defaults.worker.agent_id),
        rules_dir=_as_optional_path(worker_raw.get("rules_dir")),
    )

    repos = dict(defaults.repos)
    for label, raw_path in repos_raw.items():
        clean_label = str(label).strip()
        if not clean_label:
            continue
        clean_path = str(raw_path).strip()
        if not clean_path:
            continue
        repos[clean_label] = Path(clean_path).expanduser()

    return AppConfig(worker=worker, repos=repos, source_path=config_path)


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def _as_positive_int(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"expected positive integer, got {value!r}")
    return parsed


def _as_text(value: object, default: str) -> str:
    if value in (None, ""):
        return default
    text = str(value).strip()
    return text or default


def _as_path(value: object, default: Path) -> Path:
    if value in (None, ""):
        return default
    return Path(str(value).strip()).expanduser()


def _as_optional_path(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value).strip()).expanduser()
