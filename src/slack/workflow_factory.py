"""Builds the code-work `ExecutionWorkflow` from settings, shared by the HTTP app and
Socket Mode so both wire it identically."""

from __future__ import annotations

import logging
from pathlib import Path

from src.code.context import SkillRegistry
from src.code.github import GhCli, run_gh
from src.code.pr_facts import thread_pull_request_facts
from src.code.pr_workflow import PullRequestWorkflow
from src.code.workflow import ExecutionWorkflow
from src.code.workspace import ThreadWorkspaces
from src.core.config import Settings, resolve_code_work_mode
from src.core.project_resolver import ProjectResolver

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


def build_pull_request_workflow(execution: ExecutionWorkflow) -> PullRequestWorkflow | None:
    """The `PR` command works on the same thread worktrees as the code workflow."""
    workspaces = execution.workspaces
    if workspaces is None:
        return None
    gh = GhCli(run_gh)
    return PullRequestWorkflow(
        facts_source=thread_pull_request_facts(workspaces, gh),
        worktree_of=workspaces.thread_worktree,
        git=workspaces.git,
        gh=gh,
    )
