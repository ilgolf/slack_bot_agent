"""Harness guidance: bot-owned markdown loaded from a fixed directory into prompts."""

from __future__ import annotations

from pathlib import Path

from src.harness import DEFAULT_GUIDANCE, MAX_GUIDANCE_CHARS, load_guidance
from src.linear_tools import linear_capability


def test_guidance_joins_markdown_files_in_name_order(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("둘째")
    (tmp_path / "a.md").write_text("첫째")
    (tmp_path / "notes.txt").write_text("무시")

    assert load_guidance(tmp_path) == "첫째\n\n둘째"


def test_missing_or_empty_directory_falls_back_to_the_default_guidance(tmp_path: Path) -> None:
    assert load_guidance(tmp_path / "missing") == DEFAULT_GUIDANCE
    assert load_guidance(tmp_path) == DEFAULT_GUIDANCE
    (tmp_path / "a.md").write_text("   \n")
    assert load_guidance(tmp_path) == DEFAULT_GUIDANCE


def test_guidance_is_size_capped_and_ignores_links_out_of_the_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("밖의 비밀")
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "big.md").write_text("가" * (MAX_GUIDANCE_CHARS + 500))
    (harness / "link.md").symlink_to(outside)

    guidance = load_guidance(harness)

    assert len(guidance) == MAX_GUIDANCE_CHARS
    assert "밖의 비밀" not in guidance


def test_the_shipped_guidance_states_every_linear_operation_the_bot_supports() -> None:
    guidance = load_guidance()

    assert guidance != DEFAULT_GUIDANCE
    for operation in linear_capability().operations:
        assert operation.description in guidance
    assert "`실행`" in guidance
