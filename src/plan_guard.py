"""Deterministic checks on planned file writes (plan.md Phase 16).

Pure functions that raise `ValueError` with a Slack-safe reason; the workflow turns
that into its usual "실행 계획을 만들지 못했습니다" answer. Paths must already be
validated as project-relative by the caller.
"""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Collection, Sequence
from pathlib import Path

_RISKY_BASENAMES = {"conftest.py", "setup.py", "pyproject.toml", "setup.cfg", "tox.ini", "makefile"}
_RISKY_DIRECTORIES = {".github", ".husky"}
_RISKY_SUFFIXES = (".sh",)

PLAN_MAX_TOTAL_BYTES = 2 * 1024 * 1024
REPAIR_MAX_TOTAL_BYTES = 1024 * 1024
_MASS_DELETION_MIN_LINES = 20
_MASS_DELETION_RATIO = 0.5


def reject_blanking(project_root: Path, writes: Sequence[tuple[str, str]]) -> None:
    """Replacing a non-empty file with nothing is a deletion in disguise."""
    for relative_path, content in writes:
        if content.strip():
            continue
        try:
            before = (project_root / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if before.strip():
            raise ValueError(
                f"`{relative_path}`의 기존 내용을 모두 비우는 변경은 지원하지 않습니다"
            )


def reject_mass_deletion(
    project_root: Path, writes: Sequence[tuple[str, str]], user_named_paths: Collection[str]
) -> None:
    """Dropping most of a sizeable file is allowed only when the user named it."""
    for relative_path, content in writes:
        if relative_path in user_named_paths:
            continue
        try:
            before = (project_root / relative_path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if len(before) < _MASS_DELETION_MIN_LINES:
            continue
        kept = sum((Counter(before) & Counter(content.splitlines())).values())
        if len(before) - kept > len(before) * _MASS_DELETION_RATIO:
            raise ValueError(
                f"`{relative_path}`의 기존 내용 대부분을 지우는 변경은 "
                "사용자가 그 파일을 직접 지정한 경우에만 만들 수 있습니다"
            )


def _normalized(relative_path: str) -> str:
    """Casefolded, forward-slash, `.`/`..`/`//`-collapsed spelling of a path."""
    return posixpath.normpath(relative_path.replace("\\", "/").casefold())


def is_risky_path(relative_path: str) -> bool:
    """Files the fixed verification commands or CI would execute."""
    parts = _normalized(relative_path).split("/")
    return (
        parts[-1] in _RISKY_BASENAMES
        or not _RISKY_DIRECTORIES.isdisjoint(parts[:-1])
        or parts[-1].endswith(_RISKY_SUFFIXES)
    )


def is_secret_path(relative_path: str) -> bool:
    """`.env` files hold secrets this bot must never write, even when asked.
    Template files such as `.env.example` carry no secrets and stay editable."""
    basename = _normalized(relative_path).rsplit("/", 1)[-1]
    if not (basename == ".env" or basename.startswith(".env.")):
        return False
    return not basename.endswith((".example", ".sample", ".template"))


def reject_oversized_total(writes: Sequence[tuple[str, str]], limit_bytes: int) -> None:
    """Per-file and file-count caps alone still allow a very large single run."""
    total = sum(len(content.encode("utf-8")) for _, content in writes)
    if total > limit_bytes:
        raise ValueError(
            f"총 쓰기 크기({total // 1024}KB)가 한도({limit_bytes // 1024}KB)를 초과합니다"
        )
