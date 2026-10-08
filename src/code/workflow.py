"""Confirmed, project-bounded code execution for Slack requests.

The language model may propose a structured plan, but this module owns every
side effect: project boundaries, the small command allowlist, pending-plan
expiry, and the final verification report.  Local ``AGENTS.md`` and trusted
skills are context for planning only; neither can relax these checks.
"""


from __future__ import annotations

import inspect
import logging
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from src.code.agent import AnalysisAgentError, PlanResponseFormatError
from src.code.context import ProjectContext, ProjectContextLoader, SkillRegistry
from src.code.executor import AwaitingProjectStore, PendingPlanStatus, PendingPlanStore
from src.code.markers import (
    CODE_INTEGRATION_MARKERS,
    CODE_MARKERS,
    CODE_PLANNING_MARKERS,
    PLAN_AUTHORING_MARKERS,
    PLAN_CONTINUATION_MARKERS,
    is_plan_follow_work_request,
)
from src.code.plan import (
    _UNCHECKED_ITEM,
    DEFAULT_MAX_AUTO_REPAIRS,
    DEFAULT_MAX_AUTOPILOT_ITEMS,
    ExecutionPlan,
    ExecutionRisk,
    ExistingFile,
    _create_plan_with_format_retry,
    _is_source_or_test_path,
    _linear_planning_sources,
    _preview_diffs,
    _read_existing_files,
    _render_diff_excerpt,
    _repair_fingerprint,
    _validate_plan,
    _validate_repair_steps,
    next_unchecked_item,
    render_plan_preview,
)
from src.code.tooluse import CommandResult, ProjectExecutionTools, VerificationStatus, _project_path
from src.code.verifier import (
    ExecutionResult,
    _diff_summary,
    _remaining_risks,
    _render_check,
    _verification_fingerprint,
    render_execution_failure,
    render_execution_result,
    review_worktree,
)
from src.code.workspace import ThreadWorkspaces, WorkspaceError
from src.core.harness import load_file
from src.core.plan_first import (
    CONFIRMED,
    MAX_DECISION_CHARS,
    PlanAnswer,
    Question,
    detect_area,
    has_open_questions,
    open_questions,
    parse_status,
    plan_first_enabled,
    plan_path,
    plan_status,
    recommended_choice,
    recommended_decision,
    record_decision,
    wants_bypass,
    with_status,
)
from src.core.plan_guard import (
    is_protected_meta_path,
    is_risky_path,
    is_secret_path,
)
from src.core.project_guidance import guidance_files
from src.core.project_resolver import (
    AmbiguousProject,
    InvalidProjectName,
    ProjectResolver,
    UnknownProject,
)
from src.core.run_state import CodeWorkState, CodeWorkStateStore
from src.core.skill_allowlist import claude_skill_names
from src.slack.request_classifier import classify_request, find_project_name_candidates
from src.slack.request_router import RequestIntent, RequestRouter
from src.slack.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)


_SLACK_MENTION = re.compile(r"<@[^>]+>")


_CONFIRMATION = re.compile(r"^(실행|실행해줘|실행합니다)$")


_CANCELLATION = re.compile(r"^(취소|취소해줘|취소합니다)$")


_DISCARD = re.compile(r"^(폐기|폐기해줘|폐기합니다)$")


_PLAN_REVIEW_TTL_SECONDS = 30 * 60


_NO_PLAN_REVIEW = (
    "진행 중인 기획 검토가 없습니다. `기획 확정 <영역>`을 보내 열린 질문 확인을 시작해 주세요."
)


_NO_ROLLBACK_WARNING = "롤백 불가: 이 프로젝트는 worktree로 격리할 수 없어 원본에 직접 적용됩니다."


# Accept a Korean postposition directly after a filename (``plan.md에``)
# while returning only the project-relative path.
_MAX_ANSWER_CHARS = 3500  # stays under Slack's message length limit


_PATH_IN_REQUEST = re.compile(
    r"(?<!\S)([\w./-]+\.[A-Za-z0-9]+)(?=$|[\s,.:!?…]|[은는이가을를에의])"
)


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


