"""P: 코드 작업 계획 타입·검증·미리보기 (plan.md Phase 32)."""


from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from src.code.agent import PlanResponseFormatError
from src.code.context import AppliedSkill, GitState, ProjectContext
from src.code.tooluse import ProjectExecutionTools, _project_path
from src.core.message_text import content_text
from src.core.plan_guard import (
    PLAN_MAX_TOTAL_BYTES,
    REPAIR_MAX_TOTAL_BYTES,
    is_protected_meta_path,
    is_risky_path,
    is_secret_path,
    reject_blanking,
    reject_mass_deletion,
    reject_oversized_total,
)
from src.core.run_state import CodeWorkState

logger = logging.getLogger(__name__)


_ALLOWED_VERIFICATIONS = ("run_tests", "run_lint", "run_typecheck")


DEFAULT_MAX_AUTO_REPAIRS = 2


DEFAULT_MAX_AUTOPILOT_ITEMS = 20


class ExecutionRisk(StrEnum):
    REVERSIBLE = "reversible"
    MODIFY = "modify"
    HIGH = "high"


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


def _create_plan_with_format_retry(
    creator: Callable[..., ExecutionPlan],
    request: str,
    context: ProjectContext,
    skills: list[AppliedSkill],
    existing_files: list[ExistingFile],
) -> ExecutionPlan:
    try:
        return creator(request, context, skills, existing_files)
    except PlanResponseFormatError:
        logger.warning("execution_plan_format_retry project=%s attempt=1", context.project_name)
        retry_request = (
            f"{request}\n\n형식 교정: 직전 응답은 실행 계획 JSON으로 파싱되지 않았습니다. "
            "설명이나 Markdown 코드 블록 없이 goal, project_name, affected_files, "
            "steps, verification_commands, risk 필드를 가진 JSON 객체 하나만 반환하세요. "
            "plan.md 본문은 steps[].content 문자열에 넣으세요."
        )
        return creator(retry_request, context, skills, existing_files)


def _linear_planning_sources(root: Path) -> list[str]:
    """Select a small read-only sample of existing Linear implementation and tests."""
    paths: list[str] = []
    total_bytes = 0
    for folder_name in ("src", "tests"):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("**/*linear*.py")):
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            if size > 16_000 or total_bytes + size > 48_000:
                continue
            paths.append(str(path.relative_to(root)))
            total_bytes += size
            if len(paths) == 4:
                return paths
    return paths


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
    evidence_paths: Sequence[str] = (),
) -> str:
    instructions = ", ".join(f"`{item}`" for item in plan.applied_agents) or "없음"
    skills = ", ".join(f"`{item}`" for item in plan.applied_skills) or "없음"
    files = "\n".join(f"- `{path}`" for path in plan.affected_files) or "- 파일 변경 없음"
    steps = "\n".join(f"- `{step.path}` 작성" for step in plan.steps) or "- 파일 변경 없음"
    checks = ", ".join(plan.verification_commands) or "없음"
    evidence = ", ".join(f"`{path}`" for path in evidence_paths) or "없음"
    risk = (
        "검증은 승인 후에만 실행되며, 환경 의존 오류가 발생할 수 있습니다."
        if plan.verification_commands
        else "자동 검증 없이 적용됩니다. 적용 후 직접 확인해 주세요."
    )
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
        f"조사 근거: {evidence}\n고정 검증 명령: {checks}\n"
        f"자동 복구 예산: 최대 {max_auto_repairs}회\n"
        "미지원 작업: 임의 셸·HTTP·패키지 설치/삭제·rename·Git commit/push\n"
        f"남은 위험: {risk}\n"
        f"적용 AGENTS.md: {instructions}\n적용 Skill: {skills}\n\n"
        "내용을 확인한 뒤 같은 스레드에 `실행`이라고 보내면 적용합니다. "
        "`취소`하면 계획만 제거합니다."
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


_UNCHECKED_ITEM = re.compile(r"^\s*- \[ \] (.+)$", re.MULTILINE)


