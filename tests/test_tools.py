"""Filesystem tools for the tool-calling analysis agent (see plan.md Section 6, 8).

Every tool takes a project **name**, resolving and validating it via `ProjectResolver`
itself — an unknown project or a relative path that would escape it comes back as a
clear error string, never a raised exception past the tool boundary (see plan.md
Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.project_resolver import ProjectResolver
from src.tools import list_files, list_projects, read_file


def test_read_file_reads_file_inside_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "my-project" / "README.md").write_text("hello world")
    project_resolver = ProjectResolver(root=tmp_path)

    content = read_file(project_resolver, "my-project", "README.md")

    assert content == "hello world"


def test_read_file_returns_error_string_for_unknown_project(tmp_path: Path) -> None:
    project_resolver = ProjectResolver(root=tmp_path)

    result = read_file(project_resolver, "missing-project", "README.md")

    assert "찾을 수 없습니다" in result


def test_read_file_returns_error_string_for_path_escaping_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "secret.txt").write_text("top secret")
    project_resolver = ProjectResolver(root=tmp_path)

    result = read_file(project_resolver, "my-project", "../secret.txt")

    assert "허용되지 않은" in result


def test_read_file_returns_error_string_for_binary_file(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "my-project" / "image.bin").write_bytes(b"\xff\xd8\xff")
    project_resolver = ProjectResolver(root=tmp_path)

    result = read_file(project_resolver, "my-project", "image.bin")

    assert "텍스트 파일이 아니어서" in result


def test_list_files_lists_files_inside_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "my-project" / "a.txt").write_text("a")
    (tmp_path / "my-project" / "b.txt").write_text("b")
    project_resolver = ProjectResolver(root=tmp_path)

    files = list_files(project_resolver, "my-project")

    assert set(files) == {"a.txt", "b.txt"}


def test_list_files_returns_error_string_for_unknown_project(tmp_path: Path) -> None:
    project_resolver = ProjectResolver(root=tmp_path)

    result = list_files(project_resolver, "missing-project")

    assert "찾을 수 없습니다" in result


def test_list_files_returns_error_string_for_path_escaping_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    project_resolver = ProjectResolver(root=tmp_path)

    result = list_files(project_resolver, "my-project", "..")

    assert "허용되지 않은" in result


def test_list_projects_returns_project_directory_names(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "not-a-project.txt").write_text("x")
    project_resolver = ProjectResolver(root=tmp_path)

    names = list_projects(project_resolver)

    assert set(names) == {"alpha", "beta"}


def test_list_projects_includes_orca_projects_collection(tmp_path: Path) -> None:
    (tmp_path / "projects" / "alpha").mkdir(parents=True)
    project_resolver = ProjectResolver(root=tmp_path)

    assert list_projects(project_resolver) == ["alpha"]
