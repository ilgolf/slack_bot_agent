"""Confirmed, project-bounded code execution for Slack requests.

The language model may propose a structured plan, but this module owns every
side effect: project boundaries, the small command allowlist, pending-plan
expiry, and the final verification report.  Local ``AGENTS.md`` and trusted
skills are context for planning only; neither can relax these checks.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from src.project_resolver import InvalidProjectName, ProjectResolver, UnknownProject
from src.request_classifier import classify_request
from src.run_state import CodeWorkState, CodeWorkStateStore
from src.thread_context import ThreadContextStore
from src.tool_policy import ToolCategory
from src.tool_registry import ToolRegistry, definition

logger = logging.getLogger(__name__)

_SLACK_MENTION = re.compile(r"<@[^>]+>")
_CONFIRMATION = re.compile(r"^(실행|실행해줘|실행합니다)$")
_CANCELLATION = re.compile(r"^(취소|취소해줘|취소합니다)$")
# Accept a Korean postposition directly after a filename (``plan.md에``)
# while returning only the project-relative path.
_PATH_IN_REQUEST = re.compile(
    r"(?<!\S)([\w./-]+\.[A-Za-z0-9]+)(?=$|[\s,.:!?…]|[은는이가을를에의])"
)
_EXECUTION_MARKERS = (
    "수정",
    "변경",
    "고쳐",
    "구현",
    "리팩터",
    "리팩토",
    "추가",
    "테스트 실행",
    "test 실행",
    "pytest",
    "ruff",
    "mypy",
    "린트",
    "타입 검사",
    "fix",
    "implement",
    "refactor",
    "update",
    "run test",
    "run lint",
    "typecheck",
    "작업 plan",
    "작업 계획",
    "구현 계획",
    "plan.md에 적은대로",
    "plan.md에 적은 대로",
    "개발 진행",
    "계획 짜",
    "plan 짜",
    "plan 만들어",
    "설계해",
    "github 연동",
    "gitlab 연동",
    "api 연동",
    "연동 작업",
)
_ALLOWED_VERIFICATIONS = ("run_tests", "run_lint", "run_typecheck")
_MAX_WRITE_BYTES = 1_000_000
DEFAULT_MAX_AUTO_REPAIRS = 2
_GREP_IGNORED_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__"})


class ExecutionRisk(StrEnum):
    REVERSIBLE = "reversible"
    MODIFY = "modify"
    HIGH = "high"


@dataclass(frozen=True)
class InstructionSource:
    """A project-local instruction file, ordered from root to most specific."""

    relative_path: str
    content: str
    precedence: int


@dataclass(frozen=True)
class GitState:
    is_repository: bool
    changed_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectContext:
    project_name: str
    root: Path
    instructions: list[InstructionSource]
    git: GitState


class ProjectContextLoader:
    """Loads only project-contained AGENTS.md files and read-only Git metadata."""

    def __init__(self, project_resolver: ProjectResolver) -> None:
        self.project_resolver = project_resolver

    def load(self, project_name: str, *, target_paths: list[str] | None = None) -> ProjectContext:
        root = self.project_resolver.resolve(project_name).resolve()
        directories = {root}
        for target_path in target_paths or []:
            target = _project_path(root, target_path)
            directories.add(target if target.is_dir() else target.parent)

        instruction_paths: set[Path] = set()
        for directory in directories:
            current = directory
            while True:
                for filename in ("AGENTS.md", "CLAUDE.md"):
                    candidate = current / filename
                    if candidate.exists():
                        if candidate.is_symlink() or not candidate.is_file():
                            raise ValueError(f"허용되지 않은 지침 파일 경로입니다: {candidate}")
                        resolved = candidate.resolve()
                        if not resolved.is_relative_to(root):
                            raise ValueError(
                                f"프로젝트 밖 지침 파일은 사용할 수 없습니다: {candidate}"
                            )
                        instruction_paths.add(resolved)
                if current == root:
                    break
                current = current.parent

        instructions = [
            InstructionSource(
                relative_path=str(path.relative_to(root)),
                content=path.read_text(encoding="utf-8"),
                precedence=index,
            )
            for index, path in enumerate(
                sorted(instruction_paths, key=lambda item: (len(item.parts), str(item)))
            )
        ]
        return ProjectContext(
            project_name=project_name,
            root=root,
            instructions=instructions,
            git=_read_git_state(root),
        )


@dataclass(frozen=True)
class AppliedSkill:
    name: str
    relative_path: str
    version: str
    content: str


class SkillRegistry:
    """Reads named skills only from explicitly trusted global roots.

    A project can opt in through ``.piplup/allowed-skills.txt``.  The allowlist
    never grants a project path the ability to supply its own executable skill.
    """

    def __init__(self, trusted_roots: dict[str, str | Path] | None = None) -> None:
        self.trusted_roots = {
            name: Path(path).expanduser().resolve() for name, path in (trusted_roots or {}).items()
        }

    def select(self, *, intent: str, project_root: Path) -> list[AppliedSkill]:
        allowed_names = self._project_allowlist(project_root)
        desired_names = _skills_for_intent(intent)
        skills: list[AppliedSkill] = []
        for name in desired_names:
            skill = self._global_skill(name, allowed_names) or self._claude_project_skill(
                name, project_root, allowed_names
            )
            if skill is not None:
                skills.append(skill)
        return skills

    def _global_skill(self, name: str, allowed_names: set[str]) -> AppliedSkill | None:
        if name not in allowed_names:
            return None
        skill_path = self._find_skill(name)
        return _load_skill(name, skill_path) if skill_path is not None else None

    @staticmethod
    def _claude_project_skill(
        name: str, project_root: Path, allowed_names: set[str]
    ) -> AppliedSkill | None:
        if f"claude:{name}" not in allowed_names:
            return None
        skill_path = project_root / ".claude" / "skills" / name / "SKILL.md"
        if not skill_path.is_file() or skill_path.is_symlink():
            return None
        resolved = skill_path.resolve()
        if not resolved.is_relative_to(project_root.resolve()):
            return None
        return _load_skill(f"claude:{name}", resolved)

    @staticmethod
    def _project_allowlist(project_root: Path) -> set[str]:
        allowlist = project_root / ".piplup" / "allowed-skills.txt"
        if not allowlist.is_file() or allowlist.is_symlink():
            return set()
        resolved = allowlist.resolve()
        if not resolved.is_relative_to(project_root.resolve()):
            return set()
        return {
            line.strip()
            for line in allowlist.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

    def _find_skill(self, name: str) -> Path | None:
        for root in self.trusted_roots.values():
            candidate = root / name / "SKILL.md"
            if not candidate.is_file() or candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved.is_relative_to(root):
                return resolved
        return None


@dataclass(frozen=True)
class ExecutionStep:
    action: Literal["write_file"]
    path: str
    content: str


@dataclass(frozen=True)
class ExecutionPlan:
    goal: str
    project_name: str
    affected_files: list[str]
    steps: list[ExecutionStep]
    verification_commands: list[str]
    risk: ExecutionRisk
    applied_agents: list[str] = field(default_factory=list)
    applied_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExistingFile:
    """A target file's current content, read before planning so the model
    edits real code instead of guessing at it. `content` is `None` when the
    path doesn't exist yet — a plausible new-file request, not a read error.
    """

    relative_path: str
    content: str | None


class ExecutionPlanCreator(Protocol):
    def create_execution_plan(
        self,
        request: str,
        context: ProjectContext,
        skills: list[AppliedSkill],
        existing_files: list[ExistingFile],
    ) -> ExecutionPlan: ...


class RepairPlanCreator(Protocol):
    def create_repair_steps(
        self,
        plan: ExecutionPlan,
        existing_files: list[ExistingFile],
        checks: list[CommandResult],
    ) -> list[ExecutionStep]: ...


class PendingPlanStatus(StrEnum):
    MISSING = "missing"
    READY = "ready"
    EXPIRED = "expired"


class VerificationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ENVIRONMENT_ERROR = "environment_error"


@dataclass(frozen=True)
class PendingPlan:
    plan: ExecutionPlan
    created_at: datetime
    fingerprint: str


class PendingPlanStore:
    """Thread-keyed plan storage.  Pending plans are deliberately not persistent."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.ttl = ttl
        self._lock = Lock()
        self._plans: dict[tuple[str, str], PendingPlan] = {}

    def put(
        self, channel_id: str, thread_ts: str, plan: ExecutionPlan, *, request: str
    ) -> PendingPlan:
        fingerprint = _fingerprint(request)
        key = (channel_id, thread_ts)
        with self._lock:
            existing = self._plans.get(key)
            if (
                existing is not None
                and not self._expired(existing)
                and existing.fingerprint == fingerprint
            ):
                return existing
            pending = PendingPlan(plan=plan, created_at=datetime.now(UTC), fingerprint=fingerprint)
            self._plans[key] = pending
            return pending

    def take(self, channel_id: str, thread_ts: str) -> tuple[PendingPlanStatus, PendingPlan | None]:
        key = (channel_id, thread_ts)
        with self._lock:
            pending = self._plans.pop(key, None)
            if pending is None:
                return PendingPlanStatus.MISSING, None
            if self._expired(pending):
                return PendingPlanStatus.EXPIRED, None
            return PendingPlanStatus.READY, pending

    def cancel(self, channel_id: str, thread_ts: str) -> PendingPlanStatus:
        status, _ = self.take(channel_id, thread_ts)
        return status

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        """Check a non-expired draft without consuming its confirmation."""
        with self._lock:
            pending = self._plans.get((channel_id, thread_ts))
            if pending is None:
                return False
            if self._expired(pending):
                self._plans.pop((channel_id, thread_ts), None)
                return False
            return True

    def peek(self, channel_id: str, thread_ts: str) -> ExecutionPlan | None:
        """Read a non-expired draft's plan without consuming its confirmation
        — for a clarification message that describes what's pending."""
        with self._lock:
            pending = self._plans.get((channel_id, thread_ts))
            if pending is None:
                return None
            if self._expired(pending):
                self._plans.pop((channel_id, thread_ts), None)
                return None
            return pending.plan

    def _expired(self, pending: PendingPlan) -> bool:
        return datetime.now(UTC) - pending.created_at > self.ttl


