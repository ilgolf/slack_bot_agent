"""Plan-first development: docs/<area>/plan.md must be confirmed before code is written
(plan.md Phase 28)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plan_first import detect_area, has_open_questions, plan_first_enabled, plan_status


def _plan(root: Path, area: str, text: str) -> None:
    path = root / "docs" / area / "plan.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("# 기획\n\n상태: 확정\n", "확정"),
        ("# 기획\n\n상태: 초안\n", "초안"),
        ("# 기획\n\n상태: 검토중\n", "초안"),
        ("# 기획\n\n본문만 있고 상태 줄이 없다\n", "초안"),
        ("# 기획\n\n> 상태: 확정 이라고 인용만 한 줄\n", "초안"),
    ],
)
def test_plan_status_reads_the_status_line_and_anything_else_is_a_draft(
    tmp_path: Path, text: str, status: str
) -> None:
    _plan(tmp_path, "linear", text)

    assert plan_status(tmp_path, "linear") == status


def test_a_missing_plan_is_a_draft(tmp_path: Path) -> None:
    assert plan_status(tmp_path, "notion") == "초안"


def test_open_questions_are_the_unchecked_items_under_that_section_only() -> None:
    assert has_open_questions("## 열린 질문\n\n- [ ] Q1. 무엇?\n")
    assert not has_open_questions("## 열린 질문\n\n- [x] Q1. 정함\n")
    assert not has_open_questions("## 목표\n\n- [ ] 이건 할 일 목록\n\n## 열린 질문\n\n없음\n")
    assert not has_open_questions("## 열린 질문\n\n- [x] Q1\n\n## 슬라이스\n\n- [ ] 나중에\n")
    assert not has_open_questions("본문")


@pytest.mark.parametrize(
    ("messages", "area"),
    [
        (["linear 개발 진행해"], "linear"),
        (["Notion 쪽 기획해줘"], "notion"),
        (["docs/code/plan.md 보고 진행해"], "code"),
        (["코드 에이전트 개발 진행해"], "code"),
        (["linear 기획 확정", "이제 notion 개발 진행해"], "notion"),
        (["개발 진행해"], None),
        (["decode 이슈 고쳐줘", "linearity 문제"], None),
    ],
)
def test_the_area_is_the_most_recently_named_one(messages: list[str], area: str | None) -> None:
    assert detect_area(messages) == area


def test_plan_first_is_opt_in_by_a_plain_marker_file(tmp_path: Path) -> None:
    assert not plan_first_enabled(tmp_path)

    (tmp_path / ".piplup").mkdir()
    marker = tmp_path / ".piplup" / "plan-first"
    marker.write_text("", encoding="utf-8")
    assert plan_first_enabled(tmp_path)

    marker.unlink()
    outside = tmp_path / "outside"
    outside.write_text("", encoding="utf-8")
    marker.symlink_to(outside)
    assert not plan_first_enabled(tmp_path)
