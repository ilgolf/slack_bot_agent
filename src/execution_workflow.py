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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from src.project_resolver import InvalidProjectName, ProjectResolver, UnknownProject
from src.request_classifier import classify_request
from src.thread_context import ThreadContextStore

logger = logging.getLogger(__name__)

_SLACK_MENTION = re.compile(r"<@[^>]+>")
_CONFIRMATION = re.compile(r"^(실행|실행해줘|실행합니다)$")
_CANCELLATION = re.compile(r"^(취소|취소해줘|취소합니다)$")
_PATH_IN_REQUEST = re.compile(r"(?<!\S)([\w./-]+\.[A-Za-z0-9]+)(?!\S)")
_EXECUTION_MARKERS = (
    "수정",
    "변경",
    "고쳐",
    "구현",
    "리팩터",
    "리팩토",
    "추가",
    "테스트 실행",
    "린트",
    "타입 검사",
    "fix",
    "implement",
    "refactor",
    "update",
    "run test",
    "run lint",
    "typecheck",
)
_ALLOWED_VERIFICATIONS = ("run_tests", "run_lint", "run_typecheck")
_MAX_WRITE_BYTES = 1_000_000


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


class ExecutionPlanCreator(Protocol):
    def create_execution_plan(
        self,
        request: str,
        context: ProjectContext,
        skills: list[AppliedSkill],
    ) -> ExecutionPlan: ...


class PendingPlanStatus(StrEnum):
    MISSING = "missing"
    READY = "ready"
    EXPIRED = "expired"


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

    def _expired(self, pending: PendingPlan) -> bool:
        return datetime.now(UTC) - pending.created_at > self.ttl


@dataclass(frozen=True)
class CommandResult:
    name: str
    success: bool
    output: str


@dataclass(frozen=True)
class ExecutionResult:
    changed_files: list[str]
    diffs: list[str]
    checks: list[CommandResult]
    remaining_risks: list[str]


class ProjectExecutionTools:
    """Restricted filesystem and verification tools used after confirmation only."""

    COMMANDS = {
        "run_tests": ("uv", "run", "pytest"),
        "run_lint": ("uv", "run", "ruff", "check", "src", "tests"),
        "run_typecheck": ("uv", "run", "mypy"),
    }

    def __init__(self, root: Path, allowed_paths: list[str]) -> None:
        self.root = root.resolve()
        self.allowed_paths = set(allowed_paths)

    def read_file(self, relative_path: str) -> str:
        return _project_path(self.root, relative_path).read_text(encoding="utf-8")

    def list_files(self, relative_path: str = ".") -> list[str]:
        path = _project_path(self.root, relative_path)
        return sorted(item.name for item in path.iterdir())

    def grep(self, query: str, relative_path: str = ".") -> list[str]:
        if not query or len(query) > 200:
            raise ValueError("검색어가 올바르지 않습니다")
        directory = _project_path(self.root, relative_path)
        if not directory.is_dir():
            raise ValueError("검색 경로가 디렉터리가 아닙니다")
        matches: list[str] = []
        for path in directory.rglob("*"):
            if len(matches) >= 100:
                break
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if query in path.read_text(encoding="utf-8"):
                    matches.append(str(path.relative_to(self.root)))
            except (OSError, UnicodeDecodeError):
                continue
        return matches

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
        return CommandResult(name=name, success=completed.returncode == 0, output=output[-2000:])


