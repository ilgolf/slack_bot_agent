"""Plan-first development: docs/<area>/plan.md must be confirmed before code is written
(plan.md Phase 28)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.plan_first import (
    PlanAnswer,
    detect_area,
    has_open_questions,
    open_questions,
    parse_answer,
    plan_first_enabled,
    plan_status,
    record_decision,
    wants_bypass,
    with_status,
)


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


def test_with_status_replaces_the_status_line_or_adds_one_below_the_title() -> None:
    assert with_status("# 기획\n\n상태: 초안\n\n본문\n", "확정") == "# 기획\n\n상태: 확정\n\n본문\n"
    assert with_status("# 기획\n\n본문\n", "확정") == "# 기획\n상태: 확정\n\n본문\n"
    assert with_status("본문만\n", "확정") == "상태: 확정\n본문만\n"


@pytest.mark.parametrize(
    "text",
    [
        "기획 없이 바로 개발 진행해",
        "기획 단계 우회해서 구현해",
        "docs 우회하고 코드 써",
        "plan-first 우회",
        "기획 건너뛰고 진행해",
        "기획을 생략하고 개발해",
        "Linear 개발, 기획 스킵",
    ],
)
def test_an_explicit_request_to_skip_the_plan_is_a_bypass(text: str) -> None:
    assert wants_bypass(text)


@pytest.mark.parametrize(
    "text",
    [
        "linear 개발 진행해",
        "기획부터 해줘",
        "기획 우회하지 마",
        "기획 건너뛰지 말고 진행해",
        "기획 없이는 개발하지 마",
        "우회 방법 알려줘",
        "docs 정리해줘",
    ],
)
def test_ordinary_or_negated_wording_is_not_a_bypass(text: str) -> None:
    assert not wants_bypass(text)


_PLAN = """# 기획

상태: 초안

## 목표
- [ ] 이건 할 일이라 질문이 아니다

## 열린 질문

- [ ] Q2. 쓰기를 몇 개 허용할까?
  추천: 1개 (이유 한 줄)
- [x] Q3. 이미 정함
  결정: 켠다
- [ ] Q4. 추천이 없는 질문
- [ ] Q5. 두 줄 추천
  설명 줄
  추천: 폴백 없음

## 슬라이스
- [ ] 이것도 질문이 아니다
"""


def test_the_unchecked_questions_are_read_with_their_number_text_and_recommendation() -> None:
    questions = open_questions(_PLAN)

    assert [(q.number, q.text, q.recommendation) for q in questions] == [
        ("Q2", "쓰기를 몇 개 허용할까?", "1개 (이유 한 줄)"),
        ("Q4", "추천이 없는 질문", None),
        ("Q5", "두 줄 추천", "폴백 없음"),
    ]


def test_a_document_without_questions_has_none() -> None:
    assert open_questions("본문만") == []
    assert open_questions("## 열린 질문\n\n- [x] Q1. 정함\n") == []


def test_a_decision_checks_the_question_and_adds_a_line_below_its_block() -> None:
    question = open_questions(_PLAN)[0]

    updated = record_decision(_PLAN, question, "1개로 시작")

    assert (
        "- [x] Q2. 쓰기를 몇 개 허용할까?\n  추천: 1개 (이유 한 줄)\n  결정: 1개로 시작\n"
        in updated
    )
    assert [q.number for q in open_questions(updated)] == ["Q4", "Q5"]
    # everything else is untouched
    assert updated.replace("- [x] Q2.", "- [ ] Q2.").replace("  결정: 1개로 시작\n", "") == _PLAN


def test_a_decision_goes_below_a_questions_last_indented_line() -> None:
    question = open_questions(_PLAN)[2]

    updated = record_decision(_PLAN, question, "폴백 없이")

    assert "  추천: 폴백 없음\n  결정: 폴백 없이\n\n## 슬라이스" in updated


def test_a_decision_is_one_trimmed_line_of_at_most_300_characters() -> None:
    question = open_questions(_PLAN)[0]

    assert "  결정: 줄바꿈 정리\n" in record_decision(_PLAN, question, "  줄바꿈\n  정리  ")
    assert "가" * 300 in record_decision(_PLAN, question, "가" * 300)
    for bad in ("", "   ", "가" * 301):
        with pytest.raises(ValueError):
            record_decision(_PLAN, question, bad)


@pytest.mark.parametrize(
    ("message", "kind", "text"),
    [
        ("추천대로", "recommended", ""),
        ("추천대로 해줘", "recommended", ""),
        ("나머지 추천대로", "all_recommended", ""),
        ("보류", "hold", ""),
        ("중단", "stop", ""),
        ("결정: 쓰기는 1개로", "decision", "쓰기는 1개로"),
        ("결정：쓰기는 1개로", "decision", "쓰기는 1개로"),
        ("결정:\n여러 줄\n답", "decision", "여러 줄\n답"),
        ("결정:", "decision", ""),
    ],
)
def test_the_fixed_answers_to_a_plan_review_are_recognised(
    message: str, kind: str, text: str
) -> None:
    assert parse_answer(message) == PlanAnswer(kind, text)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "message",
    [
        "추천대로 하자",
        "나머지는 추천대로",
        "보류할게",
        "중단해줘 지금",
        "Q2는 1개로 하자",
        "결정했어",
        "실행",
    ],
)
def test_wording_around_the_fixed_answers_is_not_an_answer(message: str) -> None:
    assert parse_answer(message) is None
