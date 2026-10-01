"""Filesystem tools for the tool-calling analysis agent.

Every tool takes a project **name** and resolves + validates it itself via
`ProjectResolver` — an unknown project or a relative path that escapes it comes back
as a clear error string, never a raised exception past the tool boundary, so a
tool-calling agent can relay the failure in its own reply (see plan.md Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.project_resolver import InvalidProjectName, ProjectResolver, UnknownProject

_IGNORED_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", "build", "target", ".gradle", ".idea"}
)
_MAX_FIND_RESULTS = 200
_MAX_FIND_DEPTH = 12


class PathEscapesProject(Exception):
    pass


def _resolve_within_project(project_path: Path, relative_path: str) -> Path:
    resolved_project = project_path.resolve()
    target = (resolved_project / relative_path).resolve()
    if not target.is_relative_to(resolved_project):
        raise PathEscapesProject(relative_path)
    return target


def read_file(project_resolver: ProjectResolver, project_name: str, relative_path: str) -> str:
    try:
        project_path = project_resolver.resolve(project_name)
    except (UnknownProject, InvalidProjectName):
        return f"프로젝트를 찾을 수 없습니다: {project_name}"

    try:
        target = _resolve_within_project(project_path, relative_path)
    except PathEscapesProject:
        return f"허용되지 않은 경로입니다: {relative_path}"

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"텍스트 파일이 아니어서 읽을 수 없습니다: {relative_path}"
    except OSError:
        return f"파일을 읽을 수 없습니다: {relative_path}"


def list_files(
    project_resolver: ProjectResolver, project_name: str, relative_path: str = "."
) -> list[str] | str:
    try:
        project_path = project_resolver.resolve(project_name)
    except (UnknownProject, InvalidProjectName):
        return f"프로젝트를 찾을 수 없습니다: {project_name}"

    try:
        target = _resolve_within_project(project_path, relative_path)
    except PathEscapesProject:
        return f"허용되지 않은 경로입니다: {relative_path}"

    try:
        return sorted(p.name for p in target.iterdir())
    except OSError:
        return f"디렉터리를 읽을 수 없습니다: {relative_path}"


def find_files(
    project_resolver: ProjectResolver,
    project_name: str,
    relative_path: str = ".",
    *,
    max_results: int = _MAX_FIND_RESULTS,
    max_depth: int = _MAX_FIND_DEPTH,
) -> list[str] | str:
    try:
        project_path = project_resolver.resolve(project_name)
    except (UnknownProject, InvalidProjectName):
        return f"프로젝트를 찾을 수 없습니다: {project_name}"

    try:
        target = _resolve_within_project(project_path, relative_path)
    except PathEscapesProject:
        return f"허용되지 않은 경로입니다: {relative_path}"

    root = project_path.resolve()
    found = sorted(
        path.relative_to(root).as_posix()
        for path in target.rglob("*")
        if path.is_file()
        and len(path.relative_to(root).parts) <= max_depth
        and not _IGNORED_DIRS.intersection(path.relative_to(root).parts)
    )
    if len(found) <= max_results:
        return found
    notice = f"… 결과가 잘렸습니다 (총 {len(found)}개 중 {max_results}개 표시)"
    return [*found[:max_results], notice]


def list_projects(project_resolver: ProjectResolver) -> list[str]:
    return [path.name for path in project_resolver.project_directories()]
