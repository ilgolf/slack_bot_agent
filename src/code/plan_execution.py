"""Runs a confirmed, bounded execution plan (plan.md Phases 1-12).

The model proposed the plan and any repair steps; this code applies them inside the project
(or its thread worktree), runs only the plan-approved checks and reports the result.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from src.code.context import ProjectContext, ProjectContextLoader
from src.code.executor import PendingPlanStatus, PendingPlanStore
from src.code.plan import (
    DEFAULT_MAX_AUTO_REPAIRS,
    ExecutionPlan,
    _read_existing_files,
    _repair_fingerprint,
    _validate_repair_steps,
)
from src.code.tooluse import CommandResult, ProjectExecutionTools
from src.code.verifier import (
    ExecutionResult,
    _remaining_risks,
    _verification_fingerprint,
    render_execution_failure,
    render_execution_result,
)
from src.code.workspace import ThreadWorkspaces, WorkspaceError
from src.core.run_state import CodeWorkState

logger = logging.getLogger(__name__)

NO_ROLLBACK_WARNING = "롤백 불가: 이 프로젝트는 worktree로 격리할 수 없어 원본에 직접 적용됩니다."


def with_thread_ids(
    func: Callable[..., Any], channel_id: str, thread_ts: str
) -> Callable[..., Any]:
    """Pass the Slack thread to agents that trace per thread; others are unchanged."""
    if "channel_id" not in inspect.signature(func).parameters:
        return func
    return partial(func, channel_id=channel_id, thread_ts=thread_ts)


class PlanExecutor:
    """Applies the thread's pending plan; shared state stays with the workflow and is
    injected (code-work state, verification runner, last results, traces)."""

    def __init__(
        self,
        *,
        plan_store: PendingPlanStore,
        context_loader: ProjectContextLoader,
        workspaces: ThreadWorkspaces | None,
        set_state: Callable[[str, str, CodeWorkState], None],
        run_verifications: Callable[..., list[CommandResult]],
        last_results: dict[tuple[str, str], ExecutionResult],
        record_trace: Callable[..., None],
    ) -> None:
        self.plan_store = plan_store
        self.context_loader = context_loader
        self.workspaces = workspaces
        self._set_state = set_state
        self._run_verifications = run_verifications
        self._last_results = last_results
        self._record_trace = record_trace

    def _prepare_workspace(
        self, context: ProjectContext, plan: ExecutionPlan, channel_id: str, thread_ts: str
    ) -> tuple[Path | None, str | None]:
        """The thread worktree to write in, or `None` plus a warning when the project
        cannot be isolated."""
        assert self.workspaces is not None
        if not self.workspaces.can_isolate(context.root):
            return None, NO_ROLLBACK_WARNING
        if self.workspaces.existing(plan.project_name, channel_id, thread_ts) is None:
            # A fresh worktree starts from HEAD, so uncommitted edits to approved files
            # in the original would be silently dropped or overwritten.
            self.workspaces.check_clean(context.root, plan.affected_files)
        path = self.workspaces.ensure(context.root, plan.project_name, channel_id, thread_ts)
        return path, (None if path else NO_ROLLBACK_WARNING)

    def execute(
        self, channel_id: str, thread_ts: str, agent: object, *, isolate: bool = True
    ) -> str:
        status, pending = self.plan_store.take(channel_id, thread_ts)
        if status is PendingPlanStatus.EXPIRED:
            self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "보류된 실행 계획이 만료되었습니다. 새로 계획을 만들어 주세요."
        if status is PendingPlanStatus.MISSING or pending is None:
            return "실행할 보류 계획이 없습니다. 먼저 코드 작업 요청을 보내 주세요."

        plan = pending.plan
        self._set_state(channel_id, thread_ts, CodeWorkState.IMPLEMENTING)
        changed_files: list[str] = []
        diffs: list[str] = []
        workspace: Path | None = None
        rollback_warning: str | None = None
        try:
            context = self.context_loader.load(plan.project_name, target_paths=plan.affected_files)
            if isolate and self.workspaces is not None:
                workspace, rollback_warning = self._prepare_workspace(
                    context, plan, channel_id, thread_ts
                )
                if workspace is not None:
                    context = self.context_loader.load(
                        plan.project_name, target_paths=plan.affected_files, root=workspace
                    )
            tools = ProjectExecutionTools(context.root, plan.affected_files)
            for step in plan.steps:
                changed_file, diff = tools.write_file(step.path, step.content)
                changed_files.append(changed_file)
                diffs.append(diff)
        except WorkspaceError as exc:
            logger.warning("workspace_failed project=%s", plan.project_name)
            self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"❌ 실행하지 못했습니다. 파일은 변경하지 않았습니다.\n{exc}"
        except (OSError, ValueError) as exc:
            logger.warning(
                "execution_failed project=%s reason=%s applied=%s",
                plan.project_name,
                exc,
                changed_files,
            )
            self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return render_execution_failure(plan, str(exc), changed_files)

        checks = self._run_verifications(channel_id, thread_ts, plan, tools, changed_files)
        repair_attempts = 0
        repair_failure_reason: str | None = None
        repairer = getattr(agent, "create_repair_steps", None)
        if callable(repairer):
            repairer = with_thread_ids(repairer, channel_id, thread_ts)
        failure_fingerprints = {_verification_fingerprint(checks)}
        repair_fingerprints: set[str] = set()
        while not all(check.success for check in checks) and callable(repairer):
            if repair_attempts >= DEFAULT_MAX_AUTO_REPAIRS:
                repair_failure_reason = "자동 복구 한도 초과"
                break
            repair_attempts += 1
            self._set_state(channel_id, thread_ts, CodeWorkState.REPAIRING)
            try:
                repair_steps = repairer(
                    plan,
                    _read_existing_files(context.root, plan.affected_files),
                    checks,
                )
                _validate_repair_steps(repair_steps, plan.affected_files)
                repair_fingerprint = _repair_fingerprint(repair_steps)
                if repair_fingerprint in repair_fingerprints:
                    repair_failure_reason = "동일한 자동 복구 diff가 반복되었습니다"
                    break
                repair_fingerprints.add(repair_fingerprint)
                for step in repair_steps:
                    changed_file, diff = tools.write_file(step.path, step.content)
                    changed_files.append(changed_file)
                    diffs.append(diff)
            except (OSError, ValueError) as exc:
                logger.warning("repair_failed project=%s reason=%s", plan.project_name, exc)
                repair_failure_reason = str(exc)
                break
            except Exception as exc:
                # A malformed LLM repair response must end this one confirmed
                # run safely; it must never escape the Slack listener.
                logger.warning(
                    "repair_plan_invalid project=%s category=%s",
                    plan.project_name,
                    type(exc).__name__,
                )
                repair_failure_reason = "자동 복구 계획을 해석하지 못했습니다"
                break
            checks = self._run_verifications(channel_id, thread_ts, plan, tools, changed_files)
            failure_fingerprint = _verification_fingerprint(checks)
            if (
                not all(check.success for check in checks)
                and failure_fingerprint in failure_fingerprints
            ):
                repair_failure_reason = "동일한 검증 실패가 반복되었습니다"
                break
            failure_fingerprints.add(failure_fingerprint)

        remaining_risks = _remaining_risks(checks)
        if workspace is not None and self.workspaces is not None:
            try:
                self.workspaces.commit(workspace, plan.affected_files, plan.goal)
            except WorkspaceError as exc:
                logger.warning("workspace_commit_failed project=%s", plan.project_name)
                remaining_risks.append(f"{exc} worktree의 변경은 커밋되지 않았습니다.")
        result = ExecutionResult(
            changed_files=changed_files,
            diffs=diffs,
            checks=checks,
            remaining_risks=remaining_risks,
            repair_attempts=repair_attempts,
            repair_failure_reason=repair_failure_reason,
            workspace_branch=(
                self.workspaces.branch_name(channel_id, thread_ts)
                if workspace is not None and self.workspaces is not None
                else None
            ),
            workspace_path=str(workspace) if workspace is not None else None,
            rollback_warning=rollback_warning,
        )
        self._last_results[(channel_id, thread_ts)] = result
        logger.info(
            "execution_completed project=%s files=%s checks=%s success=%s",
            plan.project_name,
            changed_files,
            [check.name for check in checks],
            all(check.success for check in checks),
        )
        final_state = (
            CodeWorkState.SUCCEEDED
            if all(check.success for check in checks)
            else CodeWorkState.FAILED
        )
        self._set_state(channel_id, thread_ts, final_state)
        self._record_trace(
            agent,
            channel_id,
            thread_ts,
            repair_attempts=repair_attempts,
            termination_reason=(
                repair_failure_reason
                or ("verified" if final_state is CodeWorkState.SUCCEEDED else "verification_failed")
            ),
        )
        return render_execution_result(plan, result)
