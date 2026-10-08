"""Resolves a local project directory under a configured projects root.

No git clone, no cache — a project is only ever something that already exists on
disk under the configured root (see plan.md's design summary).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class UnknownProject(Exception):
    pass


class InvalidProjectName(Exception):
    pass


class AmbiguousProject(UnknownProject):
    """More than one local checkout has the requested Git remote name."""


class ProjectResolver:
    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root)

    def resolve(self, name: str) -> Path:
        normalized_name = name.removeprefix("projects/")
        if (
            not normalized_name
            or "/" in normalized_name
            or "\\" in normalized_name
            or ".." in Path(normalized_name).parts
        ):
            raise InvalidProjectName(name)

        projects = self.project_directories()
        for path in projects:
            if path.name == normalized_name:
                return path
        matches = [
            path for path in projects if _origin_repository_name(path) == normalized_name.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousProject(name)
        raise UnknownProject(name)

    def project_directories(self) -> list[Path]:
        """Return allowed project roots for either common Orca layout.

        Some installations configure ``~/orca`` while others configure
        ``~/orca/projects``.  Only the dedicated ``projects`` collection gets
        one additional level; this is not an unrestricted recursive search.
        """
        root = self.root.expanduser()
        if not root.is_dir():
            return []
        direct = [path for path in root.iterdir() if path.is_dir() and path.name != "projects"]
        collection = root / "projects"
        nested = (
            [path for path in collection.iterdir() if path.is_dir()] if collection.is_dir() else []
        )
        return sorted([*direct, *nested], key=lambda path: path.name.casefold())


def _origin_repository_name(project_path: Path) -> str | None:
    if not (project_path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "config", "--local", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    repository_name = result.stdout.strip().rsplit("/", 1)[-1].removesuffix(".git")
    return repository_name.casefold() or None