class AwaitingProjectStore:
    """Remembers a code-work request that stalled only for lack of a project
    name, so the very next reply supplying just the name resumes it instead
    of being judged as a fresh, markerless message. Thread context already
    carries the original request text — this only needs to remember *that*
    a request is waiting, TTL-bound like `PendingPlanStore`."""

    def __init__(self, *, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.ttl = ttl
        self._lock = Lock()
        self._marked_at: dict[tuple[str, str], datetime] = {}

    def mark(self, channel_id: str, thread_ts: str) -> None:
        with self._lock:
            self._marked_at[(channel_id, thread_ts)] = datetime.now(UTC)

    def clear(self, channel_id: str, thread_ts: str) -> None:
        with self._lock:
            self._marked_at.pop((channel_id, thread_ts), None)

    def has_pending(self, channel_id: str, thread_ts: str) -> bool:
        with self._lock:
            marked_at = self._marked_at.get((channel_id, thread_ts))
            if marked_at is None:
                return False
            if datetime.now(UTC) - marked_at > self.ttl:
                self._marked_at.pop((channel_id, thread_ts), None)
                return False
            return True


@dataclass(frozen=True)
class CommandResult:
    name: str
    success: bool
    output: str
    status: VerificationStatus | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            object.__setattr__(
                self,
                "status",
                VerificationStatus.SUCCEEDED if self.success else VerificationStatus.FAILED,
            )


@dataclass(frozen=True)
class ExecutionResult:
    changed_files: list[str]
    diffs: list[str]
    checks: list[CommandResult]
    remaining_risks: list[str]
    repair_attempts: int = 0
    repair_failure_reason: str | None = None


class ProjectExecutionTools:
    """Restricted filesystem and verification tools used after confirmation only."""

    COMMANDS = {
        # Use the interpreter running the bot so verification is bound to its
        # virtual environment and does not depend on a separately installed
        # ``uv`` binary being present on PATH.
        "run_tests": (sys.executable, "-m", "pytest"),
        "run_lint": (sys.executable, "-m", "ruff", "check", "src", "tests"),
        "run_typecheck": (sys.executable, "-m", "mypy"),
    }

    def __init__(self, root: Path, allowed_paths: list[str]) -> None:
        self.root = root.resolve()
        # Freeze the confirmed plan's scope. Callers can neither expand it by
        # mutating their original list nor accidentally widen it mid-run.
        self.allowed_paths = frozenset(allowed_paths)

    def read_file(self, relative_path: str) -> str:
        return _project_path(self.root, relative_path).read_text(encoding="utf-8")

    def list_files(self, relative_path: str = ".") -> list[str]:
        path = _project_path(self.root, relative_path, allow_root=True)
        return sorted(item.name for item in path.iterdir())

    def grep(self, query: str, relative_path: str = ".") -> list[str]:
        if not query or len(query) > 200:
            raise ValueError("검색어가 올바르지 않습니다")
        directory = _project_path(self.root, relative_path, allow_root=True)
        if not directory.is_dir():
            raise ValueError("검색 경로가 디렉터리가 아닙니다")
        matches: list[str] = []
        for path in directory.rglob("*"):
            if len(matches) >= 100:
                break
            if path.is_symlink() or not path.is_file():
                continue
            relative_parts = path.relative_to(self.root).parts
            if _GREP_IGNORED_DIRS.intersection(relative_parts):
                continue
            try:
                if query in path.read_text(encoding="utf-8"):
                    matches.append(str(path.relative_to(self.root)))
            except (OSError, UnicodeDecodeError):
                continue
        return matches

    def git_status(self) -> list[str]:
        return _read_git_state(self.root).changed_files

    def git_diff(self, relative_path: str | None = None) -> str:
        command = ["git", "-C", str(self.root), "diff", "--"]
        if relative_path is not None:
            _project_path(self.root, relative_path)
            command.append(relative_path)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return (completed.stdout + completed.stderr)[-32_000:]

    def write_file(self, relative_path: str, content: str) -> tuple[str, str]:
        if relative_path not in self.allowed_paths:
            raise ValueError(f"계획에 포함되지 않은 파일입니다: {relative_path}")
        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            raise ValueError("파일 내용이 허용 크기를 초과합니다")
        target = _project_path(self.root, relative_path)
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )
        return relative_path, diff

    def run_check(self, name: str) -> CommandResult:
        command = self.COMMANDS.get(name)
        if command is None:
            raise ValueError(f"허용되지 않은 검증 명령입니다: {name}")
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        success = completed.returncode == 0
        return CommandResult(
            name=name,
            success=success,
            output=output[-2000:],
            status=VerificationStatus.SUCCEEDED if success else VerificationStatus.FAILED,
        )

    def tool_registry(self) -> ToolRegistry:
        """Expose the same bounded primitives to a future iterative planner.

        This prevents a second, weaker path-validation implementation from
        being introduced for tool calling.
        """
        return ToolRegistry(
            [
                definition("list_files", ToolCategory.PROJECT_READ, self.list_files),
                definition(
                    "read_file",
                    ToolCategory.PROJECT_READ,
                    self.read_file,
                    evidence_arg="relative_path",
                ),
                definition("grep", ToolCategory.PROJECT_SEARCH, self.grep),
                definition("git_status", ToolCategory.GIT_READ, self.git_status),
                definition("git_diff", ToolCategory.GIT_READ, self.git_diff),
                definition("write_file", ToolCategory.PROJECT_WRITE, self.write_file),
                definition("run_tests", ToolCategory.VERIFY, lambda: self.run_check("run_tests")),
                definition("run_lint", ToolCategory.VERIFY, lambda: self.run_check("run_lint")),
                definition(
                    "run_typecheck", ToolCategory.VERIFY, lambda: self.run_check("run_typecheck")
                ),
            ]
        )