@dataclass
class _PlanReview:
    """An open plan review in one thread: which area, when it started, what was put on hold."""

    area: str
    started: float
    held: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Recorded:
    text: str
    decision: str
    error: str | None = None


def _question_key(question: Question) -> str:
    return f"{question.number}\n{question.text}"


def _with_thread_ids(
    func: Callable[..., Any], channel_id: str, thread_ts: str
) -> Callable[..., Any]:
    """Pass the Slack thread to agents that trace per thread; others are unchanged."""
    if "channel_id" not in inspect.signature(func).parameters:
        return func
    return partial(func, channel_id=channel_id, thread_ts=thread_ts)


def _edit_named_paths(command_text: str) -> list[str]:
    """Files the user named in this message, which the edit agent may touch even if they are
    management or code-executing files; a plan-authoring request names plan.md."""
    follow = _is_plan_follow_request(command_text)
    named = [
        path
        for path in _PATH_IN_REQUEST.findall(command_text)
        if not (follow and is_protected_meta_path(path))
    ]
    if not follow and any(marker in command_text.casefold() for marker in PLAN_AUTHORING_MARKERS):
        named.append("plan.md")
    return list(dict.fromkeys(named))


def _edit_named_paths_in_thread(
    command_text: str, earlier_user_messages: Sequence[str]
) -> list[str]:
    """`_edit_named_paths` plus the management files (`plan.md`, ...) the user themself named
    in earlier messages of this thread, so a follow-up without a file name keeps them
    editable. Only the user's own words count — never bot replies or data the bot read —
    and a plan-follow request keeps `plan.md` read-only whatever was said before."""
    named = _edit_named_paths(command_text)
    if _is_plan_follow_request(command_text):
        return named
    earlier = [
        path
        for message in earlier_user_messages
        for path in _edit_named_paths(message)
        if is_protected_meta_path(path)
    ]
    return list(dict.fromkeys([*named, *earlier]))


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
        self.max_autopilot_items = max_autopilot_items
        self._plan_reviews: dict[tuple[str, str], _PlanReview] = {}
        self._autopilot_active: set[tuple[str, str]] = set()
        self._autopilot_cancelled: set[tuple[str, str]] = set()
        self._last_results: dict[tuple[str, str], ExecutionResult] = {}
        self.project_resolver = project_resolver
        self.context_loader = context_loader or ProjectContextLoader(project_resolver)
        self.skill_registry = skill_registry or SkillRegistry()
        self.plan_store = plan_store or PendingPlanStore()
        self.awaiting_project_store = awaiting_project_store or AwaitingProjectStore()
        self.code_work_state_store = code_work_state_store or CodeWorkStateStore()

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
            return self._execute_pending(channel_id, thread_ts, agent)
        if _CANCELLATION.fullmatch(command_text):
            if (channel_id, thread_ts) in self._autopilot_active:
                self._autopilot_cancelled.add((channel_id, thread_ts))
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
        redirect_with_pending_plan = has_pending_plan and _PATH_IN_REQUEST.search(command_text)
        # A stalled request awaiting only a project name resumes on the very
        # next message — that reply (e.g. just a bare project name) has no
        # code-work marker of its own and would otherwise be judged unrelated.
        awaiting_project = self.awaiting_project_store.has_pending(channel_id, thread_ts)
        autopilot = auto_execute or _is_autopilot_request(command_text)
        if autopilot and not auto_execute and (channel_id, thread_ts) in self._autopilot_active:
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
                return (
                    "스레드에 여러 프로젝트가 섞여 있어 대상을 정하지 못했습니다: "
                    f"{candidate_list}. 진행할 프로젝트명을 알려주세요."
                )
        project_name = explicit_project_name or self._project_name(contextual_text)
        if project_name is None:
            self.awaiting_project_store.mark(channel_id, thread_ts)
            return "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."
        self.awaiting_project_store.clear(channel_id, thread_ts)
        if autopilot and not auto_execute:
            return self._run_autopilot(
                project_name, channel_id, thread_ts, thread_context, agent, on_progress
            )
        edit_root = None if autopilot else self._direct_edit_root(agent, project_name)
        if edit_root is not None:
            return self._edit_directly(
                edit_root,
                project_name,
                channel_id,
                thread_ts,
                command_text,
                contextual_text,
                agent,
                thread_context.user_messages(channel_id, thread_ts),
            )
        creator = getattr(agent, "create_execution_plan", None)
        if not callable(creator):
            return "코드 실행 계획에는 LLM 코드 에이전트가 필요합니다."
        creator = _with_thread_ids(creator, channel_id, thread_ts)
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

        # A file named in this exact message always wins — it's what the user
        # just said, e.g. redirecting a pending plan to a different file. Only
        # fall back to thread context when this message names nothing at all.
        explicit_target_paths = _PATH_IN_REQUEST.findall(command_text)
        target_paths = explicit_target_paths or _PATH_IN_REQUEST.findall(contextual_text)
        plan_follow_request = _is_plan_follow_request(command_text) or (
            not explicit_target_paths and _is_plan_follow_request(contextual_text)
        )
        # plan.md is already loaded into planning context via `target_paths`
        # below, so when it's the thing being followed, its own content is
        # sufficient grounding for a brand-new feature that has no prior
        # src/tests code to investigate — the model isn't limited to only
        # modifying what analyze() happened to read.
        plan_document_is_evidence = plan_follow_request and any(
            Path(path).name.casefold() == "plan.md" for path in target_paths
        )
        linear_plan_document = (
            "linear" in command_text.casefold()
            and any(Path(path).name.casefold() == "plan.md" for path in explicit_target_paths)
            and not plan_follow_request
        )
        # "Follow plan.md" names the file as a read-only specification, not
        # as a requested write target.  Keep it in planning context, but do
        # not let that wording authorize the model to overwrite it.
        user_writable_paths = explicit_target_paths
        if plan_follow_request:
            user_writable_paths = [
                path for path in explicit_target_paths if not is_protected_meta_path(path)
            ]
        # Asking for a plan to be written authorizes plan.md as a write target.
        # Every other proposed file still follows the usual rules. Judged from this
        # message alone, never thread context.
        plan_authoring = not plan_follow_request and any(
            marker in command_text.casefold() for marker in PLAN_AUTHORING_MARKERS
        )
        if plan_authoring:
            user_writable_paths = list(dict.fromkeys([*user_writable_paths, "plan.md"]))
        investigator = getattr(agent, "analyze", None)
        # An explicitly named file is already a bounded, first-party source
        # for the planner. Do not make a plan-file creation/edit request fail
        # merely because the optional exploratory LLM chose not to read it.
        source_paths: list[str] = []
        # A directly named file normally supplies bounded planning context.
        # Following an existing plan is different: the plan is specification,
        # not implementation evidence, so related source and test files must
        # still be investigated before the model can propose a code change.
        investigate_code = any(_is_source_or_test_path(path) for path in explicit_target_paths)
        if callable(investigator) and (
            not explicit_target_paths or plan_follow_request or investigate_code
        ):
            investigation = investigator(
                contextual_text, channel_id=channel_id, thread_ts=thread_ts
            )
            source_paths = [
                source
                for source in getattr(investigation, "sources", [])
                if isinstance(source, str)
            ]
            if not source_paths and not plan_document_is_evidence:
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                scope = (
                    getattr(investigation, "summary", "") or "관련 파일을 찾지 못했습니다"
                ).rstrip(".")
                ask = "관련 모듈명 또는 파일을 지정해 주세요."
                tail = "" if "지정해" in scope else f" {ask}"
                return (
                    f"`src/`·`tests/`에서 코드 근거를 찾지 못해 실행 계획을 만들 수 없습니다. "
                    f"탐색 결과: {scope}.{tail}"
                )
            # Preserve an explicitly named target first, then add only actual
            # files the investigator read. `_read_existing_files` and the
            # context loader validate each path against the project boundary.
            target_paths = list(dict.fromkeys([*target_paths, *source_paths]))
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
            # A model that includes a protected file alongside real changes
            # (e.g. wanting to note progress in plan.md) shouldn't kill an
            # otherwise-valid plan — drop the unauthorized step and keep the
            # rest. `_validate_plan` still guards anything this misses.
            unauthorized_protected_paths = {
                path
                for path in plan.affected_files
                if is_protected_meta_path(path) and path not in user_writable_paths
            }
            if unauthorized_protected_paths:
                plan = replace(
                    plan,
                    affected_files=[
                        path
                        for path in plan.affected_files
                        if path not in unauthorized_protected_paths
                    ],
                    steps=[
                        step
                        for step in plan.steps
                        if step.path not in unauthorized_protected_paths
                    ],
                )
                if not plan.steps:
                    dropped = ", ".join(
                        f"`{path}`" for path in sorted(unauthorized_protected_paths)
                    )
                    raise ValueError(
                        "제안된 변경이 프로젝트 관리 파일뿐이라 실행 계획을 만들지 못했습니다: "
                        f"{dropped}"
                    )
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
            return "같은 Git 저장소 이름의 로컬 프로젝트가 여러 개입니다. 폴더명을 지정해 주세요."
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
            return self._execute_pending(channel_id, thread_ts, agent, isolate=False)
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
        evidence_paths = [
            item.relative_path for item in existing_files if item.content is not None
        ]
        preview = render_plan_preview(
            plan, detailed_context.git, diffs, evidence_paths=evidence_paths
        )
        if self.workspaces is not None and not self.workspaces.can_isolate(detailed_context.root):
            preview += f"\n⚠️ {_NO_ROLLBACK_WARNING}"
        return preview

    def _run_autopilot(
        self,
        project_name: str,
        channel_id: str,
        thread_ts: str,
        thread_context: ThreadContextStore,
        agent: object,
        on_progress: Callable[[str], None] | None = None,
    ) -> str | None:
        """Plan and execute every unchecked plan.md item without confirmation,
        checking each off only after its verification succeeded."""
        key = (channel_id, thread_ts)
        self._autopilot_active.add(key)
        try:
            return self._autopilot_loop(project_name, key, thread_context, agent, on_progress)
        finally:
            self._autopilot_active.discard(key)
            self._autopilot_cancelled.discard(key)

    def _autopilot_loop(
        self,
        project_name: str,
        key: tuple[str, str],
        thread_context: ThreadContextStore,
        agent: object,
        on_progress: Callable[[str], None] | None,
    ) -> str | None:
        channel_id, thread_ts = key
        plan_file = self.project_resolver.resolve(project_name) / "plan.md"
        total = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
        result: str | None = None
        completed = 0
        summary_lines: list[str] = []
        all_diffs: list[str] = []
        while (item := next_unchecked_item(plan_file.read_text())) is not None:
            if completed >= self.max_autopilot_items:
                remaining = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
                return (
                    f"⛔ 자동 진행 중단: 최대 {self.max_autopilot_items}개 항목까지만 "
                    f"진행합니다 ({completed}개 완료, 남은 항목 {remaining}개)\n{result}"
                )
            result = self.process(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f"{project_name} plan.md 기준으로 다음 항목을 진행해: {item}",
                thread_context=thread_context,
                agent=agent,
                auto_execute=True,
            )
            state = self.code_work_state_store.state(channel_id=channel_id, thread_ts=thread_ts)
            if state is not CodeWorkState.SUCCEEDED:
                return f"⛔ 자동 진행 중단 ({completed}개 완료): `{item}` 실패\n{result}"
            plan_file.write_text(
                plan_file.read_text().replace(f"- [ ] {item}", f"- [x] {item}", 1)
            )
            completed += 1
            finished = self._last_results.get(key)
            files = ", ".join(
                f"`{path}`" for path in dict.fromkeys(finished.changed_files if finished else [])
            )
            unverified = " (검증 없음)" if finished is None or not finished.checks else ""
            summary_lines.append(f"- ✅ {item}: {files}{unverified}")
            if finished is not None:
                all_diffs.extend(finished.diffs)
                summary_lines.extend(f"  {_render_check(check)}" for check in finished.checks)
            if on_progress is not None:
                on_progress(f"🔄 자동 진행 {completed}/{total} 완료")
            if key in self._autopilot_cancelled:
                remaining = len(_UNCHECKED_ITEM.findall(plan_file.read_text()))
                return f"⛔ 자동 진행 취소 ({completed}개 완료, 남은 항목 {remaining}개)\n{result}"
        return (
            f"✅ 전체 완료 ({completed}/{total}개 항목)\n"
            + "\n".join(summary_lines)
            + f"\nDiff 요약: {_diff_summary(all_diffs)}"
        )

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

    def _edit_directly(
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

        assert self.workspaces is not None
        workspaces = self.workspaces
        edit = getattr(agent, "edit_code")  # noqa: B009 - checked by _direct_edit_root
        named = frozenset(_edit_named_paths_in_thread(command_text, earlier_user_messages))
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.IMPLEMENTING)
        try:
            worktree = workspaces.ensure(root, project_name, channel_id, thread_ts)
        except WorkspaceError as exc:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"❌ 실행하지 못했습니다. 파일은 변경하지 않았습니다.\n{exc}"
        if worktree is None:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "❌ 이 프로젝트는 worktree로 격리할 수 없어 직접 편집하지 않았습니다."

        plan_limits = _plan_first_limits(worktree, command_text, earlier_user_messages)
        write_roots = plan_limits.write_roots
        named = named | plan_limits.named
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
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.IDLE)
            # Nothing to review: the run was a question about the code, so its reply is the
            # answer, not a report about an edit that never happened.
            return (
                agent_text.strip()[:_MAX_ANSWER_CHARS]
                or "코드 에이전트가 파일을 변경하지 않았고 답변도 비어 있습니다."
            )

        applied_agents, applied_skills = _edit_applied_guidance(worktree)
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
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.REPAIRING)
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
        self._set_code_work_state(
            channel_id, thread_ts, CodeWorkState.SUCCEEDED if succeeded else CodeWorkState.FAILED
        )
        self._record_execution_trace(
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
        return f"{render_execution_result(plan, result)}\n\n변경 내용:\n{previews}"

    def _edit_interrupted(self, channel_id: str, thread_ts: str, exc: Exception) -> str:
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
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
            assert self.workspaces is not None
            self.workspaces.discard_thread(channel_id, thread_ts)
        except WorkspaceError:
            discarded = False
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
        listed = "\n".join(f"- {violation}" for violation in violations)
        outcome = (
            "작업 브랜치와 worktree를 폐기했습니다. 원본 체크아웃은 변경하지 않았습니다."
            if discarded
            else "worktree를 폐기하지 못했습니다. `폐기`라고 보내 정리해 주세요."
        )
        return f"❌ 코드 에이전트의 변경이 안전 검사를 통과하지 못했습니다.\n{listed}\n{outcome}"

    def confirm_plan(self, channel_id: str, thread_ts: str, area: str) -> str:
        """The user's `기획 확정 <영역>` (plan.md Phase 28): only this code, never the agent,
        sets an area's plan to `확정`, and only when it has no open questions left."""
        worktree = (
            self.workspaces.thread_worktree(channel_id, thread_ts)
            if self.workspaces is not None
            else None
        )
        if worktree is None:
            return (
                "확정할 기획이 없습니다. 먼저 `개발 진행해`로 이 스레드에서 기획을 쓰게 해 주세요."
            )
        if not plan_first_enabled(worktree):
            return "이 프로젝트는 기획 우선 모드가 아닙니다 (`.piplup/plan-first`가 없습니다)."
        relative = f"docs/{area}/plan.md"
        try:
            text = plan_path(worktree, area).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return f"`{relative}`가 없어 확정할 수 없습니다. 먼저 기획을 쓰게 해 주세요."
        if parse_status(text) == CONFIRMED:
            return f"`{relative}`는 이미 확정되어 있습니다."
        if has_open_questions(text):
            self._plan_reviews[(channel_id, thread_ts)] = _PlanReview(area, time.monotonic())
            return self._next_question(channel_id, thread_ts, worktree, area, text)
        assert self.workspaces is not None
        try:
            plan_path(worktree, area).write_text(with_status(text, CONFIRMED), encoding="utf-8")
            self.workspaces.commit(worktree, [relative], f"기획 확정: {area}")
        except (OSError, WorkspaceError):
            return f"❌ `{relative}`를 확정하지 못했습니다. 파일을 확인해 주세요."
        return (
            f"✅ `{relative}`를 확정했습니다. 이제 같은 스레드에서 개발을 요청하면 "
            "기획의 슬라이스를 구현합니다."
        )

    def has_plan_review(self, channel_id: str, thread_ts: str) -> bool:
        review = self._plan_reviews.get((channel_id, thread_ts))
        return review is not None and time.monotonic() - review.started <= _PLAN_REVIEW_TTL_SECONDS

    def answer_plan_review(self, channel_id: str, thread_ts: str, answer: PlanAnswer) -> str:
        """An answer to the plan review `기획 확정 <영역>` started (plan.md Phase 31). The
        dialogue is plain code: it asks, the person decides, the decision is recorded."""
        key = (channel_id, thread_ts)
        review = self._plan_reviews.get(key)
        worktree = (
            self.workspaces.thread_worktree(channel_id, thread_ts)
            if self.workspaces is not None
            else None
        )
        if review is None or worktree is None:
            return _NO_PLAN_REVIEW
        if time.monotonic() - review.started > _PLAN_REVIEW_TTL_SECONDS:
            del self._plan_reviews[key]
            return _NO_PLAN_REVIEW
        try:
            text = plan_path(worktree, review.area).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            del self._plan_reviews[key]
            return _NO_PLAN_REVIEW
        pending = [q for q in open_questions(text) if _question_key(q) not in review.held]
        if answer.kind == "stop":
            del self._plan_reviews[key]
            left = len(open_questions(text))
            return (
                f"기획 검토를 중단했습니다. 열린 질문 {left}개가 남아 있습니다. "
                f"`기획 확정 {review.area}`를 보내면 이어서 묻습니다."
            )
        if not pending:
            return self._next_question(channel_id, thread_ts, worktree, review.area, text)
        current = pending[0]
        if answer.kind == "hold":
            review.held.add(_question_key(current))
            prefix = f"{current.number}를 보류했습니다.\n\n"
            return self._next_question(channel_id, thread_ts, worktree, review.area, text, prefix)
        if answer.kind == "all_recommended":
            return self._record_all_recommended(channel_id, thread_ts, worktree, review, pending)
        if answer.kind == "recommended" and recommended_decision(current) is None:
            return (
                f"{current.number}에는 추천이 없어 `추천대로`를 쓸 수 없습니다. "
                "`결정: <내용>`으로 답하거나 `보류`해 주세요."
            )
        if answer.kind == "choice":
            if not current.options:
                return (
                    f"{current.number}에는 선택지가 없습니다. `결정: <내용>`으로 답하거나 "
                    "`보류`해 주세요."
                )
            number = int(answer.text)
            if not 1 <= number <= len(current.options):
                return f"{current.number}의 선택지는 1~{len(current.options)}번입니다."
            decision: str | None = current.options[number - 1]
        elif answer.kind == "recommended":
            decision = recommended_decision(current)
        else:
            decision = answer.text
        recorded = self._record_decision(worktree, review.area, text, current, decision or "")
        if recorded.error is not None:
            return recorded.error
        prefix = f"✅ {current.number} 결정을 기록했습니다: {recorded.decision}\n\n"
        return self._next_question(
            channel_id, thread_ts, worktree, review.area, recorded.text, prefix
        )

    def _record_all_recommended(
        self,
        channel_id: str,
        thread_ts: str,
        worktree: Path,
        review: _PlanReview,
        pending: list[Question],
    ) -> str:
        missing = [q.number for q in pending if recommended_decision(q) is None]
        if missing:
            return (
                f"{', '.join(missing)}에는 추천이 없어 `나머지 추천대로`를 쓸 수 없습니다. "
                "`결정: <내용>`으로 답하거나 `보류`해 주세요."
            )
        numbers = [q.number for q in pending]
        text = plan_path(worktree, review.area).read_text(encoding="utf-8")
        for _ in numbers:
            current = next(
                q for q in open_questions(text) if _question_key(q) not in review.held
            )
            recorded = self._record_decision(
                worktree, review.area, text, current, recommended_decision(current) or ""
            )
            if recorded.error is not None:
                return recorded.error
            text = recorded.text
        prefix = f"✅ 남은 질문 {len(numbers)}개를 추천대로 기록했습니다: {', '.join(numbers)}\n\n"
        return self._next_question(channel_id, thread_ts, worktree, review.area, text, prefix)

    def _record_decision(
        self, worktree: Path, area: str, text: str, question: Question, decision: str
    ) -> _Recorded:
        assert self.workspaces is not None
        relative = f"docs/{area}/plan.md"
        try:
            updated = record_decision(text, question, decision)
        except ValueError:
            return _Recorded(
                text, decision, f"결정은 1~{MAX_DECISION_CHARS}자(300자 이내)의 한 줄이어야 합니다."
            )
        try:
            plan_path(worktree, area).write_text(updated, encoding="utf-8")
            self.workspaces.commit(worktree, [relative], f"기획 결정: {area} {question.number}")
        except (OSError, WorkspaceError):
            return _Recorded(text, decision, f"❌ `{relative}`에 결정을 기록하지 못했습니다.")
        return _Recorded(updated, " ".join(decision.split()))

    def _next_question(
        self,
        channel_id: str,
        thread_ts: str,
        worktree: Path,
        area: str,
        text: str,
        prefix: str = "",
    ) -> str:
        key = (channel_id, thread_ts)
        review = self._plan_reviews.get(key)
        held = review.held if review is not None else set()
        open_now = open_questions(text)
        pending = [q for q in open_now if _question_key(q) not in held]
        if pending:
            question = pending[0]
            advice = (
                f"추천: {question.recommendation}"
                if question.recommendation
                else "추천: 없음 — `결정: <내용>`으로 답해 주세요."
            )
            recommended = recommended_choice(question)
            options = "".join(
                f"{number}) {option}{' (추천)' if number == recommended else ''}\n"
                for number, option in enumerate(question.options, start=1)
            )
            how = (
                "번호만 보내면 됩니다. 그 밖의 답: "
                if question.options
                else "답하는 법: `추천대로` · "
            ) + "`결정: <내용>` · `보류` · `나머지 추천대로` · `중단`"
            return (
                f"{prefix}📝 `docs/{area}/plan.md` 열린 질문 {len(open_now)}개 중 하나입니다.\n"
                f"*{question.number}.* {question.text}\n{options}{advice}\n\n{how}"
            )
        self._plan_reviews.pop(key, None)
        if open_now:
            return (
                f"{prefix}남은 질문 {len(open_now)}개가 모두 보류 상태입니다. 결정한 뒤 "
                f"`기획 확정 {area}`를 다시 보내면 이어서 묻습니다."
            )
        return (
            f"{prefix}모든 열린 질문이 정해졌습니다. `기획 확정 {area}`를 다시 보내면 "
            "기획을 확정합니다."
        )

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

    def _prepare_workspace(
        self, context: ProjectContext, plan: ExecutionPlan, channel_id: str, thread_ts: str
    ) -> tuple[Path | None, str | None]:
        """The thread worktree to write in, or `None` plus a warning when the project
        cannot be isolated."""
        assert self.workspaces is not None
        if not self.workspaces.can_isolate(context.root):
            return None, _NO_ROLLBACK_WARNING
        if self.workspaces.existing(plan.project_name, channel_id, thread_ts) is None:
            # A fresh worktree starts from HEAD, so uncommitted edits to approved files
            # in the original would be silently dropped or overwritten.
            self.workspaces.check_clean(context.root, plan.affected_files)
        path = self.workspaces.ensure(context.root, plan.project_name, channel_id, thread_ts)
        return path, (None if path else _NO_ROLLBACK_WARNING)

    def _execute_pending(
        self, channel_id: str, thread_ts: str, agent: object, *, isolate: bool = True
    ) -> str:
        status, pending = self.plan_store.take(channel_id, thread_ts)
        if status is PendingPlanStatus.EXPIRED:
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "보류된 실행 계획이 만료되었습니다. 새로 계획을 만들어 주세요."
        if status is PendingPlanStatus.MISSING or pending is None:
            return "실행할 보류 계획이 없습니다. 먼저 코드 작업 요청을 보내 주세요."

        plan = pending.plan
        self._set_code_work_state(channel_id, thread_ts, CodeWorkState.IMPLEMENTING)
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
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"❌ 실행하지 못했습니다. 파일은 변경하지 않았습니다.\n{exc}"
        except (OSError, ValueError) as exc:
            logger.warning(
                "execution_failed project=%s reason=%s applied=%s",
                plan.project_name,
                exc,
                changed_files,
            )
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return render_execution_failure(plan, str(exc), changed_files)

        checks = self._run_verifications(channel_id, thread_ts, plan, tools, changed_files)
        repair_attempts = 0
        repair_failure_reason: str | None = None
        repairer = getattr(agent, "create_repair_steps", None)
        if callable(repairer):
            repairer = _with_thread_ids(repairer, channel_id, thread_ts)
        failure_fingerprints = {_verification_fingerprint(checks)}
        repair_fingerprints: set[str] = set()
        while not all(check.success for check in checks) and callable(repairer):
            if repair_attempts >= DEFAULT_MAX_AUTO_REPAIRS:
                repair_failure_reason = "자동 복구 한도 초과"
                break
            repair_attempts += 1
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.REPAIRING)
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
        self._set_code_work_state(channel_id, thread_ts, final_state)
        self._record_execution_trace(
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

    def _set_code_work_state(
        self, channel_id: str, thread_ts: str, state: CodeWorkState
    ) -> None:
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


@dataclass(frozen=True)
class _PlanFirstLimits:
    write_roots: tuple[str, ...] | None = None
    named: frozenset[str] = frozenset()
    stage: str | None = None
    area: str | None = None


def _plan_first_limits(
    worktree: Path, command_text: str, earlier_user_messages: Sequence[str]
) -> _PlanFirstLimits:
    """What a plan-first project allows this run to write (plan.md Phase 28): `docs/` only,
    plus the one area plan the user's words point at, until that plan is confirmed. A project
    that did not opt in is not limited and gets no stage."""
    if not plan_first_enabled(worktree):
        return _PlanFirstLimits()
    area = detect_area([*earlier_user_messages, command_text])
    if wants_bypass(command_text):
        # Only this message counts: an earlier "기획 없이" does not carry over to later ones.
        return _PlanFirstLimits(stage="bypassed", area=area)
    if area is not None and plan_status(worktree, area) == CONFIRMED:
        return _PlanFirstLimits(stage="developing", area=area)
    named = frozenset({f"docs/{area}/plan.md"}) if area is not None else frozenset()
    return _PlanFirstLimits(("docs/",), named, "planning", area)


def _edit_applied_guidance(worktree: Path) -> tuple[list[str], list[str]]:
    """What the Claude edit run was actually given: the guidance files handed over in the
    prompt (CLAUDE.md is not read) and the allowlisted project skills."""

    def is_plain_file(path: Path) -> bool:
        return path.is_file() and not path.is_symlink()

    agents = guidance_files(worktree)
    skills = [
        f"claude:{name}"
        for name in claude_skill_names(worktree)
        if is_plain_file(worktree / ".claude" / "skills" / name / "SKILL.md")
    ]
    return agents, skills


def _is_execution_request(text: str) -> bool:
    normalized = text.casefold()
    return is_plan_follow_work_request(normalized) or any(
        marker in normalized for marker in _EXECUTION_MARKERS
    )


# Korean postpositions that mean "based on / according to / following" plan.md
# — whichever verb comes after them ("구현해줘", "진행해줘", "작업해줘", ...) is
# unbounded and not worth enumerating, but this small, closed set of relational
# particles is what actually signals "plan.md is a spec to implement" versus
# "plan.md is the thing being written to" (e.g. "plan.md 수정해줘", "plan.md에
# 계획 짜볼래?", both left unprotected, matching direct-edit requests).
_PLAN_REFERENCE_MARKERS = ("대로", "기준으로", "보고", "따라", "맞춰", "확인 후", "확인하고")


def _is_plan_follow_request(text: str) -> bool:
    """plan.md referenced with a "following/based on" postposition is a
    read-only spec to implement in code, not a write target — independent of
    which verb names the implementation work."""
    normalized = text.casefold()
    if "plan.md" not in normalized:
        return False
    return any(marker in normalized for marker in _PLAN_REFERENCE_MARKERS)


def _is_autopilot_request(text: str) -> bool:
    return "끝까지 진행" in text
