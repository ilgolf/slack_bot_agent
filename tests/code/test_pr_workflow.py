"""`PR` 명령의 미리보기 (plan.md Phase 33 D). 사실은 주입한다."""

from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from src.code.github import GhCli, GhResult
from src.code.pr_workflow import PullRequestFacts, PullRequestWorkflow
from src.code.workspace import GitCommands

_FACTS = PullRequestFacts(
    branch="bot/C1-1.0",
    base="main",
    commit_count=2,
    first_commit_subject="bot: 로그인 수정",
    changed_files=["src/a.py", "tests/test_a.py"],
)


class Calls:
    """git과 gh 호출을 순서대로 기록한다."""

    def __init__(self, gh_url: str = "https://github.com/acme/repo/pull/7") -> None:
        self.log: list[str] = []
        self._gh_url = gh_url
        self.git_error: Exception | None = None
        self.gh_result: GhResult | None = None
        self.gh_timeout = False

    def git_runner(self, argv: list[str]) -> str:
        self.log.append("git " + " ".join(argv[5:]))
        if self.git_error is not None:
            raise self.git_error
        return ""

    def gh_runner(self, argv: list[str], cwd: Path) -> GhResult:
        self.log.append("gh " + " ".join(argv[1:3]))
        if self.gh_timeout:
            raise TimeoutError
        if self.gh_result is not None:
            return self.gh_result
        return GhResult(0, self._gh_url + "\n", "")


def _workflow(
    calls: Calls, *, facts: PullRequestFacts | None = _FACTS, ttl: timedelta | None = None
) -> PullRequestWorkflow:
    return PullRequestWorkflow(
        facts_source=lambda channel_id, thread_ts: facts,
        worktree_of=lambda channel_id, thread_ts: Path("/wt"),
        git=GitCommands(calls.git_runner),
        gh=GhCli(calls.gh_runner),
        **({"ttl": ttl} if ttl is not None else {}),
    )


def test_the_preview_shows_branch_base_commits_title_and_files_and_keeps_a_draft() -> None:
    workflow = _workflow(Calls())

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
    workflow = _workflow(Calls(), facts=None)

    reply = workflow.preview("C1", "1.0", "PR")

    assert "올릴 작업 브랜치가 없습니다" in reply
    assert not workflow.has_pending("C1", "1.0")


def test_the_draft_remembers_who_asked_for_it() -> None:
    workflow = _workflow(Calls())

    workflow.preview("C1", "1.0", "PR", requester="U42")

    pending = workflow.pending("C1", "1.0")
    assert pending is not None
    assert pending.requester == "U42"


def test_someone_other_than_the_requester_cannot_send_it_and_the_draft_stays() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U99")

    assert reply is not None
    assert "요청한 사람만" in reply
    assert calls.log == []
    assert workflow.has_pending("C1", "1.0")


def test_an_unknown_requester_can_never_send_it() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR")

    reply = workflow.confirm("C1", "1.0", user_id=None)

    assert reply is not None
    assert "요청한 사람만" in reply
    assert calls.log == []


def test_the_requester_pushes_the_branch_then_opens_the_pull_request_exactly_once() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR 제목: 로그인 오류 수정", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")
    again = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "https://github.com/acme/repo/pull/7" in reply
    assert calls.log == ["git push origin bot/C1-1.0", "gh pr create"]
    assert again is None
    assert not workflow.has_pending("C1", "1.0")


def test_a_branch_without_commits_gets_no_draft() -> None:
    facts = PullRequestFacts("bot/C1-1.0", "main", 0, "", [])
    workflow = _workflow(Calls(), facts=facts)

    reply = workflow.preview("C1", "1.0", "PR", requester="U42")

    assert "올릴 커밋이 없습니다" in reply
    assert not workflow.has_pending("C1", "1.0")


def test_an_expired_draft_is_not_sent_and_says_so() -> None:
    calls = Calls()
    workflow = _workflow(calls, ttl=timedelta(seconds=-1))
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "만료" in reply
    assert calls.log == []
    assert not workflow.has_pending("C1", "1.0")


def test_cancelling_drops_the_draft_without_touching_git_or_github() -> None:
    calls = Calls()
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.cancel("C1", "1.0")
    nothing = workflow.cancel("C1", "1.0")

    assert reply is not None
    assert "취소" in reply
    assert nothing is None
    assert calls.log == []
    assert not workflow.has_pending("C1", "1.0")


def test_a_failed_push_opens_no_pull_request_and_is_not_retried() -> None:
    calls = Calls()
    calls.git_error = RuntimeError("git 명령이 실패했습니다.")
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "푸시에 실패" in reply
    assert "자동 재시도하지 않았습니다" in reply
    assert calls.log == ["git push origin bot/C1-1.0"]
    assert not workflow.has_pending("C1", "1.0")


@pytest.mark.parametrize(
    ("gh_result", "expected"),
    [
        (GhResult(1, "", "To get started, please run:  gh auth login"), "gh auth login"),
        (GhResult(1, "", "already exists:\nhttps://github.com/acme/repo/pull/3"), "pull/3"),
        (GhResult(1, "", "something else broke"), "PR을 만들지 못했습니다"),
    ],
)
def test_a_failed_pull_request_after_a_good_push_says_the_push_is_done(
    gh_result: GhResult, expected: str
) -> None:
    calls = Calls()
    calls.gh_result = gh_result
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "푸시는 완료" in reply
    assert expected in reply
    assert calls.log == ["git push origin bot/C1-1.0", "gh pr create"]
    assert not workflow.has_pending("C1", "1.0")


def test_a_gh_timeout_asks_the_user_to_check_github_instead_of_retrying() -> None:
    calls = Calls()
    calls.gh_timeout = True
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "GitHub에서 확인" in reply
    assert calls.log.count("gh pr create") == 1


def test_a_push_timeout_is_reported_as_a_failed_push() -> None:
    calls = Calls()
    calls.git_error = subprocess.TimeoutExpired("git", 30)
    workflow = _workflow(calls)
    workflow.preview("C1", "1.0", "PR", requester="U42")

    reply = workflow.confirm("C1", "1.0", user_id="U42")

    assert reply is not None
    assert "푸시에 실패" in reply
    assert calls.log == ["git push origin bot/C1-1.0"]