def _read_existing_files(root: Path, target_paths: list[str]) -> list[ExistingFile]:
    """Read each candidate target path with the same bounded validation as the
    execution tools; a missing or invalid path becomes `content=None` rather
    than raising, since the request may be to create that file."""
    tools = ProjectExecutionTools(root, [])
    files: list[ExistingFile] = []
    for path in target_paths:
        try:
            content: str | None = tools.read_file(path)
        except (OSError, ValueError):
            content = None
        files.append(ExistingFile(relative_path=path, content=content))
    return files


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
    ) -> None:
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
        defer_missing_confirmation: bool = False,
    ) -> str | None:
        command_text = _SLACK_MENTION.sub("", text).strip()
        if _CONFIRMATION.fullmatch(command_text):
            response = self._execute_pending(channel_id, thread_ts, agent)
            if defer_missing_confirmation and response.startswith("실행할 보류 계획이 없습니다"):
                return None
            return response
        if _CANCELLATION.fullmatch(command_text):
            cancellation_response = self._cancel_pending(channel_id, thread_ts)
            if defer_missing_confirmation and cancellation_response is None:
                return None
            return cancellation_response
        # A thread with a plan already pending can be redirected by naming a
        # different file alone — no modification verb required. Without a
        # pending plan, a bare file mention still isn't an execution request.
        has_pending_plan = self.plan_store.has_pending(channel_id, thread_ts)
        redirect_with_pending_plan = has_pending_plan and _PATH_IN_REQUEST.search(command_text)
        # A stalled request awaiting only a project name resumes on the very
        # next message — that reply (e.g. just a bare project name) has no
        # code-work marker of its own and would otherwise be judged unrelated.
        awaiting_project = self.awaiting_project_store.has_pending(channel_id, thread_ts)
        if (
            not _is_execution_request(command_text)
            and not redirect_with_pending_plan
            and not awaiting_project
        ):
            if has_pending_plan:
                return self._pending_plan_clarification(channel_id, thread_ts)
            return None

        contextual_text = self._with_thread_context(
            channel_id, thread_ts, command_text, thread_context
        )
        project_name = self._project_name(contextual_text)
        if project_name is None:
            self.awaiting_project_store.mark(channel_id, thread_ts)
            return "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."
        self.awaiting_project_store.clear(channel_id, thread_ts)
        creator = getattr(agent, "create_execution_plan", None)
        if not callable(creator):
            return "코드 실행 계획에는 LLM 코드 에이전트가 필요합니다."
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
        plan_follow_request = _is_plan_follow_request(command_text)
        # "Follow plan.md" names the file as a read-only specification, not
        # as a requested write target.  Keep it in planning context, but do
        # not let that wording authorize the model to overwrite it.
        user_writable_paths = explicit_target_paths
        if plan_follow_request:
            user_writable_paths = [
                path for path in explicit_target_paths if not _is_protected_meta_path(path)
            ]
        investigator = getattr(agent, "analyze", None)
        # An explicitly named file is already a bounded, first-party source
        # for the planner. Do not make a plan-file creation/edit request fail
        # merely because the optional exploratory LLM chose not to read it.
        source_paths: list[str] = []
        # A directly named file normally supplies bounded planning context.
        # Following an existing plan is different: the plan is specification,
        # not implementation evidence, so related source and test files must
        # still be investigated before the model can propose a code change.
        if callable(investigator) and (not explicit_target_paths or plan_follow_request):
            investigation = investigator(
                contextual_text, channel_id=channel_id, thread_ts=thread_ts
            )
            source_paths = [
                source
                for source in getattr(investigation, "sources", [])
                if isinstance(source, str)
            ]
            if not source_paths:
                self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
                return "코드 조사 근거 파일을 읽지 못해 실행 계획을 만들 수 없습니다."
            # Preserve an explicitly named target first, then add only actual
            # files the investigator read. `_read_existing_files` and the
            # context loader validate each path against the project boundary.
            target_paths = list(dict.fromkeys([*target_paths, *source_paths]))
        try:
            context = self.context_loader.load(project_name, target_paths=target_paths)
            skills = self.skill_registry.select(intent=command_text, project_root=context.root)
            existing_files = _read_existing_files(context.root, target_paths)
            plan = creator(contextual_text, context, skills, existing_files)
            if not target_paths and plan.affected_files:
                # No file was named, so the model proposed one itself while
                # planning. Re-read whatever it picked and ask again so an
                # already-existing file still isn't written to blind.
                reread = _read_existing_files(context.root, plan.affected_files)
                if any(item.content is not None for item in reread):
                    existing_files = reread
                    context = self.context_loader.load(
                        project_name, target_paths=plan.affected_files
                    )
                    plan = creator(contextual_text, context, skills, existing_files)
            _validate_plan(
                plan,
                project_name,
                user_writable_paths,
                project_root=context.root,
                evidence_paths=source_paths,
                require_code_evidence=plan_follow_request,
                restrict_new_files_to_code_roots=plan_follow_request,
            )
            detailed_context = self.context_loader.load(
                project_name, target_paths=plan.affected_files
            )
        except (InvalidProjectName, UnknownProject):
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return "대상 프로젝트를 찾을 수 없습니다."
        except (OSError, ValueError) as exc:
            logger.warning("execution_plan_rejected project=%s reason=%s", project_name, exc)
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"실행 계획을 만들지 못했습니다: {exc}"
        except Exception as exc:
            logger.exception("execution_plan_failed project=%s", project_name)
            self._set_code_work_state(channel_id, thread_ts, CodeWorkState.FAILED)
            return f"실행 계획 생성에 실패했습니다: {exc}"

        plan = replace(
            plan,
            applied_agents=[
                instruction.relative_path for instruction in detailed_context.instructions
            ],
            applied_skills=[f"{skill.name}@{skill.version}" for skill in skills],
        )
        self.plan_store.put(channel_id, thread_ts, plan, request=command_text)
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
        return render_plan_preview(plan, detailed_context.git, diffs)

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

    def _execute_pending(self, channel_id: str, thread_ts: str, agent: object) -> str:
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
        try:
            context = self.context_loader.load(plan.project_name, target_paths=plan.affected_files)
            tools = ProjectExecutionTools(context.root, plan.affected_files)
            for step in plan.steps:
                changed_file, diff = tools.write_file(step.path, step.content)
                changed_files.append(changed_file)
                diffs.append(diff)
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

        result = ExecutionResult(
            changed_files=changed_files,
            diffs=diffs,
            checks=checks,
            remaining_risks=_remaining_risks(checks),
            repair_attempts=repair_attempts,
            repair_failure_reason=repair_failure_reason,
        )
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


