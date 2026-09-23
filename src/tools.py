"""Filesystem tools for the tool-calling analysis agent.

Every tool takes a project **name** and resolves + validates it itself via
`ProjectResolver` — an unknown project or a relative path that escapes it comes back
as a clear error string, never a raised exception past the tool boundary, so a
tool-calling agent can relay the failure in its own reply (see plan.md Section 8).
"""

from __future__ import annotations

from pathlib import Path

from src.project_resolver import InvalidProjectName, ProjectResolver, UnknownProject


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


def list_projects(project_resolver: ProjectResolver) -> list[str]:
    return [path.name for path in project_resolver.project_directories()]
