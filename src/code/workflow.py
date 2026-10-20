"""Confirmed, project-bounded code execution for Slack requests.

The language model may propose a structured plan, but this module owns every
side effect: project boundaries, the small command allowlist, pending-plan
expiry, and the final verification report.  Local ``AGENTS.md`` and trusted
skills are context for planning only; neither can relax these checks.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from src.code.agent import PlanResponseFormatError
from src.code.autopilot import Autopilot
from src.code.context import AppliedSkill, ProjectContext, ProjectContextLoader, SkillRegistry
from src.code.direct_edit import DirectEditor
from src.code.edit_scope import (
    PATH_IN_REQUEST,
    PlanRequest,
    drop_unauthorized_protected_steps,
    interpret_plan_request,
)
from src.code.executor import AwaitingProjectStore, PendingPlanStatus, PendingPlanStore
from src.code.markers import (
    CODE_INTEGRATION_MARKERS,
    CODE_MARKERS,
    CODE_PLANNING_MARKERS,
    PLAN_CONTINUATION_MARKERS,
    is_plan_follow_work_request,
)
from src.code.plan import (
    DEFAULT_MAX_AUTOPILOT_ITEMS,
    ExecutionPlan,
    ExistingFile,
    _create_plan_with_format_retry,
    _is_source_or_test_path,
    _linear_planning_sources,
    _preview_diffs,
    _read_existing_files,
    _validate_plan,
    render_plan_preview,
)
from src.code.plan_execution import NO_ROLLBACK_WARNING, PlanExecutor, with_thread_ids
from src.code.plan_review import PlanReviewDialogue
from src.code.tooluse import CommandResult, ProjectExecutionTools, VerificationStatus, _project_path
from src.code.verifier import (
    ExecutionResult,
)
from src.code.workspace import ThreadWorkspaces, WorkspaceError
from src.core.plan_first import (
    PlanAnswer,
)
from src.core.plan_guard import (
    is_protected_meta_path,
    is_risky_path,
    is_secret_path,
)
from src.core.project_resolver import (
    AmbiguousProject,
    InvalidProjectName,
    ProjectResolver,
    UnknownProject,
)
from src.core.run_state import CodeWorkState, CodeWorkStateStore
from src.slack.request_classifier import classify_request, find_project_name_candidates
from src.slack.request_router import RequestIntent, RequestRouter
from src.slack.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)


_SLACK_MENTION = re.compile(r"<@[^>]+>")


_AMBIGUOUS_PROJECT = "같은 Git 저장소 이름의 로컬 프로젝트가 여러 개입니다. 폴더명을 지정해 주세요."


_CONFIRMATION = re.compile(r"^(실행|실행해줘|실행합니다)$")


_CANCELLATION = re.compile(r"^(취소|취소해줘|취소합니다)$")


_DISCARD = re.compile(r"^(폐기|폐기해줘|폐기합니다)$")


# ExecutionWorkflow's plan-building gate intentionally uses narrower test
# phrasing than RequestRouter's CODE_MARKERS (bare "테스트"/"test"/"코드" are
# excluded here) — a pre-existing behavior this shared-vocabulary refactor
# preserves rather than widens.
_EXECUTION_EXCLUDED_BROAD_MARKERS = ("테스트", "test", "코드")


_EXECUTION_ONLY_TEST_MARKERS = ("테스트 실행", "test 실행")


_EXECUTION_MARKERS = (
    tuple(marker for marker in CODE_MARKERS if marker not in _EXECUTION_EXCLUDED_BROAD_MARKERS)
    + _EXECUTION_ONLY_TEST_MARKERS
    + CODE_PLANNING_MARKERS
    + CODE_INTEGRATION_MARKERS
    + PLAN_CONTINUATION_MARKERS
)


def _is_guarded_path(path: str) -> bool:
    return is_protected_meta_path(path) or is_risky_path(path) or is_secret_path(path)


def _unseen_existing_files(
    root: Path, affected_files: list[str], existing_files: list[ExistingFile]
) -> list[str]:
    """Affected paths that exist on disk but whose content the planner never saw.
    Protected, code-executing and secret paths are left to plan validation, which
    rejects them outright instead of replanning around them."""
    seen: set[Path] = set()
    for item in existing_files:
        if item.content is not None:
            try:
                seen.add(_project_path(root, item.relative_path))
            except ValueError:
                continue
    unseen: list[str] = []
    for path in affected_files:
        try:
            target = _project_path(root, path)
        except ValueError:
            continue
        if _is_guarded_path(path):
            continue
        if target.exists() and target not in seen:
            unseen.append(path)
    return unseen


class ExecutionWorkflow:
    """Creates a preview, then applies only its confirmed bounded plan."""

    def __init__(
        self,
        *,
        project_resolver: ProjectResolver,
        context_loader: ProjectContextLoader | None = None,
        skill_registry: SkillRegistry | None = None,
        plan_store: PendingPlanStore | None = None,
        awaiting_project_store: AwaitingProjectStore | None = None,
        code_work_state_store: CodeWorkStateStore | None = None,
        max_autopilot_items: int = DEFAULT_MAX_AUTOPILOT_ITEMS,
        workspaces: ThreadWorkspaces | None = None,
        code_work_mode: str = "plan",
    ) -> None:
        self.workspaces = workspaces
        self.code_work_mode = code_work_mode
        self._plan_reviews = PlanReviewDialogue(workspaces)
        self._last_results: dict[tuple[str, str], ExecutionResult] = {}
        self.project_resolver = project_resolver
        self.context_loader = context_loader or ProjectContextLoader(project_resolver)
        self.skill_registry = skill_registry or SkillRegistry()
        self.plan_store = plan_store or PendingPlanStore()
        self.awaiting_project_store = awaiting_project_store or AwaitingProjectStore()
        self.code_work_state_store = code_work_state_store or CodeWorkStateStore()
        self._autopilot = Autopilot(
            project_resolver=project_resolver,
            state_store=self.code_work_state_store,
            last_results=self._last_results,
            max_items=max_autopilot_items,
            process=lambda **kwargs: self.process(**kwargs),
        )

    def process(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
        agent: object,
        trusted_code_work: bool = False,
        auto_execute: bool = False,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        command_text = _SLACK_MENTION.sub("", text).strip()
        if _CONFIRMATION.fullmatch(command_text):
            return self._plan_executor().execute(channel_id, thread_ts, agent)
        if _CANCELLATION.fullmatch(command_text):
            if self._autopilot.is_active(channel_id, thread_ts):
                self._autopilot.cancel(channel_id, thread_ts)
                return "⛔ 자동 진행 취소를 요청했습니다. 현재 항목이 끝나면 중단합니다."
            return self._cancel_pending(channel_id, thread_ts)
        if _DISCARD.fullmatch(command_text):
            return self._discard(channel_id, thread_ts)
        if RequestRouter().route(command_text).intent in {
            RequestIntent.LINEAR_READ,
            RequestIntent.LINEAR_MUTATION,
        }:
            return None
        # A thread with a plan already pending can be redirected by naming a
        # different file alone — no modification verb required. Without a
        # pending plan, a bare file mention still isn't an execution request.
        has_pending_plan = self.plan_store.has_pending(channel_id, thread_ts)
        redirect_with_pending_plan = has_pending_plan and PATH_IN_REQUEST.search(command_text)
        # A stalled request awaiting only a project name resumes on the very
        # next message — that reply (e.g. just a bare project name) has no
        # code-work marker of its own and would otherwise be judged unrelated.
        awaiting_project = self.awaiting_project_store.has_pending(channel_id, thread_ts)
        autopilot = auto_execute or _is_autopilot_request(command_text)
        if autopilot and not auto_execute and self._autopilot.is_active(channel_id, thread_ts):
            return (
                "⏳ 이미 자동 진행 중입니다. 끝나면 결과를 알려드립니다. "
                "중단하려면 `취소`라고 보내 주세요."
            )
        if (
            not trusted_code_work
            and not _is_execution_request(command_text)
            and not autopilot
            and not redirect_with_pending_plan
            and not awaiting_project
        ):
            if has_pending_plan:
                return self._pending_plan_clarification(channel_id, thread_ts)
            return None

        contextual_text = self._with_thread_context(
            channel_id, thread_ts, command_text, thread_context
        )
        project_name, project_reply = self._target_project(
            channel_id, thread_ts, command_text, contextual_text
        )
        if project_name is None:
            return project_reply
        if autopilot and not auto_execute:
            return self._autopilot.run(
                project_name, channel_id, thread_ts, thread_context, agent, on_progress
            )
        edit_root = None if autopilot else self._direct_edit_root(agent, project_name)
        if edit_root is not None:
            return self._direct_editor().edit(
                edit_root,
                project_name,
                channel_id,
                thread_ts,
                command_text,
                contextual_text,
                agent,
                thread_context.user_messages(channel_id, thread_ts),
            )
        return self._plan_and_preview(
            agent=agent,
            project_name=project_name,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_text=command_text,
            contextual_text=contextual_text,
            autopilot=autopilot,
        )

    def _target_project(
        self, channel_id: str, thread_ts: str, command_text: str, contextual_text: str
    ) -> tuple[str | None, str | None]:
        """The project this request is about, or `None` plus the reply that asks for it."""
        # An explicit project name in this exact message always wins. Only
        # when this message names none do we fall back to thread history —
        # and only then can more than one prior project create real
        # ambiguity worth asking about instead of silently guessing.
        explicit_project_name = self._project_name(command_text)
        if explicit_project_name is None:
            candidates = find_project_name_candidates(contextual_text, self.project_resolver)
            if len(candidates) > 1:
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                candidate_list = ", ".join(f"`{name}`" for name in candidates)
                return None, (
                    "스레드에 여러 프로젝트가 섞여 있어 대상을 정하지 못했습니다: "
                    f"{candidate_list}. 진행할 프로젝트명을 알려주세요."
                )
        project_name = explicit_project_name or self._project_name(contextual_text)
        if project_name is None:
            self.awaiting_project_store.mark(channel_id, thread_ts)
            return None, "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."
        self.awaiting_project_store.clear(channel_id, thread_ts)
        return project_name, None

    def _investigate(
        self,
        agent: object,
        channel_id: str,
        thread_ts: str,
        contextual_text: str,
        request: PlanRequest,
    ) -> tuple[list[str], list[str], str | None]:
        """What the agent's read-only investigation found: the source files it read, the
        planning target paths including them, and a reply when planning cannot go on."""
        investigator = getattr(agent, "analyze", None)
        # An explicitly named file is already a bounded, first-party source
        # for the planner. Do not make a plan-file creation/edit request fail
        # merely because the optional exploratory LLM chose not to read it.
        target_paths = list(request.target_paths)
        source_paths: list[str] = []
        # A directly named file normally supplies bounded planning context.
        # Following an existing plan is different: the plan is specification,
        # not implementation evidence, so related source and test files must
        # still be investigated before the model can propose a code change.
        investigate_code = any(
            _is_source_or_test_path(path) for path in request.explicit_target_paths
        )
        if callable(investigator) and (
            not request.explicit_target_paths or request.plan_follow_request or investigate_code
        ):
            try:
                investigation = investigator(
                    contextual_text, channel_id=channel_id, thread_ts=thread_ts
                )
            except AmbiguousProject:
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                return [], target_paths, _AMBIGUOUS_PROJECT
            except (InvalidProjectName, UnknownProject):
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                return [], target_paths, "대상 프로젝트를 찾을 수 없습니다."
            source_paths = [
                source
                for source in getattr(investigation, "sources", [])
                if isinstance(source, str)
            ]
            if not source_paths and not request.plan_document_is_evidence:
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                scope = (
                    getattr(investigation, "summary", "") or "관련 파일을 찾지 못했습니다"
                ).rstrip(".")
                ask = "관련 모듈명 또는 파일을 지정해 주세요."
                tail = "" if "지정해" in scope else f" {ask}"
                return (
                    [],
                    target_paths,
                    (
                        f"`src/`·`tests/`에서 코드 근거를 찾지 못해 실행 계획을 만들 수 없습니다. "
                        f"탐색 결과: {scope}.{tail}"
                    ),
                )
            # Preserve an explicitly named target first, then add only actual
            # files the investigator read. `_read_existing_files` and the
            # context loader validate each path against the project boundary.
            target_paths = list(dict.fromkeys([*target_paths, *source_paths]))
        return source_paths, target_paths, None

    def _plan_and_preview(
        self,
        *,
        agent: object,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        command_text: str,
        contextual_text: str,
        autopilot: bool,
    ) -> str:
        """Investigate, plan and validate a code change, then either run it (autopilot)
        or store it as the thread's pending plan and return the preview to confirm."""
        creator = getattr(agent, "create_execution_plan", None)
        if not callable(creator):
            return "코드 실행 계획에는 LLM 코드 에이전트가 필요합니다."
        creator = with_thread_ids(creator, channel_id, thread_ts)
        # Later plans in a thread build on its worktree, not on the original checkout.
        workspace_root = (
            self.workspaces.existing(project_name, channel_id, thread_ts)
            if self.workspaces is not None and not autopilot
            else None
        )
        self.code_work_state_store.set(
            channel_id=channel_id,
            thread_ts=thread_ts,
            state=CodeWorkState.DISCOVERING,
        )

        request = interpret_plan_request(command_text, contextual_text)
        target_paths = request.target_paths
        plan_follow_request = request.plan_follow_request
        plan_document_is_evidence = request.plan_document_is_evidence
        linear_plan_document = request.linear_plan_document
        user_writable_paths = request.user_writable_paths
        source_paths, target_paths, investigation_reply = self._investigate(
            agent, channel_id, thread_ts, contextual_text, request
        )
        if investigation_reply is not None:
            return investigation_reply
        try:
            if linear_plan_document:
                project_root = self.project_resolver.resolve(project_name)
                related_paths = _linear_planning_sources(project_root)
                target_paths = list(dict.fromkeys([*target_paths, *related_paths]))
            context = self.context_loader.load(
                project_name, target_paths=target_paths, root=workspace_root
            )
            skills = self.skill_registry.select(intent=command_text, project_root=context.root)
            existing_files = _read_existing_files(context.root, target_paths)
            plan = _create_plan_with_format_retry(
                creator, contextual_text, context, skills, existing_files
            )
            unseen = _unseen_existing_files(context.root, plan.affected_files, existing_files)
            if unseen:
                # The model picked existing files the planner was never shown.
                # Show them and ask once more so nothing is rewritten blind.
                target_paths = list(dict.fromkeys([*target_paths, *unseen]))
                existing_files = _read_existing_files(context.root, target_paths)
                context = self.context_loader.load(
                    project_name, target_paths=target_paths, root=workspace_root
                )
                plan = _create_plan_with_format_retry(
                    creator, contextual_text, context, skills, existing_files
                )
                still_unseen = _unseen_existing_files(
                    context.root, plan.affected_files, existing_files
                )
                if still_unseen:
                    listed = ", ".join(f"`{path}`" for path in still_unseen)
                    raise ValueError(
                        f"계획이 내용을 보지 못한 기존 파일을 수정하려 합니다: {listed}"
                    )
            plan = drop_unauthorized_protected_steps(plan, user_writable_paths)
            _validate_plan(
                plan,
                project_name,
                user_writable_paths,
                project_root=context.root,
                evidence_paths=source_paths,
                require_code_evidence=plan_follow_request and not plan_document_is_evidence,
                restrict_new_files_to_code_roots=plan_follow_request,
            )
            if linear_plan_document and not set(plan.affected_files).issubset(
                set(user_writable_paths)
            ):
                raise ValueError("계획 문서 요청은 명시한 plan.md만 변경할 수 있습니다")
            detailed_context = self.context_loader.load(
                project_name, target_paths=plan.affected_files, root=workspace_root
            )
        except AmbiguousProject:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return _AMBIGUOUS_PROJECT
        except (InvalidProjectName, UnknownProject):
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "대상 프로젝트를 찾을 수 없습니다."
        except PlanResponseFormatError:
            logger.warning("execution_plan_format_failed project=%s attempts=2", project_name)
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return (
                "모델이 두 번 연속 실행 계획 JSON을 올바르게 반환하지 못했습니다. "
                "파일은 변경하지 않았습니다. 요청을 더 짧게 나누어 다시 시도해 주세요."
            )
        except (OSError, ValueError) as exc:
            logger.warning("execution_plan_rejected project=%s reason=%s", project_name, exc)
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"실행 계획을 만들지 못했습니다: {exc}"
        except Exception as exc:
            logger.exception("execution_plan_failed project=%s", project_name)
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"실행 계획 생성에 실패했습니다: {exc}"

        return self._store_and_preview(
            agent=agent,
            plan=plan,
            project_name=project_name,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_text=command_text,
            detailed_context=detailed_context,
            skills=skills,
            existing_files=existing_files,
            autopilot=autopilot,
        )

    def _store_and_preview(
        self,
        *,
        agent: object,
        plan: ExecutionPlan,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        command_text: str,
        detailed_context: ProjectContext,
        skills: list[AppliedSkill],
        existing_files: list[ExistingFile],
        autopilot: bool,
    ) -> str:
        """Record what was applied to the plan, then run it (autopilot) or store it as the
        thread's pending plan and return the preview to confirm."""

        if not (detailed_context.root / "pyproject.toml").exists():
            # The fixed checks are Python tooling; on any other stack they would
            # only fail for reasons unrelated to the change.
            plan = replace(plan, verification_commands=[])
        plan = replace(
            plan,
            applied_agents=[
                instruction.relative_path for instruction in detailed_context.instructions
            ],
            applied_skills=[f"{skill.name}@{skill.version}" for skill in skills],
        )
        self.plan_store.put(channel_id, thread_ts, plan, request=command_text)
        if autopilot:
            return self._plan_executor().execute(channel_id, thread_ts, agent, isolate=False)
        self.code_work_state_store.set(
            channel_id=channel_id,
            thread_ts=thread_ts,
            state=CodeWorkState.AWAITING_CONFIRMATION,
        )
        logger.info(
            "execution_plan_ready project=%s files=%s checks=%s risk=%s skills=%s",
            project_name,
            plan.affected_files,
            plan.verification_commands,
            plan.risk,
            plan.applied_skills,
        )
        diffs = _preview_diffs(detailed_context.root, plan.steps)
        evidence_paths = [item.relative_path for item in existing_files if item.content is not None]
        preview = render_plan_preview(
            plan, detailed_context.git, diffs, evidence_paths=evidence_paths
        )
        if self.workspaces is not None and not self.workspaces.can_isolate(detailed_context.root):
            preview += f"\n⚠️ {NO_ROLLBACK_WARNING}"
        return preview

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        return self.plan_store.has_pending(channel_id, thread_ts)

    def has_open_conversation(self, channel_id: str, thread_ts: str) -> bool:
        """True when this thread has an unfinished code-work exchange — a
        full plan awaiting confirmation, or a request stalled only for a
        project name — so the coordinator should give `process()` a second
        look at an otherwise-unclassified message before general analysis."""
        return self.plan_store.has_pending(
            channel_id, thread_ts
        ) or self.awaiting_project_store.has_pending(channel_id, thread_ts)

    def _pending_plan_clarification(self, channel_id: str, thread_ts: str) -> str | None:
        """A fixed, deterministic nudge — never an LLM call — so this never
        varies in wording or risks an open-ended clarification loop."""
        plan = self.plan_store.peek(channel_id, thread_ts)
        if plan is None:
            return None
        files = ", ".join(f"`{path}`" for path in plan.affected_files) or "지정된 파일 없음"
        return (
            f"보류 중인 실행 계획이 있습니다: {plan.goal} (대상: {files}).\n"
            "이대로 진행하려면 `실행`, 취소하려면 `취소`라고 답해주세요. "
            "다른 파일을 대상으로 하려면 파일 경로를 알려주세요."
        )

    def _project_name(self, contextual_text: str) -> str | None:
        request = classify_request(contextual_text, self.project_resolver)
        return request.project_name

    @staticmethod
    def _with_thread_context(
        channel_id: str, thread_ts: str, text: str, thread_context: ThreadContextStore
    ) -> str:
        prior = thread_context.read(channel_id, thread_ts)
        return f"스레드 맥락:\n{prior}\n현재 요청:\n{text}" if prior else text

    def _cancel_pending(self, channel_id: str, thread_ts: str) -> str | None:
        status = self.plan_store.cancel(channel_id, thread_ts)
        if status is PendingPlanStatus.READY:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.CANCELLED)
            logger.info("execution_plan_cancelled channel=%s thread=%s", channel_id, thread_ts)
            return "보류된 실행 계획을 취소했습니다. 파일은 변경하지 않았습니다."
        if status is PendingPlanStatus.EXPIRED:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "보류된 실행 계획이 만료되었습니다. 파일은 변경하지 않았습니다."
        return None

    def _plan_executor(self) -> PlanExecutor:
        return PlanExecutor(
            plan_store=self.plan_store,
            context_loader=self.context_loader,
            workspaces=self.workspaces,
            set_state=self._set_code_work_state,
            run_verifications=self._run_verifications,
            last_results=self._last_results,
            record_trace=self._record_execution_trace,
        )

    def _direct_editor(self) -> DirectEditor:
        assert self.workspaces is not None
        return DirectEditor(
            workspaces=self.workspaces,
            set_state=self._set_code_work_state,
            run_verifications=self._run_verifications,
            last_results=self._last_results,
            record_trace=self._record_execution_trace,
        )

    def _direct_edit_root(self, agent: object, project_name: str) -> Path | None:
        """The project root when the agent should edit a worktree directly: edit mode is
        on, the agent can edit, and the project can be isolated. Otherwise `None`."""
        if self.code_work_mode != "edit" or self.workspaces is None:
            return None
        if not getattr(agent, "supports_edit", False):
            return None
        try:
            root = self.project_resolver.resolve(project_name).resolve()
        except (InvalidProjectName, UnknownProject, AmbiguousProject):
            return None
        return root if self.workspaces.can_isolate(root) else None

    def confirm_plan(self, channel_id: str, thread_ts: str, area: str) -> str:
        return self._plan_reviews.confirm_plan(channel_id, thread_ts, area)

    def has_plan_review(self, channel_id: str, thread_ts: str) -> bool:
        return self._plan_reviews.has_plan_review(channel_id, thread_ts)

    def answer_plan_review(self, channel_id: str, thread_ts: str, answer: PlanAnswer) -> str:
        return self._plan_reviews.answer_plan_review(channel_id, thread_ts, answer)

    def _discard(self, channel_id: str, thread_ts: str) -> str:
        none_message = "폐기할 작업 브랜치(worktree)가 없습니다."
        if self.workspaces is None:
            return none_message
        try:
            removed = self.workspaces.discard_thread(channel_id, thread_ts)
        except WorkspaceError as exc:
            return f"❌ {exc}"
        self.plan_store.cancel(channel_id, thread_ts)
        if not removed:
            return none_message
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.CANCELLED)
        return (
            "🗑️ 이 스레드의 작업 브랜치와 worktree를 폐기했습니다. "
            "원본 체크아웃은 변경하지 않았습니다."
        )

    def _run_verifications(
        self,
        channel_id: str,
        thread_ts: str,
        plan: ExecutionPlan,
        tools: ProjectExecutionTools,
        changed_files: list[str],
    ) -> list[CommandResult]:
        """Run only plan-approved checks and turn runner errors into observations."""
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.VERIFYING)
        checks: list[CommandResult] = []
        for name in plan.verification_commands:
            try:
                checks.append(tools.run_check(name))
            except subprocess.TimeoutExpired:
                checks.append(
                    CommandResult(name, False, "검증 시간 초과", VerificationStatus.TIMED_OUT)
                )
                break
            except OSError as exc:
                logger.warning(
                    "verification_failed project=%s check=%s reason=%s applied=%s",
                    plan.project_name,
                    name,
                    exc,
                    changed_files,
                )
                checks.append(
                    CommandResult(
                        name,
                        False,
                        f"검증 도구를 시작할 수 없습니다 ({type(exc).__name__}).",
                        VerificationStatus.ENVIRONMENT_ERROR,
                    )
                )
                break
        return checks

    def _set_code_work_state(self, channel_id: str, thread_ts: str, state: CodeWorkState) -> None:
        self.code_work_state_store.set(
            channel_id=channel_id,
            thread_ts=thread_ts,
            state=state,
        )

    @staticmethod
    def _record_execution_trace(
        agent: object,
        channel_id: str,
        thread_ts: str,
        *,
        repair_attempts: int,
        termination_reason: str,
    ) -> None:
        trace_store = getattr(agent, "thread_trace_store", None)
        trace = trace_store.get(channel_id, thread_ts) if trace_store is not None else None
        if trace is not None:
            trace.record_code_work(
                repair_attempts=repair_attempts,
                termination_reason=termination_reason,
            )


def _is_execution_request(text: str) -> bool:
    normalized = text.casefold()
    return is_plan_follow_work_request(normalized) or any(
        marker in normalized for marker in _EXECUTION_MARKERS
    )


def _is_autopilot_request(text: str) -> bool:
    return "끝까지 진행" in text
