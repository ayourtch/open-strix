"""Tests for the chainlink worker config loader."""

from __future__ import annotations

from pathlib import Path
import sys


SKILL_DIR = Path(__file__).resolve().parents[1] / "optional-skills" / "chainlink-worker"
sys.path.insert(0, str(SKILL_DIR))
try:
    import config as chainlink_worker_config
finally:
    sys.path.pop(0)


def test_load_config_defaults_when_missing(tmp_path: Path, monkeypatch) -> None:
    missing_path = tmp_path / "missing.toml"
    monkeypatch.setattr(chainlink_worker_config, "DEFAULT_CONFIG_PATH", missing_path)

    loaded = chainlink_worker_config.load_config()

    assert loaded.worker.chainlink_cwd == chainlink_worker_config.DEFAULT_CHAINLINK_CWD
    assert loaded.worker.poll_interval_seconds == 30
    assert loaded.worker.codex_poll_seconds == 10
    assert loaded.worker.max_codex_wait_seconds == 1800
    assert loaded.worker.agent_id == "backlog-worker"
    assert loaded.worker.rules_dir is None
    assert loaded.repos["open-strix"] == Path("/Users/timkellogg/code/sandbox/goat-herder/open-strix")
    assert loaded.source_path is None


def test_load_config_merges_custom_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[worker]
chainlink_cwd = "~/work/goat-herder"
poll_interval_seconds = 45
codex_poll_seconds = 7
max_codex_wait_seconds = 1200
agent_id = "night-shift"
rules_dir = "~/rules"

[repos]
open-strix = "~/src/open-strix"
new-repo = "~/src/new-repo"
""".strip(),
        encoding="utf-8",
    )

    loaded = chainlink_worker_config.load_config(config_path)

    assert loaded.source_path == config_path
    assert loaded.worker.chainlink_cwd == Path("~/work/goat-herder").expanduser()
    assert loaded.worker.poll_interval_seconds == 45
    assert loaded.worker.codex_poll_seconds == 7
    assert loaded.worker.max_codex_wait_seconds == 1200
    assert loaded.worker.agent_id == "night-shift"
    assert loaded.worker.rules_dir == Path("~/rules").expanduser()
    assert loaded.repos["open-strix"] == Path("~/src/open-strix").expanduser()
    assert loaded.repos["new-repo"] == Path("~/src/new-repo").expanduser()
    assert loaded.repos["vera-prism"] == chainlink_worker_config.DEFAULT_REPOS["vera-prism"]


def test_empty_rules_dir_becomes_none(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[worker]
rules_dir = ""
""".strip(),
        encoding="utf-8",
    )

    loaded = chainlink_worker_config.load_config(config_path)

    assert loaded.worker.rules_dir is None
