"""도구 경계: 프로젝트 안 파일 읽기·쓰기와 허용된 검증 명령 실행 (plan.md Phase 32)."""


from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from src.core.plan_guard import (
    MAX_WRITE_BYTES,
)


class VerificationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ENVIRONMENT_ERROR = "environment_error"


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


# The checks run the project's own code, so they get only what a plain run needs: none of
# the bot's settings or secrets, no color codes in the Slack text, and no bytecode or caches
# written into the worktree (they would show up as untracked files in the post-run review).
_VERIFICATION_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")


def _verification_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _VERIFICATION_ENV_KEYS if key in os.environ}
    return {**env, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"}


class ProjectExecutionTools:
    """Restricted filesystem and verification tools used after confirmation only."""

    COMMANDS = {
        # Use the interpreter running the bot so verification is bound to its
        # virtual environment and does not depend on a separately installed
        # ``uv`` binary being present on PATH.
        "run_tests": (sys.executable, "-m", "pytest", "--color=no", "-p", "no:cacheprovider"),
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

    def write_file(self, relative_path: str, content: str) -> tuple[str, str]:
        if relative_path not in self.allowed_paths:
            raise ValueError(f"계획에 포함되지 않은 파일입니다: {relative_path}")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
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
            env=_verification_env(),
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


def _project_path(root: Path, relative_path: str, *, allow_root: bool = False) -> Path:
    path = Path(relative_path)
    is_root = str(path) in {"", "."}
    if (is_root and not allow_root) or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"허용되지 않은 경로입니다: {relative_path}")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"허용되지 않은 경로입니다: {relative_path}")
    return target


def _redact_output(output: str) -> str:
    return re.sub(
        r"(?i)\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD))=\S+",
        r"\1=[REDACTED]",
        output,
    )
