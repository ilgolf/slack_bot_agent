"""Post-run review of a worktree an agent edited (plan.md Phase 18).

The hook in `edit_guard` is the first defense; this is the authoritative one. It reads
what actually changed from `git status`/`diff` — never the agent's own account — and
reports violations as short messages that name paths but never carry file content.
"""

from __future__ import annotations

import difflib
import hashlib
import posixpath
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.code.edit_guard import is_within_roots
from src.code.plan import ExecutionPlan, render_code_work_status
from src.code.tooluse import CommandResult, VerificationStatus, _redact_output
from src.code.workspace import GitCommands
from src.core.plan_first import (
    CONFIRMED,
    is_plan_path,
    parse_status,
)
from src.core.plan_guard import (
    MAX_WRITE_BYTES,
    PLAN_MAX_TOTAL_BYTES,
    blanks_file,
    drops_most_lines,
    is_protected_meta_path,
    is_risky_path,
    is_secret_path,
)
from src.core.run_state import CodeWorkState

MAX_CHANGED_FILES = 20
_UNTRACKED = "??"


@dataclass(frozen=True)
class EditReview:
    changed_files: list[str]
    diffs: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def review_worktree(
    git: GitCommands,
    worktree: Path,
    *,
    named_paths: Collection[str] = (),
    write_roots: Collection[str] | None = None,
    plan_first: bool = False,
) -> EditReview:
    """`write_roots` confine changes to those directories; `plan_first` also refuses a plan
    the agent itself marked `확정` (only the user's `기획 확정` command may)."""
    entries = _status_entries(git, worktree)
    changed = [path for _, path in entries]
    if len(entries) > MAX_CHANGED_FILES:
        return EditReview(
            changed,
            violations=[
                f"변경된 파일이 {len(entries)}개로 한도({MAX_CHANGED_FILES}개)를 넘습니다."
            ],
        )
    named = {posixpath.normpath(name.replace("\\", "/")) for name in named_paths}
    diffs: list[str] = []
    violations: list[str] = []
    total_bytes = 0
    for status, path in entries:
        found = _check_entry(git, worktree, status, path, path in named)
        violations.extend(found.violations)
        if write_roots is not None and not is_within_roots(path, write_roots):
            violations.append(
                f"`{path}`: 기획이 확정되기 전에는 "
                f"{', '.join(write_roots)} 아래만 수정할 수 있습니다."
            )
        if plan_first and _confirmed_by_agent(git, worktree, status, path):
            violations.append(
                f"`{path}`: 기획 상태를 `확정`으로 바꾸는 것은 "
                "사용자의 `기획 확정` 명령만 할 수 있습니다."
            )
        total_bytes += found.size
        diffs.append(found.diff)
    if total_bytes > PLAN_MAX_TOTAL_BYTES:
        violations.append(
            f"변경 내용의 총 크기({total_bytes // 1024}KB)가 한도"
            f"({PLAN_MAX_TOTAL_BYTES // 1024}KB)를 넘습니다."
        )
    return EditReview(changed, diffs, violations)


@dataclass(frozen=True)
class _Checked:
    violations: list[str]
    size: int = 0
    diff: str = ""


