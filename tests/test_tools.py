"""Filesystem tools for the tool-calling analysis agent (see plan.md Section 6, 8).

Every tool takes a project **name**, resolving and validating it via `ProjectResolver`
itself — an unknown project or a relative path that would escape it comes back as a
clear error string, never a raised exception past the tool boundary (see plan.md
Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.project_resolver import ProjectResolver
from src.tools import find_files, list_files, list_projects, read_file


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


def test_find_files_lists_nested_files_and_skips_ignored_directories(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    (project / "api" / "src" / "main" / "java").mkdir(parents=True)
    (project / "api" / "src" / "main" / "java" / "X.java").write_text("class X {}")
    (project / "README.md").write_text("hello")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]")
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "node_modules" / "pkg" / "index.js").write_text("x")
    project_resolver = ProjectResolver(root=tmp_path)

    result = find_files(project_resolver, "my-project")

    assert result == ["README.md", "api/src/main/java/X.java"]


def test_find_files_truncates_results_over_the_limit_and_says_so(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    project.mkdir()
    for index in range(5):
        (project / f"f{index}.txt").write_text("x")
    project_resolver = ProjectResolver(root=tmp_path)

    result = find_files(project_resolver, "my-project", max_results=3)

    assert result[:3] == ["f0.txt", "f1.txt", "f2.txt"]
    assert len(result) == 4
    assert "5개 중 3개" in result[3]


def test_find_files_excludes_files_deeper_than_the_depth_limit(tmp_path: Path) -> None:
    project = tmp_path / "my-project"
    (project / "d1" / "d2").mkdir(parents=True)
    (project / "a.txt").write_text("x")
    (project / "d1" / "b.txt").write_text("x")
    (project / "d1" / "d2" / "c.txt").write_text("x")
    project_resolver = ProjectResolver(root=tmp_path)

    result = find_files(project_resolver, "my-project", max_depth=2)

    assert result == ["a.txt", "d1/b.txt"]


def test_find_files_rejects_a_path_escaping_the_project(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    (tmp_path / "secret.txt").write_text("top secret")
    project_resolver = ProjectResolver(root=tmp_path)

    result = find_files(project_resolver, "my-project", "..")

    assert result == "허용되지 않은 경로입니다: .."