def next_unchecked_item(plan_text: str) -> str | None:
    match = _UNCHECKED_ITEM.search(plan_text)
    return match.group(1) if match else None


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
        if is_protected_meta_path(path) and path not in named:
            raise ValueError(
                f"`{path}`는 프로젝트 관리 파일이라 사용자가 직접 지정한 경우에만 "
                "수정할 수 있습니다"
            )
    for path in plan.affected_files:
        if is_secret_path(path):
            raise ValueError(f"`{path}`는 비밀값 파일이라 수정할 수 없습니다")
        if is_risky_path(path) and path not in named:
            raise ValueError(
                f"`{path}`는 검증이나 CI에서 코드로 실행될 수 있어 사용자가 직접 지정한 "
                "경우에만 수정할 수 있습니다"
            )
    if plan.risk is ExecutionRisk.HIGH:
        raise ValueError("고위험 작업(의존성·네트워크·삭제·Git push)은 지원하지 않습니다")
    if len(plan.steps) > 20 or len(plan.affected_files) > 20:
        raise ValueError("한 계획에서 변경할 수 있는 파일 수를 초과했습니다")
    step_paths = {step.path for step in plan.steps}
    if set(plan.affected_files) != step_paths:
        mismatched = sorted(set(plan.affected_files) ^ step_paths)
        listed = ", ".join(f"`{path}`" for path in mismatched)
        raise ValueError(
            "승인 파일 범위 밖 수정이 포함되어 새 계획과 실행 확인이 필요합니다 "
            f"(불일치 파일: {listed})"
        )
    for path in plan.affected_files:
        _project_path(Path("/safe-root"), path)
        if (
            restrict_new_files_to_code_roots
            and project_root is not None
            and not _project_path(project_root, path).exists()
            and not _is_source_or_test_path(path)
        ):
            raise ValueError(
                f"새 파일 `{path}`은 `src/` 또는 `tests/` 아래에 만들어야 합니다"
            )
    if require_code_evidence and not any(_is_source_or_test_path(path) for path in evidence_paths):
        raise ValueError(
            "plan.md 실행에는 기존 `src/` 또는 `tests/` 파일을 읽은 구현 근거가 필요합니다"
        )
    if any(step.action != "write_file" for step in plan.steps):
        raise ValueError("허용되지 않은 실행 단계가 포함되었습니다")
    if project_root is not None:
        writes = [(step.path, step.content) for step in plan.steps]
        reject_oversized_total(writes, PLAN_MAX_TOTAL_BYTES)
        reject_blanking(project_root, writes)
        reject_mass_deletion(project_root, writes, named)
    if any(command not in _ALLOWED_VERIFICATIONS for command in plan.verification_commands):
        raise ValueError("허용되지 않은 검증 명령이 포함되었습니다")
    if len(set(plan.verification_commands)) != len(plan.verification_commands):
        raise ValueError("검증 명령이 중복되었습니다")


def _is_source_or_test_path(path: str) -> bool:
    directories = path.replace("\\", "/").split("/")[:-1]
    return "src" in directories or directories[:1] == ["tests"]


def _validate_repair_steps(steps: object, approved_paths: Sequence[str]) -> None:
    if not isinstance(steps, list) or not steps:
        raise ValueError("자동 복구 단계가 비어 있습니다")
    approved = set(approved_paths)
    for step in steps:
        if not isinstance(step, ExecutionStep) or step.action != "write_file":
            raise ValueError("허용되지 않은 자동 복구 단계입니다")
        if step.path not in approved:
            raise ValueError("자동 복구에 승인 파일 범위 밖 수정이 필요합니다")
        if is_protected_meta_path(step.path):
            raise ValueError("보호 파일은 자동 복구할 수 없어 새 계획과 확인이 필요합니다")
        if is_risky_path(step.path) or is_secret_path(step.path):
            raise ValueError("코드로 실행되거나 비밀값을 담은 파일은 자동 복구할 수 없습니다")
    reject_oversized_total([(step.path, step.content) for step in steps], REPAIR_MAX_TOTAL_BYTES)


def _fingerprint(request: str) -> str:
    return hashlib.sha256(request.strip().casefold().encode("utf-8")).hexdigest()


def _repair_fingerprint(steps: Sequence[ExecutionStep]) -> str:
    payload = [(step.path, step.content) for step in steps]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def parse_execution_plan(content: object) -> ExecutionPlan:
    """Parse the LLM's strict JSON plan without accepting arbitrary commands."""
    text = content_text(content).strip()
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
