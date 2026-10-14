"""읽기 전용 요청 감지 (plan.md Phase 34). 현재 메시지만 본다."""

from __future__ import annotations

import pytest

from src.core.read_only import wants_read_only


@pytest.mark.parametrize(
    "text",
    [
        "표로 요약해줘. 코드는 고치지 마.",
        "수정하지 마",
        "파일은 수정하지 말고 요약해줘",
        "건드리지 마",
        "코드 변경하지 말고 설명만 해줘",
        "읽기만 해줘",
        "읽기 전용으로 봐줘",
        "read-only로 확인해줘",
        "수정 없이 현황만 알려줘",
        "코드는 고치지마",
    ],
)
def test_a_message_that_forbids_edits_is_read_only(text: str) -> None:
    assert wants_read_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "이 버그를 고쳐줘",
        "README 수정해줘",
        "수정하지 않은 파일 목록을 보여줘",
        "테스트를 추가해줘",
        "linear 개발 진행해",
        "",
    ],
)
def test_other_messages_are_not_read_only(text: str) -> None:
    assert not wants_read_only(text)