def _check_entry(
    git: GitCommands, worktree: Path, status: str, path: str, is_named: bool
) -> _Checked:
    if "D" in status:
        return _Checked([f"`{path}`: 파일 삭제는 지원하지 않습니다."])
    if "R" in status or "C" in status:
        return _Checked([f"`{path}`: 파일 이름 변경·복사는 지원하지 않습니다."])
    if "T" in status:
        return _Checked([f"`{path}`: 파일 형식 변경은 지원하지 않습니다."])
    violations: list[str] = []
    if is_secret_path(path):
        violations.append(f"`{path}`: 비밀값 파일은 수정할 수 없습니다.")
    elif (is_protected_meta_path(path) or is_risky_path(path)) and not is_named:
        violations.append(f"`{path}`: 직접 지정하지 않은 관리·실행 관련 파일은 수정할 수 없습니다.")
    try:
        after = (worktree / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _Checked([*violations, f"`{path}`: 텍스트 파일이 아니거나 읽을 수 없습니다."])
    size = len(after.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        violations.append(f"`{path}`: 파일 크기가 한도({MAX_WRITE_BYTES // 1000}KB)를 넘습니다.")
    before = None if status == _UNTRACKED else _head_text(git, worktree, path)
    if before is not None and blanks_file(before, after):
        violations.append(f"`{path}`: 기존 내용을 모두 비우는 변경은 지원하지 않습니다.")
    elif before is not None and not is_named and drops_most_lines(before, after):
        violations.append(
            f"`{path}`: 기존 내용 대부분을 지우는 변경은 직접 지정한 파일만 가능합니다."
        )
    return _Checked(violations, size, _diff(git, worktree, status, path, after))


def _confirmed_by_agent(git: GitCommands, worktree: Path, status: str, path: str) -> bool:
    """A plan that now says `확정` although the committed version did not."""
    if not is_plan_path(path) or "D" in status:
        return False
    try:
        after = parse_status((worktree / path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    if after != CONFIRMED:
        return False
    before = None if status == _UNTRACKED else _head_text(git, worktree, path)
    return before is None or parse_status(before) != CONFIRMED


def _status_entries(git: GitCommands, worktree: Path) -> list[tuple[str, str]]:
    raw = git(worktree, "status", "--porcelain", "-uall", "-z")
    parts = raw.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            index += 1  # the original path follows a rename or copy entry
        entries.append((status, path))
    return sorted(entries, key=lambda item: item[1])


def _head_text(git: GitCommands, worktree: Path, path: str) -> str | None:
    try:
        return git(worktree, "show", f"HEAD:{path}")
    except RuntimeError:
        return None


def _diff(git: GitCommands, worktree: Path, status: str, path: str, after: str) -> str:
    if status == _UNTRACKED:
        return "".join(
            difflib.unified_diff(
                [], after.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{path}"
            )
        )
    try:
        return git(worktree, "diff", "HEAD", "--", path)
    except RuntimeError:
        return ""


@dataclass(frozen=True)
class ExecutionResult:
    changed_files: list[str]
    diffs: list[str]
    checks: list[CommandResult]
    remaining_risks: list[str]
    repair_attempts: int = 0
    repair_failure_reason: str | None = None
    workspace_branch: str | None = None
    workspace_path: str | None = None
    rollback_warning: str | None = None


def _outcome_line(result: ExecutionResult) -> str:
    if result.repair_failure_reason:
        return f"❌ 구현 실패: {result.repair_failure_reason}"
    if not result.checks:
        return "✅ 구현 완료 (자동 검증 없음)"
    if not all(check.success for check in result.checks):
        return "⚠️ 변경 적용, 검증 실패"
    if result.repair_attempts:
        return f"✅ 구현 완료 (자동 복구 {result.repair_attempts}회)"
    return render_code_work_status(CodeWorkState.SUCCEEDED)


def render_execution_result(plan: ExecutionPlan, result: ExecutionResult) -> str:
    changes = "\n".join(f"- `{path}`" for path in result.changed_files) or "- 파일 변경 없음"
    checks = "\n".join(_render_check(check) for check in result.checks) or "- 실행한 검증 명령 없음"
    risks = "\n".join(f"- {risk}" for risk in result.remaining_risks) or "- 없음"
    instructions = ", ".join(f"`{item}`" for item in plan.applied_agents) or "없음"
    skills = ", ".join(f"`{item}`" for item in plan.applied_skills) or "없음"
    outcome = _outcome_line(result)
    workspace = ""
    if result.workspace_branch is not None:
        workspace = (
            f"작업 브랜치: `{result.workspace_branch}`\n작업 경로: `{result.workspace_path}`\n"
            "원본 체크아웃은 변경하지 않았습니다. 결과를 버리려면 `폐기`라고 보내세요.\n"
        )
    elif result.rollback_warning is not None:
        workspace = f"⚠️ {result.rollback_warning}\n"
    return (
        f"{outcome}\n{workspace}변경 파일:\n{changes}\nDiff 요약: {_diff_summary(result.diffs)}\n"
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


def _verification_fingerprint(checks: Sequence[CommandResult]) -> str:
    payload = [(check.name, check.status, check.output) for check in checks if not check.success]
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def _remaining_risks(checks: list[CommandResult]) -> list[str]:
    if not checks:
        return ["자동 검증 없이 적용됐습니다. 변경 내용을 직접 확인해 주세요."]
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
