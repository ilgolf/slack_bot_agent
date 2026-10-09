"""PR 제목·본문 만들기 (plan.md Phase 33 B). 코드가 만들고 LLM과 diff 내용은 쓰지 않는다."""

from __future__ import annotations

from src.code.pull_request import build_pull_request


def test_a_title_the_user_gave_is_used_as_is() -> None:
    draft = build_pull_request(
        "PR 제목: 로그인 오류 수정", commit_subject="bot: fix", changed_files=["a.py"]
    )

    assert draft.title == "로그인 오류 수정"


def test_without_a_given_title_the_commit_subject_is_the_title() -> None:
    draft = build_pull_request("PR", commit_subject="bot: 로그인 오류 수정", changed_files=["a.py"])

    assert draft.title == "bot: 로그인 오류 수정"


def test_an_empty_title_after_the_label_falls_back_to_the_commit_subject() -> None:
    draft = build_pull_request("PR 제목:   ", commit_subject="bot: fix", changed_files=["a.py"])

    assert draft.title == "bot: fix"


def test_the_body_lists_the_changed_files_and_no_diff_content() -> None:
    draft = build_pull_request(
        "PR", commit_subject="bot: fix", changed_files=["src/a.py", "tests/test_a.py"]
    )

    assert draft.body == "변경한 파일:\n- `src/a.py`\n- `tests/test_a.py`"