class ExecutionWorkflow:
    """Creates a preview, then applies only its confirmed bounded plan."""

    def __init__(
        self,
        *,
        project_resolver: ProjectResolver,
        context_loader: ProjectContextLoader | None = None,
        skill_registry: SkillRegistry | None = None,
        plan_store: PendingPlanStore | None = None,
    ) -> None:
        self.project_resolver = project_resolver
        self.context_loader = context_loader or ProjectContextLoader(project_resolver)
        self.skill_registry = skill_registry or SkillRegistry()
        self.plan_store = plan_store or PendingPlanStore()

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
            response = self._execute_pending(channel_id, thread_ts)
            if defer_missing_confirmation and response.startswith("실행할 보류 계획이 없습니다"):
                return None
            return response
        if _CANCELLATION.fullmatch(command_text):
            cancellation_response = self._cancel_pending(channel_id, thread_ts)
            if defer_missing_confirmation and cancellation_response is None:
                return None
            return cancellation_response
        if not _is_execution_request(command_text):
            return None

        project_name = self._project_name(channel_id, thread_ts, command_text, thread_context)
        if project_name is None:
            return "코드 작업할 대상 프로젝트명을 요청에 포함해 주세요."
        creator = getattr(agent, "create_execution_plan", None)
        if not callable(creator):
            return "코드 실행 계획에는 LLM 코드 에이전트가 필요합니다."

        target_paths = _PATH_IN_REQUEST.findall(command_text)
        try:
            context = self.context_loader.load(project_name, target_paths=target_paths)
            skills = self.skill_registry.select(intent=command_text, project_root=context.root)
            plan = creator(command_text, context, skills)
            _validate_plan(plan, project_name)
            detailed_context = self.context_loader.load(
                project_name, target_paths=plan.affected_files
            )
        except (InvalidProjectName, UnknownProject):
            return "대상 프로젝트를 찾을 수 없습니다."
        except (OSError, ValueError) as exc:
            logger.warning("execution_plan_rejected project=%s reason=%s", project_name, exc)
            return f"실행 계획을 만들지 못했습니다: {exc}"
        except Exception as exc:
            logger.exception("execution_plan_failed project=%s", project_name)
            return f"실행 계획 생성에 실패했습니다: {exc}"

        plan = replace(
            plan,
            applied_agents=[
                instruction.relative_path for instruction in detailed_context.instructions
            ],
            applied_skills=[f"{skill.name}@{skill.version}" for skill in skills],
        )
        self.plan_store.put(channel_id, thread_ts, plan, request=command_text)
        logger.info(
            "execution_plan_ready project=%s files=%s checks=%s risk=%s skills=%s",
            project_name,
            plan.affected_files,
            plan.verification_commands,
            plan.risk,
            plan.applied_skills,
        )
        return render_plan_preview(plan, detailed_context.git)

    def _project_name(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        thread_context: ThreadContextStore,
    ) -> str | None:
        # Keep project selection deterministic and let the current message win.
        prior = thread_context.read(channel_id, thread_ts)
        question = f"스레드 맥락:\n{prior}\n현재 요청:\n{text}" if prior else text
        request = classify_request(question, self.project_resolver)
        if request.project_name is not None:
            return request.project_name
        return None

    def _cancel_pending(self, channel_id: str, thread_ts: str) -> str | None:
        status = self.plan_store.cancel(channel_id, thread_ts)
        if status is PendingPlanStatus.READY:
            logger.info("execution_plan_cancelled channel=%s thread=%s", channel_id, thread_ts)
            return "보류된 실행 계획을 취소했습니다. 파일은 변경하지 않았습니다."
        if status is PendingPlanStatus.EXPIRED:
            return "보류된 실행 계획이 만료되었습니다. 파일은 변경하지 않았습니다."
        return None

    def _execute_pending(self, channel_id: str, thread_ts: str) -> str:
        status, pending = self.plan_store.take(channel_id, thread_ts)
        if status is PendingPlanStatus.EXPIRED:
            return "보류된 실행 계획이 만료되었습니다. 새로 계획을 만들어 주세요."
        if status is PendingPlanStatus.MISSING or pending is None:
            return "실행할 보류 계획이 없습니다. 먼저 코드 작업 요청을 보내 주세요."

        plan = pending.plan
        try:
            context = self.context_loader.load(plan.project_name, target_paths=plan.affected_files)
            tools = ProjectExecutionTools(context.root, plan.affected_files)
            changed_files: list[str] = []
            diffs: list[str] = []
            for step in plan.steps:
                changed_file, diff = tools.write_file(step.path, step.content)
                changed_files.append(changed_file)
                diffs.append(diff)
            checks = [tools.run_check(name) for name in plan.verification_commands]
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            logger.warning("execution_failed project=%s reason=%s", plan.project_name, exc)
            return render_execution_failure(plan, str(exc))

        result = ExecutionResult(
            changed_files=changed_files,
            diffs=diffs,
            checks=checks,
            remaining_risks=_remaining_risks(checks),
        )
        logger.info(
            "execution_completed project=%s files=%s checks=%s success=%s",
            plan.project_name,
            changed_files,
            [check.name for check in checks],
            all(check.success for check in checks),
        )
        return render_execution_result(plan, result)


