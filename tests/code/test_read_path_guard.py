"""Read/Grep/Glob calls are judged before they run (plan.md Phase 15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.code.read_path_guard import is_allowed_read


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("inside")
    (tmp_path / "secret.txt").write_text("outside")
    return root


def test_read_inside_the_project_is_allowed(project: Path) -> None:
    assert is_allowed_read(project, "Read", {"file_path": str(project / "a.txt")})


def test_read_of_an_absolute_path_outside_the_project_is_denied(
    project: Path, tmp_path: Path
) -> None:
    assert not is_allowed_read(project, "Read", {"file_path": str(tmp_path / "secret.txt")})


def test_read_through_dotdot_out_of_the_project_is_denied(project: Path) -> None:
    assert not is_allowed_read(project, "Read", {"file_path": str(project / ".." / "secret.txt")})


def test_read_through_a_symlink_pointing_outside_is_denied(project: Path, tmp_path: Path) -> None:
    (project / "link.txt").symlink_to(tmp_path / "secret.txt")

    assert not is_allowed_read(project, "Read", {"file_path": str(project / "link.txt")})


def test_relative_read_path_is_judged_against_the_project_root(project: Path) -> None:
    assert is_allowed_read(project, "Read", {"file_path": "a.txt"})
    assert not is_allowed_read(project, "Read", {"file_path": "../secret.txt"})


def test_grep_is_denied_for_an_outside_path_and_allowed_without_a_path(
    project: Path, tmp_path: Path
) -> None:
    assert not is_allowed_read(project, "Grep", {"pattern": "x", "path": str(tmp_path)})
    assert is_allowed_read(project, "Grep", {"pattern": "x"})
    assert is_allowed_read(project, "Grep", {"pattern": "x", "path": str(project)})


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Glob", {"pattern": "/etc/*"}),
        ("Glob", {"pattern": "../*.txt"}),
        ("Glob", {"pattern": "*.txt", "path": "/etc"}),
        ("Grep", {"pattern": "x", "glob": "/etc/*"}),
        ("Grep", {"pattern": "x", "glob": "../*"}),
    ],
)
def test_glob_patterns_reaching_outside_the_project_are_denied(
    project: Path, tool: str, tool_input: dict[str, str]
) -> None:
    assert not is_allowed_read(project, tool, tool_input)


def test_glob_pattern_inside_the_project_is_allowed(project: Path) -> None:
    assert is_allowed_read(project, "Glob", {"pattern": "src/**/*.py"})


def test_judgement_errors_deny_the_call(project: Path) -> None:
    assert not is_allowed_read(project, "Read", {"file_path": 123})
    assert not is_allowed_read(project, "Read", {})