def _preview_diffs(root: Path, steps: list[ExecutionStep]) -> list[tuple[str, str]]:
    """Compute a read-only unified diff per step against the file currently on
    disk — nothing is written. A missing/invalid path is treated as empty
    "before" content, matching a new-file step."""
    diffs: list[tuple[str, str]] = []
    for step in steps:
        before = ""
        try:
            target = _project_path(root, step.path)
            if target.exists():
                before = target.read_text(encoding="utf-8")
        except (OSError, ValueError):
            before = ""
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                step.content.splitlines(keepends=True),
                fromfile=f"a/{step.path}",
                tofile=f"b/{step.path}",
            )
        )
        diffs.append((step.path, diff))
    return diffs


def _render_diff_excerpt(diff: str, *, max_lines: int = 12) -> str:
    lines = diff.splitlines()
    if not lines:
        return "(변경 없음)"
    excerpt = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        excerpt += f"\n... ({len(lines) - max_lines}줄 생략)"
    return excerpt


def render_plan_preview(
    plan: ExecutionPlan,
    git: GitState,
    diffs: list[tuple[str, str]] | None = None,
    *,
    max_auto_repairs: int = DEFAULT_MAX_AUTO_REPAIRS,
) -> str:
    instructions = ", ".join(f"`{item}`" for item in plan.applied_agents) or "없음"
    skills = ", ".join(f"`{item}`" for item in plan.applied_skills) or "없음"
    files = "\n".join(f"- `{path}`" for path in plan.affected_files) or "- 파일 변경 없음"
    steps = "\n".join(f"- `{step.path}` 작성" for step in plan.steps) or "- 파일 변경 없음"
    checks = ", ".join(plan.verification_commands) or "없음"
    git_summary = (
        "Git 저장소 아님" if not git.is_repository else f"기존 변경 {len(git.changed_files)}개"
    )
    diff_sections = (
        "\n\n".join(
            f"`{path}`:\n```\n{_render_diff_excerpt(diff)}\n```" for path, diff in diffs
        )
        if diffs
        else "(변경 없음)"
    )
    return (
        f"{render_code_work_status(CodeWorkState.AWAITING_CONFIRMATION)}\n"
        "코드 실행 계획을 만들었습니다.\n"
        f"목표: {plan.goal}\n프로젝트: `{plan.project_name}` ({git_summary})\n"
        f"위험도: `{plan.risk}`\n승인 파일 범위:\n{files}\n실행 단계:\n{steps}\n"
        f"변경 미리보기:\n{diff_sections}\n"
        f"고정 검증 명령: {checks}\n자동 복구 예산: 최대 {max_auto_repairs}회\n"
        "미지원 작업: 임의 셸·HTTP·패키지 설치/삭제·rename·Git commit/push\n"
        "남은 위험: 검증은 승인 후에만 실행되며, 환경 의존 오류가 발생할 수 있습니다.\n"
        f"적용 AGENTS.md: {instructions}\n적용 Skill: {skills}\n\n"
        "내용을 확인한 뒤 같은 스레드에 `실행`이라고 보내면 적용합니다. "
        "`취소`하면 계획만 제거합니다."
    )