def render_plan_preview(plan: ExecutionPlan, git: GitState) -> str:
    instructions = ", ".join(f"`{item}`" for item in plan.applied_agents) or "없음"
    skills = ", ".join(f"`{item}`" for item in plan.applied_skills) or "없음"
    files = "\n".join(f"- `{path}`" for path in plan.affected_files) or "- 파일 변경 없음"
    steps = "\n".join(f"- `{step.path}` 작성" for step in plan.steps) or "- 파일 변경 없음"
    checks = ", ".join(plan.verification_commands) or "없음"
    git_summary = (
        "Git 저장소 아님" if not git.is_repository else f"기존 변경 {len(git.changed_files)}개"
    )
    return (
        "코드 실행 계획을 만들었습니다.\n"
        f"목표: {plan.goal}\n프로젝트: `{plan.project_name}` ({git_summary})\n"
        f"위험도: `{plan.risk}`\n영향 파일:\n{files}\n실행 단계:\n{steps}\n"
        f"검증: {checks}\n적용 AGENTS.md: {instructions}\n적용 Skill: {skills}\n\n"
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
        "✅ 실행 및 검증 완료"
        if all(check.success for check in result.checks)
        else "⚠️ 실행 완료, 검증 실패"
    )
    return (
        f"{outcome}\n변경 파일:\n{changes}\nDiff 요약: {_diff_summary(result.diffs)}\n"
        f"검증 결과:\n{checks}\n"
        f"적용 AGENTS.md: {instructions}\n적용 Skill: {skills}\n남은 위험:\n{risks}"
    )


def render_execution_failure(plan: ExecutionPlan, reason: str) -> str:
    return (
        "❌ 실행 실패\n"
        f"프로젝트: `{plan.project_name}`\n원인: {reason}\n"
        "일부 파일이 이미 변경됐을 수 있으므로 Git diff를 확인한 뒤 새 계획을 만들어 주세요."
    )


def _project_path(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if str(path) in {"", "."} or path.is_absolute() or ".." in path.parts:
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


def _validate_plan(plan: ExecutionPlan, project_name: str) -> None:
    if plan.project_name != project_name:
        raise ValueError("계획의 프로젝트가 요청 대상과 다릅니다")
    if plan.risk is ExecutionRisk.HIGH:
        raise ValueError("고위험 작업(의존성·네트워크·삭제·Git push)은 지원하지 않습니다")
    if len(plan.steps) > 20 or len(plan.affected_files) > 20:
        raise ValueError("한 계획에서 변경할 수 있는 파일 수를 초과했습니다")
    if set(plan.affected_files) != {step.path for step in plan.steps}:
        raise ValueError("영향 파일과 쓰기 단계가 일치하지 않습니다")
    for path in plan.affected_files:
        _project_path(Path("/safe-root"), path)
    if any(step.action != "write_file" for step in plan.steps):
        raise ValueError("허용되지 않은 실행 단계가 포함되었습니다")
    if any(command not in _ALLOWED_VERIFICATIONS for command in plan.verification_commands):
        raise ValueError("허용되지 않은 검증 명령이 포함되었습니다")
    if len(set(plan.verification_commands)) != len(plan.verification_commands):
        raise ValueError("검증 명령이 중복되었습니다")


def _fingerprint(request: str) -> str:
    return hashlib.sha256(request.strip().casefold().encode("utf-8")).hexdigest()


def _remaining_risks(checks: list[CommandResult]) -> list[str]:
    if not checks:
        return ["검증 명령이 실행하지 않은 환경별 통합 테스트는 확인되지 않았습니다."]
    if any(not check.success for check in checks):
        return ["실패한 검증을 수정한 뒤 새 계획을 만들어 다시 실행해야 합니다."]
    return []


def _render_check(check: CommandResult) -> str:
    if check.success:
        return f"- ✅ {check.name}"
    detail = _redact_output(check.output).replace("\n", " ")[:300] or "출력 없음"
    return f"- ❌ {check.name}: {detail}"


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
