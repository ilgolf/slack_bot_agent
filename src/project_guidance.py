"""Hands a project's AGENTS.md to Claude, which only reads CLAUDE.md natively (spike A)."""

from __future__ import annotations

from pathlib import Path

MAX_AGENTS_MD_BYTES = 32 * 1024


def with_agents_md(prompt: str, project_root: Path) -> str:
    """Puts the project-root AGENTS.md ahead of `prompt`; a missing file, a symlink, a path
    leaving the project or a file over `MAX_AGENTS_MD_BYTES` hands over nothing."""
    path = project_root / "AGENTS.md"
    if not path.is_file() or path.is_symlink():
        return prompt
    if not path.resolve().is_relative_to(project_root.resolve()):
        return prompt
    if path.stat().st_size > MAX_AGENTS_MD_BYTES:
        return prompt
    guidance = path.read_text(encoding="utf-8")
    return f"프로젝트 지침 (AGENTS.md):\n{guidance}\n\n---\n\n{prompt}"
