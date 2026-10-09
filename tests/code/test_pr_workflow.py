"""`PR` 명령의 미리보기 (plan.md Phase 33 D). 사실은 주입한다."""

from __future__ import annotations

from src.code.pr_workflow import PullRequestFacts, PullRequestWorkflow

_FACTS = PullRequestFacts(
    branch="bot/C1-1.0",
    base="main",
    commit_count=2,
    first_commit_subject="bot: 로그인 수정",
    changed_files=["src/a.py", "tests/test_a.py"],
)


def test_the_preview_shows_branch_base_commits_title_and_files_and_keeps_a_draft() -> None:
    workflow = PullRequestWorkflow(facts_source=lambda channel_id, thread_ts: _FACTS)

    reply = workflow.preview("C1", "1.0", "PR 제목: 로그인 오류 수정")

    assert "`bot/C1-1.0`" in reply
    assert "`main`" in reply
    assert "커밋 2개" in reply
    assert "로그인 오류 수정" in reply
    assert "`src/a.py`" in reply
    assert "원격과 다를 수 있" in reply
    assert "`실행`" in reply
    assert workflow.has_pending("C1", "1.0")


def test_without_a_thread_branch_to_send_no_draft_is_kept() -> None:
    workflow = PullRequestWorkflow(facts_source=lambda channel_id, thread_ts: None)

    reply = workflow.preview("C1", "1.0", "PR")

    assert "올릴 작업 브랜치가 없습니다" in reply
    assert not workflow.has_pending("C1", "1.0")