def render_execution_result(plan: ExecutionPlan, result: ExecutionResult) -> str:
    changes = "\n".join(f"- `{path}`" for path in result.changed_files) or "- 파일 변경 없음"
    checks = "\n".join(_render_check(check) for check in result.checks) or "- 실행한 검증 명령 없음"
    risks = "\n".join(f"- {risk}" for risk in result.remaining_risks) or "- 없음"
    instructions = ", ".join(f"`{item}`" for item in plan.applied_agents) or "없음"
    skills = ", ".join(f"`{item}`" for item in plan.applied_skills) or "없음"
    outcome = (
        (
            "❌ 구현 실패: 자동 복구 한도 초과"
            if result.repair_failure_reason == "자동 복구 한도 초과"
            else f"❌ 구현 실패: {result.repair_failure_reason}"
        )
        if result.repair_failure_reason
        else (
        (
            f"✅ 구현 완료 (자동 복구 {result.repair_attempts}회)"
            if result.repair_attempts
            else render_code_work_status(CodeWorkState.SUCCEEDED)
        )
        if all(check.success for check in result.checks)
        else "⚠️ 변경 적용, 검증 실패"
        )
    )
    return (
        f"{outcome}\n변경 파일:\n{changes}\nDiff 요약: {_diff_summary(result.diffs)}\n"
        f"검증 결과:\n{checks}\n"
        f"적용 AGENTS.md: {instructions}\n적용 Skill: {skills}\n남은 위험:\n{risks}"
    )


