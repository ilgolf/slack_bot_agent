"""Judges Claude's `Write`/`Edit` calls before they run (plan.md Phase 18).

Reads stay with `read_path_guard`; this module decides whether an edit may touch a path.
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Mapping
from pathlib import Path

from src.plan_guard import (
    MAX_WRITE_BYTES,
    is_protected_meta_path,
    is_risky_path,
    is_secret_path,
    reject_blanking,
)
from src.read_path_guard import is_allowed_read

_READ_TOOLS = {"Read", "Grep", "Glob"}
_EDIT_TOOLS = {"Write", "Edit"}
_GIT_METADATA = ".git"


def is_allowed_tool_call(
    worktree: Path,
    tool_name: str,
    tool_input: Mapping[str, object],
    *,
    named_paths: Collection[str] = (),
    write_roots: Collection[str] | None = None,
) -> bool:
    """The one decision for any tool call in edit mode: reads and edits stay inside the
    worktree under the rules above, and every other tool (Bash included) is denied.
    Anything malformed or that raises while being judged is denied too."""
    try:
        if tool_name == "Read":
            path = tool_input.get("file_path")
            return (
                isinstance(path, str)
                and _relative(worktree, path) != "."
                and is_allowed_read(worktree, tool_name, tool_input)
            )
        if tool_name in _READ_TOOLS:
            if not isinstance(tool_input.get("pattern"), str):
                return False
            return is_allowed_read(worktree, tool_name, tool_input)
        if tool_name in _EDIT_TOOLS:
            return is_allowed_edit(
                worktree, tool_name, tool_input, named_paths=named_paths, write_roots=write_roots
            )
    except Exception:
        return False
    return False


def is_allowed_edit(
    worktree: Path,
    tool_name: str,
    tool_input: Mapping[str, object],
    *,
    named_paths: Collection[str] = (),
    write_roots: Collection[str] | None = None,
) -> bool:
    """`named_paths` are files the user asked for by name; only those may be management
    files (plan.md, CLAUDE.md, ...) or code-executing files (conftest.py, *.sh, ...).
    `write_roots`, when given, are the only directories an edit may land in."""
    if tool_name not in _EDIT_TOOLS:
        return False
    path = tool_input.get("file_path")
    if not is_allowed_read(worktree, "Read", {"file_path": path}):
        return False
    if _touches_git_metadata(worktree, str(path)):
        return False
    relative = _relative(worktree, str(path))
    if relative == ".":
        return False
    if write_roots is not None and not is_within_roots(relative, write_roots):
        return False
    if is_secret_path(relative):
        return False
    named = {posixpath.normpath(name.replace("\\", "/")) for name in named_paths}
    guarded = is_protected_meta_path(relative) or is_risky_path(relative)
    if guarded and relative not in named:
        return False
    return tool_name != "Write" or _is_acceptable_write(worktree, relative, tool_input)


def is_within_roots(relative: str, roots: Collection[str]) -> bool:
    """`relative` is a normalized worktree-relative path; a root is a directory like `docs/`."""
    return any(relative.startswith(root.rstrip("/") + "/") for root in roots)


def _is_acceptable_write(worktree: Path, relative: str, tool_input: Mapping[str, object]) -> bool:
    """A whole-file write may be neither oversized nor a deletion in disguise."""
    content = tool_input.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return False
    try:
        reject_blanking(worktree, [(relative, content)])
    except ValueError:
        return False
    return True


def _touches_git_metadata(worktree: Path, path: str) -> bool:
    """`.git` in a worktree is a file pointing at the original repository; rewriting it,
    or anything under a `.git` directory, would break out of the isolation."""
    parts = posixpath.normpath(_relative(worktree, path).casefold()).split("/")
    return _GIT_METADATA in parts


def _relative(worktree: Path, path: str) -> str:
    return (worktree.resolve() / path).resolve().relative_to(worktree.resolve()).as_posix()
