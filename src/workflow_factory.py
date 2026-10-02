"""Builds the code-work `ExecutionWorkflow` from settings, shared by the HTTP app and
Socket Mode so both wire it identically."""

from __future__ import annotations

import logging
from pathlib import Path

from src.config import Settings, resolve_code_work_mode
from src.execution_workflow import ExecutionWorkflow, SkillRegistry
from src.project_resolver import ProjectResolver
from src.thread_workspace import ThreadWorkspaces

logger = logging.getLogger(__name__)


def build_execution_workflow(settings: Settings) -> ExecutionWorkflow:
    mode, reason = resolve_code_work_mode(settings)
    if reason is not None:
        logger.warning(reason)
    return ExecutionWorkflow(
        project_resolver=ProjectResolver(root=Path(settings.projects_root).expanduser()),
        skill_registry=SkillRegistry({"codex": "~/.codex/skills"}),
        workspaces=ThreadWorkspaces(Path(settings.worktrees_root).expanduser()),
        code_work_mode=mode,
    )
