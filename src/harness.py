"""Bot guidance loaded into prompts (plan.md Phase 20).

The SDK runs with `setting_sources=[]` in a project `cwd`, so an AGENTS.md or skill would
never be read. The bot instead reads its own `harness/*.md` from a fixed directory and puts
the text in the prompt. This is guidance, not a security boundary: confirmations and guards
stay in code.
"""

from __future__ import annotations

from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
MAX_GUIDANCE_CHARS = 8000
DEFAULT_GUIDANCE = (
    "Goodra-bot은 로컬 프로젝트 분석, 스레드 요약, Linear 이슈 조회·생성·수정, "
    "코드 작업(계획 → `실행` 확인 → 스레드 전용 worktree 편집)을 지원합니다."
)


def load_file(name: str, directory: Path = HARNESS_DIR) -> str:
    """One harness file by name, stripped; empty when it is missing, a symlink out of the
    directory, or not a plain file inside it."""
    root = directory.resolve()
    resolved = (directory / name).resolve()
    if not resolved.is_file() or not resolved.is_relative_to(root):
        return ""
    return resolved.read_text(encoding="utf-8").strip()[:MAX_GUIDANCE_CHARS]


def load_guidance(directory: Path = HARNESS_DIR) -> str:
    """Concatenate the directory's `*.md` files by name; fall back to a one-line summary."""
    if not directory.is_dir():
        return DEFAULT_GUIDANCE
    root = directory.resolve()
    parts: list[str] = []
    for path in sorted(directory.glob("*.md")):
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root):
            continue
        text = resolved.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)
    guidance = "\n\n".join(parts)[:MAX_GUIDANCE_CHARS]
    return guidance or DEFAULT_GUIDANCE
