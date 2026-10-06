"""A project opts into skills through `.piplup/allowed-skills.txt`, one name per line.

The file never lets a project supply its own executable skill by path: it only names
skills a consumer already knows where to find.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILL_NAME = re.compile(r"[A-Za-z0-9_-]+")


def project_allowlist(project_root: Path) -> set[str]:
    allowlist = project_root / ".piplup" / "allowed-skills.txt"
    if not allowlist.is_file() or allowlist.is_symlink():
        return set()
    resolved = allowlist.resolve()
    if not resolved.is_relative_to(project_root.resolve()):
        return set()
    return {
        line.strip()
        for line in allowlist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def claude_skill_names(project_root: Path) -> tuple[str, ...]:
    """Only the project's own `claude:<name>` entries are Claude skills; the rest of the
    allowlist names Codex skills the plan mode reads from trusted roots."""
    prefix = "claude:"
    names = (
        name.removeprefix(prefix)
        for name in project_allowlist(project_root)
        if name.startswith(prefix)
    )
    # A name becomes a directory under `.claude/skills/`, so path-like ones are dropped.
    return tuple(sorted(name for name in names if _SKILL_NAME.fullmatch(name)))
