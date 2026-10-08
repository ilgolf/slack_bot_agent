"""Judges Claude's `Read`/`Grep`/`Glob` calls before they run (plan.md Phase 15).

A call is allowed only when every path it can reach stays inside the project root.
Any malformed input is denied.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePath

_PATH_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path"}
_PATTERN_FIELDS = {"Glob": ("pattern",), "Grep": ("glob",)}


def is_allowed_read(project_root: Path, tool_name: str, tool_input: Mapping[str, object]) -> bool:
    try:
        return _is_allowed(project_root, tool_name, tool_input)
    except (OSError, ValueError, TypeError):
        return False


def _is_allowed(project_root: Path, tool_name: str, tool_input: Mapping[str, object]) -> bool:
    if tool_name not in _PATH_TOOLS:
        return False
    root = project_root.resolve()
    path = tool_input.get(_PATH_TOOLS[tool_name])
    if path is None:
        if tool_name == "Read":
            return False
    elif not isinstance(path, str) or not _is_inside(root, path):
        return False
    return all(
        _is_contained_pattern(tool_input[field])
        for field in _PATTERN_FIELDS.get(tool_name, ())
        if field in tool_input
    )


def _is_inside(root: Path, path: str) -> bool:
    return (root / path).resolve().is_relative_to(root)


def _is_contained_pattern(pattern: object) -> bool:
    if not isinstance(pattern, str):
        return False
    parts = PurePath(pattern)
    return not parts.is_absolute() and ".." not in parts.parts
