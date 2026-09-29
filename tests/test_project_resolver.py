"""ProjectResolver: resolves a local project directory under a configured root.

No git clone, no cache — a project is only ever something that already exists on
disk under the configured root (see plan.md's design summary).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.project_resolver import (
    AmbiguousProject,
    InvalidProjectName,
    ProjectResolver,
    UnknownProject,
)


def test_resolve_returns_path_for_existing_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    resolver = ProjectResolver(root=tmp_path)

    result = resolver.resolve("my-project")

    assert result == tmp_path / "my-project"


def test_resolve_supports_orca_root_projects_collection(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "my-project"
    project.mkdir(parents=True)
    resolver = ProjectResolver(root=tmp_path)

    assert resolver.resolve("my-project") == project
    assert resolver.resolve("projects/my-project") == project


def test_resolve_raises_unknown_project_when_directory_does_not_exist(tmp_path: Path) -> None:
    resolver = ProjectResolver(root=tmp_path)

    with pytest.raises(UnknownProject):
        resolver.resolve("missing-project")


def test_resolve_uses_unique_git_origin_repository_name(tmp_path: Path) -> None:
    checkout = tmp_path / "piplup-agent-v2"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/ilgolf/slack_bot_agent.git"],
        check=True,
    )

    assert ProjectResolver(root=tmp_path).resolve("slack_bot_agent") == checkout


def test_duplicate_origin_name_requires_local_folder_name(tmp_path: Path) -> None:
    for name in ("first-checkout", "second-checkout"):
        checkout = tmp_path / name
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(
            ["git", "-C", str(checkout), "remote", "add", "origin",
             "git@github.com:ilgolf/slack_bot_agent.git"],
            check=True,
        )

    with pytest.raises(AmbiguousProject):
        ProjectResolver(root=tmp_path).resolve("slack_bot_agent")


@pytest.mark.parametrize("bad_name", ["../secret", "/etc/passwd", "sub/dir", ".."])
def test_resolve_rejects_path_traversal_names(tmp_path: Path, bad_name: str) -> None:
    resolver = ProjectResolver(root=tmp_path)

    with pytest.raises(InvalidProjectName):
        resolver.resolve(bad_name)


def test_resolve_rejects_traversal_even_when_the_target_exists_on_disk(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    resolver = ProjectResolver(root=root)

    with pytest.raises(InvalidProjectName):
        resolver.resolve("../secret")