def render_execution_failure(
    plan: ExecutionPlan, reason: str, changed_files: list[str] | None = None
) -> str:
    applied = "\n".join(f"- `{path}`" for path in changed_files or []) or "- 없음"
    return (
        "❌ 실행 실패\n"
        f"프로젝트: `{plan.project_name}`\n원인: {reason}\n"
        f"이미 적용된 파일:\n{applied}\n"
        "남은 단계는 진행하지 않았습니다. 위 파일들의 Git diff를 확인한 뒤 새 계획을 만들어 주세요."
    )


def render_code_work_status(state: CodeWorkState, *, repair_attempt: int | None = None) -> str:
    """Return the fixed, content-safe Slack text for a code-work lifecycle state.

    State reporting is deliberately independent from model output: it exposes
    progress without leaking prompts, file bodies, or agent reasoning.
    """
    messages = {
        CodeWorkState.DISCOVERING: "🔎 코드 구조를 확인 중입니다…",
        CodeWorkState.AWAITING_CONFIRMATION: "📝 구현 계획을 만들었습니다. 실행 확인을 기다립니다.",
        CodeWorkState.IMPLEMENTING: "🛠️ 코드 변경을 적용했습니다. 검증 중입니다…",
        CodeWorkState.VERIFYING: "🛠️ 코드 변경을 적용했습니다. 검증 중입니다…",
        CodeWorkState.SUCCEEDED: "✅ 구현 및 검증 완료",
        CodeWorkState.FAILED: "❌ 구현 실패",
        CodeWorkState.CANCELLED: "🚫 구현 계획을 취소했습니다.",
    }
    if state is CodeWorkState.REPAIRING:
        attempt = repair_attempt if repair_attempt is not None else 1
        return f"🔁 테스트 실패를 분석해 수정 중입니다… ({attempt}/2)"
    return messages.get(state, "코드 작업을 기다리고 있습니다.")


