"""with_agents_md: the project's AGENTS.md handed to Claude ahead of the request."""

from __future__ import annotations

from pathlib import Path

from src.project_guidance import MAX_AGENTS_MD_BYTES, with_agents_md


def test_an_agents_md_at_the_size_limit_is_handed_over(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("a" * MAX_AGENTS_MD_BYTES, encoding="utf-8")

    assert "a" * 100 in with_agents_md("요청", tmp_path)


def test_an_agents_md_over_the_size_limit_is_not_handed_over(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("a" * (MAX_AGENTS_MD_BYTES + 1), encoding="utf-8")

    assert with_agents_md("요청", tmp_path) == "요청"
