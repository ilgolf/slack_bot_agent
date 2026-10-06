"""build_execution_workflow wires the code-work mode and worktrees from settings."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.config import Settings
from src.workflow_factory import build_execution_workflow


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # Settings reads ./.env; keep the real one out of tests


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        projects_root=str(tmp_path / "projects"),
        worktrees_root=str(tmp_path / "worktrees"),
        **overrides,  # type: ignore[arg-type]
    )


def test_the_workflow_uses_analysis_mode_by_default(tmp_path: Path) -> None:
    workflow = build_execution_workflow(_settings(tmp_path))

    assert workflow.code_work_mode == "analysis"
    assert workflow.workspaces is not None


def test_edit_mode_is_wired_when_configured_and_worktrees_are_usable(tmp_path: Path) -> None:
    workflow = build_execution_workflow(_settings(tmp_path, code_work_mode="edit"))

    assert workflow.code_work_mode == "edit"


def test_edit_mode_falls_back_to_plan_and_logs_why_under_a_dot_claude_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, code_work_mode="edit")
    settings.worktrees_root = str(tmp_path / ".claude" / "worktrees")

    with caplog.at_level(logging.WARNING):
        workflow = build_execution_workflow(settings)

    assert workflow.code_work_mode == "plan"
    assert any(".claude" in record.getMessage() for record in caplog.records)