def _project_path(root: Path, relative_path: str, *, allow_root: bool = False) -> Path:
    path = Path(relative_path)
    is_root = str(path) in {"", "."}
    if (is_root and not allow_root) or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"허용되지 않은 경로입니다: {relative_path}")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"허용되지 않은 경로입니다: {relative_path}")
    return target


def _read_git_state(root: Path) -> GitState:
    try:
        inside = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "--is-inside-work-tree"),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GitState(is_repository=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitState(is_repository=False)
    status = subprocess.run(
        ("git", "-C", str(root), "status", "--porcelain"),
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    return GitState(
        is_repository=True,
        changed_files=[line[3:] for line in status.stdout.splitlines() if len(line) > 3],
    )


def _load_skill(name: str, skill_path: Path) -> AppliedSkill:
    """Read one allowlisted skill after its location has passed boundary checks."""
    content = skill_path.read_text(encoding="utf-8")
    return AppliedSkill(
        name=name,
        relative_path=str(skill_path),
        version=hashlib.sha256(content.encode("utf-8")).hexdigest()[:12],
        content=content,
    )


def _skills_for_intent(intent: str) -> tuple[str, ...]:
    normalized = intent.casefold()
    if any(word in normalized for word in ("리뷰", "review")):
        return ("code-review",)
    if any(word in normalized for word in ("스프레드시트", "xlsx", "csv")):
        return ("spreadsheets",)
    if any(word in normalized for word in ("문서", "readme", "markdown", ".md")):
        return ("documents",)
    return ()


def _is_execution_request(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in _EXECUTION_MARKERS)


def _is_plan_follow_request(text: str) -> bool:
    normalized = text.casefold()
    return "plan.md에 적은대로" in normalized or "plan.md에 적은 대로" in normalized


_PROTECTED_META_BASENAMES = {"plan.md", "plan.archive.md", "claude.md", "agents.md"}
_PROTECTED_META_PREFIXES = (".omx/", ".claude/", ".git/")


def _is_protected_meta_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    if basename in _PROTECTED_META_BASENAMES:
        return True
    return normalized.startswith(_PROTECTED_META_PREFIXES)


def _validate_plan(
    plan: ExecutionPlan,
    project_name: str,
    user_named_paths: Sequence[str],
    *,
    project_root: Path | None = None,
    evidence_paths: Sequence[str] = (),
    require_code_evidence: bool = False,
    restrict_new_files_to_code_roots: bool = False,
) -> None:
    if plan.project_name != project_name:
        raise ValueError("계획의 프로젝트가 요청 대상과 다릅니다")
    named = set(user_named_paths)
    for path in plan.affected_files:
        if _is_protected_meta_path(path) and path not in named:
            raise ValueError(
                f"`{path}`는 프로젝트 관리 파일이라 사용자가 직접 지정한 경우에만 "
                "수정할 수 있습니다"
            )
    if plan.risk is ExecutionRisk.HIGH:
        raise ValueError("고위험 작업(의존성·네트워크·삭제·Git push)은 지원하지 않습니다")
    if len(plan.steps) > 20 or len(plan.affected_files) > 20:
        raise ValueError("한 계획에서 변경할 수 있는 파일 수를 초과했습니다")
    if set(plan.affected_files) != {step.path for step in plan.steps}:
        raise ValueError(
            "승인 파일 범위 밖 수정이 포함되어 새 계획과 실행 확인이 필요합니다"
        )
    for path in plan.affected_files:
        _project_path(Path("/safe-root"), path)
        if (
            restrict_new_files_to_code_roots
            and project_root is not None
            and not _project_path(project_root, path).exists()
        ):
            if not _is_source_or_test_path(path):
                raise ValueError(
                    f"새 파일 `{path}`은 `src/` 또는 `tests/` 아래에 만들어야 합니다"
                )
    if require_code_evidence and not any(_is_source_or_test_path(path) for path in evidence_paths):
        raise ValueError(
            "plan.md 실행에는 기존 `src/` 또는 `tests/` 파일을 읽은 구현 근거가 필요합니다"
        )
    if any(step.action != "write_file" for step in plan.steps):
        raise ValueError("허용되지 않은 실행 단계가 포함되었습니다")
    if any(command not in _ALLOWED_VERIFICATIONS for command in plan.verification_commands):
        raise ValueError("허용되지 않은 검증 명령이 포함되었습니다")
    if len(set(plan.verification_commands)) != len(plan.verification_commands):
        raise ValueError("검증 명령이 중복되었습니다")


def _is_source_or_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith("src/") or normalized.startswith("tests/")


def _validate_repair_steps(steps: object, approved_paths: Sequence[str]) -> None:
    if not isinstance(steps, list) or not steps:
        raise ValueError("자동 복구 단계가 비어 있습니다")
    approved = set(approved_paths)
    for step in steps:
        if not isinstance(step, ExecutionStep) or step.action != "write_file":
            raise ValueError("허용되지 않은 자동 복구 단계입니다")
        if step.path not in approved:
            raise ValueError("자동 복구에 승인 파일 범위 밖 수정이 필요합니다")
        if _is_protected_meta_path(step.path):
            raise ValueError("보호 파일은 자동 복구할 수 없어 새 계획과 확인이 필요합니다")


def _fingerprint(request: str) -> str:
    return hashlib.sha256(request.strip().casefold().encode("utf-8")).hexdigest()


def _repair_fingerprint(steps: Sequence[ExecutionStep]) -> str:
    payload = [(step.path, step.content) for step in steps]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _verification_fingerprint(checks: Sequence[CommandResult]) -> str:
    payload = [(check.name, check.status, check.output) for check in checks if not check.success]
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _remaining_risks(checks: list[CommandResult]) -> list[str]:
    if not checks:
        return ["검증 명령이 실행하지 않은 환경별 통합 테스트는 확인되지 않았습니다."]
    if any(check.status is VerificationStatus.ENVIRONMENT_ERROR for check in checks):
        return ["검증 실행 환경 오류를 해결한 뒤 새 계획을 만들어 다시 실행해야 합니다."]
    if any(check.status is VerificationStatus.TIMED_OUT for check in checks):
        return ["검증 시간 초과 원인을 확인한 뒤 새 계획을 만들어 다시 실행해야 합니다."]
    if any(not check.success for check in checks):
        return ["실패한 검증을 수정한 뒤 새 계획을 만들어 다시 실행해야 합니다."]
    return []


def _render_check(check: CommandResult) -> str:
    if check.success:
        return f"- ✅ {check.name}"
    detail = _redact_output(check.output).replace("\n", " ")[:300] or "출력 없음"
    label = {
        VerificationStatus.ENVIRONMENT_ERROR: "실행 환경 오류",
        VerificationStatus.TIMED_OUT: "시간 초과",
    }.get(check.status or VerificationStatus.FAILED)
    prefix = f"- ❌ {check.name} ({label})" if label else f"- ❌ {check.name}"
    return f"{prefix}: {detail}"


def _diff_summary(diffs: list[str]) -> str:
    added = sum(
        1
        for diff in diffs
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for diff in diffs
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return f"+{added}/-{removed}줄"


def _redact_output(output: str) -> str:
    return re.sub(
        r"(?i)\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))=\S+",
        r"\1=[REDACTED]",
        output,
    )


def parse_execution_plan(content: object) -> ExecutionPlan:
    """Parse the LLM's strict JSON plan without accepting arbitrary commands."""
    text = str(content).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text.replace("\u00a0", " "))
        risk = ExecutionRisk(payload["risk"])
        raw_steps = payload["steps"]
        steps = [
            ExecutionStep(action=step["action"], path=step["path"], content=step["content"])
            for step in raw_steps
        ]
        plan = ExecutionPlan(
            goal=payload["goal"],
            project_name=payload["project_name"],
            affected_files=payload["affected_files"],
            steps=steps,
            verification_commands=payload.get("verification_commands", []),
            risk=risk,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("모델 응답이 유효한 실행 계획 형식이 아닙니다") from exc
    if not isinstance(plan.goal, str) or not isinstance(plan.project_name, str):
        raise ValueError("실행 계획의 목표 또는 프로젝트가 올바르지 않습니다")
    if not all(isinstance(path, str) for path in plan.affected_files):
        raise ValueError("실행 계획의 파일 경로가 올바르지 않습니다")
    if not all(isinstance(command, str) for command in plan.verification_commands):
        raise ValueError("실행 계획의 검증 명령이 올바르지 않습니다")
    return plan
