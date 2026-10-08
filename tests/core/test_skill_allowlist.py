"""The project's skill allowlist: which names a project may turn on."""

from __future__ import annotations

from pathlib import Path

from src.core.skill_allowlist import claude_skill_names


def _allowlist(project: Path, text: str) -> None:
    (project / ".piplup").mkdir()
    (project / ".piplup" / "allowed-skills.txt").write_text(text, encoding="utf-8")


def test_claude_skill_names_keep_only_plain_names_and_drop_path_like_ones(tmp_path: Path) -> None:
    _allowlist(
        tmp_path,
        "claude:good-skill\nclaude:Good_2\nclaude:../../x\nclaude:a/b\nclaude:\nclaude:two words\n",
    )

    assert claude_skill_names(tmp_path) == ("Good_2", "good-skill")
