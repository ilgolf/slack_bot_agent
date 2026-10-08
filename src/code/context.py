"""코드 에이전트 실행 맥락: 프로젝트 지침·git 상태·skill 선택 (plan.md Phase 32)."""


from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.code.tooluse import _project_path
from src.core.project_resolver import (
    ProjectResolver,
)
from src.core.skill_allowlist import project_allowlist


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

    def load(
        self,
        project_name: str,
        *,
        target_paths: list[str] | None = None,
        root: Path | None = None,
    ) -> ProjectContext:
        """`root` swaps in a thread worktree of the same project for the files read."""
        resolved = self.project_resolver.resolve(project_name).resolve()
        root = root.resolve() if root is not None else resolved
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
        allowed_names = project_allowlist(project_root)
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

    def _find_skill(self, name: str) -> Path | None:
        for root in self.trusted_roots.values():
            candidate = root / name / "SKILL.md"
            if not candidate.is_file() or candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved.is_relative_to(root):
                return resolved
        return None


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
