"""Hands a project's guidance for the Slack agent to Claude.

Claude does not read AGENTS.md natively and its CLAUDE.md loading is switched off (it is
written for developers, not for an agent without a shell), so these files go in the prompt.
`.piplup/slack.md` is where a project says how the Slack agent should work on it.
"""

from __future__ import annotations

from pathlib import Path

MAX_AGENTS_MD_BYTES = 32 * 1024
# In prompt order: shared rules first, Slack-specific ones after.
GUIDANCE_FILES = ("AGENTS.md", ".piplup/slack.md")


def guidance_files(project_root: Path) -> list[str]:
    """The guidance files a prompt would carry; a missing file, a symlink, a path leaving the
    project or a file over `MAX_AGENTS_MD_BYTES` is left out."""
    root = project_root.resolve()
    usable = []
    for name in GUIDANCE_FILES:
        path = project_root / name
        if not path.is_file() or path.is_symlink():
            continue
        if not path.resolve().is_relative_to(root):
            continue
        if path.stat().st_size > MAX_AGENTS_MD_BYTES:
            continue
        usable.append(name)
    return usable


def with_project_guidance(prompt: str, project_root: Path) -> str:
    sections = [
        f"프로젝트 지침 ({name}):\n{(project_root / name).read_text(encoding='utf-8')}"
        for name in guidance_files(project_root)
    ]
    if not sections:
        return prompt
    return "\n\n".join(sections) + f"\n\n---\n\n{prompt}"
