"""One direct-edit run in a thread worktree (plan.md Phases 18 and 28).

The agent edits files itself; this code owns everything around it: what may be written,
the authoritative post-edit review, verification, bounded auto-repair and the commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from src.code.agent import AnalysisAgentError
from src.code.edit_scope import (
    PlanFirstLimits,
    area_plan_for_named_plan,
    edit_applied_guidance,
    edit_named_paths_in_thread,
    plan_first_limits,
)
from src.code.plan import (
    DEFAULT_MAX_AUTO_REPAIRS,
    ExecutionPlan,
    ExecutionRisk,
    _render_diff_excerpt,
)
from src.code.tooluse import CommandResult, ProjectExecutionTools
from src.code.verifier import (
    ExecutionResult,
    _remaining_risks,
    _verification_fingerprint,
    render_execution_result,
    review_worktree,
)
from src.code.workspace import ThreadWorkspaces, WorkspaceError
from src.core.harness import load_file
from src.core.read_only import wants_read_only
from src.core.run_state import CodeWorkState

logger = logging.getLogger(__name__)

_MAX_ANSWER_CHARS = 3500  # stays under Slack's message length limit
_MAX_AGENT_REPORT_CHARS = 1500  # the agent's own summary beside the diffs


class DirectEditor:
    """Runs the edit agent in a thread worktree; the collaborators are injected so the
    workflow keeps the shared state (code-work state, last results, traces)."""

    def __init__(
        self,
        *,
        workspaces: ThreadWorkspaces,
        set_state: Callable[[str, str, CodeWorkState], None],
        run_verifications: Callable[..., list[CommandResult]],
        last_results: dict[tuple[str, str], ExecutionResult],
        record_trace: Callable[..., None],
    ) -> None:
        self.workspaces = workspaces
        self._set_state = set_state
        self._run_verifications = run_verifications
        self._last_results = last_results
        self._record_trace = record_trace

    def edit(
        self,
        root: Path,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        command_text: str,
        contextual_text: str,
        agent: object,
        earlier_user_messages: Sequence[str] = (),
    ) -> str:
        from src.code.prompts import build_edit_prompt, build_edit_repair_prompt

        workspaces = self.workspaces
        edit = getattr(agent, "edit_code")  # noqa: B009 - checked by ExecutionWorkflow._direct_edit_root
        named = frozenset(edit_named_paths_in_thread(command_text, earlier_user_messages))
        self._set_state(channel_id, thread_ts, CodeWorkState.IMPLEMENTING)
        try:
            worktree = workspaces.ensure(root, project_name, channel_id, thread_ts)
        except WorkspaceError as exc:
            self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"❌ 실행하지 못했습니다. 파일은 변경하지 않았습니다.\n{exc}"
        if worktree is None:
            self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "❌ 이 프로젝트는 worktree로 격리할 수 없어 직접 편집하지 않았습니다."

        # A message that forbids edits wins over every stage: nothing is writable this run.
        plan_limits = (
            PlanFirstLimits(write_roots=(), stage="readonly")
            if wants_read_only(command_text)
            else plan_first_limits(worktree, command_text, earlier_user_messages)
        )
        write_roots = plan_limits.write_roots
        named = named | plan_limits.named
        named = named | area_plan_for_named_plan(
            worktree, named, [*earlier_user_messages, command_text]
        )
        plan_first = plan_limits.stage is not None
        # Only a project that opted in to plan-first gets a write limit; the agent is given
        # the keyword only then, so agents that predate it keep working unchanged.
        limits = {} if write_roots is None else {"write_roots": write_roots}

        def run_edit(prompt: str) -> str:
            return str(
                edit(
                    prompt,
                    worktree,
                    project_name=project_name,
                    named_paths=named,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    **limits,
                )
            )

        goal = command_text.strip().splitlines()[0] if command_text.strip() else "코드 작업"
        try:
            agent_text = run_edit(
                build_edit_prompt(
                    contextual_text,
                    named,
                    stage=plan_limits.stage,
                    area=plan_limits.area,
                    plan_rules=load_file("plan-docs.md") if plan_limits.stage == "planning" else "",
                )
            )
        except AnalysisAgentError as exc:
            return self._edit_interrupted(channel_id, thread_ts, exc)
        review = review_worktree(
            workspaces.git,
            worktree,
            named_paths=named,
            write_roots=write_roots,
            plan_first=plan_first,
        )
        if not review.ok:
            return self._edit_rejected(project_name, channel_id, thread_ts, review.violations)
        if not review.changed_files:
            self._set_state(channel_id, thread_ts, CodeWorkState.IDLE)
            # Nothing to review: the run was a question about the code, so its reply is the
            # answer, not a report about an edit that never happened.
            return (
                agent_text.strip()[:_MAX_ANSWER_CHARS]
                or "코드 에이전트가 파일을 변경하지 않았고 답변도 비어 있습니다."
            )

        applied_agents, applied_skills = edit_applied_guidance(worktree)
        plan = ExecutionPlan(
            goal=goal,
            project_name=project_name,
            affected_files=list(review.changed_files),
            steps=[],
            verification_commands=["run_tests"] if (worktree / "pyproject.toml").exists() else [],
            risk=ExecutionRisk.MODIFY,
            applied_agents=applied_agents,
            applied_skills=applied_skills,
        )
        tools = ProjectExecutionTools(worktree, review.changed_files)
        checks = self._run_verifications(
            channel_id, thread_ts, plan, tools, list(review.changed_files)
        )
        repair_attempts = 0
        repair_failure_reason: str | None = None
        failure_fingerprints = {_verification_fingerprint(checks)}
        while not all(check.success for check in checks):
            if repair_attempts >= DEFAULT_MAX_AUTO_REPAIRS:
                repair_failure_reason = "자동 복구 한도 초과"
                break
            repair_attempts += 1
            self._set_state(channel_id, thread_ts, CodeWorkState.REPAIRING)
            try:
                run_edit(build_edit_repair_prompt(contextual_text, review.changed_files, checks))
            except AnalysisAgentError as exc:
                return self._edit_interrupted(channel_id, thread_ts, exc)
            review = review_worktree(
                workspaces.git,
                worktree,
                named_paths=named,
                write_roots=write_roots,
                plan_first=plan_first,
            )
            if not review.ok:
                return self._edit_rejected(project_name, channel_id, thread_ts, review.violations)
            plan = replace(plan, affected_files=list(review.changed_files))
            tools = ProjectExecutionTools(worktree, review.changed_files)
            checks = self._run_verifications(
                channel_id, thread_ts, plan, tools, list(review.changed_files)
            )
            fingerprint = _verification_fingerprint(checks)
            if not all(check.success for check in checks) and fingerprint in failure_fingerprints:
                repair_failure_reason = "동일한 검증 실패가 반복되었습니다"
                break
            failure_fingerprints.add(fingerprint)

        risks = _remaining_risks(checks)
        try:
            workspaces.commit(worktree, review.changed_files, goal)
            dirty = workspaces.dirty_files(root, review.changed_files)
        except WorkspaceError as exc:
            logger.warning("workspace_commit_failed project=%s", project_name)
            risks.append(f"{exc} worktree의 변경은 커밋되지 않았습니다.")
            dirty = []
        if dirty:
            listed = ", ".join(f"`{path}`" for path in dirty)
            risks.append(
                f"원본 체크아웃에 커밋하지 않은 변경이 있는 파일과 겹칩니다: {listed}. "
                "에이전트는 커밋된 내용(HEAD)을 기준으로 작업했습니다."
            )
        result = ExecutionResult(
            changed_files=list(review.changed_files),
            diffs=list(review.diffs),
            checks=checks,
            remaining_risks=risks,
            repair_attempts=repair_attempts,
            repair_failure_reason=repair_failure_reason,
            workspace_branch=workspaces.branch_name(channel_id, thread_ts),
            workspace_path=str(worktree),
        )
        self._last_results[(channel_id, thread_ts)] = result
        succeeded = all(check.success for check in checks)
        self._set_state(
            channel_id, thread_ts, CodeWorkState.SUCCEEDED if succeeded else CodeWorkState.FAILED
        )
        self._record_trace(
            agent,
            channel_id,
            thread_ts,
            repair_attempts=repair_attempts,
            termination_reason=repair_failure_reason
            or ("verified" if succeeded else "verification_failed"),
        )
        previews = "\n\n".join(
            f"`{path}`:\n```\n{_render_diff_excerpt(diff)}\n```"
            for path, diff in zip(review.changed_files, review.diffs, strict=False)
        )
        report = agent_text.strip()[:_MAX_AGENT_REPORT_CHARS]
        report_block = f"에이전트 보고:\n{report}\n\n" if report else ""
        return f"{render_execution_result(plan, result)}\n\n{report_block}변경 내용:\n{previews}"

    def _edit_interrupted(self, channel_id: str, thread_ts: str, exc: Exception) -> str:
        self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
        return (
            f"❌ 코드 에이전트 편집이 중단됐습니다: {exc}\n"
            "worktree에 일부 변경이 남아 있을 수 있습니다. 버리려면 `폐기`라고 보내세요."
        )

    def _edit_rejected(
        self, project_name: str, channel_id: str, thread_ts: str, violations: list[str]
    ) -> str:
        """The review is the authoritative gate: any violation throws the worktree away."""
        logger.warning("edit_rejected project=%s violations=%d", project_name, len(violations))
        discarded = True
        try:
            self.workspaces.discard_thread(channel_id, thread_ts)
        except WorkspaceError:
            discarded = False
        self._set_state(channel_id, thread_ts, CodeWorkState.FAILED)
        listed = "\n".join(f"- {violation}" for violation in violations)
        outcome = (
            "작업 브랜치와 worktree를 폐기했습니다. 원본 체크아웃은 변경하지 않았습니다."
            if discarded
            else "worktree를 폐기하지 못했습니다. `폐기`라고 보내 정리해 주세요."
        )
        return f"❌ 코드 에이전트의 변경이 안전 검사를 통과하지 못했습니다.\n{listed}\n{outcome}"
